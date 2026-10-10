# -*- coding: utf-8 -*-
"""统一历史数据界面(与 case 无关的共享模块)。

提供两件事,供 case00 / case01 两个实时面(5010,互斥)共同挂载:

  - `GET /api/runs`:聚合三处历史数据(case00 痕迹 / case01 成品 / 压缩成品),
    归一化为同一组摘要字段。只读 XPath 映射,不迁移、不重写数据;字段命名对齐
    case01 `review_app._brief` 口径,于是配置工具与前端读同一套接口。
  - `GET /embed/explore`:数据界面(左侧 Menu 分组历史 run + 右侧摘要/详情)。
    纯文件/数据浏览,**不依赖当前实时面的运行状态**(服务保持/未跑都能开)。

为什么独立成一个模块:5010 单入口、case00 与 case01 互斥,而数据界面本质上
和"当前跑哪个 case"无关,应两侧都可用。所以放这里,由两侧各自 `include_router`。
"""
import datetime as _dt
import json
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
from case01.review_export import ReviewPackageResponse

# 统一根:本文件位于 <root>/live/history.py,向上两级即 provenance/provenance。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_root(env_var: str, *parts: str) -> str:
    """数据根目录:环境变量优先,否则用仓库内默认位置。

    为什么必须可覆盖(2026-09-22):`case01/runs` 按约定**不入库**(只入约定与代码),
    所以"成品记录在哪"不能在代码里写死 —— 写死的后果是 CI 上没有记录可读,而
    `tests/test_live_history_quality.py` 又要断言"至少有一条",于是 CI 必红。
    现在:CI 用 `CASE01_RUNS_ROOT` 指向签入的夹具(`tests/fixtures/case01_runs`),
    真实代码路径照样跑;异地部署换数据盘也不用改代码。

    **环境变量必须是绝对路径**(2026-10-05 修):此前环境变量给相对值时按 cwd
    解释,行为随进程 cwd 漂移 —— 同一个 `runs` 从仓库根启动读不到、从子目录启动
    读得到。现在走 `case_engine.paths.require_abs_path`,相对值当场报错。
    """
    raw = os.environ.get(env_var)
    if raw:
        from case_engine.paths import require_abs_path
        return require_abs_path(raw, what=env_var)
    # 2026-10-08:已知的"数据根"改走 `case_engine.paths.data_root()` —— 它们的家搬到仓根 `data/` 下,
    # 搬迁期新位置优先、老位置回落并出声。**保留 (_data_root) 这个入口形态**:
    # 多个读取方与 `tests/test_runs_root_env_parity.py` 都按它断言,换签名等于白改一遍。
    kind = _KIND_BY_PARTS.get((env_var,) + tuple(parts))
    if kind:
        from case_engine.paths import data_root
        return data_root(kind, env_var=env_var)
    return os.path.join(BASE_DIR, *parts)


# (环境变量, 老位置各部分) → data_root 的 kind。未列的继续走 BASE_DIR 拼接(如 results/compressed)。
_KIND_BY_PARTS = {
    ("CASE01_RUNS_ROOT", "case01", "runs"): "case01.records",
    ("CASE00_CHECKPOINTS_ROOT", "results", "checkpoints"): "case00.state",
}


def _data_root_soft(env_var: str, *parts: str):
    """同 `_data_root`,但**不抛**:解析失败时返回 `(None, 原因)`。

    2026-10-05 第六轮 M2 的推广:相对根配置(`AmbiguousPathError`)在 `_data_root`
    里是抛异常,而"列表/详情"这类**只读浏览**接口不该因为一个环境变量配错就 500 ——
    该数据源整块不可用是**可预期**的降级,不是服务器错误。调用方拿到 `None` 后
    跳过该源,并把原因写进响应(见 `/api/runs` 的 `source_errors`)。

    为什么不在 `_data_root` 里直接吞:契约/交接包那条链要**明确的原因码**
    (422 `invalid_review_record_or_catalog`),它需要异常本身;这里是浏览链,
    语义不同。两处各按自己的语义处理,共享同一份判据字符串。
    """
    try:
        return _data_root(env_var, *parts), ""
    except Exception as exc:  # noqa: BLE001 —— 配置错不该冒成 500
        return None, "{}: {}".format(env_var, exc)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "frontend/templates"))
# 顶栏外部工具链接:mavis 仓的 config_tool 是**独立进程**(默认 8060),
# 地址可用环境变量 MAVIS_CONFIG_TOOL_URL 覆盖(换机/换端口)。
templates.env.globals["extra_nav_links"] = [
    {"label": "Configuration Tool ↗", "url": os.environ.get("MAVIS_CONFIG_TOOL_URL",
                                                 "http://127.0.0.1:8060/"),
     "title": "Mavis role and scene configuration tool (separate service; override its URL with MAVIS_CONFIG_TOOL_URL)"},
]


