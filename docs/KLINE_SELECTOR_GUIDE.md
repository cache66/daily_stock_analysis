# K 线筛选器操作说明

## 适用场景

这套工具适合做**独立于主分析流程**的 A 股 K 线条件筛选。

适合的使用方式：

- 想快速扫一遍 A 股，找出满足固定 K 线条件的候选股
- 想先缩小标的池，再做人工复盘或二次分析
- 想中断后续跑，不想每次都从头开始

当前入口：

- 服务类：`src/services/kline_selector_service.py`
- 运行脚本：`scripts/select_kline_candidates.py`
- 次日策略扫描脚本：`scripts/scan_next_day_setups.py`

## 当前已实现的能力

- 扫描范围：A 股
- 自动排除：北交所
- 自动过滤：明显的指数 / ETF / 非目标标的
- 支持 checkpoint 落盘
- 支持 `--resume` 断点续跑
- 支持现货预过滤，减少长周期历史 K 线抓取量
- 支持并发扫描
- 已针对当前 Windows 环境做过一轮低延迟优化：
  - K 线筛选专用 fast path 默认关闭 Akshare 的随机 sleep
  - 普通 A 股历史 K 线优先走 `sina -> tencent -> em`

## 当前默认规则

默认筛选规则如下：

1. 最近 `10` 个交易日上涨占比大于 `70%`
2. 最近 `10` 个交易日至少出现 `1` 次涨停
3. 最新 K 线的 `high` 创最近 `100` 个交易日新高
4. 总市值不超过 `500` 亿

补充说明：

- 上涨占比按 `close > 前一交易日 close` 计算
- 涨停口径按 A 股常见规则识别：
  - 主板 `10%`
  - 科创板 / 创业板 `20%`
  - ST `5%`
- 市值在内部统一按“元”比较，CLI 输入时用“亿”更方便

## 运行环境

当前建议的实际运行方式：

- 代码目录：`D:\bb\daily_stock_analysis`
- Python 环境：`E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe`

后续如果继续处理或复跑，建议都在仓库根目录下执行命令。

## 快速开始

先看帮助：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --help
```

## `/signals` 页面补充说明

- 当前 `/signals` 页面除了按日期和按股票查看快照外，还支持：
  - 多日对比里的 `新增 / 掉队 / streak 排行`
  - `streak` 按主题或行业分组
  - 点击 `新增 / 掉队` 标签联动筛选
  - `streak` 多选联动与“选择本组”
  - 分组联动后显示当前主题组 / 行业组，并支持再次点击“取消本组”
  - 当前选中结果导出为 `Markdown / JSON`
  - 当前选中结果一键推送到已配置通知渠道
- 长日期范围下已补充几项稳态优化：
  - 单日查询不再额外计算多日对比摘要
  - 前端对相同查询和历史请求做缓存，减少来回切换时的重复请求
  - 多日对比卡片默认折叠，只先展示前 10 天，可按需展开
  - 联动筛选改为状态驱动，减少“点击一次触发两次请求”的重复加载
- 导出 / 推送的作用范围说明：
  - 若当前有联动选中，则优先导出 / 推送联动结果
  - 若联动来自 `streak` 分组选择，导出 / 推送摘要会带上对应主题组 / 行业组标签
  - 若开启“只看连续新高”且没有联动选中，则导出 / 推送当前页连续新高结果
- 其他情况下，导出 / 推送当前页可见结果

## 汇总表全量重建

当你遇到下面这些场景时，建议运行一次汇总表重建脚本：

- 历史 `kline_signal_snapshot` 早于汇总表能力落地，需要补齐老数据
- 数据库迁移、备份恢复或换机器后，想重新生成预计算表
- 后续如果连续新高或对比摘要算法升级，想统一重刷历史汇总

全量重建：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py
```

只重建百日新高：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high
```

只重建某一段时间内受影响的汇总：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --start-date 2026-04-01 --end-date 2026-04-30
```

