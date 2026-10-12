#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开源改装铺 · 产品A：AnythingLLM 一键包 —— 双模式选择器

职责：
  1. 检测本机物理内存；
  2. 按规则推荐运行模式：
       ≥16GB  -> 本地模式（Qwen3-8B Q4_K_M 本地推理，bge-m3 本地向量化）
       <16GB  -> 云端 API 模式（尤其 8GB 档：7B 本体跑不动，bge-m3 本地向量化仍可）
     依据：dual-launch-verification.md 第 5 节（bge-m3 峰值 1.85GB，8GB 可跑；
     7B Q8 本体约 7.5GB + 系统 2-3GB + bge-m3 2GB 在 8GB 下挤不下）。
  3. 云端模式：引导用户选择国产大模型厂商（Qwen / GLM / DeepSeek 任选其一），
     收集 API key（getpass，不回显），做基础格式校验，写入 config/mode.json，
     供 setup_anythingllm.py 经 update-env 注入 AnythingLLM。
  4. 本地模式：仅写 mode.json（模型下载由安装流程执行）。

内存检测：
  Windows：ctypes GlobalMemoryStatusEx（标准 Win32 API，
    https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-globalmemorystatusex）
  Linux：/proc/meminfo（构建机自测用）；其他平台：best-effort 返回 None。