# 专家审核面的路径与端点函数名(case01/expert_review_app.py:697-698)
EXPERT_FACE_PATH = "/review/expert"
EXPERT_FACE_ENDPOINT = "expert_index"


def _route_paths(app) -> set:
    """把 app 上**所有层**的路由路径摊平成一个集合。

    为什么手写递归:本版 starlette 的 `include_router` 不在 `app.routes` 里摊平,
    而是塞成 `_IncludedRouter`(它没有 `.path`/`.routes`,只有 `original_router`)。
    2026-10-09 实测:只数 `getattr(r,"path")` 会得到 6 条默认路由、
    `/review/expert` 看不见 ⇒ 顶栏的"专家审核"永远不出现(假阴性)。
    """
    out = set()
    seen = set()

    def walk(routes, depth=0):
        if depth > 6:
            return
        for r in routes or []:
            if id(r) in seen:
                continue
            seen.add(id(r))
            p = getattr(r, "path", None)
            if p:
                out.add(p)
            for attr in ("original_router", "routes", "app"):
                sub = getattr(r, attr, None)
                inner = getattr(sub, "routes", None) if sub is not None else None
                if inner:
                    walk(inner, depth + 1)

    walk(getattr(app, "routes", []))
    return out


def _face_serves(app, path: str) -> bool:
    """这个面上有没有 `path` 这条路由 —— 两条独立判据,任一命中即算有:
    ① 自己摊平路由树;② Starlette 公开的 `url_path_for`(按端点函数名,不随版本变)。
    单用②要写死函数名,单用①会被路由树实现细节骗到 —— 两条同用才不会"面在、按钮不出现"。
    """
    if path in _route_paths(app):
        return True
    try:
        app.url_path_for(EXPERT_FACE_ENDPOINT)
        return True
    except Exception:  # noqa: BLE001 - 名字没注册就是 KeyError/BuildError,按"没有"处理
        return False


def nav_links_for(app) -> list:
    """顶栏链接:外部工具是环境变量给的,**专家审核面只有它真挂着的时候才给链接**。

    为什么按"路由在不在"判而不是按 case 判:5010 单入口、case00 与 case01 互斥,
    `/review/expert` 由 case01 的 review_app 挂(`live_run.py` 的 `with_review`),
    case00 面上压根没有这条路由 —— 写死一个入口就是自己造 404,
    与《给平台侧_嵌入与数据接入》§二"拿错面就是 404"是同一条边界。
    """
    links = list(templates.env.globals.get("extra_nav_links") or [])
    if _face_serves(app, EXPERT_FACE_PATH):
        links.insert(0, {"label": "Expert Review", "url": EXPERT_FACE_PATH,
                         "title": "Expert review: candidate queue, individual verdicts, and corrections "
                                 "(case01 only; writes require no authentication)"})
    return links

router = APIRouter()


def _fmt_ckpt_dt(t: str) -> str:
    """checkpoint 文件名里的 %Y%m%d-%H%M → 可读;解析失败原样返回。"""
    try:
        return _dt.datetime.strptime(t, "%Y%m%d-%H%M").strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError):
        return t or ""


# ---------------------------------------------------------------------------
# 场景 / 引擎元数据(供菜单按"场景 · 引擎"分层,而非单一 case01 中心化)
# ---------------------------------------------------------------------------
def _cases_root() -> str:
    """场景根:环境变量优先(须绝对),否则仓库内 `cases/`。

    2026-10-05 由模块级常量改为函数:原来 import 期就求值一次,
    之后再设 `CASE_ENGINE_CASES_ROOT` 完全不生效(测试/换盘都要重启进程),
    且与 `case_engine.scenarios.default_cases_root()` 的"每次实时读"口径不一致。
    现在两处同走 `case_engine.paths.resolve_root`,不再可能给出不同答案。
    """
    from case_engine.paths import resolve_root
    return resolve_root("CASE_ENGINE_CASES_ROOT", os.path.join(BASE_DIR, "cases"))


def _scenario_meta() -> dict:
    """case_id → {name, engine, engine_name}。读取失败返回空,不影响主流程。"""
    try:
        from case_engine.scenarios import discover
        from case_engine.engines import ENGINES
        out = {}
        for s in discover(_cases_root()):
            eng = s.engine or ""
            out[s.case_id] = {
                "name": s.name or s.case_id,
                "engine": eng,
                "engine_name": (ENGINES.get(eng) or {}).get("name", eng),
            }
        return out
    except Exception:  # noqa: BLE001 —— 元数据拿不到只影响展示名,不拖垮列表
        return {}


# 单次读取,模块重载后自动刷新(每次 /api/runs 调一次,数据量小可接受)
_META_CACHE = {"v": None, "data": {}}


def _scenario_meta_cached() -> dict:
    root = _cases_root()
    if _META_CACHE["v"] != root:
        _META_CACHE["v"] = root
        _META_CACHE["data"] = _scenario_meta()
    return _META_CACHE["data"]


