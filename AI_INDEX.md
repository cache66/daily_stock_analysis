# AI_INDEX — 本工程"一页式"索引（给 AI 助手/OpenClaw 优先读这个）

> 目的：让 AI 用最小上下文了解本工程（这是什么、每天跑什么、有哪些策略/脚本/文档、去哪找产物）。
> 规则真源：`AGENTS.md`（与本文件冲突时以 AGENTS.md 为准）。本文件只做导航，不重复细节。

## 1. 项目一句话

股票智能分析系统（A 股/港股/美股）：抓数据 → 技术分析/新闻 → LLM 分析 → 报告 → 通知；本机另有一套"个人策略复盘"自动化（见 §3）。

## 2. 关键规则（AI 必读）

- 目录边界：后端 `src/ data_provider/ api/ bot/`；Web `apps/dsa-web/`；桌面 `apps/dsa-desktop/`；部署 `scripts/ .github/workflows/ docker/`
- 未经用户确认：不 `git commit/tag/push`
- 改动策略/工具要留痕：`docs/个人策略文档/个人策略目录.md` + `个人策略修改记录.md` + `docs/CHANGELOG.md`（`[Unreleased]` 扁平格式 `- [新功能|改进|修复|文档|测试|chore] 描述`）
- 不复权缓存 `data/cache/history/cn/*.csv`（date,open,high,low,close,volume,amount,pct_chg）；`data/stock_analysis.db` 的 `stock_daily` 是前复权"评估真源"
- 涨停判定口径：主板 9.65% / 创业板 19.65%（收盘涨幅近似）

## 3. 每日自动链路（本机 cron，工作日）

| 时间 | 脚本 | 作用 | 日志 |
| --- | --- | --- | --- |
| 18:10 | `data/runtime/screening_daily.sh` | 策略筛选 Top-N 采集 | `data/run_logs/` |
| 18:40 | `data/runtime/warm_daily.sh` | 缓存补数（Tushare 按日全市场）+ 预热 | `warm_daily.log` |
| 19:00 | `data/runtime/structure_scan_daily.sh` | 结构扫描 + 000300 补数 + 个股五段式快照 | `structure_scan_daily.log` |
| 19:05 | `data/runtime/matrix_daily.sh` | 矩阵复盘 + stock_daily 同步 + 红利T + 深跌企稳 + 复盘导出 + 计分板 + **作战计划（AI 审核）** | `matrix_daily.log` |

- `matrix_daily.sh` 用 `flock` 防重叠；`rebuild_stock_daily_qfq` 步骤有 `timeout 900` 保护
- 手动补数：`./.venv-linux/bin/python scripts/backfill_index_daily.py --codes 000300`
- AI 用量：`data/stock_analysis.db` 表 `llm_usage`（字段 `called_at/model/total_tokens`），每天 AI 审核约 19:35-19:51 集中发生

## 4. 策略清单（状态 + 一句话 + 文档）

| 策略 | 状态 | 一句话 | 文档 |
| --- | --- | --- | --- |
| 业绩强势 earnings_surprise | ✅ 默认每日 | 业绩超预期 + 位置分层 | `docs/local_strategies/core/` |
| 百日新高 hundred_day_high | ✅ 默认每日 | 创百日新高（防守市降权进观察区） | 同上 |
| 趋势龙头 trend_leader_unified | ✅ 默认每日 | 趋势结构龙头（严格模式，0 命中即空仓） | 同上 |
| 快复盘 fast_review | ✅ 每日（矩阵内） | 当日强势票筛选 → 作战计划候选池 | `docs/个人策略文档/个人每日复盘约定.md` |
| 红利 T 提示 dividend_t_watch | ✅ 每日 | ①趋势红利回踩 -3/-5/-7% 档 + MA 护栏 | `docs/local_strategies/topics/` |
| 深跌企稳 screen_deep_recovery | ✅ 每日 | 年内回撤≥35% + 站上MA20 + 5日>2% | 同上 |
| 吃股息（池/体检/三档/示例组合） | ✅ 执行约定 v1.0 | 精简B（股息+低波+现金流）排序 | `docs/个人策略文档/吃股息策略-实施与验证总结.md` |
| 日线慢涨 daily_slow_rise | ❄️ 冻结 | w10 触发淘汰线 | `docs/local_strategies/README.md` |
| 长平台释放 long_base_release | ❄️ 停用 | 稳定负期望 | 同上 |
| 月线慢涨 monthly_slow_rise | ❄️ 冻结 | 低频扩展 | 同上 |
| 连续上涨占比/连涨段 | 🧪 试验 | 均负期望，不单用 | 同上 |

