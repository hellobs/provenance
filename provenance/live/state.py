"""全局状态与共享工具:所有模块从这里取 BASE_DIR/manager/sim_state 等。"""
import json
import os
import threading
from typing import Dict

from mavisframework.config.loader import personas
from mavisframework.runtime.logger import get_logger

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

log = get_logger("provenance.live", level="info")

# ---------------------------------------------------------------------------
# checkpoint 倾向序列加载(带缓存)
# ---------------------------------------------------------------------------
_series_cache = {}
_series_cache_lock = threading.Lock()


def _dir_mtime_sig(ckpt_dir: str) -> str:
    """目录内 simulate-*.json 的数量 + 最新 mtime,作为缓存失效签名"""
    import glob as _g
    files = _g.glob(os.path.join(ckpt_dir, "simulate-*.json"))
    if not files:
        return ""
    latest = max(os.path.getmtime(p) for p in files)
    return "{}-{}".format(len(files), int(latest))


def load_tendency_series(ckpt_dir: str, agent: str):
    """读取某角色在 ckpt_dir 下的倾向序列(带缓存)。

    返回 [(datetime, {goal: value}), ...] 按时间排序;
    缓存键 = 目录 mtime 签名,新 checkpoint 落盘自动失效。
    """
    import datetime as _dt

    sig = _dir_mtime_sig(ckpt_dir)
    with _series_cache_lock:
        cached = _series_cache.get(ckpt_dir)
        if cached and cached[0] == sig:
            return cached[1].get(agent, [])

    import glob as _g

    all_series = {}
    files = sorted(_g.glob(os.path.join(ckpt_dir, "simulate-*.json")))
    for p in files:
        try:
            with open(p, "r", encoding="utf-8") as f:
                c = json.load(f)
        except Exception as e:
            log.warning("load_tendency_series 解析存档失败,跳过(p={}): {}".format(p, e))
            continue
        t = os.path.basename(p).replace("simulate-", "").replace(".json", "")
        try:
            dt = _dt.datetime.strptime(t, "%Y%m%d-%H%M")
        except ValueError as e:
            log.warning("load_tendency_series 时间格式非法,跳过(p={}, time={}): {}".format(p, t, e))
            continue
        for aname, ag in (c.get("agents") or {}).items():
            vt = (ag.get("status") or {}).get("value_tendency") or {}
            if vt:
                all_series.setdefault(aname, []).append((dt, vt))
    with _series_cache_lock:
        _series_cache[ckpt_dir] = (sig, all_series)
    return all_series.get(agent, [])


# 窗口条目时间反查缓存:ckpt_dir -> (签名, {agent: {条目签名: [时间...]}})
_window_time_cache = {}
_window_time_cache_lock = threading.Lock()


def _window_entry_sig(entry: dict) -> str:
    """窗口条目签名:action + feedback(旧存档无 time 时用于跨 checkpoint 匹配)"""
    try:
        fb = json.dumps(entry.get("feedback") or {}, sort_keys=True, default=str)
    except Exception:
        fb = ""
    return "{}||{}".format(str(entry.get("action", "")), fb)


