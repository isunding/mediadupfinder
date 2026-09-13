#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mediadupfinder.py — 媒体文件查重工具（按元数据分组）

分组规则：
  强候选：大小完全相同 + 时长误差 ≤ 1 秒
  中候选：文件名相似 + 分辨率/码率相同
          或 文件名相同 + 分辨率/码率不同
  弱候选：时长接近 + 分辨率相同 + 大小不同

用法：
  python mediadupfinder.py /path/to/folder
  python mediadupfinder.py /path/to/folder -o result.json
  python mediadupfinder.py --drives G-U
  python mediadupfinder.py --drives G-U --min-size-mb 100 --exclude-dir CHN
"""

__version__ = "0.3.4"

import argparse
import json
import os
import re
import string
import sys
import time
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

try:
    from pymediainfo import MediaInfo
except ImportError:
    sys.stderr.write(
        "错误：缺少 pymediainfo 库。\n"
        "请运行：pip install pymediainfo\n"
        "并确保系统已安装 MediaInfo 命令行工具：\n"
        "  Windows: https://mediaarea.net/en/MediaInfo/Download/Windows\n"
        "  macOS:   brew install mediainfo\n"
        "  Linux:   sudo apt install mediainfo\n"
    )
    sys.exit(1)


# ---------- 盘符工具 ----------
def get_drives(start='G', end='U'):
    """生成从 start 到 end 的所有存在盘符（Windows），返回字符串路径列表"""
    if sys.platform != "win32":
        print("错误：--drives 仅支持 Windows 系统")
        sys.exit(1)
    drives = []
    start, end = start.upper(), end.upper()
    for letter in string.ascii_uppercase:
        if start <= letter <= end:
            p = f"{letter}:\\"
            if os.path.exists(p):
                drives.append(p)
    return drives


# ---------- 支持的文件类型 ----------
VIDEO_EXTS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".m2ts", ".3gp", ".rmvb",
}
AUDIO_EXTS = {
    ".mp3", ".flac", ".wav", ".aac", ".m4a", ".ogg",
    ".wma", ".opus", ".ape", ".alac", ".aiff",
}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


# ---------- 数值安全转换 ----------
def _to_int(v, default=0):
    try:
        return int(float(str(v).replace(" ", "").replace(",", "")))
    except (TypeError, ValueError):
        return default


# ---------- 元数据提取 ----------
def extract_metadata(path):
    """用 MediaInfo 读取文件头，返回 (meta_dict, None)；失败返回 (None, error_string)。"""
    try:
        mi = MediaInfo.parse(path)
        data = mi.to_data()
    except Exception as e:
        return None, f"parse_error: {e.__class__.__name__}"

    tracks = data.get("tracks", [])
    if not tracks:
        return None, "no_tracks"

    general = video = audio = None
    for t in tracks:
        tt = t.get("track_type")
        if tt == "General":
            general = t
        elif tt == "Video":
            if video is None:
                video = t
        elif tt == "Audio":
            if audio is None:
                audio = t
        if general and video and audio:
            break
    if general is None:
        general = {}

    size = _to_int(general.get("file_size"), 0)
    duration_ms = _to_int(general.get("duration"), 0)

    if not size:
        return None, "missing_file_size"
    if not duration_ms:
        return None, "missing_duration"

    width = _to_int(video.get("width"), 0) if video else 0
    height = _to_int(video.get("height"), 0) if video else 0
    v_bitrate = _to_int(video.get("bit_rate"), 0) if video else 0
    a_bitrate = _to_int(audio.get("bit_rate"), 0) if audio else 0

    if not v_bitrate and not a_bitrate:
        general_br = _to_int(general.get("overall_bit_rate") or general.get("bit_rate"), 0)
        if general_br:
            v_bitrate = general_br
        elif duration_ms > 0 and size > 0:
            est_total = int(size * 8 / (duration_ms / 1000.0))
            if video and audio:
                v_bitrate = int(est_total * 0.85)
                a_bitrate = est_total - v_bitrate
            elif video:
                v_bitrate = est_total
            elif audio:
                a_bitrate = est_total

    if width and height:
        resolution = f"{width}x{height}"
    elif video is None and audio is not None:
        resolution = "audio"
    else:
        resolution = "unknown"

    return {
        "path": path,
        "name": os.path.basename(path),
        "size": size,
        "duration": round(duration_ms / 1000.0, 3),
        "width": width,
        "height": height,
        "resolution": resolution,
        "video_bitrate": v_bitrate,
        "audio_bitrate": a_bitrate,
        "format": general.get("format") or "",
    }, None


def scan_folder(roots, min_size_bytes=0, exclude_dir_keywords=None,
                progress_every=500, workers=None):
    """
    递归扫描多个根目录，返回 (元数据列表, 失败列表[{path, reason}], 扫描的根列表)
    使用线程池并行解析媒体元数据。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    BATCH = 2000

    try:
        from tqdm import tqdm as _tqdm
        _HAS_TQDM = True
    except ImportError:
        _HAS_TQDM = False

    if workers is None or workers < 1:
        workers = min(16, (os.cpu_count() or 4) * 2)

    if exclude_dir_keywords is None:
        exclude_dir_keywords = []
    exclude_keywords = [k.upper() for k in exclude_dir_keywords if k]

    scanned_roots = []
    start_time = time.time()
    drive_files = defaultdict(list)

    def _parse(p):
        meta, err = extract_metadata(p)
        return meta, p, err

    for root in roots:
        if not os.path.exists(root):
            continue
        scanned_roots.append(root)
        candidates = []

        for dirpath, dirnames, filenames in os.walk(
            root, topdown=True, onerror=lambda e: None
        ):
            if exclude_keywords:
                dirnames[:] = [
                    d for d in dirnames
                    if not any(k in d.upper() for k in exclude_keywords)
                ]

            for fname in filenames:
                ext = os.path.splitext(fname)[1].lower()
                if ext not in MEDIA_EXTS:
                    continue
                try:
                    full = os.path.join(dirpath, fname)
                    if os.path.getsize(full) < min_size_bytes:
                        continue
                except OSError:
                    continue
                candidates.append(full)

        if not candidates:
            continue

        drive_files[root] = candidates

    drive_total = len(scanned_roots)

    for idx, root in enumerate(scanned_roots, 1):
        print(f"[{idx}/{drive_total}] 扫描盘符: {root}  "
              f"({len(drive_files[root])} 个候选文件, 线程数: {workers})")

    metas = []
    failed = []
    count = 0
    failed_count = 0

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for idx, root in enumerate(scanned_roots, 1):
            candidates = drive_files.get(root, [])
            if not candidates:
                continue

            drive_ok = 0
            drive_start = time.time()
            drive_fail = 0

            for bi in range(0, len(candidates), BATCH):
                batch = candidates[bi:bi + BATCH]
                futures = {pool.submit(_parse, p): p for p in batch}

                iterator = as_completed(futures)
                if _HAS_TQDM:
                    iterator = _tqdm(
                        iterator,
                        total=len(batch),
                        desc=f"  {os.path.basename(root.rstrip(os.sep)) or root}",
                        leave=False,
                        unit="f",
                    )

                for fut in iterator:
                    meta, path_str, err = fut.result()
                    if meta is None:
                        failed.append({"path": path_str, "reason": err or "unknown"})
                        failed_count += 1
                        drive_fail += 1
                    else:
                        metas.append(meta)
                        count += 1
                        drive_ok += 1
                        if not _HAS_TQDM and count % progress_every == 0:
                            elapsed = time.time() - start_time
                            fps = count / elapsed if elapsed > 0 else 0
                            print(f"  已读取 {count} 个文件 | "
                                  f"{fps:.1f} 文件/秒 | "
                                  f"失败 {failed_count}")

            drive_elapsed = time.time() - drive_start
            drive_fps = drive_ok / drive_elapsed if drive_elapsed > 0 else 0
            print(f"  盘符完成: {root}  共 {drive_ok} 个文件"
                  + (f"  {drive_fail} 失败" if drive_fail else "")
                  + f"  耗时 {drive_elapsed:.1f}s  ({drive_fps:.1f} 文件/秒)\n")

    total_elapsed = time.time() - start_time
    print(f"共读取 {count} 个文件  失败 {failed_count}  "
          f"总耗时 {total_elapsed:.1f}s  "
          f"({count / total_elapsed:.1f} 文件/秒)")
    return metas, failed, scanned_roots


