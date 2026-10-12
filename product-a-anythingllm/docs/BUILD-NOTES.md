# 构建说明（BUILD-NOTES.md）

> 产品A：AnythingLLM 本地知识库问答一键包 · Phase 1 构建记录
> 编写：2026-10-11 · 环境：Linux VM（无 Windows 真机）

---

## 一、"需验证"项最终结论

| # | 项 | 结论 | 来源 |
|---|---|---|---|
| 1 | Qwen3-7B 级 GGUF 真实可用仓库 | ✅ `Qwen/Qwen3-8B-GGUF`（Qwen 官方组织；Qwen3 系列是 8B 而非 7B，属"7B 级"）。默认 `Qwen3-8B-Q4_K_M.gguf`（5027783488 B），可选 `Qwen3-8B-Q8_0.gguf`（8709518112 B） | 2026-10-11 ModelScope SDK `HubApi().get_model()` + `get_model_files()` 程序化核实 |
| 2 | ModelScope 直链 URL 模式 | ✅ `https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={file}`；curl 实测 `206` + `Accept-Ranges: bytes`，断点续传可行 | 2026-10-11 curl 实测 |
| 3 | API key 无人值守获取 | ✅ 主路径 `POST /api/system/generate-api-key`（单用户无鉴权），返回 `{"apiKey": {"secret": ...}}`；备用 SQLite `api_keys` 表 INSERT | anything-llm 源码：`server/endpoints/system.js:1056`、`server/models/apiKeys.js`、`server/prisma/schema.prisma`、`server/utils/middleware/validatedRequest.js`（dual-launch-verification.md 第 1 节） |
| 4 | 工作区创建 API | ✅ `POST /api/v1/workspace/new`（`[validApiKey]`），字段 `name` / `openAiPrompt` / `chatMode` / `topN` 经源码 `#swagger` 注释核实 | `server/endpoints/api/workspace/index.js:28` |
| 5 | 云端模式提供商配置 API | ✅ `POST /api/v1/system/update-env`，键 `LLMProvider="generic-openai"` + `GENERIC_OPEN_AI_BASE_PATH` / `_API_KEY` / `_MODEL_PREF` 经源码核实；DeepSeek baseURL `https://api.deepseek.com/v1` 经源码核实 | `server/endpoints/api/system/index.js:107`、`server/utils/AiProviders/genericOpenAi/index.js`、`server/utils/helpers/updateENV.js:1154`、`server/utils/AiProviders/deepseek/index.js:27` |
| 6 | 遥测关闭 | ✅ `DISABLE_TELEMETRY=true` 环境变量（源码注释明示）；桌面端 server 为 Electron 子进程，写 `HKCU\Environment` 后新启动继承。⚠️ 继承链待真机验证 | `server/utils/telemetry/index.js` 头部注释 |
| 7 | Ollama `/SILENT` 用户级 | ✅ Inno Setup 官方开关；`app/ollama.iss` 的 `PrivilegesRequired=lowest` → 装到 `{localappdata}\Programs\Ollama`，免 UAC | https://jrsoftware.org/ishelp/index.php?topic=setupcmdline · https://github.com/ollama/ollama/blob/main/app/ollama.iss |
| 8 | AnythingLLM `/S` | ✅ 官方文档明确：`/S` 静默安装，跳过约 2GB Meeting Assistant 模型，固定装到 `%LOCALAPPDATA%\Programs\AnythingLLM`（仅当前用户） | https://github.com/mintplex-labs/anythingllm-docs/blob/HEAD/pages/installation-desktop/windows.mdx |
| 9 | CDN 国内可达性 | ⚠️ **无法定论**：本机出口 DNS 被拦截（公共 DNS REFUSED），curl 经代理返回 200 不能代表用户直连。官方自述 "globally accessible"。按"未知"做保守 fallback（见 VENDOR-NOTES 第 3 节），不赌 CDN | 2026-10-11 本机探测 + 官方文档 |
| 10 | FFmpeg 预置来源 | ⏳ 未定：待 Phase 4 确定国内可达的静态构建源（候选：随 `ffmpeg` 官方构建经国内镜像，或 gyan.dev 经 Actions 下载）。`check_ffmpeg.py` 的补齐目标路径亦待真机用 `ffmpeg_install.log` 核实 | — |
| 11 | bge-m3 预下载缓存目录 | ⏳ 未定：AnythingLLM 原生嵌入引擎的本地缓存路径待 Windows 真机核实（源码只确认了 fallback host `https://cdn.anythingllm.com/support/models/`，`server/utils/EmbeddingEngines/native/index.js:37`） | — |
| 12 | 内嵌 Python 下载源 | ⏳ 未定：构建时锁定 `python-3.12.x-embed-amd64.zip` 具体小版本号，改走国内镜像（npmmirror 等） | https://www.python.org/downloads/windows/ |
| 13 | 云端厂商 model 名 | ⏳ `qwen-plus` / `glm-4-flash` / `deepseek-chat` 为常用档位，**构建前按厂商当期文档复核**；base_url 为各厂商官方 OpenAI 兼容地址（DeepSeek 的经源码核实） | 各厂商开放平台文档 |
| 14 | Inno 卸载程序文件名 | ⏳ NSIS 卸载节中 `unins000.exe` 为 Inno 默认名，待真机确认 Ollama 实际文件名（文件存在才执行，已做存在性检查，不会报错） | — |

