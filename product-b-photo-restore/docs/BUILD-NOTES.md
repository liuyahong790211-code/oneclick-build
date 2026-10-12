# 产品B（老片修复一键包）构建说明

> Phase 2 交付物 6/6。本文档收敛所有「需验证」项的最终结论（或明确移交对象），
> 并给出 Windows-only 实测清单移交 Phase 4。
> 约定：凡标注「需验证」均为**未在本机确认、必须在 Windows/Actions runner 上确认**的项，
> 不许当作已验证来写。

## 一、已验证结论（有来源，非虚构）

| # | 结论 | 来源 |
|---|---|---|
| 1 | 推理内核纯 torch 自研，**严禁 basicsr/realesrgan/facexlib** | tech-spike-report.md 坑点1：basicsr pip 安装卡死（6 分钟无响应） |
| 2 | 权重用 ModelScope `damo/cv_rrdb_image-super-resolution` 的 `pytorch_model.pt`（64MB），**顶层即 state_dict**，无 `params_ema` 外层 | dual-launch-verification.md §4（2026-10-11 torch 实测：16.70M 参数，23 个 RRDB block，conv_first (64,3,3,3)，与官方 x4plus 同构） |
| 3 | 权重**内置进安装包**（不走首次下载） | 同上 §4 注意事项：“建议内置，省去用户端下载失败风险” |
| 4 | `AI-ModelScope/Real-ESRGAN` 仓库是**老版 ESRGAN 权重**（x2/x4/x8），不是 Real-ESRGAN x4plus，**禁用** | 同上 §4 |
| 5 | 视频修复必须 GPU 是硬门槛；CPU 修 1 分钟 720p@30fps 理论需 7.7 天 | tech-spike-report.md §二外推（2 核 CPU 实测 400 秒/每百万输入像素） |
| 6 | 无 N 卡 → 禁用视频功能 + 弹窗，照片功能保留；建议 6GB+ 显存 | spike 报告“无 N 卡直接劝退视频功能”；RTX 3060 级约 0.3–0.5 秒/帧（社区实测数据，报告引用） |
| 7 | pip/下载硬编码国内镜像，禁用默认源 | spike 坑点2：download.pytorch.org 在本机卡死，阿里云镜像 1.5 秒装完 torch |
| 8 | 安装/运行时临时目录禁用系统 /tmp（本机 /tmp 仅 512MB tmpfs） | spike 坑点3；本产品：NSIS 大文件直解 `$INSTDIR`，视频管线工作目录建在输出同级 |
| 9 | GFPGAN 人脸模块**永久冻结**，任何情况不捆绑 | spike 报告 §二坑点4（权重许可冲突未解决） |
| 10 | 音画同步校验：AAC 编码填充带来约 13ms 自然偏差，属正常 | spike 实测（视频流 0.500s / 音频流 0.487s） |
| 11 | 合成参数 `-crf 18 -preset fast` | workspace 既有 lesson（AGENTS.md：母版 preset fast + crf 18） |
| 12 | 内核 CPU 实测：160x120→640x480 约 7.4s（2 核），roundtrip MAE 6.6 | spike 实测；本 Phase 已复跑（见 tests/test_rrdbnet_cpu.py 输出） |

## 二、仍需验证项（→ Phase 4 / Windows 真机）

| # | 事项 | 当前假设 | 验证方式 |
|---|---|---|---|
| V1 | 便携 Python 发行版 | python-build-standalone（含 pip，开箱即用） | Actions runner 上确认 `python.exe -m pip` 可用；备选 python.org embeddable zip + get-pip.py |
| V2 | torch CPU wheel 精确版本 | 构建时锁定，写入 `version.json` 的 `torch_version`（`core/torch_setup.py` 内 `DEFAULT_TORCH_VERSION` 为占位） | 构建机从清华镜像下载时确定 |
| V3 | GPU 版 torch 下载链路 | 清华 PyPI + pytorch 官方 cu128 extra-index | spike 曾遇到 download.pytorch.org 卡死；若用户侧不可达，改走 `mirrors.aliyun.com/pytorch-wheels` 等国内 CUDA wheel 镜像 |
| V4 | ffmpeg Windows 二进制 | gyan.dev essentials 构建（含 ffmpeg.exe + ffprobe.exe） | runner 上确认版本；`-fps_mode passthrough` 在本机 ffmpeg 8.1 已测通过，打包版本需复测 |
| V5 | 音频统一转 AAC（`-c:a aac`）而非 `-c:a copy` | 原音频非 AAC 时 copy 可能导致部分播放器异常 | runner 上用 AAC/MP3/AC3 片源各测一次 |
| V6 | 音画同步阈值 120ms | spike 实测 AAC 填充 13ms，阈值取 120ms 留余量 | 不同 ffmpeg 构建的填充量级实测后可收紧/放宽 |
| V7 | 显存分段无硬编码常量 | 用真实一帧运行时校准（`torch.cuda.max_memory_allocated`），不写死“720p 占 X GB” | N 卡真机上确认校准批量 >1 且不 OOM |
| V8 | 拖拽（tkinterdnd2） | 可选依赖，缺失降级为按钮添加 | Windows 打包后实测拖拽；不阻塞（降级路径已覆盖） |
| V9 | 安装包图标 icon.ico | Phase 3 品牌资源产出 | 有则用，无则 NSIS 用默认图标（不阻塞） |
| V10 | NSIS 脚本语法 | Linux 下 `makensis -nocd` 语法检查 | Phase 4 联调清单项 |

