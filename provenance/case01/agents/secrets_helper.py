# -*- coding: utf-8 -*-
"""临时:把 OpenRouter key 写入 .secrets.json(不入 git)。
用法: python write_secrets.py <key>
"""
import sys

from case01.agents.secrets import write_secrets

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m case01.agents.secrets_helper <API_KEY>")
        sys.exit(1)
    p = write_secrets(sys.argv[1])
    print("secrets written ->", p)
