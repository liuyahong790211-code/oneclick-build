"""简易 GUI（产品B：老片修复一键包）.

GUI 框架选择论证（Tkinter vs PySide6，按 Yahong「小白买了省心」标准决策）：
- 选 **Tkinter**，弃 PySide6。理由：
  1. **零依赖**：Tkinter 随 Python 标准库发布，打包时不需要额外装 ~100MB 的
     PySide6/Qt wheel，NSIS 脚本不用处理 Qt DLL 与 plugins 目录，安装失败面最小。
  2. **需求匹配**：本界面只有文件列表/按钮/进度条/日志窗口，ttk 主题化后观感
     足够（clam 主题 + 大字体），不需要 QML 级别的精美度。
  3. **体积**：整包体积直接影响爱发电买家下载意愿；PySide6 会让安装包凭空
     大 100MB+，违背「能小则小」原则。
- 拖拽：优先用可选的 ``tkinterdnd2``（pip 包，**不硬依赖**）；缺失时自动降级为
  「添加文件/添加文件夹」按钮，保证零依赖底线。

约束：
- ``import gui.app`` 必须无副作用（不建 Tk 实例、不 import torch），以便无界面
  环境做导入自检；所有重型 import 延迟到工作线程内。
- 无 N 卡时：视频模式单选框禁用 + 启动弹窗明确提示（core/gpu_check.py 文案），
  照片修复完整保留（PLAN.md Phase 2 验收项）。
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

# 让 `python gui/app.py` 与 `python -m gui.app` 都能找到 core 包
_PKG_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_ROOT not in sys.path:
    sys.path.insert(0, _PKG_ROOT)

from core import gpu_check  # noqa: E402  (gpu_check 自身不依赖 torch，导入安全)

PHOTO_EXTS = ('.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tiff', '.tif')
VIDEO_EXTS = ('.mp4', '.mov', '.avi', '.mkv', '.m4v')


class PhotoRestoreApp(tk.Tk):
    def __init__(self, gpu_info=None):
        super().__init__()
        self.title('老片修复一键包')
        self.geometry('760x620')
        self.gpu_info = gpu_info if gpu_info is not None else gpu_check.check_nvidia()
        self.video_allowed = gpu_check.meets_video_requirement(self.gpu_info)
        self.files = []          # [(path, status)]
        self.mode = tk.StringVar(value='photo')
        self.out_dir = tk.StringVar(value=os.path.join(os.path.expanduser('~'), 'Pictures', 'PhotoRestore'))
        self.msg_q = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self._build_ui()
        self._apply_gpu_gate(first=True)
        self.after(120, self._pump_queue)

    # -- 界面 -------------------------------------------------------------
    def _build_ui(self):
        style = ttk.Style(self)
        for theme in ('clam', 'vista', 'xpnative', 'default'):
            if theme in style.theme_names():
                style.theme_use(theme)
                break
        top = ttk.Frame(self, padding=10)
        top.pack(fill='x')
        ttk.Label(top, text='模式：').pack(side='left')
        self.rb_photo = ttk.Radiobutton(top, text='照片修复', value='photo',
                                        variable=self.mode, command=self._on_mode_change)
        self.rb_video = ttk.Radiobutton(top, text='视频修复', value='video',
                                        variable=self.mode, command=self._on_mode_change)
        self.rb_photo.pack(side='left', padx=6)
        self.rb_video.pack(side='left', padx=6)
        self.gpu_label = ttk.Label(top, text='', foreground='#666')
        self.gpu_label.pack(side='left', padx=12)

        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill='both', expand=True)
        cols = ('file', 'status')
        self.tree = ttk.Treeview(mid, columns=cols, show='headings', height=10)
        self.tree.heading('file', text='文件')
        self.tree.heading('status', text='状态')
        self.tree.column('file', width=520)
        self.tree.column('status', width=120)
        self.tree.pack(side='left', fill='both', expand=True)
        sb = ttk.Scrollbar(mid, orient='vertical', command=self.tree.yview)
        sb.pack(side='right', fill='y')
        self.tree.configure(yscrollcommand=sb.set)
        self._register_dnd()

        bar = ttk.Frame(self, padding=10)
        bar.pack(fill='x')
        for text, cmd in [('添加文件', self._add_files), ('添加文件夹', self._add_folder),
                          ('删除选中', self._del_selected), ('上移', self._move_up),
                          ('下移', self._move_down), ('清空', self._clear)]:
            ttk.Button(bar, text=text, command=cmd).pack(side='left', padx=3)

        out = ttk.Frame(self, padding=(10, 0))
        out.pack(fill='x')
        ttk.Label(out, text='输出目录：').pack(side='left')
        ttk.Entry(out, textvariable=self.out_dir, width=52).pack(side='left', padx=4)
        ttk.Button(out, text='选择…', command=self._choose_out).pack(side='left')

        prog = ttk.Frame(self, padding=10)
        prog.pack(fill='x')
        self.prog = ttk.Progressbar(prog, mode='determinate', maximum=100)
        self.prog.pack(fill='x')
        self.prog_label = ttk.Label(prog, text='就绪')
        self.prog_label.pack(anchor='w', pady=(4, 0))

        run = ttk.Frame(self, padding=(10, 0))
        run.pack(fill='x')
        self.btn_start = ttk.Button(run, text='开始处理', command=self._start)
        self.btn_stop = ttk.Button(run, text='停止', command=self._stop, state='disabled')
        self.btn_start.pack(side='left', padx=3)
        self.btn_stop.pack(side='left', padx=3)

        ttk.Label(self, text='运行日志：').pack(anchor='w', padx=10)
        self.logw = ScrolledText(self, height=8, state='disabled', font=('Consolas', 9))
        self.logw.pack(fill='both', expand=False, padx=10, pady=(0, 10))

    def _register_dnd(self):
        """可选的拖拽支持：tkinterdnd2 存在则启用，否则静默降级。"""
        try:
            from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
        except ImportError:
            self._log('提示：未安装 tkinterdnd2，拖拽不可用，请用「添加文件」按钮（功能不受影响）。')
            return
        # 注：tkinterdnd2 要求根窗口由 TkinterDnD.Tk 创建；此处仅注册事件，
        # 若根窗口类型不支持则捕获异常降级。「需验证」：Windows 打包后实测拖拽。
        try:
            self.tree.drop_target_register(DND_FILES)
            self.tree.dnd_bind('<<Drop>>', self._on_drop)
            self._log('拖拽已启用：可直接把文件拖进列表。')
        except Exception as e:  # noqa: BLE001
            self._log(f'拖拽注册失败（已降级为按钮添加）：{e}')

    # -- 显卡门控 -----------------------------------------------------------
    def _apply_gpu_gate(self, first=False):
        if self.video_allowed:
            self.gpu_label.config(text=gpu_check.video_ready_message(self.gpu_info))
        else:
            self.rb_video.config(state='disabled')
            self.mode.set('photo')
            self.gpu_label.config(text='视频修复不可用（未检测到符合要求的 N 卡）')
            if first:
                # 启动弹窗：明确告知原因与替代方案（验收项）
                messagebox.showwarning('视频功能不可用',
                                       gpu_check.video_block_message(self.gpu_info))

    def _on_mode_change(self):
        mode = self.mode.get()
        exts = PHOTO_EXTS if mode == 'photo' else VIDEO_EXTS
        # 切换模式时剔除不符合的已加文件，避免误跑
        kept = [(p, s) for p, s in self.files if p.lower().endswith(exts)]
        if len(kept) != len(self.files):
            self.files = kept
            self._refresh_tree()
            self._log(f'已切换为{"照片" if mode == "photo" else "视频"}模式，不符合的文件已移出队列。')

    # -- 队列操作 -----------------------------------------------------------
    def _refresh_tree(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for p, s in self.files:
            self.tree.insert('', 'end', values=(p, s))

    def _pick_files(self, paths):
        exts = PHOTO_EXTS if self.mode.get() == 'photo' else VIDEO_EXTS
        added = 0
        for p in paths:
            p = p.strip('{}')  # tkinterdnd2 传来的路径可能带花括号
            if os.path.isfile(p) and p.lower().endswith(exts) \
                    and not any(p == q for q, _ in self.files):
                self.files.append((p, '等待'))
                added += 1
        if added:
            self._refresh_tree()
            self._log(f'已添加 {added} 个文件。')

    def _add_files(self):
        exts = PHOTO_EXTS if self.mode.get() == 'photo' else VIDEO_EXTS
        paths = filedialog.askopenfilenames(
            title='选择文件', filetypes=[('支持的文件', ' '.join('*' + e for e in exts)), ('全部', '*.*')])
        if paths:
            self._pick_files(paths)

    def _add_folder(self):
        d = filedialog.askdirectory(title='选择文件夹（自动扫描子目录）')
        if not d:
            return
        exts = PHOTO_EXTS if self.mode.get() == 'photo' else VIDEO_EXTS
        found = [os.path.join(r, f) for r, _, fs in os.walk(d)
                 for f in fs if f.lower().endswith(exts)]
        self._pick_files(sorted(found))

    def _on_drop(self, event):
        self._pick_files(self.tk.splitlist(event.data))

    def _selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        return self.tree.index(sel[0])

    def _del_selected(self):
        i = self._selected_index()
        if i is not None:
            del self.files[i]
            self._refresh_tree()

    def _move_up(self):
        i = self._selected_index()
        if i and i > 0:
            self.files[i - 1], self.files[i] = self.files[i], self.files[i - 1]
            self._refresh_tree()
            self.tree.selection_set(self.tree.get_children()[i - 1])

    def _move_down(self):
        i = self._selected_index()
        if i is not None and i < len(self.files) - 1:
            self.files[i + 1], self.files[i] = self.files[i], self.files[i + 1]
            self._refresh_tree()
            self.tree.selection_set(self.tree.get_children()[i + 1])

    def _clear(self):
        if self._running():
            messagebox.showinfo('提示', '处理中，请先停止再清空。')
            return
        self.files = []
        self._refresh_tree()

    def _choose_out(self):
        d = filedialog.askdirectory(title='选择输出目录')
        if d:
            self.out_dir.set(d)

    # -- 运行 ---------------------------------------------------------------
    def _running(self):
        return self.worker is not None and self.worker.is_alive()

    def _log(self, msg):
        self.msg_q.put(('log', msg))

    def _start(self):
        if self._running():
            return
        if not self.files:
            messagebox.showinfo('提示', '请先添加文件。')
            return
        if self.mode.get() == 'video' and not self.video_allowed:
            messagebox.showwarning('不可用', gpu_check.video_block_message(self.gpu_info))
            return
        out = self.out_dir.get().strip()
        if not out:
            messagebox.showinfo('提示', '请先选择输出目录。')
            return
        os.makedirs(out, exist_ok=True)
        self.stop_event.clear()
        self.prog['value'] = 0
        self.btn_start.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.worker = threading.Thread(target=self._worker,
                                        args=(list(self.files), self.mode.get(), out),
                                        daemon=True)
        self.worker.start()

    def _stop(self):
        self.stop_event.set()
        self._log('收到停止请求，当前文件完成后停下…')

    def _worker(self, files, mode, out_dir):
        q = self.msg_q
        try:
            if mode == 'photo':
                from core.rrdbnet_production import PhotoRestorer
                q.put(('log', '正在加载超分模型（64MB，约需数秒）…'))
                restorer = PhotoRestorer()
                q.put(('log', f'模型就绪（{restorer.n_params / 1e6:.1f}M 参数，设备 {restorer.device}）。'
                              'CPU 上单张照片约需数秒到数十秒，请耐心等待。'))
                total = len(files)
                for i, (p, _) in enumerate(files):
                    if self.stop_event.is_set():
                        break
                    q.put(('file_status', i, '处理中'))
                    q.put(('log', f'[{i + 1}/{total}] {os.path.basename(p)}'))
                    base = os.path.splitext(os.path.basename(p))[0]
                    dst = os.path.join(out_dir, base + '_4x.png')
                    restorer.upscale_file(p, dst)
                    q.put(('file_status', i, '完成'))
                    q.put(('progress', (i + 1) / total * 100, f'[{i + 1}/{total}] 完成'))
            else:
                from core.rrdbnet_production import PhotoRestorer
                from core.video_pipeline import VideoPipeline
                # 首次运行 GPU torch 补装（installer 决策：CUDA 版 torch 不进安装包，
                # 此处按显卡检测结果从清华镜像拉取；无 N 卡时 ensure 内直接跳过）
                from core.torch_setup import ensure_gpu_torch
                _install_dir = _PKG_ROOT  # 安装根目录（含 core/gui/models/version.json）
                action, tmsg = ensure_gpu_torch(_install_dir, sys.executable,
                                                gpu_info=self.gpu_info)
                q.put(('log', f'torch 环境检查：{tmsg}'))
                if action == 'failed':
                    raise RuntimeError(tmsg)
                q.put(('log', '正在加载超分模型…'))
                restorer = PhotoRestorer()
                pipe = VideoPipeline(upscale_fn=restorer.upscale_image,
                                     log=lambda m: q.put(('log', m)))
                total = len(files)
                for i, (p, _) in enumerate(files):
                    if self.stop_event.is_set():
                        break
                    q.put(('file_status', i, '处理中'))
                    base = os.path.splitext(os.path.basename(p))[0]
                    dst = os.path.join(out_dir, base + '_4x.mp4')
                    def cb(done, tot, _i=i, _t=total):  # noqa: B023
                        frac = (_i + done / max(tot, 1)) / _t
                        q.put(('progress', frac * 100, f'视频 {_i + 1}/{_t}：{done}/{tot} 帧'))
                    r = pipe.process(p, dst, progress_cb=cb)
                    q.put(('file_status', i, '完成'))
                    if not r['av_sync']['ok']:
                        q.put(('log', '警告：' + r['av_sync']['note']))
            q.put(('done', '全部完成。' if not self.stop_event.is_set() else '已停止。'))
        except Exception as e:  # noqa: BLE001
            q.put(('error', f'出错：{e}'))

    def _pump_queue(self):
        try:
            while True:
                msg = self.msg_q.get_nowait()
                kind = msg[0]
                if kind == 'log':
                    self.logw.config(state='normal')
                    self.logw.insert('end', msg[1] + '\n')
                    self.logw.see('end')
                    self.logw.config(state='disabled')
                elif kind == 'progress':
                    self.prog['value'] = msg[1]
                    self.prog_label.config(text=msg[2])
                elif kind == 'file_status':
                    idx, text = msg[1], msg[2]
                    if idx < len(self.files):
                        p, _ = self.files[idx]
                        self.files[idx] = (p, text)
                        self._refresh_tree()
                elif kind == 'done':
                    self.btn_start.config(state='normal')
                    self.btn_stop.config(state='disabled')
                    self.prog_label.config(text=msg[1])
                    self._log(msg[1])
                    messagebox.showinfo('完成', msg[1])
                elif kind == 'error':
                    self.btn_start.config(state='normal')
                    self.btn_stop.config(state='disabled')
                    self._log(msg[1])
                    messagebox.showerror('出错', msg[1] + '\n\n可把日志窗口内容发给客服排查。')
        except queue.Empty:
            pass
        self.after(120, self._pump_queue)


def main():
    info = gpu_check.check_nvidia()
    app = PhotoRestoreApp(gpu_info=info)
    app.mainloop()


if __name__ == '__main__':
    main()