仅用标准库。退出码：0=成功写入配置；1=用户取消/失败。
"""

import argparse
import ctypes
import getpass
import json
import os
import sys

LOCAL_MIN_GB = 16  # 本地 7B 模式的内存门槛（PLAN 已定，不再讨论）

# 云端厂商（OpenAI 兼容接口，经 AnythingLLM generic-openai 提供商接入；
# env 键名来源：server/utils/AiProviders/genericOpenAi/index.js）。
# base_url 为各厂商官方文档的标准 OpenAI 兼容地址；model 名为常用档位，
# 构建前需复核当期可用模型名（标“需验证”，见 BUILD-NOTES）。
PROVIDERS = {
    "1": {
        "id": "qwen",
        "label": "通义千问 Qwen（阿里云）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",  # 需验证：以阿里云当期模型名为准
        "key_hint": "sk- 开头，在阿里云百炼控制台获取",
        "signup": "https://bailian.console.aliyun.com/",
    },
    "2": {
        "id": "glm",
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4/",
        "model": "glm-4-flash",  # 需验证：以智谱当期模型名为准
        "key_hint": "在智谱开放平台获取 API Key",
        "signup": "https://open.bigmodel.cn/",
    },
    "3": {
        "id": "deepseek",
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",  # 来源：server/utils/AiProviders/deepseek/index.js
        "model": "deepseek-chat",  # 需验证：以 DeepSeek 当期模型名为准
        "key_hint": "sk- 开头，在 DeepSeek 开放平台获取",
        "signup": "https://platform.deepseek.com/",
    },
}


def get_total_ram_gb():
    """返回物理内存 GB 数；检测不到返回 None。"""
    try:
        if os.name == "nt":
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            st = MEMORYSTATUSEX()
            st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st)):
                return st.ullTotalPhys / (1024 ** 3)
            return None
        # Linux / 其他：读 /proc/meminfo（构建机自测与开发调试用）
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb / (1024 ** 2)
    except Exception:
        pass
    return None


def recommend_mode(ram_gb):
    """内存 -> 推荐模式。检测不到内存时保守推荐云端模式（不断用户后路）。"""
    if ram_gb is None:
        return "cloud"
    return "local" if ram_gb >= LOCAL_MIN_GB else "cloud"


def ask_choice(prompt, options):
    """options: dict key->label；返回用户选择的 key（默认首个）。"""
    print(prompt)
    for k in sorted(options):
        print(f"  [{k}] {options[k]}")
    while True:
        ans = input(f"请选择 [{'/'.join(sorted(options))}]（默认 {sorted(options)[0]}）：").strip()
        if ans == "":
            return sorted(options)[0]
        if ans in options:
            return ans
        print("输入无效，请重新选择。")


def collect_cloud_provider():
    """引导用户选厂商 + 填 key；返回 provider dict（含用户 key）或 None（取消）。"""
    print("\n—— 云端 API 模式 ——")
    print("你的电脑内存较小，本地跑 7B 大模型会很吃力。")
    print("云端模式下：文档向量化仍在本地（bge-m3，约 2GB 内存），问答走国产大模型 API，\n"
          "按量计费，日常问答每月通常几块钱。\n")
    labels = {k: v["label"] for k, v in PROVIDERS.items()}
    choice = ask_choice("请选择一家国产大模型厂商（任选其一）：", labels)
    p = dict(PROVIDERS[choice])
    print(f"\n已选：{p['label']}")
    print(f"  申请地址：{p['signup']}")
    print(f"  Key 格式提示：{p['key_hint']}")
    print("  （输入时不回显，Key 只保存在本机安装目录的配置文件中，不上传任何地方）")
    for _ in range(3):
        key = getpass.getpass("请输入 API Key（直接回车取消）：").strip()
        if not key:
            print("已取消。")
            return None
        if len(key) < 8:
            print("Key 太短（<8 字符），请检查是否复制完整。")
            continue
        # 基础校验通过；连通性由 setup_anythingllm 写入后在界面首问时验证
        p["api_key"] = key
        return p
    print("多次输入无效，已取消。")
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="AnythingLLM 一键包双模式选择器")
    ap.add_argument("--install-dir", required=True, help="安装目录（config/mode.json 写到其下）")
    ap.add_argument("--mode", default="auto", choices=["auto", "local", "cloud"],
                    help="auto=按内存推荐并交互确认；local/cloud=直接指定")
    ap.add_argument("--non-interactive", action="store_true",
                    help="非交互：auto 时直接采用推荐值（NSIS 已弹过选择页时用）")
    ap.add_argument("--provider", default=None, choices=["qwen", "glm", "deepseek"],
                    help="云端模式预选厂商（非交互下配合 --api-key 使用）")
    ap.add_argument("--api-key", default=None, help="云端模式 API key（非交互）")
    a = ap.parse_args(argv)

    ram_gb = get_total_ram_gb()
    recommended = recommend_mode(ram_gb)
    print(f"检测到物理内存：{ram_gb:.1f} GB" if ram_gb else "内存检测失败")
    print(f"推荐模式：{'本地模式' if recommended == 'local' else '云端 API 模式'}")

    mode = a.mode
    if mode == "auto":
        if a.non_interactive:
            mode = recommended
            print(f"非交互：采用推荐模式 {mode}")
        else:
            c = ask_choice("请选择运行模式：", {"1": f"本地模式（推荐，内存≥{LOCAL_MIN_GB}GB）",
                                              "2": "云端 API 模式（内存较小或想省电）"})
            mode = "local" if c == "1" else "cloud"
            if mode != recommended:
                print(f"注意：你选择了与推荐（{recommended}）不同的模式。")

    mode_cfg = {"mode": mode,
                "ram_gb": round(ram_gb, 1) if ram_gb else None,
                "recommended": recommended}

    if mode == "cloud":
        provider = None
        if a.provider and a.api_key:
            p = next(v for v in PROVIDERS.values() if v["id"] == a.provider)
            provider = dict(p)
            provider["api_key"] = a.api_key
        elif not a.non_interactive:
            provider = collect_cloud_provider()
        if provider is None:
            print("云端模式需要 API Key，未提供则无法继续。", file=sys.stderr)
            return 1
        # key 不打印明文
        mode_cfg["provider"] = {k: v for k, v in provider.items() if k != "api_key"}
        mode_cfg["provider"]["api_key"] = provider["api_key"]
        print(f"云端厂商：{provider['label']}（Key 已保存到本机配置）")
    else:
        print("本地模式：将下载 Qwen3-8B（约 5GB）并在本机运行。")

    cfg_dir = os.path.join(os.path.abspath(a.install_dir), "config")
    os.makedirs(cfg_dir, exist_ok=True)
    cfg_path = os.path.join(cfg_dir, "mode.json")
    with open(cfg_path, "w", encoding="utf-8") as fh:
        json.dump(mode_cfg, fh, ensure_ascii=False, indent=2)
    print(f"模式配置已写入：{cfg_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
