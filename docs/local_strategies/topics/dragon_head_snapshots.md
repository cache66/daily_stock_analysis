# 龙头专题快照

## 1. 目标

这个脚本用于把龙头候选池按日期落库到现有 `kline_signal_snapshot`。

落库后的好处：

- 可以复用现有快照查询与历史回看能力
- 后续更容易接到 `/signals`
- 能形成“龙头主题”的连续样本

## 2. 脚本

脚本：

`scripts/collect_dragon_head_snapshots.py`

默认 signal type：

`dragon_head_candidate`

## 3. 常用命令

采集当天龙头快照：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_dragon_head_snapshots.py
```

指定快照日期：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_dragon_head_snapshots.py --snapshot-date 2026-04-11
```

包含 `pseudo_leader` 一并落库：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_dragon_head_snapshots.py --minimum-probability low --include-pseudo-leaders
```

## 4. 落库内容

`metrics_payload` 重点包含：

- `leader_probability`
- `leader_type`
- `recognizability_score`
- `logic_consensus_score`
- `capital_consensus_score`
- `sector_leadership_score`
- `relative_strength_score`
- `liquidity_score`
- `catalyst_score`
- `ranking_tuple`
- `factor_breakdown`

`history_payload` 包含：

- `previous_hit_count`
- `latest_previous_hit_date`
- `days_since_previous_hit`

## 5. 推荐流程

推荐顺序：

1. 先用 `select_dragon_head_candidates.py` 看当前龙头候选池
2. 再用 `collect_dragon_head_snapshots.py` 做按日期落库
3. 后续如果需要，再把它接到 `/signals` 做横向对比

## 6. `/signals` 集成

当前龙头专题快照已经接入 `/signals`，使用的 signal type 是：

- `dragon_head_candidate`

页面会直接展示：

- `leader_type`
- `leader_probability`
- `recognizability_score`
- `logic_consensus_score`
- `capital_consensus_score`
- `sector_leadership_score`
- `relative_strength_score`
- `liquidity_score`
- `catalyst_score`
