#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开源改装铺 · 产品A：AnythingLLM 一键包 —— 预配置脚本

执行时机：NSIS 安装的最后阶段（Ollama/AnythingLLM/模型均已就绪，AnythingLLM
桌面端已启动，127.0.0.1:3001 可连通）。

步骤：
  1. 等待 3001 就绪
  2. 取 Developer API key：
     - 主路径：POST http://127.0.0.1:3001/api/system/generate-api-key
       （源码：server/endpoints/system.js；单用户模式下 validatedRequest 直接放行，
       来源见 dual-launch-verification.md 第 1 节；返回 {"apiKey": {"secret": ...}}）
     - 备用：向 %APPDATA%\\anythingllm-desktop\\storage\\anythingllm.db 的 api_keys
       表 INSERT 一行（表结构来源：server/prisma/schema.prisma）
  3. 写入安装目录 config/oneclick.json（含 key、模式、模型名）
  4. 关闭遥测：写 HKCU\\Environment\\DISABLE_TELEMETRY=true
     （来源：server/utils/telemetry/index.js 注释 "disabled by setting
     DISABLE_TELEMETRY=true"；桌面端 server 为 Electron 子进程，继承用户环境变量）
  5. 创建默认中文工作区「我的知识库」，注入清洗过的 system prompt
     （端点来源：server/endpoints/api/workspace/index.js -> POST /api/v1/workspace/new，
     字段 openAiPrompt 经源码 #swagger 注释核实）
  6. 云端 API 模式：在工作区/系统级写入 generic-openai 提供商配置
     （端点 POST /api/v1/system/update-env，env 键名经源码核实：
     LLMProvider / GENERIC_OPEN_AI_BASE_PATH / GENERIC_OPEN_AI_API_KEY /
     GENERIC_OPEN_AI_MODEL_PREF）

仅用标准库（urllib/json/sqlite3/ctypes），内嵌 Python 直接运行。

退出码：0=全部成功；2=部分成功（key 拿到但工作区/遥测某一步失败，不阻塞）；
        1=致命失败（key 拿不到）。
