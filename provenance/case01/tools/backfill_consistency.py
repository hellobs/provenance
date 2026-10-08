# -*- coding: utf-8 -*-
"""回填一致性戳:给**已有的** run.json 补上 `branch_action.source` 与 `consistency`。

为什么要单独一个工具:已有的记录是"预设分支但记录里没说"的产物(还带完整反思/分流)。
重跑映射要 ~90 秒 LLM 且会覆盖 reflection/router,风险大;这一步是**纯计算**,
只往记录里加两个字段,不碰其它任何内容。

用法(在 provenance/provenance 下):

    python -m case01.tools.backfill_consistency            # 只看会怎么改(不写盘)
    python -m case01.tools.backfill_consistency --write    # 真写
    python -m case01.tools.backfill_consistency --write --runs-dir <记录根,默认 data/case01/runs>

**边界**:本工具只会算 `quick_scan`(关键词快筛)。记录上已有 `llm_stance`(立场判官,
主判据)的戳时**不覆盖它** —— 用弱判据盖掉强判据是口径倒退;source 该补的照补。
"""
import argparse
import glob
import io
import json
import os

from case_engine.paths import data_root
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from case01.consistency import quick_scan  # noqa: E402

# 已有立场判官戳的记录本工具不动(见 `backfill`);main 靠这个前缀识别并单独计数。
_SKIP_NOTE = "已有立场判官(llm_stance)戳,不覆盖"


def detect_branch_source(ba: dict) -> str:
    """从 branch_action 反推分支来源,**不靠猜**(2026-10-03 体检修)。

    原来是无条件 `setdefault("source", "preset")` —— 那只对"分支由运行参数指定"
    的老记录成立。现在批量跑出来的记录是 **judge 模式**(分支由 T0 回答判定),
    实测 185 份 run.json 里 134 份没写 source 且全带 judge 特征,照原样回填会
    把 72% 的记录**误标成 preset**:preset 与 judge 在平台侧是不同语义,标错了
    研究者会以为这些样本是预设分支跑的。

    判据(按可靠性排序):
      - `judge` 字段以 llm 开头  → judge(它就是那个判官写的)
      - 有 `attempts`/`raw_outputs` → judge(只有判官路径才会留这两样)
      - 有 `timeline` → preset(judge 模式下**不能**用它当判据:实测存在
        `source=judge` 却同时带 `timeline=B` 的记录)
    判不出来就返回 "unknown" —— 宁可标"不知道",也不谎报一个具体来源。
    """
    judge = str(ba.get("judge") or "")
    if judge.startswith("llm"):
        return "judge"
    if judge == "judge-failed":
        return "judge-failed"
    # 编排器强制分支时写的是 judge="forced"(那是运行参数指定的,属 preset 侧);
    # 规则判定器写的是 judge="rules"。
    if judge == "forced":
        return "preset"
    if judge == "rules":
        return "rules"
    if ba.get("attempts") is not None or ba.get("raw_outputs") is not None:
        return "judge"
    if ba.get("timeline"):
        return "preset"
    return "unknown"


def _backup_and_write(path, original, rec):
    """先把**原始内容**备份再写(runs/ 不入库,写坏了没法从 git 回滚 —— 同
    backfill_failed_stages.py:153 的做法)。备份的是改动前那份,不是改后的。"""
    with io.open(path + ".bak", "w", encoding="utf-8") as f:
        f.write(original)
    with io.open(path, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)


def backfill(path, write=False):
    with io.open(path, encoding="utf-8") as f:
        original = f.read()
    rec = json.loads(original)
    before = (rec.get("branch_action") or {}).get("source"), (rec.get("consistency") or {}).get("verdict")
    ba = dict(rec.get("branch_action") or {})
    # 已有 source 就**不覆盖**:那是当初跑的时候写下的事实,回填不该改写它。
    filled_source = not ba.get("source")
    if filled_source:
        ba["source"] = detect_branch_source(ba)
    rec["branch_action"] = ba
    existing = rec.get("consistency") or {}
    if str(existing.get("method", "")).startswith("llm_stance"):
        # 同一条规矩也适用于戳本身(2026-10-04 体检修):本工具是**纯计算**的
        # quick_scan,无条件覆盖会把立场判官(主判据)的结论降回关键词快筛 ——
        # 判官重算过的记录必须比任何后续回填优先。source 该补的照补,只是不碰戳。
        note = _SKIP_NOTE + "(保留 method={})".format(existing.get("method"))
        if write and filled_source:
            _backup_and_write(path, original, rec)
        return before, (ba["source"], existing.get("verdict")), note
    verdict, reason = quick_scan(rec)
    rec["consistency"] = {"verdict": verdict, "reason": reason, "method": "quick_scan",
                          "branch_source": ba.get("source")}
    after = (ba["source"], verdict)
    if write:
        _backup_and_write(path, original, rec)
    return before, after, reason


def main(argv=None):
    ap = argparse.ArgumentParser(description="给已有记录回填一致性戳(纯计算)")
    # 2026-10-08:默认值原为相对路径 "case01/runs"(成品记录已搬到 data/case01/runs,
    # 老默认会直接报"没找到记录");改走 data_root,与环境变量同一口径。
    ap.add_argument("--runs-dir", default=data_root("case01.records",
                                                    env_var="CASE01_RUNS_ROOT"))
    ap.add_argument("--write", action="store_true", help="真写盘(默认只看)")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.runs_dir, "*", "run.json")))
    if not paths:
        print("没找到记录:", args.runs_dir)
        return 1
    n_bad = 0
    n_skip = 0
    for p in paths:
        before, after, reason = backfill(p, write=args.write)
        run_id = os.path.basename(os.path.dirname(p))
        if reason.startswith(_SKIP_NOTE):
            n_skip += 1
        flag = {"consistent": "一致  ", "inconsistent": "不一致", "unknown": "判不了"}.get(after[1], after[1])
        if after[1] != "consistent":
            n_bad += 1
        print("  {:<40} {}   source {} -> {}   verdict {} -> {}".format(
            run_id, flag, before[0] or "-", after[0], before[1] or "-", after[1]))
        if after[1] != "consistent":
            print("      {}".format(reason))
    print("\n合计 {} 条,其中不一致/判不了 {} 条;已有立场判官戳被保留 {} 条;{}".format(
        len(paths), n_bad, n_skip, "已写盘" if args.write else "未写盘(--write 才写)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
