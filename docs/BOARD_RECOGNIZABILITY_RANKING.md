# 板块辨识度 TopN 快照

`scripts/collect_board_recognizability_rankings.py`

用于从已经落库的信号快照中，按板块归并出“当前各板块辨识度最强的前 N 只股票”，并将结果再次回写到 `kline_signal_snapshot`。

当前默认输入源是：

- `hundred_day_high`

这样可以直接复用现有的百日新高快照、归因和历史命中数据，不依赖一次性扫描全市场实时数据。

## 适用场景

- 想快速梳理“现在每个板块最有辨识度的股票”
- 想基于已有快照做二次排序，而不是重新跑一遍全市场扫描
- 想把“板块前三”也作为可查询、可回溯的快照写回数据库

## 用法

默认使用最新可用的 `hundred_day_high` 快照，每个板块保留前 3 名：

```bash
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_board_recognizability_rankings.py
```

指定来源信号、来源日期和 TopN：

```bash
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_board_recognizability_rankings.py --source-signal-type hundred_day_high --snapshot-date 2026-04-09 --top-n 3
```

只导出文件，不写数据库：

```bash
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_board_recognizability_rankings.py --skip-db-persist
```

## 输出

默认输出目录：

`data/board_recognizability_rankings/<snapshot_date>/`

包含：

- `board_recognizability_topn.csv`
- `board_recognizability_topn.txt`
- `board_recognizability_topn.md`

## 入库规则

每个板块会单独生成一个 `signal_type`，格式为：

`board_recognizability__<safe_name>_<hash>`

这样同一只股票即使同时属于多个板块，也可以在同一天分别落到不同板块的 TopN 快照里，不会和 `signal_type + signal_date + code` 的唯一键冲突。

每条快照会附带：

- `board_rank`
- `board_candidate_count`
- 来源 `source_signal_type`
- 来源快照日期 `source_signal_date`
- 来源快照里的 `reason_summary / theme_label / technical_logic`
- 该股票在同一板块 TopN 快照中的历史出现次数

## 排序口径

脚本会优先使用结构化高辨识度字段；如果来源快照没有这些字段，再回退到更通用的强势特征：

- `leader_probability`
- `recognizability_score`
- `logic_consensus_score`
- `capital_consensus_score`
- `sector_leadership_score`
- `relative_strength_score`
- `liquidity_score`
- `catalyst_score`
- `previous_hit_count`
- 是否连续命中
- `total_market_cap`
- `latest_high / close`

对 `hundred_day_high` 这类来源，通常更依赖：

- 历史重复命中次数
- 是否连续命中
- 市值辨识度
- 近期创新高强度

## 定时调度

如需在 `schedule` 模式下每日分析完成后自动刷新板块辨识度快照，可配置：

```env
BOARD_RECOGNIZABILITY_SNAPSHOT_ENABLED=true
BOARD_RECOGNIZABILITY_SNAPSHOT_SOURCE_SIGNAL_TYPE=hundred_day_high
BOARD_RECOGNIZABILITY_SNAPSHOT_TOP_N=3
```

执行顺序为：

1. 常规定时分析
2. 可选百日新高快照刷新
3. 板块辨识度 TopN 快照刷新
4. 可选模块主题核心快照刷新

## `/signals` 查询

落库后的 `board_recognizability__*` 快照会被 `/signals` 动态发现并自动生成板块辨识度 tab。

前端会展示这些结构化字段：

- `board_rank`
- `board_candidate_count`
- `source_signal_type`
- `source_signal_date`
- `total_market_cap_yi`
