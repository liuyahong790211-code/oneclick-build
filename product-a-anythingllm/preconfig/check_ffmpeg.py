#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FFmpeg 可用性检查与随包补齐（供 NSIS Section 3 调用）。

背景（来源：AnythingLLM 官方文档 installation-desktop/windows.mdx）：
  即使静默安装（/S），安装程序仍会从官方 CDN 下载 FFmpeg 等运行时依赖；
  失败只写 install 目录下的 ffmpeg_install.log，不弹窗。若国内 CDN 不可达，
  音视频转写功能会缺失。本脚本在检测到缺失时，用随包预置的 FFmpeg 静态
  构建补齐（--fallback-dir 指向安装包内 vendor/bin/ffmpeg）。

  FFmpeg 在 AnythingLLM Desktop 中的预期位置需在 Windows 真机上用
  ffmpeg_install.log 核实（见 docs/BUILD-NOTES.md），本脚本采用“检查常见
  位置 -> 找不到则记录需人工核实”的保守策略，不向未知路径乱写文件。

退出码：0=原本就有；2=用随包补齐成功；1=缺失且无法补齐（不阻塞主流程）。
"""
import argparse
import os
import shutil
import sys


def find_ffmpeg(install_dir):
    """在 AnythingLLM 安装目录及系统 PATH 中找 ffmpeg.exe。"""
    candidates = [
        os.path.join(install_dir, "resources", "ffmpeg.exe"),
        os.path.join(install_dir, "ffmpeg.exe"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return shutil.which("ffmpeg")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--install-dir", required=True, help="AnythingLLM 安装目录")
    p.add_argument("--fallback-dir", default=None, help="随包 FFmpeg 目录（含 ffmpeg.exe）")
    a = p.parse_args()

    found = find_ffmpeg(a.install_dir)
    if found:
        print(f"FFmpeg 可用：{found}", flush=True)
        return 0

    print("FFmpeg 缺失（CDN 下载可能失败），尝试随包补齐...", flush=True)
    if a.fallback_dir:
        src = os.path.join(a.fallback_dir, "ffmpeg.exe")
        if os.path.isfile(src):
            # 补齐目标：用户 PATH 下的本产品目录（不碰 AnythingLLM 安装目录，
            # 具体注入点待真机用 ffmpeg_install.log 核实）
            dst_dir = os.path.join(os.path.expandvars("%LOCALAPPDATA%"),
                                   "Programs", "AnythingLLM-OneClick", "bin")
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, "ffmpeg.exe")
            shutil.copy2(src, dst)
            print(f"已补齐随包 FFmpeg 到：{dst}", flush=True)
            print("注意：AnythingLLM 内置调用点需在 Windows 真机上核实（见 BUILD-NOTES）。",
                  flush=True)
            return 2

    print("ERROR: 无法补齐 FFmpeg。音视频转写功能将不可用，纯文本问答不受影响。",
          flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
