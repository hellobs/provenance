# -*- coding: utf-8 -*-
"""兼容壳:实现已抽到独立包 mavis-vizkit(packages/mavis-vizkit)。

本模块只做 re-export,不含任何逻辑;新代码请直接 `import mavis_vizkit`。
```
from mavis_vizkit import (
    EVENT_TYPES, Fanout, Visualizer, create, load_entry_point_plugins,
    names, register,
)
```
"""
from mavis_vizkit import (  # noqa: F401
    EVENT_TYPES,
    Fanout,
    Visualizer,
    create,
    load_entry_point_plugins,
    names,
    register,
)