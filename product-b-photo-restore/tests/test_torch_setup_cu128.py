"""回归测试：ensure_gpu_torch 必须请求 +cu128 版 torch（2026-10-13 真机事故）.

事故：version.json 里 torch_version 为 "2.9.1+cpu"，旧代码 pip install torch==2.9.1+cpu
被判定"已满足要求"直接跳过 → 安装后仍无 CUDA → 用户看到"请检查显卡驱动"误导性报错。
修复：pin 换成 +cu128。用 dry_run=True 不真装，只检查拼出的 pip 命令。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.torch_setup import ensure_gpu_torch
from core.gpu_check import GpuInfo


def test_pip_command_requests_cu128_not_cpu():
    d = tempfile.mkdtemp()
    with open(os.path.join(d, 'version.json'), 'w', encoding='utf-8') as f:
        json.dump({'torch_version': '2.9.1+cpu'}, f)
    gpu = GpuInfo(has_nvidia=True, name='RTX 4070 Ti', total_vram_gb=12.0,
                  cuda_available=False)
    action, cmd = ensure_gpu_torch(d, sys.executable, gpu_info=gpu, dry_run=True)
    assert action == 'install', cmd
    assert 'torch==2.9.1+cu128' in cmd, f'pip pin 错误: {cmd}'
    assert '+cpu' not in cmd, f'命令里不应出现 +cpu: {cmd}'
    # cu128 官方源做主源（wheel 本体），清华做 extra（小依赖）
    assert cmd.index('download.pytorch.org/whl/cu128') < cmd.index('tuna.tsinghua'), cmd


if __name__ == '__main__':
    test_pip_command_requests_cu128_not_cpu()
    print('torch_setup cu128 regression test: PASS')
