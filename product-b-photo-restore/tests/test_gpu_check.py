"""gpu_check 单元测试：检测函数全部可 mock，覆盖 N 卡缺失分支.

运行：python3 tests/test_gpu_check.py（无需 torch、无需显卡）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.gpu_check import (GpuInfo, check_nvidia, meets_video_requirement,
                            video_block_message, video_ready_message)


def test_no_nvidia_branch():
    """本机场景（Linux VM 无 N 卡）：双探针都返回空 → has_nvidia=False."""
    info = check_nvidia(cuda_probe=lambda: (False, None, None),
                        smi_probe=lambda: (False, None, None))
    assert info.has_nvidia is False
    assert info.method == 'none'
    assert meets_video_requirement(info) is False
    msg = video_block_message(info)
    assert '未检测到 NVIDIA' in msg and '照片修复' in msg and '6GB' in msg
    print('[1] 无 N 卡分支通过：禁用视频 + 弹窗文案正确')


def test_smi_only_branch():
    """有 N 卡硬件但 torch 无 CUDA（nvidia-smi 回退路径）."""
    info = check_nvidia(cuda_probe=lambda: (False, None, None),
                        smi_probe=lambda: (True, 'NVIDIA GeForce RTX 3060', 12288.0))
    assert info.has_nvidia is True
    assert info.method == 'nvidia-smi'
    assert info.cuda_available is False
    assert abs(info.total_vram_gb - 12.0) < 0.01
    assert meets_video_requirement(info) is True
    print('[2] nvidia-smi 回退路径通过：12GB → 满足视频要求')


def test_cuda_branch():
    """torch.cuda 可用（mock 0.5GB 核显级显存 → 不满足 6GB 门槛）."""
    info = check_nvidia(
        cuda_probe=lambda: (True, 'NVIDIA GeForce MX450', int(0.5 * 1024**3)),
        smi_probe=lambda: (False, None, None))
    assert info.has_nvidia is True and info.method == 'torch.cuda'
    assert meets_video_requirement(info) is False  # 0.5GB < 6GB
    msg = video_block_message(info)
    assert '显存' in msg and '照片修复' in msg
    print('[3] 低显存 N 卡分支通过：禁用视频 + 提示换卡')


def test_vram_unknown_is_conservative():
    """显存未知（None）时按不满足处理——宁可保守禁用."""
    info = GpuInfo(has_nvidia=True, name='Unknown NVIDIA', total_vram_gb=None,
                   cuda_available=False, method='nvidia-smi')
    assert meets_video_requirement(info) is False
    print('[4] 显存未知保守策略通过')


def test_ready_message():
    info = GpuInfo(has_nvidia=True, name='NVIDIA GeForce RTX 4060',
                   total_vram_gb=8.0, cuda_available=True, method='torch.cuda')
    assert meets_video_requirement(info) is True
    assert 'RTX 4060' in video_ready_message(info)
    print('[5] 达标 N 卡分支通过')


def test_real_environment():
    """真实环境探测（本 VM 应走 none 分支；有卡机器上仅打印不强断言）."""
    info = check_nvidia()
    print(f'[6] 真实环境：has_nvidia={info.has_nvidia}, method={info.method}, '
          f'name={info.name}, vram={info.total_vram_gb}, detail={info.detail}')


if __name__ == '__main__':
    test_no_nvidia_branch()
    test_smi_only_branch()
    test_cuda_branch()
    test_vram_unknown_is_conservative()
    test_ready_message()
    test_real_environment()
    print('ALL PASS')
