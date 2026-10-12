# 产品B：老片修复一键包（Real-ESRGAN 单引擎）

> Phase 2 交付物总览。产品哲学：不做糊弄版，买了放心安心省心。

## 目录结构

```
product-b-photo-restore/
├── core/
│   ├── rrdbnet_production.py  # 推理内核：自研 RRDBNet 纯 torch，无 basicsr 依赖
│   ├── video_pipeline.py      # ffmpeg 管线：抽帧→分段→超分→合成→混音→音画校验
│   ├── gpu_check.py           # 启动时 N 卡检测（torch.cuda / nvidia-smi 双路径）
│   └── torch_setup.py         # 首次运行 GPU 版 torch 补装（清华镜像）
├── gui/
│   └── app.py                 # Tkinter GUI：拖拽/批量队列/双模式/进度条/日志
├── installer/
│   ├── photorestore-oneclick.nsi  # NSIS 安装脚本（用户级，免 UAC）
│   └── launcher/start.pyw         # 无控制台启动器
├── docs/
│   └── BUILD-NOTES.md         # 构建说明：已验证结论 + 需验证项 + Windows 实测清单
└── tests/
    ├── test_rrdbnet_cpu.py    # 内核 CPU 实测（spike 用例复跑）
    ├── test_video_pipeline.py # 视频管线 dry-run（mock 超分）
    ├── test_gpu_check.py       # 显卡检测单元测试（含无 N 卡分支）
    └── test_gui_import.py      # GUI 导入自检
```

## 快速自测（Linux）

```bash
cd ~/workspace/afdian-products/product-b-photo-restore
python3 tests/test_gpu_check.py      # 无需 torch/GPU
python3 tests/test_gui_import.py     # 无需显示环境
python3 tests/test_video_pipeline.py # 需 ffmpeg/ffprobe + PIL
~/workspace/verify-assets/venv/bin/python tests/test_rrdbnet_cpu.py  # 需 torch+PIL，约 2-4 分钟
```

## 关键产品规则

- 视频修复必须 N 卡 6GB+（硬门槛）；无 N 卡自动禁用视频功能，照片修复不受影响。
- GFPGAN 人脸模块永久冻结。
- pip/下载全部走国内镜像；临时目录禁用系统 /tmp。
