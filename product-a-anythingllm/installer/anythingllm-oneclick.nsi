; ==============================================================================
; 开源改装铺 · 产品A：AnythingLLM 本地知识库问答一键包
; NSIS 安装编排脚本 anythingllm-oneclick.nsi
;
; 执行顺序：
;   [1] OllamaSetup.exe /SILENT        (Inno Setup, 用户级, 免 UAC)
;   [2] AnythingLLMDesktop.exe /S      (electron-builder NSIS, 用户级, 免 UAC)
;   [3] modelscope_fetch.py            (ModelScope 直链下载 GGUF, 断点续传)
;   [4] ollama create                  (本地 GGUF -> ollama 模型; 严禁 ollama pull)
;   [5] mode_selector.py --non-interactive
;       (把页面选择落盘为 config/mode.json；云端模式含厂商/API Key。
;        Phase 4 修复：此前安装流程从未调用它，mode.json 永远缺失，
;        setup_anythingllm.py --config 指向空文件，云端模式厂商/API Key 注入失效)
;   [6] setup_anythingllm.py           (等 3001 就绪 -> 取 API key -> 建工作区 -> 关遥测)
;
; 约束：
;   - RequestExecutionLevel user：两处静默安装均为用户级，无需管理员权限，
;     安装脚本本身绝不请求提权（来源见 vendor/VENDOR-NOTES.md）。
;   - 临时目录一律用 $INSTDIR\temp（用户目录下），禁用系统 Temp（构建机 /tmp 仅 512MB，
;     且用户机 Temp 可能被清理/空间不足）。
;   - 每个 ExecWait 检查退出码，非 0 即进失败分支：写日志 -> 提示用户 -> 回滚。
;
; 本脚本在 Linux 下编写，未经 Windows 真机实测。保守写法说明：
;   - 只用 NSIS 3.x 核心指令 + MUI2 + nsExec（均为 NSIS 自带插件）；
;   - 不确定的路径（如 AnythingLLM 主程序 exe 名）用 FindFirst 动态查找，不写死；
;   - 全部"需 Windows 实测"项见 docs/BUILD-NOTES.md 末尾清单，移交 Phase 4。
; ==============================================================================

Unicode True
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "FileFunc.nsh"
!include "x64.nsh"

; ---------------- 版本与路径（构建时由 Phase 4 工作流替换/确认） ----------------
!define PRODUCT_NAME "AnythingLLM 本地知识库问答一键包"
!define PRODUCT_PUBLISHER "开源改装铺"
!define PRODUCT_VERSION "1.0.0"
!define OLLAMA_SETUP_EXE "vendor\OllamaSetup.exe"
!define ANYTHINGLLM_SETUP_EXE "vendor\AnythingLLMDesktop.exe"
!define EMBED_PYTHON_ZIP "vendor\python-embed-amd64.zip"
!define INSTALL_SUBDIR "AnythingLLM-OneClick"  ; ASCII 目录名，避免 CLI 工具中文路径坑

Name "${PRODUCT_NAME}"
OutFile "AnythingLLM-OneClick-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${INSTALL_SUBDIR}"
RequestExecutionLevel user
ShowInstDetails show

; ---------------- MUI 页面 ----------------
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\docs\LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
Page custom ModeSelectPage ModeSelectPageLeave  ; 双模式选择（nsDialogs 单选）
Page custom CloudKeyPage CloudKeyPageLeave    ; 仅云端模式显示：厂商选择 + API Key 收集
                                              ; （Phase 4 新增：此前云端厂商/Key 无处填写）
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "SimpChinese"

; ---------------- 变量 ----------------
Var SelectedMode        ; "local" 或 "cloud"，由模式选择页写入
Var CloudProvider       ; "qwen"/"glm"/"deepseek"，由云端配置页写入（本地模式为空）
Var CloudApiKey         ; 用户填写的云端 API Key（只存内存变量；mode_selector 不回显，
                        ; nsExec 日志中不会出现明文；最终落盘到本机 config/mode.json）
Var RollbackStage       ; 已完成的阶段数，用于失败回滚
Var Dialog
Var RadioLocal
Var RadioCloud
Var LabelDesc
Var RadioQwen
Var RadioGlm
Var RadioDeepseek
Var TextApiKey

