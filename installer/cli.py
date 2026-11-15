from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from .catalog_index import parse_unity6_catalog_bin
from .config import ProjectConfig, load_config
from .lang_patcher import apply_lang_patches, collect_lang_patches_from_mods
from .patch_engine import build_and_deploy_mod, find_personality_bundles
from .state_ledger import StateLedger, is_game_running


def _check_pkg(name: str) -> str:
    return "installed" if importlib.util.find_spec(name) is not None else "missing"


def _discover_mod_dirs(cfg: ProjectConfig, requested_mod: str = "") -> list[Path]:
    if requested_mod:
        return [cfg.mods_dir / requested_mod]
    if not cfg.mods_dir.exists():
        return []
    result: list[Path] = []
    for mod_json in sorted(cfg.mods_dir.glob("*/mod.json")):
        if mod_json.parent.name.startswith("_template"):
            continue
        try:
            data = json.loads(mod_json.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("enabled", True):
            result.append(mod_json.parent)
    return result


def _read_manifest(mod_dir: Path) -> dict[str, Any]:
    return json.loads((mod_dir / "mod.json").read_text(encoding="utf-8"))


def cmd_status(_args: argparse.Namespace) -> int:
    cfg = load_config()
    unity_ver = cfg.detect_unity_version()
    lang_name, lang_dir = cfg.resolve_active_lang_dir()
    ledger = StateLedger.read_only(cfg.state_dir)
    l_stat = ledger.active_summary()
    cat_path = cfg.catalog_bin_path
    mod_dirs = _discover_mod_dirs(cfg)

    print("=== Limbus Claude Skin (10916) | Environment Check ===")
    print(f"Skin Pack Root     : {cfg.workspace_root}")
    print(f"Game Root          : {cfg.game_root} [{'OK' if cfg.game_root.exists() else 'MISSING'}]")
    print(f"Unity Engine       : {unity_ver}")
    print(f"Active Custom Lang : {lang_name or '(none)'} -> {lang_dir or 'N/A'}")
    print(f"Catalog Binary     : {cat_path} [{'OK' if cat_path.exists() else 'MISSING'}]")
    print(f"Unity Cache Root   : {cfg.unity_cache_root} [{'OK' if cfg.unity_cache_root.exists() else 'MISSING'}]")
    print(
        f"Game Process       : {'RUNNING (close LimbusCompany.exe first)' if is_game_running() else 'Stopped (safe)'}"
    )

    if cat_path.exists() and cfg.unity_cache_root.exists():
        records = parse_unity6_catalog_bin(cat_path, cfg.unity_cache_root)
        cached = sum(1 for r in records if r.is_cached)
        print(f"Addressables Index : {cached} / {len(records)} bundles cached on disk")
        for mdir in mod_dirs:
            manifest = _read_manifest(mdir)
            pid = int(manifest["target_personality_id"])
            mod_label = str(manifest.get("name") or mdir.name)
            bundles = find_personality_bundles(cfg, pid)
            if not bundles:
                print(f"Target {pid} [{mdir.name}]: NOT FOUND in catalog ({mod_label})")
                continue
            for role, rec in bundles.items():
                st = "READY" if rec.is_cached else f"NOT CACHED (open Identity {pid} in-game once)"
                print(f"Target {pid} [{role:4s}]: {rec.bundle_filename} -> {st}")

    print(
        "Python Deps        : "
        f"lz4={_check_pkg('lz4')}, PIL={_check_pkg('PIL')}, UnityPy={_check_pkg('UnityPy')}, "
        f"etcpak={_check_pkg('etcpak')}, astc_encoder={_check_pkg('astc_encoder')}, "
        f"texture2ddecoder={_check_pkg('texture2ddecoder')}"
    )
    print(
        f"Client Mod State   : {'MODDED' if l_stat['is_modded'] else 'CLEAN (Unmodified)'}"
        f" (bundles={len(l_stat['patched_bundles'])}, lang_files={len(l_stat['patched_lang_files'])})"
    )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if not args.build_only and is_game_running():
        print(
            "[Blocked] LimbusCompany.exe is currently running! "
            "Please close the game first, then re-run install / apply.",
            file=sys.stderr,
        )
        return 2

    for pkg in ("UnityPy", "lz4", "PIL", "etcpak"):
        if importlib.util.find_spec(pkg) is None:
            print(
                f"[Error] Missing dependency '{pkg}'. Run: pip install UnityPy lz4 Pillow etcpak",
                file=sys.stderr,
            )
            return 1

    cfg = load_config()
    if not args.build_only and not cfg.game_data_dir.exists():
        print(
            f"[Error] Game directory not found at: {cfg.game_root}\n"
            "Copy config.example.json to config.local.json and set your custom 'game_root'.",
            file=sys.stderr,
        )
        return 1

    target_mods = _discover_mod_dirs(cfg, args.mod)
    if not target_mods:
        print("[Error] No enabled skin mod found under mods/.", file=sys.stderr)
        return 1

    for mdir in target_mods:
        if not (mdir / "mod.json").exists():
            print(f"[Error] mod.json not found in {mdir}", file=sys.stderr)
            return 1

    ledger = StateLedger.read_only(cfg.state_dir) if args.build_only else StateLedger(cfg.state_dir)

    if not args.build_only:
        _, lang_dir = cfg.resolve_active_lang_dir()
        if lang_dir:
            plan = collect_lang_patches_from_mods(cfg.mods_dir)
            if plan.total_rules > 0:
                changes = apply_lang_patches(lang_dir, plan, ledger)
                print(f"[Lang] Applied {len(changes)} localization patch(es).")

    applied_labels: list[str] = []
    for mdir in target_mods:
        manifest = _read_manifest(mdir)
        applied_labels.append(f"{manifest.get('name', mdir.name)} ({manifest['target_personality_id']})")
        summary = build_and_deploy_mod(cfg, mdir, ledger, deploy=not args.build_only)
        mode = "Built only (dry run)" if args.build_only else "Installed & Deployed"
        print(
            f"[Skin:{mdir.name}] {mode} -> illust={len(summary['replaced_illusts'])}, "
            f"sd_frames={len(summary['replaced_sd_frames'])}, "
            f"fx_sprites={len(summary['replaced_fx_sprites'])}, "
            f"spine_tex={len(summary['replaced_spine_textures'])}, "
            f"spine_assets={len(summary['replaced_spine_assets'])}"
        )

    if not args.build_only:
        joined = ", ".join(applied_labels)
        print(f"\nDone! {joined} is now active in your game.")
        print("IMPORTANT: Keep this folder intact — your clean original backup lives in .state/.")
        print("Run restore_vanilla.cmd (or `python -m installer revert`) anytime to restore vanilla files.")
    return 0


def cmd_revert(_args: argparse.Namespace) -> int:
    if is_game_running():
        print(
            "[Blocked] LimbusCompany.exe is currently running! "
            "Please close the game first, then re-run revert.",
            file=sys.stderr,
        )
        return 2

    cfg = load_config()
    if not (cfg.state_dir / "ledger.json").exists():
        print("Restored 0 Lang JSON file(s) and 0 AssetBundle __data file(s) (client already clean).")
        return 0
    ledger = StateLedger(cfg.state_dir)
    langs = ledger.revert_all_lang_files()
    bundles = ledger.revert_all_bundles()
    print(f"Restored {len(langs)} Lang JSON file(s) and {len(bundles)} AssetBundle __data file(s) to vanilla state.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="installer",
        description="Lightweight local installer for Limbus Company Rodion — Thumb Father (10916) Claude Skin",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Auto-detect game paths, cache readiness & current mod state")

    p_apply = sub.add_parser("apply", help="Backup vanilla files and install the skin mod")
    p_apply.add_argument("--mod", default="", help="Specific mod folder name under mods/")
    p_apply.add_argument("--build-only", action="store_true", help="Build into build/ without writing game files")

    sub.add_parser("revert", help="Restore all modified Lang files & AssetBundles to clean vanilla state")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    dispatch = {
        "status": cmd_status,
        "apply": cmd_apply,
        "revert": cmd_revert,
    }
    return dispatch[args.cmd](args)