## 三、Windows-only 实测清单（移交 Phase 4）

在 GitHub Actions `windows-latest` runner（无 GPU，覆盖“无 N 卡”分支）+ 至少一台 N 卡真机上执行：

**A. 无 N 卡分支（runner 可测）**
- [ ] 安装全程无 UAC 弹窗，装到 `%LOCALAPPDATA%\Programs\PhotoRestore`
- [ ] 首次启动：torch CPU 版已就绪（pip --no-index 本地安装成功）
- [ ] 启动即弹“视频功能不可用”警告，文案与 `gpu_check.video_block_message` 一致
- [ ] 视频模式单选框禁用；照片修复跑通一张测试图（输出 4 倍尺寸）
- [ ] 卸载后 `$INSTDIR` 删除、HKCU 卸载注册表项清理、开始菜单/桌面快捷方式删除

**B. N 卡 6GB+ 真机（需 Yahong 或志愿者机器）**
- [ ] 显卡检测识别正确（名称/显存），视频模式可用
- [ ] 首次视频任务触发 GPU 版 torch 自动补装（清华镜像），补装后 `torch.cuda.is_available()=True`
- [ ] 1 分钟 720p 视频端到端：记录渲染时长（预期 RTX 3060 级约 9–15 分钟，spike 外推）
- [ ] 显存校准批量 >1 且全程无 OOM；输出 4 倍分辨率 + 音频保留 + 音画同步校验通过
- [ ] 大照片（如 4000x3000）自动分块路径不爆显存

**C. N 卡 <6GB / A 卡 / 核显真机**
- [ ] 视频按钮禁用 + “显存不足”弹窗；照片修复完整可用

**D. SmartScreen**
- [ ] 无代码签名 → “Windows 已保护你的电脑” → “更多信息” → “仍要运行”全流程截图（手册用；列入需 Yahong 事项候选）

## 四、已知限制（写进商品页/手册，不隐瞒）

1. 无代码签名证书（零支出约束），首次运行有 SmartScreen 警告，手册截图讲透。
2. 视频修复硬需 N 卡 6GB+；A 卡/核显/无独显用户只能用照片修复（启动即明确告知）。
3. CPU 跑照片：2 核约 7 秒/张（160x120→640x480）；大照片分块后更慢，属正常。
4. macOS 不支持（torch/CUDA 与 NSIS 均为 Windows-only 路线）。

## 五、Phase 4 集成 bug 修复记录（2026-10-11，Phase 4 执行）

### ✅ 已修复 Bug B-1：`staging/wheels/` 从未被打包（同类集成断点）

- **现象**：NSIS 主安装节用
  `pip install --no-index --find-links "$INSTDIR\wheels" torch pillow numpy`
  做离线安装，但 `File /r` 列表里漏掉了 `${STAGING}\wheels`
  （staging 约定注释里明明列了 `wheels/`，见脚本头部注释），
  `$INSTDIR\wheels` 根本不存在 → torch 安装必失败。
  属与产品 A mode.json 断点同类的"安装时引用了、但打包时没放进去"问题。
- **修复**：`installer/photorestore-oneclick.nsi` 主安装节补上
  `File /r "${STAGING}\wheels"`（`python/` 之后、`launcher/` 之前）＋头部注释记一笔。
- **待验证**：Phase 4 工作流在备齐 `staging/wheels/`（torch CPU wheel＋pillow＋numpy，
  从 `download.pytorch.org/whl/cpu` 与清华 PyPI 下载）后，`makensis` 编译通过；
  联调清单 `INTEGRATION-TEST.md` B-08 项覆盖"torch 离线安装成功"。