---

## 二、本 Phase 实测记录

| 测试 | 结果 |
|---|---|
| `py_compile`（5 个 Python 文件） | ✅ 全部通过 |
| `clean_think_tags` 单元测试 | ✅ 7/7（含：空对删除、非空对完整保留、孤立标签删除、system prompt 自清洗）。**自测中抓到 1 个真 bug**（第二条正则误删非空对标签，已修复为占位符保护法） |
| `recommend_mode` 边界测试 | ✅ 32/16→local；15.9/8/None→cloud |
| `build_url` + `Modelfile` 生成 | ✅ URL 与 `FROM ./Qwen3-8B-Q4_K_M.gguf` 正确 |
| `mode_selector` 非交互（cloud+local） | ✅ `mode.json` 写入正确 |
| 真实下载测试（ModelScope 直链 README.md 7577B） | ✅ 首次下载 1.5s 完成 |
| 断点续传测试（截断到 3000B 后续传） | ✅ 通过；**顺带发现并修复 2 个真 bug**：(a) 代理吞 Range 头返回 200 时的重试死循环 → 改为标记后不再带 Range；(b) 200 响应后 `ab` 追加导致数据拼接 → 200 一律清空分片从头写 |
| NSIS 脚本 | ⚠️ Linux 下无 `makensis`，未做语法检查。写法保守（仅核心指令+MUI2+nsExec+nsDialogs，无第三方插件；exe 名动态查找不写死）。**Phase 4 新增 `CloudKeyPage`＋`SecModeConfig`（见第五节），编译验证仍必须在 Actions runner 上跑 `makensis`** |

---

## 三、设计决策备忘

1. **Python 脚本零第三方依赖**：全部只用标准库（urllib/json/sqlite3/ctypes），内嵌 Python（embeddable zip）直接运行，无需 pip。—— 规避 spike 坑点2（pip 源问题）。
2. **临时目录**：`$INSTDIR\temp`（用户目录下），经 `ONECLICK_TEMP` 传入；禁用系统 Temp。—— 规避 spike 坑点3。
3. **`ollama create` 不 `ollama pull`**：下载器生成 `Modelfile`（`FROM ./Qwen3-8B-Q4_K_M.gguf` + 保守参数，不覆写 TEMPLATE），NSIS 调 `ollama create oneclick-qwen3-8b`。—— 规避 spike 坑点1。
4. **双模式阈值**：`≥16GB→local`，`<16GB→cloud`（内存检测不到时保守走 cloud）。云端模式 bge-m3 仍在本地（验证报告：峰值 1.85GB，8GB 可跑）。
5. **AnythingLLM 主程序 exe 名不写死**：`LaunchAnythingLLM` 用 FindFirst 遍历安装目录排除 `Uninstall*`，防官方改名。
6. **回滚原则**：只删自己创建的东西；Ollama/AnythingLLM 若为本次新装也不在自动回滚中卸载（避免误删用户原有版本），失败时明确提示用户手动处理。
7. **API key 明文**只落在安装目录 `config/oneclick.json`，不上传任何地方；mode_selector 中 key 输入用 getpass 不回显。

---

## 四、Windows-only 实测清单（移交 Phase 4，Actions `windows-latest` runner）

> 以下每一项都必须在真机/runner 上跑过才能发布；Linux 下无法验证。

