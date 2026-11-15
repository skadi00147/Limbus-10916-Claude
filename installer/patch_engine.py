from __future__ import annotations

import copy
import json
import math
import re
import struct
import warnings
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageEnhance, ImageFilter
from PIL.Image import Transpose

from .catalog_index import BundleRecord, parse_unity6_catalog_bin
from .config import ProjectConfig
from .state_ledger import StateLedger, atomic_write_bytes

warnings.filterwarnings("ignore", message=".*No valid Unity version found.*")

UNITY_FALLBACK_VERSION = "6000.3.12f1"
UNITY_PATH_SP_RENDERER = 1257685109
UNITY_PATH_SP_EFFECT = 955408368
SD_EXPANSION_PAD = 256

# Opt-in per-personality external FX shadow sprite cloning specs (empty by default for 10916).
DEFAULT_FX_SHADOW_SPECS: dict[int, list[dict[str, Any]]] = {}

_STANDARD_SPRITE_CHANNELS = [
    {"stream": 0, "offset": 0, "format": 0, "dimension": 3},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 1, "offset": 0, "format": 0, "dimension": 2},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
    {"stream": 0, "offset": 0, "format": 0, "dimension": 0},
]


def _natural_sort_key(text: str) -> list[int | str]:
    return [int(tok) if tok.isdigit() else tok.lower() for tok in re.split(r"(\d+)", text)]


def _collect_local_pptrs(val: Any) -> list[int]:
    """Collect all same-file (m_FileID == 0) non-zero m_PathID references in a typetree structure."""
    refs: list[int] = []
    if isinstance(val, dict):
        if "m_PathID" in val and "m_FileID" in val:
            if int(val.get("m_FileID", 0)) == 0 and int(val.get("m_PathID", 0)) != 0:
                refs.append(int(val["m_PathID"]))
        for child in val.values():
            refs.extend(_collect_local_pptrs(child))
    elif isinstance(val, list):
        for item in val:
            refs.extend(_collect_local_pptrs(item))
    return refs


def _collect_appearance_clip_sources(env: Any, pid_str: str) -> dict[int, list[str]]:
    """Trace AnimationClips reachable from the personality's primary Appearance prefab (Animator + motionList)."""
    by_pid: dict[int, Any] = {int(o.path_id): o for o in env.objects}

    preferred_go_pids: set[int] = set()
    for obj in env.objects:
        if obj.type.name != "AssetBundle":
            continue
        ab_tree = obj.read_typetree()
        for cname, entry in ab_tree.get("m_Container") or []:
            low_c = str(cname).lower()
            if pid_str in low_c and ("appearance" in low_c or "prefab/sd" in low_c):
                asset_ref = entry.get("asset") or {}
                if int(asset_ref.get("m_FileID", 0)) == 0 and int(asset_ref.get("m_PathID", 0)) != 0:
                    preferred_go_pids.add(int(asset_ref["m_PathID"]))

    primary_apps: list[dict[str, Any]] = []
    fallback_apps: list[dict[str, Any]] = []
    for obj in env.objects:
        if obj.type.name != "MonoBehaviour":
            continue
        try:
            tree = obj.read_typetree()
        except Exception:
            continue
        if not isinstance(tree, dict) or "motionList" not in tree or "_anim" not in tree:
            continue
        go_pid = int((tree.get("m_GameObject") or {}).get("m_PathID", 0))
        go_obj = by_pid.get(go_pid)
        go_name = str(go_obj.read().m_Name) if go_obj is not None and go_obj.type.name == "GameObject" else ""
        if go_pid in preferred_go_pids or pid_str in go_name:
            primary_apps.append(tree)
        else:
            fallback_apps.append(tree)

    targets = primary_apps or fallback_apps
    clip_sources: dict[int, list[str]] = {}
    if not targets:
        return clip_sources

    def trace_from_root(root_pid: int, source_label: str) -> None:
        queue: list[int] = [root_pid]
        visited: set[int] = set()
        while queue:
            cur_pid = queue.pop(0)
            if cur_pid in visited or cur_pid == 0:
                continue
            visited.add(cur_pid)
            cur_obj = by_pid.get(cur_pid)
            if cur_obj is None:
                continue
            tname = cur_obj.type.name
            if tname == "AnimationClip":
                clip_sources.setdefault(cur_pid, []).append(source_label)
            elif tname in ("MonoBehaviour", "AnimatorController", "AnimatorOverrideController", "Animator"):
                try:
                    sub_tree = cur_obj.read_typetree()
                except Exception:
                    continue
                for npid in _collect_local_pptrs(sub_tree):
                    if npid not in visited:
                        queue.append(npid)

    for app_tree in targets:
        anim_ref = app_tree.get("_anim") or {}
        if int(anim_ref.get("m_FileID", 0)) == 0 and int(anim_ref.get("m_PathID", 0)) != 0:
            trace_from_root(int(anim_ref["m_PathID"]), "Animator")

        for motion in app_tree.get("motionList") or []:
            for tl_ref in motion.get("timelineAssets") or []:
                if int(tl_ref.get("m_FileID", 0)) != 0 or int(tl_ref.get("m_PathID", 0)) == 0:
                    continue
                tl_pid = int(tl_ref["m_PathID"])
                tl_obj = by_pid.get(tl_pid)
                tl_label = "Timeline"
                if tl_obj is not None:
                    try:
                        tl_label = str(tl_obj.read_typetree().get("m_Name") or tl_label)
                    except Exception:
                        pass
                trace_from_root(tl_pid, tl_label)

    return clip_sources


def _extract_sprite_mesh_info(
    sp: Any,
    upright_w: int,
    upright_h: int,
) -> tuple[list[list[float]], list[list[int]], float, float]:
    """Compute upright mesh polygon vertices, triangles, and true pivot inside sp.image bounds."""
    from UnityPy.helpers.MeshHelper import MeshHandler

    mesh = MeshHandler(sp.m_RD, sp.object_reader.version)
    mesh.process()
    if not mesh.m_Vertices:
        return [], [], round(float(sp.m_Pivot.x), 4), round(float(sp.m_Pivot.y), 4)

    min_x = min(x for x, _y, _z in mesh.m_Vertices)
    min_y = min(y for _x, y, _z in mesh.m_Vertices)
    factor = float(sp.m_PixelsToUnits or 100.0)
    verts_upright = [
        [round((x - min_x) * factor, 2), round(upright_h - (y - min_y) * factor, 2)]
        for x, y, _z in mesh.m_Vertices
    ]
    triangles = [list(tri) for sub in mesh.get_triangles() for tri in sub]
    piv_x = ((-min_x * factor) / upright_w) if upright_w > 0 else float(sp.m_Pivot.x)
    piv_y = ((-min_y * factor) / upright_h) if upright_h > 0 else float(sp.m_Pivot.y)
    return verts_upright, triangles, round(float(piv_x), 4), round(float(piv_y), 4)


