"""首次运行 torch 环境自检/补装（产品B：老片修复一键包）.

背景（见 installer/photorestore-oneclick.nsi 注释的打包决策）：
- 安装包只内置 **CPU 版 torch wheel**（约 190MB；来源：PyTorch 官方 wheel 体积）。
- GPU(CUDA) 版 torch + nvidia 依赖合计超 2GB，进安装包体积爆炸，
  故**不内置**；首次运行时若检测到 N 卡且当前 torch 无 CUDA，
  本模块从清华 PyPI 镜像拉取 GPU 版 torch（国内可达，spike 已验证镜像可用）。

pip 镜像硬编码（PLAN.md 硬约束：禁用默认源）：
- 清华：https://pypi.tuna.tsinghua.edu.cn/simple
  （来源：清华大学开源软件镜像站文档；spike 用阿里云镜像 1.5 秒装完 torch CPU，
   清华同为可信国内源，二选一此处固定用清华）
"""

import subprocess
import sys

TSINGHUA_PYPI = 'https://pypi.tuna.tsinghua.edu.cn/simple'

# GPU 版 torch 的版本销定：与打包时内置的 CPU 版大版本对齐，
# 避免 CUDA/cuDNN 依赖错位。「需验证」：以 NSIS 构建时实际下载的 CPU wheel 版本为准，
# 构建脚本负责把 TORCH_VERSION 写进安装目录的 version.json，本模块优先读它。
DEFAULT_TORCH_VERSION = '2.9.1'  # 占位，「需验证」：构建时以实际 wheel 版本覆盖


def torch_has_cuda():
    try:
        import torch
        return bool(torch.cuda.is_available())
    except ImportError:
        return False


def read_bundled_torch_version(install_dir):
    """读安装目录 version.json 里构建时记录的 torch 版本；没有则回退默认值。"""
    import json
    import os
    p = os.path.join(install_dir, 'version.json')
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f).get('torch_version') or DEFAULT_TORCH_VERSION
    except (OSError, ValueError):
        return DEFAULT_TORCH_VERSION


def ensure_gpu_torch(install_dir, python_exe, gpu_info=None, dry_run=False):
    """确保 GPU 可用时 torch 支持 CUDA。

    返回 (action, message)：action ∈ {'ok', 'installed', 'skipped', 'failed'}。
    dry_run=True 时只返回将要执行的 pip 命令，不执行（供测试/日志）。
    """
    from .gpu_check import check_nvidia, meets_video_requirement
    info = gpu_info or check_nvidia()
    if not meets_video_requirement(info):
        return 'skipped', '未检测到符合视频要求的 N 卡，无需安装 GPU 版 torch'
    if torch_has_cuda():
        return 'ok', '当前 torch 已支持 CUDA，无需补装'
    ver = read_bundled_torch_version(install_dir)
    # v1.0.4: ver 形如 2.9.1+cpu，直接 pip install torch==2.9.1+cpu 会被判定
    # "已满足要求"而跳过安装（2026-10-13 真机实证）。必须换成 +cu128 才能真装上。
    cu_ver = ver.split('+')[0] + '+cu128'
    cmd = [python_exe, '-m', 'pip', 'install', f'torch=={cu_ver}',
           '--index-url', 'https://download.pytorch.org/whl/cu128',
           '--extra-index-url', TSINGHUA_PYPI,
           '--no-cache-dir']
    # 注：CUDA wheel 本体仍走 pytorch 官方 CDN（国内直连 download.pytorch.org
    # 在 spike 曾卡死重试；「需验证」：若 Actions/用户侧不可达，改走
    # mirrors.aliyun.com/pytorch-wheels 等国内 CUDA wheel 镜像）。
    if dry_run:
        return 'install', ' '.join(cmd)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if r.returncode != 0:
        return 'failed', f'GPU 版 torch 安装失败：{r.stderr[-1000:]}'
    return ('installed' if torch_has_cuda() else 'failed',
            'GPU 版 torch 安装完成' if torch_has_cuda() else '安装后 torch 仍无 CUDA，请检查显卡驱动')
