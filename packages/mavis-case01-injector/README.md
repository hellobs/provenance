# mavis-case01-injector

case01 注入器 + world 事实层(阶段 3 抽包,设计稿:`provenance/case01/docs/任务_阶段3_抽包设计稿_20260918.md` 附录)。

- 自封闭:包内除 `_providers.py`(过渡 seam)外无任何 case01 引用
- 硬依赖:mavisframework、mavis-vizkit(不在 PyPI,由上层安装序保证)
- 过渡 seam:`_providers.py` 集中了对 case01 的 reflection/orchestrator/场景数据回退,
  收尾时删该文件并改为调用方注入(增量 3)
- provenance 侧经 `case01/injector/*` shim 兼容,调用方零改动