def _decorate(rows: list) -> list:
    """给每条 run 补 case_name/engine_name,并把 case_id 对齐到场景发现层(id)。"""
    meta = _scenario_meta_cached()
    for r in rows:
        cid = r.get("case_id") or ""
        # 归一化:case01_mavis → case01_stock(以场景发现层为准);未知则原样
        m = meta.get(cid)
        if m is None:
            # case01_mavis 是旧命名,map 到 case01_stock 场景
            if cid == "case01_mavis":
                m = meta.get("case01_stock")
        if m:
            r["case_id"] = ("case01_stock" if cid == "case01_mavis" else cid)
            r["case_name"] = m["name"]
            r["engine_id"] = r.get("engine_id") or m["engine"]
            r["engine_name"] = m["engine_name"]
        else:
            r.setdefault("case_name", cid)
            r.setdefault("engine_name", r.get("engine_id") or "")
    return rows


def _brief_checkpoint(name: str) -> dict:
    """归一化一个 case00 checkpoint 目录(对话+决策+倾向序列快照)为统一摘要。

    `incomplete`(2026-09-24 第十轮体检):54 个痕迹目录里有 13 个**没有 conversation.json**
    (测试/冒烟残留:`_test_storage`、`hc`、`mazecheck`、`stage`…),它们在列表里和真痕迹
    长得一样、点开是空页。以前这条信息只体现在"n_turns=0",平台分不出
    "这条是空的"和"这条还没跑完" —— 现在缺什么就写在 `incomplete` 里(不静默)。
    """
    ck_root = os.path.join(_data_root("CASE00_CHECKPOINTS_ROOT", "results", "checkpoints"), name)
    shots = sorted(
        f for f in os.listdir(ck_root)
        if f.startswith("simulate-") and f.endswith(".json")
    )
    start_date = _fmt_ckpt_dt(shots[0][len("simulate-"):-len(".json")]) if shots else ""
    end_date = _fmt_ckpt_dt(shots[-1][len("simulate-"):-len(".json")]) if shots else ""
    n_turns = 0
    conv = os.path.join(ck_root, "conversation.json")
    has_conv = os.path.isfile(conv)
    if has_conv:
        try:
            n_turns = len(json.load(open(conv, encoding="utf-8")) or [])
        except Exception:  # noqa: BLE001 —— 单条损坏不影响列表
            n_turns = 0
    missing = []
    if not has_conv:
        missing.append("缺 conversation.json(没有对话记录)")
    if not shots:
        missing.append("没有任何快照")
    out = {
        "source": "checkpoint",
        "run_id": name,
        "case_id": "case00_village",
        "engine_id": "",
        "branch": "",
        "start_date": start_date,
        "end_date": end_date,
        "n_turns": n_turns,
        "n_reflections": 0,
        "has_graph": bool(os.path.isdir(os.path.join(ck_root, "storage"))),
        # 统一字段:case00 痕迹不是 case01 成品 → 没有一致性戳(unverified,不过滤)
        "quality": "unverified", "consistency": "unverified",
        "branch_source": "", "debug": "",
    }
    if missing:
        out["incomplete"] = ";".join(missing)
    return out


def _brief_review(run_id: str) -> dict:
    """归一化一个 case01 成品 run(读 run.json,与 review_app._brief 同口径)。

    2026-09-21 加质检口径(`quality`/`consistency`/`branch_source`/`debug`),
    **与 5002 契约同源** —— 直接复用 `case01.full_context.quality_of`,
    避免"两处各判一套"漂移。平台按 5010 取数时,看到的集合必须与 5002 一致。
    """
    p = os.path.join(_data_root("CASE01_RUNS_ROOT", "case01", "runs"), run_id, "run.json")
    data = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:  # noqa: BLE001
        return {"source": "review", "run_id": run_id, "case_id": "case01_mavis",
                "engine_id": "", "branch": "", "start_date": "", "end_date": "",
                "n_turns": 0, "n_reflections": 0, "has_graph": False,
                "quality": "unverified", "consistency": "unverified",
                "branch_source": "", "debug": "",
                "error": "读 run.json 失败: {}".format(exc)}
    router_data = data.get("router") or {}
    n_runs = len(data.get("turns") or [])
    q = _quality_of(data)
    return {
        "source": "review",
        "run_id": run_id,
        "case_id": "case01_mavis",
        "engine_id": "mavis" if "injector" in data else "legacy",
        "branch": data.get("branch", ""),
        "start_date": data.get("start_date", ""),
        "end_date": data.get("end_date", ""),
        "n_turns": n_runs,
        "n_reflections": 1 if ((data.get("reflection") or {}).get("text")) else 0,
        "has_graph": bool(router_data.get("issues") or n_runs),
        **q,
    }