def _decode_clip_sprite_timelines(
    env: Any,
    char_tags: list[str],
    pid_str: str,
    packed_sprite_pids: set[int],
    reachable_clip_sources: dict[int, list[str]] | None = None,
) -> dict[int, int]:
    """Scan AnimationClip timelines in memory to rank standalone Sprite appearances."""
    standalone_first_seen: dict[int, int] = {}
    order_counter = 0

    for obj in env.objects:
        if obj.type.name != "AnimationClip":
            continue
        tree = obj.read_typetree()
        cname = str(tree.get("m_Name") or "")
        cpid = int(obj.path_id)
        in_appearance = bool(reachable_clip_sources and cpid in reachable_clip_sources)
        if char_tags:
            if not any(cname.startswith(f"{tag}_") for tag in char_tags) and not in_appearance:
                continue
        elif reachable_clip_sources:
            if not in_appearance:
                continue
        elif pid_str not in cname:
            continue

        mc = tree.get("m_MuscleClip") or {}
        cbc = tree.get("m_ClipBindingConstant") or {}
        bindings = cbc.get("genericBindings") or []
        pptr_map = cbc.get("pptrCurveMapping") or []
        if not bindings or not pptr_map:
            continue

        curve_cursor = 0
        target_cidx: int | None = None
        fallback_cidx: int | None = None
        for b in bindings:
            tid = int(b.get("typeID", 0))
            attr = int(b.get("attribute", 0))
            slots = 4 if (tid == 4 and attr == 2) else (3 if (tid == 4 and attr in (1, 3, 4)) else 1)
            if b.get("isPPtrCurve") and tid == 212:
                bpath = int(b.get("path", 0))
                if bpath == UNITY_PATH_SP_RENDERER:
                    target_cidx = curve_cursor
                elif bpath != UNITY_PATH_SP_EFFECT and fallback_cidx is None:
                    fallback_cidx = curve_cursor
            curve_cursor += slots

        use_cidx = target_cidx if target_cidx is not None else fallback_cidx
        if use_cidx is None:
            continue

        sc = ((mc.get("m_Clip") or {}).get("data") or {}).get("m_StreamedClip") or {}
        u32s = sc.get("data") or []
        if not u32s:
            continue
        raw = struct.pack(f"<{len(u32s)}I", *u32s)

        pos = 0
        raw_len = len(raw)
        while pos + 8 <= raw_len:
            tval, nkeys = struct.unpack_from("<fI", raw, pos)
            pos += 8
            for _ in range(nkeys):
                if pos + 20 > raw_len:
                    break
                cidx, _c0, _c1, _c2, val = struct.unpack_from("<Iffff", raw, pos)
                pos += 20
                if cidx == use_cidx and tval < 1e10:
                    idx = int(round(val))
                    if 0 <= idx < len(pptr_map):
                        sp_pid = int(pptr_map[idx].get("m_PathID", 0))
                        if sp_pid != 0 and sp_pid not in packed_sprite_pids and sp_pid not in standalone_first_seen:
                            standalone_first_seen[sp_pid] = order_counter
                            order_counter += 1

    return standalone_first_seen


def _collect_sd_meta_in_memory(
    env: Any,
    obj_by_pid: dict[int, Any],
    pid_str: str,
    role: str,
) -> dict[str, dict[str, Any]]:
    """Collect SD Sprite dimension, pivot & variant inheritance metadata strictly in memory."""
    sd_meta: dict[str, dict[str, Any]] = {}
    char_tags: list[str] = []
    packed_sprite_pids: set[int] = set()

    for obj in env.objects:
        if obj.type.name != "SpriteAtlas":
            continue
        sa_tree = obj.read_typetree()
        sa_name = str(sa_tree.get("m_Name") or "")
        if pid_str not in sa_name:
            continue

        m_tag = re.match(rf"^{pid_str}_(.+)_SDAtlas$", sa_name)
        if m_tag and m_tag.group(1) not in char_tags:
            char_tags.append(m_tag.group(1))

        sa_rd_lookup: dict[tuple[tuple[Any, ...], Any], dict[str, Any]] = {}
        for pair in sa_tree.get("m_RenderDataMap") or []:
            rk = pair["first"] if isinstance(pair, dict) else pair[0]
            rv = pair["second"] if isinstance(pair, dict) else pair[1]
            sa_rd_lookup[_norm_render_data_key(rk)] = rv

        for sp_pptr in sa_tree.get("m_PackedSprites") or []:
            sp_pid = int(sp_pptr["m_PathID"])
            packed_sprite_pids.add(sp_pid)
            sp_obj = obj_by_pid.get(sp_pid)
            if sp_obj is None:
                continue
            sp_tree = sp_obj.read_typetree()
            sard = sa_rd_lookup.get(_norm_render_data_key(sp_tree["m_RenderDataKey"]))
            if sard is None:
                continue

            sp_name = str(sp_tree["m_Name"])
            rw = int(round(float(sard["textureRect"]["width"])))
            rh = int(round(float(sard["textureRect"]["height"])))
            sp_read = sp_obj.read()
            _, _, piv_x, piv_y = _extract_sprite_mesh_info(sp_read, rw, rh)
            sd_meta[sp_name] = {
                "name": sp_name,
                "primary_frame": None,
                "source_type": "atlas",
                "width": rw,
                "height": rh,
                "pivot_x": piv_x,
                "pivot_y": piv_y,
                "sprite_path_id": sp_pid,
                "texture_path_id": int(sard["texture"]["m_PathID"]),
                "bundle_role": role,
            }

    reachable_clip_sources = _collect_appearance_clip_sources(env, pid_str)
    standalone_first_seen = _decode_clip_sprite_timelines(
        env,
        char_tags,
        pid_str,
        packed_sprite_pids,
        reachable_clip_sources=reachable_clip_sources,
    )
    if standalone_first_seen:
        grouped_by_orig_name: dict[str, list[tuple[int, Any, dict[str, Any]]]] = {}
        for sp_pid in standalone_first_seen:
            sp_obj = obj_by_pid.get(sp_pid)
            if sp_obj is None or sp_obj.type.name != "Sprite":
                continue
            sp_tree = sp_obj.read_typetree()
            tex_pid = int(((sp_tree.get("m_RD") or {}).get("texture") or {}).get("m_PathID", 0))
            sp_name = str(sp_tree.get("m_Name") or "")
            if tex_pid == 0 or re.search(r"_e\d*$", sp_name):
                continue
            grouped_by_orig_name.setdefault(sp_name, []).append((sp_pid, sp_obj, sp_tree))

        suffixes = ["", "_b", "_c", "_d", "_e2"]
        for orig_name, candidates in grouped_by_orig_name.items():
            candidates.sort(
                key=lambda item: (
                    standalone_first_seen.get(item[0], 999999),
                    -int(
                        round(
                            float(item[2]["m_Rect"]["width"])
                            * float(item[2]["m_Rect"]["height"])
                        )
                    ),
                    item[0],
                )
            )
            for idx, (sp_pid, sp_obj, sp_tree) in enumerate(candidates):
                sfx = suffixes[idx] if idx < len(suffixes) else f"_v{idx + 1}"
                exp_name = f"{orig_name}{sfx}"
                primary_ref = orig_name if idx > 0 else None
                tr = (sp_tree.get("m_RD") or {}).get("textureRect") or sp_tree["m_Rect"]
                rw = int(round(float(tr["width"])))
                rh = int(round(float(tr["height"])))
                sp_read = sp_obj.read()
                _, _, piv_x, piv_y = _extract_sprite_mesh_info(sp_read, rw, rh)
                tex_pid = int(((sp_tree.get("m_RD") or {}).get("texture") or {}).get("m_PathID", 0))
                sd_meta[exp_name] = {
                    "name": exp_name,
                    "primary_frame": primary_ref,
                    "source_type": "standalone",
                    "width": rw,
                    "height": rh,
                    "pivot_x": piv_x,
                    "pivot_y": piv_y,
                    "sprite_path_id": sp_pid,
                    "texture_path_id": tex_pid,
                    "bundle_role": role,
                }

    return sd_meta


