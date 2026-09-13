#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web.py — 媒体查重 Web 后端

启动:  python web.py [--port 5000] [--allow-root D:\\] [--auth user:pass]
浏览器打开: http://localhost:5000

运行期文件（首次启动自动创建，旧版散落在根目录的文件会自动迁移）:
  logs/    扫描日志 scan_YYYYMMDD.log（S3）
  history/ 任务历史 scan_history.json（P6）
  config/  元数据缓存 .mdf_cache.json（B5）、单实例锁 .web.lock（S2）
  扫描结果 dup_result_<任务ID>.json/.csv/.html 仍写入项目根目录

API:
  POST   /api/start                启动扫描任务
  POST   /api/pause                暂停当前任务
  POST   /api/resume               继续当前任务
  POST   /api/cancel               取消当前任务（保留已解析的部分结果）
  GET    /api/progress             SSE 实时进度推送
  GET    /api/status               查询任务状态
  POST   /api/preview              启动采样预览（B3）
  GET    /api/preview_status       查询预览状态
  GET    /api/result               最近一次扫描结果 JSON
  GET    /api/download/<filename>  下载结果文件
  GET    /api/download_latest/<ext>下载最近任务的 json/csv/html
  GET    /api/history              历史任务列表（P6）
  GET    /api/history/<id>         单个历史任务元信息
  GET    /api/history/<id>/result  历史任务结果 JSON
  DELETE /api/history/<id>         删除历史记录
  GET    /api/thumb                用 FFmpeg 抽帧生成缩略图（R2）
  POST   /api/open                 在文件管理器中打开/定位文件（R3）
