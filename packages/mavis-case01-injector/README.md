# mavis-case01-injector

> **状态**:现行
> **最后核对**:2026-09-27
> **说明**:case01 注入器包说明

归档说明(2026-10-08):本文点名的 `任务_阶段3_抽包设计稿_20260918.md` 已连同其余历史文档移出本仓,原件在需求方机器 `GTC/archive/provenance_docs_261008/case01/docs/`(逐文件哈希见该目录 README);正文里『见 X 第 N 节』这类句子按此查,仓内现行口径看 `docs/文档索引.md`。

case01 注入器 + world 事实层(阶段 3 抽包,设计稿:`provenance/case01/docs/任务_阶段3_抽包设计稿_20260918.md` 附录)。

- 自封闭:包内除 `_providers.py`(过渡 seam)外无任何 case01 引用
- 硬依赖:mavisframework、mavis-vizkit(不在 PyPI,由上层安装序保证)
- 过渡 seam:`_providers.py` 集中了对 case01 的 reflection/orchestrator/场景数据回退,
  收尾时删该文件并改为调用方注入(增量 3)
- provenance 侧经 `case01/injector/*` shim 兼容,调用方零改动