def find_personality_bundles(cfg: ProjectConfig, personality_id: int) -> dict[str, BundleRecord]:
    records = parse_unity6_catalog_bin(cfg.catalog_bin_path, cfg.unity_cache_root)
    pid_tag = f"p{personality_id}_"
    result: dict[str, BundleRecord] = {}
    for rec in records:
        if rec.bundle_filename.startswith(pid_tag):
            if "_sd_assets_all_" in rec.bundle_filename:
                result["sd"] = rec
            elif "_assets_all_" in rec.bundle_filename:
                result["main"] = rec
    return result


def _premultiply_alpha_rgba(im: Image.Image) -> Image.Image:
    """Premultiply RGB channels by alpha for Unity Spine shaders (_StraightAlphaInput=0)."""
    rgba = im.convert("RGBA")
    rgb = rgba.convert("RGB")
    alpha = rgba.getchannel("A")
    black = Image.new("RGB", rgba.size, (0, 0, 0))
    premult_rgb = Image.composite(rgb, black, alpha)
    r, g, b = premult_rgb.split()
    return Image.merge("RGBA", (r, g, b, alpha))


def _atlas_page_uses_pma(atlas_texts: list[str], tex_stem: str) -> bool:
    target_png = f"{tex_stem.lower()}.png"
    for txt in atlas_texts:
        cur_page_png: str | None = None
        cur_pma = False
        for raw_line in txt.splitlines():
            s = raw_line.strip()
            if not s:
                if cur_page_png == target_png:
                    return cur_pma
                cur_page_png = None
                cur_pma = False
                continue
            low = s.lower()
            if low.endswith(".png") and ":" not in s:
                if cur_page_png == target_png:
                    return cur_pma
                cur_page_png = Path(s).name.lower()
                cur_pma = False
            elif ":" in s and cur_page_png is not None:
                k, v = [p.strip().lower() for p in s.split(":", 1)]
                if k == "pma":
                    cur_pma = (v == "true")
        if cur_page_png == target_png:
            return cur_pma
    return False


def apply_image_adjustments(
    img: Image.Image,
    target_size: tuple[int, int],
    params: dict[str, Any] | None = None,
    pad: int = 0,
) -> Image.Image:
    img = img.convert("RGBA")
    tw, th = target_size
    p = params or {}

    scale = float(p.get("scale", 1.0))
    offset_x = int(round(float(p.get("offset_x", 0.0))))
    offset_y = int(round(float(p.get("offset_y", 0.0))))
    alpha_choke = int(round(float(p.get("alpha_choke", 0))))
    brightness = float(p.get("brightness", 1.0))
    contrast = float(p.get("contrast", 1.0))
    saturation = float(p.get("saturation", 1.0))

    fit_ratio = min(tw / max(1, img.width), th / max(1, img.height))
    final_w = max(1, int(round(img.width * fit_ratio * scale)))
    final_h = max(1, int(round(img.height * fit_ratio * scale)))
    if (final_w, final_h) != img.size:
        img = img.resize((final_w, final_h), Image.Resampling.LANCZOS)

    if abs(brightness - 1.0) > 1e-3 or abs(contrast - 1.0) > 1e-3 or abs(saturation - 1.0) > 1e-3:
        r, g, b, a = img.split()
        rgb = Image.merge("RGB", (r, g, b))
        if abs(brightness - 1.0) > 1e-3:
            rgb = ImageEnhance.Brightness(rgb).enhance(brightness)
        if abs(contrast - 1.0) > 1e-3:
            rgb = ImageEnhance.Contrast(rgb).enhance(contrast)
        if abs(saturation - 1.0) > 1e-3:
            rgb = ImageEnhance.Color(rgb).enhance(saturation)
        r, g, b = rgb.split()
        img = Image.merge("RGBA", (r, g, b, a))

    if alpha_choke > 0:
        steps = min(alpha_choke, 6)
        bordered = Image.new("RGBA", (img.width + 2, img.height + 2), (0, 0, 0, 0))
        bordered.paste(img, (1, 1))
        r, g, b, a = bordered.split()
        for _ in range(steps):
            a = a.filter(ImageFilter.MinFilter(3))
        img = Image.merge("RGBA", (r, g, b, a)).crop((1, 1, img.width + 1, img.height + 1))

    out_w = tw + 2 * pad
    out_h = th + 2 * pad
    paste_x = pad + (tw - final_w) // 2 + offset_x
    paste_y = pad + (th - final_h) // 2 + offset_y
    if pad == 0 and (final_w, final_h) == (tw, th) and paste_x == 0 and paste_y == 0:
        return img

    out = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    out.paste(img, (paste_x, paste_y), img)
    return out


def _norm_render_data_key(k: Any) -> tuple[tuple[Any, ...], Any]:
    first = k["first"] if isinstance(k, dict) else k[0]
    second = k["second"] if isinstance(k, dict) else k[1]
    f_tup = tuple(first.values()) if isinstance(first, dict) else tuple(first)
    return (f_tup, second)


