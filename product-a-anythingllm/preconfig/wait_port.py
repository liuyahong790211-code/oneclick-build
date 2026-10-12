#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等待 TCP 端口就绪的小工具（供 NSIS 调用）。退出码 0=就绪，1=超时。"""
import argparse
import socket
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--host", default="127.0.0.1")
    a = p.parse_args()
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((a.host, a.port), timeout=2)
            s.close()
            print("ok", flush=True)
            return 0
        except OSError:
            time.sleep(1)
    print("timeout", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
