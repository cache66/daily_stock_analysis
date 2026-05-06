# 短线编排单机接入说明

最后更新：2026-05-03

## 1. 目标

本文只解决一件事：

如何在一台机器上，用当前工程编排本机的 `WonderTrader` 和 `FinGenius`，并把最终结果收口到当前工程里。

## 2. 推荐目录

建议保持三套目录分离：
- 当前工程：
  - `D:\bb\daily_stock_analysis`
- `WonderTrader`：
  - `D:\bb\WonderTrader`
- `FinGenius`：
  - `D:\bb\FinGenius`

建议三套独立虚拟环境：
- 当前工程一个 venv
- `WonderTrader` 一个 venv
- `FinGenius` 一个 venv

这样依赖最不容易打架。

当前建议 `FinGenius` 单独固定到 `Python 3.11`：

- 推荐解释器：
  - `D:\bb\FinGenius\.venv311\Scripts\python.exe`
- 2026-05-03 已验证这套环境可直接跑通：
  - `HotMoneyTool + ChipAnalysisTool` 轻量批量
  - `HotMoneyTool + ChipAnalysisTool + BigDealAnalysisTool` 单票全量

## 3. 当前工程侧已具备的能力

当前工程里的：
- [scripts/run_shortline_hub.py](/d:/bb/daily_stock_analysis/scripts/run_shortline_hub.py:1)

已经支持：
- `--mode stub`
- `--mode process`

其中 `process` 模式会：
1. 先调 `WonderTrader` 外部脚本
2. 再调 `FinGenius` 外部脚本
3. 合并结果
4. 输出：
   - `shortline_candidates.json`
   - `shortline_explanations.json`
   - `shortline_combined_results.json`
   - `shortline_report.md`
   - `run_summary.json`

`WonderTrader` 这一侧，当前更推荐的 `--wt-python-executable` 是当前工程自己的 Python，而不是 `D:\bb\WonderTrader\wtpy\.venv\Scripts\python.exe`。

原因很简单：
- bridge 会通过路径注入方式加载 `D:\bb\WonderTrader\wtpy`
- 但真实 `wondertrader_real_engine` helper 同时还要复用当前工程依赖
- 2026-05-03 实测中，直接用主工程 Python 可以稳定走到 `scan_source=wondertrader_real_engine`
- 如果强行用 `wtpy` 自己的 venv，通常还需要额外补 `PyYAML / chardet / numpy` 等依赖，否则会静默回退到 `wondertrader_cache_scan`

## 4. 外部脚本模板

仓库已内置两个桥接模板：
- [scripts/bridges/shortline_wondertrader_bridge_template.py](/d:/bb/daily_stock_analysis/scripts/bridges/shortline_wondertrader_bridge_template.py:1)
- [scripts/bridges/shortline_fingenius_bridge_template.py](/d:/bb/daily_stock_analysis/scripts/bridges/shortline_fingenius_bridge_template.py:1)

当前推荐放到外部目录：
- `D:\bb\WonderTrader\bridge\wt_export_candidates.py`
- `D:\bb\FinGenius\bridge\fg_explain_candidate.py`

## 5. 手工调试顺序

### 第一步：先调 `WonderTrader` 外部脚本

拿这个样例文件：
- `data/templates/shortline_hub/wt_request_example.json`

让外部脚本写出候选 JSON，确认字段契约正确。

### 第二步：再调 `FinGenius` 外部脚本

拿这个样例文件：
- `data/templates/shortline_hub/fg_request_example.json`

让外部脚本写出解释 JSON，确认字段契约正确。

### 第三步：再回到当前工程统一编排

等两个外部脚本都能单独跑通之后，再执行：

```powershell
python scripts/run_shortline_hub.py `
  --mode process `
  --trade-date 2026-05-02 `
  --top-n 10 `
  --run-id shortline_real_20260502 `
  --output-dir data/manual_runs/shortline_real_20260502 `
  --wt-python-executable python `
  --wt-script-path D:\bb\WonderTrader\bridge\wt_export_candidates.py `
  --wt-workdir D:\bb\WonderTrader `
  --wt-runtime-dir data\runtime\shortline_hub\wondertrader `
  --fg-python-executable D:\bb\FinGenius\.venv311\Scripts\python.exe `
  --fg-script-path D:\bb\FinGenius\bridge\fg_explain_candidate.py `
  --fg-workdir D:\bb\FinGenius `
  --fg-runtime-dir data\runtime\shortline_hub\fingenius
