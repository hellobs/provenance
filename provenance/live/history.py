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

# 统一根:本文件位于 <root>/live/history.py,向上两级即 provenance/provenance。
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_root(env_var: str, *parts: str) -> str:
    """数据根目录:环境变量优先,否则用仓库内默认位置。

    为什么必须可覆盖(2026-09-22):`case01/runs` 按约定**不入库**(只入约定与代码),
    所以"成品记录在哪"不能在代码里写死 —— 写死的后果是 CI 上没有记录可读,而
    `tests/test_live_history_quality.py` 又要断言"至少有一条",于是 CI 必红。
    现在:CI 用 `CASE01_RUNS_ROOT` 指向签入的夹具(`tests/fixtures/case01_runs`),
    真实代码路径照样跑;异地部署换数据盘也不用改代码。
    """
    return os.environ.get(env_var) or os.path.join(BASE_DIR, *parts)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "frontend/templates"))
# 顶栏外部工具链接:mavis 仓的 config_tool 是**独立进程**(默认 8060),
# 地址可用环境变量 MAVIS_CONFIG_TOOL_URL 覆盖(换机/换端口)。
templates.env.globals["extra_nav_links"] = [
    {"label": "配置工具 ↗", "url": os.environ.get("MAVIS_CONFIG_TOOL_URL",
                                                 "http://127.0.0.1:8060/"),
     "title": "mavis 的角色/场景配置工具(独立进程;地址用 MAVIS_CONFIG_TOOL_URL 覆盖)"},
]

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
_CASES_ROOT = os.environ.get(
    "CASE_ENGINE_CASES_ROOT", os.path.join(BASE_DIR, "cases"))


def _scenario_meta() -> dict:
    """case_id → {name, engine, engine_name}。读取失败返回空,不影响主流程。"""
    try:
        from case_engine.scenarios import discover
        from case_engine.engines import ENGINES
        out = {}
        for s in discover(_CASES_ROOT):
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
    if _META_CACHE["v"] != _CASES_ROOT:
        _META_CACHE["v"] = _CASES_ROOT
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
                "branch_source": q.get("branch_source", ""),
                "debug": q.get("debug", ""),
                # 废弃标记(2026-09-24):索引行里必须带,平台才能自己筛;
                # 只靠"默认隐藏"是不够的(一条自洽的废弃样本以前会被判 ok 发出去)。
                "deprecated": bool(q.get("deprecated")),
                "deprecated_reason": q.get("deprecated_reason", "")}
    except Exception:  # noqa: BLE001 - 引擎侧缺席时不能假装"没问题"
        return {"quality": "unverified", "consistency": "unverified",
                "branch_source": "", "debug": "",
                "deprecated": False, "deprecated_reason": ""}


# 默认不给平台的质检类别(与 5002 `serve.list_runs` 同口径):
#   questionable = 预设分支与 AI 的 T0 立场自相矛盾,或判定失败(run 停在 T0)
#   debug        = 调试跑(内容不完整)
#   deprecated   = 记录被显式标废弃(样本作废;2026-09-24 加 —— 一条自洽的废弃样本同样是废的,
#                  以前它只要恰好判成 ok 就会被静默发给平台)
_HIDDEN_QUALITY = ("questionable", "debug", "deprecated")


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
    """
    runs = []

    ck_root = _data_root("CASE00_CHECKPOINTS_ROOT", "results", "checkpoints")
    if os.path.isdir(ck_root):
        for n in sorted(os.listdir(ck_root)):
            if os.path.isdir(os.path.join(ck_root, n)):
                runs.append(_brief_checkpoint(n))

    c1_root = _data_root("CASE01_RUNS_ROOT", "case01", "runs")
    if os.path.isdir(c1_root):
        for n in sorted(os.listdir(c1_root)):
            if os.path.isfile(os.path.join(c1_root, n, "run.json")):
                runs.append(_brief_review(n))

    comp_root = _data_root("RESULTS_COMPRESSED_ROOT", "results", "compressed")
    if os.path.isdir(comp_root):
        for n in sorted(os.listdir(comp_root)):
            if os.path.isdir(os.path.join(comp_root, n)):
                runs.append(_brief_compressed(n))

    _decorate(runs)

    hidden = []
    if not include_questionable:
        keep = []
        for r in runs:
            if str(r.get("quality") or "unverified") in _HIDDEN_QUALITY:
                hidden.append({"run_id": r.get("run_id"), "quality": r.get("quality")})
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
    if source:
        runs = [r for r in runs if str(r.get("source") or "") == source]
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
    if hidden:
        body["excluded"] = {
            "count": len(hidden), "runs": hidden,
            "reason": "questionable=预设分支与 AI 的 T0 立场矛盾,或判定失败(停在 T0);"
                      "debug=调试跑(内容不完整);deprecated=记录已被显式标废弃(样本作废)。"
                      "加 ?include_questionable=1 取全量",
        }
    return JSONResponse(body)


@router.get("/embed/explore", response_class=HTMLResponse)
async def explore_page(request: Request) -> HTMLResponse:
    """数据界面:纯数据浏览,不依赖运行状态;由 history.html 渲染。"""
    return templates.TemplateResponse(request, "history.html",
                                      {"embed": "explore", "title": "历史数据"})


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
        p = os.path.join(_data_root("CASE01_RUNS_ROOT", "case01", "runs"), run_id, "run.json")
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
        ck_root = os.path.join(_data_root("CASE00_CHECKPOINTS_ROOT", "results", "checkpoints"),
                               run_id)
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
        comp_root = os.path.join(_data_root("RESULTS_COMPRESSED_ROOT", "results", "compressed"),
                                 run_id)
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