只重建某一只或某几只股票相关的汇总：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --code 600519
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --codes 600519,000001,300750
```

只看当前规模和预计算覆盖情况，不执行重建：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --report-only --log-level WARNING
```

输出一份结构化性能观测 JSON：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --report-json data\signal_summary_perf_report.json --log-level WARNING
```

如果你想一边重建、一边把每次结果累积成时间序列日志，可以继续指定一份长期日志文件：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\rebuild_signal_summary_tables.py --signal-type hundred_day_high --report-json data\signal_summary_perf_report.json --append-jsonl data\signal_summary_perf_history.jsonl --log-level WARNING
```

说明：

- 这个脚本不会改动主表 `kline_signal_snapshot`
- 它做的是基于主表重新生成：
  - `kline_signal_daily_summary`
  - `kline_signal_streak_snapshot`
- 如果预计算表缺失、脏了、或者历史数据还没补算，这个脚本就是修复入口
- `--report-only` 可用于日常观察：
  - `snapshot_count / snapshot_day_count / snapshot_code_count`
  - `daily_summary_count`
  - `streak_snapshot_count / streak_code_count`
- `--append-jsonl` 会把本次结果按一行一个 JSON 追加到历史日志里，后续可直接按时间观察：
  - `elapsed_seconds`
  - `throughput_snapshot_rows_per_second`
  - `snapshot_count`
  - `daily_summary_count`
  - `streak_snapshot_count`
- 执行重建时还会额外输出：
  - `elapsed_seconds`
  - `throughput_snapshot_rows_per_second`

## 趋势查看脚本 / 小报表

当 `signal_summary_perf_history.jsonl` 已经积累了一些记录后，可以直接生成一份趋势摘要：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_summary_perf_trend.py --input data\signal_summary_perf_history.jsonl --output-md data\signal_summary_perf_trend.md --signal-type hundred_day_high --limit 20
```

脚本会做两件事：

- 在终端输出最近样本的摘要：
  - `record_count`
  - `latest_snapshot_count`
  - `avg_elapsed_seconds`
  - `avg_throughput`
- 生成一份 Markdown 小报表：
  - `data\signal_summary_perf_trend.md`

常用筛选：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_summary_perf_trend.py --mode rebuild
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_summary_perf_trend.py --signal-type hundred_day_high --limit 50
```

适合场景：

- 观察重建耗时是否随 `snapshot_count` 增长
- 对比 `report_only` 和 `rebuild` 两类记录
- 快速把 JSONL 变成便于归档和分享的小报表

直接按默认规则跑：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py
```

按“复盘后次日确认”策略扫描：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\scan_next_day_setups.py --strategy all
```

如果只是先确认链路通不通，建议先跑一个小样本：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --limit 20 --log-level INFO
```

## 最常用命令

### 1. 小样本试跑

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --limit 100 --log-level DEBUG
```

适合场景：

- 先确认数据源是否正常
- 先观察日志和输出结构
- 网络不稳定时先做冒烟验证

### 2. 改阈值

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --min-up-ratio 0.8 --max-total-mv-yi 300
```

这条命令会把条件收紧为：

- 最近上涨占比 > `80%`
- 总市值 <= `300` 亿

### 3. 调整并发

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --max-workers 2 --limit 20
```

建议：

- 当前 Windows 环境默认先用 `--max-workers 1`
- 小样本压测可尝试 `--max-workers 2`
- 全市场提速优先用分片，不建议继续拉高 worker

### 4. 关闭现货预过滤

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --disable-spot-prefilter
```

适合场景：

- 更重视召回率
- 不想让现货字段先把候选池裁掉

### 5. 更激进压缩候选池

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --min-60d-change-pct-prefilter 15 --min-turnover-rate-prefilter 1
```

适合场景：

- 只想先抓更强势、更活跃的一批票
- 需要尽量压缩全市场扫描时间

