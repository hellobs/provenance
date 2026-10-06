# -*- coding: utf-8 -*-
"""单一入口的种子派生链:对外只露**一个**种子。

契约(改任何一条都要升 RNG_SCHEME):
    RNG_SCHEME 是标签集合与派生算法的版本号。同一 master 在不同 scheme 下
    派生出不同流,所以版本号必须与参数一起留痕,否则"同种子"三个字失去含义。
    不传 master 时**什么都不做** —— 默认路径保持今天的非确定行为,
    既有的批量语料语义不受影响(这是这条链敢进主干的唯一理由)。

两条流为什么不需要互相隔离:
    模型采样侧沿用 `CASE01_LLM_SEED = master` 原值(manifest.seed 记的就是人敲
    进去的那一个数,对外仍然只有一个种子);Python RNG 走派生值。两者不共享
    PRNG 序列,不存在"同种子导致两条流相关"的问题。
"""
import hashlib
import os
import random
import subprocess
import sys
import time

RNG_SCHEME = "rng-scheme-1"
ENV_MASTER = "CASE01_SEED"
ENV_LLM_SEED = "CASE01_LLM_SEED"
WORLD_LABEL = "world"
ENV_LLM_BASE_URL = "CASE01_LLM_BASE_URL"
# 环境身份里要抄进旁证的开关:只挑"改了它就改了总体"的那几个,不做全 env 快照
# (全量快照会把密钥一起抄进交付文件 —— 本仓对这条有先例纪律)。
ENV_PROBE_KEYS = (
    "OLLAMA_NUM_PARALLEL", "OLLAMA_KEEP_ALIVE", "OLLAMA_FLASH_ATTENTION",
    "CUDA_VISIBLE_DEVICES", "CASE01_LLM_PROVIDER", ENV_LLM_BASE_URL,
    "CASE01_LLM_MODEL",
)


def ensure_run_id(run_id=""):
    """种子链启用时**必须**有名字:名字既进世界流的派生盐,也决定旁证写到哪儿。

    与 `orchestrator.run_case01` 的缺省命名同一格式 —— 在这里先定名是为了让
    "派生用的 run_id"与"落盘用的 run_id"是同一个值(否则旁证会写到另一个目录,
    而记录自己叫别的名字)。
    """
    text = str(run_id or "").strip()
    return text or time.strftime("run-%Y%m%d-%H%M%S")


def _ollama_root(base_url=""):
    root = (base_url or os.environ.get(ENV_LLM_BASE_URL, "")
            or "http://127.0.0.1:11434").rstrip("/")
    return root[:-3].rstrip("/") if root.endswith("/v1") else root


def probe_environment(base_url="", timeout=2.0):
    """把"这条记录是在什么环境上跑出来的"记下来 —— 只读、限时、探针失败只留原因。

    为什么种子链要管环境(2026-10-06 第二十轮体检的直接后果):`--seed` 钉住的是
    **采样流**,而"同种子重跑"要成立还得知道当时**是谁在答、哪个解释器、哪棵树**。
    这三样在 manifest 里都取不到:`judge_model` 是客户端声明值(不是服务端实测)、
    `engine_id` 是标签不是版本、未提交的改动不进任何留痕。
    ⇒ **只在启用种子链时才探** —— 默认路径不新增任何网络调用或子进程,
      行为与今天逐字节相同;探针一律吞异常(环境记不上是可惜,拖垮一局是事故)。
    """
    import json
    import urllib.request

    out = {"python_executable": sys.executable,
           "python_version": sys.version.split()[0]}
    try:
        import mavisframework                       # 版本要取真导入的那个模块对象
        out["engine_file"] = getattr(mavisframework, "__file__", "") or ""
        out["engine_version"] = str(getattr(mavisframework, "__version__", ""))
    except Exception as exc:
        out["engine_file"] = ""
        out["engine_version"] = ""
        out["engine_probe_reason"] = repr(exc)[:120]
    out["env"] = {k: os.environ[k] for k in ENV_PROBE_KEYS
                  if str(os.environ.get(k, "")).strip()}

    root = _ollama_root(base_url)
    for name, path in (("ollama_version", "/api/version"),
                       ("ollama_resident", "/api/ps")):
        try:
            with urllib.request.urlopen(root + path, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
            if name == "ollama_version":
                out[name] = str(payload.get("version") or "")
            else:
                # 排序后写入:驻留列表是集合语义,不排序会把"顺序不同"误报成"环境不同"。
                # 这里是**全部**驻留模型(含 embedding),刻意不筛成单个主模型 ——
                # 本仓的台账探针就是因为筛了才说不清"到底谁在答"。
                out[name] = sorted({str(m.get("name")) for m in payload.get("models") or []
                                    if m.get("name")})
        except Exception as exc:
            out[name] = "" if name == "ollama_version" else []
            out.setdefault("ollama_probe_reason", repr(exc)[:120])
    out["git"] = _probe_git()
    return out


def _probe_git(timeout=5.0):
    """HEAD + 工作树脏不脏。取不到就如实记 unknown/-1,不猜"干净"。

    **不加 pathspec**:我第一版只数了 `case01/case_engine/live/tools/packages` 五个目录,
    于是 `provenance/live_switch.py`(在包根、也正好是种子链改过的文件)被静默漏掉 ——
    与本轮体检记下的"过滤器的 0 要自证"同族错误。改成整仓一次 `git status`,
    再在 Python 侧把"跑批产物"与"代码"分开数:两者都脏,但对复现的含义完全不同
    (产物脏 = 多几条记录;代码脏 = 重跑用的不是这棵树)。
    `results/` 之外的未跟踪目录(如 `case01/runs`)被 .gitignore 挡着,不会进来。
    """
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # .../provenance/provenance
    root = os.path.dirname(pkg)                                         # git 根(上一层)
    rel = os.path.basename(pkg) + "/"
    out = {"git_head": "", "worktree_dirty_paths": -1, "worktree_dirty_code_paths": -1}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True, timeout=timeout)
        if head.returncode == 0:
            out["git_head"] = head.stdout.strip()[:40]
        st = subprocess.run(["git", "status", "--porcelain"], cwd=root,
                            capture_output=True, text=True, timeout=timeout)
        if st.returncode == 0:
            paths = [line[3:].strip().strip('"') for line in st.stdout.splitlines()
                     if line.strip()]
            # 跑批产物:交付时本来就要另给记录包,不算"代码不一致"
            data = [p for p in paths
                    if p.startswith(rel + "results/") or p.startswith(rel + "data/")]
            out["worktree_dirty_paths"] = len(paths)
            out["worktree_dirty_code_paths"] = len(paths) - len(data)
    except Exception as exc:
        out["git_probe_reason"] = repr(exc)[:120]
    return out



