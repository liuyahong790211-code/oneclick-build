"""ffmpeg 视频管线（产品B：老片修复一键包）.

流程：抽帧 → 分段处理（防爆显存，按实测显存占用动态定批量）→ 逐帧超分 →
libx264 合成 → 原音频混入 → 音画同步校验。

设计要点：
- 超分函数 ``upscale_fn`` 由外部注入（签名 ``PIL.Image -> PIL.Image``），
  生产环境传入 ``PhotoRestorer.upscale_image``；测试时可注入 mock 做 dry-run。
- 工作目录默认建在输出文件同级目录下（``tempfile.mkdtemp(dir=dst_parent)``），
  **禁用系统 /tmp**（PLAN.md 硬约束：/tmp 仅 512MB tmpfs）。
- 分段策略不写死"几帧一批"：先用真实一帧做显存校准
  （``torch.cuda.max_memory_allocated``），按 70% 空闲显存折算批量；
  无 CUDA 时逐帧顺序处理（CPU 跑视频本就极慢，见 spike 外推）。

ffmpeg 参数来源：ffmpeg 官方文档（muxer/demuxer/filter 选项说明），
关键参数已在本机 ffmpeg 8.1 实测通过（见 tests/test_video_pipeline.py dry-run）。
拿不准的已标「需验证」。
"""

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field


@dataclass
class VideoInfo:
    path: str
    width: int
    height: int
    fps: float
    duration_s: float
    has_audio: bool
    audio_codec: str | None = None
    nb_frames: int | None = None


class PipelineError(RuntimeError):
    pass