### 6. 断点续跑

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --resume
```

如果想自定义 checkpoint 路径：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --checkpoint-path data\custom_kline_checkpoint.json --resume
```

### 7. 扫描次日确认策略（新增）

扫描全部次日策略（内包日 / NR7 / 反转）：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\scan_next_day_setups.py --strategy all --max-workers 1
```

仅扫描内包日：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\scan_next_day_setups.py --strategy inside_day
```

仅扫描 NR7：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\scan_next_day_setups.py --strategy nr7
```

仅扫描反转形态：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\scan_next_day_setups.py --strategy reversal
```

补充说明：

- 这个脚本识别的是“信号日候选”，不是直接给次日买入结论。
- 实战执行仍建议次日盘中做突破确认和风险控制。
- 默认同样支持 `--shard-count` / `--shard-index`、`--resume`、现货预过滤参数。

## 默认参数

默认参数如下：

- `lookback_days=10`
- `min_up_ratio=0.7`
- `new_high_window=100`
- `max_total_mv_yi=500`
- `max_workers=1`
- `min_60d_change_pct_prefilter=10`
- `checkpoint_path=data/kline_selector_checkpoint.json`
- `checkpoint_every=50`

稳定性提示：
- 当前 Windows 环境默认以 `max_workers=1` 作为稳定基线
- 真实全市场实跑建议优先使用 checkpoint / `--resume`
- 如需提速，更推荐使用分片并行，不建议继续拉高 `--max-workers`

## 输出文件说明

脚本默认会在 `data/` 下生成这些文件：

- `a_share_universe_no_bse.txt`
  - 本次参与扫描的 A 股样本列表
- `kline_selector_candidates.csv`
  - 结构化结果，便于二次分析
- `kline_selector_candidates.txt`
  - 命中的股票代码，一行一个
- `kline_selector_candidates.md`
  - 当前规则摘要 + 命中结果
- `kline_selector_checkpoint.json`
  - 断点续跑文件
- `next_day_setup_candidates.csv / txt / md`
  - 次日确认策略候选结果
- `next_day_setup_checkpoint.json`
  - 次日确认策略扫描的断点续跑文件

## CSV 字段说明

`kline_selector_candidates.csv` 当前核心字段包括：

- `code`
- `name`
- `total_market_cap`
- `total_market_cap_yi`
- `up_days`
- `lookback_days`
- `up_ratio`
- `recent_limit_up_dates`
- `latest_high`
- `window_high`
- `new_high_window`
- `history_source`
- `failure_reason`

## 现货预过滤说明

在拉 100+ 个交易日的历史 K 线之前，脚本会先用股票池里已经有的轻量字段做一次便宜过滤。

默认启用的口径：

- `60日涨跌幅 >= 10`

可选参数：

- `--min-turnover-rate-prefilter`
- `--require-positive-change-prefilter`
- `--exclude-st-prefilter`
- `--disable-spot-prefilter`

经验建议：

- 想缩短耗时：保留默认预过滤
- 想尽量不漏票：关闭预过滤

## 断点续跑说明

全市场扫描默认会把进度写到：

```text
data/kline_selector_checkpoint.json
```

使用规则：

- 首次运行：直接跑脚本即可
- 中断后继续：加 `--resume`
- 想换保存位置：加 `--checkpoint-path`
- 想调整保存频率：加 `--checkpoint-every`

注意：

- `--resume` 依赖和上次一致的筛选参数与预过滤参数
- 如果参数变了但继续复用旧 checkpoint，脚本会拒绝恢复

## 当前性能表现

基于 2026-03-30 在当前环境的实际验证：

- `get_a_share_universe(limit=5)` 大约 `18.78s`
- `scripts/select_kline_candidates.py --limit 5 --max-workers 1` 大约 `28.89s`

其中当前主要耗时分布大致是：

- 股票池加载：仍然是首段瓶颈
- 单股历史 K 线：优化后约 `0.84s ~ 1.39s`
- 单股实时行情：腾讯单股查询约 `0.07s ~ 0.09s`

