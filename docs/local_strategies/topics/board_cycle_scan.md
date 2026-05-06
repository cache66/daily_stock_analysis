# 板块周期扫描（board_cycle_scan）

## 1. 目标

这条专题链路用于按指定板块批量扫描成分股，输出两个层面的结果：

- 板块层：当前板块是否处于 `strengthening / watch / idle` 的周期状态
- 个股层：板块内哪些股票更像 `leader / earnings_supported / watch`

它适合回答：

- 指定板块现在是不是整体在转强
- 板块里哪些股票既有龙头特征，也有业绩或基本面支撑
- 同一只股票同时属于多个板块时，当前更该归到哪个 `primary_board`

## 1.1 当前定位

当前把 `board_cycle_scan` 定位为“半自动专题工具”，而不是全自动主策略。

当前共识：

- 板块池和板块成分仍然需要人工介入维护
- 脚本负责扫描、打分、候选输出、版本跟踪和留痕
- 结果适合专题复盘和人工跟踪，不适合作为全市场自动选板块入口
- 在板块池来源没有显著改善前，这条线先按“可用半成品”维护，不继续强推全自动化

## 2. 脚本与服务

脚本：
`scripts/select_board_cycle_candidates.py`

服务：
`src/services/board_cycle_scan_service.py`

辅助模板脚本：
`scripts/generate_board_universe_template.py`

默认输出目录：
`data/board_cycle_scan/`

默认模板文件：
`data/templates/board_cycle_scan/board_universe_template.csv`

这是按需运行的专题扫描，不进入默认每日主链路，也不直接改动 `/signals` 默认口径。

现在额外支持两类本地兜底：

- `--board-universe-file`
  - 直接从本地 CSV 读取板块成分股，绕过远端板块成分接口。
- `--use-local-cache` + `--board-universe-cache-dir`
  - 先尝试远端拉取；若为空或失败，则回退到本地 cache 目录下的板块成分 CSV。

现在还支持一条更稳定的“项目内官方 seed”入口：

- 官方 seed 文件：
  - `data/board_cycle_scan_seed/board_universe.csv`
- 官方 seed 元信息：
  - `data/board_cycle_scan_seed/board_universe_meta.json`
- 当未显式传 `--board-universe-file` 时：
  - `scripts/select_board_cycle_candidates.py` 会优先自动复用这份官方 seed
  - `run_summary.txt` 会记录 `board_universe_file_mode=official_seed`

## 3. 常用命令

扫描多个板块：

```powershell
python scripts/select_board_cycle_candidates.py --boards 锂矿,白酒
```

限制每个板块只取前 20 个成分股做本地 smoke：

```powershell
python scripts/select_board_cycle_candidates.py --boards 锂矿,白酒 --limit-per-board 20 --output-dir data/manual_runs/board_cycle_scan_smoke_20260501
```

指定板块类型并控制每个板块保留的候选数量：

```powershell
python scripts/select_board_cycle_candidates.py --boards 锂矿 --board-type concept --top-per-board 3
```

先生成本地板块成分模板，再人工补齐可选字段：

```powershell
python scripts/generate_board_universe_template.py --boards 锂矿,猪肉 --board-type concept --output-file data/manual_runs/board_universe_template_smoke_20260502.csv
```

使用本地板块成分文件做离线 smoke：

```powershell
python scripts/select_board_cycle_candidates.py --boards 锂矿,猪肉 --board-type concept --board-universe-file data/manual_runs/board_cycle_scan_golden_smoke_20260502/board_universe.csv --output-dir data/manual_runs/board_cycle_scan_golden_smoke_20260502/output
```

只开 cache 回退做“远端失败但本地可跑”的 smoke：

```powershell
python scripts/select_board_cycle_candidates.py --boards 锂矿,猪肉 --board-type concept --board-universe-cache-dir data/manual_runs/board_cycle_scan_golden_smoke_20260502/cache --use-local-cache --output-dir data/manual_runs/board_cycle_scan_golden_smoke_20260502/output_cache_fallback
```

使用项目内官方 seed 直接扫描，不再显式传 `--board-universe-file`：

```powershell
python scripts/import_board_universe_seed.py --input-file data/manual_runs/board_cycle_scan_active_boards_20260502/board_universe_from_board_recognizability.csv --source-label recognizability_manual
python scripts/select_board_cycle_candidates.py --boards 半导体,通信设备 --board-type industry --limit-per-board 2 --output-dir data/manual_runs/board_cycle_scan_seed_default_smoke_20260502
```

## 4. 输出内容

脚本会输出：

- `board_summary.csv`
- `board_summary.md`
- `board_stock_candidates.csv`
- `board_stock_candidates.md`
- `run_summary.txt`

如果某个板块本次没有拿到可用成分股，脚本现在会自动把 warning 写进：

- `run_summary.txt`
- `board_summary.md`

板块层重点字段：

- `board_cycle_score`
- `board_cycle_label`
- `leader_count`
- `earnings_supported_count`
- `leader_ratio`
- `earnings_supported_ratio`
- `board_reason_summary`
- `top_leaders`
- `top_earnings_supported`

个股层重点字段：

- `stock_role`
- `board_stock_score`
- `board_rank`
- `selection_reason`
- `logic_match_score`
- `board_leader_score`
- `earnings_support_score`
- `primary_board`
- `primary_board_reason`
- `related_boards`

## 5. 当前实现口径

当前实现是轻量级专题扫描，优先复用已有数据与基本面上下文：

- `board_cycle_score = breadth_score + leadership_score + earnings_support_score + structure_score`
- `leader` 需要同时具备板块龙头提示、逻辑匹配和正向业绩支撑
- `earnings_supported` 用于标记有明确业绩/质量支撑但不一定已是板块龙头的样本
- `watch` 表示与板块逻辑有关，但当前证据还不够强

