# -*- coding: utf-8 -*-
"""`python -m case_engine` 入口 → case_engine.cli.main(),退出码透传。

带 `__main__` 守卫(2026-09-24 体检):无守卫时任何代码
`import case_engine.__main__` 都会当场执行 CLI 并把导入方杀掉(SystemExit);
`python -m` 方式下 `__name__` 恰为 "__main__",守卫对它零影响。
"""
from case_engine.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
