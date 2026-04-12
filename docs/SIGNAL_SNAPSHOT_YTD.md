# 信号快照年内涨幅统计

## 适用场景

当你已经有 `hundred_day_high` 这类已落库信号快照时，可以直接基于数据库里的历史命中结果统计“年内涨幅”，不需要重新全市场扫描。

这条链路适合做：

- 查看某天新高股的年内涨幅排行
- 对比不同新高样本的年内强弱分布
- 配合现有 `1/3/5/10` 日 forward return 一起看“短期表现 + 年内位置”
- 直接在 `/signals` 页面查看单只信号的年内涨幅

## 脚本入口

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_snapshot_ytd.py
```

默认行为：

- `signal_type` 默认是 `hundred_day_high`
- 若不传日期，会自动取该信号类型当前库里**最新一天**的快照
- 再按每只股票所在自然年的首个交易日收盘价，计算到信号日收盘价的涨幅

公式：

```text
年内涨幅 = (信号日收盘价 / 年初首个交易日收盘价 - 1) * 100%
```

## 常用命令

统计最新一批百日新高的年内涨幅：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_snapshot_ytd.py
```

指定某一天：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_snapshot_ytd.py --signal-date 2026-04-07
```

按 profile 过滤百日新高：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_snapshot_ytd.py --signal-date 2026-04-07 --profile momentum_strict
```

只看某几只股票：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\report_signal_snapshot_ytd.py --signal-date 2026-04-07 --codes 000586,000720,600488
```

## 输出结果

默认输出到 `data/`：

- `signal_snapshot_ytd_report.json`
- `signal_snapshot_ytd_report.md`
- `signal_snapshot_ytd_report.csv`

每行会包含：

- `signal_date`
- `code`
- `name`
- `year_start_date`
- `year_start_close`
- `signal_close`
- `ytd_return_pct`
- `previous_hit_count`

## `/signals` 页面联动

当前 `/signals` 已直接展示：

- 左侧信号卡片中的 `YTD`
- 右侧历史观察卡片中的“信号日年内涨幅”
- 历史命中列表中的 `YTD`
- 多日对比卡片中的 `平均 YTD / 中位 YTD`
- 当前页内的 `YTD > 0% / 20% / 50%` 快速筛选
- 页面内可直接切到 `新高且业绩` 组合视图，查看 `hundred_day_high ∩ earnings_surprise`

页面里的口径与脚本保持一致：

- 优先使用已落库快照中的 `close`
- 本地 `StockDaily` 有年初数据时直接计算
- 本地缺失时，再按需补拉该股票从年初到信号日的历史日线

## 已知边界

- 这里的“年内”按**信号所在自然年**计算，不是滚动 365 天。
- 若某只股票在该年信号日前没有足够日线数据，会标记为 `insufficient_data`。
- 信号日价格优先取快照里已落库的 `metrics_payload.close`；没有时再回退到本地 `StockDaily` 表。
- 这条统计链路只依赖**已落库快照 + 本地日线数据**，不会重新做全市场条件扫描。