说明：

- 这已经明显快于之前同样参数下的约 `103.40s`
- 如果后续还要继续提速，优先应优化股票池加载阶段

## 推荐使用方式

### 场景 1：先看链路是否正常

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --limit 20 --log-level INFO
```

### 场景 2：正式扫全市场

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --max-workers 1
```

如需在全市场场景继续提速，优先保持 `--max-workers 1` 并改用后文的“分片并行运行”方案。

### 场景 3：网络不稳定，先保守一点

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --max-workers 1 --limit 100
```

### 场景 4：中途断了，继续跑

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --resume
```

## 常见问题

### 1. 为什么运行很慢？

常见原因：

- 股票池加载阶段遇到 `akshare_spot_em` 不稳定
- 网络环境不佳
- 并发开太高导致上游接口更容易失败

建议：

- 先用 `--limit 20` 小样本验证
- 再根据情况调 `--max-workers`
- 必要时保留 checkpoint，使用 `--resume`

### 2. 为什么一个候选都没有？

这是正常现象，不代表脚本有问题。

原因可能是：

- 当前规则本来就偏严格
- 当天市场环境下符合条件的票确实很少
- 你把阈值调得更严格了

### 3. 输出结果放在哪里？

默认都在：

```text
data/
```

如果你传了 `--output-dir`，则按你指定的目录输出。

### 4. 下次接手先看哪里？

建议优先看：

- `docs/KLINE_PROCESS_LOG.md`
- `docs/KLINE_SELECTOR_GUIDE.md`
- `src/services/kline_selector_service.py`
- `scripts/select_kline_candidates.py`

## 扩展建议

后续如果继续加规则，建议按现有规则类设计扩展，不要把逻辑继续堆进一个大函数里。

适合后续追加的方向：

- 均线多头排列
- 缩量回踩
- 最近 N 日换手率放大
- 最近 N 日成交额门槛
- 行业 / 板块过滤
- ST / 次新股排除
## 独立百日新高入口

现在除了原有的组合 K 线策略：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --max-workers 1
```

还新增了独立的“百日新高”入口：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py
```

两者区别：

- `select_kline_candidates.py`
  - 组合策略
  - 默认规则是“近 10 日上涨占比 > 70% + 近 10 日至少 1 次涨停 + 100 日新高 + 市值上限”
- `select_hundred_day_high_candidates.py`
  - 独立百日新高策略
  - 默认规则是“100 日新高 + 市值上限”
  - 不再要求近 10 日上涨占比，也不再要求近 10 日出现涨停

常用示例：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --limit 100
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --new-high-window 120
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --snapshot-date 2026-04-04
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --history-lookback-days 180
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --resume
```

新增能力：

- 会按 `signal_type=hundred_day_high` 将当日命中的股票落到数据库快照表，唯一键是 `(signal_type, signal_date, code)`，同一天重跑会覆盖更新，不会重复累计
- 如果你要同时积累多套 profile 的同日样本，不能继续共用 `signal_type=hundred_day_high`，否则不同 profile 会在同一天互相覆盖；推荐改用独立命名空间，例如：
  - `hundred_day_high_profile__breakout_balanced`
  - `hundred_day_high_profile__momentum_strict`
  - `hundred_day_high_profile__breakout_loose`
- 会查询该股票近 `--history-lookback-days` 天内是否也命中过同口径百日新高，并输出：
  - `previous_hit_count`
  - `latest_previous_hit_date`
  - `days_since_previous_hit`
  - `recent_hit_dates`（详细结果保存在数据库 `history_payload`）
- 默认会尝试做“上涨原因”归因：
  - 先收集基本面、所属板块、近期新闻
  - 再做主题级海外映射（如 AI 算力 / 有色涨价 / 创新药等）
  - 最后用 LLM 压缩成 `reason_summary`、`industry_logic`、`news_logic`、`technical_logic`、`cause_tags`、`theme_label`
- 现在默认按“两阶段”执行：
  - 阶段 1：扫描过程中命中即先落快照，`/signals` 可以更早看到当天结果
  - 阶段 2：扫描完成后再统一补全 `reason_summary / industry_logic / news_logic / technical_logic / theme_label`
- 如需只跑筛选和历史回看、跳过归因，可加：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --skip-cause-analysis
```

