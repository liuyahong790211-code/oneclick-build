#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
开源改装铺 · 产品A：AnythingLLM 一键包 —— ModelScope GGUF 下载器

功能：
  从 ModelScope 直链下载 Qwen3-8B GGUF 模型，支持断点续传、失败重试、进度显示，
  下载完成后在目标目录生成 Ollama Modelfile（供 `ollama create` 使用）。

模型选择（2026-10-11 经 ModelScope HubApi.get_model / get_model_files 程序化核实）：
  仓库：Qwen/Qwen3-8B-GGUF（Qwen 官方组织仓库，非第三方搬运）
  文件清单（bytes，经 API 实测）：
    Qwen3-8B-Q4_K_M.gguf   5027783488  (4.68 GiB)  ← 默认，16GB 机器本地模式
    Qwen3-8B-Q5_0.gguf     5720761152
    Qwen3-8B-Q5_K_M.gguf   5851112224
    Qwen3-8B-Q6_K.gguf     6725899040
    Qwen3-8B-Q8_0.gguf     8709518112  (8.11 GiB)  ← --quant q8_0，24GB+ 机器可选
  许可：Qwen3 权重采用 Apache 2.0（见仓库 LICENSE，商用友好；构建时复核）。

直链 URL 模式（2026-10-11 curl 实测验证：返回 206 + Accept-Ranges: bytes）：
  https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}

硬约束：
  - 严禁调用 `ollama pull`（spike 已证实在受限网络下失败：registry blob 重定向到
    *.r2.cloudflarestorage.com 被客户端拒绝）。
  - 仅用 Python 标准库（urllib），内嵌 Python 无需 pip，直接可用。
  - 临时目录：--temp-dir（安装脚本传入 $INSTDIR\\temp），禁用系统 Temp/tmp。