class VideoPipeline:
    def __init__(self, ffmpeg_path='ffmpeg', ffprobe_path='ffprobe',
                 upscale_fn=None, log=None):
        self.ffmpeg = ffmpeg_path
        self.ffprobe = ffprobe_path
        self.upscale_fn = upscale_fn
        self.log = log or (lambda msg: None)

    # -- 探测 ---------------------------------------------------------------
    def _run(self, cmd, timeout=600):
        self.log('$ ' + ' '.join(cmd))
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise PipelineError(f'命令失败（exit={p.returncode}）：{" ".join(cmd)}\n{p.stderr[-2000:]}')
        return p

    def probe(self, path) -> VideoInfo:
        """用 ffprobe 取视频流/音频流信息（来源：ffprobe 文档 -show_streams/-show_format/-of json）。"""
        p = self._run([self.ffprobe, '-v', 'error', '-show_streams', '-show_format',
                       '-of', 'json', path])
        data = json.loads(p.stdout)
        vstreams = [s for s in data.get('streams', []) if s.get('codec_type') == 'video']
        astreams = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
        if not vstreams:
            raise PipelineError(f'未找到视频流：{path}')
        vs = vstreams[0]
        fps = self._parse_fps(vs.get('r_frame_rate') or vs.get('avg_frame_rate'))
        duration = float(vs.get('duration') or data.get('format', {}).get('duration') or 0)
        nb = vs.get('nb_frames')
        return VideoInfo(
            path=path, width=int(vs['width']), height=int(vs['height']),
            fps=fps, duration_s=duration,
            has_audio=bool(astreams),
            audio_codec=astreams[0].get('codec_name') if astreams else None,
            nb_frames=int(nb) if nb and str(nb).isdigit() else None)

    @staticmethod
    def _parse_fps(rate):
        try:
            if '/' in str(rate):
                num, den = str(rate).split('/')
                return float(num) / float(den) if float(den) else 0
            return float(rate)
        except (ValueError, TypeError, ZeroDivisionError):
            return 0

    # -- 抽帧 ---------------------------------------------------------------
    def extract_frames(self, src, frames_dir, pattern='frame_%06d.png'):
        """抽帧为 PNG 序列。-fps_mode passthrough 保留原始帧率不丢帧不补帧
        （来源：ffmpeg 文档；旧版 -vsync 0 的替代写法，ffmpeg ≥5.1）。"""
        os.makedirs(frames_dir, exist_ok=True)
        self._run([self.ffmpeg, '-y', '-v', 'error', '-i', src,
                   '-fps_mode', 'passthrough',
                   os.path.join(frames_dir, pattern)])
        frames = sorted(f for f in os.listdir(frames_dir) if f.endswith('.png'))
        if not frames:
            raise PipelineError(f'抽帧得到 0 帧：{src}')
        self.log(f'抽帧完成：{len(frames)} 帧 → {frames_dir}')
        return [os.path.join(frames_dir, f) for f in frames]

    # -- 显存安全分段 --------------------------------------------------------
    def calibrate_frames_per_batch(self, sample_frame, upscale_fn=None):
        """用真实一帧实测峰值显存，折算安全批量；返回 (frames_per_batch, peak_mb)。

        不虚构"720p 约占 X GB"这类常量：直接跑一帧、读
        torch.cuda.max_memory_allocated()（来源：PyTorch 官方文档），
        按 70% 空闲显存折算。无 CUDA 时返回 1（逐帧）。
        """
        fn = upscale_fn or self.upscale_fn
        if fn is None:
            raise PipelineError('未提供 upscale_fn')
        try:
            import torch
            cuda = torch.cuda.is_available()
        except ImportError:
            cuda = False
        if not cuda:
            return 1, 0.0
        import torch  # noqa: F811
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.empty_cache()
        fn(sample_frame)  # 真实跑一帧
        peak = torch.cuda.max_memory_allocated() / (1024 ** 2)
        free = torch.cuda.mem_get_info()[0] / (1024 ** 2)
        torch.cuda.empty_cache()
        if peak <= 0:
            return 1, 0.0
        batch = max(1, int((free * 0.7) // peak))
        self.log(f'显存校准：单帧峰值 {peak:.0f}MB，空闲 {free:.0f}MB → 每批 {batch} 帧')
        return batch, peak

    # -- 主流程 --------------------------------------------------------------
    def process(self, src, dst, upscale_fn=None, progress_cb=None,
                keep_frames=False, crf=18, preset='fast', work_dir=None):
        """完整管线。返回 {'dst':..., 'frames': N, 'av_sync': {...}}。"""
        from PIL import Image
        fn = upscale_fn or self.upscale_fn
        if fn is None:
            raise PipelineError('未提供 upscale_fn（生产环境应传入 PhotoRestorer.upscale_image）')
        info = self.probe(src)
        self.log(f'输入：{info.width}x{info.height} @ {info.fps:.2f}fps，'
                 f'{info.duration_s:.2f}s，音频={"有" if info.has_audio else "无"}')

        parent = os.path.dirname(os.path.abspath(dst)) or '.'
        work = work_dir or tempfile.mkdtemp(prefix='photorestore_', dir=parent)
        frames_dir = os.path.join(work, 'frames')
        sr_dir = os.path.join(work, 'sr')
        os.makedirs(sr_dir, exist_ok=True)
        try:
            frames = self.extract_frames(src, frames_dir)
            first = Image.open(frames[0]).convert('RGB')
            batch, _ = self.calibrate_frames_per_batch(first, fn)
            total = len(frames)
            for i in range(0, total, batch):
                for j, fp in enumerate(frames[i:i + batch]):
                    idx = i + j
                    sr_img = fn(Image.open(fp).convert('RGB'))
                    sr_img.save(os.path.join(sr_dir, f'sr_{idx:06d}.png'))
                    if progress_cb:
                        progress_cb(idx + 1, total)
                # 每批后清缓存，防显存碎片累积（来源：PyTorch 文档 torch.cuda.empty_cache）
                try:
                    import torch
                    if torch.cuda.is_available():
                        torch.cuda.empty_cache()
                except ImportError:
                    pass
            self.log(f'超分完成：{total} 帧')
            self.mux(sr_dir, info, src, dst, crf=crf, preset=preset)
            av = self.check_av_sync(dst)
            self.log(f"音画同步校验：视频 {av['video_s']:.3f}s / 音频 {av['audio_s']:.3f}s，"
                     f"差 {av['diff_ms']:.0f}ms → {'通过' if av['ok'] else '告警'}")
            return {'dst': dst, 'frames': total, 'av_sync': av, 'info': info}
        finally:
            if not keep_frames:
                shutil.rmtree(work, ignore_errors=True)
            else:
                self.log(f'保留中间帧：{work}')

    def mux(self, sr_dir, info: VideoInfo, src_audio_path, dst, crf=18, preset='fast'):
        """合成：PNG 序列 → libx264 → 混入原音频。

        参数来源均为 ffmpeg 官方文档：
        - -framerate（image2 demuxer 输入帧率）；
        - -pix_fmt yuv420p（播放器兼容，H.264 主流要求）；
        - -crf 18 -preset fast（workspace 既有 lesson：母版 preset fast + crf 18）；
        - -movflags +faststart（mp4 边下边播）；
        - -c:a aac -b:a 192k（重编码音频，保证兼容；「需验证」：-c:a copy 在原音频
          非 AAC 时部分播放器异常，故统一转 AAC）；
        - -shortest（防止音频比视频长导致黑尾帧）。
        """
        sr_pattern = os.path.join(sr_dir, 'sr_%06d.png')
        cmd = [self.ffmpeg, '-y', '-v', 'error',
               '-framerate', f'{info.fps:.6f}', '-i', sr_pattern]
        if info.has_audio:
            cmd += ['-i', src_audio_path,
                    '-map', '0:v:0', '-map', '1:a:0',
                    '-c:a', 'aac', '-b:a', '192k']
        cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                '-crf', str(crf), '-preset', preset,
                '-movflags', '+faststart', '-shortest', dst]
        self._run(cmd, timeout=3600)
        self.log(f'合成完成：{dst}')

    # -- 音画同步校验 ----------------------------------------------------------
    def check_av_sync(self, path, tolerance_ms=120):
        """对比输出文件视频流/音频流时长。

        spike 实测：AAC 编码填充带来约 13ms 自然偏差，属正常。
        阈值默认 120ms：AAC 编码器延迟+填充通常 <50ms（来源：ffmpeg aac 编码器
        文档行为 + spike 实测 13ms；「需验证」：不同 ffmpeg 构建的填充量级）。
        无音频流时只校验视频流存在。
        """
        info = self.probe(path)
        p = self._run([self.ffprobe, '-v', 'error', '-show_entries',
                       'stream=codec_type,duration', '-of', 'json', path])
        streams = json.loads(p.stdout).get('streams', [])
        durs = {}
        for s in streams:
            try:
                durs[s['codec_type']] = float(s.get('duration') or 0)
            except (ValueError, TypeError):
                pass
        v = durs.get('video', info.duration_s)
        a = durs.get('audio')
        if a is None:
            return {'ok': True, 'video_s': v, 'audio_s': 0.0, 'diff_ms': 0.0,
                    'note': '无音频流，仅确认视频流正常'}
        diff_ms = abs(v - a) * 1000
        return {'ok': diff_ms <= tolerance_ms, 'video_s': v, 'audio_s': a,
                'diff_ms': diff_ms,
                'note': 'AAC 编码填充带来数十 ms 偏差属正常' if diff_ms <= tolerance_ms
                        else f'音画偏差 {diff_ms:.0f}ms 超过阈值 {tolerance_ms}ms，需人工检查'}
