# 商品涨价专题快照

## 1. 目标

这个脚本用于把“商品涨价受益候选池”按日期落库到现有 `kline_signal_snapshot` 体系。

这样做的意义是：

- 后续可以直接复用现有快照查询、历史回看、导出和对比模式
- 先把专题数据沉淀下来，再接 `/signals` 会更稳

## 2. 脚本

脚本：

`scripts/collect_commodity_beneficiary_snapshots.py`

默认 signal type 前缀：

`commodity_beneficiary`

实际落库时会形成：

- `commodity_beneficiary__optical_fiber`
- `commodity_beneficiary__memory`
- `commodity_beneficiary__hard_disk`

## 3. 常用命令

采集当天光纤专题快照：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_commodity_beneficiary_snapshots.py --commodities optical_fiber
```

采集内存和硬盘专题，并保留渠道型受益：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_commodity_beneficiary_snapshots.py --commodities memory,hard_disk --include-distribution
```

指定快照日期：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_commodity_beneficiary_snapshots.py --commodities optical_fiber --snapshot-date 2026-04-10
```

只导出文件，不写数据库：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\collect_commodity_beneficiary_snapshots.py --commodities optical_fiber --skip-db-persist
```

## 4. 落库内容

每个命中的股票会写入：

- `criteria_payload`
  - 商品专题
  - 最低概率阈值
  - 是否保留分销型
  - 是否保留反例
  - 是否开启新闻搜索

- `metrics_payload`
  - `commodity_key`
  - `subtheme_key`
  - `chain_role`
  - `pass_through_direction`
  - `earnings_validation_status`
  - `earnings_release_probability`
  - `directness`
  - `scores`

- `cause_payload`
  - `reason_summary`
  - `industry_logic`
  - `theme_label`

- `history_payload`
  - `previous_hit_count`
  - `latest_previous_hit_date`
  - `days_since_previous_hit`

## 5. 输出文件

除了数据库落库，脚本还会输出：

- 候选池 `csv`
- 候选池 `txt`
- 候选池 `md`
- 当次落库记录 `snapshot_records.csv`
- A 股样本列表

## 6. 推荐流程

推荐的实际使用顺序：

1. 先用 `collect_commodity_beneficiary_snapshots.py` 做日度专题快照
2. 观察一段时间，累计命中样本
3. 直接在 `/signals` 里切换到对应专题 tab 做展示和横向对比

这样能避免前端先行、数据层后补带来的返工。

## 7. `/signals` 集成

当前专题快照已经正式接入 `/signals`，支持：

- `commodity_beneficiary__optical_fiber`
- `commodity_beneficiary__memory`
- `commodity_beneficiary__hard_disk`

页面会直接展示：

- `subtheme`
- `chain_role`
- `pass_through_direction`
- `earnings_release_probability`
- `directness`
- `matched_example_bucket`