# ---------- 文件名归一化 ----------
_COPY_PATTERNS = [
    re.compile(r"\s*[\(\[]\s*\d+\s*[\)\]]\s*$"),
    re.compile(r"\s*[-_ ]?\s*(copy|副本|拷贝)\s*\d*\s*$", re.IGNORECASE),
]
_RES_MARKERS = re.compile(
    r"\b(1080p|720p|480p|2160p|4k|8k|hd|fhd|uhd|sd|"
    r"hdr|h265|h264|hevc|x264|x265|avc|10bit|8bit)\b",
    re.IGNORECASE,
)


@lru_cache(maxsize=65536)
def normalize_name(name: str, strip_res: bool = False) -> str:
    """去扩展名、副本标记、可忽略后缀、统一分隔符、小写；可选去掉分辨率标记。"""
    stem = os.path.splitext(name)[0].lower()

    for pat in _COPY_PATTERNS:
        stem = pat.sub("", stem)

    prev = None
    while prev != stem:
        prev = stem
        stem = re.sub(r'[-_ ](u|uc)\s*$', '', stem, flags=re.IGNORECASE)

    if strip_res:
        stem = _RES_MARKERS.sub("", stem)

    stem = re.sub(r"[\s_\-]+", " ", stem).strip()
    return stem