def resolve_master(explicit=None):
    """外部只给一个数:显式参数 > `CASE01_SEED` > `CASE01_LLM_SEED`(末项为向后兼容)。

    非法值**抛 ValueError**,不静默当成"没设" —— 静默会让调用方以为自己固定过种子,
    实际跑的是非确定路径(与 `llm.parse_seed` 同一条判据)。
    """
    for raw in (explicit, os.environ.get(ENV_MASTER, ""), os.environ.get(ENV_LLM_SEED, "")):
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            return int(text)
        except ValueError:
            raise ValueError("种子必须是整数,收到:{!r}".format(raw))
    return None


def derive_world_seed(master, run_id=""):
    """世界动力学(Python 全局 RNG)的子种子;master 为空则返回 None(不播种)。"""
    if master is None:
        return None
    key = "{}|{}|{}|{}".format(RNG_SCHEME, WORLD_LABEL, str(run_id or ""), int(master))
    # 必须用 sha256 而不是内建 hash():后者随 PYTHONHASHSEED 变化、跨进程不同值,
    # 那样"种子链"会退化成"每个进程重新随机"。
    return int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big")


def apply_chain(master, run_id="", log=None, base_url="", probe=True):
    """把 master 摊到两条流;返回诊断 dict(没传 master 时返回 None)。

    `run_id` 参与世界流派生是**硬要求**:否则同一批里的多条记录会共用同一条世界
    轨迹,把 n 条样本做成"同一条样本跑 n 遍"。
    `probe=False` 只给测试用;生产两条入口都让它探(默认路径进不来这个分支)。
    """
    say = log or (lambda msg: None)
    if master is None:
        say("种子链未启用(没传 --seed / CASE01_SEED):保持默认非确定行为")
        return None
    world = derive_world_seed(master, run_id)
    random.seed(world)
    os.environ[ENV_LLM_SEED] = str(master)
    info = {"rng_scheme": RNG_SCHEME,
            "master": int(master),
            "run_id": str(run_id or ""),
            "model_seed": int(master),
            "world_seed": world,
            "engine_rng": "seeded"}
    if probe:
        info["environment"] = probe_environment(base_url=base_url)
    say("种子链已应用:{} master={} model={} world={} run_id={}".format(
        RNG_SCHEME, master, master, world, run_id or "-"))
    return info


def write_side_record(dir_path, info, log=None):
    """把派生参数与环境身份写成运行目录旁的 `rng.json`。

    刻意**不写进 `run.json`/manifest**:键集有全等守卫(`test_manifest_keyset`),
    加键会牵动一轮测试与"产物不回填"的既有口径。旁证文件与记录同目录、同生命周期。
    内容是"怎么被固定的"(种子 + scheme + 解释器/引擎/服务端/树),不是研究结论,
    所以它可以带 manifest 不该带的东西 —— 包括"当时工作树是脏的"。
    """
    say = log or (lambda msg: None)
    if not info or not dir_path:
        return ""
    import json
    path = os.path.join(dir_path, "rng.json")
    try:
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)            # 同目录原子替换(跨盘 os.replace 会报错)
    except Exception as exc:             # 记不上要让人知道,但不能拖垮一局推演
        say("[警告] rng.json 写入失败:{} —— 种子链参数只在上面的日志行里留痕".format(exc))
        # 调用方没给 log 时也必须出声:静默丢掉留痕文件正是本仓最怕的那类失败
        # ("铁律:失败了必须有人知道" —— live/history.py)。
        print("[警告] rng.json 写入失败({}):{} —— 种子链参数只在日志里留痕".format(
            dir_path, exc), flush=True)
        return ""
    say("种子链参数已留痕 -> {}".format(path))
    return path
