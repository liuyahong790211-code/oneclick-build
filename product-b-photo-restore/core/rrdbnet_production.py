"""生产版 Real-ESRGAN x4 推理内核（产品B：老片修复一键包）.

移植自 spike 已验证的 ``~/workspace/business-plan/spike-assets/rrdbnet_min.py``，
改动点（生产化）：
1. 权重加载兼容两种真实结构：
   - ModelScope ``damo/cv_rrdb_image-super-resolution`` 的 ``pytorch_model.pt``：
     顶层即 state_dict，无外层（dual-launch-verification.md §4，2026-10-11 torch 实测）；
   - GitHub 官方 ``RealESRGAN_x4plus.pth``：顶层为 ``{'params_ema': {...}}``（spike 报告坑点2）。
   另防御性剥除 DataParallel 的 ``module.`` 前缀。
2. 加载后做结构校验（23 个 RRDB block / conv_first 形状 / 总参数量≈16.7M），
   权重损坏或错配时直接抛错，不静默跑出坏图。
3. 大图自动分块（tile）推理，防止高分辨率照片/视频帧爆显存/爆内存。

硬约束（PLAN.md）：
- 纯 torch 实现，**严禁**依赖 basicsr / realesrgan / facexlib（spike 已证实 basicsr pip 安装卡死）。
- GFPGAN 人脸模块永久冻结：本文件任何情况下不加载、不引用人脸权重。

网络结构来源：Real-ESRGAN 论文（Wang et al., arXiv:2107.10833）附录的 RRDBNet，
与官方仓库 ``realesrgan/archs/rrdbnet_arch.py`` 同构（num_feat=64, num_block=23）。
"""

import math
import os

import torch
from torch import nn
import torch.nn.functional as F

# ---------------------------------------------------------------------------
# 网络结构（与 spike 版 rrdbnet_min.py 逐行一致，未改动任一参数）
# ---------------------------------------------------------------------------