def name_similarity(a: str, b: str, threshold: float = 0.8) -> float:
    """返回两个字符串的相似度（0~1）。若能安全判断必然低于 threshold，直接返回 0。"""
    la, lb = len(a), len(b)
    if la == 0 and lb == 0:
        return 1.0
    if abs(la - lb) > (1.0 - threshold) * (la + lb) + 1:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


# ---------- 分组逻辑 ----------
def _pair_key(a, b):
    return tuple(sorted([a["path"], b["path"]]))


def find_strong_candidates(files, tol_sec: float = 1.0, claimed_pairs=None):
    """强候选：大小完全相同 + 时长误差 ≤ tol_sec（并查集传递闭包）。"""
    groups = []
    by_size = defaultdict(list)
    for f in files:
        by_size[f["size"]].append(f)

    for _, items in by_size.items():
        if len(items) < 2:
            continue

        n = len(items)
        parent = list(range(n))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            rx, ry = find(x), find(y)
            if rx != ry:
                parent[rx] = ry

        items_sorted = sorted(items, key=lambda x: x["duration"])
        for i in range(n):
            for j in range(i + 1, n):
                if items_sorted[j]["duration"] - items_sorted[i]["duration"] > tol_sec:
                    break
                union(i, j)

        clusters = defaultdict(list)
        for i in range(n):
            clusters[find(i)].append(items_sorted[i])

        for members in clusters.values():
            if len(members) < 2:
                continue
            if claimed_pairs is not None:
                for i in range(len(members)):
                    for j in range(i + 1, len(members)):
                        claimed_pairs.add(_pair_key(members[i], members[j]))
            groups.append({
                "reason": "大小相同 + 时长相近",
                "files": members,
            })
    return groups