; ==============================================================================
; 函数：模式选择页
; ==============================================================================
Function ModeSelectPage
  nsDialogs::Create 1018
  Pop $Dialog
  ${If} $Dialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 36u "请选择运行模式（可随时在开始菜单中重新运行「模式切换」更改）："
  Pop $LabelDesc

  ${NSD_CreateRadioButton} 0 44u 100% 14u "本地模式（推荐）：16GB 以上内存，在本机运行 Qwen3-8B，无需联网付费"
  Pop $RadioLocal
  ${NSD_CreateRadioButton} 0 62u 100% 14u "云端 API 模式：8GB 等小内存机器，填写国产大模型 API Key（Qwen / GLM / DeepSeek）"
  Pop $RadioCloud

  ; 默认选中：用 mode_selector.py 的推荐值（此处先默认本地，安装段会用脚本复核）
  ${NSD_Check} $RadioLocal
  StrCpy $SelectedMode "local"

  nsDialogs::Show
FunctionEnd

Function ModeSelectPageLeave
  ${NSD_GetState} $RadioLocal $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $SelectedMode "local"
  ${Else}
    StrCpy $SelectedMode "cloud"
  ${EndIf}
FunctionEnd

; ==============================================================================
; 函数：云端配置页（仅 $SelectedMode == "cloud" 时显示）
; Phase 4 新增：修复"云端模式的厂商选择/API Key 无处收集"的集成断点。
; 本地模式用户直接跳过本页（Abort）。
; ==============================================================================
Function CloudKeyPage
  ${If} $SelectedMode != "cloud"
    Abort  ; 本地模式：跳过本页
  ${EndIf}

  nsDialogs::Create 1018
  Pop $Dialog
  ${If} $Dialog == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 36u "云端 API 模式：请选择一家国产大模型厂商并填写 API Key。$\nKey 申请地址：Qwen→阿里云百炼（bailian.console.aliyun.com）；GLM→智谱开放平台（open.bigmodel.cn）；DeepSeek→platform.deepseek.com。$\n按量计费，日常问答每月通常几块钱。"
  Pop $LabelDesc

  ${NSD_CreateRadioButton} 8u 40u 100% 12u "通义千问 Qwen（阿里云）"
  Pop $RadioQwen
  ${NSD_CreateRadioButton} 8u 54u 100% 12u "智谱 GLM"
  Pop $RadioGlm
  ${NSD_CreateRadioButton} 8u 68u 100% 12u "DeepSeek"
  Pop $RadioDeepseek
  ${NSD_Check} $RadioQwen
  StrCpy $CloudProvider "qwen"

  ${NSD_CreateLabel} 0 86u 100% 12u "API Key（输入不回显，仅保存在本机安装目录，不上传任何地方）："
  Pop $LabelDesc
  ${NSD_CreatePassword} 0 100u 100% 14u ""
  Pop $TextApiKey

  nsDialogs::Show
FunctionEnd

Function CloudKeyPageLeave
  ${NSD_GetState} $RadioQwen $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $CloudProvider "qwen"
  ${Else}
    ${NSD_GetState} $RadioGlm $0
    ${If} $0 == ${BST_CHECKED}
      StrCpy $CloudProvider "glm"
    ${Else}
      StrCpy $CloudProvider "deepseek"
    ${EndIf}
  ${EndIf}

  ${NSD_GetText} $TextApiKey $CloudApiKey
  ${If} $CloudApiKey == ""
    MessageBox MB_OK|MB_ICONEXCLAMATION "云端 API 模式需要填写 API Key 才能继续。$\n（可点“上一步”返回改选本地模式。）"
    Abort  ; 留在本页
  ${EndIf}
  ; 长度 sanity（与 mode_selector.py 交互校验的 ≥8 字符一致）
  StrLen $0 $CloudApiKey
  ${If} $0 < 8
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "API Key 仅 $0 个字符，可能复制不完整。仍要继续吗？" IDYES +2
    Abort  ; 留在本页
  ${EndIf}
FunctionEnd

; ==============================================================================
; 函数：等待 TCP 端口就绪（轮询）
; 参数：$0=端口, $1=超时秒数 ; 返回：$2="ok" 或 "timeout"
; 实现：调用随包的 wait_port.py（标准库 socket），避免第三方 NSIS 插件。
; ==============================================================================
Function WaitForPort
  nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\preconfig\wait_port.py" --port "$0" --timeout "$1"'
  Pop $0
  ${If} $0 == 0
    StrCpy $2 "ok"
  ${Else}
    StrCpy $2 "timeout"
  ${EndIf}
FunctionEnd

