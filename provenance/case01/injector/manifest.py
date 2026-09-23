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
    - `branch_mode`:preset(可控对照,分支由运行参数定) / judge(T0 回答判定,01 §六);
    - `judge`:`local`(本地 Ollama/HF 权重) / `api`(OpenRouter 等外部 API) / `rules`
      (关键词规则,不调 LLM);preset 模式下写的是"这次配置里实际的判定后端",
      记录里同时有 `branch_mode` 说明它没被调用过;
    - `temperature`:判定 0.1、反思 0.4、路由 0.2(反思/路由取 `case01.reflection`
      里的常量,同一来源,不两处写数)。
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
    "default_financial_data_dir", "detect_git_commit", "file_sha256",
    "dir_content_sha256", "text_sha256", "sha256_12",
]

MANIFEST_VERSION = "injector-manifest-0.1"

# manifest 必备键:缺一个就算不合格(守卫测试按它逐条核对)。
REQUIRED_KEYS = (
    "manifest_version",
    "created_at",
    "git_commit",
    "engine_id",
    "branch_mode",
    "judge",
    "judge_model",
    "judge_prompt_version",
    "temperature",
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
_DEFAULT_BACKEND_REASON = "未给 judge_llm,按真跑路径的兜底后端(本地 OllamaClient)记录"

# 提示词版本(12 位 sha256)用到的字面量,全部从模块里现取,不在这里复制正文。
_JUDGE_PROMPT_ATTR = "JUDGE_PROMPT"
_PLAN_PROMPT_ATTR = "PLAN_PROMPT"

_REASON_UNKNOWN_COMMIT = "git rev-parse 取不到 HEAD,且 .git/HEAD 不可读"
_REASON_FINANCIAL_ABSENT = "资料目录不存在:{}"
_REASON_PROMPT_UNREADABLE = "读不到提示词({}):{}"
_REASON_NO_CLIENT = "未给 judge_llm,无法从客户端探测"
_REASON_NO_JUDGE_PROMPT = "scenario 的 branch.judge_prompt 不可用:{}"

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
                "reason": _REASON_UNKNOWN_COMMIT + "(.git 目录没找到)"}
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=10)
        sha = (out.stdout or b"").decode("utf-8", "replace").strip()
        if out.returncode == 0 and _HEX40.match(sha.lower()):
            return {"git_commit": sha, "git_commit_source": "git rev-parse HEAD",
                    "reason": ""}
        reason = "git rev-parse 退出码 {}: {}".format(
            out.returncode,
            (out.stderr or b"").decode("utf-8", "replace").strip()[:200])
    except (OSError, subprocess.SubprocessError) as e:
        reason = "git 不可用({})".format(e)
    # 回退:直接读 .git,并把回退这件事写进 reason 之外的一栏
    sha = _commit_from_git_dir(root)
    if sha:
        return {"git_commit": sha, "git_commit_source": "read .git/HEAD (fallback)",
                "reason": ""}
    return {"git_commit": "unknown", "git_commit_source": "",
            "reason": reason or _REASON_UNKNOWN_COMMIT}


