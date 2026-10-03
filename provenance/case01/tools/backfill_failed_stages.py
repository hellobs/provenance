# -*- coding: utf-8 -*-
"""回填「反思/Router 未跑成」的记录(2026-10-03 批量实验后修)。

**背景**:修 `orchestrator.py` 之前,Reflection+Router 是一个 try 包两步,
失败时只写 `reflection={"material":"","text":"(失败)"}` —— 于是:

1. `router` 字段压根没赋值 → 平台契约的九块缺一块(完整性守卫红);
2. `reflection.quality` 缺失 → 统计把"(失败)"这 4 个字当成一篇超短反思
   算进字数均值,质量门无从判断。

这个工具把**已经写坏的历史记录**按新契约补齐。原则:

- **只补缺失,不覆盖已有的**。已有 `router.issues` / `quality.score` 一律不动,
  避免把真实数据当成失败处理。
- **区分「失败」与「跳过」**:证据是 `reflection.text == "(失败)"` ——
  这说明 `run_reflection` 先抛了,Router 压根没被调用,所以是 `skipped`;
  只有 reflection 正常而 Router 炸了,才是 `error`。
- **默认 dry-run**,`--apply` 才写盘;写盘保持原排版(indent=2、无末尾换行),
  避免产生无谓的大 diff。

用法:
    python -m case01.tools.backfill_failed_stages                # 只报不改
    python -m case01.tools.backfill_failed_stages --apply        # 真改
"""
import argparse
import io
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../case01/tools
CASE01_DIR = os.path.dirname(_HERE)                          # .../case01
_PKG = os.path.dirname(CASE01_DIR)                           # .../provenance
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

RUNS_DIR = os.path.join(CASE01_DIR, "runs")

# 旧实现留下的失败指纹
_LEGACY_FAIL_TEXT = "(失败)"

_INDENT = 2


def _failed_quality(detail: str) -> dict:
    return {
        "version": "1.0", "score": None, "max_score": 100,
        "status": "error", "components": {}, "dimensions": [],
        "matched_case_anchors": [],
        "failure_reasons": ["反思生成失败"],
        "manual_review_required": [],
        "error": detail,
    }


def _failed_router(detail: str, executed: bool) -> dict:
    return {
        "raw": "", "issues": [], "postprocess": {},
        "expert_pool_version": "",
        "status": "error" if executed else "skipped",
        "executed": bool(executed),
        "error": detail,
    }


def _detect(d: dict):
    """返回 (要补什么, 原因)。不需要补则返回 (None, None)。

    **只认失败指纹** `reflection.text == "(失败)"`。绝不能因为"没有 quality 字段"
    就判定失败 —— 质量门是 2026-10-03 才上线的(`a24a8b0`),在那之前的 25 条答辩
    基线记录反思都是好的、只是没有 quality。把它们标成 error 属于伪造故障。
    缺 quality 的老记录由 batch_analyze 的 `status_dist["(无)"]` 如实呈现,不静默。
    """
    patches = {}
    refl = d.get("reflection")
    legacy_failed = (isinstance(refl, dict)
                     and (refl.get("text") or "").strip() == _LEGACY_FAIL_TEXT)

    if not isinstance(refl, dict) or not (refl.get("text") or "").strip():
        # 反思整块缺失,或正文为空 —— 视为未跑成
        patches["reflection"] = {
            "material": "", "text": "(反思生成失败)",
            "quality": _failed_quality("历史记录:反思缺失或正文为空"),
        }
    elif legacy_failed:
        # 旧实现的失败占位:正文就是 "(失败)",且没有 quality
        patches["reflection"] = {
            "material": refl.get("material", ""),
            "text": "(反思生成失败)",
            "quality": _failed_quality("历史记录:旧实现写入的失败占位(未记录原因)"),
        }

    if not isinstance(d.get("router"), dict):
        # 证据链:反思正文是旧失败指纹 → run_reflection 先抛,Router 压根没被调用
        if legacy_failed or "reflection" in patches:
            detail = "历史记录:反思先生成失败,Router 未执行"
            executed = False
        else:
            detail = "历史记录:router 字段缺失(原因未记录)"
            executed = True
        patches["router"] = _failed_router(detail, executed)

    return (patches or None), ("、".join(patches.keys()) or None)


def _scan():
    rows = []
    if not os.path.isdir(RUNS_DIR):
        return rows
    for name in sorted(os.listdir(RUNS_DIR)):
        p = os.path.join(RUNS_DIR, name, "run.json")
        if not os.path.exists(p):
            continue
        try:
            with io.open(p, encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            rows.append((name, None, "解析失败: {}".format(e)))
            continue
        patches, why = _detect(d)
        if patches:
            rows.append((name, patches, why))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description="回填未跑成的反思/Router 字段")
    ap.add_argument("--apply", action="store_true", help="真正写盘(默认只报)")
    args = ap.parse_args(argv)

    rows = _scan()
    if not rows:
        print("没有需要回填的记录。")
        return 0

    print("需要回填 {} 条:".format(len(rows)))
    for name, patches, why in rows:
        if patches is None:
            print("  ! {} — {}".format(name, why))
            continue
        print("  - {} — 补: {} ({})".format(name, "、".join(patches.keys()), why))

    if not args.apply:
        print("\n(dry-run;加 --apply 才写盘)")
        return 0

    for name, patches, _why in rows:
        if patches is None:
            continue
        p = os.path.join(RUNS_DIR, name, "run.json")
        # 先留一份原件:`case01/runs/` 按约定不入库,没有版本控制兜底,
        # 写错了不可恢复(备份落在被 ignore 的 runs/ 内部,不会污染仓库)。
        bak = p + ".pre-backfill.bak"
        if not os.path.exists(bak):
            with io.open(p, encoding="utf-8") as f:
                original = f.read()
            with io.open(bak, "w", encoding="utf-8", newline="") as f:
                f.write(original)
        with io.open(p, encoding="utf-8") as f:
            d = json.load(f)
        for k, v in patches.items():
            d[k] = v
        # 保持原排版:indent=2、无末尾换行(与 record 落盘一致)
        with io.open(p, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(d, ensure_ascii=False, indent=_INDENT))
        print("  已写 {}(原件留存在 {})".format(name, os.path.basename(bak)))
    print("\n完成:{} 条。".format(len([r for r in rows if r[1] is not None])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
