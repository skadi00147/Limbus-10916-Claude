from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    """Compute hex SHA-256 digest of a file using 1 MiB streaming chunks."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically write bytes to target path via a sibling .tmp file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Atomically write UTF-8 text to target path via a sibling .tmp file."""
    atomic_write_bytes(path, text.encode(encoding))


def is_game_running(process_name: str = "LimbusCompany.exe") -> bool:
    """Return True if the game executable is currently running on Windows."""
    if os.name != "nt":
        return False
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {process_name}", "/NH"],
            text=True,
            errors="ignore",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return process_name.lower() in out.lower()
    except Exception:
        return False


class StateLedger:
    """Transactional backup & restore ledger kept strictly inside local .state/."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.ledger_path = state_dir / "ledger.json"
        self.lang_backup_dir = state_dir / "backups" / "lang"
        self.bundle_backup_dir = state_dir / "backups" / "bundles"
        self.lang_backup_dir.mkdir(parents=True, exist_ok=True)
        self.bundle_backup_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def read_only(cls, state_dir: Path) -> "StateLedger":
        """Create a StateLedger handle without creating directories on disk."""
        inst = cls.__new__(cls)
        inst.state_dir = state_dir
        inst.ledger_path = state_dir / "ledger.json"
        inst.lang_backup_dir = state_dir / "backups" / "lang"
        inst.bundle_backup_dir = state_dir / "backups" / "bundles"
        return inst

    def load(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {"version": 1, "lang_files": {}, "bundles": {}}
        data = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        data.setdefault("lang_files", {})
        data.setdefault("bundles", {})
        return data

    def save(self, data: dict[str, Any]) -> None:
        atomic_write_text(self.ledger_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    # ---- Localization JSON Backups ----

    def backup_lang_file(self, target_file: Path) -> Path:
        ledger = self.load()
        key = str(target_file.resolve())
        current_hash = sha256_file(target_file)
        record = ledger["lang_files"].get(key)

        backup_file = self.lang_backup_dir / f"{target_file.name}.clean"
        need_fresh_backup = (
            record is None
            or not backup_file.exists()
            or (record.get("patched_sha256") and current_hash != record["patched_sha256"])
        )

        if need_fresh_backup:
            shutil.copy2(target_file, backup_file)
            ledger["lang_files"][key] = {
                "target_path": key,
                "backup_path": str(backup_file),
                "clean_sha256": current_hash,
                "patched_sha256": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.save(ledger)

        return backup_file

    def mark_lang_patched(self, target_file: Path) -> None:
        ledger = self.load()
        key = str(target_file.resolve())
        if key in ledger["lang_files"]:
            ledger["lang_files"][key]["patched_sha256"] = sha256_file(target_file)
            ledger["lang_files"][key]["updated_at"] = datetime.now(timezone.utc).isoformat()
            self.save(ledger)

    def revert_all_lang_files(self) -> list[Path]:
        ledger = self.load()
        restored: list[Path] = []
        for key, rec in list(ledger.get("lang_files", {}).items()):
            target = Path(rec["target_path"])
            backup = Path(rec["backup_path"])
            if backup.exists() and target.parent.exists():
                if target.exists() and rec.get("patched_sha256"):
                    cur_hash = sha256_file(target)
                    if cur_hash != rec["patched_sha256"] and cur_hash != rec["clean_sha256"]:
                        backup.unlink(missing_ok=True)
                        del ledger["lang_files"][key]
                        continue
                tmp = target.with_name(f"{target.name}.restore_tmp")
                shutil.copy2(backup, tmp)
                tmp.replace(target)
                restored.append(target)
            backup.unlink(missing_ok=True)
            del ledger["lang_files"][key]

        self.save(ledger)
        return restored

    # ---- Unity Addressables __data Bundle Backups ----

    def get_clean_bundle_backup_if_exists(self, data_path: Path) -> Path | None:
        """Read-only lookup for an existing clean baseline __data backup."""
        if not self.ledger_path.exists():
            return None
        ledger = self.load()
        key = str(data_path.resolve())
        rec = ledger.get("bundles", {}).get(key)
        if not rec:
            return None
        backup_file = Path(rec["backup_path"])
        return backup_file if backup_file.exists() else None

    def ensure_clean_bundle_backup(self, data_path: Path, bundle_name: str) -> Path:
        """Ensure an unmodded baseline copy of __data is stored inside .state/backups/bundles/."""
        ledger = self.load()
        key = str(data_path.resolve())
        current_hash = sha256_file(data_path)
        record = ledger["bundles"].get(key)

        safe_tag = f"{data_path.parent.parent.name}_{data_path.parent.name}"
        backup_file = self.bundle_backup_dir / f"{safe_tag}.__data.clean"

        need_fresh = (
            record is None
            or not backup_file.exists()
            or (
                record.get("patched_sha256")
                and current_hash != record["patched_sha256"]
                and current_hash != record["clean_sha256"]
            )
        )

        if need_fresh:
            shutil.copy2(data_path, backup_file)
            ledger["bundles"][key] = {
                "bundle_name": bundle_name,
                "target_path": key,
                "backup_path": str(backup_file),
                "clean_sha256": current_hash,
                "patched_sha256": None,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            self.save(ledger)

        return backup_file

    def deploy_patched_bundle(self, built_bundle: Path, target_data_path: Path, bundle_name: str) -> None:
        """Backup original __data if needed, then atomically replace target __data."""
        self.ensure_clean_bundle_backup(target_data_path, bundle_name)
        tmp_target = target_data_path.with_name("__data.mod_tmp")
        shutil.copy2(built_bundle, tmp_target)
        tmp_target.replace(target_data_path)

        ledger = self.load()
        key = str(target_data_path.resolve())
        ledger["bundles"][key]["patched_sha256"] = sha256_file(target_data_path)
        ledger["bundles"][key]["updated_at"] = datetime.now(timezone.utc).isoformat()
        self.save(ledger)

    def revert_all_bundles(self) -> list[Path]:
        ledger = self.load()
        restored: list[Path] = []
        for key, rec in list(ledger.get("bundles", {}).items()):
            target = Path(rec["target_path"])
            backup = Path(rec["backup_path"])
            if backup.exists() and target.parent.exists():
                if target.exists() and rec.get("patched_sha256"):
                    cur_hash = sha256_file(target)
                    if cur_hash != rec["patched_sha256"] and cur_hash != rec["clean_sha256"]:
                        backup.unlink(missing_ok=True)
                        del ledger["bundles"][key]
                        continue
                tmp = target.with_name("__data.restore_tmp")
                shutil.copy2(backup, tmp)
                tmp.replace(target)
                restored.append(target)
            backup.unlink(missing_ok=True)
            del ledger["bundles"][key]

        self.save(ledger)
        return restored

    def active_summary(self) -> dict[str, Any]:
        if not self.ledger_path.exists():
            return {
                "is_modded": False,
                "patched_lang_files": [],
                "patched_bundles": [],
            }
        ledger = self.load()
        patched_langs = [
            Path(r["target_path"]).name
            for r in ledger.get("lang_files", {}).values()
            if r.get("patched_sha256")
        ]
        patched_bundles = [
            r.get("bundle_name", Path(r["target_path"]).name)
            for r in ledger.get("bundles", {}).values()
            if r.get("patched_sha256")
        ]
        return {
            "is_modded": bool(patched_langs or patched_bundles),
            "patched_lang_files": patched_langs,
            "patched_bundles": patched_bundles,
        }