def _brief_unreadable(run_id: str) -> dict:
    """**有目录但没有 run.json** 的占位行(2026-10-03 体检加)。

    为什么必须有这一行:原来收集处只有 `if os.path.isfile(.../run.json)`,
    于是"跑崩了、一个字都没落盘"的目录**既不返回、也不计数、也不报 excluded** ——
    平台看到的是"少了一条",分不清"没这条"和"这条废了"。实测当时有 15 个这样的
    目录,契约 §3.1 写的"judge-failed → questionable → 进 excluded"因此**不可达**
    (没有 run.json 就没有 quality_of 可判)。

    铁律:失败了必须有人知道。这里给它 `quality="unreadable"`,进 `_HIDDEN_QUALITY`
    (不给平台建单)但**计入 excluded 并报明 run_id 与原因**。
    """
    return {"source": "review", "run_id": run_id, "case_id": "case01_mavis",
            "engine_id": "", "branch": "", "start_date": "", "end_date": "",
            "n_turns": 0, "n_reflections": 0, "has_graph": False,
            "quality": "unreadable", "consistency": "unverified",
            "branch_source": "", "debug": "",
            "error": "run 目录存在但没有 run.json(该次 run 未跑成或未落盘)"}


def _quality_of(rec: dict) -> dict:
    """质检标记:优先用 case01 的判定(单一来源);拿不到就 unverified。

    case00 的痕迹/压缩成品不是 case01 成品记录,没有一致性戳 —— 那是"判不了",
    不是"有问题",所以给 unverified 而不是过滤掉。
    """
    try:
        from case01.full_context import quality_of
        q = quality_of(rec)
        return {"quality": q.get("quality", "unverified"),
                "consistency": q.get("consistency", "unverified"),
                # reason 也要透出(2026-10-03 补):只给一个 `unverified` 标签,
                # 平台分不出"没验过"与"验了但判不了",更看不出**为什么**。
                # `quality_of` 本来就返回 reason,这里以前把它丢了。
                "reason": q.get("reason", ""),
                "branch_source": q.get("branch_source", ""),
                "debug": q.get("debug", ""),
                # 废弃标记(2026-09-24):索引行里必须带,平台才能自己筛;
                # 只靠"默认隐藏"是不够的(一条自洽的废弃样本以前会被判 ok 发出去)。
                "deprecated": bool(q.get("deprecated")),
                "deprecated_reason": q.get("deprecated_reason", ""),
                # 清单警告(2026-10-04 接进质检):以前这里逐键挑,新键会被静默丢掉
                "manifest_status": q.get("manifest_status", "unverified"),
                "manifest_warning_count": q.get("manifest_warning_count", 0),
                "manifest_warnings": q.get("manifest_warnings", [])}
    except Exception:  # noqa: BLE001 - 引擎侧缺席时不能假装"没问题"
        return {"quality": "unverified", "consistency": "unverified",
                "reason": "取不到 case01 质检判定", "branch_source": "", "debug": "",
                "deprecated": False, "deprecated_reason": "",
                # 读不到判定 ≠ 清单是空的:如实写 unverified,免得平台把"没查"当"没警告"
                "manifest_status": "unverified", "manifest_warning_count": 0,
                "manifest_warnings": []}


# 默认不给平台的质检类别(与 5002 `serve.list_runs` 同口径):
#   questionable = 预设分支与 AI 的 T0 立场自相矛盾,或判定失败(run 停在 T0)
#   debug        = 调试跑(内容不完整)
#   deprecated   = 记录被显式标废弃(样本作废;2026-09-24 加 —— 一条自洽的废弃样本同样是废的,
#                  以前它只要恰好判成 ok 就会被静默发给平台)
#   unreadable   = **有 run 目录但没有 run.json**(2026-10-03 加):该次 run 压根没跑成,
#                  以前这类目录既不返回也不计数,平台无从得知(契约 §3.1 的
#                  "judge-failed → 进 excluded" 因此不可达)。默认不给平台建单,
#                  但计入 excluded 并报明 run_id 与原因。
_HIDDEN_QUALITY = ("questionable", "debug", "deprecated", "unreadable")

# `/api/runs` 的 `source` 合法取值(与 `_brief_checkpoint`/`_brief_review`/
# `_brief_compressed` 里写的 source 字段一一对应)。
_SOURCE_VALUES = ("checkpoint", "review", "compressed")