```

## 11. 2026-05-04 follow-up

- `scripts/run-shortline-review-bundle.ps1` now supports wrapper-level `-TrackingHistoryPath`, so the PowerShell bundle entry can replay against the same cross-day tracking history file used by earlier daily serial validation.
- `scripts/run-shortline-daily.ps1` now supports wrapper-level `-TrackingHistoryPath`, so a real serial replay can explicitly reuse the same tracking history file across days.
- `scripts/run_shortline_review_bundle.py` now surfaces `tracking_repeat_symbol_count` and `tracking_longest_streak_days` in root-level `bundle_manifest.json` and `bundle_report.md`.
- Real serial validation has been completed on two sample pairs:
  - `2026-05-02 -> 2026-05-03`
  - `2026-05-03 -> 2026-05-04`
- In both pairs, tracking history artifacts landed correctly, but no natural repeated symbols appeared because the upstream candidate source still switched between `wondertrader_real_engine` and `wondertrader_export`.

如果只是日常轻量复盘，保持这套写法即可；只有需要单票深挖时再追加：

```powershell
--fg-enable-big-deal
```

## 5.1 一键入口脚本

如果当前机器目录就是这套默认布局：
- `D:\bb\daily_stock_analysis`
- `D:\bb\WonderTrader`
- `D:\bb\FinGenius`

现在可以直接使用仓库内两个 PowerShell 包装脚本，不需要每次手写完整参数：

日常轻量批量复盘：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-shortline-daily.ps1
```

默认行为：
- `trade_date=当天`
- `top_n=5`
- `run_id=shortline_daily_YYYYMMDD`
- `FinGenius` 默认使用 `D:\bb\FinGenius\.venv311\Scripts\python.exe`
- 不开启 `--fg-enable-big-deal`

单票或小样本全量核验：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-shortline-fullcheck.ps1
```

默认行为：
- `trade_date=当天`
- `top_n=1`
- `run_id=shortline_fullcheck_YYYYMMDD`
- 自动追加 `--fg-enable-big-deal`

两个脚本都会先检查：
- 当前仓库 `scripts/run_shortline_hub.py`
- `D:\bb\WonderTrader\bridge\wt_export_candidates.py`
- `D:\bb\FinGenius\bridge\fg_explain_candidate.py`
- `D:\bb\FinGenius\.venv311\Scripts\python.exe`

如果路径不完整，会直接 fail-fast，避免跑到一半才发现环境缺失。

## 5.2 近几次结果汇总

如果你想快速看最近几次 shortline daily/fullcheck 的稳定性，而不是一份份点开 `manual_runs`，现在可以直接跑：

```powershell
python scripts/summarize_shortline_runs.py `
  --runs-root data/manual_runs `
  --output-dir data/manual_runs/shortline_runs_summary_latest `
  --limit 12
```

默认会输出：
- `shortline_runs_summary.json`
- `shortline_runs_summary.md`

这个汇总会聚合：
- 最近 run 列表
- 每次 run 的 `candidate_count`
- 每次 run 的 `total_explain_elapsed_ms / avg_explain_elapsed_ms`
- 每次 run 的 `explain_cache_hit_count / explain_cache_miss_count / explain_parallel_workers`
- 每次 run 的轻量异常标签（`cache_hit_ratio_low / parallel_workers_single / tool_errors_present`）
- 每次 run 实际命中的 upstream tools
- 每次 run 前几只样本代码

适合用来快速回答这几个问题：
- 最近 daily 结果是不是稳定
- `BigDealAnalysisTool` 到底用了几次
- 哪些 run 明显更慢
- 哪些 run 基本都走 explain cache，哪些 run 主要是实时解释
- 哪些 run 已经出现了解释链路退化信号
- 候选池是不是总在重复同一批股票

## 5.3 单入口 bundle

