<div align="center">

# 📈 股票智能分析系统

[![GitHub stars](https://img.shields.io/github/stars/ZhuLinsen/daily_stock_analysis?style=social)](https://github.com/ZhuLinsen/daily_stock_analysis/stargazers)
[![CI](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml/badge.svg)](https://github.com/ZhuLinsen/daily_stock_analysis/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![GitHub Actions](https://img.shields.io/badge/GitHub%20Actions-Ready-2088FF?logo=github-actions&logoColor=white)](https://github.com/features/actions)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/)

<p align="center">
  <img src="https://trendshift.io/api/badge/trendshift/repositories/18527/daily?language=Python" alt="#1 Python Repository Of The Day | Trendshift" width="250" height="55"/>&nbsp;<a href="https://hellogithub.com/repository/ZhuLinsen/daily_stock_analysis" target="_blank"><img src="https://api.hellogithub.com/v1/widgets/recommend.svg?rid=6daa16e405ce46ed97b4a57706aeb29f&claim_uid=pfiJMqhR9uvDGlT&theme=neutral" alt="Featured｜HelloGitHub" width="230" /></a>
</p>

> 🤖 基于 AI 大模型的 A股/港股/美股/日股/韩股/台股自选股智能分析系统，每日自动分析并推送「决策仪表盘」到企业微信/飞书/Telegram/Discord/Slack/邮箱

[**产品预览**](#-产品预览) · [**功能特性**](#-功能特性) · [**快速开始**](#-快速开始) · [**推送效果**](#-推送效果) · [**文档中心**](docs/INDEX.md) · [**完整指南**](docs/full-guide.md)

简体中文 | [English](docs/README_EN.md) | [繁體中文](docs/README_CHT.md)

</div>

## 💖 赞助商 (Sponsors)
<div align="center">
  <p align="center">
    <a href="https://open.anspire.cn/?share_code=QFBC0FYC" target="_blank"><img src="./docs/assets/anspire.png" alt="Anspire Open 一站式模型和搜索服务" width="300" height="141" style="width: 300px; height: 141px; object-fit: contain;"></a>
    <a href="https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis" target="_blank"><img src="./docs/assets/serpapi_banner_zh.png" alt="轻松抓取搜索引擎上的实时金融新闻数据 - SerpApi" width="300" height="141" style="width: 300px; height: 141px; object-fit: contain;"></a>
  </p>
</div>


## 🖥️ 产品预览

<p align="center">
  <img src="docs/assets/readme_workspace_tour_20260510.gif" alt="DSA Web 工作台演示" width="720">
</p>

## ✨ 功能特性

| 能力 | 覆盖内容 |
|------|------|
| AI 决策报告 | 核心结论、评分、趋势、买卖点位、风险警报、催化因素、操作检查清单 |
| 多市场数据聚合 | 覆盖 A股、港股、美股、日股、韩股、台股和 ETF，支持行情、K 线、技术指标、新闻、公告、基本面与报告辅助数据；不同市场的数据源和能力边界见 [市场支持边界](docs/market-support.md) |
| Web / 桌面工作台 | 手动分析、任务进度、历史报告、完整 Markdown、回测、持仓、配置管理、浅色 / 深色主题 |
| Agent 策略问股 | 多轮追问，支持均线、缠论、波浪、趋势、热点、事件、成长、预期等 15 种内置策略，覆盖 Web/Bot/API |
| 智能导入与补全 | 图片、CSV/Excel、剪贴板导入；股票代码/名称/拼音/别名补全 |
| 自动化与推送 | GitHub Actions、Docker、本地定时任务、FastAPI 服务和企业微信/飞书/Telegram/Discord/Slack/邮件推送 |

> 历史报告详情会优先展示 AI 返回的原始「狙击点位」文本，避免区间价、条件说明等复杂内容在历史回看时被压缩成单个数字。

> 回测页现支持“次日验证 / 1 日窗口”视图，可按股票代码和分析日期范围查看当时 AI 预测、次日实际涨跌以及区间准确率；该视图基于历史分析记录与 1 日回测结果，不代表真实成交流水。

> Web 工作台已完成一轮 UI 升级：新增完整浅色主题，并支持浅色 / 深色主题一键切换；首页、问股、回测、持仓、设置页改为共享同一套设计 token、输入表面、状态反馈和抽屉/滚动语义，日常使用与移动端浏览更连贯。

> 问股页进一步补强了消息复制、会话导出、通知发送、历史删除和追问上下文保护；首页与报告抽屉的复制反馈、空态和错误态也已按面板独立收口，减少误触和状态串扰。

> Web 管理认证支持运行时开关；如果系统中已保留管理员密码，重新开启认证时必须提供当前密码，避免在认证关闭窗口内直接获取新的管理员会话。
> 多进程/多 worker 部署时，认证开关仅在当前进程即时生效；需重启或滚动重启全部 worker 以统一状态。

> 持仓管理补充说明：卖出录入现在会在写入前校验可用持仓，超售会直接拒绝；如果历史里误录了交易 / 资金流水 / 公司行为，可在 Web `/portfolio` 页的事件列表中直接删除后恢复快照。高并发写入场景下，直接持仓写接口可能返回 `409 portfolio_busy`，提示账本正在处理另一笔变更；CSV 导入仍保持逐条提交与部分成功语义。

### 技术栈与数据来源

| 类型 | 支持 |
|------|------|
| AI 模型 | [Anspire](https://open.anspire.cn/?share_code=QFBC0FYC)、[AIHubMix](https://aihubmix.com/?aff=CfMq)、Gemini、OpenAI 兼容、DeepSeek、通义千问、Claude、Ollama 本地模型等 |
| 行情数据 | [TickFlow](https://tickflow.org/auth/register?ref=WDSGSPS5XC)、AkShare、Tushare、Pytdx、Baostock、YFinance、Longbridge |
| 新闻搜索 | [Anspire](https://open.anspire.cn/?share_code=QFBC0FYC)、[SerpAPI](https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis)、[Tavily](https://tavily.com/)、[Bocha](https://open.bocha.cn/)、[Brave](https://brave.com/search/api/)、[MiniMax](https://platform.minimaxi.com/)、SearXNG |
| 社交舆情 | [Stock Sentiment API](https://api.adanos.org/docs)（Reddit / X / Polymarket，仅美股，可选） |

> **长桥优先策略（仅美/港股）**：在配置 `LONGBRIDGE_APP_KEY` / `LONGBRIDGE_APP_SECRET` / `LONGBRIDGE_ACCESS_TOKEN` 的前提下，美股与港股的 **日线 K 线** 与 **实时行情** 由 **Longbridge 优先拉取**；若长桥失败或部分字段缺失，再由 **YFinance（美股）/ AkShare（港股）** 兜底或合并补全字段。**未配置长桥凭据时不会调用 Longbridge**，美股/港股仍以 YFinance / AkShare 为主数据源（与未集成长桥前的行为一致）。**美股大盘指数**（如 SPX）始终以 YFinance 优先（长桥不提供指数行情）。**A 股**路由不变，仍为 Efinance → AkShare → Tushare → Pytdx → Baostock。详见 `.env.example` 与 [完整指南](docs/full-guide.md) 中长桥说明。

> **tushare增加港股查询能力**：在配置`TUSHARE_TOKEN` 的前提下，并且具有港股日线查询权限时，用户在首页输入港股代码后提交查询能够得到正常的分析结果。如果用户不具有港股查询权限时，和未改动前一样会得到错误的信息。

### 内置交易纪律

| 规则 | 说明 |
|------|------|
| 严禁追高 | 乖离率超阈值（默认 5%，可配置）自动提示风险；强势趋势股自动放宽 |
| 趋势交易 | MA5 > MA10 > MA20 多头排列 |
| 精确点位 | 买入价、止损价、目标价 |
| 检查清单 | 每项条件以「满足 / 注意 / 不满足」标记 |
| 新闻时效 | 可配置新闻最大时效（默认 3 天），避免使用过时信息 |

## 🚀 快速开始

### 方式一：[GitHub Actions（推荐）](https://www.bilibili.com/video/BV11FEb66EXG/)

> 5 分钟完成部署，零成本，无需服务器。


#### 1. Fork 本仓库

点击右上角 `Fork` 按钮（顺便点个 Star⭐ 支持一下）

#### 2. 配置 Secrets

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

**AI 模型配置（至少配置一个）**

> 详细配置说明见 [LLM 配置指南](docs/LLM_CONFIG_GUIDE.md)（极简接入、渠道模式、高级 YAML 路由、Vision、Agent、排错）。默认推荐先选服务商并填写 API Key；需要多模型时再启用渠道模式；只有高级用户才需要 YAML 路由配置。

> 现在推荐把多模型配置统一写成 `LLM_CHANNELS + LLM_<NAME>_PROTOCOL/BASE_URL/API_KEY/MODELS/ENABLED`。如需显式指定主模型或备选模型，再额外配置 `LITELLM_MODEL` / `LITELLM_FALLBACK_MODELS`。Web 设置页和 `.env` 使用同一套字段，便于相互切换。

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `ANSPIRE_API_KEYS` | [Anspire](https://open.anspire.cn/?share_code=QFBC0FYC) API Key，一Key同时启用全球热门大模型和联网搜索，本项目新用户提供35元等额的免费额度（GLM5.2、GPT等模型特惠中） | **推荐** |
| `AIHUBMIX_KEY` | [AIHubMix](https://aihubmix.com/?aff=CfMq) API Key，一Key切换使用全系模型，无需科学上网，本项目可享 10% 优惠 | **推荐** |
| `GEMINI_API_KEY` | Google Gemini API Key | 可选 |
| `ANTHROPIC_API_KEY` | Anthropic Claude API Key | 可选 |
| `OPENAI_API_KEY` | OpenAI 兼容 API Key（支持 DeepSeek、通义千问等） | 可选 |
| `OPENAI_BASE_URL` | OpenAI 兼容 API 地址（如 `https://api.deepseek.com/v1`） | 可选 |
| `OPENAI_MODEL` | 模型名称（如 `gemini-3.1-pro-preview`、`gemini-3-flash-preview`、`gpt-5.2`） | 可选 |
| `OPENAI_VISION_MODEL` | 图片识别专用模型（部分第三方模型不支持图像；不填则用 `OPENAI_MODEL`） | 可选 |
| `OLLAMA_API_BASE` | Ollama 本地服务地址（如 `http://localhost:11434`），本地/Docker 部署时使用，**不要**用 `OPENAI_BASE_URL` 配置 Ollama，详见 [LLM 配置指南 - Ollama](docs/LLM_CONFIG_GUIDE.md#示例-4使用-ollama-本地模型) | 可选 |

> 注：AI 优先级 Gemini > Anthropic > OpenAI（含 AIHubmix）> Ollama，至少配置一个。`AIHUBMIX_KEY` 无需配置 `OPENAI_BASE_URL`，系统自动适配。图片识别需 Vision 能力模型。DeepSeek 思考模式（deepseek-reasoner、deepseek-r1、qwq、deepseek-chat）按模型名自动识别，无需额外配置。**Ollama 本地模型**（无需 API Key）必须使用 `OLLAMA_API_BASE`，误用 `OPENAI_BASE_URL` 会导致 404。

<details>
<summary><b>通知渠道配置</b>（点击展开，至少配置一个）</summary>


| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `WECHAT_WEBHOOK_URL` | 企业微信 Webhook URL | 可选 |
| `FEISHU_WEBHOOK_URL` | 飞书 Webhook URL | 可选 |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token（@BotFather 获取） | 可选 |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | 可选 |
| `TELEGRAM_MESSAGE_THREAD_ID` | Telegram Topic ID (用于发送到子话题) | 可选 |
| `DISCORD_WEBHOOK_URL` | Discord Webhook URL | 可选 |
| `DISCORD_BOT_TOKEN` | Discord Bot Token（与 Webhook 二选一） | 可选 |
| `DISCORD_MAIN_CHANNEL_ID` | Discord Channel ID（使用 Bot 时需要） | 可选 |
| `DISCORD_INTERACTIONS_PUBLIC_KEY` | Discord Public Key（仅接收入站 Interaction/Webhook 回调并进行签名校验时需要） | 可选 |
| `SLACK_BOT_TOKEN` | Slack Bot Token（推荐，支持图片上传；同时配置时优先于 Webhook） | 可选 |
| `SLACK_CHANNEL_ID` | Slack Channel ID（使用 Bot 时需要） | 可选 |
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook URL（仅文本，不支持图片） | 可选 |
| `EMAIL_SENDER` | 发件人邮箱（如 `xxx@qq.com`） | 可选 |
| `EMAIL_PASSWORD` | 邮箱授权码（非登录密码） | 可选 |
| `EMAIL_RECEIVERS` | 收件人邮箱（多个用逗号分隔，留空则发给自己） | 可选 |
| `EMAIL_SENDER_NAME` | 邮件发件人显示名称（默认：daily_stock_analysis股票分析助手，支持中文并自动编码邮件头） | 可选 |
| `STOCK_GROUP_N` / `EMAIL_GROUP_N` | 股票分组发往不同邮箱（如 `STOCK_GROUP_1=600519,300750` `EMAIL_GROUP_1=user1@example.com`） | 可选 |
| `PUSHPLUS_TOKEN` | PushPlus Token（[获取地址](https://www.pushplus.plus)，国内推送服务） | 可选 |
| `PUSHPLUS_TOPIC` | PushPlus 群组编码（一对多推送，配置后消息推送给群组所有订阅用户） | 可选 |
| `SERVERCHAN3_SENDKEY` | Server酱³ Sendkey（[获取地址](https://sc3.ft07.com/)，手机APP推送服务） | 可选 |
| `CUSTOM_WEBHOOK_URLS` | 自定义 Webhook（支持钉钉等，多个用逗号分隔） | 可选 |
| `CUSTOM_WEBHOOK_BEARER_TOKEN` | 自定义 Webhook 的 Bearer Token（用于需要认证的 Webhook） | 可选 |
| `WEBHOOK_VERIFY_SSL` | Webhook HTTPS 证书校验（默认 true）。设为 false 可支持自签名证书。警告：关闭有严重安全风险，仅限可信内网 | 可选 |
| `SCHEDULE_RUN_IMMEDIATELY` | 定时模式启动时是否立即执行一次分析 | 可选 |
| `RUN_IMMEDIATELY` | 非定时模式启动时是否立即执行一次分析 | 可选 |
| `SINGLE_STOCK_NOTIFY` | 单股推送模式：设为 `true` 则每分析完一只股票立即推送 | 可选 |
| `REPORT_TYPE` | 报告类型：`simple`(精简)、`full`(完整)、`brief`(3-5句概括)，Docker环境推荐设为 `full` | 可选 |
| `REPORT_LANGUAGE` | 报告输出语言：`zh`(默认中文) / `en`(英文)；会同步影响 Prompt、Markdown 模板、通知 fallback 与 Web 报告页固定文案。仓库自带 `daily_analysis.yml` 已显式映射该变量，直接在 Actions Secrets/Variables 中配置即可生效 | 可选 |
| `REPORT_SUMMARY_ONLY` | 仅分析结果摘要：设为 `true` 时只推送汇总，不含个股详情 | 可选 |
| `REPORT_TEMPLATES_DIR` | Jinja2 模板目录（相对项目根，默认 `templates`） | 可选 |
| `REPORT_RENDERER_ENABLED` | 启用 Jinja2 模板渲染（默认 `false`，保证零回归） | 可选 |
| `REPORT_INTEGRITY_ENABLED` | 启用报告完整性校验，缺失必填字段时重试或占位补全（默认 `true`） | 可选 |
| `REPORT_INTEGRITY_RETRY` | 完整性校验重试次数（默认 `1`，`0` 表示仅占位不重试） | 可选 |
| `REPORT_HISTORY_COMPARE_N` | 历史信号对比条数，`0` 关闭（默认），`>0` 启用 | 可选 |
| `ANALYSIS_DELAY` | 个股分析和大盘分析之间的延迟（秒），避免API限流，如 `10` | 可选 |
| `MAX_WORKERS` | 异步分析任务队列并发线程数（默认 `3`）；保存后队列空闲时自动应用，繁忙时延后生效 | 可选 |
| `MERGE_EMAIL_NOTIFICATION` | 个股与大盘复盘合并推送（默认 false），减少邮件数量 | 可选 |
| `MARKDOWN_TO_IMAGE_CHANNELS` | 将 Markdown 转为图片发送的渠道（逗号分隔）：`telegram,wechat,custom,email,slack` | 可选 |
| `MARKDOWN_TO_IMAGE_MAX_CHARS` | 超过此长度不转图片，避免超大图片（默认 `15000`） | 可选 |
| `MD2IMG_ENGINE` | 转图引擎：`wkhtmltoimage`（默认）或 `markdown-to-file`（emoji 更好） | 可选 |

> 至少配置一个渠道，配置多个则同时推送。图片发送与引擎安装细节请参考 [完整指南](docs/full-guide.md)

</details>

**其他配置**

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `STOCK_LIST` | 自选股代码，如 `600519,hk00700,AAPL,7203.T,005930.KS,2330.TW` | ✅ |

**新闻源配置（推荐）**

新闻源会显著影响舆情、公告、事件和催化因素质量，建议至少配置一个搜索服务。

| Secret 名称 | 说明 | 必填 |
|------------|------|:----:|
| `ANSPIRE_API_KEYS` | [Anspire AI Search](https://aisearch.anspire.cn/)：汇聚全球舆情信息，适配A股、美股、港股等新闻和舆情检索；同一Key可复用大模型服务，本项目新用户提供免费35元等额的免费点数 | **推荐** |
| `SERPAPI_API_KEYS` | [SerpAPI](https://serpapi.com/baidu-search-api?utm_source=github_daily_stock_analysis)：搜索引擎结果补强，适合实时金融新闻 | **推荐** |
| `TAVILY_API_KEYS` | [Tavily](https://tavily.com/)：通用新闻搜索 API | 可选 |
| `BOCHA_API_KEYS` | [博查搜索](https://open.bocha.cn/)：中文搜索优化，支持 AI 摘要 | 可选 |
| `BRAVE_API_KEYS` | [Brave Search](https://brave.com/search/api/)：隐私优先，美股资讯补强 | 可选 |
| `MINIMAX_API_KEYS` | [MiniMax](https://platform.minimaxi.com/)：结构化搜索结果 | 可选 |
| `SEARXNG_BASE_URLS` | SearXNG 自建实例：无配额兜底，适合私有部署 | 可选 |

更多搜索源、社交舆情和降级规则见 [搜索服务配置](docs/full-guide.md#搜索服务配置)。

**行情数据源配置（可选）**

> 默认使用 AkShare、Baostock、YFinance 等免费数据源，日志中"未配置"的提示不影响运行。
> 如需更稳定的行情，可按市场配置以下 Secret：

| Secret 名称 | 适用市场 | 说明 |
|------------|:--------:|------|
| `TUSHARE_TOKEN` | A 股 | 提升历史行情稳定性 |
| `LONGBRIDGE_OAUTH_CLIENT_ID` + `LONGBRIDGE_OAUTH_TOKEN_CACHE_B64` | 港股/美股 | 补齐量比、换手率、PE 等字段 |

> 详见 [数据源配置](docs/full-guide.md#数据源配置)。

#### 3. 启用 Actions

`Actions` 标签 → `I understand my workflows, go ahead and enable them`

#### 4. 手动测试

`Actions` → `每日股票分析` → `Run workflow` → `Run workflow`

#### 完成

默认每个**工作日 18:00（北京时间）**自动执行，也可手动触发。默认非交易日（含 A/H/US 节假日）不执行。

> 💡 **关于跳过交易日检查的两种机制：**
> | 机制 | 配置方式 | 生效范围 | 适用场景 |
> |------|----------|----------|----------|
> | `TRADING_DAY_CHECK_ENABLED=false` | 环境变量/Secrets | 全局、长期有效 | 测试环境、长期关闭检查 |
> | `force_run` (UI 勾选) | Actions 手动触发时选择 | 单次运行 | 临时在非交易日执行一次 |
>
> - **环境变量方式**：在 `.env` 或 GitHub Secrets 中设置，影响所有运行方式（定时触发、手动触发、本地运行）
> - **UI 勾选方式**：仅在 GitHub Actions 手动触发时可见，不影响定时任务，适合临时需求
> - **断点续传与 `--dry-run` 的数据存在性判断**：会按股票所属市场的本地时区和交易日历解析“最新可复用交易日”；周末/节假日复用最近交易日，交易日盘中复用上一已完成交易日，盘后若当日数据已落库则可直接跳过。详细规则见 [完整指南](docs/full-guide.md)。

### 方式二：[客户端配置教程](https://www.bilibili.com/video/BV11FEb66Eyr/) / 本地运行 / Docker 部署

```bash
# 克隆项目
git clone https://github.com/ZhuLinsen/daily_stock_analysis.git && cd daily_stock_analysis

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env && vim .env

# 运行分析
python main.py
```

如果你不用 Web，推荐直接在 `.env` 里按条写模型渠道：

```bash
python main.py --debug
python main.py --dry-run
python main.py --stocks 600519,hk00700,AAPL,2330.TW
python main.py --market-review
python main.py --schedule
python main.py --serve-only
```

保存后也可以在 Web 设置页继续编辑同一组字段；不会要求额外配置文件。

如果同时启用了高级模型路由 YAML（`LITELLM_CONFIG`），YAML 主要用于定义可用模型和路由规则（`model_list`）；运行时主模型 / 备选模型 / Vision / Temperature 仍由 `LITELLM_MODEL`、`LITELLM_FALLBACK_MODELS`、`VISION_MODEL`、`LLM_TEMPERATURE` 等字段决定。渠道编辑器只保存渠道条目，不会覆盖这些运行时字段的选择。

> Docker 部署、定时任务配置请参考 [完整指南](docs/full-guide.md)
> 桌面客户端打包请参考 [桌面端打包说明](docs/desktop-package.md)

## 📱 推送效果

### 决策仪表盘
```
🎯 2026-02-08 决策仪表盘
共分析3只股票 | 🟢买入:0 🟡观望:2 🔴卖出:1

📊 分析结果摘要
⚪ 中钨高新(000657): 观望 | 评分 65 | 看多
⚪ 永鼎股份(600105): 观望 | 评分 48 | 震荡
🟡 新莱应材(300260): 卖出 | 评分 35 | 看空

⚪ 中钨高新 (000657)
📰 重要信息速览
💭 舆情情绪: 市场关注其AI属性与业绩高增长，情绪偏积极，但需消化短期获利盘和主力流出压力。
📊 业绩预期: 基于舆情信息，公司2025年前三季度业绩同比大幅增长，基本面强劲，为股价提供支撑。

🚨 风险警报:

风险点1：2月5日主力资金大幅净卖出3.63亿元，需警惕短期抛压。
风险点2：筹码集中度高达35.15%，表明筹码分散，拉升阻力可能较大。
风险点3：舆情中提及公司历史违规记录及重组相关风险提示，需保持关注。
✨ 利好催化:

利好1：公司被市场定位为AI服务器HDI核心供应商，受益于AI产业发展。
利好2：2025年前三季度扣非净利润同比暴涨407.52%，业绩表现强劲。
📢 最新动态: 【最新消息】舆情显示公司是AI PCB微钻领域龙头，深度绑定全球头部PCB/载板厂。2月5日主力资金净卖出3.63亿元，需关注后续资金流向。

---
生成时间: 18:00
```

### 大盘复盘
```
🎯 2026-01-10 大盘复盘

📊 主要指数
- 上证指数: 3250.12 (🟢+0.85%)
- 深证成指: 10521.36 (🟢+1.02%)
- 创业板指: 2156.78 (🟢+1.35%)

📈 市场概况
上涨: 3920 | 下跌: 1349 | 涨停: 155 | 跌停: 3

🔥 板块表现
领涨: 互联网服务、文化传媒、小金属
领跌: 保险、航空机场、光伏设备
```
## ⚙️ 配置说明

> 📖 完整环境变量、定时任务配置请参考 [完整配置指南](docs/full-guide.md)
> 邮件通知当前基于 SMTP 授权码/基础认证；若 Outlook / Exchange 账号或租户强制 OAuth2，当前版本暂不支持。


## 🖥️ Web 界面

Web 工作台提供配置管理、任务监控、手动分析、历史报告、完整 Markdown 报告、Agent 问股、回测、持仓管理、智能导入和浅色 / 深色主题。启动方式：

**本轮界面升级亮点：**

- **全新浅色主题**：不再只是深色工作台，浅色模式针对输入框、卡片层级、边界对比、状态提示做了成套重绘。
- **主题切换可持久化**：侧边栏内可随时切换浅色 / 深色主题，页面刷新后仍保留用户选择。
- **核心页面统一收口**：首页、问股、回测、持仓、设置共用同一套视觉 token、状态原语和表面组件，减少“每页一个风格”的割裂感。
- **移动端与触屏体验增强**：抽屉遮罩、滚动契约、消息操作可达性和关键按钮语义同步补强，小屏设备上更稳定。

**可选密码保护**：在 `.env` 中设置 `ADMIN_AUTH_ENABLED=true` 可启用 Web 登录，首次访问在网页设置初始密码，保护 Settings 中的 API 密钥等敏感配置。系统设置现支持运行时开启或关闭认证；关闭认证不会删除已保存密码，后续可直接重新启用。认证开启时，`POST /api/v1/auth/logout` 也需要有效会话；如果会话已经过期，前端会直接回到登录页。详见 [完整指南](docs/full-guide.md)。

### 智能导入

- 支持均线金叉、缠论、波浪理论、多头趋势、热点题材、事件驱动、成长质量、预期重估等内置策略
- 支持实时行情、K 线、技术指标、新闻和风险信息调用
- 支持多轮追问、会话导出、发送到通知渠道和后台执行
- 支持自定义策略文件与多 Agent 编排（实验性）

1. **图片**：拖拽或选择自选股截图（如 APP 持仓页、行情列表），Vision AI 自动识别代码+名称，并给出置信度
2. **文件**：上传 CSV 或 Excel (.xlsx)，自动解析代码/名称列
3. **粘贴**：从 Excel 或表格复制后粘贴，点击「解析」即可

**预览与合并**：高置信度默认勾选，中/低置信度需手动勾选；支持按代码去重、清空、全选；仅合并已勾选且解析成功的项。

**配置与限制**：
- 图片需配置 Vision API（`GEMINI_API_KEY`、`ANTHROPIC_API_KEY` 或 `OPENAI_API_KEY` 至少一个）
- 图片：JPG/PNG/WebP/GIF，≤5MB；文件：≤2MB；粘贴文本：≤100KB

**API**：`POST /api/v1/stocks/extract-from-image`（图片）、`POST /api/v1/stocks/parse-import`（文件/粘贴）。详见 [完整指南](docs/full-guide.md)。

### 智能搜索补全 (MVP)

首页分析输入框已升级为”类搜索引擎”补全框，显著降低记忆负担：

- **多维匹配**：支持输入股票代码、中文名、拼音缩写或别名（如 `gzmt` -> 贵州茅台、`tencent` -> 腾讯控股、`aapl` -> Apple Inc.）。
- **多市场覆盖**：本地索引已覆盖 **A股、港股、美股** 三个市场；通过 Tushare 或 AkShare 数据源生成，支持按需更新索引。
- **自动降级逻辑**：
  - **新股/异常**：若索引未及时更新或加载失败，系统将无缝退回普通输入模式，确保分析链路 100% 可用。
  - **未命中**：搜索未命中时，用户直接按回车即可走原有手动输入流程，完全不影响分析。

> 💡 **索引更新提示**：如需更新索引数据，可通过 `scripts/fetch_tushare_stock_list.py` 获取最新股票列表，再运行 `scripts/generate_index_from_csv.py` 重新生成索引文件。详见 [Tushare 股票列表获取工具使用说明](docs/TUSHARE_STOCK_LIST_GUIDE.md)。

**LLM 用量查询**：`GET /api/v1/usage/summary?period=today|month|all`，返回按调用类型和模型分组的 token 消耗汇总（`total_calls`、`total_tokens`、`by_call_type`、`by_model`）。

**分析 API 说明**：`POST /api/v1/analysis/analyze` 在 `async_mode=false` 时仅支持单只股票；批量 `stock_codes` 需要 `async_mode=true`。异步 `202` 响应对单股返回 `task_id`，对批量返回 `accepted` / `duplicates` 汇总结构；空白股票代码会在服务端过滤，若过滤后为空则返回 `400`。未知 `/api` 路径（含 `/api` 本身）返回 JSON `404`，不再回退到前端页面。详见 [API 规范](docs/architecture/api_spec.json)。

### 历史报告详情

在首页历史记录中选择一条分析记录后，点击「完整分析报告」按钮可在右侧抽屉中查看与推送通知格式一致的完整 Markdown 分析报告，包含舆情情报、核心结论、数据透视、作战计划等完整内容。

报告头部提供两种复制方式：

- 「复制 Markdown 源码」：保留原始 Markdown 结构，适合二次编辑、技术社区分享和笔记归档。
- 「复制纯文本」：去除常见 Markdown 格式符号，适合微信、群聊等纯文本分享场景。

### 🤖 Agent 策略问股

在 `.env` 中设置 `AGENT_MODE=true` 后启动服务，访问 `/chat` 页面即可开始多轮策略问答。

> 对用户侧文案，本项目仍以“策略”为主称呼；代码、配置和 API 主字段统一使用 `skill`，可理解为“可复用的策略能力包”。

- **选择策略**：均线金叉、缠论、波浪理论、多头趋势、商品涨价传导等 15 种内置策略
- **自然语言提问**：如「用缠论分析 600519」，Agent 自动调用实时行情、K线、技术指标、新闻等工具
- **流式进度反馈**：实时展示 AI 思考路径（行情获取 → 技术分析 → 新闻搜索 → 生成结论）
- **多轮对话**：支持追问上下文，会话历史持久化保存
- **导出与发送**：可将会话导出为 `.md` 文件，或发送到已配置的通知渠道，并提供明确的成功/失败反馈
- **后台执行**：切换页面不中断分析，完成时 Dock 问股图标显示角标
- **Bot 命令**：`/ask` 技能分析（支持多股对比）、`/chat` 自由对话、`/history` 会话历史、`/strategies` 策略/技能列表、`/research` 深度研究
- **自定义策略（Skill）**：在 `strategies/` 目录下新建 YAML 文件或在自定义 skill 目录中放入 `SKILL.md` bundle，即可添加新的交易策略，无需写代码
- **多 Agent 架构**（实验性）：设置 `AGENT_ARCH=multi` 启用 Technical → Intel → Risk → Specialist → Decision 多 Agent 级联编排，通过 `AGENT_ORCHESTRATOR_MODE` 控制深度（quick/standard/full/specialist）。其中 `strategy` / `skill` 仍作为旧值兼容并会自动归一化到 `specialist`。超时或中间阶段 JSON 解析失败时，系统会优先保留已完成阶段结果并降级生成最小可用仪表盘，避免整份报告直接退回默认占位。详见 [完整配置指南](docs/full-guide.md)
- **Intel 增强字段兼容说明**：`capital_flow_signal` 为新增扩展字段（`inflow/outflow/neutral/not_available`），用于描述 A 股资金流方向，未返回时不会影响后续阶段；下游解析建议以 `risk_alerts` 与 `positive_catalysts` 为主，新增字段可忽略，兼容历史客户端。

> **注意**：配置了任意 AI API Key 后，Agent 对话功能自动可用，无需手动设置 `AGENT_MODE=true`。如需显式关闭可设置 `AGENT_MODE=false`。每次对话会产生 LLM API 调用费用。若你手动修改了 `.env` 中的主模型 / Agent 主模型 / 备选模型 / 模型渠道配置（如 `LITELLM_MODEL` / `AGENT_LITELLM_MODEL` / `LITELLM_FALLBACK_MODELS` / `LLM_CHANNELS`），需要重启服务或触发配置重载后，新进程才会按新模型生效。

### 启动方式

1. **启动服务**（默认会自动编译前端）
   ```bash
   python main.py --webui       # 启动 Web 界面 + 执行定时分析
   python main.py --webui-only  # 仅启动 Web 界面
   ```
   启动时会在 `apps/dsa-web` 自动执行 `npm install && npm run build`。
   如需关闭自动构建，设置 `WEBUI_AUTO_BUILD=false`，并改为手动执行：
   ```bash
   cd ./apps/dsa-web
   npm install && npm run build
   cd ../..
   ```

访问 `http://127.0.0.1:8000` 即可使用。

> 在云服务器上部署后，不知道浏览器该输入什么地址？请看 [云服务器 Web 界面访问指南](docs/deploy-webui-cloud.md)。

> 也可以使用 `python main.py --serve` (等效命令)

## 🧩 相关项目 (Related Projects)

> DSA 聚焦日常分析报告；下面两个同系列项目分别覆盖选股、策略验证与策略进化，适合按需延伸使用。它们当前独立维护，后续会优先探索与 DSA 的候选股导入、回测验证和报告联动。

| 项目 | 定位 |
|------|------|
| [AlphaSift](https://github.com/ZhuLinsen/alphasift) | 多因子选股与全市场扫描，用于从股票池中提取候选标的 |
| [AlphaEvo](https://github.com/ZhuLinsen/alphaevo) | 策略回测与自我进化，用于验证策略规则，并通过迭代探索策略参数与组合 |

## 📬 联系与合作

<table>
  <tr>
    <td width="92" valign="top"><strong>合作邮箱</strong></td>
    <td valign="top">
      <a href="mailto:zhuls345@gmail.com">zhuls345@gmail.com</a><br>
      项目咨询、部署支持与功能扩展
    </td>
    <td align="center" rowspan="3" valign="middle" width="148">
      <a href="http://xhslink.com/m/tU520DWCKT" target="_blank"><img src="./docs/assets/xiaohongshu_tick.jpg" width="112" alt="小红书二维码"></a><br>
      <sub>扫码关注小红书</sub>
    </td>
  </tr>
  <tr>
    <td width="92" valign="top"><strong>小红书</strong></td>
    <td valign="top"><a href="http://xhslink.com/m/tU520DWCKT">欢迎关注小红书</a></td>
  </tr>
  <tr>
    <td width="92" valign="top"><strong>问题反馈</strong></td>
    <td valign="top"><a href="https://github.com/ZhuLinsen/daily_stock_analysis/issues">提交 Issue</a></td>
  </tr>
</table>

## 📄 License
[MIT License](LICENSE) © 2026 ZhuLinsen

欢迎在二次开发或引用时注明本仓库来源，感谢支持项目持续维护。

## ⚠️ 免责声明

本项目仅供学习和研究使用，不构成任何投资建议。股市有风险，投资需谨慎。作者不对使用本项目产生的任何损失负责。

---
