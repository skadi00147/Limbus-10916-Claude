from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .state_ledger import StateLedger


@dataclass(frozen=True)
class SkillRenameRule:
    skill_id: int
    default_name: str | None
    by_level: dict[int, str]
    source_mod: str
    priority: int

    def resolve_for_level(self, level: int, original_name: str) -> str:
        if level in self.by_level:
            return self.by_level[level]
        if self.default_name is not None:
            return self.default_name
        return original_name


@dataclass(frozen=True)
class KeyedTextRule:
    entry_id: str
    value: str
    source_mod: str
    priority: int


@dataclass(frozen=True)
class LangPatchPlan:
    skill_names: list[SkillRenameRule]
    skill_flavors: list[SkillRenameRule]
    unit_keywords: list[KeyedTextRule]

    @property
    def total_rules(self) -> int:
        return len(self.skill_names) + len(self.skill_flavors) + len(self.unit_keywords)


def _load_enabled_manifests(mods_dir: Path) -> list[tuple[int, str, dict[str, Any]]]:
    if not mods_dir.exists():
        return []
    manifests: list[tuple[int, str, dict[str, Any]]] = []
    for mod_json in sorted(mods_dir.glob("*/mod.json")):
        data = json.loads(mod_json.read_text(encoding="utf-8"))
        if not data.get("enabled", False):
            continue
        mod_id = str(data.get("mod_id") or mod_json.parent.name)
        prio = int(data.get("priority", 100))
        manifests.append((prio, mod_id, data))
    manifests.sort(key=lambda x: (x[0], x[1]))
    return manifests


def _collect_skill_field_rules(
    manifests: list[tuple[int, str, dict[str, Any]]],
    manifest_key: str,
) -> list[SkillRenameRule]:
    merged: dict[int, SkillRenameRule] = {}
    for prio, mod_id, data in manifests:
        raw_map = data.get(manifest_key) or {}
        for sid_str, spec in raw_map.items():
            sid = int(sid_str)
            if isinstance(spec, str):
                rule = SkillRenameRule(
                    skill_id=sid,
                    default_name=spec,
                    by_level={},
                    source_mod=mod_id,
                    priority=prio,
                )
            elif isinstance(spec, dict):
                by_lvl = {int(k): str(v) for k, v in (spec.get("by_level") or {}).items()}
                default_val = spec.get("default")
                rule = SkillRenameRule(
                    skill_id=sid,
                    default_name=str(default_val) if default_val is not None else None,
                    by_level=by_lvl,
                    source_mod=mod_id,
                    priority=prio,
                )
            else:
                raise ValueError(f"Invalid {manifest_key} entry for {sid_str} in {mod_id}")

            prev = merged.get(sid)
            if prev is not None and prev.priority == prio:
                raise ValueError(
                    f"Skill ID {sid} ({manifest_key}) conflict at same priority {prio}: '{prev.source_mod}' vs '{mod_id}'"
                )
            merged[sid] = rule
    return list(merged.values())


def _collect_keyed_text_rules(
    manifests: list[tuple[int, str, dict[str, Any]]],
    manifest_key: str,
) -> list[KeyedTextRule]:
    merged: dict[str, KeyedTextRule] = {}
    for prio, mod_id, data in manifests:
        raw_map = data.get(manifest_key) or {}
        for entry_id, value in raw_map.items():
            if not isinstance(value, str):
                raise ValueError(f"Invalid {manifest_key} entry for {entry_id} in {mod_id}: expected str")
            prev = merged.get(entry_id)
            if prev is not None and prev.priority == prio:
                raise ValueError(
                    f"Entry '{entry_id}' ({manifest_key}) conflict at same priority {prio}: '{prev.source_mod}' vs '{mod_id}'"
                )
            merged[entry_id] = KeyedTextRule(
                entry_id=str(entry_id),
                value=value,
                source_mod=mod_id,
                priority=prio,
            )
    return list(merged.values())


def collect_lang_patches_from_mods(mods_dir: Path) -> LangPatchPlan:
    manifests = _load_enabled_manifests(mods_dir)
    return LangPatchPlan(
        skill_names=_collect_skill_field_rules(manifests, "skill_names"),
        skill_flavors=_collect_skill_field_rules(manifests, "skill_flavors"),
        unit_keywords=_collect_keyed_text_rules(manifests, "unit_keywords"),
    )


