# -*- coding: utf-8 -*-
"""`python -m case_engine` 入口 → case_engine.cli.main(),退出码透传。"""
from case_engine.cli import main

raise SystemExit(main())