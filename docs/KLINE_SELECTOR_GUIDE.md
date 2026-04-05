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
  - 当前选中结果导出为 `Markdown / JSON`
  - 当前选中结果一键推送到已配置通知渠道
- 长日期范围下已补充几项稳态优化：
  - 单日查询不再额外计算多日对比摘要
  - 前端对相同查询和历史请求做缓存，减少来回切换时的重复请求
  - 多日对比卡片默认折叠，只先展示前 10 天，可按需展开
  - 联动筛选改为状态驱动，减少“点击一次触发两次请求”的重复加载
- 导出 / 推送的作用范围说明：
  - 若当前有联动选中，则优先导出 / 推送联动结果
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
- 会查询该股票近 `--history-lookback-days` 天内是否也命中过同口径百日新高，并输出：
  - `previous_hit_count`
  - `latest_previous_hit_date`
  - `days_since_previous_hit`
  - `recent_hit_dates`（详细结果保存在数据库 `history_payload`）
- 默认会尝试做“上涨原因”归因：
  - 先收集基本面、所属板块、近期新闻
  - 再做主题级海外映射（如 AI 算力 / 有色涨价 / 创新药等）
  - 最后用 LLM 压缩成 `reason_summary`、`industry_logic`、`news_logic`、`technical_logic`、`cause_tags`、`theme_label`
- 如需只跑筛选和历史回看、跳过归因，可加：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_hundred_day_high_candidates.py --skip-cause-analysis
```

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
