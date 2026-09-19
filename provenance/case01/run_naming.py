# -*- coding: utf-8 -*-
"""记录命名:一次运行一个名字,名字里带**真实日期**,并且**日期在最前**。

格式(2026-09-19 定稿)
---------------------
    样本:  <YYMMDD>-demo-<case>-<engine>-<branch>
    实跑:  <YYMMDD>-live-<case>-<engine>-<branch>-<HHMM>

例:
    260905-demo-case01-old-B          旧引擎三线样本(2026-09-05 真机演示)
    260917-demo-case01-mavis-A        mavis 三线样本(2026-09-17 生成)
    260919-live-case01-mavis-B-1424   实跑一次(2026-09-19 14:24 起跑)

为什么这么定
------------
1. **日期在前**才能按时间排序。旧格式 `live-B-<日期>` 把同一分支排在一起,
   一眼看不出哪次最新。
2. **case 必须进名字**。现在有 case00 与 case01 两套并存的案例,名字不带 case 就分不清归属。
3. **engine 也进名字**(`old` / `mavis`)。旧引擎三线是留作对照的,只靠日期区分太隐晦。
4. **实跑带 HHMM、样本不带**:同一天跑多次也不能撞名;样本是一次性留档,不需要时刻。
5. **名字里的日期是真实运行时间**;记录里的 `start_date`/`end_date` 是**模拟剧情日期**
   (如 2026-08-27 → 09-15)。这两个别混。原始/成品记录里**没有墙上时钟字段**,
   所以真实运行时间只存在于名字里——这是名字必须带时间的根本原因。

为什么单独一个模块
------------------
驱动侧(`case01.vizkit.live_run`)与运维入口(`live_switch.py`)都要生成名字。
live_run 顶层 import 了 mavis_vizkit(较重),而 `live_switch.py --status` 只是看进程,
不该被拖着一起 import——所以放在无依赖的叶子模块里,**两边共用一份实现**
(各写一份会漂移,而名字写岔会让两个面对同一条记录显示两个名字,实测踩过)。
"""
import time


def live_run_id(branch: str, when: str = "", case: str = "case01",
                engine: str = "mavis") -> str:
    """实跑:`<YYMMDD>-live-<case>-<engine>-<branch>-<HHMM>`。

    when: `YYMMDD-HHMM`,便于测试注入固定时刻;留空用当前本地时间。
    """
    stamp = when or time.strftime("%y%m%d-%H%M")
    date, _, hhmm = stamp.partition("-")
    rid = "{}-live-{}-{}-{}".format(date, case, engine, branch)
    return "{}-{}".format(rid, hhmm) if hhmm else rid


def sample_run_id(case: str, engine: str, branch: str, date: str) -> str:
    """样本:`<YYMMDD>-demo-<case>-<engine>-<branch>`(不带时刻)。"""
    return "{}-demo-{}-{}-{}".format(date, case, engine, branch)
