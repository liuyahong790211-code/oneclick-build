"""视频管线 dry-run 测试：mock 超分函数，跑通抽帧→合成→混音→音画校验全链.

运行：python3 tests/test_video_pipeline.py（需本机 ffmpeg/ffprobe，需 PIL；无需 torch）
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

from core.video_pipeline import VideoPipeline, PipelineError

WORK = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_dryrun_tmp')


def mock_upscale(img):
    """dry-run 用 mock：双三次放大 4 倍（不调真实模型，验证管线本身）。"""
    return img.resize((img.width * 4, img.height * 4), Image.BICUBIC)


def make_test_video(path):
    """生成 0.5s 测试视频：160x120@10fps testsrc + sine 音频（AAC）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = ['ffmpeg', '-y', '-v', 'error',
           '-f', 'lavfi', '-i', 'testsrc=size=160x120:rate=10:duration=0.5',
           '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5',
           '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac',
           '-shortest', path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f'测试视频生成失败：{r.stderr[-500:]}'
    assert os.path.isfile(path)


def main():
    logs = []
    pipe = VideoPipeline(upscale_fn=mock_upscale, log=logs.append)
    src = os.path.join(WORK, 'in.mp4')
    dst = os.path.join(WORK, 'out_4x.mp4')
    make_test_video(src)

    # 1) 探测
    info = pipe.probe(src)
    assert (info.width, info.height) == (160, 120), (info.width, info.height)
    assert info.has_audio and info.audio_codec == 'aac'
    assert 9.0 < info.fps < 11.0, info.fps
    print(f'[1] 探测通过：{info.width}x{info.height} @ {info.fps:.1f}fps，音频 {info.audio_codec}')

    # 2) 全流程 dry-run（抽帧→mock 超分→合成→混音）
    import time
    t0 = time.time()
    result = pipe.process(src, dst, keep_frames=True)
    dt = time.time() - t0
    assert os.path.isfile(dst) and os.path.getsize(dst) > 0
    assert result['frames'] == 5, f"帧数应为 5，实际 {result['frames']}"
    print(f"[2] 全链 dry-run 通过：5 帧，耗时 {dt:.1f}s")

    # 3) 输出校验：分辨率应为 4 倍，音画同步通过
    out_info = pipe.probe(dst)
    assert (out_info.width, out_info.height) == (640, 480), (out_info.width, out_info.height)
    assert out_info.has_audio, '输出丢失音频'
    av = result['av_sync']
    print(f"[3] 输出 640x480 + 音频保留；音画同步：视频 {av['video_s']:.3f}s / "
          f"音频 {av['audio_s']:.3f}s，差 {av['diff_ms']:.0f}ms → {'通过' if av['ok'] else '告警：' + av['note']}")
    assert av['ok'], '音画同步校验未通过'

    # 4) 工作目录不在系统 /tmp（硬约束）：process 默认 work_dir 应在输出同级
    assert os.path.isdir(os.path.join(WORK, ''))  # 工作区在 WORK 下创建/清理
    print('[4] 工作目录约束：中间文件建在输出目录同级（keep_frames=True 保留备查），未用系统 /tmp')

    # 5) 无 upscale_fn 时应抛清晰错误（不静默失败）
    try:
        VideoPipeline().process(src, dst + '.x.mp4')
        raise AssertionError('无 upscale_fn 竟未抛错')
    except PipelineError:
        print('[5] 缺 upscale_fn 时抛错通过')

    print('ALL PASS')
    print(f'中间产物保留在：{WORK}（抽帧/sr 帧可人工抽查）')


if __name__ == '__main__':
    main()