## 5. 图形结构工具组（按需运行，离线只读缓存）

| 脚本 | 用途 | 产物 |
| --- | --- | --- |
| `scripts/screen_updays_structure.py` | 上涨结构 + 伪强势自动剔除（默认上涨占比≥58%，每日跑） | `data/strategy_review/screen_updays_structure_<date>.md/csv` |
| `scripts/analyze_stock_profile.py` | 个股五段式快照（新闻/基本面/技术/筹码/一句话），`--codes` 或 `--from-csv` | `stock_profiles_<date>/INDEX.md` |
| `scripts/screen_t_friendly.py` | 温和阳线型"T 友好"（阳线占比/振幅/回撤/摆动 + 趋势质量层） | `t_friendly_<date>.md/csv` + `t_friendly_charts_<date>/` |
| `scripts/screen_active_pool.py` | 活性池（“有资金玩”=均振幅≥3%+3%+日≥10+回撤≤35%+连跌≤6；核心20+跟踪30） | `active_pool_<date>.md/csv` |
| `scripts/strong_stocks_stats.py` | 当日强势股分类（连板/首板/涨停后再走强/未涨停大涨 + 板块分布 + 连板清单） | `strong_stocks_<date>.md/csv` |
| `scripts/cap_bucket_stats.py` | 市值分层热度（小盘 vs 机构票资金结构，在线快照） | `cap_buckets_<date>.md` |

## 6. 产物地图（`data/strategy_review/`）

- 每日核心：`daily_action_plan_latest.md`（作战计划）、`daily_ai_reviews_latest.md`（AI 审核全文）、`strategy_scoreboard.md`（策略成功度）、`daily_review_latest.csv`（近月绩效）、`dividend_t_watch_<date>.md`、`deep_fall_recovery_<date>.md`
- 图形结构：`screen_updays_structure_<date>.*`、`stock_profiles_<date>/`、`t_friendly_*`、`active_pool_*`、`strong_stocks_*`、`cap_buckets_*`
- 股息线：`data/dividend_income/`、`dividend_three_tiers_*.md`、`dividend_sample_portfolio_*.md`

## 7. 日常命令速查

```bash
./.venv-linux/bin/python scripts/strong_stocks_stats.py            # 今日强势股分类
./.venv-linux/bin/python scripts/cap_bucket_stats.py               # 市值分层热度
./.venv-linux/bin/python scripts/screen_active_pool.py --compare 300413,002491
./.venv-linux/bin/python scripts/screen_t_friendly.py              # T友好图（含图表）
./.venv-linux/bin/python scripts/analyze_stock_profile.py --codes 600893
./.venv-linux/bin/python -m pytest -m "not network"                # 确定性测试
bash data/runtime/warm_daily.sh && bash data/runtime/matrix_daily.sh  # 手动跑当天复盘
```

## 8. 文档导航（按需深入）

- 协作规则：`AGENTS.md`（唯一真源）、`.github/copilot-instructions.md`
- 多条线怎么拼成一套（分层/配方/漏斗/节奏）：`docs/个人策略文档/个人策略协同总纲.md`
- 策略总目录（56K，较全）：`docs/个人策略文档/个人策略目录.md`
- 当前参数基线：`docs/个人策略文档/个人策略基线.md`
- 改动与证据库：`docs/个人策略文档/个人策略修改记录.md`
- 个人每日复盘约定：`docs/个人策略文档/个人每日复盘约定.md`
- 吃股息：`docs/个人策略文档/吃股息策略-实施与验证总结.md`
- 策略文档目录：`docs/local_strategies/README.md`
- 产品/部署文档总入口：`docs/INDEX.md`
- 用户可见变更：`docs/CHANGELOG.md`

*最后更新：2026-11-05。新增/重命名脚本或策略时请同步本文件第 4-6 节。*