如果你想把“短线日跑 + 最近结果汇总 + 快复盘短线观察”一次性收口到固定目录，现在可以直接跑：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-shortline-review-bundle.ps1
```

默认行为：
- `trade_date=当天`
- `top_n=3`
- 先执行真实 `shortline_hub process mode`
- 默认带 `--persist-snapshot`
- 然后执行 `scripts/summarize_shortline_runs.py`
- 最后执行 `scripts/run_fast_review_bundle.py --include-signals hundred_day_high`
- 默认 `fast_review` 也保留 snapshot 持久化；如只想看产物不写库，可加 `-SkipFastReviewPersistSnapshots`
- 如只想预演命令编排、检查路径和输出目录，不真正执行三步，可加 `-DryRun`

默认固定输出目录：
- `data/manual_runs/shortline_review_bundle_latest/shortline_run`
- `data/manual_runs/shortline_review_bundle_latest/shortline_runs_summary`
- `data/manual_runs/shortline_review_bundle_latest/fast_review`
- `data/manual_runs/shortline_review_bundle_latest/bundle_manifest.json`
- `data/manual_runs/shortline_review_bundle_latest/bundle_report.md`
- `data/manual_runs/shortline_review_bundle_latest/logs/`

其中 `bundle_manifest.json` 现在会显式记录：
- 每一步的 `status`
- 每一步的 `elapsed_ms`
- 每一步的 `workdir`
- 每一步的 `stdout/stderr` 日志路径
- `Shortline Runtime / Runs Summary Runtime` 两层根摘要（短线解释数量、分层分布、上游工具命中、最近 run 候选与 `top_symbols`）
- `shortline_report.md / run_summary.json / bundle_manifest.json` 中的 explain cache / 并发摘要（`explain_cache_enabled`、`explain_cache_mode`、`explain_cache_hit_count`、`explain_cache_miss_count`、`explain_parallel_workers`）
- `shortline_runs_summary.json/.md` 与 bundle 根层 `Runs Summary Runtime` 中的 recent-run 异常提醒（`cache_hit_ratio_low`、`parallel_workers_single`、`tool_errors_present`）
- `fast_review` 抽取后的结构化运行摘要（如 `signal_*_elapsed_sec`、`skipped_signal`、`total_signal_count`、`total_signal_elapsed_sec`、`skipped_reason_counts`）
- `artifact_governance`（输出类型、pointer 路径、latest 指针路径）

额外补充：
- bundle 父控制台现在不会再把成功子步骤的整段 stdout/stderr 原样回放到终端，而是只打印捕获摘要（行数 + 日志路径）；真正明细仍保留在 bundle 根目录 `logs/` 下。
- 如果 bundle 失败，根报告 `## Failed Step` 现在会额外写出失败 step 的 `stdout/stderr` 日志路径、最后几行 excerpt，以及仍处于 `pending` 的后续步骤。
- bundle 根报告的默认阅读顺序也已调整为：先看 `Shortline Runtime / Runs Summary Runtime / Fast Review Runtime`，再看各子目录路径、治理指针和步骤日志。
- `scripts/run_shortline_review_bundle.py` 当前默认会复用启动它的同一解释器作为 repo 子步骤 Python；PowerShell 包装 `scripts/run-shortline-review-bundle.ps1` 则会优先解析本机 `py -3.10`，并默认让 `WonderTrader` 也跟随这一路径。
- `scripts/run-shortline-review-bundle.ps1`、`scripts/run-shortline-daily.ps1`、`scripts/run-shortline-fullcheck.ps1` 现在共同点源 `scripts/shortline-wrapper-common.ps1`；解释器解析、路径 fail-fast 检查与 dry-run 前置输出统一收口，后续单机调路径时只需要改一处。
- `scripts/run-shortline-review-bundle.ps1` 已修复空 `RunId` 透传问题：如果未显式传 `-RunId`，不会再向 Python 入口塞一个空的 `--run-id` 参数。
- 同机上较早的两个入口 `scripts/run-shortline-daily.ps1` 与 `scripts/run-shortline-fullcheck.ps1` 现在也跟随同样的解释器解析逻辑，避免 bundle / daily / fullcheck 三个入口各自落到不同 Python 版本。
- `scripts/run-shortline-daily.ps1` 与 `scripts/run-shortline-fullcheck.ps1` 还新增了 wrapper 级 `-DryRun`：可直接打印解析后的 `run_shortline_hub.py` 真实命令，而不真正执行 `WonderTrader + FinGenius` 链路。
- `scripts/run_shortline_hub.py` 现在支持 `--enable-explain-cache / --explain-cache-path / --explain-cache-mode`；`run-shortline-daily.ps1`、`run-shortline-fullcheck.ps1` 与 `run-shortline-review-bundle.ps1` 默认都会启用 explain cache，并复用 `data/runtime/shortline_hub/explain_cache/shortline_explain_cache.json`。
  - 其中 `run-shortline-fullcheck.ps1` 会显式使用 `--explain-cache-mode full`，避免误复用 daily/light 解释缓存，保证 `BigDealAnalysisTool` 真实执行。

