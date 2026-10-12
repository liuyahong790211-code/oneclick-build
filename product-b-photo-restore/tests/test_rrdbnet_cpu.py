"""推理内核 CPU 实测（spike 用例复跑）.

用例（tech-spike-report.md §二）：160x120 → 640x480，roundtrip 校验。
权重：ModelScope damo/cv_rrdb_image-super-resolution 的 pytorch_model.pt，
用验证阶段已下载的本地缓存（dual-launch-verification.md §4），不重复下载。

运行：<venv-python> tests/test_rrdbnet_cpu.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image, ImageDraw

WEIGHT = ('/home/hatch/workspace/verify-assets/ms-weights/models/damo--cv_rrdb_image-super-resolution'
          '/snapshots/master/pytorch_model.pt')


def make_test_image():
    img = Image.new('RGB', (160, 120))
    px = img.load()
    for y in range(120):
        for x in range(160):
            px[x, y] = (x * 255 // 160, y * 255 // 120, (x + y) * 255 // 280)
    d = ImageDraw.Draw(img)
    d.rectangle([40, 30, 120, 90], outline=(255, 255, 255), width=2)
    d.ellipse([60, 45, 100, 75], fill=(200, 50, 50))
    return img


def main():
    assert os.path.isfile(WEIGHT), f'权重缓存缺失：{WEIGHT}'
    from core.rrdbnet_production import (PhotoRestorer, build_net, load_state_dict_robust,
                                         validate_state_dict)

    # 1) 权重结构指纹校验（16.7M / 23 block / conv_first 形状）
    sd = load_state_dict_robust(WEIGHT)
    n = validate_state_dict(sd)
    print(f'[1] 权重结构校验通过：{n} 参数（期望≈16.7M，23 个 RRDB block）')

    # 2) GitHub 官方版结构兼容性（params_ema 外层）——用内存伪造，不碰 GFPGAN
    fake_wrapped = {'params_ema': sd}
    import torch
    tmp = '/tmp/photorestore_test_wrapped.pt'  # 仅测试临时文件，非产品代码
    torch.save(fake_wrapped, tmp)
    sd2 = load_state_dict_robust(tmp)
    assert 'conv_first.weight' in sd2, 'params_ema 外层剥离失败'
    os.remove(tmp)
    print('[2] params_ema 外层兼容通过（GitHub 官方版结构）')

    # 3) 真实 CPU 超分 160x120 → 640x480
    import time
    restorer = PhotoRestorer(weight_path=WEIGHT, device='cpu')
    src = make_test_image()
    t0 = time.time()
    out = restorer.upscale_image(src)
    dt = time.time() - t0
    assert out.size == (640, 480), f'输出尺寸错误：{out.size}'
    print(f'[3] 超分通过：160x120 → {out.size}，耗时 {dt:.1f}s（spike 实测 7.4s，同量级）')

    # 4) roundtrip 校验：输出缩回 160x120，与输入比 MAE（spike 实测 6.6）
    import numpy as np
    back = np.asarray(out.resize((160, 120), Image.BILINEAR)).astype('float32')
    orig = np.asarray(src).astype('float32')
    mae = float(np.abs(back - orig).mean())
    print(f'[4] roundtrip MAE = {mae:.2f}（spike 实测 6.6；<20 即无结构性异常）')
    assert mae < 20, f'roundtrip MAE 异常：{mae}'

    # 5) 分块拼接正确性：同图分别走整图/分块路径，结果应基本一致
    #    （直接调 upscale_tensor 并强制分块，避免 1600x1200 在 CPU 上跑十几分钟）
    import torch
    from core.rrdbnet_production import upscale_tensor
    t = torch.from_numpy(np.asarray(
        make_test_image().resize((320, 240))).astype('float32') / 255.0
    ).permute(2, 0, 1).unsqueeze(0)
    whole = upscale_tensor(restorer.net, t, device='cpu', auto_tile_threshold=10**9)
    tiled = upscale_tensor(restorer.net, t, device='cpu',
                           tile_size=200, tile_overlap=16, auto_tile_threshold=100)
    assert tiled.shape == whole.shape == (1, 3, 960, 1280), f'分块输出形状错误：{tiled.shape}'
    seam_mae = float((tiled - whole).abs().mean() * 255)
    print(f'[5] 分块/整图一致性 MAE = {seam_mae:.2f}（<5 即拼接无结构性错位）')
    assert seam_mae < 5, f'分块拼接偏差过大：{seam_mae}'

    print('ALL PASS')


if __name__ == '__main__':
    main()
