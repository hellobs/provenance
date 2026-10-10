# -*- coding: utf-8 -*-
"""运行清单(manifest):每次 run 落盘时"这次到底跑的是什么"的可复现证据。

为什么必须有这一段(2026-09-22):
    一条 `run.json` 目前只记"跑出了什么"(turns/events/consistency/reflection),
    完全不记"用什么跑的"——同一个 run 目录放上三个月后,没人能回答:
    当时用的是哪个 commit?判定走的是本地 Ollama 还是外部 API?判定提示词是哪个版本?
    scenario / 检索资料动过没有?这些恰恰是受控实验可复现的必要条件。
    本模块产出 `manifest` 段并挂进成品记录(见 `attach_manifest`)。

铁律对齐(本仓"不允许静默"):
    - 取不到的值**不静默跳过**:写 `null`(或 `"unknown"`/`"absent"` 这类有语义的占位)
      并在 `manifest_warnings` 里写清"缺什么、为什么缺";
    - 本模块自己出错也不能吃掉 manifest:调用方 `attach_manifest` 兜底产出一份
      `manifest_error` 清单,而不是把一个没有 manifest 的记录打扮成正常的。

语义口径(与 GTC 源文档一致,不新增设定):
    - **分支一律由 T0 回答判定**(01 §六),没有"预设分支"这个档位 ——
      2026-10-10 用户要求把预设分支的入口、字段、清单项全部剔除。
      因此清单里**不再有 `branch_mode`**;判不出来就是 `branch="undetermined"`
      + `finish_reason="branch_undetermined"`,不给默认值。
    - `judge`:`local`(本地 Ollama/HF 权重) / `api`(OpenRouter 等外部 API) / `rules`
      (关键词规则,不调 LLM);`branch_source` 记 `judge` / `judge-failed`,
      跑规则判定的场合(no-llm 自检)记 `rules` 并留一条 warnings;
    - `temperature`:判定 0.1、反思 0.4、路由 0.2(反思/路由取 `case01.reflection`
      里的常量,同一来源,不两处写数);`judge=rules` 时判定温度记 `null` ——
      规则没有温度,填 0.1 等于谎报调过模型;
    - `seed`:这次固定没固定采样种子。`null` = **没固定**(默认行为,不保证逐字
      重生成),这不是"取不到",所以不进 `manifest_warnings`;有值时取自客户端自己
      声明的 `client.seed`,不另读环境变量(否则清单会与实跑漂移)。即便有值也只买到
      "同机同版本大概率逐字一致",跨机逐字复现仍不成立(模型只记 tag、推理平台版本
      不进清单)—— 见 `docs/复现说明_三档口径.md` §五。
"""
import hashlib
import os
import re
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "MANIFEST_VERSION", "REQUIRED_KEYS", "JUDGE_TEMPERATURE",
    "attach_manifest", "build_manifest", "collect_run_meta",
    "default_financial_data_dir", "detect_git_commit", "detect_seed", "file_sha256",
    "dir_content_sha256", "text_sha256", "sha256_12",
]

MANIFEST_VERSION = "injector-manifest-0.1"

# manifest 必备键:缺一个就算不合格(守卫测试按它逐条核对)。
REQUIRED_KEYS = (
    "manifest_version",
    "created_at",
    "git_commit",
    "engine_id",
    "judge",
    "judge_model",
    "judge_prompt_version",
    "temperature",
    "seed",
    "truncations",
    "scenario_sha256",
    "financial_data_version",
    "prompt_versions",
    "manifest_warnings",
)

ENGINE_ID = "case01-injector"

# 判定(LLMBranchJudge)的采样温度:case01/world/branch.py 与 case_engine/branch.py
# 两处调用都写的是 0.1。这里是"记录用的常量",不改任何设定。
JUDGE_TEMPERATURE = 0.1

