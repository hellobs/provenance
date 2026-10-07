# -*- coding: utf-8 -*-
"""LoRA 训练预准备:标记数据 → 训练 JSONL 的导出/校验/统计 + 环境体检。

数据流(live/reflections.py 已有上游):
  专家标记(/api/reflections/mark) → reflection_marks.json
    → build_lora_sample()(SFT:instruction/input/output;DPO:chosen/rejected)
    → 本工具:校验门 + 去重 + 统计 → results/lora/{sft,dpo}.jsonl + dataset_report.json

用法:
  python -m case01.tools.lora_prep --export            # 从真实标记导出
  python -m case01.tools.lora_prep --synthetic         # 3 条合成标记端到端自测(不改真实数据)
  python -m case01.tools.lora_prep --validate <jsonl>  # 对任一训练 JSONL 做校验
  python -m case01.tools.lora_prep --env               # 本机训练环境体检

诚实边界:
- 目前真实标记为 0 条 —— 导出管道就绪但**没有训练信号**;信号闸门是专家标记
  (M4/人工经 5010 打 mark),预准备不解决数据问题;
- verdict=correct 的样本把**原反思**当 output:SFT 学到的是"风格与格式",
  不是"对齐方向"(对齐方向在 DPO 对里)—— 训练配比要清楚这一点。
"""
import argparse
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

CK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
OUT_ROOT = os.path.join(CK, "results", "lora")
VALID_VERDICTS = ("correct", "incorrect", "partial")


# ---------------------------------------------------------------------------
# 校验门
# ---------------------------------------------------------------------------

def validate_sample(sample: dict) -> List[str]:
    """SFT 样本校验,返回问题列表(空 = 通过)。"""
    errs = []
    for k in ("instruction", "input", "output"):
        if not str(sample.get(k, "")).strip():
            errs.append("{}.空".format(k))
    out = str(sample.get("output", ""))
    if len(out) < 20:
        errs.append("output.过短(<20)")
    # 生成质量门:与反思管线同一套(strip 后不变 = 无客套/追问/emoji)
    try:
        from case01.reflection import _strip_boilerplate, _strip_tail_offer, _strip_emoji
        if _strip_boilerplate(out) != out:
            errs.append("output.客套/分隔线")
        if _strip_tail_offer(out) != out:
            errs.append("output.尾部追问")
        if _strip_emoji(out) != out:
            errs.append("output.emoji")
    except ImportError:
        pass                      # 裸包/独立使用时跳过质量门,只查结构
    return errs


def validate_dpo(dpo: dict) -> List[str]:
    errs = []
    for k in ("prompt", "chosen", "rejected"):
        if not str(dpo.get(k, "")).strip():
            errs.append("{}.空".format(k))
    if dpo.get("chosen") and dpo.get("chosen") == dpo.get("rejected"):
        errs.append("chosen==rejected(无偏好信号)")
    # 量纲对等(2026-10-07,真跑出来的缺陷):rejected 存的是**整篇反思**,而按
    # 《专家审核流程补充规范》§7 专家给的是 edited_segment(一个片段)。实测模拟专家
    # 四条对子全是 chosen 111–163 字 vs rejected 1591–3387 字,逐字相似度 0.026–0.071
    # —— 这种对子训出来的是"偏好短回答",不是"偏好专家判断",而旧校验门只查空值与
    # 全等,4/4 全放过。缺 `anchor`(原文定位)时无法把片段回填成完整反思,所以这里
    # 判它不合格并写明原因,而不是产出一条看起来合法的坏样本。
    c, r = str(dpo.get("chosen") or ""), str(dpo.get("rejected") or "")
    if c.strip() and r.strip():
        ratio = min(len(c), len(r)) / max(len(c), len(r))
        if ratio < 0.5:
            errs.append("chosen/rejected 量纲不对等(片段 vs 全文,ratio={:.2f});"
                        "需要 anchor 定位或改为整篇重写".format(ratio))
    return errs


