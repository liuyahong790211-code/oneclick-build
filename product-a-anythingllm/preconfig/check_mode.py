#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开源改装铺 · 产品A：AnythingLLM 一键包 —— mode.json 二次校验

校验 $INSTDIR\\config\\mode.json 真实存在，且其 mode 字段与用户在安装页面的
选择一致（防 mode_selector.py 静默写坏）。

用法：
    check_mode.py <mode.json 路径> <期望的 mode: local|cloud>

退出码：0=一致，2=参数错误，3=内容不一致/文件损坏。

注：此前为 NSIS 脚本内联 `python -c` 一行式，因 Python 单引号
(`encoding='utf-8'`) 与 NSIS 单引号字符串定界符冲突导致 makensis
"unterminated string" 编译失败（2026-10-12 Actions 首构建实证），
故拆为独立脚本文件，彻底避开引号嵌套。
"""

import json
import sys


def main(argv):
    if len(argv) != 3:
        print("用法: check_mode.py <mode.json 路径> <期望的 mode>")
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"mode.json 读取/解析失败：{e}")
        return 3
    if data.get("mode") == argv[2]:
        return 0
    print(f"mode.json 内容校验失败：期望 {argv[2]!r}，实际 {data.get('mode')!r}")
    return 3


if __name__ == "__main__":
    sys.exit(main(sys.argv))