class ResidualDenseBlock(nn.Module):
    def __init__(self, num_feat=64, num_grow_ch=32):
        super().__init__()
        self.conv1 = nn.Conv2d(num_feat, num_grow_ch, 3, 1, 1)
        self.conv2 = nn.Conv2d(num_feat + num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv3 = nn.Conv2d(num_feat + 2 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv4 = nn.Conv2d(num_feat + 3 * num_grow_ch, num_grow_ch, 3, 1, 1)
        self.conv5 = nn.Conv2d(num_feat + 4 * num_grow_ch, num_feat, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        x1 = self.lrelu(self.conv1(x))
        x2 = self.lrelu(self.conv2(torch.cat((x, x1), 1)))
        x3 = self.lrelu(self.conv3(torch.cat((x, x1, x2), 1)))
        x4 = self.lrelu(self.conv4(torch.cat((x, x1, x2, x3), 1)))
        x5 = self.conv5(torch.cat((x, x1, x2, x3, x4), 1))
        return x5 * 0.2 + x


class RRDB(nn.Module):
    def __init__(self, num_feat, num_grow_ch=32):
        super().__init__()
        self.rdb1 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb2 = ResidualDenseBlock(num_feat, num_grow_ch)
        self.rdb3 = ResidualDenseBlock(num_feat, num_grow_ch)

    def forward(self, x):
        out = self.rdb1(x)
        out = self.rdb2(out)
        out = self.rdb3(out)
        return out * 0.2 + x


class RRDBNet(nn.Module):
    """x4 超分网络：conv_first → 23×RRDB → 上采样×2 → 上采样×2 → conv_last."""

    def __init__(self, num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32):
        super().__init__()
        self.conv_first = nn.Conv2d(num_in_ch, num_feat, 3, 1, 1)
        self.body = nn.Sequential(*[RRDB(num_feat, num_grow_ch) for _ in range(num_block)])
        self.conv_body = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up1 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_up2 = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_hr = nn.Conv2d(num_feat, num_feat, 3, 1, 1)
        self.conv_last = nn.Conv2d(num_feat, num_out_ch, 3, 1, 1)
        self.lrelu = nn.LeakyReLU(negative_slope=0.2, inplace=True)

    def forward(self, x):
        feat = self.conv_first(x)
        body_feat = self.conv_body(self.body(feat))
        feat = feat + body_feat
        feat = self.lrelu(self.conv_up1(F.interpolate(feat, scale_factor=2, mode='nearest')))
        feat = self.lrelu(self.conv_up2(F.interpolate(feat, scale_factor=2, mode='nearest')))
        return self.conv_last(self.lrelu(self.conv_hr(feat)))


SCALE = 4  # x4plus 固定 4 倍放大

# 期望的权重结构指纹（dual-launch-verification.md §4，2026-10-11 torch 实测）：
# pytorch_model.pt：总参数 16.70M，body 共 23 个 RRDB block（body.0–body.22），
# conv_first.weight 形状 (64, 3, 3, 3)。与官方 RealESRGAN_x4plus.pth 完全同构。
_EXPECTED_BLOCKS = 23
_EXPECTED_CONV_FIRST_SHAPE = (64, 3, 3, 3)
_EXPECTED_PARAMS_MIN = 16_000_000
_EXPECTED_PARAMS_MAX = 17_500_000


def _strip_prefix(state_dict, prefix):
    if any(k.startswith(prefix) for k in state_dict.keys()):
        return {k[len(prefix):] if k.startswith(prefix) else k: v
                for k, v in state_dict.items()}
    return state_dict


def load_state_dict_robust(path, map_location='cpu'):
    """读取权重文件，兼容两种真实存在的顶层结构，返回扁平 state_dict.

    - ModelScope damo 版：torch.load 直接得到扁平 state_dict（含 'conv_first.weight'）。
    - GitHub 官方版：torch.load 得到 {'params_ema': {...}}，需剥一层。
    - 另有训练 checkpoint 可能是 {'params': {...}}，一并兼容。
    """
    obj = torch.load(path, map_location=map_location, weights_only=True)
    if not isinstance(obj, dict):
        raise ValueError(f'权重文件顶层不是 dict：{type(obj)}（{path}）')
    # 情况1：顶层即扁平 state_dict（ModelScope damo 版）
    if 'conv_first.weight' in obj:
        sd = obj
    # 情况2/3：EMA 或普通训练 checkpoint 包了一层（GitHub 官方版）
    elif 'params_ema' in obj and isinstance(obj['params_ema'], dict):
        sd = obj['params_ema']
    elif 'params' in obj and isinstance(obj['params'], dict):
        sd = obj['params']
    else:
        raise ValueError(
            f'无法识别的权重结构，顶层 keys 前5个：{list(obj.keys())[:5]}（{path}）')
    sd = _strip_prefix(sd, 'module.')  # 防御 DataParallel 前缀
    return sd


def validate_state_dict(sd):
    """校验权重与 RRDBNet(num_block=23) 同构；不通过抛 ValueError."""
    problems = []
    w = sd.get('conv_first.weight')
    if w is None:
        problems.append('缺少 conv_first.weight')
    elif tuple(w.shape) != _EXPECTED_CONV_FIRST_SHAPE:
        problems.append(f'conv_first.weight 形状 {tuple(w.shape)} ≠ 期望 {_EXPECTED_CONV_FIRST_SHAPE}')
    body_idx = sorted({int(k.split('.')[1]) for k in sd if k.startswith('body.') and k.split('.')[1].isdigit()})
    if body_idx != list(range(_EXPECTED_BLOCKS)):
        problems.append(f'body RRDB block 索引 {body_idx[:3]}…{body_idx[-3:] if body_idx else []} ≠ 期望 0–22')
    total = sum(v.numel() for v in sd.values() if hasattr(v, 'numel'))
    if not (_EXPECTED_PARAMS_MIN <= total <= _EXPECTED_PARAMS_MAX):
        problems.append(f'总参数量 {total} 不在 [{_EXPECTED_PARAMS_MIN}, {_EXPECTED_PARAMS_MAX}] 内')
    if problems:
        raise ValueError('权重结构校验失败：' + '；'.join(problems))
    return total


def build_net(weight_path, device='cpu'):
    """构建网络并加载权重（含校验），返回 eval 模式的 net."""
    sd = load_state_dict_robust(weight_path, map_location=device)
    n_params = validate_state_dict(sd)
    net = RRDBNet(num_in_ch=3, num_out_ch=3, num_feat=64, num_block=23, num_grow_ch=32)
    net.load_state_dict(sd, strict=True)
    net.eval().to(device)
    return net, n_params


def default_weight_path():
    """权重默认搜索路径：环境变量 > 安装目录 models/ > 本文件同级 models/。"""
    env = os.environ.get('PHOTORESTORE_MODEL')
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(os.path.dirname(here), 'models', 'pytorch_model.pt'),  # <安装目录>/models/
        os.path.join(here, 'models', 'pytorch_model.pt'),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return candidates[0]


@torch.no_grad()
def _forward_tile(net, tile, device):
    return net(tile.to(device)).clamp(0, 1).cpu()


@torch.no_grad()
def upscale_tensor(net, img_t, device='cpu', tile_size=512, tile_overlap=16,
                   auto_tile_threshold=768):
    """超分单张 tensor（1,3,H,W，值域 0–1）→ (1,3,H*4,W*4).

    小图直接整图推理；任一边超过 auto_tile_threshold 时自动分块，
    每块裁掉 overlap 边缘后拼接（与 Real-ESRGAN 官方 tile 逻辑同思路，
    来源：realesrgan/utils/realesrganer.py 的 tile 处理，思想级引用）。
    """
    _, _, h, w = img_t.shape
    if max(h, w) <= auto_tile_threshold:
        return _forward_tile(net, img_t, device)
    stride = tile_size - tile_overlap
    out = torch.zeros(1, 3, h * SCALE, w * SCALE)
    for y in range(0, h, stride):
        for x in range(0, w, stride):
            y1, x1 = min(y + tile_size, h), min(x + tile_size, w)
            y0, x0 = max(y1 - tile_size, 0), max(x1 - tile_size, 0)
            tile = img_t[:, :, y0:y1, x0:x1]
            sr = _forward_tile(net, tile, device)
            # 输出坐标：输入块 [y0:y1) → 输出块 [y0*4:y1*4)，边缘块按实际尺寸对齐
            oy0, ox0 = y0 * SCALE, x0 * SCALE
            oy1, ox1 = y1 * SCALE, x1 * SCALE
            out[:, :, oy0:oy1, ox0:ox1] = sr[:, :, :oy1 - oy0, :ox1 - ox0]
    return out


class PhotoRestorer:
    """生产版照片修复器：PIL 进 → PIL 出（RGB，4 倍）。"""

    def __init__(self, weight_path=None, device=None, tile_size=512, tile_overlap=16):
        from PIL import Image  # 延迟导入：import 本模块不强制要求 PIL
        self._Image = Image
        self.weight_path = weight_path or default_weight_path()
        if not os.path.isfile(self.weight_path):
            raise FileNotFoundError(
                f'找不到权重文件：{self.weight_path}\n'
                '安装包应已内置 models/pytorch_model.pt（64MB，ModelScope damo 仓库版本）。')
        self.device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
        self.net, self.n_params = build_net(self.weight_path, device=self.device)
        self.tile_size = tile_size
        self.tile_overlap = tile_overlap

    def upscale_image(self, img):
        """输入 PIL.Image（任意模式）→ 返回 4 倍 PIL.Image（RGB）。"""
        import numpy as np
        img = img.convert('RGB')
        arr = np.asarray(img).astype('float32') / 255.0
        t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
        sr = upscale_tensor(self.net, t, device=self.device,
                            tile_size=self.tile_size, tile_overlap=self.tile_overlap)
        out = (sr.squeeze(0).permute(1, 2, 0).numpy() * 255.0).round().astype('uint8')
        return self._Image.fromarray(out, 'RGB')

    def upscale_file(self, src_path, dst_path):
        img = self._Image.open(src_path)
        self.upscale_image(img).save(dst_path)
        return dst_path
