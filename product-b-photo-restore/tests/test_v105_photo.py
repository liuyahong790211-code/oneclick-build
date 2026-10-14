"""v1.0.5 回归测试：自适应倍数 / 分块进度回调 / 停止中断（纯逻辑单测）.

背景（2026-10-14 真机联调）：
- 24MP 照片无脑 x4 → 117 分块 → 25 分钟没跑完 → 改为自适应倍数（输出长边封顶 8192）。
- 照片模式 20 分钟界面零反馈 → 分块循环加 progress_cb。
- 停止按钮停不下来 → 分块循环内检查 stop_event，抛 UpscaleInterrupted。

torch 在本机未安装，用最小 stub 导入被测模块；只测不依赖真实推理的逻辑。
"""
import os
import sys
import types

# ---- 最小 torch stub（仅够 import + upscale_tensor 分块循环） ----
torch_mod = types.ModuleType('torch')


class _FakeTensor:
    def __init__(self, shape):
        self._shape = tuple(shape)

    @property
    def shape(self):
        return self._shape

    def to(self, device):
        return self

    def cpu(self):
        return self

    def clamp(self, a, b):
        return self

    @staticmethod
    def _slen(s, n):
        start, stop, step = s.indices(n)
        return max(0, (stop - start + step - 1) // step) if step > 0 else 0

    def __getitem__(self, key):
        if not isinstance(key, tuple):
            key = (key,)
        new_shape = []
        for i, k in enumerate(key):
            if isinstance(k, slice):
                new_shape.append(self._slen(k, self._shape[i]))
        new_shape.extend(self._shape[len(key):])
        return _FakeTensor(tuple(new_shape))

    def __setitem__(self, key, value):
        pass


def _zeros(*shape):
    if len(shape) == 1 and isinstance(shape[0], (tuple, list)):
        shape = tuple(shape[0])
    return _FakeTensor(tuple(shape))


def _no_grad():
    def deco(fn):
        return fn
    return deco


torch_mod.zeros = _zeros
torch_mod.no_grad = _no_grad
torch_mod.cuda = types.SimpleNamespace(is_available=lambda: False)

nn_mod = types.ModuleType('torch.nn')


class _FakeModule:
    def __init__(self, *a, **k):
        pass

    def __call__(self, x):
        return x


nn_mod.Module = _FakeModule
nn_mod.Conv2d = _FakeModule
nn_mod.LeakyReLU = _FakeModule
nn_mod.Sequential = _FakeModule
torch_mod.nn = nn_mod

f_mod = types.ModuleType('torch.nn.functional')
f_mod.interpolate = lambda x, **k: x
nn_mod.functional = f_mod

sys.modules['torch'] = torch_mod
sys.modules['torch.nn'] = nn_mod
sys.modules['torch.nn.functional'] = f_mod

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core import rrdbnet_production as rp  # noqa: E402


# ---- 1) 自适应倍数数学 ----
def test_adaptive_scale():
    assert rp.adaptive_scale(160, 120) == 4.0
    assert rp.adaptive_scale(2048, 1536) == 4.0          # 2048*4=8192 刚好封顶
    assert abs(rp.adaptive_scale(6000, 4000) - 8192 / 6000) < 1e-9
    assert abs(rp.adaptive_scale(3000, 2000) - 8192 / 3000) < 1e-9
    # 输出长边确实封顶 8192
    for w, h in [(6000, 4000), (4000, 6000), (8192, 100), (100, 9000)]:
        s = rp.adaptive_scale(w, h)
        assert max(w * s, h * s) <= 8192 + 1e-6, (w, h, s)


def test_scale_suffix():
    assert rp.scale_suffix(4.0) == '_4x'
    assert rp.scale_suffix(1.3653) == '_1x'
    assert rp.scale_suffix(2.73) == '_3x'


# ---- 2) 分块窗口纯逻辑 ----
def test_tile_windows_count_and_edges():
    wins = rp.tile_windows(2000, 2000)
    assert len(wins) == 25, len(wins)          # stride=496 → 每维 5 块
    assert wins[0] == (0, 512, 0, 512)
    assert wins[-1] == (1488, 2000, 1488, 2000)  # 边缘块右/下对齐
    # 全覆盖：每块宽高 ≤512，且整体覆盖 [0,2000)
    for y0, y1, x0, x1 in wins:
        assert 0 <= y0 < y1 <= 2000 and 0 <= x0 < x1 <= 2000
        assert y1 - y0 <= 512 and x1 - x0 <= 512


# ---- 3) 进度回调次数 ----
def _fake_net(tile):
    return tile


def test_progress_cb_called_per_tile():
    img_t = _FakeTensor((1, 3, 2000, 2000))
    calls = []
    rp.upscale_tensor(_fake_net, img_t, progress_cb=lambda d, t: calls.append((d, t)))
    assert calls == [(i, 25) for i in range(1, 26)], calls[:3]


def test_progress_cb_small_image_single():
    img_t = _FakeTensor((1, 3, 600, 600))  # ≤768 走整图路径
    calls = []
    rp.upscale_tensor(_fake_net, img_t, progress_cb=lambda d, t: calls.append((d, t)))
    assert calls == [(1, 1)], calls


# ---- 4) 停止中断 ----
class _TripAfter:
    def __init__(self, n):
        self.n = n
        self.calls = 0

    def is_set(self):
        self.calls += 1
        return self.calls > self.n


def test_stop_event_interrupts_mid_tiles():
    img_t = _FakeTensor((1, 3, 2000, 2000))
    calls = []
    try:
        rp.upscale_tensor(_fake_net, img_t,
                          progress_cb=lambda d, t: calls.append((d, t)),
                          stop_event=_TripAfter(3))
    except rp.UpscaleInterrupted:
        pass
    else:
        raise AssertionError('stop_event 置位后应抛 UpscaleInterrupted')
    assert len(calls) == 3, calls  # 前 3 块跑完，第 4 块前中断


def test_stop_event_preset_aborts_immediately():
    img_t = _FakeTensor((1, 3, 2000, 2000))
    try:
        rp.upscale_tensor(_fake_net, img_t,
                          stop_event=types.SimpleNamespace(is_set=lambda: True))
    except rp.UpscaleInterrupted:
        return
    raise AssertionError('预置位的 stop_event 应立即中断')


# ---- 5) upscale_file 自适应命名（桩掉 upscale_image，只验命名逻辑） ----
class _FakeImg:
    LANCZOS = 1

    def __init__(self, size=(0, 0)):
        self.size = size
        self.saved_to = None

    def convert(self, mode):
        return self

    def resize(self, size, filt):
        return _FakeImg(size)

    def save(self, p):
        self.saved_to = p

    @staticmethod
    def open(path):
        return _FakeImg((6000, 4000))


class _FakeImageMod:
    LANCZOS = 1
    open = staticmethod(_FakeImg.open)
    fromarray = staticmethod(lambda a, mode: _FakeImg())


def test_upscale_file_adaptive_naming():
    r = rp.PhotoRestorer.__new__(rp.PhotoRestorer)
    r._Image = _FakeImageMod

    def fake_upscale_image(img, progress_cb=None, stop_event=None):
        r.last_scale = rp.adaptive_scale(*img.size)
        return _FakeImg((8192, 5461))

    r.upscale_image = fake_upscale_image
    dst = r.upscale_file('/src/photo.JPG', out_dir='/out')
    assert dst == os.path.join('/out', 'photo_1x.png'), dst  # 6000px → scale≈1.37 → _1x


def test_upscale_file_small_image_keeps_4x():
    r = rp.PhotoRestorer.__new__(rp.PhotoRestorer)

    class _SmallMod(_FakeImageMod):
        open = staticmethod(lambda path: _FakeImg((800, 600)))

    r._Image = _SmallMod

    def fake_upscale_image(img, progress_cb=None, stop_event=None):
        r.last_scale = rp.adaptive_scale(*img.size)
        return _FakeImg((3200, 2400))

    r.upscale_image = fake_upscale_image
    dst = r.upscale_file('/src/small.png', out_dir='/out')
    assert dst == os.path.join('/out', 'small_4x.png'), dst


if __name__ == '__main__':
    test_adaptive_scale()
    test_scale_suffix()
    test_tile_windows_count_and_edges()
    test_progress_cb_called_per_tile()
    test_progress_cb_small_image_single()
    test_stop_event_interrupts_mid_tiles()
    test_stop_event_preset_aborts_immediately()
    test_upscale_file_adaptive_naming()
    test_upscale_file_small_image_keeps_4x()
    print('v1.0.5 photo pipeline tests: ALL PASS')