当前百日新高脚本也支持直接切换预设 profile：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --profile breakout_balanced
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --profile breakout_balanced_with_earnings
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --profile momentum_strict
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --profile breakout_loose
```

如果你想从今天开始持续积累三套 profile 的快照样本，推荐直接用新的批量采集脚本：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_hundred_day_high_profile_snapshots.py --limit 500 --max-workers 1
```

说明：

- 脚本会顺序执行三次筛选，并分别落到独立 `signal_type`
- 默认 signal_type 前缀是 `hundred_day_high_profile`
- 实际会生成：
  - `hundred_day_high_profile__breakout_balanced`
  - `hundred_day_high_profile__momentum_strict`
  - `hundred_day_high_profile__breakout_loose`
- 默认只做快照采集，不跑归因，适合先积累后验样本
- 后续做表现评估时，直接把 `--signal-type` 切到对应 profile 命名空间即可

三套预设的定位建议：

- `breakout_balanced`：当前默认口径，偏“先抓突破，再看后续表现”
- `breakout_balanced_with_earnings`：沿用 `breakout_balanced` 的百日新高条件，但只保留通过 `earnings_surprise balanced` 口径确认的股票；若未手动传 `--signal-type`，默认落库到 `hundred_day_high__earnings_balanced`
- `momentum_strict`：更强调强势确认，会启用上涨占比和近期涨停规则，并收紧市值/预过滤
- `breakout_loose`：更偏宽松突破，用于观察更大样本池里的后续演化

如需在预设基础上微调，CLI 显式参数仍会覆盖 profile 默认值，例如：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --profile momentum_strict --skip-recent-limit-up-rule --max-total-mv-yi 450
```

当前更推荐按下面这组默认参数理解三套 profile：

- `breakout_balanced`
  - `lookback_days=8`
  - `min_up_ratio=0.625`
  - `new_high_window=100`
  - `max_total_mv_yi=400`
  - `min_60d_change_pct_prefilter=12`
  - `min_turnover_rate_prefilter=0.8`
  - 默认启用：上涨占比规则、正涨幅预过滤、排除 ST
- `breakout_balanced_with_earnings`
  - K 线参数与 `breakout_balanced` 相同
  - 在命中百日新高后，继续复用 `earnings_surprise balanced` 做业绩确认
  - 默认 `signal_type=hundred_day_high__earnings_balanced`
- `momentum_strict`
  - `lookback_days=8`
  - `min_up_ratio=0.67`
  - `limit_up_lookback_days=8`
  - `new_high_window=110`
  - `max_total_mv_yi=70`
  - `min_60d_change_pct_prefilter=20`
  - `min_turnover_rate_prefilter=1.2`
  - 默认启用：上涨占比规则、正涨幅预过滤、排除 ST；默认关闭：近期涨停规则
- `breakout_loose`
  - `lookback_days=12`
  - `min_up_ratio=0.58`
  - `new_high_window=80`
  - `max_total_mv_yi=600`
  - `min_60d_change_pct_prefilter=8`
  - `min_turnover_rate_prefilter=0.5`
  - 默认启用：正涨幅预过滤、排除 ST；默认关闭：上涨占比规则、近期涨停规则

这次我偏好的调参方向是：

- `breakout_balanced` 不再放得太松，避免默认结果里混进太多“刚摸高但趋势并不稳定”的票
- `momentum_strict` 现在更接近 “strict-lite”：仍然偏强趋势确认，但不再死卡近期涨停；同时更明确偏向小中盘强势票，避免和 `breakout_balanced` 过度重合
- `breakout_loose` 仍然保留大样本观察价值，但不给到完全裸奔的宽松口径

如果你想把“快照落库”和“归因补全”拆成两次运行，推荐这样用：

```powershell
# 第一阶段：先落快照，页面可先看
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --snapshot-date 2026-04-04 --skip-cause-analysis