def expert_safe_record(rec: dict) -> dict:
    """从 case01 成品记录里挑出**可以给专家看**的部分(白名单)。

    为什么必须白名单:原始 `run.json` 里含 `injector`(Branch 实验元信息 + 逐节点
    dialogue 原文)、`branch_action`、`consistency`、`debug`/`quality` —— 按
    《Governance平台对接说明》§2.2/§2.3 与 0904doc 04 六.3,**这些不得进专家视图**。
    以前 `/api/run-detail` 直接回整个 run.json,等于把实验底牌一起递出去。

    白名单 = 平台契约 §2.2 明确要读的那些键。

    但**顶层白名单挡不住嵌在里面的分支真值**(2026-09-25 第十二轮体检实测):
    `state_history[*].state.branch` 每个日快照都带一个 "A"/"B"/"C",
    `audit` 里还有一条 `{"action": "set_branch", "branch": "A"}` ——
    也就是说安全视图以前把**这一局走的是哪条线**直接告诉了专家(不是"有分支这个概念",
    而是分支的取值)。所以下面再按路径清一遍(见 `_NESTED_EXPERIMENT_KEYS`)。
    """
    keep = ("run_id", "start_date", "end_date", "turns", "retrievals", "events",
            "state_history", "final_feedback", "audit", "condition_monitor",
            "reflection", "router", "summary", "compat")
    out = {k: rec.get(k) for k in keep if k in rec}
    # reflection/router 是专家审核的核心,缺了就补空结构(前端不必判 None)
    out.setdefault("reflection", {})
    out.setdefault("router", {"issues": []})
    _scrub_experiment_values(out)
    out["_view"] = "expert-safe"
    return out


# 嵌在各块里、会泄露"这一局是哪条线"的字段:值清空而不是整块删掉 ——
# 删块会改动专家要看的记录结构(平台按契约读 `state_history`/`audit`),
# 清值只丢掉实验底牌,不影响证据链的形状。
_NESTED_EXPERIMENT_KEYS = ("branch",)


def _scrub_experiment_values(out: dict) -> None:
    """就地清掉嵌套的实验取值(state_history[*].state.branch、audit[*].branch)。

    `audit` 里那条 `set_branch` 条目保留(它是"当时定过方向"的事实,删了等于改审计),
    但把 `branch` 的值清掉;它的 `action` 名字仍在 —— 与第九轮记的"页面源码里
    还留着机制名"同属一个待派单项(把实验相关的 JS/字段整体拆出去),这里先止血:
    **取值**绝不能再发给专家。

    注意**写时复制**:`out` 里的块与入参 `rec` 共用同一个对象,直接 pop 会连原记录
    一起改掉 —— 同一进程里接着取裸视图就会"莫名其妙少了字段"。
    """
    hist = out.get("state_history")
    if isinstance(hist, list):
        new_hist = []
        for h in hist:
            if not isinstance(h, dict):
                new_hist.append(h)
                continue
            st = h.get("state")
            if isinstance(st, dict) and any(k in st for k in _NESTED_EXPERIMENT_KEYS):
                h = dict(h)
                h["state"] = {k: v for k, v in st.items()
                              if k not in _NESTED_EXPERIMENT_KEYS}
            new_hist.append(h)
        out["state_history"] = new_hist
    audit = out.get("audit")
    if isinstance(audit, list):
        out["audit"] = [
            {k: v for k, v in a.items() if k not in _NESTED_EXPERIMENT_KEYS}
            if isinstance(a, dict) and any(k in a for k in _NESTED_EXPERIMENT_KEYS) else a
            for a in audit
        ]
    _scrub_router_raw(out)


# `router.raw` 是 Router 的**未加工模型原文**(含 `routing_reason` 等推理过程),
# 全库 311 条记录都带(2026-10-05 第六轮只读核查 M6)。
# 给专家视图的应是**归一后**的 `issues`(结构化 id/summary/risk_note),不是原文 ——
# 原文里既有冗长的模型推理,也可能随提示词演进而夹带不该外发的内容;把它留在
# 白名单里等于"顶层白名单做了,嵌进去的整块又漏出去"(与 2026-09-25 第十二轮
# 的 `state_history[*].state.branch` 是同一类事故)。
# 处理方式与 `_scrub_experiment_values` 一致:**写时复制**,只投影 router 块,
# 不动入参 `rec` 的共用对象(否则同进程的裸视图会莫名少字段)。
_EXPERT_ROUTER_KEYS = ("issues",)


def _scrub_router_raw(out: dict) -> None:
    """把专家视图里的 `router` 投影成只留 `issues`(去 `raw` 原文)。

    契约层是白名单唯一实现处(见 `case01/review_app.py` 的注释),所以这里改一处,
    `/api/run-detail` 与 run 导出两条链同时生效。
    """
    router = out.get("router")
    if isinstance(router, dict) and "raw" in router:
        out["router"] = {k: v for k, v in router.items()
                         if k in _EXPERT_ROUTER_KEYS}