退出码：0=成功（含续传完成），1=参数错误，2=下载失败（重试耗尽）。
"""

import argparse
import hashlib
import os
import sys
import time
import urllib.request
import urllib.error

# ---------------------------------------------------------------- 配置 ------
# 仓库与文件（来源见模块 docstring；改动此处需重新核实）
MODEL_REPO = "Qwen/Qwen3-8B-GGUF"
MODEL_REVISION = "master"
QUANT_FILES = {
    # 量化名 -> (文件名, 大小 bytes, 适用)
    "q4_k_m": ("Qwen3-8B-Q4_K_M.gguf", 5027783488, "默认：16GB 内存本地模式"),
    "q8_0":   ("Qwen3-8B-Q8_0.gguf",   8709518112, "24GB+ 内存，可选更高精度"),
}
DEFAULT_QUANT = "q4_k_m"
OLLAMA_MODEL_NAME = "oneclick-qwen3-8b"  # ollama create 后的模型名（避免与官方 qwen3 重名）

CHUNK_SIZE = 1024 * 1024       # 1MB 分片写盘
MAX_RETRIES = 8                # 每次失败最多重试次数
RETRY_BASE_DELAY = 5           # 退避基数（秒），指数增长
CONNECT_TIMEOUT = 30
SPEED_WINDOW = 10              # 速度统计窗口（秒）


def build_url(repo: str, revision: str, filename: str) -> str:
    """ModelScope 文件直链（URL 模式 2026-10-11 curl 实测可用）。"""
    return (
        f"https://modelscope.cn/api/v1/models/{repo}/repo"
        f"?Revision={revision}&FilePath={filename}"
    )


def parse_args(argv=None):
    p = argparse.ArgumentParser(description="ModelScope GGUF 下载器（断点续传）")
    p.add_argument("--dest-dir", required=True, help="模型下载目标目录")
    p.add_argument("--temp-dir", default=None,
                   help="临时目录（.part 文件所在；默认 dest-dir；禁用系统 tmp）")
    p.add_argument("--quant", default=DEFAULT_QUANT, choices=list(QUANT_FILES),
                   help="量化版本（默认 q4_k_m）")
    p.add_argument("--log", default=None, help="日志文件路径（追加）")
    p.add_argument("--no-modelfile", action="store_true", help="跳过 Modelfile 生成")
    return p.parse_args(argv)


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


def format_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.2f}{unit}" if unit != "B" else f"{int(n)}B"
        n /= 1024


def download_with_resume(url, dest_path, expected_size, logger):
    """断点续传下载。返回 True=完成且大小校验通过。"""
    part_path = dest_path + ".part"
    done = os.path.getsize(part_path) if os.path.exists(part_path) else 0
    if done > expected_size:
        logger.log(f"残留分片异常（{format_size(done)} > 预期），删除重下")
        os.remove(part_path)
        done = 0
    if done == expected_size:
        logger.log("分片已完整，直接校验通过")
        os.replace(part_path, dest_path)
        return True

    attempt = 0
    server_ignores_range = False  # 命中一次 "Range 请求返回 200" 后，后续重试不再发 Range
    while attempt <= MAX_RETRIES:
        try:
            req = urllib.request.Request(url)
            if done > 0 and not server_ignores_range:
                req.add_header("Range", f"bytes={done}-")
                logger.log(f"续传：从 {format_size(done)} 继续")
            # 伪装常用 UA，避免被 WAF 拦截（ModelScope 对空 UA 偶发 403）
            req.add_header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT) as resp:
                status = resp.status
                if status == 206:
                    content_range = resp.headers.get("Content-Range", "")
                    # 形如 bytes 1234-5678/5027783488，取总量做一致性校验
                    total = int(content_range.split("/")[-1]) if "/" in content_range else expected_size
                    if total != expected_size:
                        raise RuntimeError(f"远端文件大小变化：{total} != {expected_size}")
                elif status == 200 and done > 0:
                    # 200 = 服务器返回完整表示（可能忽略了 Range，也可能是重试时
                    # 的普通 GET）。无论哪种，都必须清空已有分片从头写，否则
                    # "ab" 追加会把新旧数据拼在一起导致大小校验永远失败。
                    if not server_ignores_range:
                        server_ignores_range = True
                        logger.log("服务器忽略 Range（返回 200），后续重试不再带 Range")
                    logger.log("清空已有分片，从头下载")
                    done = 0
                    if os.path.exists(part_path):
                        os.remove(part_path)
                elif status not in (200, 206):
                    raise RuntimeError(f"HTTP {status}")

                mode = "ab" if done > 0 else "wb"
                downloaded = done
                start_t = time.time()
                win_start_t, win_start_n = start_t, downloaded
                last_print = 0
                with open(part_path, mode) as fh:
                    while True:
                        chunk = resp.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        now = time.time()
                        # 每 2 秒或完成时打印一次进度
                        if now - last_print >= 2 or downloaded == expected_size:
                            last_print = now
                            if now - win_start_t >= 1:
                                speed = (downloaded - win_start_n) / (now - win_start_t)
                            else:
                                speed = 0
                            pct = downloaded / expected_size * 100
                            eta = (expected_size - downloaded) / speed if speed > 0 else -1
                            eta_s = f"{int(eta // 60)}分{int(eta % 60)}秒" if eta >= 0 else "--"
                            logger.log(
                                f"下载中：{pct:5.1f}% "
                                f"({format_size(downloaded)}/{format_size(expected_size)}) "
                                f"{format_size(speed)}/s 剩余约{eta_s}"
                            )
                            win_start_t, win_start_n = now, downloaded

            if downloaded == expected_size:
                os.replace(part_path, dest_path)
                logger.log(f"下载完成：{dest_path}")
                return True
            # 服务器提前断流：视为可重试
            raise RuntimeError(f"下载不完整：{format_size(downloaded)}/{format_size(expected_size)}")

        except (urllib.error.URLError, ConnectionError, TimeoutError,
                RuntimeError, OSError) as e:
            attempt += 1
            done = os.path.getsize(part_path) if os.path.exists(part_path) else 0
            if attempt > MAX_RETRIES:
                logger.log(f"重试 {MAX_RETRIES} 次仍失败，放弃：{e}")
                return False
            delay = RETRY_BASE_DELAY * (2 ** (attempt - 1))
            logger.log(f"下载中断（{e}），{delay} 秒后重试（{attempt}/{MAX_RETRIES}），已保留 {format_size(done)}")
            time.sleep(delay)
    return False


def write_modelfile(model_dir, gguf_filename, logger):
    """生成 Ollama Modelfile（语法来源：https://github.com/ollama/ollama/blob/main/docs/modelfile.md）。

    保守写法：只设 FROM + temperature/num_ctx，不覆写 TEMPLATE（沿用 llama.cpp
    GGUF 内嵌模板，避免破坏 qwen3 的 chat template）。
    """
    content = (
        f'FROM ./{gguf_filename}\n'
        f'PARAMETER temperature 0.7\n'
        f'PARAMETER num_ctx 8192\n'
    )
    path = os.path.join(model_dir, "Modelfile")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    logger.log(f"已生成 Modelfile：{path}")
    return path


def main(argv=None):
    args = parse_args(argv)
    logger = Logger(args.log)
    try:
        filename, expected_size, note = QUANT_FILES[args.quant]
        dest_dir = os.path.abspath(args.dest_dir)
        os.makedirs(dest_dir, exist_ok=True)
        temp_dir = os.path.abspath(args.temp_dir) if args.temp_dir else dest_dir
        os.makedirs(temp_dir, exist_ok=True)

        dest_path = os.path.join(dest_dir, filename)
        url = build_url(MODEL_REPO, MODEL_REVISION, filename)

        logger.log(f"模型：{MODEL_REPO} / {filename}（{format_size(expected_size)}，{note}）")
        logger.log(f"目标：{dest_path}")
        logger.log(f"临时目录：{temp_dir}（系统 tmp 已禁用）")

        # 已存在且大小正确 -> 跳过
        if os.path.exists(dest_path) and os.path.getsize(dest_path) == expected_size:
            logger.log("模型文件已存在且大小正确，跳过下载")
        else:
            # 先下载到 temp_dir（.part 续传文件也在此），完成后搬到 dest_dir
            temp_final = os.path.join(temp_dir, filename)
            ok = download_with_resume(url, temp_final, expected_size, logger)
            if not ok:
                return 2
            if os.path.abspath(temp_dir) != os.path.abspath(dest_dir):
                os.replace(temp_final, dest_path)
                logger.log(f"已移动到目标目录：{dest_path}")

        if not args.no_modelfile:
            write_modelfile(dest_dir, filename, logger)

        logger.log("全部完成")
        return 0
    finally:
        logger.close()


if __name__ == "__main__":
    sys.exit(main())