如果 bundle 中途失败：
- `bundle_manifest.json` 仍会落盘
- `bundle_report.md` 仍会生成
- 会额外写出 `failed_step / error_message / stderr_excerpt`
- 可先看 bundle 根目录 `logs/`，再决定要不要深入翻子目录产物

额外的治理约定：
- 固定索引根目录：`data/manual_runs/shortline_review_bundle_index/`
- 每次运行都会写：
  - `data/manual_runs/shortline_review_bundle_index/<trade_date>/<run_id>/bundle_pointer.json`
  - `data/manual_runs/shortline_review_bundle_index/latest.json`
- 即使本次 bundle 用的是自定义 `--output-dir`、smoke 目录或 dry-run 目录，也能从这个固定索引位置反查真实输出目录

如果不想走 PowerShell 包装，也可以直接运行 Python 入口：

```powershell
python scripts/run_shortline_review_bundle.py `
  --trade-date 2026-05-03 `
  --top-n 3 `
  --output-dir data/manual_runs/shortline_review_bundle_latest
```

常用附加参数：

```powershell
python scripts/run_shortline_review_bundle.py `
  --trade-date 2026-05-03 `
  --top-n 3 `
  --skip-fast-review-persist-snapshots `
  --dry-run `
  --output-dir data/manual_runs/shortline_review_bundle_latest
```

## 6. 当前建议

当前建议不要一步到位追求“真实策略逻辑全部连通”。
更稳的节奏是：
1. 外部脚本先能读 request / 写 output
2. 当前工程先能 `process mode` 跑通
3. 再逐步把外部脚本里的示例逻辑替换成真实业务逻辑

## 7. 当前状态

当前已经完成：
- `process mode` 支持
- 桥接模板脚本
- 外部目录骨架
- 模板 `process smoke`
- 外部 `workdir + 相对 runtime_dir` 路径问题修复
- `FinGenius` 外部 bridge 已能真实调用 upstream 单票工具
- `FinGenius` 独立 `Python 3.11` 环境已完成单票全量 + 批量轻量验证

当前还没完成：
- 真 `WonderTrader` 业务逻辑接入
- `FinGenius` 更大范围的上游能力整合（当前只收口到短线桥接所需工具）

## 8. `bridge_data` 半真实模式

当前 `D:\bb` 下的外部桥接脚本已经支持优先读取本地导出文件，所以即使你还没接上真实 `WonderTrader` / `FinGenius` 代码，也可以先手工准备一份真数据文件，让当前工程整链路跑起来。

`WonderTrader` 候选文件：
- `D:\bb\WonderTrader\bridge\bridge_data\wt_candidates_<trade_date>.json`
- `D:\bb\WonderTrader\bridge\bridge_data\wt_candidates_latest.json`
- `D:\bb\WonderTrader\bridge\bridge_data\wt_candidates_<trade_date>.csv`
- `D:\bb\WonderTrader\bridge\bridge_data\wt_candidates_latest.csv`

`FinGenius` 解释文件：
- `D:\bb\FinGenius\bridge\bridge_data\fg_explanations_<trade_date>.json`
- `D:\bb\FinGenius\bridge\bridge_data\fg_explanations_latest.json`

当前已同步两份示例文件：
- `D:\bb\WonderTrader\bridge\bridge_data\wt_candidates_2026-05-02.json`
- `D:\bb\FinGenius\bridge\bridge_data\fg_explanations_2026-05-02.json`

如果这些文件存在，桥接脚本会优先读取它们；如果不存在，才回退到内置 placeholder。

如果你想直接验证“同机半真实样本模式”和“fallback 模式”的区别，而不是手工一条条准备，可以直接运行：

```powershell
python scripts/run_shortline_bridge_data_compare.py `
  --bridge-data-trade-date 2026-05-04 `
  --fallback-trade-date 2026-05-03 `
  --top-n 2 `
  --seed-sample-data `
  --output-dir data/manual_runs/shortline_bridge_data_compare_demo_20260502 `
  --wt-python-executable python `
  --wt-script-path D:\bb\WonderTrader\bridge\wt_export_candidates.py `
  --wt-workdir D:\bb\WonderTrader `
  --wt-runtime-dir data/runtime/shortline_bridge_compare/wt_demo `
  --fg-python-executable python `
  --fg-script-path D:\bb\FinGenius\bridge\fg_explain_candidate.py `
  --fg-workdir D:\bb\FinGenius `
  --fg-runtime-dir data/runtime/shortline_bridge_compare/fg_demo