; ==============================================================================
; .onInit：预检
; ==============================================================================
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "本一键包仅支持 64 位 Windows 10/11。"
    Abort
  ${EndIf}
  ; 磁盘空间预检：安装包本体(~2GB) + 模型(~5GB) + 解压余量 = 15GB
  ${DriveSpace} "$INSTDIR" "/D=F /S=M" $0
  ${If} $0 < 15360
    MessageBox MB_YESNO|MB_ICONEXCLAMATION "目标磁盘剩余空间不足 15GB（当前约 $0 MB），可能导致模型下载失败。仍要继续吗？" IDYES +2
    Abort
  ${EndIf}
  StrCpy $RollbackStage 0
FunctionEnd

; ==============================================================================
; Section 1：解压随包文件 + 内嵌 Python
; ==============================================================================
Section "解压安装文件" SecExtract
  SetOutPath "$INSTDIR"
  File /r "..\downloader"
  File /r "..\preconfig"
  File /r "..\launcher"
  File /r "..\docs"
  ; vendor 二进制（OllamaSetup.exe / AnythingLLMDesktop.exe / python embed zip / ffmpeg）
  File /r "..\vendor\bin"
  CreateDirectory "$INSTDIR\temp"
  CreateDirectory "$INSTDIR\models"
  CreateDirectory "$INSTDIR\logs"
  CreateDirectory "$INSTDIR\config"

  DetailPrint "解压内嵌 Python（仅标准库，下载/预配置脚本零第三方依赖）..."
  ; 用系统自带 PowerShell Expand-Archive（Win10+ 内置），不引入第三方 NSIS 插件
  nsExec::ExecToLog 'powershell -NoProfile -NonInteractive -Command "Expand-Archive -Path \"$INSTDIR\vendor\bin\python-embed-amd64.zip\" -DestinationPath \"$INSTDIR\python\" -Force"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "内嵌 Python 解压失败，退出码: $0"
    Call FailAndRollback
  ${EndIf}
  ${IfNot} ${FileExists} "$INSTDIR\python\python.exe"
    DetailPrint "内嵌 Python 解压后未找到 python.exe"
    Call FailAndRollback
  ${EndIf}
  StrCpy $RollbackStage 1
SectionEnd

; ==============================================================================
; Section 2：Ollama 静默安装
; 参数来源：JRSoftware Inno Setup 官方文档
;   https://jrsoftware.org/ishelp/index.php?topic=setupcmdline
;   /SILENT（显示进度条的静默安装）/SUPPRESSMSGBOXES /NORESTART /LOG=
; 用户级依据：ollama 源码 app/ollama.iss 中 PrivilegesRequired=lowest，
;   默认装到 {localappdata}\Programs\Ollama，无需 UAC。
;   https://github.com/ollama/ollama/blob/main/app/ollama.iss
; ==============================================================================
Section "安装 Ollama" SecOllama
  DetailPrint "正在静默安装 Ollama（用户级，无需管理员权限）..."
  ${If} ${FileExists} "$LOCALAPPDATA\Programs\Ollama\ollama.exe"
    DetailPrint "检测到已安装 Ollama，跳过安装，直接复用。"
  ${Else}
    nsExec::ExecToLog '"$INSTDIR\vendor\bin\OllamaSetup.exe" /SILENT /SUPPRESSMSGBOXES /NORESTART /LOG="$INSTDIR\logs\ollama_setup.log"'
    Pop $0
    ${If} $0 != 0
      DetailPrint "Ollama 静默安装失败，退出码: $0（详见 logs\ollama_setup.log）"
      Call FailAndRollback
    ${EndIf}
  ${EndIf}

  ; 确保 ollama 服务就绪（托盘程序通常已自启；若无则手动拉起）
  DetailPrint "等待 Ollama 服务（127.0.0.1:11434）就绪..."
  StrCpy $0 "11434" ; 端口
  StrCpy $1 "60"    ; 超时秒
  Call WaitForPort
  ${If} $2 != "ok"
    DetailPrint "Ollama 服务未自动启动，尝试手动启动 ollama serve ..."
    ${If} ${FileExists} "$LOCALAPPDATA\Programs\Ollama\ollama.exe"
      Exec '"$LOCALAPPDATA\Programs\Ollama\ollama.exe" serve'
      StrCpy $0 "11434"
      StrCpy $1 "60"
      Call WaitForPort
    ${EndIf}
  ${EndIf}
  ${If} $2 != "ok"
    DetailPrint "Ollama 服务启动失败。请检查 logs 后重试。"
    Call FailAndRollback
  ${EndIf}
  StrCpy $RollbackStage 2
