# 随包资源清单（VENDOR-NOTES.md）

> 产品A：AnythingLLM 本地知识库问答一键包 · 随安装包分发的第三方二进制
> 核实日期：2026-10-11 · 核实人：Phase 1 执行子代理
> 原则：每个版本号/下载地址都有来源；拿不准的标"需验证"，不编。

---

## 1. Ollama

| 项 | 值 |
|---|---|
| 版本 | **v0.40.3**（2026-10-11 发布，GitHub Releases 最新） |
| 文件名 | `OllamaSetup.exe` |
| 大小 | 约 1505 MB（GitHub Release asset 元数据） |
| 下载地址 | `https://github.com/ollama/ollama/releases/download/v0.40.3/OllamaSetup.exe` |
| 备用地址 | `https://ollama.com/download/OllamaSetup.exe`（官网，302 跳转到最新版） |
| 安装器技术 | Inno Setup（源码 `app/ollama.iss` 开头声明 `; Inno Setup Installer for Ollama`，`OutputBaseFilename="OllamaSetup"`） |
| 静默参数 | `/SILENT`（显示进度条）或 `/VERYSILENT`（全静默），另可用 `/SUPPRESSMSGBOXES` `/NORESTART` `/LOG=` —— 来源：JRSoftware 官方文档 https://jrsoftware.org/ishelp/index.php?topic=setupcmdline |
| 用户级依据 | `app/ollama.iss` 中 `PrivilegesRequired=lowest`，默认安装到 `{localappdata}\Programs\Ollama`，**免 UAC**（2026-10-11 稀疏检出源码核实） |
| 来源 | https://github.com/ollama/ollama/blob/main/app/ollama.iss · https://api.github.com/repos/ollama/ollama/releases/tags/v0.40.3 |

**注意**：GitHub Release 在国内直连较慢，构建机（GitHub Actions `windows-latest` runner）下载无此问题；安装包在 Actions 上构建时拉取，不经过用户网络。

---

## 2. AnythingLLM Desktop

| 项 | 值 |
|---|---|
| 版本 | **v1.17.0**（2026-10-01 发布，当前 latest） |
| 文件名 | `AnythingLLMDesktop.exe` |
| 大小 | 约 410 MB（spike 实测记录） |
| 下载地址（版本锁定） | `https://cdn.anythingllm.com/legacy/1.17.0/AnythingLLMDesktop.exe`（2026-10-11 实测 HTTP 200） |
| 下载地址（浮动） | `https://cdn.anythingllm.com/latest/AnythingLLMDesktop.exe`（构建时以版本锁定地址为准，不用浮动地址） |
| 安装器技术 | electron-builder NSIS（第三方源码注释佐证；`/S` 为 NSIS 标准静默开关） |
| 静默参数 | `AnythingLLMDesktop.exe /S` —— **官方文档明确给出**（v1.16.1+ 支持）：静默安装，跳过约 2GB Meeting Assistant 模型下载 |
| 安装位置 | 固定 `%LOCALAPPDATA%\Programs\AnythingLLM`，仅当前用户（官方文档原话 "this is the only supported install location"），**免 UAC** |
| 卸载 | `"Uninstall AnythingLLM.exe" /S`（保留用户数据）；`/S /DELETE_DATA=yes` 会清空 `%APPDATA%\anythingllm-desktop` |
| 来源 | https://github.com/mintplex-labs/anythingllm-docs/blob/HEAD/pages/installation-desktop/windows.mdx · https://api.github.com/repos/Mintplex-Labs/anything-llm/releases/latest |

---

## 3. 官方 CDN 运行时依赖 —— 国内可达性验证结论与 fallback 决策

### 官方行为（来源：上表官方文档 "Silent / headless installation" 节）

> 即使静默模式（`/S`），安装程序仍会从官方 CDN（`cdn.anythingllm.com`）下载运行时依赖：
> **Ollama GPU 库、FFmpeg、Meeting Assistant 语音/分割模型**。
> 失败只写日志不弹窗，日志位于安装目录：
> `ollama_install.log` / `ffmpeg_install.log` / `meeting_assistant_install.log` /
> `meeting_assistant_model_install.log`。
> GPU 依赖缺失时 UI 会出警告，本地 LLM 降级为 CPU 推理。

GPU 支持文件也可手动补（文档 "Failed to install/extract GPU support from CDN" 节）：
`https://cdn.anythingllm.com/support/ollama/<version>/bins.7z`（基础）+
`cudav13.7z` / `cudav12.7z`（N 卡）/ `rocm_vulkan.7z`（A 卡），
解压到 `%APPDATA%\anythingllm-desktop\storage\engines\ollama\lib\ollama`，
另建 `.ollama-version` 点文件写入版本号。

### 可达性验证结论（2026-10-11）

| 探测 | 结果 |
|---|---|
| 本 VM curl `cdn.anythingllm.com` | HTTP 200（经代理出口，非直连） |
| 本 VM 公共 DNS（114.114.114.114 / 8.8.8.8）解析 | **REFUSED**（出口 DNS 被拦截，无法做权威判断） |
| 官方文档说法 | "Our CDN is globally accessible"（自述全球可达） |