def default_scenario_path() -> str:
    """`cases/case01_stock/scenario.yaml`:从本文件向上找到含 cases/ 的仓根。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cur = here
    for _ in range(8):
        cand = os.path.join(cur, "cases", "case01_stock", "scenario.yaml")
        if os.path.isfile(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return ""


def default_financial_data_dir() -> str:
    """检索资料目录:`case01/data/financial`(Investment AI 的唯一信息源)。"""
    case01_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(case01_dir, "data", "financial")


def _scenario_judge_prompt(scenario_path: str) -> Tuple[str, str]:
    """scenario.yaml 的 `branch.judge_prompt`(判定提示词的**权威来源**)。

    返回 (prompt, error)。读不了就返回 ("", 原因),由调用方记进 warnings。
    不 import yaml:这里只要一个字符串字段,用行扫描即可,少一个依赖少一处失败点。
    """
    if not scenario_path or not os.path.isfile(scenario_path):
        return "", "scenario 文件不存在:{}".format(scenario_path or "(未给)")
    try:
        with open(scenario_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        return "", "读 scenario 失败:{}".format(e)
    # 定位顶层 `branch:` 段,再找它缩进下的 `judge_prompt:`(两种写法都认)
    in_branch = False
    for i, line in enumerate(lines):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            in_branch = line.strip().startswith("branch:")
            continue
        if not in_branch:
            continue
        stripped = line.strip()
        if stripped.startswith("judge_prompt:"):
            rest = stripped.split(":", 1)[1].strip()
            if rest in (">-", ">", "|", "|-", ""):
                block = [rest] if rest else []
                for follow in lines[i + 1:]:
                    if not follow.strip():
                        block.append("")
                        continue
                    if len(follow) - len(follow.lstrip()) <= indent:
                        break
                    block.append(follow.strip())
                return "\n".join(block).strip(), ""
            return rest, ""
    return "", "scenario 的 branch.judge_prompt 段没找到"


def _prompt_versions() -> Tuple[Dict[str, str], List[str]]:
    """反思/路由/判定提示词的版本哈希(12 位)。取不到 → 缺项留痕。"""
    out: Dict[str, str] = {}
    warnings: List[str] = []
    try:
        from .. import reflection as _refl
        out["reflection"] = sha256_12(
            getattr(_refl, "REFLECTION_SYSTEM", "") + getattr(_refl, "REFLECTION_PROMPT_CN", ""))
        out["router"] = sha256_12(
            getattr(_refl, "ROUTER_PROMPT_CN", "") + getattr(_refl, "ROUTER_RISK_ANCHOR", "")
            + getattr(_refl, "ROUTER_JSON_HINT", "") + getattr(_refl, "ROUTER_REWRITE_HINT", ""))
    except Exception as e:                      # noqa: BLE001 - 缺项要留痕,不静默
        warnings.append(_REASON_PROMPT_UNREADABLE.format("case01.reflection", e))
    try:
        from ..world import branch as _branch
        out["branch_judge"] = sha256_12(getattr(_branch, _JUDGE_PROMPT_ATTR, ""))
        out["c_plan"] = sha256_12(getattr(_branch, _PLAN_PROMPT_ATTR, ""))
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
    return "unknown", "", "判不出后端类型(类名 {} 不在已知表,也没声明 backend_kind)".format(
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


def collect_run_meta(raw: Optional[dict] = None, branch: str = "",
                     branch_mode: str = "", judge_llm: Any = None,
                     backend_kind: str = "") -> Dict[str, Any]:
    """从原始记录/调用参数里抽"这次运行是什么"的全部输入(纯提取,不做哈希)。

    raw:injector 原始记录(或已映射记录);空表示"由调用方直接给参数"。
    backend_kind:显式覆盖后端类型(测试/离线场景;不走客户端探测)。
    单一来源:raw 里已经带了 `manifest_meta` 时**直接沿用**(那是最贴近真实调用点
    的一份:桥自己记的判定后端),不再让映射器重新猜一遍。
    """
    raw = raw if isinstance(raw, dict) else {}
    if raw.get("injector") and not raw.get("nodes"):
        raw = raw["injector"]                       # 误把成品记录当输入时下钻
    cached = raw.get("manifest_meta")
    if isinstance(cached, dict) and cached.get("judge") and not backend_kind:
        meta = dict(cached)
        meta.setdefault("temperature", {})
        meta["temperature"] = {**(cached.get("temperature") or {}),
                               "judge": detect_temperature(judge_llm,
                                                           (cached.get("temperature") or {})
                                                           .get("judge", JUDGE_TEMPERATURE))}
        if branch:
            meta["branch"] = branch
        if branch_mode:
            meta["branch_mode"] = branch_mode
        meta.setdefault("branch_source", raw.get("branch_source", "") or "")
        meta["judge_model"] = meta.get("judge_model") or ""
        return meta
    meta: Dict[str, Any] = {
        "branch": branch or raw.get("branch", "") or "",
        "branch_mode": branch_mode or raw.get("branch_mode", "") or "preset",
        "branch_source": raw.get("branch_source", "") or "",
        "judge_info": dict(raw.get("judge_info") or {}),
        "mode": raw.get("mode", "") or "",
    }
    kind = ""
    why = ""
    if backend_kind:
        kind = str(backend_kind).strip().lower()
        why = "由调用方显式指定"
        if kind not in _BACKEND_KINDS:
            kind, why = "unknown", "调用方给的 backend_kind={!r} 不合法".format(backend_kind)
    elif judge_llm is None:
        # 没给客户端 ≠ 后端未知:真跑路径的兜底就是本地 Ollama(见 _DEFAULT_BACKEND_REASON)。
        kind, why = _DEFAULT_BACKEND_KIND, _DEFAULT_BACKEND_REASON
    else:
        kind, _note, why = detect_backend_kind(judge_llm)
    meta["judge"] = kind
    meta["judge_backend_reason"] = why
    model = detect_judge_model(judge_llm)
    if not model and kind == "local" and judge_llm is None:
        model = DEFAULT_JUDGE_MODEL                # 没给客户端时,OllamaClient 的默认模型
    meta["judge_model"] = model
    meta["temperature"] = {"judge": detect_temperature(judge_llm)}
    return meta


def build_manifest(run_meta: Optional[dict] = None, scenario_path: str = "",
                   financial_dir: str = "", created_at: str = "",
                   git_commit: str = "") -> dict:
    """产出一段 manifest。缺项一律 `null`(+ warnings),绝不静默省略。

    git_commit 显式传入时按传入值记(便于重放/测试);传 "unknown" 视为取不到。
    """
    meta = dict(run_meta or {})
    warnings: List[str] = []

    # --- 时间 ---
    if not created_at:
        created_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    # --- git ---
    if git_commit:
        commit = git_commit
        commit_source = "由调用方传入"
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
    prompts, prompt_warnings = _prompt_versions()
    warnings.extend(prompt_warnings)
    # 判定提示词的权威来源是 scenario 的 branch.judge_prompt
    judge_prompt, jp_err = _scenario_judge_prompt(sp)
    if judge_prompt:
        prompts["scenario_branch_judge"] = sha256_12(text_sha256(judge_prompt))
    else:
        warnings.append("scenario_branch_judge=null:" +
                        _REASON_NO_JUDGE_PROMPT.format(jp_err))
    judge_prompt_version = prompts.get("branch_judge") or None
    if not judge_prompt_version:
        warnings.append("judge_prompt_version=null:读不到 case01.world.branch.{}".format(
            _JUDGE_PROMPT_ATTR))

    # --- 反思/路由温度(与调用处同一常量) ---
    try:
        from ..reflection import REFLECTION_TEMPERATURE, ROUTER_TEMPERATURE
    except Exception as e:                      # noqa: BLE001
        REFLECTION_TEMPERATURE = ROUTER_TEMPERATURE = None
        warnings.append("temperature.reflection/router=null:读不到 case01.reflection 常量:{}"
                        .format(e))
    temp = dict(meta.get("temperature") or {})
    temp["reflection"] = REFLECTION_TEMPERATURE
    temp["router"] = ROUTER_TEMPERATURE

    judge_kind = str(meta.get("judge", "") or "")
    if judge_kind not in _BACKEND_KINDS:
        warnings.append("judge={!r}:{}".format(judge_kind,
                                               meta.get("judge_backend_reason", "判不出后端")))
    branch_mode = str(meta.get("branch_mode", "") or "")
    if branch_mode not in ("preset", "judge"):
        warnings.append("branch_mode={!r} 不在 preset/judge 之内".format(branch_mode))

    manifest = {
        "manifest_version": MANIFEST_VERSION,
        "created_at": created_at,
        "git_commit": commit or "unknown",
        "git_commit_source": commit_source,
        "engine_id": ENGINE_ID,
        "branch": meta.get("branch", "") or "",
        "branch_mode": branch_mode,
        "branch_source": meta.get("branch_source", "") or "",
        "judge": judge_kind or None,
        "judge_backend_reason": meta.get("judge_backend_reason", "") or "",
        "judge_model": meta.get("judge_model") or None,
        "judge_prompt_version": judge_prompt_version,
        "temperature": temp,
        "scenario_path": sp or "",
        "scenario_sha256": scenario_sha,
        "financial_data_dir": fd or "",
        "financial_data_version": financial,
        "prompt_versions": prompts,
        "manifest_warnings": warnings,
    }
    if not temp.get("judge") and temp.get("judge") != 0:
        warnings.append("temperature.judge=null:判不出判定温度")
        manifest["manifest_warnings"] = warnings
    return manifest


def attach_manifest(record: dict, run_meta: Optional[dict] = None,
                    scenario_path: str = "", financial_dir: str = "",
                    created_at: str = "", git_commit: str = "") -> dict:
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
                                            git_commit=git_commit)
    except Exception as e:                      # noqa: BLE001 - 降级也必须有声
        record["manifest"] = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": created_at or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "engine_id": ENGINE_ID,
            "manifest_error": "{}: {}".format(type(e).__name__, e),
            "manifest_warnings": ["manifest 生成失败,清单内容不可信:{}:{}".format(
                type(e).__name__, e)],
        }
    return record