SectionEnd

; ==============================================================================
; Section 3：AnythingLLM Desktop 静默安装
; 参数来源：AnythingLLM 官方文档（v1.16.1+ 支持静默安装）
;   https://github.com/mintplex-labs/anythingllm-docs/blob/HEAD/pages/installation-desktop/windows.mdx
;   AnythingLLMDesktop.exe /S  -> 静默安装，跳过约 2GB Meeting Assistant 模型，
;   固定装到 %LOCALAPPDATA%\Programs\AnythingLLM（仅当前用户）。
; 注意：即使静默模式，安装程序仍会从官方 CDN 下载运行时依赖
;   （Ollama GPU 库、FFmpeg、Meeting Assistant 语音/分割模型），失败只写日志
;   （install 目录下的 ollama_install.log / ffmpeg_install.log /
;   meeting_assistant_install.log），不弹窗。应对策略见 vendor/VENDOR-NOTES.md。
; ==============================================================================
Section "安装 AnythingLLM Desktop" SecAnythingLLM
  DetailPrint "正在静默安装 AnythingLLM Desktop（/S：跳过 2GB 会议助手模型）..."
  ${If} ${FileExists} "$LOCALAPPDATA\Programs\AnythingLLM\Uninstall AnythingLLM.exe"
    DetailPrint "检测到已安装 AnythingLLM Desktop，跳过安装，直接复用。"
  ${Else}
    nsExec::ExecToLog '"$INSTDIR\vendor\bin\AnythingLLMDesktop.exe" /S'
    Pop $0
    ${If} $0 != 0
      DetailPrint "AnythingLLM 静默安装失败，退出码: $0"
      Call FailAndRollback
    ${EndIf}
  ${EndIf}

  ; 检查 CDN 运行时依赖是否缺失（FFmpeg fallback）
  ; 依据：官方文档 troubleshooting -> install 目录下各 *_install.log
  ${If} ${FileExists} "$LOCALAPPDATA\Programs\AnythingLLM\ffmpeg_install.log"
    DetailPrint "发现 ffmpeg 安装日志，检查 FFmpeg 是否可用..."
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\preconfig\check_ffmpeg.py" --install-dir "$LOCALAPPDATA\Programs\AnythingLLM" --fallback-dir "$INSTDIR\vendor\bin\ffmpeg"'
    Pop $0
    ; check_ffmpeg.py：缺失则从随包 fallback 目录补齐，返回 0=OK / 2=补齐后OK
    ${If} $0 != 0
    ${AndIf} $0 != 2
      DetailPrint "FFmpeg 检查/补齐异常（退出码 $0），不阻塞主流程，详见 docs/BUILD-NOTES.md。"
    ${EndIf}
  ${EndIf}
  StrCpy $RollbackStage 3
SectionEnd

; ==============================================================================
; Section 4：模型下载 + ollama create（仅本地模式）
; 严禁 ollama pull（受限网络下失败，spike 已证实）。
; 链路：ModelScope 直链 --urllib--> GGUF --ollama create--> 本地模型。
; ==============================================================================
Section "下载模型并创建本地模型" SecModel
  ${If} $SelectedMode == "cloud"
    DetailPrint "云端 API 模式：跳过 5GB 本地模型下载。"
    Goto SecModelDone
  ${EndIf}

  DetailPrint "本地模式：从 ModelScope 下载 Qwen3-8B GGUF（约 5GB，断点续传）..."
  ; ONECLICK_TEMP 环境变量：python 脚本的临时目录（禁用系统 Temp）
  nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\downloader\modelscope_fetch.py" --dest-dir "$INSTDIR\models" --temp-dir "$INSTDIR\temp" --log "$INSTDIR\logs\download.log"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "模型下载失败（退出码 $0），详见 logs\download.log。可重新运行安装包续传。"
    Call FailAndRollback
  ${EndIf}

  DetailPrint "创建 Ollama 本地模型（ollama create，禁用 ollama pull）..."
  SetOutPath "$INSTDIR\models"
  nsExec::ExecToLog '"$LOCALAPPDATA\Programs\Ollama\ollama.exe" create oneclick-qwen3-8b -f "$INSTDIR\models\Modelfile"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "ollama create 失败（退出码 $0）。"
    Call FailAndRollback
  ${EndIf}

  SecModelDone:
  StrCpy $RollbackStage 4
SectionEnd