def _brief_compressed(name: str) -> dict:
    """归一化一个压缩成品目录(只提供文件清单,没有逐轮/逐日结构)。

    `incomplete`(2026-09-24 第十轮体检):这些行除 run_id 外**什么元信息都没有**
    (日期/轮次/一致性全是空),平台拿它没法建单也没法展示。以前列表里看不出来,
    只有点进详情才发现"只有一个文件清单"。现在明说了。
    """
    return {"source": "compressed", "run_id": name, "case_id": "", "engine_id": "",
            "branch": "", "start_date": "", "end_date": "", "n_turns": 0,
            "n_reflections": 0, "has_graph": False,
            # 统一字段:不是 case01 成品就没有一致性戳 → unverified(判不了 ≠ 有问题)
            "quality": "unverified", "consistency": "unverified",
            "branch_source": "", "debug": "",
            "incomplete": "压缩成品只给文件清单(无逐轮/逐日结构,不要当成品记录用)"}


@router.get("/api/runs")
async def list_all_runs(request: Request = None, include_questionable: bool = False,
                        limit: int = 0, offset: int = 0,
                        source: str = "") -> JSONResponse:
    """统一历史数据:case00 checkpoints + case01 成品 run + 压缩成品,合而为一。

    默认**不给** `quality=questionable`/`debug` 的记录(与 5002 契约同口径),
    但**不静默**:响应里的 `excluded` 给出计数、run_id 与原因;
    `?include_questionable=1` 取全量。

    分页/过滤(2026-09-23,平台侧嵌入面在记录攒到几百条时需要):
    `?limit=`/`?offset=`/`?source=review|checkpoint|compressed`;
    `count` 是本页条数,`total` 是过滤后总数。**不认识的参数不静默吞掉** ——
    回在 `ignored_params` 里,免得平台以为 `?quality=ok` 生效了。
    `source` 的**取值**同样要认(2026-10-10):写了不在 `_SOURCE_VALUES` 里的值
    (实测最容易猜错的是 `?source=case01`)照它过滤必然是 0 条,这种 0 会写进
    `invalid_source` + `invalid_source_note`,不让平台读成"case01 没有数据"。
    """
    runs = []
    source_errors = []

    ck_root, ck_err = _data_root_soft("CASE00_CHECKPOINTS_ROOT", "results", "checkpoints")
    if ck_err:
        source_errors.append({"source": "checkpoint", "error": ck_err})
    if ck_root and os.path.isdir(ck_root):
        for n in sorted(os.listdir(ck_root)):
            if os.path.isdir(os.path.join(ck_root, n)):
                runs.append(_brief_checkpoint(n))

    c1_root, c1_err = _data_root_soft("CASE01_RUNS_ROOT", "case01", "runs")
    if c1_err:
        source_errors.append({"source": "review", "error": c1_err})
    if c1_root and os.path.isdir(c1_root):
        for n in sorted(os.listdir(c1_root)):
            d = os.path.join(c1_root, n)
            if not os.path.isdir(d):
                continue
            if os.path.isfile(os.path.join(d, "run.json")):
                runs.append(_brief_review(n))
            else:
                # 没落盘也要**留一行**(2026-10-03):否则失败样本对平台彻底隐形,
                # 平台分不清"没这条"和"这条废了"。详见 _brief_unreadable。
                runs.append(_brief_unreadable(n))

    comp_root, comp_err = _data_root_soft("RESULTS_COMPRESSED_ROOT", "results", "compressed")
    if comp_err:
        source_errors.append({"source": "compressed", "error": comp_err})
    if comp_root and os.path.isdir(comp_root):
        for n in sorted(os.listdir(comp_root)):
            if os.path.isdir(os.path.join(comp_root, n)):
                runs.append(_brief_compressed(n))

    _decorate(runs)

    hidden = []
    if not include_questionable:
        keep = []
        for r in runs:
            if str(r.get("quality") or "unverified") in _HIDDEN_QUALITY:
                # 带上原因:只给 run_id 和类别,平台还是不知道为什么被排除。
                # 不允许静默 —— 被排除也是一条需要人能看出来的结论。
                item = {"run_id": r.get("run_id"), "quality": r.get("quality")}
                # 原因来源有两种,别只认一种(2026-10-03 体检):
                # - unreadable/读盘失败走 `error` 键;
                # - review 成品的质检原因(矛盾/判定失败/反思没生成/废弃)走
                #   quality_of 返回的 `reason` 键 —— 以前只取 error,于是这些记录
                #   被排除时平台只看得到标签、看不到原因,reason 透了等于没透。
                why = r.get("error") or r.get("reason") or ""
                if why:
                    item["reason"] = why
                if r.get("deprecated_reason"):
                    item["deprecated_reason"] = r["deprecated_reason"]
                if r.get("consistency"):
                    item["consistency"] = r["consistency"]
                hidden.append(item)
            else:
                keep.append(r)
        runs = keep

    # 倒序:结束时间最新在前;无时间的归到最后。
    runs.sort(key=lambda r: (str(r.get("end_date", "") or ""), str(r.get("run_id", "") or "")),
              reverse=True)
    # 认识的查询参数就这几个;别的(比如 ?quality=ok)不许静默吞掉 → ignored_params
    known = {"include_questionable", "limit", "offset", "source"}
    ignored = sorted({k for k in (request.query_params.keys() if request is not None else [])
                      if k not in known})
    bad_source = ""
    if source:
        runs = [r for r in runs if str(r.get("source") or "") == source]
        # `source` 的**取值**也要认(2026-10-10):以前只认参数名,于是平台敲一个很自然的
        # 猜法 `?source=case01` 会拿到 count=0 —— 与本文件 `source_errors` 那条同族陷阱
        # ("看到 0 条以为这个源没数据")。只要 case01 成品的是 `source=review`。
        if source not in _SOURCE_VALUES:
            bad_source = source
    total = len(runs)
    if offset > 0:
        runs = runs[offset:]
    if limit > 0:
        runs = runs[:limit]
    # 把场景发现层也带给前端,供菜单分层(而非前端写死 case01)
    meta = _scenario_meta_cached()
    scenarios = [{"case_id": cid, **m} for cid, m in sorted(meta.items())]
    body = {"count": len(runs), "total": total, "runs": runs, "scenarios": scenarios,
            "filter": {"include_questionable": bool(include_questionable),
                       "source": source, "limit": limit, "offset": offset}}
    if ignored:
        body["ignored_params"] = ignored
        body["ignored_note"] = "这些参数本接口不认(没生效):{}".format(",".join(ignored))
    if bad_source:
        body["invalid_source"] = bad_source
        body["invalid_source_note"] = (
            "`source` 这个参数只认 {} —— 收到的 {!r} 不是其中之一,仍照它过滤,所以必然 0 条。"
            "0 条不代表没有数据:只要 case01 成品记录,用 `?source=review`。".format(
                "/".join(_SOURCE_VALUES), bad_source))
    if source_errors:
        # 数据源解析失败(如相对根配置)不是"没数据",必须显式报出 ——
        # 否则平台看到 count=0 会以为"这个源本来就空"(2026-10-05 M2 推广)。
        body["source_errors"] = source_errors
        body["source_errors_note"] = (
            "以下数据源因其根目录配置无法解析而**整块跳过**(不是没有数据):"
            + ";".join("{}={}".format(e["source"], e["error"]) for e in source_errors))
    if hidden:
        body["excluded"] = {
            "count": len(hidden), "runs": hidden,
            "reason": "questionable=预设分支与 AI 的 T0 立场矛盾,或判定失败(停在 T0);"
                      "debug=调试跑(内容不完整);deprecated=记录已被显式标废弃(样本作废);"
                      "unreadable=有 run 目录但 run.json 不在(该次 run 未落盘)。"
                      "加 ?include_questionable=1 取全量",
        }
    return JSONResponse(body)


