# -*- coding: utf-8 -*-
"""反思文本体检 + 客套开场白清理(问题 2 / 问题 3)。

用户 2026-09-19 反馈两件事:
- **问题 2**:10 条反思**全部**以"当然可以。以下是我对这次完整咨询过程的系统性反思与
  深度剖析……"开头 —— LLM 客套话,给专家看的正式文档里不专业。
  (已改 `reflection.py` 的提示词 + `_strip_boilerplate` 后处理;这个工具清理**已有**记录。)
- **问题 3**:怀疑反思被 max_tokens 截断。这个工具检查每条反思的**结尾**像不像完整句子,
  并把疑似截断的列出来。

用法(在 provenance/provenance 下):

    python -m case01.tools.reflection_audit                    # 只体检(不写)
    python -m case01.tools.reflection_audit --runs-dir case01/runs
    python -m case01.tools.reflection_audit --clean --write    # 剥掉客套开场白并写盘
"""
import argparse
import glob
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from case01.reflection import _BOILERPLATE_OPENERS, _strip_boilerplate  # noqa: E402

# 完整结尾的样子:句末标点 / 落款 / 结语标记
_OK_TAIL = ("。", "！", "？", "”", "」", "）", ")", ".", "!", "?", "—", "*", "|")


def _ends_complete(text: str) -> bool:
    """结尾像不像写完了:先剥掉最后的落款/引用/结语短行,再看正文末句有没有句末标点。

    (2026-09-19 实测:B-1618 的结尾是落款"—— 人工智能投资助手,2026年9月15日",
    直接按"最后一个字符必须是句号"判会误报;而 B-1654 结尾是"是否需要?" —— 是完整的反问句。)
    """
    t = (text or "").rstrip()
    if not t:
        return True
    lines = [ln.strip() for ln in t.splitlines() if ln.strip()]
    while lines:
        last = lines[-1]
        is_sign = last.startswith(("——", "-", ">", "*", "（完）", "(完)"))
        is_short = len(last) <= 20 and not last.endswith(_OK_TAIL)
        if is_sign or is_short:
            lines.pop()
            continue
        break
    if not lines:
        return True
    return lines[-1].endswith(_OK_TAIL)


def audit_one(rec):
    refl = (rec.get("reflection") or {}).get("text") or ""
    head = refl.lstrip()[:40].replace("\n", " ")
    tail = refl.rstrip()[-40:].replace("\n", " ")
    has_opener = any(refl.lstrip()[:40].startswith(b) for b in _BOILERPLATE_OPENERS)
    stripped = _strip_boilerplate(refl)
    t = refl.rstrip()
    truncated = bool(t) and not _ends_complete(t)
    if t and "|" in t[-40:] and not t.endswith("|"):
        truncated = True
    return {"chars": len(refl), "head": head, "tail": tail,
            "has_opener": has_opener, "truncated": truncated,
            "clean_chars": len(stripped), "changed": stripped != refl}


def main(argv=None):
    ap = argparse.ArgumentParser(description="反思文本体检 / 清理客套开场白")
    ap.add_argument("--runs-dir", default="case01/runs")
    ap.add_argument("--clean", action="store_true", help="剥掉开头的客套话")
    ap.add_argument("--write", action="store_true", help="配合 --clean 真写盘")
    args = ap.parse_args(argv)

    paths = sorted(glob.glob(os.path.join(args.runs_dir, "*", "run.json")))
    if not paths:
        print("没找到记录:", args.runs_dir)
        return 1
    n_opener = n_trunc = n_changed = 0
    for p in paths:
        run_id = os.path.basename(os.path.dirname(p))
        with io.open(p, encoding="utf-8") as f:
            rec = json.load(f)
        info = audit_one(rec)
        flags = []
        if info["has_opener"]:
            flags.append("客套开头")
        if info["truncated"]:
            flags.append("疑似截断")
        n_opener += 1 if info["has_opener"] else 0
        n_trunc += 1 if info["truncated"] else 0
        print("{:<42} {:>5} 字  {}".format(run_id, info["chars"], " ".join(flags) or "-"))
        if info["has_opener"] or info["truncated"]:
            print("     开头: {}".format(info["head"]))
            print("     结尾: {}".format(info["tail"]))
        if args.clean and info["changed"]:
            rec.setdefault("reflection", {})
            rec["reflection"]["text"] = _strip_boilerplate(
                rec["reflection"].get("text") or "")
            rec["reflection"]["stripped_opener"] = True
            n_changed += 1
            if args.write:
                with io.open(p, "w", encoding="utf-8") as f:
                    json.dump(rec, f, ensure_ascii=False, indent=2)
    print("\n{} 条:客套开头 {} / 疑似截断 {}".format(len(paths), n_opener, n_trunc))
    if args.clean:
        print("已清理 {} 条;{}".format(n_changed, "已写盘" if args.write else "未写盘(--write 才写)"))
    return 1 if (n_trunc and not args.clean) else 0


if __name__ == "__main__":
    sys.exit(main())
