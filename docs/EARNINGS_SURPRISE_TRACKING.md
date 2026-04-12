# 业绩超预期事件跟踪

## 目标

这条链路用于把“季报 / 业绩预告 / 业绩快报偏强”的股票先记录下来，后续再统一观察涨跌表现。

当前实现是**规则代理版**，不是基于卖方一致预期的严格 `earnings surprise` 计算。它更适合做：

- 财报强势事件池留痕
- 季报后 1/3/5/10 日表现复盘
- 按日期或股票回看历史命中

## 当前口径

脚本入口：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_earnings_surprise_candidates.py
```

默认会综合以下信号：

- 业绩预告 / 快报文本里出现正向关键词
  - 例如：`预增`、`扭亏`、`超预期`、`beat`
- 财务增速达到阈值
  - 默认 `营收同比 >= 10%`
  - 默认 `净利润同比 >= 20%`

默认命中逻辑是：

- 没有明显负向关键词
- 且满足“正向文本”或“增长阈值”之一

明显负向文本会直接拦截，例如：

- `预减`
- `预亏`
- `转亏`
- `不及预期`
- `miss`

## 事件去重

脚本默认开启“按事件去重”，避免同一季报在连续多天扫描时被重复记成多次新事件。

默认 event key 生成方式：

- 优先用更贴近公告日的 `event_date`
  - 先取 `quick_report_announcement_date`
  - 再取 `forecast_announcement_date`
  - 最后才退化到 `financial_report.report_date`
- 如果拿不到报告期，再退化为 `forecast_summary + quick_report_summary` 的文本 hash
- 如果连文本都没有，再退化到同比数字组合 hash

关闭去重：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_earnings_surprise_candidates.py --disable-event-dedupe
```

## 常用命令

小样本验证：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_earnings_surprise_candidates.py --limit 50 --log-level INFO
```

要求同时满足正向文本和增长阈值：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_earnings_surprise_candidates.py --require-positive-text --require-growth-thresholds
```

提高净利润同比门槛，并限制中小市值：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_earnings_surprise_candidates.py --min-net-profit-yoy 30 --max-total-mv-yi 300
```

只导出文件，不落库：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_earnings_surprise_candidates.py --skip-db-persist
```

## 输出结果

默认输出到 `data/`：

- `earnings_surprise_candidates.csv`
- `earnings_surprise_candidates.txt`
- `earnings_surprise_candidates.md`

若开启数据库落库，还会写入现有通用快照表：

- `kline_signal_snapshot`

其中 `signal_type = earnings_surprise`。

这意味着后续可以直接复用现有信号查询与表现评估链路。

当前 `/signals` 切到 `earnings_surprise` 时，也会直接展示这类快照；卡片和历史查看里可看到更贴近公告节奏的 `event_date` 口径，而不是只看报告期。

如果你还想同时看“新高 + 业绩”的交集，现在 `/signals` 里也提供了 `新高且业绩` 组合视图，会对 `hundred_day_high` 与 `earnings_surprise` 做同日交集展示。

## 后续表现评估

落库后可直接复用现有评估脚本：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\evaluate_signal_snapshot_performance.py --signal-type earnings_surprise --start-date 2026-04-01 --end-date 2026-04-30
```

常见评估输出包括：

- `1/3/5/10` 日 forward return
- 胜率
- 平均收益
- 中位数收益
- 平均最大冲高
- 平均最深回撤

## 已知边界

- 当前不是“卖方一致预期差”的严格版本，而是公开文本 + 同比增速的代理规则。
- `financial_report.report_date` 是报告期，不一定等于公告发布日期。
- 若上游数据源没有给出足够文本或同比字段，脚本会 fail-open 跳过该股票。
- 这条链路更适合“先留样本、后做统计”，不建议直接把命中结果当成交易信号。