```

这个脚本会自动：
1. 写入一份 date-specific `bridge_data` 样本
2. 跑一遍 `bridge_data` 模式
3. 再跑一遍无样本 fallback 模式
4. 输出 `compare_summary.json` 和 `compare_report.md`

## 9. Real WonderTrader path

- 当前同机短线编排里，WonderTrader 候选优先级为：`bridge_data -> real engine helper -> repo spot cache -> placeholder`。
- `real engine helper` 指向当前工程的 `src/shortline_hub/wondertrader_real_engine.py`，不是占位逻辑。
- 该 helper 会调用真实 `wtpy WtBtEngine`，并通过 `BaseExtDataLoader` 直接喂入当前工程 `data/cache/history/cn/*.csv`。
- 当同日没有 `bridge_data` 时，`D:\bb\WonderTrader\bridge\wt_export_candidates.py` 会自动尝试加载这条真实引擎路径。
- 如果历史缓存未追平请求日期，最终结果会显式带出 `history_asof_<date>`，表示该信号来自最近可用历史缓存。
## 10. V1 Additions

当前 `shortline_hub` 在最初的同机 `process mode` 基础上，又补齐了几项更适合连续日常复盘的能力：

- 手工观察批量解释：
  - `--manual-watchlist --symbols 300083,688256`
  - `--manual-watchlist --symbols-file <txt/csv>`
- 同日 explain cache：
  - 同一交易日重复解释同一只股票时，优先复用当天缓存，减少重复外部解释调用
  - 默认缓存文件路径固定为 `data/runtime/shortline_hub/explain_cache/shortline_explain_cache.json`，因此日常复盘、fullcheck 和 bundle 可以共享同一份同日 explain cache
- 历史跟踪字段：
  - `shortline_combined_results.json` 里现在带有 `tracking_appear_streak_days`
  - `tracking_last_seen_dates`
  - `tracking_tier_transition`
  - `shortline_report.md` 新增 `今日主方向` 与 `历史跟踪摘要`
- snapshot 接入：
  - `scripts/run_shortline_hub.py --persist-snapshot`
  - 会把结果写入：
    - `shortline_top_pick`
    - `shortline_watchlist`
    - `shortline_high_risk_mover`
  - 同一 `trade_date` 下重复重跑时，这三组 shortline snapshot 现在按“本次结果整批替换”同步，不再把更早同日 run 的旧股票残留在库里
- fast review 接入：
  - `scripts/run_fast_review_bundle.py` 会优先读取同日短线 snapshot
  - 若同日 snapshot 不存在，则回退读取最近同日 `shortline_*` 的 `run_summary.json`
  - 最终把 `短线观察` 摘要附加到 `fast_review_summary.md`
  - 如果当天没有 `top_pick / watchlist`、只有 `high_risk_mover`，摘要也会回退展示高风险异动里的股票名，而不是只显示 `top_symbols: none`

手工观察股 + 快照落库示例：

```powershell
python scripts/run_shortline_hub.py `
  --mode process `
  --trade-date 2026-05-03 `
  --manual-watchlist `
  --symbols 300083,688256 `
  --persist-snapshot `
  --run-id shortline_manual_watchlist_20260503 `
  --output-dir data/manual_runs/shortline_manual_watchlist_20260503 `
  --wt-python-executable python `
  --wt-script-path D:\bb\WonderTrader\bridge\wt_export_candidates.py `
  --wt-workdir D:\bb\WonderTrader `
  --wt-runtime-dir data\runtime\shortline_hub\wondertrader `
  --fg-python-executable D:\bb\FinGenius\.venv311\Scripts\python.exe `
  --fg-script-path D:\bb\FinGenius\bridge\fg_explain_candidate.py `
  --fg-workdir D:\bb\FinGenius `
  --fg-runtime-dir data\runtime\shortline_hub\fingenius
```
## 2026-05-04 Source-Mode Follow-up

- real daily replay should now use `wt_source_mode=prefer_real_engine` by default
- bridge-data-first behavior is kept only for explicit compare/demo flows
- this default is wired through:
  - `scripts/run_shortline_hub.py`
  - `scripts/run_shortline_review_bundle.py`
  - `scripts/run-shortline-daily.ps1`
  - `scripts/run-shortline-fullcheck.ps1`
  - `scripts/run-shortline-review-bundle.ps1`
- two-day real re-validation (`2026-05-03`, `2026-05-04`) confirmed:
  - both runs landed `scan_source_counts={"wondertrader_real_engine":5}`
  - the second run correctly produced `tracking_repeat_symbol_count=5` and `tracking_longest_streak_days=2`