"""

import argparse
import datetime
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
import urllib.error
import uuid

API_BASE = "http://127.0.0.1:3001"

# ---------------------------------------------------------------------------
# system prompt：中文知识库问答专用。
# 关键指令：禁止输出思考过程 / <think> 标签（spike 坑点4：qwen3 系即使加
# /no_think 仍可能残留空 <think></think>；v1.8.1+ 的 UI 已会隐藏空 thoughts，
# 见官方 changelog，但 prompt 层 + 清洗函数做双保险）。
# 注入前会经过 clean_think_tags() 做一次自清洗（防模板残留）。
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """你是「开源改装铺」本地知识库问答助手，运行在用户自己的电脑上。
规则：
1. 优先依据知识库检索到的文档片段回答；引用时注明出处。
2. 知识库中没有相关内容时，明确说明"知识库中没有找到相关内容"，再给出通用回答。
3. 用简体中文回答，条理清晰，必要时分点。
4. 直接输出最终答案，不要输出思考过程，不要使用 <think> 标签或任何形式的推理标记。
5. 不要编造文档中不存在的细节。"""

WORKSPACE_NAME = "我的知识库"


def clean_think_tags(text: str) -> str:
    """清洗空 <think> 标签（spike 坑点4）。

    只移除内容为空或纯空白的 <think>...</think> 对；有实际推理内容的
    成对标签原样保留（AnythingLLM v1.8.1+ 的 UI 会把非空 thoughts 收进折叠区，
    见官方 changelog v1.8.1 "Fix empty thoughts from reasoning models"）；
    不成对的孤立标签（模型截断等极端情况）移除。大小写不敏感，跨行匹配。
    """
    # 先把非空成对标签用占位符保护起来，避免第二步误删
    pairs = []

    def _protect(m):
        if m.group(1).strip() == "":
            return ""  # 空对直接删除
        pairs.append(m.group(0))
        return f"\x00THINKPAIR{len(pairs) - 1}\x00"

    cleaned = re.sub(r"<think>(.*?)</think>", _protect, text,
                     flags=re.IGNORECASE | re.DOTALL)
    # 此时剩下的 <think>/</think> 必然不成对（孤立标签），移除
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    # 还原被保护的非空对
    for i, original in enumerate(pairs):
        cleaned = cleaned.replace(f"\x00THINKPAIR{i}\x00", original)
    return cleaned.strip()


# ---------------------------------------------------------------------------
class Logger:
    def __init__(self, path=None):
        self.path = path
        self._fh = open(path, "a", encoding="utf-8") if path else None

    def log(self, msg):
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if self._fh:
            self._fh.write(line + "\n")
            self._fh.flush()

    def close(self):
        if self._fh:
            self._fh.close()


def wait_for_port(host, port, timeout, logger):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection((host, port), timeout=2)
            s.close()
            return True
        except OSError:
            time.sleep(2)
    return False


def api_post(path, body, api_key=None, timeout=30):
    """调用 AnythingLLM Developer API（桌面端 :3001，路径含 /api 前缀）。"""
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(API_BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_api_key_via_http(logger):
    """主路径：POST /api/system/generate-api-key（单用户模式无鉴权）。"""
    logger.log("主路径：POST /api/system/generate-api-key 获取 API key ...")
    # 端点源码：server/endpoints/system.js:1056（validatedRequest 在单用户
    # 无密码时直接 next()，见 server/utils/middleware/validatedRequest.js）
    resp = api_post("/api/system/generate-api-key", {})
    key = (resp.get("apiKey") or {}).get("secret")
    if not key:
        raise RuntimeError(f"端点返回异常：{str(resp)[:200]}")
    logger.log("API key 获取成功（已隐去明文）")
    return key


def get_api_key_via_sqlite(logger):
    """备用路径：直接向 anythingllm.db 的 api_keys 表 INSERT。

    表结构来源：server/prisma/schema.prisma -> model api_keys
    （id / name / secret(unique) / createdBy / createdAt / lastUpdatedAt）。
    secret 格式：uuid-apikey 生成形如 "XXXXXX-XXXXXX-..." 的随机串；服务端
    仅做 Bearer 字符串比对，任意唯一随机串均可（create 逻辑见
    server/models/apiKeys.js 的 ApiKey.create）。
    """
    logger.log("备用路径：SQLite 预置 api_keys ...")
    if os.name != "nt":
        raise RuntimeError("备用路径仅支持 Windows（需 %APPDATA% 路径）")
    db = os.path.join(os.path.expandvars("%APPDATA%"),
                      "anythingllm-desktop", "storage", "anythingllm.db")
    if not os.path.isfile(db):
        raise RuntimeError(f"找不到数据库文件：{db}")
    # 生成与 uuid-apikey 同风格的随机 secret（6 段 base32 风格，此处用 uuid4 hex
    # 分段；服务端只要求 unique，不校验格式 —— 见 ApiKey.create 源码）
    raw = uuid.uuid4().hex.upper()
    secret = "-".join(raw[i:i + 6] for i in range(0, 30, 6))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(db)
    try:
        con.execute(
            "INSERT INTO api_keys (name, secret, createdBy, createdAt, lastUpdatedAt)"
            " VALUES (?, ?, ?, ?, ?)",
            ("oneclick-setup", secret, None, now, now),
        )
        con.commit()
    finally:
        con.close()
    logger.log("SQLite 预置 API key 成功")
    return secret


def disable_telemetry(logger):
    """关闭遥测：写用户环境变量 DISABLE_TELEMETRY=true。

    来源：server/utils/telemetry/index.js 头部注释：
      "This can be disabled by setting DISABLE_TELEMETRY=true"。
    桌面端 server 跑在 Electron 子进程中，继承用户环境变量；写 HKCU\\Environment
    后新启动的 AnythingLLM 生效（需重启应用；安装流程中我们在配置完成后再启动）。
    需 Windows 真机验证继承链（见 BUILD-NOTES）。
    """
    if os.name != "nt":
        logger.log("非 Windows，跳过遥测关闭（Linux 构建机不跑桌面端）")
        return True
    r = subprocess.run(
        ["reg", "add", "HKCU\\Environment", "/v", "DISABLE_TELEMETRY",
         "/t", "REG_SZ", "/d", "true", "/f"],
        capture_output=True, text=True)
    if r.returncode == 0:
        logger.log("已写入 HKCU\\Environment\\DISABLE_TELEMETRY=true（重启应用后生效）")
        return True
    logger.log(f"遥测关闭失败（reg 退出码 {r.returncode}）：{r.stderr.strip()[:200]}")
    return False


def create_workspace(api_key, logger):
    """创建默认中文工作区并注入 system prompt。

    端点来源：server/endpoints/api/workspace/index.js
      app.post("/v1/workspace/new", [validApiKey], ...)
    字段经源码 #swagger 注释核实：name / openAiPrompt / chatMode / topN 等。
    """
    prompt = clean_think_tags(SYSTEM_PROMPT)
    logger.log(f"创建工作区「{WORKSPACE_NAME}」...")
    resp = api_post("/api/v1/workspace/new", {
        "name": WORKSPACE_NAME,
        "openAiPrompt": prompt,
        "chatMode": "chat",
        "topN": 4,
    }, api_key=api_key)
    ws = resp.get("workspace") or {}
    slug = ws.get("slug", "")
    logger.log(f"工作区创建成功，slug={slug}")
    return slug


def apply_cloud_provider(api_key, provider_cfg, logger):
    """云端 API 模式：经 update-env 把 LLM 提供商切到 generic-openai。

    端点：POST /api/v1/system/update-env（源码 server/endpoints/api/system/index.js:107）。
    env 键（源码 server/utils/AiProviders/genericOpenAi/index.js 及
    server/utils/helpers/updateENV.js supportedLLM）：
      LLMProvider="generic-openai", GENERIC_OPEN_AI_BASE_PATH,
      GENERIC_OPEN_AI_API_KEY, GENERIC_OPEN_AI_MODEL_PREF
    provider_cfg 来自 mode_selector 收集的用户输入（base_url/api_key/model）。
    """
    logger.log(f"云端模式：配置 {provider_cfg['label']}（generic-openai 兼容接口）...")
    resp = api_post("/api/v1/system/update-env", {
        "LLMProvider": "generic-openai",
        "GENERIC_OPEN_AI_BASE_PATH": provider_cfg["base_url"],
        "GENERIC_OPEN_AI_API_KEY": provider_cfg["api_key"],
        "GENERIC_OPEN_AI_MODEL_PREF": provider_cfg["model"],
    }, api_key=api_key)
    if resp.get("error"):
        raise RuntimeError(f"update-env 返回错误：{resp['error']}")
    logger.log("云端提供商配置已写入")


def main(argv=None):
    p = argparse.ArgumentParser(description="AnythingLLM 一键包预配置")
    p.add_argument("--install-dir", required=True)
    p.add_argument("--mode", default="local", choices=["local", "cloud"])
    p.add_argument("--config", default=None, help="mode_selector 输出的 mode.json")
    p.add_argument("--log", default=None)
    p.add_argument("--port-timeout", type=int, default=120)
    a = p.parse_args(argv)
    logger = Logger(a.log)
    partial = False  # 部分成功标记
    try:
        install_dir = os.path.abspath(a.install_dir)
        cfg_dir = os.path.join(install_dir, "config")
        os.makedirs(cfg_dir, exist_ok=True)

        logger.log("等待 AnythingLLM 服务（127.0.0.1:3001）...")
        if not wait_for_port("127.0.0.1", 3001, a.port_timeout, logger):
            logger.log("3001 端口超时，预配置中止")
            return 1

        # 1. API key（主 -> 备）
        try:
            api_key = get_api_key_via_http(logger)
        except Exception as e:
            logger.log(f"主路径失败（{e}），切换 SQLite 备用路径")
            try:
                api_key = get_api_key_via_sqlite(logger)
            except Exception as e2:
                logger.log(f"备用路径也失败：{e2}")
                return 1

        # 2. 写本地配置（key 明文只落安装目录 config，不上传任何地方）
        mode_cfg = {}
        if a.config and os.path.isfile(a.config):
            with open(a.config, encoding="utf-8") as fh:
                mode_cfg = json.load(fh)
        oneclick_cfg = {
            "api_base": API_BASE,
            "api_key": api_key,
            "mode": a.mode,
            "provider": mode_cfg.get("provider"),
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        cfg_path = os.path.join(cfg_dir, "oneclick.json")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(oneclick_cfg, fh, ensure_ascii=False, indent=2)
        logger.log(f"配置已写入 {cfg_path}")

        # 3. 关遥测（失败不致命）
        if not disable_telemetry(logger):
            partial = True

        # 4. 建中文工作区（失败不致命：用户可手动建）
        try:
            create_workspace(api_key, logger)
        except Exception as e:
            logger.log(f"工作区创建失败（{e}），用户可在界面手动创建")
            partial = True

        # 5. 云端模式：写入提供商配置
        if a.mode == "cloud":
            provider = mode_cfg.get("provider")
            if not provider or not provider.get("api_key"):
                logger.log("云端模式但未提供 API key 配置，跳过提供商写入（用户稍后手动填）")
                partial = True
            else:
                try:
                    apply_cloud_provider(api_key, provider, logger)
                except Exception as e:
                    logger.log(f"提供商配置写入失败（{e}）")
                    partial = True

        logger.log("预配置完成" + ("（部分成功）" if partial else ""))
        return 2 if partial else 0
    finally:
        logger.close()


if __name__ == "__main__":
    sys.exit(main())