def _backfill_window_times(ckpt_dir: str, agent: str, window: list) -> None:
    """为旧存档窗口条目(无 time 字段)反查模拟时间。

    新代码 observe_consequence 会写入 time;旧存档缺该字段。
    反查策略:扫描各 checkpoint 快照里该 agent 的 tendency_window,
    按 (action, feedback) 签名 + 出现次序,映射到最早出现的 checkpoint 时间。
    同签名多条:第 k 条取第 k 次出现的 checkpoint 时间(不全都标同一时刻)。
    找不到的条目保持空串(前端显示 "–")。
    """
    if not ckpt_dir or not os.path.isdir(ckpt_dir):
        return
    import glob as _g
    import datetime as _dt

    need = [w for w in window if isinstance(w, dict) and not w.get("time")]
    if not need:
        return
    sig = _dir_mtime_sig(ckpt_dir)
    with _window_time_cache_lock:
        cached = _window_time_cache.get(ckpt_dir)
        if cached and cached[0] == sig:
            times_map = cached[1].get(agent, {})
        else:
            times_map = None
    if times_map is None:
        # 扫描所有 checkpoint:记录每个签名的出现时间序列
        files = sorted(_g.glob(os.path.join(ckpt_dir, "simulate-*.json")))
        seen: Dict[str, list] = {}
        for p in files:
            t = os.path.basename(p).replace("simulate-", "").replace(".json", "")
            try:
                dt = _dt.datetime.strptime(t, "%Y%m%d-%H%M")
            except ValueError:
                continue
            try:
                with open(p, "r", encoding="utf-8") as f:
                    c = json.load(f)
            except Exception:
                continue
            ag = (c.get("agents") or {}).get(agent)
            if not ag:
                continue
            win = (ag.get("status") or {}).get("tendency_window") or []
            for w in win:
                if not isinstance(w, dict):
                    continue
                s = _window_entry_sig(w)
                seen.setdefault(s, []).append(dt)
        times_map = {s: [x.strftime("%Y%m%d-%H:%M") for x in v] for s, v in seen.items()}
        with _window_time_cache_lock:
            _window_time_cache[ckpt_dir] = (sig, {agent: times_map} if times_map else {})
    if not times_map:
        return
    # 按出现次序填时间:同签名第 k 条取第 k 次出现
    occ: Dict[str, int] = {}
    for w in window:
        if not isinstance(w, dict) or w.get("time"):
            continue
        s = _window_entry_sig(w)
        times = times_map.get(s)
        if not times:
            continue
        k = occ.get(s, 0)
        occ[s] = k + 1
        if k < len(times):
            w["time"] = times[k]


# ---------------------------------------------------------------------------
# WebSocket 连接管理(线程安全的广播)
# ---------------------------------------------------------------------------
from fastapi import WebSocket  # noqa: E402


class ConnectionManager:
    def __init__(self):
        self._queues: Dict[WebSocket, "asyncio.Queue"] = {}
        self._lock = threading.Lock()

    def register(self, ws: WebSocket, q) -> None:
        with self._lock:
            self._queues[ws] = q

    def unregister(self, ws: WebSocket) -> None:
        with self._lock:
            self._queues.pop(ws, None)

    def broadcast(self, data: dict) -> None:
        """线程安全:向所有连接队列投放消息(WebSocket 发送由各自的协程执行)"""
        from mavisframework.runtime.protocol import validate_message
        if not validate_message(data):
            print(f"[protocol] 非契约消息: type={data.get('type')}", flush=True)
        with self._lock:
            for q in self._queues.values():
                q.put_nowait(data)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._queues)


manager = ConnectionManager()

# ---------------------------------------------------------------------------
# 全局状态(server/compressor 由 live_fastapi.run_simulation 注入)
# ---------------------------------------------------------------------------
sim_state = {"status": "idle", "error": "", "start_time": "", "stride": 2, "name": ""}
compressor = None
server = None


def current_ckpt_dir() -> str:
    """当前模拟的 checkpoint 目录(绝对路径;未运行返回空串)"""
    ckpt_dir = ""
    if compressor is not None:
        ckpt_dir = getattr(compressor, "checkpoints_folder", "") or ""
    if ckpt_dir and not os.path.isabs(ckpt_dir):
        ckpt_dir = os.path.join(BASE_DIR, ckpt_dir)
    return ckpt_dir


def current_sim_name() -> str:
    return sim_state.get("name", "") or ""


def current_sim_time(fmt: str = "%Y%m%d-%H:%M") -> str:
    """当前模拟时间(模拟未运行返回空串)"""
    if server is not None and getattr(server, "game", None) is not None:
        try:
            return server.game._timer.get_date(fmt)
        except Exception:
            return ""
    return ""


def checkpoint_file(*parts) -> str:
    """BASE_DIR/results/checkpoints/<parts...> 绝对路径"""
    return os.path.join(BASE_DIR, "results/checkpoints", *parts)


def read_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("读取 json 失败(p={}): {}".format(path, e))
        return default
