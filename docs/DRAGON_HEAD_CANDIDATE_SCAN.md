# 龙头候选池扫描

## 1. 目标

这个脚本用于批量扫描 A 股，输出“高辨识度核心龙头候选池”。

它不是简单找板块强势股，而是按以下固定优先级筛选和排序：

`辨识度 > 板块地位 > 相对强度 > 流动性 > 催化 > 其它`

## 2. 脚本

脚本：

`scripts/select_dragon_head_candidates.py`

默认输出目录：

`data/dragon_head_candidates/`

输出内容：

- `dragon_head_candidates.csv`
- `dragon_head_candidates.txt`
- `dragon_head_candidates.md`
- A 股样本列表

## 3. 常用命令

扫描默认候选池：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_dragon_head_candidates.py
```

默认扫描会开启“快速模式”：
- 优先复用实时行情、板块排行和日线数据
- 只对更像候选的股票按需补抓主营资料与新闻催化
- 适合全市场或大样本初筛

调试时只扫前 100 只股票：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_dragon_head_candidates.py --limit 100
```

保留 `pseudo_leader` 一并观察：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_dragon_head_candidates.py --minimum-probability low --include-pseudo-leaders
```

关闭快速模式，使用更完整的证据抓取：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_dragon_head_candidates.py --full-analysis --enable-news-search
```

## 4. 默认过滤逻辑

默认保留：

- `leader_probability >= medium`
- `leader_type` 属于：
  - `hybrid_leader`
  - `logic_leader`
  - `capital_leader`

默认不保留：

- `pseudo_leader`

## 5. 输出解释

重点字段：

- `leader_probability`
- `leader_type`
- `recognizability_score`
- `logic_consensus_score`
- `capital_consensus_score`
- `sector_leadership_score`
- `relative_strength_score`
- `liquidity_score`
- `catalyst_score`

建议优先关注：

- `hybrid_leader + high`
- `logic_leader + high`
- `capital_leader + high`
- `medium` 但 `recognizability_score` 很高的票
