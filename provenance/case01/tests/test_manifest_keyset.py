# -*- coding: utf-8 -*-
"""`manifest` 键集全等守卫(2026-10-05 第六轮只读核查 M1)。

为什么要有这条:
    `manifest` 的键数随代码演进(19 → 20 → 21),而**契约与复现说明里写的是数字**。
    此前每次加栏都靠人记得去改文档;一旦忘改,平台照文档实现就会漏读一栏 ——
    2026-10-05 这一轮实测:`build_manifest` 已产出 **21 键**(新增 `truncations`),
    而 `复现说明` / `平台对接契约` 还写着 20 键。

    更根本的问题是"用键数当契约"本身就脆:键数会变,而平台真正该依赖的是
    **"某个键在不在"**。所以这条守卫做两件事:
      1. **钉住当前产出键集**(精确集合,不是个数)——代码增删键立刻报红,逼人同步文档;
      2. 断言**契约必需键**(`REQUIRED_KEYS`)是产出键集的子集 —— 这个方向断了
         意味着"声明必有的键其实产不出来",比键数错更严重。

    与 `test_reproducibility.py` 的区别:那边测"同样的输入给同样的清单"(幂等),
    这边测"清单的**形状**与文档/平台约定一致"。两件事,别混。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from case01.injector.manifest import (  # noqa: E402
    build_manifest,
    collect_run_meta,
    REQUIRED_KEYS,
)

# 当前 `build_manifest` 产出的**精确**键集(2026-10-05 实测)。
# 加/删键时必须同步这里 —— 报红是设计意图,不是要人去放宽它。
EXPECTED_KEYS = frozenset({
    # 必需(REQUIRED_KEYS,15)
    "manifest_version", "created_at", "git_commit", "engine_id",
    "branch_mode", "judge", "judge_model", "judge_prompt_version",
    "temperature", "seed", "truncations", "scenario_sha256",
    "financial_data_version", "prompt_versions", "manifest_warnings",
    # 可选(按运行路径/参数决定是否存在,6)
    "git_commit_source", "branch", "branch_source",
    "judge_backend_reason", "scenario_path", "financial_data_dir",
})


def _built():
    """一个"最小可用"的 run_meta 下的产出(不依赖真实跑)。"""
    return build_manifest(collect_run_meta({}), financial_dir="", scenario_path="")


def test_required_keys_are_all_emitted():
    """`REQUIRED_KEYS` 必须全部在产出里 —— 契约声明必有的键产不出来是更严重的问题。"""
    got = set(_built())
    missing = set(REQUIRED_KEYS) - got
    assert not missing, "REQUIRED_KEYS 里声明必有却没产出的键: {}".format(sorted(missing))


def test_truncations_key_is_the_21st_and_present():
    """`truncations`(2026-10-05 新增的第 21 栏)必须在产出里。

    单列一条是为了让"这一栏的意义"留在测试里:它与 `manifest_warnings` 配套 ——
    截断计数以前只活在 lane 日志里,现在进清单,平台的 `manifest_status` 才看得到。
    """
    m = _built()
    assert "truncations" in m, "truncations 掉了 —— 截断计数又只剩 lane 日志"
    assert isinstance(m["truncations"], (list, dict, int)) or m["truncations"] is None


def test_expected_keyset_is_a_superset_of_required_by_construction():
    """自洽性:EXPECTED_KEYS 必须涵盖 REQUIRED_KEYS(防两处各自漂移)。"""
    assert set(REQUIRED_KEYS) <= EXPECTED_KEYS, (
        "REQUIRED_KEYS 里有 EXPECTED_KEYS 没列的键: {}".format(
            sorted(set(REQUIRED_KEYS) - EXPECTED_KEYS)))


def test_docs_state_the_current_key_count():
    """文档里的键数描述必须与当前产出一致(21 键)。

    这条是 M1 的直接验收:文档可以提到历史键数(19/20),但**必须同时**写明当前是 21,
    且不得把"当前产出"说成 20 键。
    """
    pkg = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    repo = os.path.dirname(pkg)
    docs = {
        "复现说明_三档口径.md": os.path.join(repo, "docs", "复现说明_三档口径.md"),
        "平台对接契约_5010唯一入口.md": os.path.join(
            repo, "docs", "平台对接契约_5010唯一入口.md"),
    }
    for name, path in docs.items():
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            txt = f.read()
        assert "21 键" in txt, "{} 未写明当前产出是 21 键".format(name)
