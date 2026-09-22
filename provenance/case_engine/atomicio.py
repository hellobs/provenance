# -*- coding: utf-8 -*-
"""原子落盘(引擎通用):写同目录临时文件 → `os.replace`,写失败不留半截文件。

为什么必须有这一层(2026-09-22):
    记录原来直接 `open(path, "w")`,写到一半崩(进程被杀、磁盘满、序列化中途抛错)
    就在 run 目录里留下一个**半截 JSON**。后果比"没写"严重得多:文件存在、大小看着
    正常,读的人(只读契约、结果面板、复查同学)会当它是一条完整记录,解析失败时又只
    看到一句 JSONDecodeError,没人知道这次运行到底跑完没有。
    换成 "临时文件 → os.replace" 之后,目标路径上**要么是旧内容要么是新内容**,
    不存在中间态;失败时异常照原样抛出,不吞。

边界(如实说明):
    - 保证的是"同一文件系统内的换名原子性",不是 fsync 级持久性;要抗掉电还得
      `f.flush(); os.fsync(f.fileno())` 并 fsync 目录 —— 本仓是实验记录,不做这层;
    - 并发写同一个文件仍然只保证"换名"这一步原子,不保证"后写者一定是最后一次"
      (并发写同一条记录本来就不该发生)。

放在引擎层:`ExperimentEval.run(out_dir=...)` 与 case01 的 injector 都要用,
两边各自实现一份就会漂移(一份改了另一份照旧)。
"""
import json
import os
import tempfile
from typing import Any

__all__ = ["atomic_write_text", "write_json_atomic", "write_text_atomic"]


def _same_dir_tmp(target: str):
    """在目标同目录建一个唯一临时文件(同目录才能保证 replace 是换名而非跨盘拷贝)。"""
    directory = os.path.dirname(os.path.abspath(target)) or "."
    prefix = "." + (os.path.basename(target) or "tmp") + "."
    return tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=directory)


def atomic_write_text(target: str, text: str, encoding: str = "utf-8") -> str:
    """把 text 原子地写到 target,返回绝对路径。

    失败(编码/磁盘/替换)时:删掉临时文件、原样抛出异常 —— 目标文件保持旧内容,
    目录里不留 `.tmp` 残渣。
    """
    target = os.path.abspath(target)
    fd, tmp = _same_dir_tmp(target)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="\n") as f:
            f.write(text)
        os.replace(tmp, target)
    except BaseException:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        finally:
            pass
        raise
    return target


def write_text_atomic(target: str, text: str, encoding: str = "utf-8") -> str:
    """文本原子落盘(如 jsonl);内部就是 `atomic_write_text`。"""
    return atomic_write_text(target, text, encoding=encoding)


def write_json_atomic(target: str, data: Any, encoding: str = "utf-8",
                      indent: int = 2, ensure_ascii: bool = False,
                      **dump_kwargs) -> str:
    """把 data 序列化成 JSON 并原子落盘。序列化阶段就失败的话,一个字节都不落盘。"""
    text = json.dumps(data, ensure_ascii=ensure_ascii, indent=indent, **dump_kwargs)
    return atomic_write_text(target, text, encoding=encoding)