# 第二阶段：后补归因，不再重扫全市场
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --snapshot-date 2026-04-04 --cause-analysis-only
```

说明：

- `--cause-analysis-only` 默认只补“归因仍为空”的同日快照，已补过归因的股票会自动跳过
- 如果你想基于最新逻辑把同日已有归因也整体重跑一遍，可再加：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --snapshot-date 2026-04-04 --cause-analysis-only --force-cause-refresh
```

如需进一步走“快速结构化归因”模式，可继续附加：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --snapshot-date 2026-04-04 --cause-analysis-only --disable-llm-reason-card
```

说明：

- `--disable-news-search`：跳过新闻搜索，只用强势池、主营业务、板块、基本面等已有结构化信息
- `--disable-llm-reason-card`：不再调用 LLM 压缩原因卡，直接输出规则化 fallback 摘要
- 两者一起用时，归因速度会明显更稳，适合日常定时更新

如需接入现有每日定时分析流程，可在 `.env` 或 Web 系统设置中开启：

```env
SIGNAL_SNAPSHOT_HUNDRED_DAY_HIGH_ENABLED=true
SIGNAL_SNAPSHOT_HUNDRED_DAY_HIGH_CAUSE_ANALYSIS_ENABLED=false
```

说明：

- 打开 `SIGNAL_SNAPSHOT_HUNDRED_DAY_HIGH_ENABLED=true` 后，现有 `python main.py --schedule` 每日分析完成后，会自动刷新百日新高快照
- 若同时打开 `SIGNAL_SNAPSHOT_HUNDRED_DAY_HIGH_CAUSE_ANALYSIS_ENABLED=true`，会继续在快照阶段之后补全归因；当前定时模式默认走“双阶段”：
  阶段 1 全扫保持稳定快扫，阶段 2 仅对入选候选补抓新闻与主营，并默认关闭 LLM 原因卡以控制日更耗时
- 若只想保证 `/signals` 每天有数据、优先缩短耗时，建议先只开第一项，把第二项保持 `false`

当你想评估这条信号本身有没有持续有效，而不只是看当天命中列表时，可以直接基于已落库快照跑一个轻量表现报告：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\evaluate_signal_snapshot_performance.py --signal-type hundred_day_high --start-date 2026-04-01 --end-date 2026-04-30
```

常用变体：

```powershell
# 自定义窗口
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\evaluate_signal_snapshot_performance.py --signal-type hundred_day_high --windows 1,2,3,5,10

# 只看单只股票
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\evaluate_signal_snapshot_performance.py --signal-type hundred_day_high --code 600519
 
# 鍙湅鏌愪釜 profile 鐨勮〃鐜?
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\evaluate_signal_snapshot_performance.py --signal-type hundred_day_high --profile momentum_strict --start-date 2026-04-01 --end-date 2026-04-30
```

脚本会输出两份文件：

- `data/signal_snapshot_performance_report.json`
- `data/signal_snapshot_performance_report.md`

默认会按窗口给出：

- `total / completed / insufficient`
- `win_rate_pct`
- `avg_stock_return_pct`
- `median_stock_return_pct`
- `avg_max_runup_pct`
- `avg_worst_drawdown_pct`

说明：

- 起点价格优先使用快照当天落库的 `close`
- 若某条快照在对应窗口下未来交易日不足，会记为 `insufficient_data`
- 当前口径是“信号发生后继续持有”的纯多头 forward return 观察，更适合先比较策略有效性，不等同于完整交易系统回测