def find_mid_candidates(files, sim_threshold: float = 0.8,
                        strong_tol_sec: float = 1.0, claimed_pairs=None):
    """中候选：两种子情况。"""
    groups = []
    used_pairs = set()

    # 子情况 A：文件名相似 + 分辨率/码率相同
    by_res_bitrate = defaultdict(list)
    for f in files:
        if f["resolution"] == "audio":
            key = ("audio", f["audio_bitrate"])
        else:
            key = (f["resolution"], f["video_bitrate"])
        by_res_bitrate[key].append(f)

    for _, items in by_res_bitrate.items():
        if len(items) < 2:
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                pk = _pair_key(a, b)
                if claimed_pairs is not None and pk in claimed_pairs:
                    continue
                if pk in used_pairs:
                    continue
                if a["size"] == b["size"] and abs(a["duration"] - b["duration"]) <= strong_tol_sec:
                    continue
                na = a["_norm_strip"] if a["_norm_strip"] is not None else normalize_name(a["name"], strip_res=True)
                nb = b["_norm_strip"] if b["_norm_strip"] is not None else normalize_name(b["name"], strip_res=True)
                sim = name_similarity(na, nb, threshold=sim_threshold)
                if sim >= sim_threshold:
                    used_pairs.add(pk)
                    if claimed_pairs is not None:
                        claimed_pairs.add(pk)
                    groups.append({
                        "reason": "文件名相似 + 分辨率/码率相同",
                        "files": [a, b],
                    })

    # 子情况 B：文件名相同 + 分辨率/码率不同
    by_name = defaultdict(list)
    for f in files:
        key = f["_norm_strip"] if f["_norm_strip"] is not None else normalize_name(f["name"], strip_res=True)
        by_name[key].append(f)

    for _, items in by_name.items():
        if len(items) < 2:
            continue
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                a, b = items[i], items[j]
                pk = _pair_key(a, b)
                if claimed_pairs is not None and pk in claimed_pairs:
                    continue
                if pk in used_pairs:
                    continue
                same_res = a["resolution"] == b["resolution"]
                if not same_res:
                    same_br = False
                elif a["resolution"] == "audio":
                    same_br = a["audio_bitrate"] == b["audio_bitrate"]
                else:
                    same_br = a["video_bitrate"] == b["video_bitrate"]
                if same_res and same_br:
                    continue
                used_pairs.add(pk)
                if claimed_pairs is not None:
                    claimed_pairs.add(pk)
                diffs = []
                if not same_res:
                    diffs.append("分辨率不同")
                if not same_br:
                    diffs.append("码率不同")
                groups.append({
                    "reason": "文件名相同 + " + " / ".join(diffs),
                    "files": [a, b],
                })

    return groups


def find_weak_candidates(files, tol_sec: float = 2.0,
                         strong_tol_sec: float = 1.0, claimed_pairs=None):
    """弱候选：时长接近 + 分辨率相同 + 大小不同（且未被强候选覆盖）。"""
    groups = []
    used_pairs = set()

    by_res = defaultdict(list)
    for f in files:
        by_res[f["resolution"]].append(f)

    for _, items in by_res.items():
        if len(items) < 2:
            continue
        items_sorted = sorted(items, key=lambda x: x["duration"])
        for i in range(len(items_sorted)):
            for j in range(i + 1, len(items_sorted)):
                a, b = items_sorted[i], items_sorted[j]
                if b["duration"] - a["duration"] > tol_sec:
                    break
                if a["size"] == b["size"]:
                    if abs(a["duration"] - b["duration"]) <= strong_tol_sec:
                        continue
                    size_note = "大小相同"
                else:
                    size_note = "大小不同"
                pk = _pair_key(a, b)
                if claimed_pairs is not None and pk in claimed_pairs:
                    continue
                if pk in used_pairs:
                    continue
                used_pairs.add(pk)
                if claimed_pairs is not None:
                    claimed_pairs.add(pk)
                groups.append({
                    "reason": "时长相近 + 分辨率相同 + " + size_note,
                    "files": [a, b],
                })
    return groups


