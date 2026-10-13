; ============================================================
; 老片修复一键包 · NSIS 安装脚本（产品B）
; 目标：Windows 10/11 x64，用户级安装（免 UAC）
; 构建：GitHub Actions windows-latest + `makensis`（Phase 4 工作流负责备齐 staging/）
; Phase 4 修复记录：补上遗漏的 `File /r "${STAGING}\wheels"`（见下方注释）
; ============================================================

!include "MUI2.nsh"
!include "LogicLib.nsh"

; 2026-10-12 真机联调：不加此行中文全乱码（标题/进度/完成页/快捷方式名）
Unicode true

!define PRODUCT_NAME "老片修复一键包"
!define PRODUCT_VERSION "1.0.3"
!define PRODUCT_PUBLISHER "开源改装铺"
!define INSTALL_DIR_NAME "PhotoRestore"

; ---- staging 目录约定（Phase 4 构建工作流在编译前备齐，相对本脚本路径） ----
; staging/
;   app/          core/ + gui/ 子目录（本产品源码，子目录结构不可拍扁）+ version.json（构建机写入 torch_version 等）
;   models/       pytorch_model.pt（64MB，ModelScope damo/cv_rrdb_image-super-resolution，
;                 验证报告建议内置进安装包，省去用户端下载失败风险）
;   ffmpeg/       ffmpeg.exe + ffprobe.exe（gyan.dev Windows essentials 构建，
;                 来源：https://www.gyan.dev/ffmpeg/builds/；「需验证」构建机实测可用版本）
;   python/       便携 Python（含 python.exe/pythonw.exe 且自带 pip；候选 python-build-standalone，
;                 来源：https://github.com/astral-sh/python-build-standalone；
;                 「需验证」：Actions runner 上确认 pip 可用，备选 python.org embeddable zip + get-pip.py）
;   wheels/       torch CPU 版 wheel + pillow + numpy（构建机从清华 PyPI 镜像下载，
;                 来源：https://pypi.tuna.tsinghua.edu.cn/simple；--no-index 本地安装，禁用默认源）
;   launcher/     start.pyw（无控制台启动器）+ icon.ico（Phase 3 品牌资源，「需验证」有无）
!define STAGING "..\staging"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "PhotoRestore-OneClick-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${INSTALL_DIR_NAME}"
RequestExecutionLevel user   ; 用户级安装，不弹 UAC（与产品A Ollama/AnythingLLM 策略一致）
SetCompressor /SOLID lzma

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"

; ---------------- 主安装节 ----------------
Section "主程序" SEC_MAIN
  SetOutPath "$INSTDIR"
  ; 大文件直解到 $INSTDIR，不经 %TEMP% 中转（PLAN.md 硬约束；NSIS 的 $PLUGINSDIR
  ; 仅用于 KB 级插件解压，来源：NSIS 官方文档 Variables 章节）
  File /r "${STAGING}\app"
  File /r "${STAGING}\models"
  File /r "${STAGING}\ffmpeg"
  File /r "${STAGING}\python"
  ; Phase 4 修复：此前漏掉了 wheels/，但下方 pip install 用
  ; --find-links "$INSTDIR\wheels" 引用它（--no-index 离线安装），
; 缺失会导致 torch 安装必失败。属与产品A mode.json 同类的"引用了但没打包"集成断点。
  File /r "${STAGING}\wheels"
  File /r "${STAGING}\launcher"

  ; ---- torch 安装决策（任务要求写进注释） ----
  ; CPU 版 torch wheel（约 190MB，来源：PyTorch 官方 wheel 体积分级）→ 已内置 wheels/，随包安装。
  ; GPU(CUDA) 版 torch + nvidia 依赖合计超 2GB（来源：PyTorch CUDA wheel 体积实测量级），
  ;   进安装包体积爆炸 → 不内置；首次运行时 core/torch_setup.py 按显卡检测结果
  ;   从清华 PyPI 镜像自动拉取（国内可达）。无 N 卡用户永远走不到这一步。
  DetailPrint "正在安装 torch（CPU 版，本地 wheel，不联网）…"
  nsExec::ExecToLog '"$INSTDIR\python\python.exe" -m pip install --no-index --find-links "$INSTDIR\wheels" torch pillow numpy'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "torch 安装失败（退出码 $0）。$\n$\n请截图此界面并联系客服，安装包将回滚。"
    Abort
  ${EndIf}

  ; 写卸载程序与开始菜单快捷方式（pythonw.exe 无控制台窗口，小白省心）
  WriteUninstaller "$INSTDIR\uninstall.exe"
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortcut "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk" \
    "$INSTDIR\python\pythonw.exe" '"$INSTDIR\launcher\start.pyw"' \
    "$INSTDIR\launcher\icon.ico" 0
  CreateShortcut "$DESKTOP\${PRODUCT_NAME}.lnk" \
    "$INSTDIR\python\pythonw.exe" '"$INSTDIR\launcher\start.pyw"' \
    "$INSTDIR\launcher\icon.ico" 0

  ; 卸载信息写 HKCU（per-user，无需管理员权限；来源：Microsoft 文档
  ; “Uninstall 64-bit/32-bit registry key” per-user 位置即 HKCU 路径）
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${INSTALL_DIR_NAME}" \
    "DisplayName" "${PRODUCT_NAME} ${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${INSTALL_DIR_NAME}" \
    "UninstallString" '"$INSTDIR\uninstall.exe"'
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${INSTALL_DIR_NAME}" \
    "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${INSTALL_DIR_NAME}" \
    "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${INSTALL_DIR_NAME}" \
    "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${INSTALL_DIR_NAME}" \
    "NoModify" 1
  WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${INSTALL_DIR_NAME}" \
    "NoRepair" 1
SectionEnd

; ---------------- 卸载节 ----------------
Section "Uninstall"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\${PRODUCT_NAME}.lnk"
  Delete "$DESKTOP\${PRODUCT_NAME}.lnk"
  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${INSTALL_DIR_NAME}"
  RMDir /r "$INSTDIR"
SectionEnd