"""

import argparse
import atexit
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import string
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, Response, send_from_directory
from flask_cors import CORS

import mediadupfinder as mdf

BASE_DIR = Path(__file__).parent
RESULT_DIR = BASE_DIR                                    # 静态资源与扫描结果
LOG_DIR = BASE_DIR / "logs"                              # 扫描日志
HISTORY_DIR = BASE_DIR / "history"                       # 任务历史
CONFIG_DIR = BASE_DIR / "config"                         # 运行配置 / 元数据缓存 / 单实例锁
THUMB_DIR = BASE_DIR / "_thumbs"                         # 缩略图缓存

RESULT_FILE = RESULT_DIR / "dup_result_web.json"        # 兼容旧版固定文件名
HISTORY_FILE = HISTORY_DIR / "scan_history.json"
LOCK_FILE = CONFIG_DIR / ".web.lock"
CACHE_FILE = CONFIG_DIR / ".mdf_cache.json"
LOG_FILE = LOG_DIR / f"scan_{datetime.now():%Y%m%d}.log"
HISTORY_LIMIT = 50


def _ensure_dirs():
    """创建运行时目录，并把旧版散落在根目录的文件迁移进去。"""
    for d in (LOG_DIR, HISTORY_DIR, CONFIG_DIR):
        d.mkdir(parents=True, exist_ok=True)
    legacy = [
        (BASE_DIR / "scan_history.json", HISTORY_FILE),
        (BASE_DIR / ".web.lock", LOCK_FILE),
        (BASE_DIR / ".mdf_cache.json", CACHE_FILE),
    ]
    legacy += [(p, LOG_DIR / p.name) for p in BASE_DIR.glob("scan_*.log")]
    for old, new in legacy:
        if old.exists() and not new.exists():
            try:
                old.replace(new)
            except OSError:
                pass


_ensure_dirs()

STAGE_KEYS = ["enumerate", "parse", "grouping", "saving"]

ALLOW_ROOTS = []
AUTH = None

app = Flask(__name__, static_folder=None)
CORS(app)

logger = logging.getLogger("mdf.web")

# ---------- 全局状态 ----------
_ACTIVE_CONTROL = None
_progress_listeners = []
_state_lock = threading.RLock()
_history = []


def _blank_stages():
    return {k: {"pct": 0, "status": "pending"} for k in STAGE_KEYS}


task_state = {
    "running": False,
    "task_id": None,
    "start_time": None,
    "phase": "idle",
    "current": 0,
    "total": 0,
    "message": "空闲",
    "progress_pct": 0,
    "done": False,
    "error": None,
    "result_path": None,
    "result_name": None,
    "stats": None,
    "roots": [],
    "paused": False,
    "cancelled": False,
    "rate": 0.0,
    "eta_seconds": None,
    "elapsed": 0,
    "current_root": None,
    "current_file": None,
    "stages": _blank_stages(),
}

_preview_state = {
    "running": False,
    "done": False,
    "error": None,
    "message": "",
    "info": None,
}


# ---------- 日志（S3） ----------
def _setup_logging():
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)


# ---------- 单实例锁（S2） ----------
def _pid_alive(pid):
    if pid <= 0:
        return False
    try:
        if sys.platform == "win32":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if h:
                ctypes.windll.kernel32.CloseHandle(h)
                return True
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _acquire_lock(lock_path):
    if lock_path.exists():
        try:
            pid = int((lock_path.read_text(encoding="utf-8").strip() or "0"))
        except Exception:
            pid = 0
        if _pid_alive(pid):
            print(f"错误：已有 web.py 在运行（PID {pid}）。")
            print(f"如确认没有，请删除锁文件后重试：{lock_path}")
            return False
    lock_path.write_text(str(os.getpid()), encoding="utf-8")
    return True


def _release_lock(lock_path):
    try:
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass


# ---------- 防休眠（Q4） ----------
class KeepAwake:
    """扫描期间阻止系统休眠：Windows 用 SetThreadExecutionState，Linux 用 systemd-inhibit。"""

    ES_CONTINUOUS = 0x80000000
    ES_SYSTEM_REQUIRED = 0x00000001

    def __init__(self, enabled=True):
        self.enabled = enabled
        self._proc = None

    def __enter__(self):
        if not self.enabled:
            return self
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(
                    self.ES_CONTINUOUS | self.ES_SYSTEM_REQUIRED)
            elif sys.platform.startswith("linux"):
                import shutil
                import subprocess
                if shutil.which("systemd-inhibit"):
                    self._proc = subprocess.Popen(
                        ["systemd-inhibit", "--what=idle:sleep",
                         "--why=媒体查重扫描", "--mode=block", "sleep", "infinity"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.warning("阻止休眠失败：%s", e)
        return self

    def __exit__(self, *exc):
        try:
            if sys.platform == "win32":
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(self.ES_CONTINUOUS)
            elif self._proc is not None:
                self._proc.terminate()
        except Exception:
            pass
        return False


# ---------- 广播 ----------
def _broadcast(event: dict):
    for q in list(_progress_listeners):
        try:
            q.put(event)
        except Exception:
            pass


def _snapshot():
    with _state_lock:
        snap = dict(task_state)
        snap["stages"] = {k: dict(v) for k, v in task_state["stages"].items()}
        return snap


def _stage_update(active=None, done_all=False, stage_pct=None):
    with _state_lock:
        st = task_state["stages"]
        if done_all:
            for k in STAGE_KEYS:
                st[k]["status"] = "done"
                st[k]["pct"] = 100
            return
        for k in STAGE_KEYS:
            if st[k]["status"] == "active" and k != active:
                st[k]["status"] = "done"
                st[k]["pct"] = 100
        if active in st:
            st[active]["status"] = "active"
            if stage_pct is not None:
                st[active]["pct"] = stage_pct


def _overall_pct(phase, current, total):
    if phase == "enumerate":
        return min(5, int(current / max(total, 1) * 5)) if total > 0 else 2
    if phase == "parse":
        return 5 + int(current / max(total, 1) * 85)
    if phase == "grouping":
        return 92
    if phase == "saving":
        return 97
    if phase == "done":
        return 100
    return task_state.get("progress_pct", 0)


# ---------- 历史记录（P6） ----------
def _load_history():
    global _history
    try:
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _history = data
    except Exception as e:
        logger.warning("历史记录读取失败：%s", e)
        _history = []


def _save_history():
    try:
        HISTORY_FILE.write_text(
            json.dumps(_history, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("历史记录写入失败：%s", e)


def _record_history(task_id, start_wall, cfg, stats, out_path, status, roots, error=None):
    entry = {
        "task_id": task_id,
        "start_time": datetime.fromtimestamp(start_wall).isoformat(timespec="seconds"),
        "end_time": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "error": error,
        "result_path": str(out_path) if out_path else None,
        "result_name": out_path.name if out_path else None,
        "roots": roots,
        "stats": stats,
        "params": {
            "min_size_mb": round(cfg["min_size"] / 1024 / 1024, 2),
            "exclude_dir": cfg["exclude_list"],
            "workers": cfg["workers"],
            "duration_tol": cfg["duration_tol"],
            "weak_duration_tol": cfg["weak_duration_tol"],
            "name_sim": cfg["name_sim"],
            "dry_run": cfg["dry_run"],
            "tiers": {
                "strong": not cfg["no_strong"],
                "mid": not cfg["no_mid"],
                "weak": not cfg["no_weak"],
            },
            "include_exts": cfg["include_exts"],
            "exclude_exts": cfg["exclude_exts"],
        },
    }
    with _state_lock:
        _history.insert(0, entry)
        del _history[HISTORY_LIMIT:]
    _save_history()
    return entry


# ---------- 参数 ----------
def _parse_params(params: dict):
    try:
        min_size = int(float(params.get("min_size_mb", 100)) * 1024 * 1024)
    except (TypeError, ValueError):
        min_size = 100 * 1024 * 1024

    raw_exclude = params.get("exclude_dir")
    if raw_exclude is None:
        exclude_list = ["CHN"]
    elif isinstance(raw_exclude, str):
        exclude_list = [s.strip() for s in re.split(r"[;,，]", raw_exclude) if s.strip()]
    else:
        exclude_list = [str(s).strip() for s in raw_exclude if str(s).strip()]

    def _f(key, default):
        try:
            return float(params.get(key, default))
        except (TypeError, ValueError):
            return default

    workers = params.get("workers")
    try:
        workers = int(workers) if workers else None
    except (TypeError, ValueError):
        workers = None

    return {
        "min_size": max(0, min_size),
        "exclude_list": exclude_list,
        "workers": workers,
        "duration_tol": _f("duration_tol", 1.0),
        "weak_duration_tol": _f("weak_duration_tol", 2.0),
        "name_sim": _f("name_sim", 0.8),
        "dry_run": bool(params.get("dry_run", False)),
        "no_csv": bool(params.get("no_csv", False)),
        "no_html": bool(params.get("no_html", False)),
        "output": params.get("output_path") or None,
        "no_strong": bool(params.get("no_strong", False)),
        "no_mid": bool(params.get("no_mid", False)),
        "no_weak": bool(params.get("no_weak", False)),
        "include_exts": params.get("include_exts") or None,
        "exclude_exts": params.get("exclude_exts") or None,
        "use_cache": params.get("use_cache", True) is not False,
        "prevent_sleep": params.get("prevent_sleep", True) is not False,
    }


def _resolve_roots(params: dict):
    folder = params.get("folder")
    drives = params.get("drives")
    if folder:
        p = Path(folder).expanduser().resolve()
        if not p.exists():
            raise ValueError(f"路径不存在: {p}")
        if not p.is_dir():
            raise ValueError(f"不是文件夹: {p}")
        return [str(p)]
    if drives:
        if sys.platform != "win32":
            raise ValueError("--drives 仅支持 Windows")
        parts = drives.split("-")
        if len(parts) != 2:
            raise ValueError("--drives 格式应为 G-U")
        start, end = parts[0].strip().upper(), parts[1].strip().upper()
        roots = []
        for letter in string.ascii_uppercase:
            if start <= letter <= end:
                p = f"{letter}:\\"
                if os.path.exists(p):
                    roots.append(p)
        if not roots:
            raise ValueError(f"{start}: 到 {end}: 之间没有可用盘符")
        return roots
    raise ValueError("必须指定 folder 或 drives")


def _apply_allow_roots(roots):
    allowed, denied = mdf.filter_allowed_roots(roots, ALLOW_ROOTS)
    if denied:
        raise ValueError("以下路径不在 --allow-root 白名单内: " + ", ".join(denied))
    if not allowed:
        raise ValueError("没有可扫描的路径")
    return allowed


# ---------- 扫描线程 ----------
def _run_scan(params: dict, task_id: str):
    global _ACTIVE_CONTROL
    cfg = _parse_params(params)
    control = mdf.ScanControl()
    _ACTIVE_CONTROL = control

    start_mono = time.monotonic()
    start_wall = time.time()
    detail = {}
    rate = None
    last_t = start_mono
    last_c = 0
    last_phase = [None]

    def cb(phase, current, total, message):
        nonlocal rate, last_t, last_c
        now = time.monotonic()
        with _state_lock:
            task_state["phase"] = phase
            task_state["current"] = current
            task_state["total"] = total
            task_state["message"] = message
            task_state["elapsed"] = round(now - start_mono, 1)
            if detail:
                task_state["current_root"] = detail.get("root")
                task_state["current_file"] = detail.get("file")

            if phase == "parse":
                dt = now - last_t
                if dt >= 0.5:
                    inst = (current - last_c) / dt
                    rate = inst if rate is None else rate * 0.6 + inst * 0.4
                    last_t, last_c = now, current
                    task_state["rate"] = round(rate, 1)
                if rate and total > current:
                    task_state["eta_seconds"] = round((total - current) / rate, 1)

            stage_pct = None
            if phase == "parse":
                stage_pct = int(current / total * 100) if total else 0
            elif phase == "enumerate":
                stage_pct = 100 if total else 0
            elif phase == "grouping":
                stage_pct = 40
            elif phase == "saving":
                stage_pct = 40
            _stage_update(active=phase, stage_pct=stage_pct)
            task_state["progress_pct"] = _overall_pct(phase, current, total)

            if phase != last_phase[0] or (phase == "parse" and current % 500 == 0):
                logger.info("[%s] %s", phase, message)
                last_phase[0] = phase
        _broadcast(_snapshot())

    try:
        roots = _resolve_roots(params)
        roots = _apply_allow_roots(roots)
        with _state_lock:
            task_state["roots"] = roots
            task_state["phase"] = "enumerate"
            task_state["message"] = "扫描文件系统中..."
        _broadcast(_snapshot())

        cache = mdf.load_meta_cache(CACHE_FILE) if cfg["use_cache"] else None

        with KeepAwake(cfg["prevent_sleep"]):
            metas, failed, scanned_roots = mdf.scan_folder(
                roots,
                min_size_bytes=cfg["min_size"],
                exclude_dir_keywords=cfg["exclude_list"],
                workers=cfg["workers"],
                progress_callback=cb,
                include_exts=cfg["include_exts"],
                exclude_exts=cfg["exclude_exts"],
                cache=cache,
                use_cache=cfg["use_cache"],
                control=control,
                detail=detail,
            )
        if cache is not None:
            mdf.save_meta_cache(CACHE_FILE, cache)

        cancelled = control.cancelled

        with _state_lock:
            task_state["phase"] = "grouping"
            task_state["message"] = "分组去重中..."
            task_state["progress_pct"] = 92
            _stage_update(active="grouping", stage_pct=40)
        _broadcast(_snapshot())

        for f in metas:
            f["_norm_strip"] = mdf.normalize_name(f["name"], strip_res=True)

        claimed_pairs = set()
        strong = ([] if cfg["no_strong"] else
                  mdf.find_strong_candidates(metas, tol_sec=cfg["duration_tol"],
                                             claimed_pairs=claimed_pairs))
        mid = ([] if cfg["no_mid"] else
               mdf.find_mid_candidates(metas, sim_threshold=cfg["name_sim"],
                                       strong_tol_sec=cfg["duration_tol"],
                                       claimed_pairs=claimed_pairs))
        weak = ([] if cfg["no_weak"] else
                mdf.find_weak_candidates(metas, tol_sec=cfg["weak_duration_tol"],
                                         strong_tol_sec=cfg["duration_tol"],
                                         claimed_pairs=claimed_pairs))

        result = mdf.build_result(scanned_roots, strong, mid, weak, failed, len(metas))
        result["task_id"] = task_id
        result["cancelled"] = cancelled

        with _state_lock:
            task_state["phase"] = "saving"
            task_state["message"] = ("Dry-run 模式，跳过文件输出" if cfg["dry_run"]
                                     else "保存结果...")
            task_state["progress_pct"] = 97
            _stage_update(active="saving", stage_pct=40)
        _broadcast(_snapshot())

        out_path = None
        if not cfg["dry_run"]:
            if cfg["output"]:
                out_path = Path(cfg["output"]).expanduser().resolve()
                if out_path.suffix.lower() != ".json":
                    out_path = out_path.with_suffix(".json")
            else:
                out_path = RESULT_DIR / f"dup_result_{task_id}.json"
            with out_path.open("w", encoding="utf-8") as fp:
                json.dump(result, fp, ensure_ascii=False, indent=2)
            if not cfg["no_csv"]:
                mdf.export_csv(result, out_path.with_suffix(".csv"))
            if not cfg["no_html"]:
                mdf.export_html(result, out_path.with_suffix(".html"))
            logger.info("结果已写入 %s", out_path)

        total_wasted = sum(
            g["wasted_bytes"]
            for key in ("strong_candidates", "mid_candidates", "weak_candidates")
            for g in result[key]
        )
        stats = {
            "total_scanned": result["total_scanned"],
            "failed_files": len(result["failed_files"]),
            "strong_groups": len(result["strong_candidates"]),
            "mid_groups": len(result["mid_candidates"]),
            "weak_groups": len(result["weak_candidates"]),
            "total_wasted_gb": round(total_wasted / 1024 / 1024 / 1024, 2),
            "roots": scanned_roots,
            "elapsed": round(time.monotonic() - start_mono, 1),
            "cancelled": cancelled,
        }

        with _state_lock:
            task_state["result_path"] = str(out_path) if out_path else None
            task_state["result_name"] = out_path.name if out_path else None
            task_state["stats"] = stats
            task_state["cancelled"] = cancelled
            task_state["done"] = True
            task_state["phase"] = "done"
            task_state["progress_pct"] = 100
            task_state["message"] = ("已取消：展示已解析的部分结果" if cancelled
                                     else "扫描完成")
            _stage_update(done_all=True)
        _broadcast(_snapshot())

        _record_history(task_id, start_wall, cfg, stats, out_path,
                        "cancelled" if cancelled else "done", roots)
        logger.info("任务 %s 结束：%s", task_id, task_state["message"])

    except Exception as e:
        traceback.print_exc()
        logger.exception("任务 %s 失败", task_id)
        with _state_lock:
            task_state["phase"] = "error"
            task_state["message"] = f"错误: {e}"
            task_state["error"] = str(e)
            task_state["done"] = True
            _stage_update(done_all=True)
        _broadcast(_snapshot())
        _record_history(task_id, start_wall, cfg, None, None, "error", [], str(e))
    finally:
        _ACTIVE_CONTROL = None
        with _state_lock:
            task_state["running"] = False
            task_state["paused"] = False


# ---------- 预览线程 ----------
def _run_preview(params: dict):
    try:
        cfg = _parse_params(params)
        roots = _apply_allow_roots(_resolve_roots(params))

        def pcb(phase, current, total, message):
            with _state_lock:
                _preview_state["message"] = message

        with _state_lock:
            _preview_state["message"] = "正在枚举候选文件..."
        info = mdf.preview_scan(
            roots,
            min_size_bytes=cfg["min_size"],
            exclude_dir_keywords=cfg["exclude_list"],
            include_exts=cfg["include_exts"],
            exclude_exts=cfg["exclude_exts"],
            progress_callback=pcb,
        )
        with _state_lock:
            _preview_state["info"] = info
            _preview_state["message"] = "预览完成"
    except Exception as e:
        with _state_lock:
            _preview_state["error"] = str(e)
    finally:
        with _state_lock:
            _preview_state["running"] = False
            _preview_state["done"] = True


# ---------- 认证（B6） ----------
@app.before_request
def _require_auth():
    if AUTH is None:
        return None
    a = request.authorization
    if a and a.username == AUTH[0] and a.password == AUTH[1]:
        return None
    return Response("需要认证", 401,
                    {"WWW-Authenticate": 'Basic realm="MediaDupFinder"'})


# ---------- 页面 ----------
@app.route("/")
def index():
    return send_from_directory(RESULT_DIR, "web_index.html")


@app.route("/result")
def result_page():
    return send_from_directory(RESULT_DIR, "index.html")


@app.route("/worker-json.js")
def worker_json():
    return send_from_directory(RESULT_DIR, "worker-json.js")


@app.route("/worker-stream.js")
def worker_stream():
    return send_from_directory(RESULT_DIR, "worker-stream.js")


@app.route("/web.bat")
def web_bat():
    """Q1：一键生成 web 启动脚本，双击即可开浏览器 + 启服务。"""
    body = ("@echo off\r\n"
            "chcp 65001 >nul\r\n"
            "cd /d \"%~dp0\"\r\n"
            "echo 正在启动媒体查重 Web 服务...\r\n"
            "start \"\" /min cmd /c \"ping -n 3 127.0.0.1 >nul & start http://localhost:5000\"\r\n"
            "python web.py\r\n"
            "pause\r\n")
    return Response(body, mimetype="text/plain; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=web.bat"})


# ---------- 任务 API ----------
@app.route("/api/status")
def api_status():
    return jsonify(_snapshot())


@app.route("/api/config")
def api_config():
    return jsonify({
        "ok": True,
        "allow_roots": ALLOW_ROOTS,
        "auth_enabled": AUTH is not None,
        "history_limit": HISTORY_LIMIT,
        "ffmpeg": bool(_find_ffmpeg()),
    })


@app.route("/api/start", methods=["POST"])
def api_start():
    if task_state["running"]:
        return jsonify({"ok": False, "error": "已有任务在运行"}), 409

    params = request.get_json(silent=True) or request.form.to_dict()
    params.setdefault("min_size_mb", 100)
    params.setdefault("duration_tol", 1.0)
    params.setdefault("weak_duration_tol", 2.0)
    params.setdefault("name_sim", 0.8)
    params.setdefault("exclude_dir", ["CHN"])

    task_id = uuid.uuid4().hex[:12]
    with _state_lock:
        task_state.update({
            "running": True,
            "task_id": task_id,
            "start_time": datetime.now().isoformat(timespec="seconds"),
            "phase": "init",
            "message": "准备中...",
            "current": 0,
            "total": 0,
            "progress_pct": 0,
            "done": False,
            "error": None,
            "result_path": None,
            "result_name": None,
            "stats": None,
            "roots": [],
            "paused": False,
            "cancelled": False,
            "rate": 0.0,
            "eta_seconds": None,
            "elapsed": 0,
            "current_root": None,
            "current_file": None,
            "stages": _blank_stages(),
        })

    t = threading.Thread(target=_run_scan, args=(params, task_id), daemon=True)
    t.start()
    return jsonify({"ok": True, "task_id": task_id})


@app.route("/api/pause", methods=["POST"])
def api_pause():
    if task_state["running"] and _ACTIVE_CONTROL is not None:
        _ACTIVE_CONTROL.pause()
        with _state_lock:
            task_state["paused"] = True
            task_state["message"] = "已暂停（点击继续恢复）"
        _broadcast(_snapshot())
        logger.info("任务已暂停")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "没有正在运行的任务"}), 409


@app.route("/api/resume", methods=["POST"])
def api_resume():
    if task_state["running"] and _ACTIVE_CONTROL is not None:
        _ACTIVE_CONTROL.resume()
        with _state_lock:
            task_state["paused"] = False
            task_state["message"] = "继续扫描..."
        _broadcast(_snapshot())
        logger.info("任务已继续")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "没有正在运行的任务"}), 409


@app.route("/api/cancel", methods=["POST"])
def api_cancel():
    if task_state["running"] and _ACTIVE_CONTROL is not None:
        _ACTIVE_CONTROL.cancel()
        with _state_lock:
            task_state["paused"] = False
            task_state["cancelled"] = True
            task_state["message"] = "正在取消，保留已解析的部分结果..."
        _broadcast(_snapshot())
        logger.info("任务已请求取消")
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "没有正在运行的任务"}), 409


@app.route("/api/progress")
def api_progress():
    import queue
    q = queue.Queue()
    _progress_listeners.append(q)

    def generate():
        try:
            yield f"data: {json.dumps(_snapshot(), ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = q.get(timeout=30)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    continue
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if event.get("done") or event.get("phase") == "error":
                    break
        finally:
            if q in _progress_listeners:
                _progress_listeners.remove(q)

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no",
                             "Connection": "keep-alive"})


@app.route("/api/preview", methods=["POST"])
def api_preview():
    if _preview_state["running"]:
        return jsonify({"ok": False, "error": "预览正在进行"}), 409
    params = request.get_json(silent=True) or request.form.to_dict()
    with _state_lock:
        _preview_state.update({
            "running": True, "done": False, "error": None,
            "message": "准备中...", "info": None,
        })
    t = threading.Thread(target=_run_preview, args=(params,), daemon=True)
    t.start()
    return jsonify({"ok": True})


@app.route("/api/preview_status")
def api_preview_status():
    with _state_lock:
        return jsonify(dict(_preview_state))


# ---------- 结果 API ----------
def _latest_result_path():
    with _state_lock:
        for h in _history:
            rp = h.get("result_path")
            if rp and Path(rp).exists():
                return Path(rp), h.get("task_id")
    if RESULT_FILE.exists():
        return RESULT_FILE, None
    return None, None


@app.route("/api/result")
def api_result():
    """返回最近一次结果；?path=dup_result_xxx.json 可指定结果文件（仅限结果目录）。"""
    want = request.args.get("path", "")
    if want:
        safe = os.path.basename(want)
        if not safe.startswith("dup_result") or not (RESULT_DIR / safe).exists():
            return jsonify({"ok": False, "error": "结果文件不存在"}), 404
        path, task_id = RESULT_DIR / safe, None
    else:
        path, task_id = _latest_result_path()
    if path is None:
        return jsonify({"ok": False, "error": "还没有结果，请先扫描"}), 404
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    return jsonify({"ok": True, "data": data, "path": str(path),
                    "name": path.name, "task_id": task_id})


@app.route("/api/download/<filename>")
def api_download(filename):
    safe = os.path.basename(filename)
    if not safe.startswith("dup_result"):
        return jsonify({"error": "forbidden"}), 403
    if not (RESULT_DIR / safe).exists():
        return jsonify({"error": "not found"}), 404
    return send_from_directory(RESULT_DIR, safe, as_attachment=True)


@app.route("/api/download_latest/<ext>")
def api_download_latest(ext):
    rp = task_state.get("result_path")
    if not rp:
        return jsonify({"error": "no result"}), 404
    p = Path(rp)
    if ext.lower() in ("csv", "html"):
        p = p.with_suffix("." + ext.lower())
    if not p.exists():
        return jsonify({"error": "not found"}), 404
    return send_from_directory(p.parent, p.name, as_attachment=True)


# ---------- 历史 API（P6） ----------
@app.route("/api/history")
def api_history():
    with _state_lock:
        items = [dict(h) for h in _history]
    for it in items:
        rp = it.get("result_path")
        it["available"] = bool(rp and Path(rp).exists())
    return jsonify({"ok": True, "items": items})


@app.route("/api/history/<task_id>")
def api_history_one(task_id):
    with _state_lock:
        for h in _history:
            if h.get("task_id") == task_id:
                return jsonify({"ok": True, "item": h})
    return jsonify({"ok": False, "error": "记录不存在"}), 404


@app.route("/api/history/<task_id>/result")
def api_history_result(task_id):
    with _state_lock:
        entry = next((h for h in _history if h.get("task_id") == task_id), None)
    if not entry:
        return jsonify({"ok": False, "error": "记录不存在"}), 404
    rp = entry.get("result_path")
    if not rp or not Path(rp).exists():
        return jsonify({"ok": False, "error": "结果文件已不存在"}), 404
    with Path(rp).open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    return jsonify({"ok": True, "data": data, "item": entry})


@app.route("/api/history/<task_id>", methods=["DELETE"])
def api_history_delete(task_id):
    global _history
    with _state_lock:
        before = len(_history)
        _history = [h for h in _history if h.get("task_id") != task_id]
        changed = len(_history) != before
    if changed:
        _save_history()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "记录不存在"}), 404


# ---------- 缩略图 / 打开文件（R2 / R3） ----------
_FFMPEG = None
_FFMPEG_CHECKED = False


def _find_ffmpeg():
    """定位 ffmpeg 可执行文件（PATH 优先，其次常见安装目录），找不到返回 None。"""
    global _FFMPEG, _FFMPEG_CHECKED
    if _FFMPEG_CHECKED:
        return _FFMPEG
    _FFMPEG_CHECKED = True
    exe = shutil.which("ffmpeg")
    if not exe:
        candidates = [
            r"C:\ffmpeg\bin\ffmpeg.exe",
            r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
            "/usr/local/bin/ffmpeg",
            "/opt/homebrew/bin/ffmpeg",
        ]
        for cand in candidates:
            if cand and Path(cand).exists():
                exe = cand
                break
    _FFMPEG = exe
    return exe


@app.route("/api/thumb")
def api_thumb():
    """R2：用 FFmpeg 抽帧生成缩略图，按 路径+大小+修改时间 缓存到 _thumbs/。"""
    src = request.args.get("path", "")
    if not src:
        return jsonify({"error": "缺少 path"}), 400
    p = Path(src)
    if not p.is_file():
        return jsonify({"error": "文件不存在"}), 404
    exe = _find_ffmpeg()
    if not exe:
        return jsonify({"error": "未找到 ffmpeg"}), 503
    try:
        st = p.stat()
    except OSError:
        return jsonify({"error": "文件不存在"}), 404

    key = hashlib.md5(f"{p}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")).hexdigest()
    THUMB_DIR.mkdir(exist_ok=True)
    out = THUMB_DIR / f"{key}.jpg"
    if not out.exists():
        for seek in ("5", "0"):
            cmd = [exe, "-ss", seek, "-i", str(p), "-frames:v", "1",
                   "-vf", "scale=320:-1", "-q:v", "5", "-y", str(out)]
            try:
                subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=30, check=False)
            except (subprocess.SubprocessError, OSError):
                break
            if out.exists():
                break
    if not out.exists():
        return jsonify({"error": "抽帧失败"}), 500
    return send_from_directory(THUMB_DIR, out.name, max_age=86400)


@app.route("/api/open", methods=["POST"])
def api_open():
    """R3：在系统文件管理器中打开文件或定位其所在文件夹（仅本机运行时有意义）。"""
    data = request.get_json(silent=True) or {}
    target = data.get("path") or ""
    reveal = bool(data.get("reveal", True))
    if not target:
        return jsonify({"ok": False, "error": "缺少 path"}), 400
    p = Path(target)
    if not p.exists():
        return jsonify({"ok": False, "error": "路径不存在"}), 404
    try:
        if sys.platform == "win32":
            if p.is_dir():
                os.startfile(str(p))  # noqa: S606
            elif reveal:
                subprocess.Popen(["explorer", "/select,", str(p)])
            else:
                os.startfile(str(p))  # noqa: S606
        elif sys.platform == "darwin":
            cmd = ["open", "-R", str(p)] if (reveal and p.is_file()) else ["open", str(p)]
            subprocess.Popen(cmd)
        else:
            subprocess.Popen(["xdg-open", str(p if p.is_dir() else p.parent)])
    except Exception as e:  # pragma: no cover - 平台相关
        return jsonify({"ok": False, "error": str(e)}), 500
    return jsonify({"ok": True})


# ---------- 启动 ----------
def _parse_args():
    ap = argparse.ArgumentParser(description="媒体查重 Web 后端")
    ap.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    ap.add_argument("--port", type=int, default=5000, help="监听端口（默认 5000）")
    ap.add_argument("--allow-root", action="append", default=None,
                    help="只允许扫描这些根路径（可多次指定，防止误扫系统盘）")
    ap.add_argument("--auth", default=None, help="HTTP 基础认证，格式 user:pass")
    ap.add_argument("--no-lock", action="store_true", help="不创建单实例锁文件")
    ap.add_argument("--debug", action="store_true", help="开启 Flask 调试模式")
    return ap.parse_args()


def main():
    global AUTH, ALLOW_ROOTS
    args = _parse_args()
    _setup_logging()
    ALLOW_ROOTS = args.allow_root or []
    if args.auth:
        if ":" not in args.auth:
            print("错误：--auth 格式应为 user:pass")
            sys.exit(1)
        u, p = args.auth.split(":", 1)
        AUTH = (u, p)

    _load_history()

    if not args.no_lock:
        if not _acquire_lock(LOCK_FILE):
            sys.exit(1)
        atexit.register(_release_lock, LOCK_FILE)

    print("=" * 60)
    print(" 媒体查重 Web 服务")
    print(f" 打开浏览器访问: http://localhost:{args.port}")
    if ALLOW_ROOTS:
        print(f" 路径白名单: {', '.join(ALLOW_ROOTS)}")
    if AUTH:
        print(f" HTTP 认证: 已启用（用户 {AUTH[0]}）")
    print(f" 日志文件: {LOG_FILE.relative_to(BASE_DIR)}")
    print(" 按 Ctrl+C 停止服务")
    print("=" * 60)
    logger.info("Web 服务启动，端口 %s", args.port)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