# ---------- 建议保留 ----------
def suggest_keep(group_files):
    """建议保留：分辨率 > 总码率 > 文件大小（大的优先）。"""
    def score(f):
        return (
            f["width"] * f["height"],
            f["video_bitrate"] + f["audio_bitrate"],
            f["size"],
        )
    return max(group_files, key=score)["path"]


# ---------- 结果打包与输出 ----------
def _median_size(files):
    sizes = sorted(f["size"] for f in files)
    n = len(sizes)
    if n == 0:
        return 0
    if n % 2 == 1:
        return sizes[n // 2]
    return (sizes[n // 2 - 1] + sizes[n // 2]) // 2


def build_result(roots, strong, mid, weak, failed, total_scanned):
    def pack(groups, label):
        out = []
        for i, g in enumerate(groups):
            files = g["files"]
            median = _median_size(files)
            wasted = (len(files) - 1) * median
            out.append({
                "group_id": f"{label}_{i:04d}",
                "reason": g["reason"],
                "file_count": len(files),
                "median_size": median,
                "wasted_bytes": wasted,
                "suggested_keep": suggest_keep(files),
                "files": [
                    {
                        "path": f["path"],
                        "name": f["name"],
                        "size": f["size"],
                        "size_mb": round(f["size"] / 1024 / 1024, 2),
                        "duration": f["duration"],
                        "resolution": f["resolution"],
                        "video_bitrate": f["video_bitrate"],
                        "audio_bitrate": f["audio_bitrate"],
                        "format": f["format"],
                    }
                    for f in files
                ],
            })
        out.sort(key=lambda x: x["wasted_bytes"], reverse=True)
        for i, g in enumerate(out):
            g["group_id"] = f"{label}_{i:04d}"
        return out

    return {
        "scan_time": datetime.now().isoformat(timespec="seconds"),
        "roots": roots,
        "total_scanned": total_scanned,
        "failed_files": failed,
        "strong_candidates": pack(strong, "strong"),
        "mid_candidates": pack(mid, "mid"),
        "weak_candidates": pack(weak, "weak"),
    }


def print_summary(result, max_print: int = 20):
    print()
    print("=" * 64)
    print(f"扫描完成：{result['total_scanned']} 个文件")
    total_wasted = sum(
        g["wasted_bytes"]
        for key in ("strong_candidates", "mid_candidates", "weak_candidates")
        for g in result[key]
    )
    print(f"预计可释放空间：{total_wasted / 1024 / 1024 / 1024:.2f} GB")
    print(f"强候选组：{len(result['strong_candidates'])}")
    print(f"中候选组：{len(result['mid_candidates'])}")
    print(f"弱候选组：{len(result['weak_candidates'])}")
    if result["failed_files"]:
        print(f"无法读取：{len(result['failed_files'])} 个文件")
    print("=" * 64)

    for label, key in [("强候选", "strong_candidates"),
                       ("中候选", "mid_candidates"),
                       ("弱候选", "weak_candidates")]:
        groups = result[key]
        if not groups:
            continue
        print(f"\n【{label}】共 {len(groups)} 组  (按浪费空间降序)")
        for idx, g in enumerate(groups[:max_print], 1):
            wasted_mb = g["wasted_bytes"] / 1024 / 1024
            print(f"  #{idx} [{g['group_id']}] 浪费 {wasted_mb:.1f}MB  "
                  f"× {g['file_count']} 个文件  — {g['reason']}")
            for f in g["files"]:
                mark = "★保留" if f["path"] == g["suggested_keep"] else "      "
                print(f"     {mark}  {f['name']}  "
                      f"({f['size_mb']}MB, {f['duration']}s, {f['resolution']})")
        if len(groups) > max_print:
            print(f"  ... 还有 {len(groups) - max_print} 组，详见 JSON")


# ---------- CSV 导出 ----------
def export_csv(result, csv_path):
    """将结果导出为 CSV：每行一个文件，包含 group_id、wasted 等列。"""
    import csv as _csv

    rows = []
    for cat, key in [("strong", "strong_candidates"),
                     ("mid", "mid_candidates"),
                     ("weak", "weak_candidates")]:
        for g in result[key]:
            keep_path = g["suggested_keep"]
            for f in g["files"]:
                rows.append({
                    "category": cat,
                    "group_id": g["group_id"],
                    "wasted_mb": round(g["wasted_bytes"] / 1024 / 1024, 2),
                    "file_count": g["file_count"],
                    "suggested_keep": f["path"] == keep_path,
                    "name": f["name"],
                    "path": f["path"],
                    "size_mb": f["size_mb"],
                    "duration_s": f["duration"],
                    "resolution": f["resolution"],
                    "video_bitrate": f["video_bitrate"],
                    "audio_bitrate": f["audio_bitrate"],
                    "format": f["format"],
                })

    fieldnames = [
        "category", "group_id", "wasted_mb", "file_count", "suggested_keep",
        "name", "path", "size_mb", "duration_s", "resolution",
        "video_bitrate", "audio_bitrate", "format",
    ]
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fp:
        writer = _csv.DictWriter(fp, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    total_failed = len(result.get("failed_files", []))
    if total_failed:
        fail_path = Path(csv_path).with_name(Path(csv_path).stem + "_failed.csv")
        with open(fail_path, "w", encoding="utf-8-sig", newline="") as fp:
            w = _csv.DictWriter(fp, fieldnames=["path", "reason"])
            w.writeheader()
            w.writerows(result["failed_files"])
        print(f"失败文件列表已写入：{fail_path.resolve()}")


# ---------- HTML 导出 ----------
def export_html(result, html_path):
    """将结果导出为带内联样式的单页 HTML。"""

    def _esc(s):
        return (str(s).replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))

    total_wasted = sum(
        g["wasted_bytes"]
        for key in ("strong_candidates", "mid_candidates", "weak_candidates")
        for g in result[key]
    )

    parts = []
    parts.append("""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>媒体查重结果</title>
<style>
body { font-family: Segoe UI, Consolas, sans-serif; margin: 24px; background: #f8f9fa; }
h1 { margin-bottom: 4px; }
.meta { color: #555; font-size: 14px; margin-bottom: 20px; }
h2 { border-bottom: 2px solid #333; padding-bottom: 4px; margin-top: 32px; }
.group { border: 1px solid #ddd; border-radius: 6px; padding: 12px 16px; margin: 10px 0; background: #fff; }
.group-head { font-weight: bold; margin-bottom: 8px; color: #333; }
.group-head .gid { color: #888; margin-right: 8px; }
.group-head .wasted { color: #c0392b; margin-left: 8px; }
.group-head .reason { color: #555; margin-left: 8px; font-weight: normal; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 4px 8px; text-align: left; border-bottom: 1px solid #eee; }
th { background: #f0f0f0; }
tr.keep td { background: #e8f5e9; font-weight: bold; }
.badge { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; margin-right: 6px; }
.badge-strong { background: #ffcdd2; color: #b71c1c; }
.badge-mid { background: #ffe0b2; color: #e65100; }
.badge-weak { background: #fff9c4; color: #f57f17; }
</style></head><body>""")

    parts.append(f"<h1>媒体查重结果</h1>")
    parts.append(
        f'<div class="meta">扫描时间：{_esc(result["scan_time"])}  |  '
        f'扫描文件数：{result["total_scanned"]}  |  '
        f'预计可释放：{total_wasted / 1024 / 1024 / 1024:.2f} GB</div>'
    )

    cat_label = {
        "strong_candidates": ("强候选", "badge-strong"),
        "mid_candidates": ("中候选", "badge-mid"),
        "weak_candidates": ("弱候选", "badge-weak"),
    }

    for key, (label, cls) in cat_label.items():
        groups = result[key]
        parts.append(
            f'<h2><span class="badge {cls}">{label}</span>'
            f' 共 {len(groups)} 组</h2>'
        )
        if not groups:
            parts.append("<p><em>无</em></p>")
            continue

        for g in groups:
            wasted_mb = g["wasted_bytes"] / 1024 / 1024
            parts.append('<div class="group">')
            parts.append(
                f'<div class="group-head">'
                f'<span class="gid">[{_esc(g["group_id"])}]</span>'
                f'<span class="wasted">浪费 {wasted_mb:.1f}MB</span>'
                f'<span class="reason">— {_esc(g["reason"])}  (共 {g["file_count"]} 个文件)</span>'
                f'</div>'
            )
            parts.append(
                "<table><thead><tr>"
                "<th>#</th><th>建议</th><th>文件名</th><th>路径</th>"
                "<th>大小(MB)</th><th>时长(s)</th><th>分辨率</th>"
                "<th>视频码率</th><th>音频码率</th><th>格式</th>"
                "</tr></thead><tbody>"
            )
            keep_path = g["suggested_keep"]
            for idx, f in enumerate(g["files"], 1):
                keep = f["path"] == keep_path
                cls_row = ' class="keep"' if keep else ""
                keep_mark = "★保留" if keep else ""
                parts.append(
                    f'<tr{cls_row}>'
                    f"<td>{idx}</td><td>{keep_mark}</td>"
                    f"<td>{_esc(f['name'])}</td>"
                    f"<td>{_esc(f['path'])}</td>"
                    f"<td>{f['size_mb']}</td>"
                    f"<td>{f['duration']}</td>"
                    f"<td>{_esc(f['resolution'])}</td>"
                    f"<td>{f['video_bitrate']}</td>"
                    f"<td>{f['audio_bitrate']}</td>"
                    f"<td>{_esc(f['format'])}</td>"
                    f"</tr>"
                )
            parts.append("</tbody></table></div>")

    if result.get("failed_files"):
        parts.append(f'<h2>读取失败 ({len(result["failed_files"])})</h2><ul>')
        for item in result["failed_files"]:
            parts.append(f"<li>{_esc(item['path'])} — {_esc(item['reason'])}</li>")
        parts.append("</ul>")

    parts.append("</body></html>")

    Path(html_path).write_text("".join(parts), encoding="utf-8")


# ---------- 主程序 ----------
def main():
    parser = argparse.ArgumentParser(description="媒体文件查重工具（按元数据）")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    src_group = parser.add_mutually_exclusive_group(required=True)
    src_group.add_argument("folder", nargs="?", help="要扫描的文件夹")
    src_group.add_argument("--drives", default=None, help="盘符范围，如 G-U，表示扫描 G: 到 U: 的所有硬盘")
    parser.add_argument("-o", "--output", default=None,
                        help="输出 JSON 路径（默认 dup_result_<时间戳>.json，避免覆盖）")
    parser.add_argument("--csv", default=None, help="CSV 输出路径（默认与 -o 同名，扩展名 .csv）")
    parser.add_argument("--html", default=None, help="HTML 输出路径（默认与 -o 同名，扩展名 .html）")
    parser.add_argument("--no-csv", action="store_true", help="跳过 CSV 输出")
    parser.add_argument("--no-html", action="store_true", help="跳过 HTML 输出")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印摘要，不写任何结果文件")
    parser.add_argument("--min-size-mb", type=float, default=100, help="忽略小于该大小(MB)的文件，默认 100")
    parser.add_argument("--duration-tol", type=float, default=1.0, help="强候选时长容差（秒，默认 1.0）")
    parser.add_argument("--weak-duration-tol", type=float, default=2.0, help="弱候选时长容差（秒，默认 2.0）")
    parser.add_argument("--name-sim", type=float, default=0.8, help="中候选文件名相似度阈值（默认 0.8）")
    parser.add_argument("--exclude-dir", action="append", default=None,
                        help="忽略名称包含该关键字的文件夹（可多次指定，默认 CHN；"
                             "传 --exclude-dir '' 则不排除任何目录）")
    parser.add_argument("--workers", type=int, default=None,
                        help="线程池并发数（默认 min(16, cpu*2)）")
    args = parser.parse_args()

    if args.output is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        args.output = f"dup_result_{ts}.json"

    roots = []
    if args.drives is not None:
        if not args.drives.strip():
            print("错误：--drives 不能为空字符串")
            sys.exit(1)
        parts = args.drives.split('-')
        if len(parts) != 2:
            print("错误：--drives 格式应为 G-U")
            sys.exit(1)
        start, end = parts[0].strip().upper(), parts[1].strip().upper()
        roots = get_drives(start, end)
        if not roots:
            print(f"错误：{start}: 到 {end}: 之间没有任何可用盘符")
            sys.exit(1)
        print(f"检测到盘符：{', '.join(str(r) for r in roots)}")
    elif args.folder is not None:
        folder = Path(args.folder).expanduser().resolve()
        if not folder.exists():
            print(f"错误：路径不存在 — {folder}")
            sys.exit(1)
        if not folder.is_dir():
            print(f"错误：不是文件夹 — {folder}")
            sys.exit(1)
        roots = [folder]
    else:
        print("错误：请指定文件夹或 --drives")
        sys.exit(1)

    min_size = int(args.min_size_mb * 1024 * 1024)

    exclude_list = args.exclude_dir if args.exclude_dir is not None else ["CHN"]
    if exclude_list == [""]:
        exclude_list = []

    if args.workers is not None:
        if args.workers < 1:
            print(f"错误：--workers 必须 ≥ 1（收到 {args.workers}）")
            sys.exit(1)
        print(f"使用指定线程数：{args.workers}")

    metas, failed, scanned_roots = scan_folder(
        roots,
        min_size_bytes=min_size,
        exclude_dir_keywords=exclude_list,
        workers=args.workers,
    )
    print(f"成功读取 {len(metas)} 个文件，失败 {len(failed)} 个")
    if failed:
        reason_counts = defaultdict(int)
        for item in failed:
            reason_counts[item["reason"]] += 1
        print("  失败原因分布：")
        for reason, cnt in sorted(reason_counts.items(), key=lambda x: -x[1]):
            print(f"    {reason}: {cnt}")

    for f in metas:
        f["_norm_strip"] = normalize_name(f["name"], strip_res=True)

    claimed_pairs = set()

    print("正在分组...")
    strong = find_strong_candidates(metas, tol_sec=args.duration_tol, claimed_pairs=claimed_pairs)
    mid = find_mid_candidates(metas, sim_threshold=args.name_sim,
                              strong_tol_sec=args.duration_tol, claimed_pairs=claimed_pairs)
    weak = find_weak_candidates(metas, tol_sec=args.weak_duration_tol,
                                strong_tol_sec=args.duration_tol, claimed_pairs=claimed_pairs)

    result = build_result(scanned_roots, strong, mid, weak, failed, len(metas))

    print_summary(result)

    if args.dry_run:
        print("\n[dry-run] 跳过所有文件输出。")
        return

    out_path = Path(args.output)
    with out_path.open("w", encoding="utf-8") as fp:
        json.dump(result, fp, ensure_ascii=False, indent=2)
    print(f"\nJSON 已写入：{out_path.resolve()}")

    if not args.no_csv:
        csv_path = args.csv or str(out_path.with_suffix(".csv"))
        export_csv(result, csv_path)
        print(f"CSV 已写入：{Path(csv_path).resolve()}")

    if not args.no_html:
        html_path = args.html or str(out_path.with_suffix(".html"))
        export_html(result, html_path)
        print(f"HTML 已写入：{Path(html_path).resolve()}")


if __name__ == "__main__":
    main()