def _iter_skill_lang_files(lang_dir: Path) -> list[Path]:
    files: list[Path] = []
    files.extend(sorted(lang_dir.glob("Skills_personality-*.json")))
    files.extend(sorted(lang_dir.glob("Skills_Ego_Personality-*.json")))
    base = lang_dir / "Skills.json"
    if base.exists():
        files.append(base)
    return files


def _iter_unit_keyword_lang_files(lang_dir: Path) -> list[Path]:
    return sorted(lang_dir.glob("UnitKeyword*.json"))


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _apply_skill_file_patches(
    lang_dir: Path,
    name_rules: list[SkillRenameRule],
    flavor_rules: list[SkillRenameRule],
    ledger: StateLedger,
) -> list[dict[str, Any]]:
    name_map = {r.skill_id: r for r in name_rules}
    flavor_map = {r.skill_id: r for r in flavor_rules}
    changes: list[dict[str, Any]] = []
    if not name_map and not flavor_map:
        return changes

    target_sids = set(name_map) | set(flavor_map)
    for path in _iter_skill_lang_files(lang_dir):
        data_probe = json.loads(path.read_text(encoding="utf-8-sig"))
        if not any(item.get("id") in target_sids for item in data_probe.get("dataList", [])):
            continue

        backup_path = ledger.backup_lang_file(path)
        source_to_read = backup_path if backup_path.exists() else path
        data = json.loads(source_to_read.read_text(encoding="utf-8-sig"))
        modified = False

        for item in data.get("dataList", []):
            sid = item.get("id")
            if sid not in target_sids:
                continue
            n_rule = name_map.get(sid)
            f_rule = flavor_map.get(sid)
            for lv_obj in item.get("levelList", []):
                lvl = int(lv_obj.get("level", 1))
                if n_rule is not None:
                    old_name = str(lv_obj.get("name", ""))
                    new_name = n_rule.resolve_for_level(lvl, old_name)
                    if new_name != old_name:
                        lv_obj["name"] = new_name
                        modified = True
                        changes.append({"kind": "skill_name", "file": path.name, "skill_id": sid, "new": new_name})
                if f_rule is not None:
                    old_flavor = str(lv_obj.get("flavor") or "")
                    new_flavor = f_rule.resolve_for_level(lvl, old_flavor)
                    if new_flavor != old_flavor:
                        lv_obj["flavor"] = new_flavor
                        modified = True
                        changes.append({"kind": "skill_flavor", "file": path.name, "skill_id": sid, "new": new_flavor})

        if modified:
            _write_json_atomic(path, data)
            ledger.mark_lang_patched(path)

    return changes


def _apply_unit_keyword_patches(
    lang_dir: Path,
    keyword_rules: list[KeyedTextRule],
    ledger: StateLedger,
) -> list[dict[str, Any]]:
    rule_map = {r.entry_id: r for r in keyword_rules}
    changes: list[dict[str, Any]] = []
    if not rule_map:
        return changes

    for path in _iter_unit_keyword_lang_files(lang_dir):
        data_probe = json.loads(path.read_text(encoding="utf-8-sig"))
        if not any(str(item.get("id")) in rule_map for item in data_probe.get("dataList", [])):
            continue

        backup_path = ledger.backup_lang_file(path)
        source_to_read = backup_path if backup_path.exists() else path
        data = json.loads(source_to_read.read_text(encoding="utf-8-sig"))
        modified = False

        for item in data.get("dataList", []):
            eid = str(item.get("id", ""))
            rule = rule_map.get(eid)
            if rule is None:
                continue
            old_val = str(item.get("content", ""))
            if rule.value != old_val:
                item["content"] = rule.value
                modified = True
                changes.append({"kind": "unit_keyword", "file": path.name, "skill_id": eid, "new": rule.value})

        if modified:
            _write_json_atomic(path, data)
            ledger.mark_lang_patched(path)

    return changes


def apply_lang_patches(
    lang_dir: Path,
    plan: LangPatchPlan,
    ledger: StateLedger,
) -> list[dict[str, Any]]:
    """Apply all enabled Lang patches safely with automatic backup tracking."""
    changes: list[dict[str, Any]] = []
    changes.extend(_apply_skill_file_patches(lang_dir, plan.skill_names, plan.skill_flavors, ledger))
    changes.extend(_apply_unit_keyword_patches(lang_dir, plan.unit_keywords, ledger))
    return changes