# 后端类型探测:类名 → 类型。用类名而不是 import 判断,避免把
# case01.agents.llm(会读 secrets)拖进 manifest 的导入路径。
_BACKEND_BY_CLASSNAME = {
    "OllamaClient": "local",
    "VLLMClient": "local",
    "LocalHFClient": "local",
    "OpenRouterClient": "api",
    "RuleBranchRouter": "rules",
    "RuleBranchJudge": "rules",
}
_BACKEND_KINDS = ("local", "api", "rules")

DEFAULT_JUDGE_MODEL = "qwen3:4b-instruct-2507-q4_K_M"   # OllamaClient 的默认 chat_model
# 没给判定客户端时的默认假设:真跑路径(bridge._judge_client / _install_c_plan)的
# 兜底就是本地 OllamaClient —— 与其把 judge 写成 "unknown",不如照实记成
# "local + 默认模型",并在 judge_backend_reason 里写明这是默认假设、不是探测结果。
_DEFAULT_BACKEND_KIND = "local"
_DEFAULT_BACKEND_REASON = ("no judge_llm given; recorded as the production-path "
                           "fallback backend (local OllamaClient)")

# 提示词版本(12 位 sha256)用到的字面量,全部从模块里现取,不在这里复制正文。
_JUDGE_PROMPT_ATTR = "JUDGE_PROMPT"
_PLAN_PROMPT_ATTR = "PLAN_PROMPT"

_REASON_UNKNOWN_COMMIT = "git rev-parse could not read HEAD and .git/HEAD is unreadable"
_REASON_FINANCIAL_ABSENT = "data directory does not exist: {}"
_REASON_PROMPT_UNREADABLE = "cannot read the prompt ({}): {}"
_REASON_NO_CLIENT = "no judge_llm given; cannot probe it from the client"

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")


# ----------------------------------------------------------------------
# 哈希原语:文本一律先把换行折成 \n,免得 Windows 的 CRLF 把同一份内容
# 算成两个哈希(可复现性最容易被这个坑掉)。
# ----------------------------------------------------------------------
def _norm_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def file_sha256(path: str) -> str:
    """文件内容 sha256(十六进制)。读不到就抛,由调用方决定怎么留痕。"""
    with open(path, "rb") as f:
        return hashlib.sha256(_norm_bytes(f.read())).hexdigest()


def text_sha256(text: str) -> str:
    return hashlib.sha256(_norm_bytes((text or "").encode("utf-8"))).hexdigest()


def sha256_12(value: str) -> str:
    """把一段十六进制哈希(或任意字符串)收成前 12 位,作为"版本"标识。

    已经是 64/40 位十六进制的直接用(不许二次哈希,否则版本对不上原始哈希);
    其余情况按文本哈希后取前 12 位。
    """
    v = str(value or "").strip().lower()
    if _HEX64.match(v) or _HEX40.match(v):
        return v[:12]
    return text_sha256(value)[:12]