如需只导出文件、不写数据库快照，可加：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --skip-db-persist
```

输出文件：

- `hundred_day_high_candidates.csv`
- `hundred_day_high_candidates.txt`
- `hundred_day_high_candidates.md`
- `hundred_day_high_checkpoint.json`

其中 `csv / md` 现已补充这些摘要字段：

- `industry`
- `reason_summary`
- `industry_logic`
- `news_logic`
- `technical_logic`
- `cause_tags`
- `theme_label`
- `latest_previous_hit_date`
- `previous_hit_count`
- `days_since_previous_hit`

补充说明：

- `theme_label` 走的是“主题级海外映射”而不是强绑定个股映射
- 当前实现会优先参考当日强势池/涨停池行业、主营业务、新闻标题等高置信信息
- 若证据不足，`theme_label` 会保持为空，避免把普通行业票误贴成 AI / 创新药 / 资源涨价等海外主题

## 百日新高查询 API

当前已补充最小后端查询入口，便于按日期查看当天命中的快照，或按股票查看最近一段时间的同口径历史：

### 1. 按日期查询当天快照

```text
GET /api/v1/signals/kline-snapshots?signal_type=hundred_day_high&signal_date=2026-04-04
```

可选参数：

- `code`
- `limit`

当前返回重点字段包括：

- `code`
- `name`
- `signal_date`
- `industry`
- `reason_summary`
- `industry_logic`
- `news_logic`
- `technical_logic`
- `theme_label`
- `latest_previous_hit_date`
- `previous_hit_count`
- `days_since_previous_hit`
- `close`
- `latest_high`
- `window_high`

### 2. 按股票查看最近历史

```text
GET /api/v1/signals/kline-snapshots/hundred_day_high/600519?days=180
```

当前返回：

- 历史命中列表（按日期倒序）
- 连续命中摘要：
  - `is_current_streak`
  - `current_streak_count`
  - `current_streak_start_date`
  - `current_streak_end_date`
  - `longest_streak_count`
- 基于已存快照的近似回撤摘要：
  - `anchor_close`
  - `max_signal_high`
  - `distance_from_max_signal_high_pct`
  - `latest_signal_high`
  - `distance_from_latest_signal_high_pct`

说明：

- 第一版“跌了多少”是基于已落库信号快照近似计算，不额外抓长历史行情
- 如果后续需要 `250` 日高点 / 历史最高价回撤，再在下一阶段扩展

Web 端也已补充最小查看入口：

- 访问 `/signals`
- 默认查看当日 `hundred_day_high` 快照
- 左侧看当天命中的票，右侧看：
  - 连续新高次数
  - 相对最近信号高点 / 最大信号高点的近似回撤
  - `industry_logic / news_logic / technical_logic`
- 继续增强后还支持：
  - 单日 / 日期范围切换
  - 多日对比：
    - 每日命中数
    - 连续新高数
    - 新增 / 掉队
  - `streak` 排行：
    - 当前连续命中次数
    - 历史最长连续命中次数
  - 列表中的：
    - 连续新高 / 其他新高显式分组
    - 分页
    - 客户端排序与“只看连续新高”筛选

## 分片并行运行

如果全市场直接单进程跑太慢，推荐不要继续拉高 `--max-workers`，而是保持每个进程 `--max-workers 1`，改成多进程分片：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --max-workers 1 --shard-count 4 --shard-index 0 --output-dir C:\temp\kline_combo_full_20260331
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --max-workers 1 --shard-count 4 --shard-index 1 --output-dir C:\temp\kline_combo_full_20260331
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --max-workers 1 --shard-count 4 --shard-index 2 --output-dir C:\temp\kline_combo_full_20260331
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_kline_candidates.py --max-workers 1 --shard-count 4 --shard-index 3 --output-dir C:\temp\kline_combo_full_20260331
```

