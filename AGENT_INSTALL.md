# AGENT_INSTALL.md — AI Agent Automated Setup & Restore Guide

Welcome, fellow AI Agent!  
This repository contains a standalone visual skin mod (**Claude-Themed Rodion — Thumb Father**, Identity ID `10916`) for **Limbus Company** (PC / Steam official client, Unity `6000.3.12f1`), along with a lightweight, transparent CLI installer (`installer/`).  
*(Note: This release contains **ONLY** the `10916` skin assets and the minimal CLI installer/restorer. The Studio authoring UI is intentionally excluded.)*

Follow the steps below to safely back up the user's original game files and install (or restore) the skin on **Windows**.

---

## Safety & Integrity Rules (Must Read First)

1. **Close the game first**: Ensure `LimbusCompany.exe` is **not** running before running `apply` or `revert`.
2. **Never delete `.state/`**: Clean backups of the user's original `__data` AssetBundles (and any modified `Lang/*.json` files) are saved inside `<repo>/.state/`. **Keep this repository folder on disk** so the user can restore vanilla files anytime.
3. **Never write personal paths back to tracked files**: If manual path overrides are needed on a non-standard setup, copy `config.example.json` to `config.local.json` (which is gitignored).
4. **Do not tamper with game DLLs, BepInEx, or shared `UnitKeyword` tags**:
   - This installer uses **zero** BepInEx plugins, DLL injection, or memory tampering; it patches only `p10916` local Addressables cache (`__data`).
   - **Shared-Tag Hazard (`UnitKeyword_SPIDER_HOUSE`)**: In `Lang/<active>/UnitKeyword*.json`, `UnitKeyword_SPIDER_HOUSE` is **shared across all Spider House identities** (such as `10415` and `10916`). Patching it directly in the official client would overwrite the tag for every Spider House identity simultaneously, so it is **intentionally left vanilla** in `mod.json`. Do not add `unit_keywords` rules unless the user explicitly asks for a global override after reading `installer/lang_patcher.py`.

---

## Step 1: Verify Python 3.12+ & Create Virtual Environment

Check whether `.venv` already exists in the repo root; if not, locate Python `>=3.12` (`py -3` or `python`), create `.venv`, and install dependencies (`UnityPy>=1.25.0`, `lz4>=4.4.0`, `Pillow>=12.0.0`, `etcpak>=0.9.15`, `astc-encoder-py>=0.1.12`, `texture2ddecoder>=1.0.6`). Note that `etcpak` is required because several vanilla `10916` SD sprites use `DXT5`.

In **PowerShell**:

```powershell
# 1. Check Python >= 3.12
python --version

# 2. Create .venv if missing
if (!(Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}

# 3. Install required packages (if default PyPI is slow/blocked in CN, fallback to Tsinghua Tuna mirror)
.\.venv\Scripts\python.exe -m pip install "UnityPy>=1.25.0" "lz4>=4.4.0" "Pillow>=12.0.0" "etcpak>=0.9.15" "astc-encoder-py>=0.1.12" "texture2ddecoder>=1.0.6"
```

*(If PyPI times out in mainland China, retry with `-i https://pypi.tuna.tsinghua.edu.cn/simple`.)*

---

## Step 2: Run Environment & Path Auto-Discovery Check

Run:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m installer status
```

`installer/config.py` automatically detects:
- **Game Root**: via Windows Registry (`SteamPath` / `InstallPath`) + `steamapps/libraryfolders.vdf` + common drive paths (`C:`–`H:`).
- **LocalLow Cache & Catalog**: via Windows Known Folder (`FOLDERID_LocalAppDataLow`) -> `ProjectMoon\LimbusCompany\com.unity.addressables\catalog_S1.bin` and `Unity\ProjectMoon_LimbusCompany`.
- **Active Localization Folder**: via `LimbusCompany_Data/Lang/config.json`.

### Troubleshooting `status` Output
- **`Game Root [...] [MISSING]`**:
  Search the user's drives for `LimbusCompany.exe` (e.g. `Get-PSDrive -PSProvider FileSystem`), then create `config.local.json` from `config.example.json` and set `"game_root"` to the folder containing `LimbusCompany_Data`.
- **`Target 10916 [main]: NOT CACHED`**:
  The user has not viewed this Identity in-game yet, so Unity Addressables has not downloaded the `p10916` bundle into `LocalLow` cache. Ask the user to:
  1. Launch **Limbus Company** once,
  2. Open the Sinner / Identity screen (or Theatre / Dispensary) and view **Rodion — Thumb Father (10916)** once so assets cache locally,
  3. Close the game, and tell you to continue.
- **`Catalog Binary [MISSING]`**:
  Ensure the user has launched the game and logged in at least once on this Windows user account.

---

## Step 3: Install (Backup + Patch + Deploy)

*(Optional dry-run verification without touching `.state/` or real game files: run `.\.venv\Scripts\python.exe -X utf8 -m installer apply --build-only`.)*

Once `status` shows `Game Root [OK]`, `Catalog Binary [OK]`, and `Target 10916 [main]` as `READY` (with `LimbusCompany.exe` closed), run:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m installer apply
```

### Expected Healthy Output
You should see a summary line matching the `10916` asset inventory:
- `[Skin:limbus-rodion-thumb-father-10916-Claude] Installed & Deployed -> illust=4, sd_frames=39, fx_sprites=0, spine_tex=1, spine_assets=2`

*(Note: `10916` stores all combat SD frames as standalone sprites inside `p10916_assets_all_*.bundle` rather than a `SpriteAtlas`. The `39` custom combat SD frames are injected cleanly with Full-Quad margin expansion, `dead.png` stays vanilla, `thumb_father_rodion_idle` Spine assets/texture are replaced with automatic `pma:true` premultiplication, and all `4` U1 `10916_normal*` illustrations are updated. U3 artwork, voices, skill names/icons, and story remain vanilla.)*

---

## Step 4: How to Restore Vanilla Files (Uninstall)

If the user ever wants to uninstall the mod or prepare cleanly before a major game update, run (with the game closed):

```powershell
.\.venv\Scripts\python.exe -X utf8 -m installer revert
```

(Or tell the user they can simply double-click `restore_vanilla.cmd`.)

---

## Step 5: Wrap Up

Report clearly to the user:
1. What was installed (`4` U1 `10916_normal*` illustrations, `39` standalone combat SD frames with vanilla `dead.png`, and `thumb_father_rodion_idle` breathing Spine; U3, voices, skill names/icons, story, and shared `UnitKeyword_SPIDER_HOUSE` left vanilla).
2. Remind them **not to delete this folder** (or its `.state/` subfolder), and explain how to restore anytime (`restore_vanilla.cmd` or asking you to run `revert`).
