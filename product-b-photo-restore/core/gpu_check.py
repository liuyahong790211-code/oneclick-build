"""启动时 NVIDIA 显卡检测（产品B：老片修复一键包）.

策略（双路径，任一命中即认定）：
1. ``torch.cuda.is_available()`` + 设备属性（主路径；来源：PyTorch 官方文档
   torch.cuda.is_available / torch.cuda.get_device_properties）。
2. ``nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits``
   回退路径（来源：NVIDIA nvidia-smi 官方文档的 query 语法；torch 未安装或
   CUDA 不可用但驱动存在时仍可识别显卡）。

产品规则（PLAN.md / spike 实测结论）：
- 视频修复必须 GPU 是硬门槛：CPU 修 1 分钟 720p@30fps 理论需 7.7 天（spike 外推）。
- 无 N 卡 → 禁用视频功能按钮并弹窗明确提示（建议 6GB+ 显存），照片修复完整保留。
- 本模块不 import torch（延迟到函数内），以便无 torch 环境也能做检测与单元测试。
"""

import shutil
import subprocess
from dataclasses import dataclass


@dataclass
class GpuInfo:
    has_nvidia: bool = False
    name: str | None = None            # 如 'NVIDIA GeForce RTX 3060'
    total_vram_gb: float | None = None
    cuda_available: bool = False       # torch 能否实际调用 CUDA
    method: str = 'none'               # 'torch.cuda' | 'nvidia-smi' | 'none'
    detail: str = ''


def _default_cuda_probe():
    """返回 (available, name, total_bytes)；torch 缺失则 (False, None, None)。"""
    try:
        import torch
    except ImportError:
        return False, None, None
    try:
        if not torch.cuda.is_available():
            return False, None, None
        prop = torch.cuda.get_device_properties(0)
        return True, prop.name, prop.total_memory
    except Exception:
        return False, None, None


def _default_smi_probe():
    """返回 (found, name, total_mib)；nvidia-smi 不存在则 (False, None, None)。"""
    if shutil.which('nvidia-smi') is None:
        return False, None, None
    try:
        out = subprocess.run(
            ['nvidia-smi', '--query-gpu=name,memory.total',
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=15, check=False)
    except (subprocess.SubprocessError, OSError):
        return False, None, None
    if out.returncode != 0 or not out.stdout.strip():
        return False, None, None
    # 取第一块卡；行格式如 "NVIDIA GeForce RTX 3060, 12288"
    first = out.stdout.strip().splitlines()[0]
    parts = [p.strip() for p in first.split(',')]
    name = parts[0] if parts else None
    total_mib = None
    if len(parts) > 1:
        try:
            total_mib = float(parts[1].split()[0])
        except (ValueError, IndexError):
            total_mib = None
    return True, name, total_mib


def check_nvidia(cuda_probe=None, smi_probe=None) -> GpuInfo:
    """检测 NVIDIA 显卡。cuda_probe / smi_probe 可注入 mock（单元测试用）。

    顺序：先 torch.cuda（能确认 CUDA 真可用），再 nvidia-smi（只确认硬件存在）。
    """
    cuda_probe = cuda_probe or _default_cuda_probe
    smi_probe = smi_probe or _default_smi_probe

    avail, name, total_bytes = cuda_probe()
    if avail:
        return GpuInfo(
            has_nvidia=True,
            name=name,
            total_vram_gb=(total_bytes / (1024 ** 3) if total_bytes else None),
            cuda_available=True,
            method='torch.cuda',
            detail='torch.cuda.is_available()=True')

    found, smi_name, total_mib = smi_probe()
    if found:
        return GpuInfo(
            has_nvidia=True,
            name=smi_name,
            total_vram_gb=(total_mib / 1024 if total_mib else None),
            cuda_available=False,
            method='nvidia-smi',
            detail='nvidia-smi 识别到 NVIDIA 显卡，但 torch CUDA 不可用（可能需安装 GPU 版 torch）')

    return GpuInfo(
        has_nvidia=False,
        detail='未检测到 NVIDIA 显卡（torch.cuda 与 nvidia-smi 均无结果）')


def meets_video_requirement(info: GpuInfo, min_vram_gb: float = 6.0) -> bool:
    """视频功能是否可用：有 N 卡且显存 >= min_vram_gb（默认 6GB，spike 结论）。

    显存未知（None）时按不满足处理——宁可保守禁用，不让用户遭遇爆显存崩溃。
    """
    return bool(info.has_nvidia and info.total_vram_gb
                and info.total_vram_gb >= min_vram_gb)


def video_block_message(info: GpuInfo, min_vram_gb: float = 6.0) -> str:
    """无 N 卡 / 显存不足时弹窗给用户的中文说明（GUI 直接引用）。

    文案经 Yahong 产品哲学对齐：说清门槛、说清替代方案（照片修复不受影响）。
    """
    if not info.has_nvidia:
        return (
            '未检测到 NVIDIA 独立显卡，【视频修复】功能已禁用。\n\n'
            '原因：视频修复是逐帧超分，CPU 处理 1 分钟 720p 视频理论需要数天，'
            '实际不可用（实测数据，非估计）。\n\n'
            '建议：使用搭载 NVIDIA 显卡（建议 6GB 以上显存，如 RTX 3060 级别）的电脑进行视频修复。\n'
            '【照片修复】功能不受影响，可正常使用。'
        )
    vram = f'{info.total_vram_gb:.1f}GB' if info.total_vram_gb else '未知'
    return (
        f'检测到 {info.name or "NVIDIA 显卡"}，但显存为 {vram}，'
        f'低于视频修复建议的 {min_vram_gb:.0f}GB。\n\n'
        '显存不足强行跑视频修复可能中途崩溃或极慢，【视频修复】功能已禁用。\n\n'
        '建议：换 6GB 以上显存的 N 卡再试。\n'
        '【照片修复】功能不受影响，可正常使用。'
    )


def video_ready_message(info: GpuInfo) -> str:
    vram = f'{info.total_vram_gb:.1f}GB' if info.total_vram_gb else '未知'
    return f'检测到 {info.name or "NVIDIA 显卡"}（显存 {vram}），视频修复功能可用。'