独立百日新高策略同理：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --max-workers 1 --shard-count 4 --shard-index 0 --output-dir C:\temp\hundred_day_high_full_20260331
```

说明：

- 分片运行时，脚本会自动把输出写到 `shard_01_of_04` 这类子目录，避免互相覆盖
- checkpoint 文件也会自动追加 shard 后缀
- 每个分片输出目录里都会保留一份策略专用 checkpoint，方便后续合并

## 合并分片结果

全部分片跑完后，用下面的脚本把多个分片目录重新合并成一套完整输出：

组合 K 线策略：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\merge_kline_shard_results.py --mode combo --input-dirs `
  C:\temp\kline_combo_full_20260331\shard_01_of_04 `
  C:\temp\kline_combo_full_20260331\shard_02_of_04 `
  C:\temp\kline_combo_full_20260331\shard_03_of_04 `
  C:\temp\kline_combo_full_20260331\shard_04_of_04 `
  --output-dir C:\temp\kline_combo_full_20260331\merged
```

独立百日新高策略：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\merge_kline_shard_results.py --mode hundred-day-high --input-dirs `
  C:\temp\hundred_day_high_full_20260331\shard_01_of_04 `
  C:\temp\hundred_day_high_full_20260331\shard_02_of_04 `
  C:\temp\hundred_day_high_full_20260331\shard_03_of_04 `
  C:\temp\hundred_day_high_full_20260331\shard_04_of_04 `
  --output-dir C:\temp\hundred_day_high_full_20260331\merged
```

## 2026-04-25 补充：百日新高质量突破字段

- `scripts/select_hundred_day_high_candidates.py` 现在会在命中结果中补充以下质量字段：
  - `minervini_template_score` / `minervini_template_passed`
  - `breakout_follow_through_score`
  - `industry_strength_confirmed` / `industry_strength_score` / `industry_strength_label`
- 新字段会同时进入：
  - `hundred_day_high_candidates.csv`
  - 快照 `metrics_payload`（供 `--cause-analysis-only` 与 `/signals` 查询复用）
  - `hundred_day_high_candidates.md` 的结果行（便于人工复盘）

## 鏃ョ嚎缂撳瓨

涓轰簡闄嶄綆澶氱瓥鐣ャ€佸ぇ鏍锋湰鎵弿鏃剁殑閲嶅涓嬭浇鍘嬪姏锛孌ataFetcherManager` 鐜板湪浼氬湪 manager 灞傝嚜鍔ㄥ鐢ㄦ湰鍦版棩绾跨紦瀛橈細

- 榛樿鐩綍锛?`data/cache/history/`
- 璇诲彇绛栫暐锛氬厛璇荤紦瀛橈紝鍐嶆寜闇€鑱旂綉
- 澧為噺绛栫暐锛氬綋璇锋眰鍙戠敓鍒板熬閮ㄦ墿灞曟椂锛屽彧琛ュ熬閮ㄧ己澶辩殑閭ｄ竴娈靛巻鍙叉棩绾?
- 鎸囨爣涓€鑷存€э細纾佺洏鍙繚瀛樺熀纭€鏃ョ嚎鍒楋紝璇诲洖鍚庝細閲嶇畻 `ma5 / ma10 / ma20 / volume_ratio`

鍙€夌幆澧冨彉閲忥細

- `HISTORY_DISK_CACHE_ENABLED`
- `HISTORY_DISK_CACHE_DIR`
- `HISTORY_DISK_CACHE_TTL_SECONDS`
- `HISTORY_DISK_CACHE_OVERLAP_DAYS`

鍏朵腑 `TTL` 涓昏鐢ㄤ簬鈥滃寘鍚渶鏂拌鎯呯殑璇锋眰鈥濓紝閬垮厤鏃犻檺澶嶇敤褰撴棩闄勮繎鐨勫彲鑳介檲鏃ф暟鎹紱鑰屽凡缁忔埅姝㈢殑鍘嗗彶鏃堕棿鑼冨洿浼氫紭鍏堢洿鎺ュ鐢ㄧ紦瀛樸€?