- [ ] `makensis installer/anythingllm-oneclick.nsi` 编译通过（`makensis -nocd` 语法检查）
- [ ] `OllamaSetup.exe /SILENT /SUPPRESSMSGBOXES /NORESTART /LOG=...` 退出码为 0；`%LOCALAPPDATA%\Programs\Ollama\ollama.exe` 存在；`127.0.0.1:11434` 可连通
- [ ] `AnythingLLMDesktop.exe /S` 退出码为 0；`%LOCALAPPDATA%\Programs\AnythingLLM` 存在；`127.0.0.1:3001` 在 180s 内就绪
- [ ] `Uninstall AnythingLLM.exe` 文件名与 NSIS 脚本假设一致；Ollama 卸载程序文件名确认（`unins000.exe`？）
- [ ] AnythingLLM 主程序 exe 名确认（`LaunchAnythingLLM` 的动态查找是否命中）
- [ ] `POST /api/system/generate-api-key` 真机可用（单用户模式无鉴权）
- [ ] `POST /api/v1/workspace/new` 创建中文工作区成功，`openAiPrompt` 生效
- [ ] `POST /api/v1/system/update-env` 写入 `generic-openai` 配置后，云端模式问答可用
- [ ] `DISABLE_TELEMETRY=true`（HKCU\Environment）在 Electron 子进程继承链中生效
- [ ] CDN 依赖：检查 `%LOCALAPPDATA%\Programs\AnythingLLM\ffmpeg_install.log` / `ollama_install.log`，确认 `check_ffmpeg.py` 的补齐路径是否与 AnythingLLM 实际调用点一致
- [ ] bge-m3 原生引擎缓存目录确认；预下载方案验证
- [ ] 5GB 模型下载：断点续传（中途杀进程再跑）、弱网重试在真机可用
- [ ] `ollama create oneclick-qwen3-8b -f Modelfile` 在 Windows 路径下成功；AnythingLLM 中选中该模型问答正常
- [ ] 卸载流程：`uninstall.exe` 删除模型/配置/快捷方式；可选卸载 Ollama 与 AnythingLLM Desktop
- [ ] SmartScreen 警告出现位置截图（Phase 3 手册用）
- [ ] 云端厂商 model 名复核（`qwen-plus` / `glm-4-flash` / `deepseek-chat` 当期可用性）

## 五、Phase 4 集成 bug 修复记录（2026-10-11，Phase 4 执行）

### ✅ 已修复 Bug A-1：`mode_selector.py` 在安装流程中从未被调用（核心集成断点）

- **现象**：模式选择页只把 `$SelectedMode` 传给 `setup_anythingllm.py --mode`，
  但 NSIS 全程从未调用 `mode_selector.py --non-interactive`，
  `$INSTDIR\config\mode.json` 在安装流程中永远不会被写入；
  `setup_anythingllm.py --config` 指向不存在的文件，云端模式下 provider 为空，
  `update-env` 注入被跳过（日志 "云端模式但未提供 API key 配置"），云端模式形同虚设。
- **根因**：Phase 1 只做了"页面选模式"，漏掉了"把选择落盘"的调用；
  且此前没有任何页面收集云端厂商/API Key（`mode_selector --non-interactive`
  在云端模式下无 `--provider/--api-key` 会直接返回 1）。
- **修复**（`installer/anythingllm-oneclick.nsi`）：
  1. 新增 `CloudKeyPage`（仅云端模式显示）：厂商三选一（Qwen/GLM/DeepSeek，
     `${NSD_CreateRadioButton}`）＋ API Key 密码框（`${NSD_CreatePassword}`，
     不回显）；空 Key 或 Key <8 字符时 `Abort` 留页（与 `mode_selector.py`
     交互校验口径一致）；
  2. 新增 `Section "写入模式配置" SecModeConfig`（在模型下载节之后、预配置节之前）：
     调 `mode_selector.py --install-dir … --mode $SelectedMode --non-interactive`
     （云端追加 `--provider/--api-key`），退出码非 0 即回滚；
     随后用 `python -c` 做 mode.json 内容二次校验（`mode` 字段与用户选择一致，
     路径经 `sys.argv` 传递，`mode_selector` 本人不回显 Key，nsExec 日志无明文）；
  3. `RollbackStage` 重编号：ModeConfig=5，Preconfig=6，Shortcuts=7；
     回滚阈值同步（config 清理 `>=6`，ollama 删模型 `>=5`）。
- **顺带核实（非 bug）**：曾怀疑缺 `!include "nsDialogs.nsh"` 会导致
  `${NSD_*}` 宏无法编译；已查 NSIS 官方源码
  `Contrib/Modern UI 2/MUI2.nsh` 第 26 行确认 MUI2 自带
  `!include nsDialogs.nsh`，无需重复引入。`${NSD_CreatePassword}` 为 NSIS
  官方文档记载的 nsDialogs 自带宏，无第三方插件。
- **待验证**：`makensis` 编译（Actions runner）＋联调清单
  `INTEGRATION-TEST.md` 中 mode_selector 集成 4 项。

### Windows-only 清单同步更新

- 本文件第四节清单新增（已同步进 Phase 4 `build/INTEGRATION-TEST.md`）：
  - [ ] 云端模式：配置页填写厂商+Key 后，`config/mode.json` 含 `provider.api_key`
  - [ ] 本地模式：配置页被跳过，`mode.json` 的 `mode=="local"`
  - [ ] `setup_anythingllm.py` 读取 `--config` 后云端 `update-env` 真实写入成功
  - [ ] 安装日志中无 API Key 明文（抽查 `logs/`）