@router.get("/embed/explore", response_class=HTMLResponse)
async def explore_page(request: Request) -> HTMLResponse:
    """数据界面:纯数据浏览,不依赖运行状态;由 history.html 渲染。"""
    return templates.TemplateResponse(request, "history.html",
                                      {"embed": "explore", "title": "历史数据",
                                       "extra_nav_links": nav_links_for(request.app)})


@router.get("/api/review-package/{run_id}", response_model=ReviewPackageResponse,
            responses={404: {"description": "Run 不存在或路径非法"},
                       422: {"description": "记录或类别目录格式错误"},
                       503: {"description": "记录或类别目录暂不可读"}})
async def review_package(run_id: str) -> JSONResponse:
    """平台建单用只读交接包；与实时 case 无关，不分配专家、不接收审核写回。"""
    from case01.review_export import build_review_package

    if not run_id or run_id in (".", "..") or any(x in run_id for x in ("/", "\\", "\x00")):
        return JSONResponse({"ok": False, "error": "invalid_run_id"}, status_code=404)
    try:
        # `_data_root` 必须在 try 内(2026-10-05 第六轮 M2):环境变量给了**相对路径**
        # 时 `require_abs_path` 抛 `AmbiguousPathError(ValueError)`,原实现在 try 外,
        # 于是"配置是相对根"这个可预期的输入错直接冒成 500(未捕获异常),
        # 而不是下面那三个明确的原因码。挪进来后落入 `ValueError` → 422。
        root = os.path.realpath(_data_root("CASE01_RUNS_ROOT", "case01", "runs"))
        path = os.path.realpath(os.path.join(root, run_id, "run.json"))
        if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
            return JSONResponse({"ok": False, "error": "run_not_found"}, status_code=404)
        with open(path, encoding="utf-8") as stream:
            record = json.load(stream)
        package = build_review_package(record, run_id)
    except (ValueError, TypeError, KeyError):
        return JSONResponse({"ok": False, "error": "invalid_review_record_or_catalog"},
                            status_code=422)
    except OSError:
        return JSONResponse({"ok": False, "error": "review_source_unavailable"}, status_code=503)
    return JSONResponse({"ok": True, "data": package}, headers={
        "ETag": '"{}"'.format(package["revision"]), "Cache-Control": "no-store"})