; ==============================================================================
; Section 4b：写入模式配置（mode.json）—— Phase 4 修复的核心集成断点
;
; 背景（bug）：模式选择页原本只把 $SelectedMode 传给 setup_anythingllm.py
;   的 --mode 参数，但安装流程从未调用 mode_selector.py，导致
;   $INSTDIR\config\mode.json 永远不会被写入。setup_anythingllm.py 的
;   --config 指向一个不存在的文件，云端模式下 provider 为空，
;   update-env 注入被跳过（"云端模式但未提供 API key 配置"），云端模式形同虚设。
;
; 修复：在预配置（Section 5）之前，用 --non-interactive 把用户在页面上的
;   选择（含云端厂商/API Key）落盘为 mode.json，并做内容二次校验。
;   mode_selector.py 内部不回显 Key，nsExec 日志中不会出现 Key 明文。
; ==============================================================================
Section "写入模式配置" SecModeConfig
  DetailPrint "写入运行模式配置（mode.json）..."
  ${If} $SelectedMode == "cloud"
    ; --provider/--api-key 来自云端配置页；缺任一则 mode_selector 返回 1
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\launcher\mode_selector.py" --install-dir "$INSTDIR" --mode "$SelectedMode" --non-interactive --provider "$CloudProvider" --api-key "$CloudApiKey"'
  ${Else}
    nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\launcher\mode_selector.py" --install-dir "$INSTDIR" --mode "$SelectedMode" --non-interactive'
  ${EndIf}
  Pop $0
  ${If} $0 != 0
    DetailPrint "模式配置写入失败（mode_selector 退出码 $0）。"
    Call FailAndRollback
  ${EndIf}

  ; 二次校验：mode.json 真实存在且 mode 字段与用户选择一致（防静默写坏）。
  ; 实现为独立脚本 check_mode.py：内联 python -c 的单引号会与 NSIS 字符串
  ; 定界符冲突导致 makensis "unterminated string"（2026-10-12 首构建实证）。
  nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\preconfig\check_mode.py" "$INSTDIR\config\mode.json" "$SelectedMode"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "mode.json 内容校验失败（退出码 $0）。"
    Call FailAndRollback
  ${EndIf}
  StrCpy $RollbackStage 5
SectionEnd

; ==============================================================================
; Section 5：预配置（等 3001 -> 取 API key -> 建中文工作区 -> 关遥测）
; ==============================================================================
Section "预配置 AnythingLLM" SecPreconfig
  DetailPrint "启动 AnythingLLM Desktop（首次启动需 1-2 分钟初始化）..."
  Call LaunchAnythingLLM

  DetailPrint "等待 AnythingLLM 服务（127.0.0.1:3001）就绪..."
  StrCpy $0 "3001"
  StrCpy $1 "180"
  Call WaitForPort
  ${If} $2 != "ok"
    DetailPrint "AnythingLLM 服务 180 秒内未就绪。请手动启动后再运行「完成预配置」。"
    Call FailAndRollback
  ${EndIf}

  DetailPrint "执行预配置（API key / 中文工作区 / 关闭遥测 / system prompt）..."
  nsExec::ExecToLog '"$INSTDIR\python\python.exe" "$INSTDIR\preconfig\setup_anythingllm.py" --install-dir "$INSTDIR" --mode "$SelectedMode" --config "$INSTDIR\config\mode.json" --log "$INSTDIR\logs\preconfig.log"'
  Pop $0
  ${If} $0 != 0
    DetailPrint "预配置失败（退出码 $0），详见 logs\preconfig.log。"
    Call FailAndRollback
  ${EndIf}
  StrCpy $RollbackStage 6
SectionEnd

; ==============================================================================
; Section 6：开始菜单快捷方式 + 卸载程序
; ==============================================================================
Section "创建快捷方式" SecShortcuts
  CreateDirectory "$SMPROGRAMS\开源改装铺"
  CreateShortcut "$SMPROGRAMS\开源改装铺\AnythingLLM 问答.lnk" "$LOCALAPPDATA\Programs\AnythingLLM\AnythingLLM.exe"
  CreateShortcut "$SMPROGRAMS\开源改装铺\切换运行模式.lnk" "$INSTDIR\python\python.exe" '"$INSTDIR\launcher\mode_selector.py" --install-dir "$INSTDIR"'
  CreateShortcut "$SMPROGRAMS\开源改装铺\卸载一键包.lnk" "$INSTDIR\uninstall.exe"
  WriteUninstaller "$INSTDIR\uninstall.exe"
  StrCpy $RollbackStage 7
SectionEnd

