# 开源改装铺 · 双首发产品构建仓库（公开）

> 本文件是未来公开构建仓库的 `README.md`。仓库归属待 Yahong 定——
> 定下来后，把本 `build/` 目录下的 `.github/` 搬到仓库根目录的 `.github/`，
> 再把两个产品目录放进去，即可跑构建。**不要把本机 `~/workspace/afdian-products/` 整个推上去。**

## 这个仓库里放什么

| 放 | 说明 |
|---|---|
| `product-a-anythingllm/` | 产品 A 的全部**构建脚本与配置**：`installer/*.nsi`、`downloader/`、`preconfig/`、`launcher/`、`docs/`（含 `BUILD-NOTES.md`、`LICENSE.txt`） |
| `product-b-photo-restore/` | 产品 B 的全部**构建脚本与配置**：`installer/*.nsi`、`core/`、`gui/`、`tests/`、`docs/` |
| `.github/workflows/build-windows.yml` | GitHub Actions 构建流水线（`windows-latest`，手动触发） |
| `shared/`、`listing/` | 用户手册、商品页文案、封面图（对外可见的文档资产） |

## 这个仓库里**不放**什么

| 不放 | 去哪了 |
|---|---|
| 大模型权重（Qwen3-8B GGUF 约 5GB） | 安装时从 ModelScope 下载（断点续传），不进仓库不进安装包 |
| 第三方二进制（OllamaSetup.exe 约 1.5GB、AnythingLLMDesktop.exe 约 410MB、FFmpeg、Python） | 构建时由工作流自动下载到 `vendor/bin/` / `staging/`，不提交 |
| 商业文案定价策略以外的内部讨论、API Key、任何账号密码 | 永远不进公开仓库 |
| 爱发电商品页的最终售卖文案（如含未公开信息） | 上架时由 Yahong 在爱发电后台填写 |

## 构建产物去哪了

每次在 Actions 页面点 "Run workflow"，`windows-latest` runner 会：
1. 下载第三方二进制 → 2. `makensis` 编译两个 NSIS 脚本 → 3. 把成品安装包存为 **Artifacts**（保留 90 天）。
   - `AnythingLLM-OneClick-Setup`（产品 A）
   - `PhotoRestore-OneClick-Setup`（产品 B）
4. （可选）勾选 `draft_release` 会再建一个 **draft Release**（草稿，不公开发布），安装包挂在下面。

## Yahong 在网页上下载安装包（三步，不需要懂构建）

1. 打开仓库页面 → 点顶部 **Actions** 标签 → 点左侧 **Build Windows installers**。
2. 点某一次运行记录（绿色 ✅ 的那次）→ 滚到页面底部 **Artifacts** → 点
   `AnythingLLM-OneClick-Setup` 或 `PhotoRestore-OneClick-Setup` 下载 zip。
3. 解压 zip 就得到 `.exe` 安装包，发给买家或自己先装一遍试用。

> 构建失败（红色 ❌）时：点那次运行 → 点失败的 job → 展开红色步骤看最后几行报错，
> 截图发给爱妃即可，不用自己修。

## 常见问题

- **为什么 release 里是 draft？** 正式发布前需要先跑完联调清单
  （`build/INTEGRATION-TEST.md`）；draft 只是暂存，不会公开。
- **安装包有多大？** 产品 A 约 2GB（含 Ollama/AnythingLLM 本体），产品 B 约 400–600MB
  （含 torch CPU、64MB 权重、FFmpeg）。都在 GitHub 单文件 2GB 上限内。
- **SmartScreen 警告？** 无代码签名证书（零支出），首次运行会出现
  "Windows 已保护你的电脑" → 点"更多信息" → "仍要运行"。手册里有截图教程。