同一只股票允许属于多个板块，但最终会给出：

- `primary_board`
- `related_boards`

这样既保留多板块联动事实，也避免重点名单重复膨胀。

限制说明：

- 当板块成分股拉取失败时，产物里出现的 `board_cycle_label=idle`、`constituent_count=0` 和空候选，不代表真实市场状态，只代表本次上游未成功取到板块成分股。

## 6. 本地输入文件格式

`--board-universe-file` 最低只需要下面 3 列：

- `board_name`
- `code`
- `name`

可选增强列：

- `board_type`
- `logic_keywords`
  - 支持用 `;` 或 `,` 分隔
- `leader_candidates`
  - 支持用 `;` 或 `,` 分隔
- `belong_boards`
  - 用于本地覆写多板块归属，避免再去远端查 `get_belong_boards`
- `revenue_yoy`
- `net_profit_yoy`
- `earnings_report_date`
- `earnings_quality_verdict`
- `earnings_quality_score_total`
- `earnings_cycle_phase`

如果这些可选基本面列已经给全，脚本会直接用本地覆写，不再为这些字段去远端补抓上下文。

`scripts/generate_board_universe_template.py` 的用途就是先帮你把：

- `board_name`
- `board_type`
- `code`
- `name`

这几列尽量自动拉出来；如果远端拉取失败，它也会保留该板块一行 `needs_manual_fill` 的占位记录，方便你继续人工补齐，而不是整个流程直接中断。

## 7. 使用建议

这条链路更适合专题复盘或人工验证，不适合直接替代默认每日快复盘：

1. 先明确要看的板块，再按需扫描，而不是每天全市场自动跑。
2. 先看 `board_summary.*` 判断板块是否值得继续跟踪，再看 `board_stock_candidates.*` 读个股。
3. 若要做更大范围验证，优先先调 `--limit-per-board`，再扩大样本。
4. 如果目的是验证策略逻辑，而不是验证上游板块接口，优先使用 `--board-universe-file` 跑离线 golden smoke。
# 8. 版本跟踪输出补充

为了让 `board_cycle_scan` 更像“板块跟踪”而不是一次性静态扫描，脚本现在会在常规产物之外额外写出：

- `board_change_summary.csv`
- `board_change_summary.md`
- `board_tracking_history.csv`

对比基线规则：

1. 如果当前 `output_dir` 里已经存在上一轮的 `board_summary.csv`，优先和同目录旧结果对比。
2. 否则会在同级目录里寻找最近一轮、且和本轮 `board_name` 有交集的扫描结果，作为上一轮基线。
3. 如果两种情况都没有命中，则本轮记为 `is_first_run=true`。

`board_updated=true` 的触发条件：

- `board_cycle_label` 变化
- `abs(board_cycle_score_delta) >= 2.0`
- `top_leaders` 变化
- `earnings_supported_count` 变化

连续跟踪同一批板块时，推荐把多次运行放在同一个父目录下，例如：

```powershell
python scripts/select_board_cycle_candidates.py --boards 锂矿,猪肉 --board-type concept --board-universe-file data/manual_runs/board_cycle_scan_golden_smoke_20260502/board_universe.csv --output-dir data/manual_runs/board_cycle_scan_tracking_smoke_20260502/run_001
python scripts/select_board_cycle_candidates.py --boards 锂矿,猪肉 --board-type concept --board-universe-file data/manual_runs/board_cycle_scan_golden_smoke_20260502/board_universe.csv --output-dir data/manual_runs/board_cycle_scan_tracking_smoke_20260502/run_002
```

这样第二轮会自动识别 `run_001` 为上一轮，并在 `run_002` 里生成变化摘要和累积历史。

## 9. 官方本地 seed 维护

如果你已经有一份人工维护过、质量更高的板块成分 CSV，当前推荐先导入到项目内固定位置，再让 `board_cycle_scan` 默认复用。

导入命令：

```powershell
python scripts/import_board_universe_seed.py --input-file data/manual_runs/board_cycle_scan_active_boards_20260502/board_universe_from_board_recognizability.csv --source-label recognizability_manual
```

默认落点：

- `data/board_cycle_scan_seed/board_universe.csv`
- `data/board_cycle_scan_seed/board_universe_meta.json`
- `data/board_cycle_scan_seed/run_summary.txt`

当前约定：

- 这份 seed 是 `board_cycle_scan` 的项目内主入口资产
- `manual_runs/` 下的 CSV 更适合作为导入来源或历史样本，不再作为长期主入口
- `board_universe_meta.json` 会记录：
  - `source_file`
  - `source_label`
  - `imported_at`
  - `expire_after_days`
  - `row_count`
  - `board_count`
- 当前建议维护口径仍然是 3 天一轮人工复核 / 覆盖导入
- 如果某轮板块定义变化较大，优先人工直接修 seed，再跑扫描；不要把扫描结果反向当成自动板块发现器

## 10. 板块池缓存补充

当前更推荐把“题材板块池”作为 `board_cycle_scan` 的独立前置资产维护，而不是每次扫描时都直接去远端找板块池。

独立刷新命令：

```powershell
python scripts/refresh_board_concept_pool.py --source auto --expire-after-days 3
```

默认输出目录：

- `data/board_concept_pool/board_concept_pool.csv`
- `data/board_concept_pool/board_concept_pool_meta.json`

当前约定：

- 板块池主来源为 `Tushare THS/DC`
- 默认缓存有效期为 3 天
- 若远端刷新失败但本地已有旧缓存，则保持 fail-open，继续保留旧缓存
- 这一层当前只管理“板块池清单”，还不负责每个板块的成分股缓存