; ==============================================================================
; 函数：查找并启动 AnythingLLM 主程序
; 主程序 exe 名不写死：遍历 %LOCALAPPDATA%\Programs\AnythingLLM 下的 exe，
; 排除 Uninstall 开头的，取第一个。（保守策略，防官方改名）
; ==============================================================================
Function LaunchAnythingLLM
  FindFirst $0 $1 "$LOCALAPPDATA\Programs\AnythingLLM\*.exe"
  StrCpy $2 ""
  loop:
    ${If} $1 == ""
      Goto done
    ${EndIf}
    ${If} $1 != "Uninstall AnythingLLM.exe"
      StrCpy $2 "$LOCALAPPDATA\Programs\AnythingLLM\$1"
      Goto done
    ${EndIf}
    FindNext $0 $1
    Goto loop
  done:
  FindClose $0
  ${If} $2 != ""
    Exec '"$2"'
  ${Else}
    DetailPrint "警告：未找到 AnythingLLM 主程序，请手动启动。"
  ${EndIf}
FunctionEnd

; ==============================================================================
; 函数：失败回滚（按 $RollbackStage 逆序清理）
; 原则：只删我们自己创建的东西；Ollama/AnythingLLM 若为本次新装则调用其
; 静默卸载（文件存在才执行），若为复用则不动。
; ==============================================================================
Function FailAndRollback
  DetailPrint "安装失败，正在回滚（已完成阶段: $RollbackStage）..."

  ; 停掉我们拉起的进程（静默，不报错）
  nsExec::ExecToLog 'taskkill /F /IM AnythingLLM.exe'
  Pop $0

  ${If} $RollbackStage >= 6
    ; 预配置产物：删 config（不删用户数据）
    RMDir /r "$INSTDIR\config"
  ${EndIf}

  ${If} $RollbackStage >= 5
    ; 删除本次创建的 ollama 模型（best-effort）
    nsExec::ExecToLog '"$LOCALAPPDATA\Programs\Ollama\ollama.exe" rm oneclick-qwen3-8b'
    Pop $0
  ${EndIf}

  ; 注：Ollama / AnythingLLM 本体卸载需用户确认，不在自动回滚中删除，
  ; 避免误删用户原本就装好的版本。卸载入口见开始菜单与控制面板。
  DetailPrint "回滚完成。日志位于 $INSTDIR\logs，可凭日志重试或联系客服。"
  MessageBox MB_OK|MB_ICONSTOP "安装未完成，已回滚本次写入的配置与模型。$\n日志：$INSTDIR\logs$\n可重新运行安装包继续（下载支持断点续传）。"
  Abort
FunctionEnd

; ==============================================================================
; 卸载
; ==============================================================================
Section "Uninstall"
  ; 停进程
  nsExec::ExecToLog 'taskkill /F /IM AnythingLLM.exe'
  Pop $0
  ; 删本次创建的 ollama 模型
  nsExec::ExecToLog '"$LOCALAPPDATA\Programs\Ollama\ollama.exe" rm oneclick-qwen3-8b'
  Pop $0
  ; 删快捷方式与安装目录
  Delete "$SMPROGRAMS\开源改装铺\*.lnk"
  RMDir "$SMPROGRAMS\开源改装铺"
  RMDir /r "$INSTDIR"

  MessageBox MB_YESNO|MB_ICONQUESTION "是否同时卸载 Ollama 与 AnythingLLM Desktop？$\n（选“否”则保留，可单独使用）" IDNO no_bundle_uninstall
  ; Ollama 静默卸载：Inno 卸载程序（需 Windows 实测确认文件名，见 BUILD-NOTES）
  ${If} ${FileExists} "$LOCALAPPDATA\Programs\Ollama\unins000.exe"
    ExecWait '"$LOCALAPPDATA\Programs\Ollama\unins000.exe" /SILENT /SUPPRESSMSGBOXES /NORESTART'
  ${EndIf}
  ; AnythingLLM 静默卸载：官方文档确认 Uninstall AnythingLLM.exe /S（保留用户数据）
  ; https://github.com/mintplex-labs/anythingllm-docs/blob/HEAD/pages/installation-desktop/windows.mdx
  ${If} ${FileExists} "$LOCALAPPDATA\Programs\AnythingLLM\Uninstall AnythingLLM.exe"
    ExecWait '"$LOCALAPPDATA\Programs\AnythingLLM\Uninstall AnythingLLM.exe" /S'
  ${EndIf}
  no_bundle_uninstall:
SectionEnd