def dir_content_sha256(root: str) -> str:
    """目录内容的"合体哈希":按相对路径排序,逐个 `路径 + 内容哈希` 再哈希。

    只吃常规文件;`.pyc`/`__pycache__` 这类缓存跳过(它们不进版本语义)。
    """
    items: List[Tuple[str, str]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
        for fn in sorted(filenames):
            if fn.endswith((".pyc", ".pyo")):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace("\\", "/")
            items.append((rel, file_sha256(full)))
    h = hashlib.sha256()
    for rel, digest in sorted(items):
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(digest.encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


# ----------------------------------------------------------------------
# 运行环境探测
# ----------------------------------------------------------------------
def _repo_root(start: Optional[str] = None) -> str:
    """从本文件向上找最近的 .git/HEAD,返回仓根;找不到返回 ""。"""
    here = os.path.abspath(start or os.path.dirname(os.path.abspath(__file__)))
    cur = here
    for _ in range(12):
        if os.path.isfile(os.path.join(cur, ".git", "HEAD")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


def _commit_from_git_dir(repo_root: str) -> str:
    """直接读 .git/HEAD:支持 `ref: refs/heads/x` 与 detached(裸 commit)。"""
    head_path = os.path.join(repo_root, ".git", "HEAD")
    try:
        with open(head_path, encoding="utf-8") as f:
            head = (f.read() or "").strip()
    except OSError:
        return ""
    if not head:
        return ""
    if not head.startswith("ref:"):
        return head if _HEX40.match(head.lower()) else ""
    ref = head.split(":", 1)[1].strip()
    for candidate in (ref, os.path.join("packed-refs")):
        p = os.path.join(repo_root, ".git", *candidate.split("/"))
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as f:
                    text = f.read()
            except OSError:
                continue
            if candidate == "packed-refs":
                for line in text.splitlines():
                    line = line.strip()
                    if line.endswith(" " + ref):
                        sha = line.split(" ", 1)[0]
                        return sha if _HEX40.match(sha.lower()) else ""
                continue
            sha = text.strip()
            return sha if _HEX40.match(sha.lower()) else ""
    return ""


def detect_git_commit(repo_root: str = "") -> Dict[str, str]:
    """当前仓 HEAD commit。取不到 → `unknown` + 原因(不许静默)。

    返回 `{"git_commit": "8f11893...", "git_commit_source": "...", "reason": ""}`;
    取不到时 `git_commit == "unknown"`,`reason` 非空。
    """
    root = repo_root or _repo_root()
    if not root:
        return {"git_commit": "unknown", "git_commit_source": "",
                "reason": _REASON_UNKNOWN_COMMIT + " (no .git directory found)"}
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=10)
        sha = (out.stdout or b"").decode("utf-8", "replace").strip()
        if out.returncode == 0 and _HEX40.match(sha.lower()):
            return {"git_commit": sha, "git_commit_source": "git rev-parse HEAD",
                    "reason": ""}
        reason = "git rev-parse exit code {}: {}".format(
            out.returncode,
            (out.stderr or b"").decode("utf-8", "replace").strip()[:200])
    except (OSError, subprocess.SubprocessError) as e:
        reason = "git unavailable ({})".format(e)
    # 回退:直接读 .git,并把回退这件事写进 reason 之外的一栏
    sha = _commit_from_git_dir(root)
    if sha:
        return {"git_commit": sha, "git_commit_source": "read .git/HEAD (fallback)",
                "reason": ""}
    return {"git_commit": "unknown", "git_commit_source": "",
            "reason": reason or _REASON_UNKNOWN_COMMIT}


def default_scenario_path() -> str:
    """`cases/case01_stock/scenario.yaml`(阶段 3:路径解析经 _providers)。"""
    from mavis_case01_injector._providers import default_scenario_yaml
    return default_scenario_yaml()


def default_financial_data_dir() -> str:
    """检索资料目录:`case01/data/financial`(阶段 3:路径解析经 _providers)。"""
    from mavis_case01_injector._providers import default_financial_data_dir as _dfd
    return _dfd()


def _prompt_versions(language: str = "legacy") -> Tuple[Dict[str, str], List[str]]:
    """反思/路由/判定提示词的版本哈希(12 位)。取不到 → 缺项留痕。"""
    out: Dict[str, str] = {}
    warnings: List[str] = []
    try:
        from mavis_case01_injector._providers import import_reflection
        _refl = import_reflection()
        if language == "en":
            from mavis_case01_injector._providers import import_reflection_en
            reflection_en = import_reflection_en()
            out["reflection"] = sha256_12(
                reflection_en.REFLECTION_SYSTEM + reflection_en.REFLECTION_PROMPT)
            out["router"] = sha256_12(
                reflection_en.ROUTER_PROMPT + reflection_en.RISK_ANCHOR +
                reflection_en.JSON_HINT + reflection_en.REWRITE_HINT)
        else:
            out["reflection"] = sha256_12(
                getattr(_refl, "REFLECTION_SYSTEM", "") + getattr(_refl, "REFLECTION_PROMPT_CN", ""))
            out["router"] = sha256_12(
                getattr(_refl, "ROUTER_PROMPT_CN", "") + getattr(_refl, "ROUTER_RISK_ANCHOR", "")
                + getattr(_refl, "ROUTER_JSON_HINT", "") + getattr(_refl, "ROUTER_REWRITE_HINT", ""))
    except Exception as e:                      # noqa: BLE001 - 缺项要留痕,不静默
        warnings.append(_REASON_PROMPT_UNREADABLE.format("case01.reflection", e))
    try:
        from mavis_case01_injector.world import branch as _branch
        out["branch_judge"] = sha256_12(getattr(
            _branch, "JUDGE_PROMPT_EN" if language == "en" else _JUDGE_PROMPT_ATTR, ""))
        out["c_plan"] = sha256_12(getattr(
            _branch, "PLAN_PROMPT_EN" if language == "en" else _PLAN_PROMPT_ATTR, ""))
        out["stance"] = sha256_12(getattr(
            _branch, "STANCE_PROMPT_EN" if language == "en" else "STANCE_PROMPT", ""))
    except Exception as e:                      # noqa: BLE001
        warnings.append(_REASON_PROMPT_UNREADABLE.format("case01.world.branch", e))
    return out, warnings


def detect_backend_kind(client: Any) -> Tuple[str, str, str]:
    """客户端 → (后端类型, 说明, 原因)。

    优先读客户端自己声明的 `backend_kind`(桩/新客户端用),其次按类名映射。
    都判不出来 → ("unknown", ..., 原因),调用方据此写 warnings(**不猜**)。
    """
    if client is None:
        return "unknown", "", _REASON_NO_CLIENT
    declared = str(getattr(client, "backend_kind", "") or "").strip().lower()
    if declared in _BACKEND_KINDS:
        return declared, "client.backend_kind={!r}".format(declared), ""
    cls_name = type(client).__name__
    kind = _BACKEND_BY_CLASSNAME.get(cls_name, "")
    if kind:
        return kind, "class {}".format(cls_name), ""
    return "unknown", "", ("cannot determine the backend type (class name {} is not in "
                           "the known table and no backend_kind was declared)").format(
        cls_name)


def detect_judge_model(client: Any) -> str:
    """判定用的模型名:客户端声明优先,否则已知默认值,再否则空串(→ null)。"""
    for attr in ("chat_model", "model", "chat_model_path", "model_name"):
        val = getattr(client, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def detect_temperature(client: Any, default: float = JUDGE_TEMPERATURE) -> float:
    """采样温度:客户端声明优先(真值),否则本模块常量(与调用处同一常量)。"""
    val = getattr(client, "temperature", None)
    try:
        if val is not None:
            return round(float(val), 4)
    except (TypeError, ValueError):
        pass
    return round(float(default), 4)


def detect_seed(client: Any) -> Optional[int]:
    """这次用的采样种子:客户端自己声明的才算,**不读环境变量**。

    读环境变量会造出一个"清单说固定了种子、客户端其实没固定"的假证据(和
    `judge_model` 必须取自真拿到的客户端是同一条理由)。没声明 → null,含义是
    **这次没固定种子、逐字重生成不保证**,而不是"取不到"(所以不进 warnings)。
    """
    val = getattr(client, "seed", None)
    if val is None or (isinstance(val, str) and not val.strip()):
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def seed_among(clients: Dict[str, Any]) -> Optional[int]:
    """这次运行**真用到的客户端**里第一个自报的种子(判定优先)。

    口径与 `detect_seed` 完全一致:只认客户端自己声明的值,不读环境变量。
    为什么要扫一圈:起面路径的判定走桥自己兜底的客户端、对话与反思走挂在 agent 上的
    `Case01SafeProvider`,种子只在后者的请求体里 —— 只看判定那一个就会落 `seed: null`
    (2026-10-06 小镇三条真跑实测:`rng.json` 记着 master=20261015,而
    `manifest.seed` 全为 null;批路径不受影响,它本来就同时收 llms)。
    """
    for client in (clients or {}).values():
        seed = detect_seed(client)
        if seed is not None:
            return seed
    return None


def truncation_counts(clients: Dict[str, Any]) -> Dict[str, int]:
    """各客户端各自的 max_tokens 截断次数(只留非零项,同一对象不重复计数)。

    客户端(`_ChatMixin` / `Case01SafeProvider`)在 `finish_reason == "length"` 时给
    实例的 `.truncations` 加一。取不到就当中 0 —— 0 与"没有该属性"在 warnings 里
    语义相同(都不告警),但真发生时**必须**留痕,否则"截断的输出与完整输出长得一模
    一样",事后无从分辨(2026-10-05 体检 §七)。

    为什么要一次收多个客户端:此前只看判定那一个对象,而生产路径把 `router_llm`
    交给判定后 `llm`(本地,跑对话与反思)就成了另一个对象、永不被数 ——
    报出来的数比真实发生的小,而清单里"小"和"没有"看起来是同一回事。

    标签为什么可能带 "/":同一个实例常挂在多个标签下(批路径 `router_llm =
    router_llm or llm`,且 C 线仓位计划也用同一个对象),而计数记在**实例**上,
    阶段信息不在里面。此前只取表里第一个标签,于是落在仓位计划上的截断被写成
    "判定 1 次" —— 那是标签,不是测量点(2026-10-07 实测:8b 两条截断真落
    `max_tokens=800` 的 C 线计划,判官输出反而完整)。现在把该实例的全部标签并列,
    至少读者知道这一处不是单一阶段;要精确到阶段得让客户端自己记 caller,未做。
    """
    groups: Dict[int, list] = {}       # id(client) → [实例, 该实例的标签列表]
    for label, client in (clients or {}).items():
        if client is None:
            continue
        group = groups.get(id(client))
        if group is None:
            groups[id(client)] = [client, [label]]
        elif label not in group[1]:
            group[1].append(label)
    out: Dict[str, int] = {}
    for client, labels in groups.values():
        try:
            n = int(getattr(client, "truncations", 0) or 0)
        except (TypeError, ValueError):
            continue
        if n:
            out["/".join(labels)] = n
    return out


def _detail_text(counts: Dict[str, int]) -> str:
    """把 {标签: 次数} 拼成一句人话(空则空串)。"""
    if not counts:
        return ""
    return "(" + ", ".join("{}: {} time(s)".format(k, v) for k, v in counts.items()) + ")"


def collect_run_meta(raw: Optional[dict] = None, branch: str = "",
                     judge_llm: Any = None,
                     backend_kind: str = "",
                     branch_source: str = "",
                     llms: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """从原始记录/调用参数里抽"这次运行是什么"的全部输入(纯提取,不做哈希)。

    raw:injector 原始记录(或已映射记录);空表示"由调用方直接给参数"。
    backend_kind:显式覆盖后端类型(测试/离线场景;不走客户端探测)。
    branch_source:调用方直接给的分支来源(preset/judge/rules);生产路径
        (`case01/orchestrator`)把它记在 `branch_action.source` 里而不是顶层字段,
        所以需要一个显式入口,否则清单里的 branch_source 会留空。
    llms:这次运行**另外还用到的**模型客户端 {标签: 客户端}。截断计数要收齐所有
        客户端,只看判定那一个会把别处的截断漏掉(见 `truncation_counts`)。
    单一来源:raw 里已经带了 `manifest_meta` 时**直接沿用**(那是最贴近真实调用点
    的一份:桥自己记的判定后端),不再让映射器重新猜一遍。
    """
    clients: Dict[str, Any] = {"judge": judge_llm}
    clients.update(llms or {})
    counts = truncation_counts(clients)
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("injector") and not raw.get("nodes"):
        raw = raw["injector"]                       # 误把成品记录当输入时下钻
    cached = raw.get("manifest_meta")
    if isinstance(cached, dict) and cached.get("judge") and not backend_kind:
        meta = dict(cached)
        meta.setdefault("temperature", {})
        cached_judge = (cached.get("temperature") or {}).get(
            "judge", None if cached.get("judge") == "rules" else JUDGE_TEMPERATURE)
        meta["temperature"] = {**(cached.get("temperature") or {}),
                               "judge": None if cached.get("judge") == "rules"
                                        else detect_temperature(judge_llm, cached_judge)}
        if branch:
            meta["branch"] = branch
        meta.pop("branch_mode", None)      # 没有 preset 了(2026-10-10 剔除)
        meta["branch_source"] = (branch_source or meta.get("branch_source")
                                 or raw.get("branch_source", "") or "")
        meta["judge_model"] = meta.get("judge_model") or ""
        if meta.get("seed") is None:
            meta["seed"] = seed_among(clients)
        meta.setdefault("truncations", sum(counts.values()))
        meta.setdefault("truncation_detail", counts)
        return meta
    meta: Dict[str, Any] = {
        "branch": branch or raw.get("branch", "") or "",
        "language": raw.get("language", "legacy"),
        "branch_source": branch_source or raw.get("branch_source", "") or "",
        "judge_info": dict(raw.get("judge_info") or {}),
        "mode": raw.get("mode", "") or "",
    }
    kind = ""
    why = ""
    if backend_kind:
        kind = str(backend_kind).strip().lower()
        why = "explicitly specified by the caller"
        if kind not in _BACKEND_KINDS:
            kind, why = "unknown", "the caller's backend_kind={!r} is not valid".format(backend_kind)
    elif judge_llm is None:
        # 没给客户端 ≠ 后端未知:真跑路径的兜底就是本地 Ollama(见 _DEFAULT_BACKEND_REASON)。
        kind, why = _DEFAULT_BACKEND_KIND, _DEFAULT_BACKEND_REASON
    else:
        kind, note, why = detect_backend_kind(judge_llm)
    meta["judge"] = kind
    # 探测成功时也记下"是怎么判出来的"(客户端声明的 backend_kind / 类名映射),
    # 事后核对指纹时不用猜是客户端自报的还是我们认类名认出来的。
    meta["judge_backend_reason"] = why or note
    model = detect_judge_model(judge_llm)
    if not model and kind == "local" and judge_llm is None:
        model = DEFAULT_JUDGE_MODEL                # 没给客户端时,OllamaClient 的默认模型
    meta["judge_model"] = model
    # 规则判定没有采样温度:写 0.1 会让"judge=rules + temperature=0.1"读起来像
    # 真的调过模型。规则路径记 null,由 build_manifest 落一条 warnings 说明。
    meta["temperature"] = {"judge": None if kind == "rules"
                                     else detect_temperature(judge_llm)}
    meta["seed"] = None if kind == "rules" else seed_among(clients)
    # 截断计数:客户端(_ChatMixin/Case01SafeProvider)把"输出被 max_tokens 截断"
    # 记在实例的 .truncations 上。此前这个数只 print 一次就没了 —— 32 次截断只活在
    # lane 日志里,台账/清单读不到(2026-10-05 体检 §七)。这里把它抽进 meta,
    # 由 build_manifest 升级成 manifest_warnings 的一条(读侧一律按嵌套路径取)。
    # 收的是这次运行用到的**所有**客户端;detail 只进 meta(清单键集另有全等守卫)。
    meta["truncations"] = sum(counts.values())
    meta["truncation_detail"] = counts
    return meta


def build_manifest(run_meta: Optional[dict] = None, scenario_path: str = "",
                   financial_dir: str = "", created_at: str = "",
                   git_commit: str = "",
                   engine_id: str = ENGINE_ID) -> dict:
    """产出一段 manifest。缺项一律 `null`(+ warnings),绝不静默省略。

    git_commit 显式传入时按传入值记(便于重放/测试);传 "unknown" 视为取不到。
    engine_id:哪个引擎跑出来的。默认注入器;生产路径(`case01/run` → orchestrator)
        必须传自己的值,否则清单一律自称 injector,事后分不清两条路。
    """
    meta = dict(run_meta or {})
    warnings: List[str] = []

    # --- 时间 ---
    if not created_at:
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- git ---
    if git_commit:
        commit = git_commit
        commit_source = "supplied by the caller"
        if commit == "unknown":
            warnings.append("git_commit=unknown:" + _REASON_UNKNOWN_COMMIT)
    else:
        info = detect_git_commit()
        commit = info["git_commit"]
        commit_source = info.get("git_commit_source", "")
        if info.get("reason"):
            warnings.append("git_commit=unknown:" + info["reason"])

    # --- scenario 内容哈希 ---
    sp = scenario_path or default_scenario_path()
    scenario_sha = None
    if not sp:
        warnings.append("scenario_sha256=null:找不到 cases/case01_stock/scenario.yaml")
    elif not os.path.isfile(sp):
        warnings.append("scenario_sha256=null:scenario 文件不存在:{}".format(sp))
    else:
        try:
            scenario_sha = file_sha256(sp)
        except OSError as e:
            warnings.append("scenario_sha256=null:读 scenario 失败:{}".format(e))

    # --- 检索资料目录哈希(不存在 → "absent",如实写) ---
    fd = financial_dir or default_financial_data_dir()
    financial: Optional[str]
    if not os.path.isdir(fd):
        financial = "absent"
        warnings.append("financial_data_version=absent:" + _REASON_FINANCIAL_ABSENT.format(fd))
    else:
        try:
            financial = dir_content_sha256(fd)
        except OSError as e:
            financial = None
            warnings.append("financial_data_version=null:读资料目录失败:{}".format(e))

    # --- 提示词版本 ---
    language = meta.get("language", "legacy")
    prompts, prompt_warnings = _prompt_versions(language)
    if language != "legacy":
        from .language import ROLE_TEXT
        import json
        prompts["role_text"] = sha256_12(json.dumps(
            ROLE_TEXT[language], ensure_ascii=False, sort_keys=True))
    warnings.extend(prompt_warnings)
    # ⚠ 命名订正(2026-10-08,查 #56 时撞上):这里的键原先叫 `scenario_branch_judge`,
    # 读起来像"分支判定提示词的权威来源"。实测**不是**:`cases/case01_stock/scenario.yaml`
    # 的 `branch.judge_prompt` 段装的是 **Reflection Router** 的提示词(与
    # `case01/reflection.py:ROUTER_PROMPT_CN` 同一段话),运行时**没有任何代码读它**
    # (`LLMBranchJudge` 用 `world/branch.py` 的模块常量 `JUDGE_PROMPT`),只有这段清单
    # 在给它算哈希。所以按现在的键名,审计里会出现"分支判定提示词 = 某个哈希",而那个哈希
    # 其实是 Router 提示词 —— 与"警告文案里的标签≠测量点"同一族错。
    # 改法:键名如实叫 scenario 的**字段**哈希(它就是一个场景文件里的字段),
    # 判定提示词的真实版本继续由 `prompts["branch_judge"]`(模块常量)提供。
    #
    # 2026-10-09 会议:scenario 的 branch 段整段删除(「分支判定」功能删除),该字段
    # **已不存在** ⇒ 不再记 `scenario_branch_judge_field`(读了也只会是空)。判定提示词
    # 的真实版本仍由 `prompts["branch_judge"]` 提供 —— 判定逻辑本身自包含在 injector 包
    # (`world/branch.py` 模块常量),不依赖 scenario 文件。
    judge_prompt_version = prompts.get("branch_judge") or None
    if not judge_prompt_version:
        warnings.append("judge_prompt_version=null: cannot read case01.world.branch.{}".format(
            _JUDGE_PROMPT_ATTR))

    # --- 反思/路由温度(与调用处同一常量) ---
    try:
        from mavis_case01_injector._providers import import_reflection
        _refl = import_reflection()
        REFLECTION_TEMPERATURE = _refl.REFLECTION_TEMPERATURE
        ROUTER_TEMPERATURE = _refl.ROUTER_TEMPERATURE
    except Exception as e:                      # noqa: BLE001
        REFLECTION_TEMPERATURE = ROUTER_TEMPERATURE = None
        warnings.append("temperature.reflection/router=null: cannot read the case01.reflection constants: {}"
                        .format(e))
    temp = dict(meta.get("temperature") or {})
    temp["reflection"] = REFLECTION_TEMPERATURE
    temp["router"] = ROUTER_TEMPERATURE

    judge_kind = str(meta.get("judge", "") or "")
    if judge_kind not in _BACKEND_KINDS:
        warnings.append("judge={!r}:{}".format(judge_kind,
                                               meta.get("judge_backend_reason",
                                                        "cannot determine the backend")))
    # 截断计数:此前只活在 lane 日志/一次 print 里,清单读不到(2026-10-05 体检 §七)。
    # 非零才进 warnings —— 0 是常态,不占位。
    truncations = int(meta.get("truncations") or 0)
    if truncations:
        # 前缀 `truncations=N` 是读侧与测试认的形态;括号里补"哪几处各几次",
        # 否则一个光秃秃的总数没法定位是判定的输出短了还是对话被压了。
        warnings.append(
            "truncations={}{}: {} output(s) were cut short by max_tokens this run "
            "(truncated text looks just like complete text; verify by hand before "
            "quoting it)".format(
                truncations, _detail_text(meta.get("truncation_detail") or {}),
                truncations))

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_at": created_at,
        "git_commit": commit or "unknown",
        "git_commit_source": commit_source,
        "engine_id": engine_id,
        "language": language,
        "branch": meta.get("branch", "") or "",
        "branch_source": meta.get("branch_source", "") or "",
        "judge": judge_kind or None,
        "judge_backend_reason": meta.get("judge_backend_reason", "") or "",
        "judge_model": meta.get("judge_model") or None,
        "judge_prompt_version": judge_prompt_version,
        "temperature": temp,
        "seed": meta.get("seed"),
        "truncations": int(meta.get("truncations") or 0),
        "scenario_path": sp or "",
        "scenario_sha256": scenario_sha,
        "financial_data_dir": fd or "",
        "financial_data_version": financial,
        "prompt_versions": prompts,
        "manifest_warnings": warnings,
    }
    if not temp.get("judge") and temp.get("judge") != 0:
        warnings.append(
            "temperature.judge=null:{}".format(
                "the backend is rules; a rule-based decision has no sampling temperature"
                if judge_kind == "rules"
                else "cannot determine the judge temperature"))
        manifest["manifest_warnings"] = warnings
    return manifest


def attach_manifest(record: dict, run_meta: Optional[dict] = None,
                    scenario_path: str = "", financial_dir: str = "",
                    created_at: str = "", git_commit: str = "",
                    engine_id: str = ENGINE_ID) -> dict:
    """把 manifest 挂到记录上。**任何失败都要留痕**(产出 manifest_error 段)。

    返回挂好 manifest 的记录(就地改也返回)。已有 manifest 时不覆盖(单一来源:
    重跑 Reflection/Router 不许把第一次运行的清单冲掉);除非显式想要刷新调用
    `build_manifest`。
    """
    if not isinstance(record, dict):
        return record
    if isinstance(record.get("manifest"), dict) and record["manifest"]:
        return record
    try:
        record["manifest"] = build_manifest(run_meta, scenario_path=scenario_path,
                                            financial_dir=financial_dir,
                                            created_at=created_at,
                                            git_commit=git_commit,
                                            engine_id=engine_id)
    except Exception as e:                      # noqa: BLE001 - 降级也必须有声
        record["manifest"] = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine_id": engine_id,
            "manifest_error": "{}: {}".format(type(e).__name__, e),
            "manifest_warnings": ["manifest 生成失败,清单内容不可信:{}:{}".format(
                type(e).__name__, e)],
        }
    return record