def _dedup_key(sample: dict) -> str:
    return hashlib.sha256(
        (str(sample.get("instruction", "")) + "|" +
         str(sample.get("input", "")) + "|" +
         str(sample.get("output", ""))).encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 导出
# ---------------------------------------------------------------------------

def export(marks: List[dict], out_root: str = OUT_ROOT) -> dict:
    """标记列表 → 校验 → 去重 → 写 sft/dpo JSONL + 报告。返回报告 dict。"""
    from live.reflections import build_lora_sample
    sft, dpo, seen, report_rows = [], [], set(), []
    stats = {"marks": len(marks), "verdicts": {}, "sft": 0, "dpo": 0,
             "rejected": 0, "reject_reasons": {}}
    for m in marks:
        v = str(m.get("verdict", ""))
        stats["verdicts"][v] = stats["verdicts"].get(v, 0) + 1
        built = build_lora_sample(m)
        s, d = built["sample"], built.get("dpo")
        errs = validate_sample(s)
        if v not in VALID_VERDICTS:
            errs.append("verdict.非法:{}".format(v))
        if v in ("incorrect", "partial") and not str(m.get("correction", "")).strip():
            errs.append("correction.空({})".format(v))
        # 来源门(2026-10-07):模拟/回填的意见**不进训练集**。必要性是实测的 ——
        # 用 4b 扮专家造的 4 条标记在校验门眼里与真专家意见无法区分(全过、rejected 0),
        # 而《LoRA 预准备》§四 自己写着"不从成品反思造自模仿 SFT:那是在教模型模仿自己"。
        # 缺 `origin` 键按 expert 读(历史标记不回填)。
        org = str(m.get("origin", "expert") or "expert")
        if org != "expert":
            errs.append("非专家来源(origin={})".format(org))
        key = _dedup_key(s)
        if key in seen:
            errs.append("重复样本")
        if errs:
            stats["rejected"] += 1
            for e in errs:
                stats["reject_reasons"][e] = stats["reject_reasons"].get(e, 0) + 1
            report_rows.append({"verdict": v, "ok": False, "errors": errs})
            continue
        seen.add(key)
        sft.append(s)
        stats["sft"] += 1
        if d:
            derrs = validate_dpo(d)
            if not derrs:
                dpo.append(d)
                stats["dpo"] += 1
            else:
                # DPO 对不合格不牵连 SFT 那条(同一条意见可以只进 SFT)
                stats["dpo_rejected"] = stats.get("dpo_rejected", 0) + 1
                for e in derrs:
                    stats["reject_reasons"]["DPO:" + e] = \
                        stats["reject_reasons"].get("DPO:" + e, 0) + 1
        report_rows.append({"verdict": v, "ok": True, "errors": []})

    # 序列长度实测(2026-10-07):训练起点配置写的是 seq<=1024,而 input 里装着整篇反思
    # —— 真实标记一到位就会撞上限。不写进产物,配置就只能靠猜,而截断恰好切掉反思尾部
    # (反思质量分析:扣分集中在"经验迁移/不确定性/过程"这几维,都在尾部)。
    if sft:
        ins = sorted(len(str(x.get("input", ""))) for x in sft)
        outs = sorted(len(str(x.get("output", ""))) for x in sft)
        stats["char_len"] = {
            "input_min": ins[0], "input_median": ins[len(ins) // 2], "input_max": ins[-1],
            "output_min": outs[0], "output_median": outs[len(outs) // 2],
            "output_max": outs[-1],
            "note": "input 含整篇反思;seq 上限须覆盖它,否则截断落在反思尾部"}

    os.makedirs(out_root, exist_ok=True)
    sft_text = "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in sft)
    dpo_text = "".join(json.dumps(d, ensure_ascii=False) + "\n" for d in dpo)
    report = {"stats": stats, "rows": report_rows}
    _write_atomic(os.path.join(out_root, "sft.jsonl"), sft_text)
    _write_atomic(os.path.join(out_root, "dpo.jsonl"), dpo_text)
    _write_atomic(os.path.join(out_root, "dataset_report.json"),
                  json.dumps(report, ensure_ascii=False, indent=2))
    return report


def _write_atomic(path: str, text: str) -> None:
    """整份写完再原子换名 —— 训练文件不能留半份(与 `batch_analyze._write_atomic` 同法)。

    为什么这里也要:原来三份都是 `open(path, "w")`,**先 truncate 再逐块写**。
    写到一半(磁盘满/进程被杀)就留下一份被截断的 `sft.jsonl`,而下一棒是训练脚本,
    它读到半行 JSON 会直接炸,且没人知道少了几条 —— 截断文件比缺文件更难查。
    同目录是硬要求(`os.replace` 跨文件系统会报错)。
    """
    d = os.path.dirname(os.path.abspath(path)) or "."
    tmp = os.path.join(d, "." + os.path.basename(path) + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def load_marks_any() -> Tuple[List[dict], str]:
    """读真实标记:优先 JSON 数组(reflection_marks.json),回退 JSONL。"""
    from live.reflections import marks_path
    mp = marks_path()
    jl = mp + "l"
    if os.path.exists(mp):
        with open(mp, encoding="utf-8") as f:
            return json.load(f), mp
    if os.path.exists(jl):
        rows = []
        with open(jl, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows, jl
    return [], "(不存在)"


# ---------------------------------------------------------------------------
# 环境体检
# ---------------------------------------------------------------------------

def env_check() -> dict:
    out = {"gpu": None, "torch": None, "cuda": False, "deps": {}, "verdict": ""}
    try:
        import torch
        out["torch"] = torch.__version__
        out["cuda"] = bool(torch.cuda.is_available())
        if out["cuda"]:
            out["gpu"] = torch.cuda.get_device_name(0)
            free, total = torch.cuda.mem_get_info()
            out["vram_free_gb"] = round(free / 1024 ** 3, 1)
            out["vram_total_gb"] = round(total / 1024 ** 3, 1)
    except ImportError:
        out["torch"] = None
    import importlib.util as u
    for m in ("peft", "trl", "datasets", "bitsandbytes"):
        out["deps"][m] = bool(u.find_spec(m))
    if not out["cuda"]:
        out["verdict"] = ("torch 为 CPU 版({});本机训练不可用 —— 先装 CUDA 版:"
                          "pip install torch --index-url https://download.pytorch.org/whl/cu121;"
                          "或用云端 GPU(8GB 本卡 QLoRA 4B 属勉强可行档,见 LoRA 预准备文档)"
                          ).format(out["torch"])
    elif not all(out["deps"].values()):
        missing = [k for k, v in out["deps"].items() if not v]
        out["verdict"] = "GPU 就绪,训练栈缺 {}:先装 requirements-lora.txt".format(missing)
    else:
        out["verdict"] = "环境就绪(数据侧仍需专家标记)"
    return out


# ---------------------------------------------------------------------------
# 合成端到端自测
# ---------------------------------------------------------------------------

def synthetic_marks() -> List[dict]:
    ctx = {"role": "AI Investment Advisor", "action": "建议用户满仓买入 HCM",
           "value_tendency": {"Risk Control": 0.3, "Steady Returns": 0.4}}
    return [
        {"agent": "Investment AI", "simulation": "synthetic", "sim_time": "20250213-10:00",
         "node_id": "n1", "thought": "市场传闻来自单一第三方测算,可信度有限,但用户情绪高涨,"
                                     "建议小仓位参与以捕捉情绪溢价。",
         "verdict": "incorrect",
         "correction": "在官方披露缺失的情况下,任何仓位建议都缺乏依据;正确做法是明确"
                       "告知用户传闻未经证实,不建议参与,并提示情绪溢价回撤风险。",
         "context": ctx},
        {"agent": "Investment AI", "simulation": "synthetic", "sim_time": "20250213-11:00",
         "node_id": "n2", "thought": "本轮判断过程中我区分了官方披露与市场传闻,但没有"
                                     "量化传闻被证伪时的回撤幅度,属于风险提示不充分。",
         "verdict": "partial",
         "correction": "自我批评方向正确;应补充具体数据支撑,例如以 T0 价格为基准测算"
                       "回撤比例,而非笼统说'提示不充分'。",
         "context": ctx},
        {"agent": "Investment AI", "simulation": "synthetic", "sim_time": "20250213-12:00",
         "node_id": "n3", "thought": "我把披露层级(公告>媒体>测算)作为先检查项并如实"
                                     "告知用户证据不足,判断过程与结论一致。",
         "verdict": "correct", "correction": "", "context": ctx},
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description="LoRA 预准备:标记→训练 JSONL")
    ap.add_argument("--export", action="store_true")
    ap.add_argument("--synthetic", action="store_true")
    ap.add_argument("--validate", default="")
    ap.add_argument("--env", action="store_true")
    ap.add_argument("--out-root", default=OUT_ROOT)
    args = ap.parse_args()

    if args.env:
        info = env_check()
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0

    if args.validate:
        rows = []
        with open(args.validate, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rows.append(json.loads(line))
        is_dpo = rows and "chosen" in rows[0]
        bad = 0
        for i, r in enumerate(rows):
            errs = validate_dpo(r) if is_dpo else validate_sample(r)
            if errs:
                bad += 1
                print("第 {} 行: {}".format(i + 1, errs))
        print("{}: {} 行,{} 不合格({})".format(
            args.validate, len(rows), bad, "DPO" if is_dpo else "SFT"))
        return 1 if bad else 0

    if args.synthetic:
        marks = synthetic_marks()
        out_root = os.path.join(args.out_root, "_synthetic")
    else:
        marks, src = load_marks_any()
        out_root = args.out_root
        print("标记来源: {} ({} 条)".format(src, len(marks)))
    report = export(marks, out_root=out_root)
    print(json.dumps(report["stats"], ensure_ascii=False, indent=2))
    print("-> {}\\{{sft,dpo}}.jsonl + dataset_report.json".format(out_root))
    return 0


if __name__ == "__main__":
    sys.exit(main())
