from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Unity 6 (6000.3.x) Binary Addressables Catalog bundle entry pattern
_BIN_CATALOG_PATTERN = re.compile(
    rb"([a-z0-9_./-]+_([0-9a-f]{32})\.bundle).{16,28}?\x20\x00\x00\x00([0-9a-f]{32})",
    re.DOTALL,
)


@dataclass(frozen=True)
class BundleRecord:
    bundle_filename: str
    top_folder_hash: str
    version_hash: str
    data_path: Path
    is_cached: bool
    size_bytes: int


def parse_unity6_catalog_bin(catalog_bin_path: Path, unity_cache_root: Path) -> list[BundleRecord]:
    """Parse Unity 6000.3 binary catalog_S1.bin without external dependencies."""
    if not catalog_bin_path.exists():
        raise FileNotFoundError(f"Binary catalog not found: {catalog_bin_path}")

    raw = catalog_bin_path.read_bytes()
    records: list[BundleRecord] = []
    seen: set[str] = set()

    for bfile_bytes, ver_hash_bytes, top_hash_bytes in _BIN_CATALOG_PATTERN.findall(raw):
        bname = bfile_bytes.decode("utf-8", errors="ignore")
        if bname in seen:
            continue
        seen.add(bname)

        top_h = top_hash_bytes.decode("ascii")
        ver_h = ver_hash_bytes.decode("ascii")
        data_file = unity_cache_root / top_h / ver_h / "__data"
        exists = data_file.exists()
        size = data_file.stat().st_size if exists else 0

        records.append(
            BundleRecord(
                bundle_filename=bname,
                top_folder_hash=top_h,
                version_hash=ver_h,
                data_path=data_file,
                is_cached=exists,
                size_bytes=size,
            )
        )

    return records