**结论：国内可达性无法在本环境下定论**（DNS 被拦截，不能证实也不能证伪）。
按"可达性未知"做保守工程决策，不赌 CDN。

### Fallback 决策（已写入安装流程）

1. **Meeting Assistant 相关**：一键包固定用 `/S`（不带 `/DOWNLOAD_MODEL=yes`），
   约 2GB 转录模型本来就不下；会议助手功能在产品中不承诺，**直接跳过**，无 fallback 负担。
2. **FFmpeg**：安装包预置 FFmpeg 静态构建（`vendor/bin/ffmpeg/ffmpeg.exe`，
   来源待定——见"需验证"第 4 项）。`check_ffmpeg.py` 在安装后检查可用性，
   缺失则从随包目录补齐到产品自有目录；AnythingLLM 内置调用点的确切路径
   待 Windows 真机用 `ffmpeg_install.log` 核实（见 BUILD-NOTES）。
   影响面：仅音视频转写功能；**纯文本知识库问答不受影响**。
3. **Ollama GPU 库（内嵌引擎用）**：**不需要 fallback**。本地 7B 推理由我们
   单独静默安装的 Ollama（`%LOCALAPPDATA%\Programs\Ollama`，自带完整 GPU 库）
   在 `127.0.0.1:11434` 提供服务；AnythingLLM 桌面端会自动发现本地 Ollama
   （官方文档 troubleshooting 原话 "automatically detected"）。
   内嵌引擎的 GPU 库缺失只影响"桌面端内置 Ollama"那一路，本产品不用那一路。
4. **最坏情况**（CDN 全不可达）：用户得到——AnythingLLM 本体 + 外部 Ollama
   GPU 推理 + 预置 FFmpeg + 本地模型/云端 API，核心问答链路完整可用。

---

## 4. 大模型（不进安装包，首次安装时下载）

| 项 | 值 |
|---|---|
| 仓库 | `Qwen/Qwen3-8B-GGUF`（ModelScope，Qwen 官方组织） |
| 默认文件 | `Qwen3-8B-Q4_K_M.gguf` —— 5027783488 bytes（4.68 GiB） |
| 可选文件 | `Qwen3-8B-Q8_0.gguf` —— 8709518112 bytes（8.11 GiB，`--quant q8_0`） |
| 直链模式 | `https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={file}`（2026-10-11 curl 实测 206 + `Accept-Ranges: bytes`） |
| 许可 | Qwen3 权重为 Apache 2.0（仓库 LICENSE；构建前复核） |
| 核实方式 | ModelScope SDK `HubApi().get_model()` + `get_model_files()` 程序化核实（2026-10-11） |
| ollama 模型名 | `oneclick-qwen3-8b`（`ollama create`，**严禁 `ollama pull`**） |

---

## 5. 向量模型（bge-m3，随 AnythingLLM 原生嵌入引擎）

- AnythingLLM 原生嵌入引擎的模型下载：主源 HuggingFace，fallback 为
  `https://cdn.anythingllm.com/support/models/`（源码：
  `server/utils/EmbeddingEngines/native/index.js:37 #fallbackHost`）。
- 国内用户 HF 主源大概率失败，会走 CDN fallback（可达性同第 3 节，未知）。
- 对策（**需验证**，见 BUILD-NOTES 第 5 项）：在安装流程中预先从 ModelScope
 （`BAAI/bge-m3`，验证报告已实测可下）下载到原生引擎的本地缓存目录，
  使其跳过下载。缓存目录路径待 Windows 真机核实。

---

## 6. 内嵌 Python（运行下载/预配置脚本）

| 项 | 值 |
|---|---|
| 用途 | 运行 `modelscope_fetch.py` / `setup_anythingllm.py` / `mode_selector.py`（全部只用标准库，零 pip 依赖） |
| 方案 | python.org `python-3.12.x-embed-amd64.zip`（约 15MB），NSIS 中用 PowerShell `Expand-Archive` 解压 |
| 来源 | https://www.python.org/downloads/windows/（embeddable package；**需验证**：构建时锁定具体小版本号并改走国内镜像，如 npmmirror） |

---

## 7. vendor/bin 目录结构（构建时放入）

```
vendor/bin/
  OllamaSetup.exe            # v0.40.3，见第 1 节
  AnythingLLMDesktop.exe     # v1.17.0，见第 2 节
  python-embed-amd64.zip     # 见第 6 节
  ffmpeg/
    ffmpeg.exe               # 静态构建，见第 3 节（来源待验证）
```

> GitHub Release 单文件 2GB 上限：OllamaSetup.exe（1.5GB）+ AnythingLLMDesktop.exe
> （410MB）+ 其他 < 2GB，可进同一个 release asset；5GB 模型走 ModelScope，
> 不进安装包（符合 PLAN 硬约束第 6 条）。
