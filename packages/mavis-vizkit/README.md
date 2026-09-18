# mavis-vizkit

面向任意 mavis 使用方的观察者 / 可视化插件集。它只消费"事件字典"的键名,
**运行时绝不 import mavisframework**,也不依赖任何业务仓库的目录布局。

任意 mavis 使用方都可以把它当普通包安装,然后注册自己的可视化后端或复用内置插件。
场景配置(角色、坐标、贴图别名)一律由调用方通过构造参数传入,包内不猜默认值。

## 事件协议(本包消费的键名契约)

引擎 / 注入层只负责产生以下事件,谁来画由调用方决定。前端 / Unity / 平台按这些键解析。

- `init`: `{"type", "agents": [name,...], "time"}`
- `time`: `{"type", "time", "step"?}`
- `agent`: `{"type", "name", "coord": [x,y], "path": [...], "action", "location", "currently", "time"}`
- `chat_line`: `{"type", "speaker", "text", "time"?}`
- `story`: `{"type", "id", "event_type", "content", "targets": [...], "time"}`
- `snapshot`: `{"type", "agents": {name: {...}}, "time"}`

这些键与 mavis 的 `mavisframework.runtime.protocol` 对齐(见该模块的 TypedDict)。

## 四种内置插件

- `console`: 控制台插件,收集事件并可打印摘要(CI/调试,零依赖)。
- `report`: 审查页插件,把整份 run 记录渲染成静态 HTML(需调用方注入 `page_fn` / `index_fn`)。
- `town`: 小镇风格插件,把事件翻译成 Phaser 前端可消费的消息(需 `alias` + `scenario_dir`)。
- `live`: 实时推送插件,起本地 FastAPI 服务边跑边推事件(需 `alias` + `scenario_dir` + `static_root` + `template_dir`)。

导入即注册: `import mavis_vizkit.plugins`(或 import 包)。第三方插件可放在入口点组
`mavis_vizkit.plugins` 下,调用 `mavis_vizkit.load_entry_point_plugins()` 生效。

## 记录布局

`normalize_record` / `events_from_record` 按 `nodes_key`(默认 `"nodes"`)找节点列表;
若节点挂在某个嵌套段下,由调用方传 `meta_key=` 指定该嵌套段的键名。包内不写死任何
调用方的布局键名。

## 常用用法

```python
from mavis_vizkit import Fanout, create

town = create("town", alias={"Alice": "Mr. Zhou"}, scenario_dir="/path/to/scenario",
              roles=["Alice", "Bob"])
live = create("live", port=5010, alias={"Alice": "Mr. Zhou"},
              scenario_dir="/path/to/scenario",
              static_root="/path/to/frontend/static",
              template_dir="/path/to/frontend/templates")
fanout = Fanout([town, live], on_error=lambda n, e: print(n, e))
```

构造 `town`/`live` 时若没传必需的 `alias` / `scenario_dir` / 前端资源根,会抛出清晰的
`ValueError`,而不是回退到任何本仓路径。`report` 若没传 `page_fn`/`index_fn` 同理。

## 回放

```bash
python -m mavis_vizkit.replay --record runs/demo/run.json --town out.jsonl \
    --alias "Alice=Mr. Zhou,Bob=AI Advisor" --scenario-dir ./scenario
```

## 开发与测试

```bash
pip install -e .[dev]
python -m pytest tests -q
```