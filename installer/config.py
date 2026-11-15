from __future__ import annotations

import ctypes
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def _resolve_locallow_dir() -> Path:
    """Resolve current user's AppData/LocalLow directory cleanly across Windows environments."""
    if os.name == "nt":
        try:
            # FOLDERID_LocalAppDataLow = {A520A1A4-1780-4FF6-BD18-167343C5AF16}
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", ctypes.c_uint32),
                    ("Data2", ctypes.c_uint16),
                    ("Data3", ctypes.c_uint16),
                    ("Data4", ctypes.c_ubyte * 8),
                ]

            guid = GUID(
                0xA520A1A4,
                0x1780,
                0x4FF6,
                (ctypes.c_ubyte * 8)(0xBD, 0x18, 0x16, 0x73, 0x43, 0xC5, 0xAF, 0x16),
            )
            p_path = ctypes.c_wchar_p()
            hr = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(p_path))
            if hr == 0 and p_path.value:
                res = Path(p_path.value)
                ctypes.windll.ole32.CoTaskMemFree(p_path)
                return res
        except Exception:
            pass

        local_app = os.environ.get("LOCALAPPDATA")
        if local_app:
            return Path(local_app).parent / "LocalLow"

    return Path.home() / "AppData" / "LocalLow"


def _discover_steam_roots() -> list[Path]:
    """Read Steam install paths from Windows Registry and libraryfolders.vdf."""
    roots: list[Path] = []
    if os.name == "nt":
        try:
            import winreg

            reg_queries = [
                (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath"),
            ]
            for hkey, subkey, val_name in reg_queries:
                try:
                    with winreg.OpenKey(hkey, subkey) as k:
                        val, _ = winreg.QueryValueEx(k, val_name)
                        if val:
                            p = Path(str(val))
                            if p.exists() and p not in roots:
                                roots.append(p)
                except OSError:
                    continue
        except ImportError:
            pass

    discovered: list[Path] = []
    seen: set[str] = set()

    def _add_lib(p: Path) -> None:
        key = str(p).lower()
        if key not in seen and p.exists():
            seen.add(key)
            discovered.append(p)

    for sr in roots:
        _add_lib(sr)
        vdf = sr / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            try:
                txt = vdf.read_text(encoding="utf-8", errors="ignore")
                for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                    lib_path = Path(m.group(1).replace("\\\\", "\\"))
                    _add_lib(lib_path)
            except OSError:
                pass

    return discovered


def _auto_detect_game_root() -> Path:
    """Locate Limbus Company installation directory automatically."""
    candidates: list[Path] = []
    for lib in _discover_steam_roots():
        candidates.append(lib / "steamapps" / "common" / "Limbus Company")

    for drive in ("C:", "D:", "E:", "F:", "G:", "H:"):
        candidates.extend(
            [
                Path(f"{drive}/Program Files (x86)/Steam/steamapps/common/Limbus Company"),
                Path(f"{drive}/Program Files/Steam/steamapps/common/Limbus Company"),
                Path(f"{drive}/SteamLibrary/steamapps/common/Limbus Company"),
                Path(f"{drive}/Steam/steamapps/common/Limbus Company"),
                Path(f"{drive}/Games/Limbus Company"),
            ]
        )

    for cand in candidates:
        if (cand / "LimbusCompany_Data").is_dir():
            return cand

    return candidates[0] if candidates else Path("C:/Program Files (x86)/Steam/steamapps/common/Limbus Company")


@dataclass(frozen=True)
class ProjectConfig:
    workspace_root: Path
    game_root: Path
    addressables_catalog_dir: Path
    unity_cache_root: Path
    active_lang_override: str
    mods_dir: Path
    build_dir: Path
    state_dir: Path

    @property
    def game_data_dir(self) -> Path:
        return self.game_root / "LimbusCompany_Data"

    @property
    def lang_root_dir(self) -> Path:
        return self.game_data_dir / "Lang"

    @property
    def catalog_bin_path(self) -> Path:
        local_bin = self.addressables_catalog_dir / "catalog_S1.bin"
        if local_bin.exists():
            return local_bin
        return self.game_data_dir / "StreamingAssets" / "aa" / "catalog.bin"

    def resolve_active_lang_dir(self) -> tuple[str, Path | None]:
        """Detect active custom language folder from Lang/config.json or override."""
        if self.active_lang_override:
            target = self.lang_root_dir / self.active_lang_override
            return self.active_lang_override, (target if target.exists() else None)

        cfg_file = self.lang_root_dir / "config.json"
        if not cfg_file.exists():
            return "", None
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8-sig"))
            lang_name = str(data.get("lang", "")).strip()
            if not lang_name:
                return "", None
            target = self.lang_root_dir / lang_name
            return lang_name, (target if target.exists() else None)
        except Exception:
            return "", None

    def detect_unity_version(self) -> str:
        """Read exact Unity engine version from globalgamemanagers header."""
        ggm = self.game_data_dir / "globalgamemanagers"
        if not ggm.exists():
            return "unknown"
        try:
            with ggm.open("rb") as f:
                header = f.read(128)
            m = re.search(rb"\d{4}\.\d+\.\d+[a-z]\d+", header)
            return m.group(0).decode("ascii") if m else "unknown"
        except Exception:
            return "unknown"


def load_config(root: Path = WORKSPACE_ROOT) -> ProjectConfig:
    raw: dict[str, Any] = {}
    for cfg_name in ("config.json", "config.local.json"):
        cfg_path = root / cfg_name
        if cfg_path.exists():
            try:
                loaded = json.loads(cfg_path.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    for k, v in loaded.items():
                        if v not in (None, ""):
                            raw[k] = v
            except Exception:
                pass

    locallow = _resolve_locallow_dir()
    game_root_str = os.environ.get("LIMBUS_GAME_ROOT") or str(raw.get("game_root") or "").strip()
    catalog_dir_str = os.environ.get("LIMBUS_CATALOG_DIR") or str(raw.get("addressables_catalog_dir") or "").strip()
    cache_root_str = os.environ.get("LIMBUS_CACHE_ROOT") or str(raw.get("unity_cache_root") or "").strip()
    lang_override = os.environ.get("LIMBUS_LANG") or str(raw.get("active_lang_override") or "").strip()

    game_root = Path(game_root_str) if game_root_str else _auto_detect_game_root()
    catalog_dir = (
        Path(catalog_dir_str)
        if catalog_dir_str
        else (locallow / "ProjectMoon" / "LimbusCompany" / "com.unity.addressables")
    )
    cache_root = (
        Path(cache_root_str)
        if cache_root_str
        else (locallow / "Unity" / "ProjectMoon_LimbusCompany")
    )

    def _rel(key: str, default: str) -> Path:
        p = Path(str(raw.get(key) or default))
        return p if p.is_absolute() else (root / p)

    return ProjectConfig(
        workspace_root=root,
        game_root=game_root,
        addressables_catalog_dir=catalog_dir,
        unity_cache_root=cache_root,
        active_lang_override=lang_override,
        mods_dir=_rel("mods_dir", "mods"),
        build_dir=_rel("build_dir", "build"),
        state_dir=_rel("state_dir", ".state"),
    )