def _trim_and_align_sd_quad(
    exp_img: Image.Image,
    base_size: tuple[int, int],
    orig_pivot: tuple[float, float],
    pad: int = SD_EXPANSION_PAD,
) -> tuple[Image.Image, int, int, float, float]:
    tw, th = base_size
    anchor_x_top = float(pad) + float(tw) * float(orig_pivot[0])
    anchor_y_top = float(pad) + float(th) * (1.0 - float(orig_pivot[1]))

    bbox = exp_img.getbbox()
    if bbox is None:
        bx0, by0, bx1, by1 = pad, pad, pad + tw, pad + th
    else:
        bx0 = max(0, bbox[0] - 2)
        by0 = max(0, bbox[1] - 2)
        bx1 = min(exp_img.width, bbox[2] + 2)
        by1 = min(exp_img.height, bbox[3] + 2)

    cw = max(4, ((bx1 - bx0 + 3) // 4) * 4)
    ch = max(4, ((by1 - by0 + 3) // 4) * 4)

    trimmed = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    crop_part = exp_img.crop((bx0, by0, bx1, by1))
    trimmed.paste(crop_part, (0, 0))

    pivot_px_x = anchor_x_top - float(bx0)
    pivot_px_y = float(ch) - (anchor_y_top - float(by0))
    return trimmed, cw, ch, pivot_px_x, pivot_px_y


def _write_quad_mesh_to_sprite_tree(
    sp_tree: dict[str, Any],
    cw: int,
    ch: int,
    pivot_px_x: float,
    pivot_px_y: float,
) -> float:
    factor = float(sp_tree.get("m_PixelsToUnits") or 100.0)
    x0 = -pivot_px_x / factor
    x1 = (float(cw) - pivot_px_x) / factor
    y0 = -pivot_px_y / factor
    y1 = (float(ch) - pivot_px_y) / factor

    v_bytes = struct.pack(
        "<12f",
        x0, y0, 0.0,
        x0, y1, 0.0,
        x1, y1, 0.0,
        x1, y0, 0.0,
    ) + (b"\x00" * 32)
    idx_list = list(struct.pack("<6H", 0, 1, 2, 0, 2, 3))

    sp_tree["m_IsPolygon"] = False
    sp_tree["m_Rect"] = {"x": 0.0, "y": 0.0, "width": float(cw), "height": float(ch)}
    sp_tree["m_Offset"] = {"x": pivot_px_x - cw * 0.5, "y": pivot_px_y - ch * 0.5}
    sp_tree["m_Pivot"] = {"x": pivot_px_x / cw, "y": pivot_px_y / ch}

    rd = sp_tree["m_RD"]
    rd["textureRect"] = {"x": 0.0, "y": 0.0, "width": float(cw), "height": float(ch)}
    rd["textureRectOffset"] = {"x": 0.0, "y": 0.0}
    rd["settingsRaw"] = 64
    rd["uvTransform"] = {"x": factor, "y": pivot_px_x, "z": factor, "w": pivot_px_y}

    vd = rd["m_VertexData"]
    vd["m_VertexCount"] = 4
    vd["m_Channels"] = [dict(ch_def) for ch_def in _STANDARD_SPRITE_CHANNELS]
    vd["m_DataSize"] = v_bytes

    rd["m_IndexBuffer"] = idx_list
    rd["m_SubMeshes"] = [
        {
            "firstByte": 0,
            "indexCount": 6,
            "topology": 0,
            "baseVertex": 0,
            "firstVertex": 0,
            "vertexCount": 4,
            "localAABB": {
                "m_Center": {"x": (x0 + x1) * 0.5, "y": (y0 + y1) * 0.5, "z": 0.0},
                "m_Extent": {"x": (x1 - x0) * 0.5, "y": (y1 - y0) * 0.5, "z": 0.0},
            },
        }
    ]
    return factor


class _ShelfRegion:
    def __init__(self, x0: int, y0: int, x1: int, y1: int) -> None:
        self.x0 = ((x0 + 3) // 4) * 4
        self.y0 = ((y0 + 3) // 4) * 4
        self.x1 = (x1 // 4) * 4
        self.y1 = (y1 // 4) * 4
        self.cur_y = self.y0
        self.shelves: list[list[int]] = []

    def allocate(self, w: int, h: int, gap: int = 4) -> tuple[int, int] | None:
        pw = ((w + gap + 3) // 4) * 4
        ph = ((h + gap + 3) // 4) * 4
        if self.x0 + w > self.x1 or self.y0 + h > self.y1:
            return None
        for sh in self.shelves:
            sy, sh_h, nx = sh
            if h <= sh_h and nx + w <= self.x1:
                sh[2] = min(self.x1, nx + pw)
                return (nx, sy)
        if self.cur_y + h <= self.y1:
            sy = self.cur_y
            self.shelves.append([sy, ph, min(self.x1, self.x0 + pw)])
            self.cur_y = min(self.y1, self.cur_y + ph)
            return (self.x0, sy)
        return None


def _crop_centered_fx_frame(
    edited_padded: Image.Image,
    base_size: tuple[int, int],
    target_canvas_size: tuple[int, int],
    pad: int = SD_EXPANSION_PAD,
) -> Image.Image:
    cx = float(pad) + float(base_size[0]) * 0.5
    cy = float(pad) + float(base_size[1]) * 0.5
    half_w = float(target_canvas_size[0]) * 0.5
    half_h = float(target_canvas_size[1]) * 0.5

    bbox = edited_padded.getbbox()
    if bbox is not None:
        half_w = max(half_w, cx - float(bbox[0]) + 2.0, float(bbox[2]) - cx + 2.0)
        half_h = max(half_h, cy - float(bbox[1]) + 2.0, float(bbox[3]) - cy + 2.0)

    cw = max(4, ((int(math.ceil(half_w * 2.0)) + 3) // 4) * 4)
    ch = max(4, ((int(math.ceil(half_h * 2.0)) + 3) // 4) * 4)

    out = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    src_x0 = int(round(cx - cw * 0.5))
    src_y0 = int(round(cy - ch * 0.5))
    ix0, iy0 = max(0, src_x0), max(0, src_y0)
    ix1, iy1 = min(edited_padded.width, src_x0 + cw), min(edited_padded.height, src_y0 + ch)
    if ix1 > ix0 and iy1 > iy0:
        out.paste(edited_padded.crop((ix0, iy0, ix1, iy1)), (ix0 - src_x0, iy0 - src_y0))
    return out


def _allocate_unused_pid(used_pids: set[int], seed: int) -> int:
    candidate = 10_000_000_000 + (abs(int(seed)) % 8_000_000_000_000)
    while candidate in used_pids or candidate == 0:
        candidate += 1
    used_pids.add(candidate)
    return candidate


def _clone_serialized_object(sf: Any, src_or: Any, new_pid: int) -> Any:
    from UnityPy.streams import EndianBinaryReader

    new_or = copy.copy(src_or)
    new_or.path_id = new_pid
    raw = bytes(src_or.get_raw_data())
    new_or.reader = EndianBinaryReader(raw, endian=src_or.reader.endian)
    new_or.byte_start = 0
    new_or.byte_size = len(raw)
    new_or.data = b""
    sf.objects[new_pid] = new_or
    return new_or


def _find_standalone_sprite_tex_template(
    env: Any,
    avoid_tex_pids: set[int],
) -> tuple[Any, Any] | None:
    objs_by_sf: dict[int, dict[int, Any]] = {}
    fallback: tuple[Any, Any] | None = None
    for obj in env.objects:
        if obj.type.name != "Sprite":
            continue
        sf = obj.assets_file
        sf_objs = objs_by_sf.setdefault(id(sf), {int(o.path_id): o for o in sf.objects.values()})
        sp_tree = obj.read_typetree()
        atlas_pid = int((sp_tree.get("m_SpriteAtlas") or {}).get("m_PathID", 0))
        tex_pptr = (sp_tree.get("m_RD") or {}).get("texture") or {}
        if atlas_pid != 0 or int(tex_pptr.get("m_FileID", 0)) != 0:
            continue
        tex_pid = int(tex_pptr.get("m_PathID", 0))
        tex_or = sf_objs.get(tex_pid)
        if tex_or is None:
            continue
        if tex_pid not in avoid_tex_pids:
            return (obj, tex_or)
        if fallback is None:
            fallback = (obj, tex_or)
    return fallback


def _patch_fx_uv_shadow_sprites(
    env: Any,
    specs: list[dict[str, Any]],
    sd_meta_by_name: dict[str, dict[str, Any]],
    resolve_edited_sd_frame: Any,
    custom_frames: dict[str, Path],
    avoid_tex_pids: set[int],
) -> list[str]:
    """Clone modded SD poses as local standalone Sprites and repoint external FX UVModule refs."""
    from UnityPy.enums import TextureFormat

    if not specs or not custom_frames:
        return []

    spec_by_ext_sp = {int(s["ext_sprite_pid"]): s for s in specs}
    ps_hits: list[tuple[Any, dict[str, Any]]] = []
    referenced_ext_sps: set[int] = set()
    for obj in env.objects:
        if obj.type.name != "ParticleSystem":
            continue
        ps_tree = obj.read_typetree()
        uv = ps_tree.get("UVModule") or {}
        if not uv.get("enabled"):
            continue
        matched = False
        for entry in uv.get("sprites") or []:
            sp_ref = entry.get("sprite") or {}
            sp_pid = int(sp_ref.get("m_PathID", 0))
            if int(sp_ref.get("m_FileID", 0)) != 0 and sp_pid in spec_by_ext_sp:
                referenced_ext_sps.add(sp_pid)
                matched = True
        if matched:
            ps_hits.append((obj, ps_tree))

    if not referenced_ext_sps:
        return []

    tmpl = _find_standalone_sprite_tex_template(env, avoid_tex_pids)
    if tmpl is None:
        return []
    tmpl_sp_or, tmpl_tex_or = tmpl
    target_sf = tmpl_sp_or.assets_file
    used_pids = {int(pid) for pid in target_sf.objects.keys()}

    ext_sp_to_new: dict[int, int] = {}
    ext_tex_to_new: dict[int, int] = {}
    replaced_names: list[str] = []

    for ext_sp_pid in sorted(referenced_ext_sps):
        spec = spec_by_ext_sp[ext_sp_pid]
        fname = str(spec["frame"])
        if fname not in custom_frames or fname not in sd_meta_by_name:
            continue
        fmeta = sd_meta_by_name[fname]
        base_sz = (int(fmeta["width"]), int(fmeta["height"]))
        edited_padded = resolve_edited_sd_frame(fname, base_sz)
        if edited_padded is None:
            continue

        csz = tuple(int(v) for v in spec["canvas_size"])
        fx_img = _crop_centered_fx_frame(edited_padded, base_sz, (csz[0], csz[1]))
        cw, ch = fx_img.size

        new_tex_pid = _allocate_unused_pid(used_pids, ext_sp_pid * 3 + 11)
        new_sp_pid = _allocate_unused_pid(used_pids, ext_sp_pid * 3 + 17)
        asset_name = str(spec.get("name") or f"FX_Mod_{fname}")

        nt_or = _clone_serialized_object(target_sf, tmpl_tex_or, new_tex_pid)
        nt_obj = nt_or.read()
        nt_obj.m_Name = asset_name
        nt_obj.set_image(fx_img, target_format=TextureFormat.RGBA32)
        nt_obj.save()

        ns_or = _clone_serialized_object(target_sf, tmpl_sp_or, new_sp_pid)
        ns_tree = ns_or.read_typetree()
        ns_tree["m_Name"] = asset_name
        ns_tree["m_PixelsToUnits"] = 100.0
        ns_tree["m_SpriteAtlas"] = {"m_FileID": 0, "m_PathID": 0}
        ns_tree["m_RD"]["texture"] = {"m_FileID": 0, "m_PathID": new_tex_pid}
        _write_quad_mesh_to_sprite_tree(ns_tree, cw, ch, float(cw) * 0.5, float(ch) * 0.5)
        ns_or.save_typetree(ns_tree)

        ext_sp_to_new[ext_sp_pid] = new_sp_pid
        ext_tex_pid = int(spec.get("ext_tex_pid") or 0)
        if ext_tex_pid:
            ext_tex_to_new[ext_tex_pid] = new_tex_pid
        replaced_names.append(f"{asset_name}({fname})")

    if not ext_sp_to_new:
        return []

    for ps_obj, ps_tree in ps_hits:
        changed = False
        for entry in (ps_tree.get("UVModule") or {}).get("sprites") or []:
            sp_ref = entry.get("sprite") or {}
            sp_pid = int(sp_ref.get("m_PathID", 0))
            if int(sp_ref.get("m_FileID", 0)) != 0 and sp_pid in ext_sp_to_new:
                entry["sprite"] = {"m_FileID": 0, "m_PathID": ext_sp_to_new[sp_pid]}
                changed = True
        if changed:
            ps_obj.save_typetree(ps_tree)

    for obj in target_sf.objects.values():
        if obj.type.name != "AssetBundle":
            continue
        ab_tree = obj.read_typetree()
        ab_changed = False
        for pptr in ab_tree.get("m_PreloadTable") or []:
            if int(pptr.get("m_FileID", 0)) == 0:
                continue
            ppid = int(pptr.get("m_PathID", 0))
            if ppid in ext_sp_to_new:
                pptr["m_FileID"] = 0
                pptr["m_PathID"] = ext_sp_to_new[ppid]
                ab_changed = True
            elif ppid in ext_tex_to_new:
                pptr["m_FileID"] = 0
                pptr["m_PathID"] = ext_tex_to_new[ppid]
                ab_changed = True
        if ab_changed:
            obj.save_typetree(ab_tree)

    return replaced_names


def build_and_deploy_mod(
    cfg: ProjectConfig,
    mod_dir: Path,
    ledger: StateLedger,
    deploy: bool = True,
) -> dict[str, Any]:
    """Pack skin PNGs and Spine assets into local LZ4 Unity AssetBundle(s) with backup protection."""
    import UnityPy
    from UnityPy.enums import TextureFormat
    from UnityPy.export.Texture2DConverter import get_image_from_texture2d

    UnityPy.config.FALLBACK_UNITY_VERSION = UNITY_FALLBACK_VERSION

    manifest = json.loads((mod_dir / "mod.json").read_text(encoding="utf-8"))
    pid = int(manifest["target_personality_id"])
    bundles = find_personality_bundles(cfg, pid)
    if not bundles:
        raise FileNotFoundError(
            f"Cannot locate bundles for personality {pid}. Make sure Limbus Company is installed and catalog_S1.bin exists."
        )

    for role, rec in bundles.items():
        if not rec.is_cached:
            raise FileNotFoundError(
                f"Local bundle cache missing ({rec.bundle_filename}). "
                f"Please launch Limbus Company once and view Identity ID {pid} in-game (Identity screen / combat) so the game caches its assets, then close the game and re-run."
            )

    illust_src_dir = mod_dir / "illust"
    sd_src_dir = mod_dir / "sd_frames"
    spine_src_dir = mod_dir / "spine"
    illust_params_map: dict[str, Any] = manifest.get("illust_params") or {}
    frame_params_map: dict[str, Any] = manifest.get("frame_params") or {}
    high_quality_rgba = bool(manifest.get("force_rgba32_atlas", False))

    custom_illusts = {p.stem: p for p in illust_src_dir.glob("*.png")} if illust_src_dir.exists() else {}
    custom_frames = {p.stem: p for p in sd_src_dir.glob("*.png")} if sd_src_dir.exists() else {}
    custom_spine_tex = {p.stem: p for p in spine_src_dir.glob("*.png")} if spine_src_dir.exists() else {}
    custom_spine_atlas = {p.name: p for p in spine_src_dir.glob("*.atlas")} if spine_src_dir.exists() else {}
    custom_spine_json = {p.stem: p for p in spine_src_dir.glob("*.json")} if spine_src_dir.exists() else {}

    summary: dict[str, Any] = {
        "personality_id": pid,
        "mod_name": mod_dir.name,
        "replaced_illusts": [],
        "replaced_sd_frames": [],
        "replaced_fx_sprites": [],
        "replaced_spine_textures": [],
        "replaced_spine_assets": [],
        "built_bundles": [],
        "deployed_bundles": [],
        "deployed": deploy,
    }

    if (
        not custom_illusts
        and not custom_frames
        and not custom_spine_tex
        and not custom_spine_atlas
        and not custom_spine_json
    ):
        return summary

    ro_ledger = None if deploy else StateLedger.read_only(cfg.state_dir)
    out_build_dir = cfg.build_dir / str(pid)
    out_build_dir.mkdir(parents=True, exist_ok=True)
    pid_str = str(pid)

    raw_fx_specs = manifest.get("fx_shadow_specs")
    if not isinstance(raw_fx_specs, list):
        raw_fx_specs = manifest.get("fx_shadow_sprites")
    fx_specs: list[dict[str, Any]] = list(DEFAULT_FX_SHADOW_SPECS.get(pid, []))
    if isinstance(raw_fx_specs, list):
        by_ext_sp = {int(s["ext_sprite_pid"]): s for s in fx_specs}
        for item in raw_fx_specs:
            by_ext_sp[int(item["ext_sprite_pid"])] = item
        fx_specs = list(by_ext_sp.values())

    for role, rec in bundles.items():
        if deploy:
            clean_src = ledger.ensure_clean_bundle_backup(rec.data_path, rec.bundle_filename)
        else:
            assert ro_ledger is not None
            existing_clean = ro_ledger.get_clean_bundle_backup_if_exists(rec.data_path)
            clean_src = existing_clean if existing_clean is not None else rec.data_path

        env = UnityPy.load(clean_src.read_bytes())
        bundle_modified = False
        obj_by_pid: dict[int, Any] = {int(o.path_id): o for o in env.objects}

        sd_meta_by_name = _collect_sd_meta_in_memory(env, obj_by_pid, pid_str, role) if custom_frames else {}
        resolved_sd_cache: dict[str, Image.Image | None] = {}

        def resolve_edited_sd_frame(frame_name: str, target_size: tuple[int, int]) -> Image.Image | None:
            if frame_name in resolved_sd_cache:
                cached = resolved_sd_cache[frame_name]
                return cached.copy() if cached is not None else None

            tw, th = target_size
            if frame_name in custom_frames:
                with Image.open(custom_frames[frame_name]) as user_im:
                    edited = apply_image_adjustments(
                        user_im, (tw, th), frame_params_map.get(frame_name), pad=SD_EXPANSION_PAD
                    )
                resolved_sd_cache[frame_name] = edited
                return edited.copy()

            fmeta = sd_meta_by_name.get(frame_name)
            prim_name = fmeta.get("primary_frame") if fmeta else None
            if prim_name and prim_name in custom_frames and prim_name in sd_meta_by_name:
                pmeta = sd_meta_by_name[prim_name]
                pw, ph = int(pmeta["width"]), int(pmeta["height"])
                prim_edited = resolve_edited_sd_frame(prim_name, (pw, ph))
                if prim_edited is not None:
                    p_ax = SD_EXPANSION_PAD + float(pmeta["pivot_x"]) * pw
                    p_ay = SD_EXPANSION_PAD + (1.0 - float(pmeta["pivot_y"])) * ph
                    t_ax = SD_EXPANSION_PAD + float(fmeta["pivot_x"]) * tw
                    t_ay = SD_EXPANSION_PAD + (1.0 - float(fmeta["pivot_y"])) * th
                    out_w = tw + 2 * SD_EXPANSION_PAD
                    out_h = th + 2 * SD_EXPANSION_PAD
                    derived = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
                    paste_x = int(round(t_ax - p_ax))
                    paste_y = int(round(t_ay - p_ay))
                    derived.paste(prim_edited, (paste_x, paste_y), prim_edited)
                    if frame_name in frame_params_map:
                        fp = frame_params_map.get(frame_name) or {}
                        sc = float(fp.get("scale", 1.0))
                        ox = int(round(float(fp.get("offset_x", 0.0))))
                        oy = int(round(float(fp.get("offset_y", 0.0))))
                        if abs(sc - 1.0) > 1e-4 or ox != 0 or oy != 0:
                            sw = max(1, int(round(out_w * sc)))
                            sh = max(1, int(round(out_h * sc)))
                            scaled = derived.resize((sw, sh), Image.Resampling.LANCZOS)
                            adj = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
                            adj.paste(scaled, ((out_w - sw) // 2 + ox, (out_h - sh) // 2 + oy), scaled)
                            derived = adj
                    resolved_sd_cache[frame_name] = derived
                    return derived.copy()

            resolved_sd_cache[frame_name] = None
            return None

        standalone_by_tex_pid: dict[int, dict[str, Any]] = {
            int(f["texture_path_id"]): f
            for f in sd_meta_by_name.values()
            if f.get("source_type") == "standalone" and f.get("bundle_role") == role
        }

        bundle_atlas_texts: dict[str, str] = {}
        text_name_counts: Counter[str] = Counter()
        if custom_spine_tex or custom_spine_atlas or custom_spine_json:
            for obj in env.objects:
                if obj.type.name != "TextAsset":
                    continue
                ta = obj.read()
                text_name_counts[ta.m_Name] += 1
                if ta.m_Name.endswith(".atlas"):
                    if ta.m_Name in custom_spine_atlas:
                        bundle_atlas_texts[ta.m_Name] = custom_spine_atlas[ta.m_Name].read_text(encoding="utf-8")
                    else:
                        bundle_atlas_texts[ta.m_Name] = (
                            ta.m_Script
                            if isinstance(ta.m_Script, str)
                            else bytes(ta.m_Script).decode("utf-8", errors="ignore")
                        )
            for aname, apath in custom_spine_atlas.items():
                bundle_atlas_texts.setdefault(aname, apath.read_text(encoding="utf-8"))
        effective_atlas_texts = list(bundle_atlas_texts.values())

        if custom_spine_atlas or custom_spine_json:
            for obj in env.objects:
                if obj.type.name != "TextAsset":
                    continue
                ta = obj.read()
                tname = ta.m_Name
                src_text_path: Path | None = None
                if tname in custom_spine_atlas and text_name_counts[tname] == 1:
                    src_text_path = custom_spine_atlas[tname]
                elif tname in custom_spine_json and text_name_counts[tname] == 1:
                    src_text_path = custom_spine_json[tname]
                elif tname.endswith(".json") and tname[:-5] in custom_spine_json and text_name_counts[tname] == 1:
                    src_text_path = custom_spine_json[tname[:-5]]

                if src_text_path is None:
                    continue
                ta.m_Script = src_text_path.read_text(encoding="utf-8")
                ta.save()
                bundle_modified = True
                summary["replaced_spine_assets"].append(src_text_path.name)

        for obj in env.objects:
            if obj.type.name != "Texture2D":
                continue

            if custom_frames and obj.path_id in standalone_by_tex_pid:
                fmeta = standalone_by_tex_pid[obj.path_id]
                fname = fmeta["name"]
                base_sz = (int(fmeta["width"]), int(fmeta["height"]))
                edited_sd = resolve_edited_sd_frame(fname, base_sz)
                if edited_sd is not None:
                    trimmed, cw, ch, ppx, ppy = _trim_and_align_sd_quad(
                        edited_sd,
                        base_sz,
                        (float(fmeta["pivot_x"]), float(fmeta["pivot_y"])),
                    )
                    tex = obj.read()
                    target_fmt = (
                        TextureFormat.RGBA32
                        if high_quality_rgba
                        else TextureFormat(int(tex.m_TextureFormat))
                    )
                    tex.set_image(trimmed, target_format=target_fmt)
                    tex.save()

                    sp_obj = obj_by_pid.get(int(fmeta["sprite_path_id"]))
                    if sp_obj is not None:
                        sp_tree = sp_obj.read_typetree()
                        _write_quad_mesh_to_sprite_tree(sp_tree, cw, ch, ppx, ppy)
                        sp_obj.save_typetree(sp_tree)

                    bundle_modified = True
                    summary["replaced_sd_frames"].append(fname)
                continue

            if not custom_illusts and not custom_spine_tex:
                continue

            tex = obj.read()
            tname = tex.m_Name
            if tname in custom_illusts:
                with Image.open(custom_illusts[tname]) as raw_im:
                    adjusted = apply_image_adjustments(
                        raw_im,
                        (tex.m_Width, tex.m_Height),
                        illust_params_map.get(tname),
                    )
                tex.set_image(adjusted, target_format=TextureFormat.RGBA32)
                tex.save()
                bundle_modified = True
                summary["replaced_illusts"].append(tname)
            elif tname in custom_spine_tex:
                with Image.open(custom_spine_tex[tname]) as spine_im:
                    raw_im = spine_im.convert("RGBA")
                if tname in illust_params_map:
                    raw_im = apply_image_adjustments(raw_im, raw_im.size, illust_params_map.get(tname))
                if _atlas_page_uses_pma(effective_atlas_texts, tname):
                    raw_im = _premultiply_alpha_rgba(raw_im)
                tex.set_image(raw_im, target_format=TextureFormat.RGBA32)
                tex.save()
                bundle_modified = True
                summary["replaced_spine_textures"].append(tname)

        if custom_frames:
            for obj in env.objects:
                if obj.type.name != "SpriteAtlas":
                    continue
                sa_tree = obj.read_typetree()
                if str(pid) not in str(sa_tree.get("m_Name") or ""):
                    continue

                sa_rd_lookup: dict[tuple[tuple[Any, ...], Any], dict[str, Any]] = {}
                all_atlas_tids: set[int] = set()
                for pair in sa_tree.get("m_RenderDataMap") or []:
                    rk = pair["first"] if isinstance(pair, dict) else pair[0]
                    rv = pair["second"] if isinstance(pair, dict) else pair[1]
                    sa_rd_lookup[_norm_render_data_key(rk)] = rv
                    tid_val = int(rv["texture"]["m_PathID"])
                    if tid_val != 0:
                        all_atlas_tids.add(tid_val)

                retained_counts: Counter[int] = Counter()
                pending_frames: list[dict[str, Any]] = []

                for sp_pptr in sa_tree.get("m_PackedSprites") or []:
                    sp_pid = int(sp_pptr["m_PathID"])
                    sp_obj = obj_by_pid.get(sp_pid)
                    if sp_obj is None:
                        continue
                    sp_tree = sp_obj.read_typetree()
                    sard = sa_rd_lookup.get(_norm_render_data_key(sp_tree["m_RenderDataKey"]))
                    if sard is None:
                        continue

                    orig_tid = int(sard["texture"]["m_PathID"])
                    rw = int(round(float(sard["textureRect"]["width"])))
                    rh = int(round(float(sard["textureRect"]["height"])))
                    sp_name = str(sp_tree["m_Name"])

                    upright_edited = resolve_edited_sd_frame(sp_name, (rw, rh))
                    if upright_edited is None:
                        retained_counts[orig_tid] += 1
                        continue

                    sp_read = sp_obj.read()
                    _, _, piv_x, piv_y = _extract_sprite_mesh_info(sp_read, rw, rh)
                    trimmed, cw, ch, ppx, ppy = _trim_and_align_sd_quad(upright_edited, (rw, rh), (piv_x, piv_y))
                    pending_frames.append(
                        {
                            "name": sp_name,
                            "sp_obj": sp_obj,
                            "sp_tree": sp_tree,
                            "sard": sard,
                            "trimmed": trimmed,
                            "cw": cw,
                            "ch": ch,
                            "ppx": ppx,
                            "ppy": ppy,
                        }
                    )

                if not pending_frames:
                    continue

                tex_readers: dict[int, Any] = {}
                orig_sizes: dict[int, tuple[int, int]] = {}
                for tid in all_atlas_tids:
                    tobj = obj_by_pid[tid].read()
                    tex_readers[tid] = tobj
                    orig_sizes[tid] = (int(tobj.m_Width), int(tobj.m_Height))

                ordered_tids = sorted(
                    all_atlas_tids,
                    key=lambda t: (retained_counts[t] > 0, orig_sizes[t][0] * orig_sizes[t][1], t),
                )

                bins_state: dict[int, dict[str, Any]] = {}

                def ensure_bin_initialized(tid: int) -> dict[str, Any]:
                    if tid in bins_state:
                        return bins_state[tid]
                    ow, oh = orig_sizes[tid]
                    tobj = tex_readers[tid]
                    if retained_counts[tid] == 0:
                        bw, bh = max(2048, ow), max(2048, oh)
                        canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
                        regions = [_ShelfRegion(0, 0, bw, bh)]
                    else:
                        orig_unflipped = get_image_from_texture2d(tobj, False).convert("RGBA")
                        bw, bh = max(2048, ow), max(2048, oh)
                        canvas = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
                        canvas.paste(orig_unflipped, (0, 0))
                        regions = []
                        if ow + 8 < bw:
                            regions.append(_ShelfRegion(ow + 8, 0, bw, oh))
                        if oh + 8 < bh:
                            regions.append(_ShelfRegion(0, oh + 8, bw, bh))
                    st = {"tid": tid, "canvas": canvas, "regions": regions, "dirty": False}
                    bins_state[tid] = st
                    return st

                def expand_bin_capacity(st: dict[str, Any]) -> bool:
                    cur_w, cur_h = st["canvas"].size
                    if cur_h < 4096:
                        new_w, new_h = cur_w, 4096
                        new_c = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
                        new_c.paste(st["canvas"], (0, 0))
                        st["canvas"] = new_c
                        st["regions"].append(_ShelfRegion(0, cur_h + 8, new_w, new_h))
                        return True
                    if cur_w < 4096:
                        new_w, new_h = 4096, 4096
                        new_c = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
                        new_c.paste(st["canvas"], (0, 0))
                        st["canvas"] = new_c
                        st["regions"].append(_ShelfRegion(cur_w + 8, 0, new_w, new_h))
                        return True
                    return False

                pending_frames.sort(key=lambda it: (-it["ch"], -it["cw"], it["name"]))

                for item in pending_frames:
                    cw, ch = item["cw"], item["ch"]
                    placed_tid: int | None = None
                    placed_xy: tuple[int, int] | None = None

                    for tid in ordered_tids:
                        st = ensure_bin_initialized(tid)
                        for reg in st["regions"]:
                            xy = reg.allocate(cw, ch, gap=4)
                            if xy is not None:
                                placed_tid = tid
                                placed_xy = xy
                                break
                        if placed_xy is not None:
                            break

                    if placed_xy is None:
                        for tid in ordered_tids:
                            st = ensure_bin_initialized(tid)
                            while expand_bin_capacity(st):
                                xy = st["regions"][-1].allocate(cw, ch, gap=4)
                                if xy is not None:
                                    placed_tid = tid
                                    placed_xy = xy
                                    break
                            if placed_xy is not None:
                                break

                    if placed_tid is None or placed_xy is None:
                        raise RuntimeError(f"Atlas bin overflow while packing {item['name']} ({cw}x{ch})")

                    rx, ry = placed_xy
                    st = bins_state[placed_tid]
                    patch_unflipped = item["trimmed"].transpose(Transpose.FLIP_TOP_BOTTOM)
                    st["canvas"].paste(patch_unflipped, (rx, ry))
                    st["dirty"] = True

                    sp_tree = item["sp_tree"]
                    ppx, ppy = item["ppx"], item["ppy"]
                    factor = _write_quad_mesh_to_sprite_tree(sp_tree, cw, ch, ppx, ppy)
                    item["sp_obj"].save_typetree(sp_tree)

                    sard = item["sard"]
                    sard["texture"] = {"m_FileID": 0, "m_PathID": placed_tid}
                    sard["textureRect"] = {
                        "x": float(rx),
                        "y": float(ry),
                        "width": float(cw),
                        "height": float(ch),
                    }
                    sard["textureRectOffset"] = {"x": 0.0, "y": 0.0}
                    sard["atlasRectOffset"] = {
                        "x": float(rx) + (ppx - cw * 0.5),
                        "y": float(ry) + (ppy - ch * 0.5),
                    }
                    sard["uvTransform"] = {
                        "x": factor,
                        "y": float(rx) + ppx,
                        "z": factor,
                        "w": float(ry) + ppy,
                    }
                    sard["settingsRaw"] = 65
                    summary["replaced_sd_frames"].append(item["name"])

                obj.save_typetree(sa_tree)

                for tid, st in bins_state.items():
                    if not st["dirty"]:
                        continue
                    tobj = tex_readers[tid]
                    upright_atlas = st["canvas"].transpose(Transpose.FLIP_TOP_BOTTOM)
                    target_fmt = (
                        TextureFormat.RGBA32
                        if high_quality_rgba
                        else TextureFormat(int(tobj.m_TextureFormat))
                    )
                    tobj.set_image(upright_atlas, target_format=target_fmt)
                    tobj.save()
                    bundle_modified = True

        if custom_frames and fx_specs:
            avoid_tids = set(standalone_by_tex_pid.keys())
            fx_replaced = _patch_fx_uv_shadow_sprites(
                env,
                fx_specs,
                sd_meta_by_name,
                resolve_edited_sd_frame,
                custom_frames,
                avoid_tids,
            )
            if fx_replaced:
                summary["replaced_fx_sprites"].extend(fx_replaced)
                bundle_modified = True

        if bundle_modified:
            built_file = out_build_dir / f"{role}.__data"
            packed_bytes = env.file.save(packer="lz4")
            atomic_write_bytes(built_file, packed_bytes)
            summary["built_bundles"].append(str(built_file))
            if deploy:
                ledger.deploy_patched_bundle(built_file, rec.data_path, rec.bundle_filename)
                summary["deployed_bundles"].append(str(rec.data_path))

    return summary