@router.get("/api/run-detail/{source}/{run_id}")
async def run_detail(source: str, run_id: str, raw: bool = False) -> JSONResponse:
    """共享只读详情:按 source 返回该 run 的数据,供前端自渲染。

    刻意不调用 case01 实时面的 `/embed/review`——那路由只挂在 case01 侧,
    case00 跑起来会 404(实测踩过)。历史界面应该**自含**详情渲染,
    与"当前跑哪个 case"无关,任何实时面都能打开所有 run 的详情。
    source ∈ {review, checkpoint, compressed};路径白名单,杜绝穿越。

    **默认给"专家安全视图"**(2026-09-21):case01 成品只回
    `expert_safe_record()` 白名单里的键 —— 原始 run.json 里的 `injector`(Branch
    实验元信息 + 逐节点 dialogue 原文)、`branch_action`、`consistency`、`debug`/`quality`
    按契约不得进专家视图。要原始全文(内部排查/引擎侧)加 `?raw=1`。
    """
    if not run_id or run_id in (".", "..") or "/" in run_id or "\\" in run_id:
        return JSONResponse({"ok": False, "errors": ["非法 run_id: {}".format(run_id)]},
                            status_code=404)
    if source == "review":
        root, rerr = _data_root_soft("CASE01_RUNS_ROOT", "case01", "runs")
        if rerr:
            return JSONResponse({"ok": False, "errors": [rerr]}, status_code=422)
        p = os.path.join(root, run_id, "run.json")
        if not os.path.isfile(p):
            return JSONResponse({"ok": False, "errors": ["没有该 case01 成品: {}".format(run_id)]},
                                status_code=404)
        try:
            with open(p, "r", encoding="utf-8") as f:
                rec = json.load(f)
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "errors": ["读 run.json 失败: {}".format(exc)]},
                                status_code=500)
        if raw:
            return JSONResponse({"ok": True, "source": "review", "run_id": run_id,
                                 "view": "raw", "data": rec})
        return JSONResponse({"ok": True, "source": "review", "run_id": run_id,
                             "view": "expert-safe",
                             "data": expert_safe_record(rec)})
    if source == "checkpoint":
        ck_base, cerr = _data_root_soft("CASE00_CHECKPOINTS_ROOT", "results", "checkpoints")
        if cerr:
            return JSONResponse({"ok": False, "errors": [cerr]}, status_code=422)
        ck_root = os.path.join(ck_base, run_id)
        if not os.path.isdir(ck_root):
            return JSONResponse({"ok": False, "errors": ["没有该 case00 痕迹: {}".format(run_id)]},
                                status_code=404)
        conv = {}
        cpath = os.path.join(ck_root, "conversation.json")
        missing = []
        if os.path.isfile(cpath):
            try:
                with open(cpath, "r", encoding="utf-8") as f:
                    conv = json.load(f) or {}
            except Exception:  # noqa: BLE001
                conv = {"error": "conversation.json 不可读"}
                missing.append("conversation.json 存在但读不出(已损坏?)")
        else:
            # 不静默(2026-09-24 第十轮体检):以前这里回 `conversation: {}` + `shots: []`,
            # 200 正常响应 —— 平台点开一条测试残留痕迹只会看到空白,不知道是"没有"还是"坏了"。
            missing.append("缺 conversation.json(没有对话记录)")
        shots = sorted(f for f in os.listdir(ck_root)
                       if f.startswith("simulate-") and f.endswith(".json"))
        if not shots:
            missing.append("没有任何快照")
        body = {"ok": True, "source": "checkpoint", "run_id": run_id,
                # view(2026-09-24):三种 source 的 data 形状**完全不同**,
                # 客户端得有一个字段能判断自己在渲染哪一套;以前只有 review 带 view。
                "view": "case00-archive",
                "data": {"conversation": conv, "shots": shots}}
        if missing:
            body["incomplete"] = ";".join(missing)
        return JSONResponse(body)
    if source == "compressed":
        comp_base, xerr = _data_root_soft("RESULTS_COMPRESSED_ROOT", "results", "compressed")
        if xerr:
            return JSONResponse({"ok": False, "errors": [xerr]}, status_code=422)
        comp_root = os.path.join(comp_base, run_id)
        if not os.path.isdir(comp_root):
            return JSONResponse({"ok": False, "errors": ["没有该压缩成品: {}".format(run_id)]},
                                status_code=404)
        files = sorted(f for f in os.listdir(comp_root))
        return JSONResponse({"ok": True, "source": "compressed", "run_id": run_id,
                             "view": "compressed-index",
                             "incomplete": "压缩成品只给文件清单(无逐轮/逐日结构,不要当成品记录用)",
                             "data": {"files": files}})
    return JSONResponse({"ok": False, "errors": ["未知 source: {}".format(source)]},
                        status_code=404)
