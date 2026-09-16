# case01 injector（骨架）

把 case01 的节点剧本作为"mavis 上的一套事件参数约束"注入。设计依据见
`../docs/case01_over_mavis_设计说明.md`，执行步骤见 `../docs/case01_over_mavis_执行计划.md`。

## 现状

- 已完成：节点序列生成（`nodes.py`）、桥接与记录（`bridge.py`）、dry-run CLI（`run_injector.py`）。
- 未完成（阶段 2）：mavis 场景装配（两个角色的 mavis 配置与地图）、自定义条件注册、世界推进接入。
  `bridge._build_mavis()` 与 `bridge._apply_world()` 中标注了待办。

## 用法

```bash
# dry-run：不加载 mavis，只产出与真实运行同构的记录
python -m case01.injector.run_injector --timeline B --dry-run --out runs_injector/dry-B.json

# 真实运行（阶段 2 完成后）
python -m case01.injector.run_injector --timeline B --scenario-dir <mavis 场景目录>
```

## 与 mavis 的接口

本桥提供两个回调，直接传给 `Simulator`：

- `external_state(name, step, sim_time, game) -> dict`：步级临时状态，非空时进入提示词，不写记忆。
- `interaction_request(step, sim_time, game) -> [{"from","to","focus"}]`：请求一次交互，跳过冷却与概率门。

两者都是纯新增、默认关闭的能力；不传时 mavis 行为与之前完全一致。

## 记录

`run_record()` 输出 `schema_version=injector-0.1`，含每个节点的日期、步骤、释放事件、临时状态、
交互与重试次数。与 case01 `run.json` 的字段级对齐是阶段 3 的验收项，当前显式标记
`case01_run_compatible: false`。
