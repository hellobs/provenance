# -*- coding: utf-8 -*-
"""内置插件:导入即注册(console / report / town / live)。

`import mavis_vizkit.plugins` 即可让四个内置插件进入注册表;再由包的
`load_entry_point_plugins()` 可读取第三方入口点组 `mavis_vizkit.plugins`。
"""
from . import console as _console  # noqa: E402,F401
from . import report as _report    # noqa: E402,F401
from . import town as _town        # noqa: E402,F401
from . import live as _live        # noqa: E402,F401