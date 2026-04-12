# 商品涨价受益候选池扫描

## 1. 目标

这个脚本用于批量扫描 A 股，输出“商品涨价受益候选池”。

相比 `/chat` 或 `/ask` 的单票分析，它更适合做：

- 每日或每周专题巡检
- 从全市场里先找一批更可能受益的候选
- 再回到单票模式做更深的验证

## 2. 配置目录

第三版开始，专题映射和样例表已经从代码常量抽到独立配置目录：

`config/commodity_pass_through/`

当前内置文件：

- `optical_fiber.json`
- `memory.json`
- `hard_disk.json`
- `copper.json`

每个文件包含：

- 商品大类关键词
- 产业链角色关键词
- `subthemes`
- 真实 A 股样例白名单 / 反例

后续如果要扩展更多专题，优先新增配置文件，而不是继续把规则写死在 Python 常量里。

## 3. 扫描脚本

脚本：

`scripts/select_commodity_beneficiaries.py`

默认支持的商品：

- `optical_fiber`
- `memory`
- `hard_disk`

默认输出目录：

`data/commodity_beneficiaries/`

每个专题会输出：

- `csv`
- `txt`
- `md`
- A 股样本列表

## 4. 常用命令

只扫光纤：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_commodity_beneficiaries.py --commodities optical_fiber
```

扫内存和硬盘，并允许保留渠道型受益：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_commodity_beneficiaries.py --commodities memory,hard_disk --include-distribution
```

调试时只扫前 100 只股票：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_commodity_beneficiaries.py --commodities optical_fiber --limit 100
```

开启新闻搜索做更强确认：

```powershell
E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe scripts\select_commodity_beneficiaries.py --commodities memory --enable-news-search
```

## 5. 默认筛选逻辑

默认保留：

- `earnings_release_probability >= medium`
- `chain_role` 为 `upstream` 或 `midstream`
- `directness` 为 `direct_beneficiary`

可选放宽：

- `--include-distribution`
  作用：把 `distribution` / `indirect_beneficiary` 也保留下来

默认不保留：

- 精确命中反例样例表的股票

可选放宽：

- `--include-counterexamples`

## 6. 输出解释

重点字段：

- `commodity_key`
- `subtheme_key`
- `chain_role`
- `pass_through_direction`
- `earnings_validation_status`
- `earnings_release_probability`
- `directness`
- `matched_example_bucket`

建议优先关注：

- `high + direct_beneficiary`
- `medium + direct_beneficiary`
- `medium + indirect_beneficiary` 且来自白名单样例

## 7. 当前限制

- 默认不启用新闻搜索，适合快扫，但细节确认会弱一些
- 结果仍是“候选池”，不是最终结论
- 更适合作为专题选股前置筛选，而不是直接替代单票深度分析

如果你要把结果按日期沉淀到数据库，而不是只看导出文件，请继续使用：

- `docs/COMMODITY_BENEFICIARY_SNAPSHOTS.md`
- `scripts/collect_commodity_beneficiary_snapshots.py`
