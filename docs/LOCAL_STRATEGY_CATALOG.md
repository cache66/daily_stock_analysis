# 本地策略目录（Local Strategy Catalog）

最后更新：2026-05-04

本文档回答三件事：

1. 当前仓库有哪些本地策略资产与脚本入口；
2. 默认每日主链路到底跑哪些策略；
3. 每条策略或专题现在应该去看哪份现行文档。

如果你只想看“我们自己维护的主策略中文文档”，直接从 [`docs/local_strategies/README.md`](./local_strategies/README.md) 进入。

## 1. 文档真源分工

建议按这个顺序读：

1. 本文：看资产清单、默认每日链路、脚本入口。
2. [`docs/local_strategies/README.md`](./local_strategies/README.md)：看我们自己的策略文档目录。
3. [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md)：看当前默认参数和基线口径。
4. [`docs/AI_MODIFICATION_LOG.md`](./AI_MODIFICATION_LOG.md)：看详细变更、验证命令、实跑证据。
5. [`docs/CHANGELOG.md`](./CHANGELOG.md)：看用户可见变化摘要。

当前本地策略文档统一收口到：

- `docs/local_strategies/core/`
- `docs/local_strategies/supporting/`
- `docs/local_strategies/topics/`

根目录不再维护另一套“主策略入口”。

## 2. 默认每日主链路

主入口：

```bash
python scripts/run_fast_review_bundle.py --strategy-profile-file config/local_strategy_profile.json
```

当前默认每日只保留 3 条核心策略：

- `earnings` -> `earnings_surprise`
- `hundred_day_high`
- `trend_leader` -> `trend_leader_unified`
- 每日快复盘阅读层会在 `fast_review_strategy_focus.csv/.md` 与 `fast_review_summary.md` 中，为焦点强势股补充上涨原因摘要与标签；优先复用已有 `reason_summary / cause_tags`，缺失时才做轻量补算，当前不写回 snapshot
- 2026-05-06 补充：上述 A 股上游快照脚本在手工传入非交易日 `--snapshot-date` 时，现会自动回退到最近一个 A 股交易日；例如 `2026-05-04` 会内部解析为 `2026-04-30`，避免短线日跑前置步骤硬跑休市日而拿不到应复用的上游快照。

默认不进入每日主循环：

- `monthly_slow_rise`
- `continuous_up_ratio`
- `continuous_up_streak`
- 各类专题扫描、专题快照和映射器

当前默认链路里的 `hundred_day_high` 已在“共享扫描壳 + 入选后 breakout-quality 并发补强”的结构下运行；这属于运行时优化，不改变信号类型或主入口。
`trend_leader_unified` 当前默认 `trend_max_workers=2`；当外层主扫描已启用 multi-worker 时，不再为单候选内部再起一层 `fundamental/capital` 线程池，以减少嵌套并发抖动。
`trend_leader_unified` 当前还会对弱趋势样本延迟初始化 `DragonHeadAnalysisService`，并把“边缘弱趋势 + 弱价量”样本更早短路出 `board/dragon/fundamental` 链路，以减少冷启动噪音。
`trend_leader_unified` 快扫还单独收紧了远端补全 budget：默认 `fundamental=0.6s`、`capital_flow=0.45s`，并支持用 `--fundamental-budget-seconds`、`--capital-flow-budget-seconds` 做诊断对照。
`trend_leader_unified` 共享前筛现在只会在 `pct_change` 整列都不可用时才触发行情补水；若列里已经有可用值，则不再为了少量空值逐票补 `quote`，继续按“缺字段不硬拦”的快扫口径优先控制长尾耗时。
`trend_leader_unified` 的快扫业绩上下文现已显式收窄为 `enabled_blocks=("financial",)`；同时 manager 会兼容复用旧的更宽 earnings cache，避免策略切到窄 block profile 后首轮完全失去 warm-cache 价值。
`trend_leader_unified` 的 `spot-enriched universe` 失败降级现在允许复用“已过 TTL 但结构完整”的本地 `spot` reference cache；该 cache 仅用于 live `spot` 失败时兜底，不会替代正常的新鲜数据优先级，但能减少异常日退回 generic provider 前的额外等待。
`trend_leader_unified` 当前还会显式开启 stale `spot` reference cache 的优先复用；这是趋势快扫的专项 opt-in，只用于缩短准备阶段，不改其他策略的默认 `spot` 新鲜度策略。
`trend_leader_unified` 的 `sector_rankings` 预热当前会优先请求 stale cache；对快扫而言，这部分只用于板块强弱辅助上下文，不是硬门槛，因此在 cache 过期但远端不稳时，优先复用本地旧结果比阻塞等待远端更划算。

## 3. 策略资产总表

| 层级 | 策略/信号 | 入口脚本 | 默认 `signal_type` | 默认每日 | 说明文档 |
| --- | --- | --- | --- | --- | --- |
| 核心 | `trend_leader_unified` | `scripts/select_trend_leader_candidates.py` | `trend_leader_unified` | 是 | [`docs/local_strategies/core/trend_leader_unified.md`](./local_strategies/core/trend_leader_unified.md) |
| 核心 | `earnings_surprise` | `scripts/select_earnings_surprise_candidates.py` | `earnings_surprise` | 是 | [`docs/local_strategies/core/earnings_surprise.md`](./local_strategies/core/earnings_surprise.md) |
| 核心 | `hundred_day_high` | `scripts/select_hundred_day_high_candidates.py` | `hundred_day_high` | 是 | [`docs/local_strategies/core/hundred_day_high.md`](./local_strategies/core/hundred_day_high.md) |
| 扩展 | `monthly_slow_rise` | `scripts/select_monthly_slow_rise_candidates.py` | `monthly_slow_rise` | 否 | [`docs/local_strategies/core/monthly_slow_rise.md`](./local_strategies/core/monthly_slow_rise.md) |
| 观察 | `continuous_up_ratio` | `scripts/run_fast_review_bundle.py`（内部收集） | `continuous_up_ratio` | 否 | [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md) |
| 观察 | `continuous_up_streak` | `scripts/run_fast_review_bundle.py`（内部收集） | `continuous_up_streak` | 否 | [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md) |
| 专题 | `dragon_head_candidate` | `scripts/select_dragon_head_candidates.py` | `dragon_head_candidate` | 否 | [`docs/local_strategies/topics/dragon_head_candidate_scan.md`](./local_strategies/topics/dragon_head_candidate_scan.md) |
| 专题 | `board_cycle_scan` | `scripts/select_board_cycle_candidates.py` | 无默认落库 `signal_type` | 否 | [`docs/local_strategies/topics/board_cycle_scan.md`](./local_strategies/topics/board_cycle_scan.md) |
| 专题 | `theme_core_mapper` | `scripts/select_theme_core_candidates.py` | `theme_core_mapper` | 否 | [`docs/local_strategies/topics/theme_core_mapper.md`](./local_strategies/topics/theme_core_mapper.md) |
| 专题 | `commodity_price_pass_through` | `scripts/select_commodity_beneficiaries.py` | `commodity_price_pass_through` | 否 | [`docs/local_strategies/topics/commodity_price_pass_through.md`](./local_strategies/topics/commodity_price_pass_through.md) |
| 工具 | 通用 K 线筛选 | `scripts/select_kline_candidates.py` | 无固定落库命名 | 否 | [`docs/KLINE_SELECTOR_GUIDE.md`](./KLINE_SELECTOR_GUIDE.md) |

## 4. Supporting 文档索引

这些文档不直接代表一条独立每日策略，但属于主策略体系的共用说明：

| 文档 | 作用 |
| --- | --- |
| [`docs/local_strategies/supporting/main_strategy_blueprint.md`](./local_strategies/supporting/main_strategy_blueprint.md) | 定义主策略长期收敛框架。 |
| [`docs/local_strategies/supporting/capital_profile.md`](./local_strategies/supporting/capital_profile.md) | 统一资金层字段、评分与使用边界。 |
| [`docs/local_strategies/supporting/earnings_strategy_breakdown.md`](./local_strategies/supporting/earnings_strategy_breakdown.md) | 拆解 `earnings_surprise` 的字段、权重、门槛和快照。 |
| [`docs/local_strategies/supporting/earnings_playbook.md`](./local_strategies/supporting/earnings_playbook.md) | 业绩线的判读与复盘手册。 |
| [`docs/local_strategies/supporting/earnings_quality_signal.md`](./local_strategies/supporting/earnings_quality_signal.md) | 业绩质量子评分的补充说明。 |

## 5. Topics 文档索引

这些文档服务题材、专题和快照沉淀，不进入默认每日主链路：

| 文档 | 作用 |
| --- | --- |
| [`docs/local_strategies/topics/board_cycle_scan.md`](./local_strategies/topics/board_cycle_scan.md) | 按指定板块输出板块周期评分、板块龙头、业绩支撑股与 `primary_board/related_boards` 归属结果；支持 `--board-universe-file` 本地输入、`--use-local-cache` 板块成分缓存回退，并新增项目内官方 seed `data/board_cycle_scan_seed/board_universe.csv` 作为默认入口。当前定位是“半自动专题工具”：板块池仍需人工维护，脚本负责扫描、打分和留痕，不作为全自动主策略入口。 |
| [`docs/local_strategies/topics/board_recognizability_ranking.md`](./local_strategies/topics/board_recognizability_ranking.md) | 板块辨识度排行。 |
| [`docs/local_strategies/topics/commodity_beneficiary_scan.md`](./local_strategies/topics/commodity_beneficiary_scan.md) | 商品涨价受益候选池扫描。 |
| [`docs/local_strategies/topics/commodity_beneficiary_snapshots.md`](./local_strategies/topics/commodity_beneficiary_snapshots.md) | 商品涨价专题候选按日落库。 |
| [`docs/local_strategies/topics/commodity_price_pass_through.md`](./local_strategies/topics/commodity_price_pass_through.md) | 商品涨价到业绩传导的专题策略。 |
| [`docs/local_strategies/topics/dragon_head_candidate_scan.md`](./local_strategies/topics/dragon_head_candidate_scan.md) | 高辨识度龙头候选扫描。 |
| [`docs/local_strategies/topics/dragon_head_snapshots.md`](./local_strategies/topics/dragon_head_snapshots.md) | 龙头候选专题快照落库。 |
| [`docs/local_strategies/topics/dragon_head_strategy.md`](./local_strategies/topics/dragon_head_strategy.md) | 龙头识别专题说明。 |
| [`docs/local_strategies/topics/theme_core_board_scan.md`](./local_strategies/topics/theme_core_board_scan.md) | 模块层主题核心扫描。 |
| [`docs/local_strategies/topics/theme_core_board_snapshots.md`](./local_strategies/topics/theme_core_board_snapshots.md) | 模块层主题核心快照。 |
| [`docs/local_strategies/topics/theme_core_candidate_scan.md`](./local_strategies/topics/theme_core_candidate_scan.md) | 题材核心候选扫描。 |
| [`docs/local_strategies/topics/theme_core_mapper.md`](./local_strategies/topics/theme_core_mapper.md) | 题材核心映射器说明。 |

## 6. 和 `/signals` 的关系

这些脚本写入 `kline_signal_snapshot` 后，会出现在 `/signals` 页面。

常用 API：

- `GET /api/v1/signals/kline-snapshots?signal_type=<type>&signal_date=<YYYY-MM-DD>`
- `GET /api/v1/signals/kline-snapshot-counts?signal_date=<YYYY-MM-DD>`

## 7. 维护约定

涉及本地策略资产改动时，按 `AGENTS.md` 同步更新：

- [`docs/LOCAL_STRATEGY_CATALOG.md`](./LOCAL_STRATEGY_CATALOG.md)
- [`docs/LOCAL_STRATEGY_BASELINE.md`](./LOCAL_STRATEGY_BASELINE.md)
- [`docs/AI_MODIFICATION_LOG.md`](./AI_MODIFICATION_LOG.md)
- [`docs/CHANGELOG.md`](./CHANGELOG.md)

如果文档和代码冲突，以脚本与实现为准。
`hundred_day_high` 补充约定：如果未显式传 `--checkpoint-path`，默认 checkpoint 跟随各自 `output_dir` 落到 `hundred_day_high_checkpoint.json`，避免 bundle 与手工任务并发时共用根目录 checkpoint。
`hundred_day_high` 进一步补充：如果两个运行中的任务误用同一个 `output_dir`，当前脚本会基于 `hundred_day_high_run.lock` 直接 fail-fast，而不是继续共享同一目录。
`fast_review bundle` 补充约定：共享 `--limit` 不再下传给 `hundred_day_high` 主筛选；聚合层仅在载入结果后按 `--hundred-day-output-limit`（默认 `30`，`0` 表示不裁剪）限制导出行数，避免小 `limit` 把百日新高输入 universe 压缩成假 `no_rows`。

## 8. 板块池缓存工具补充

为 `board_cycle_scan` 额外新增一个独立工具脚本：

- `scripts/import_board_universe_seed.py`
  - 把人工维护的板块成分 CSV 导入到项目内官方 seed 路径
  - 默认写入 `data/board_cycle_scan_seed/board_universe.csv`
  - 同步写入 `data/board_cycle_scan_seed/board_universe_meta.json`
  - 同步写入 `data/board_cycle_scan_seed/run_summary.txt`
  - `scripts/select_board_cycle_candidates.py` 在未显式传 `--board-universe-file` 时会优先复用这份 seed
- `scripts/refresh_board_concept_pool.py`
  - 用 `Tushare THS/DC` 维护本地题材板块池缓存
  - 默认写入 `data/board_concept_pool/board_concept_pool.csv`
  - 同步写入 `data/board_concept_pool/board_concept_pool_meta.json`
  - 默认 3 天刷新一次
  - 远端刷新失败但本地已有旧缓存时保持 fail-open

这层当前还是“前置资产管理”，不是默认每日复盘主链路的一部分。
## 9. Shortline Hub

`shortline_hub` 当前定位是“按需运行的短线复盘/解释编排层”，不进入默认每日主链路，但已经和主工程的查询与快复盘打通。

- 入口脚本：`scripts/run_shortline_hub.py`
- 单入口 bundle：
  - `scripts/run_shortline_review_bundle.py`
  - `scripts/run-shortline-review-bundle.ps1`
- 主要模式：
  - `--mode process`
  - `--manual-watchlist --symbols ...`
  - `--manual-watchlist --symbols-file ...`
  - `--persist-snapshot`
- 默认写入的短线 snapshot 类型：
  - `shortline_top_pick`
  - `shortline_watchlist`
  - `shortline_high_risk_mover`
- 关联文档：
  - [`docs/architecture/2026-05-02-shortline-hub-single-machine-setup.md`](./architecture/2026-05-02-shortline-hub-single-machine-setup.md)

补充说明：
- 2026-05-05 再补充：`shortline_hub` 已把“只有少量近期历史条目缺失 `volume`、但 `amount` 放量比例可验证”和“整段历史都要靠 `amount` 重建量能”拆开处理。前者改标为 `volume_ratio_validated_by_amount_history`，不再统一压成 `watchlist`；后者仍保留 `volume_reconstructed_from_amount` 的谨慎分层。
- 2026-05-05 再补充：`shortline_hub` 新增 `driver_support` 逻辑支撑层，用来区分强逻辑驱动、题材接力和纯资金接力；目前只有业绩、涨价链和真正的产业突破会争取 `top_pick`，模板化的“扩散/情绪锚点”默认只会落到 `watchlist`，避免把弱主题误当成强逻辑。
- 2026-05-04 补充：`shortline_hub` 的真实 `WonderTrader` 回放现在会先把历史行情截到 `<= trade_date` 再计算候选，避免历史回放日误带出 `history_asof_*`；同时 `volume_reconstructed_from_amount` 调整为“保留扣分、不再一票打入 high_risk_mover”，让真实强势股在数据可接受时仍可进入 `watchlist/top_pick`。
- 2026-05-05 补充：如果手工把 `trade_date` 设在节假日，而本地 `tushare_trade_cal_sse.csv` 又刚好误标或缺失，`shortline_hub` 现在会优先用 AkShare 交易日历兜底非交易日判断；`2026-05-04` 这类休市日不再因为 `engine_asof=2026-04-30` 被误打成 `history_asof_* -> high_risk_mover`。
- 2026-05-04 再补充：为了避免重建量能样本被抬得过高，带有 `volume_reconstructed_from_amount` 的候选现在最多只进入 `watchlist`，不再直接进入 `top_pick`。
- 2026-05-04 再补充：`shortline_hub` 的历史跟踪闭环现已真正接通。重复出现的股票会在结果里写出 `tracking_appear_streak_days / tracking_last_seen_dates / tracking_tier_transition`，并在输出目录与 runtime 目录同时落 `shortline_tracking_history.json/csv`；同日手工重跑不会把 streak 虚增。

- `scripts/run_fast_review_bundle.py` 现在会优先读取同日短线 snapshot，并在 `fast_review_summary.md` 自动附加 `短线观察` 摘要。
- `scripts/run-shortline-replay-validation.ps1` 把短线历史回放验收固定成三段：`baseline -> cold -> warm`。默认示例就是 `2026-04-29 -> 2026-04-30 -> 2026-04-30 warm rerun`，三段共用同一份 explain cache 与 tracking history，适合集中验证 `tracking_repeat_symbol_count`、`quality_verdict` 与 cache warm-up 是否按预期收敛。
- `scripts/run_shortline_review_bundle.py` 会把 `短线日跑 -> shortline runs summary -> fast review` 三步收口到固定 latest 目录，便于单机每天直接查看同一位置。
- `scripts/summarize_shortline_runs.py` 现在除了汇总候选数、上游工具与解释耗时，还会把最近几次 run 的 `explain_cache_hit_count / explain_cache_miss_count / explain_parallel_workers` 一并写入 `shortline_runs_summary.json/.md`，更适合横向比较“是缓存命中变高了，还是实时解释变慢了”。
- `shortline_runs_summary.json/.md` 现在还会产出轻量异常提醒：首版包括 `cache_hit_ratio_low`、`parallel_workers_single` 与 `tool_errors_present`；`shortline review bundle` 根层 `Runs Summary Runtime` 也会直接透传这些汇总，便于先在根报告判断最近几次 run 是否有解释链路退化迹象。
- `scripts/run_shortline_review_bundle.py` 现已补齐 bundle 层控制面：可独立控制 `shortline` 与 `fast_review` 的 snapshot 持久化，并支持 `--dry-run` 只落命令与 manifest、不真正执行子步骤。
- `bundle_manifest.json` 现会显式记录每一步的 `status / elapsed_ms / workdir / stdout_log_path / stderr_log_path`，对应日志统一落在 bundle 目录下的 `logs/`，便于排查是 `shortline`、`runs_summary` 还是 `fast_review` 哪一步变慢或失败。
- `bundle_manifest.json` 与 `bundle_report.md` 现已补齐 `Shortline Runtime / Runs Summary Runtime` 两层紧凑摘要：可直接在 bundle 根看到本轮 `shortline` 的解释数量、分层分布、上游工具命中与解释耗时，以及最近 `runs_summary` 的交易日覆盖、工具命中、最新一轮候选数和 `top_symbols`。
- `shortline_report.md`、`run_summary.json` 以及 bundle 根层 `Shortline Runtime` 现在还会显式写出 explain cache / 并发摘要：包括 `explain_cache_enabled`、`explain_cache_mode`、`explain_cache_hit_count`、`explain_cache_miss_count` 与 `explain_parallel_workers`，便于判断这轮是主要复用缓存，还是主要耗在实时解释。
- `bundle_manifest.json` 现还会从 `fast_review.stdout.log` 里抽取结构化运行摘要，包括 `include_signals / persist_snapshots / signal_*_elapsed_sec / skipped_signal`，并补齐 `total_signal_count / total_signal_elapsed_sec / skipped_reason_counts` 这类 bundle 根层紧凑汇总；这样不用翻完整日志也能先看出到底是哪条快复盘信号最慢、这轮有没有真正筛出候选、哪些信号为何被跳过。
- `scripts/run_shortline_review_bundle.py` 的父控制台日志现已收敛：成功子步骤不再把整段 stdout/stderr 原样回放到 bundle 父进程，而是只打印 `stdout_lines / stderr_lines / stdout_log / stderr_log` 紧凑摘要，真实明细仍留在 bundle 根目录 `logs/` 下。
- 如果 bundle 中途失败，根目录 `bundle_report.md` 现在还会直接写出失败 step 的 `stdout/stderr` 日志路径、最后 excerpt 和 `pending_steps`，便于先在根目录判断“哪一步挂了、后面哪些步骤没跑”。
- `bundle_report.md` 的章节顺序也已收紧：现在默认先展示 `Shortline Runtime / Runs Summary Runtime / Fast Review Runtime` 三层摘要，再展示各子目录路径与 artifact 细节，更适合日常先扫结论、再决定是否下钻。
- `scripts/run_shortline_review_bundle.py` 现在默认直接复用当前解释器 `sys.executable` 作为 repo 子步骤入口；`scripts/run-shortline-review-bundle.ps1` 也会优先解析 `py -3.10` 并让 `WonderTrader` 默认复用同一解释器，减少本机 `python` 漂到别的版本后把 bundle 子命令带偏。
- `scripts/run_shortline_hub.py` 现已支持 `--enable-explain-cache / --explain-cache-path / --explain-cache-mode`；`run-shortline-daily.ps1`、`run-shortline-fullcheck.ps1` 与 `run-shortline-review-bundle.ps1` 默认都会启用 explain cache，并共享 `data/runtime/shortline_hub/explain_cache/shortline_explain_cache.json`。
  - `run-shortline-fullcheck.ps1` 会单独走 `explain_cache_mode=full`，与 daily/bundle 的轻量解释缓存隔离，避免 `BigDealAnalysisTool` 被 light cache 误命中短路。
- 上述 3 个 PowerShell 短线包装入口现在统一点源 `scripts/shortline-wrapper-common.ps1`，把 repo / WonderTrader / FinGenius 的解释器解析、路径检查与 dry-run 前置准备收口到一处，减少三份脚本各自漂移。
- `scripts/run-shortline-review-bundle.ps1` 已修正空 `RunId` 场景：只有非空时才透传 `--run-id`，避免 `-DryRun` 之类的包装调用因为空参数直接报 argparse 错误。
- `scripts/run-shortline-daily.ps1` 与 `scripts/run-shortline-fullcheck.ps1` 现在也统一采用同样的解释器解析策略：优先 `py -3.10`，回退 `Get-Command python`，并让 `WonderTrader` 默认复用同一 repo 解释器，减少不同短线入口在同机上跑出不同 Python 环境的偏差。
- `scripts/run-shortline-daily.ps1` 与 `scripts/run-shortline-fullcheck.ps1` 现已补上 wrapper 级 `-DryRun`：会先解析 repo/WT/FG 的解释器与路径，再打印最终 `run_shortline_hub.py` 命令但不真正执行，后续验证短线入口包装层时不必碰真实链路。
- `bundle_manifest.json` 现已补齐 `artifact_governance`：会标记当前输出属于 `default_latest / dated / smoke / dry_run / custom` 哪一类，并在 `data/manual_runs/shortline_review_bundle_index/<trade_date>/<run_id>/bundle_pointer.json` 写统一指针；同时更新 `.../shortline_review_bundle_index/latest.json`，后面即使用自定义目录跑，也能从固定 index 位置反查到最近结果。
- 如果 bundle 中途失败，`bundle_manifest.json` 与 `bundle_report.md` 现在仍会落盘，并附带 `failed_step / error_message / stderr_excerpt`，便于第二天直接回看失败现场。
- 当同日 shortline snapshot 没有 `top_pick / watchlist`、只剩 `high_risk_mover` 时，`短线观察` 会自动回退展示高风险异动里的前几只股票，不再只显示 `top_symbols: none`。
- 同一交易日如果重复执行 `scripts/run_shortline_hub.py --persist-snapshot`，短线 snapshot 现在按 `top_pick / watchlist / high_risk_mover` 三组做“整批覆盖”同步；旧的同日短线股票不会继续残留到快复盘摘要里。
- 如果当天没有短线 snapshot，`fast review` 会回退读取最近同日 `shortline_*` 的 `run_summary.json`。
- 当前单机实跑下，`WonderTrader` bridge 更推荐使用当前工程 Python 作为 `--wt-python-executable`；这样可以直接复用主工程依赖，并稳定触发 `wondertrader_real_engine` 路径。

## 10. Shortline Hub 2026-05-04 Follow-up

- `shortline review bundle` root `Shortline Runtime` now shows `tracking_repeat_symbol_count / tracking_longest_streak_days` directly.
- `scripts/run-shortline-daily.ps1` now supports wrapper-level `-TrackingHistoryPath` for explicit cross-day tracking-history reuse.
- Real serial validation completed on `2026-05-02 -> 2026-05-03` and `2026-05-03 -> 2026-05-04`.
- Tracking history landed correctly in both runs; no natural repeated symbols appeared because upstream candidate sourcing still switched between `wondertrader_real_engine` and `wondertrader_export`.
## 10.1 Shortline Hub 2026-05-04 Source-Mode Follow-up

- `shortline_runs_summary.json/.md` 与 `shortline review bundle` 根层现在还会把质量判定依据直接写出来，包括 `tracking_history_ready`、`source_real_engine_expected_passed` 与顶层 `quality_failed_due_to`；看到 `degraded` 或 `quality_failed` 时不需要再靠反推理解是哪条规则触发。
- `shortline review bundle` 顶层状态现已做兼容回退：`status` 继续表示旧的执行结果，`execution_status` 负责显式执行态，而 `overall_status` 才承载 `quality_failed` 这类总体结论。旧脚本若只认 `success/failed`，可继续看 `status`。
- `tracking_continuity_weak` 现在只会在 shortline 已具备跨日比较基础时触发；如果当前只有首日样本、同日重跑样本或刚切换 tracking history 文件，quality guardrails 不会再因为 `tracking_repeat_symbol_count=0` 直接把结果降级。
- `shortline review bundle` 根层现在会把执行和质量拆开显示：`execution_status` 只表示 step 是否跑完，而 `status` 会在 `quality_summary.verdict=fail` 时升级为 `quality_failed`，更适合脚本和人工复盘直接做第一层判断。

- `shortline_runs_summary.json/.md` 与 `shortline review bundle` 根层现在还会额外输出统一的 `quality_summary`；默认 `quality_profile=standard` 会把系统完整性问题标成 `fail`，把 tracking/cache/并发退化标成 `warning`，便于先判断是流程坏了还是结果偏弱。

- `scripts/run-shortline-review-bundle.ps1` now also supports wrapper-level `-TrackingHistoryPath`, so bundle replays can reuse the same tracking history file without dropping to the Python entrypoint.
- daily default has now been stabilized to `wt_source_mode=prefer_real_engine` across:
  - `run_shortline_hub.py`
  - `run_shortline_review_bundle.py`
  - `run-shortline-daily.ps1`
  - `run-shortline-fullcheck.ps1`
  - `run-shortline-review-bundle.ps1`
- `prefer_bridge_data` is still preserved as an explicit opt-in mode for compare/demo workflows, but it is no longer the hidden default for real daily replay.
- Real re-validation after the source-mode fix:
  - `2026-05-03`: `scan_source_counts={"wondertrader_real_engine":5}`
  - `2026-05-04`: `scan_source_counts={"wondertrader_real_engine":5}`, `tracking_repeat_symbol_count=5`, `tracking_longest_streak_days=2`
- `shortline_report.md` result overview now directly surfaces:
  - `tracking_repeat_symbol_count`
  - `tracking_longest_streak_days`
  - `tracking_focus_symbols`
  so repeated-symbol progress can be judged from the report header without reading the full tracking section.
- `scripts/summarize_shortline_runs.py` 现也会把 `shortline_review_bundle/*/shortline_run` 纳入 recent runs summary，并对同 `run_id` 只保留最新一次重跑结果；bundle 根摘要与独立 `runs_summary` 的“最新短线运行”视角现已对齐。
Update note 2026-05-05: `shortline_hub` now reads same-day snapshot-backed hard logic evidence from `earnings_surprise`, `commodity_beneficiary__*`, and conservative `trend_leader_unified` before falling back to explanation-text keywords, so real earnings / price-cycle / industry-catalyst names can be lifted out of `flow_only`.
Update note 2026-05-05: the same shortline hard-evidence layer now supports recent snapshot fallback instead of same-day-only reads. If the target `trade_date` is a holiday, non-trading day, or simply lacks fresh upstream rows, `shortline_hub` will reuse the latest valid `earnings_surprise` / `commodity_beneficiary__*` / conservative `trend_leader_unified` snapshots from the recent 3-5 trade-day window, and `scripts/run-shortline-daily.ps1` now pre-runs those three upstream snapshot producers before launching `run_shortline_hub.py`.
Update note 2026-05-06: the three A-share upstream snapshot producers used by `run-shortline-daily.ps1` now resolve non-trading-day `--snapshot-date` values through the shared CN trading calendar. A holiday date such as `2026-05-04` now executes as `2026-04-30` at the producer layer instead of hard-using the holiday date.
Update note 2026-05-06: the snapshot-backed hard-evidence layer in `shortline_hub` is now truly live in the real CLI path as well. `scripts/run_shortline_hub.py` now injects `DatabaseManager` into `ShortlineHubOrchestrator`, so the previously added `earnings_surprise / commodity_beneficiary__* / trend_leader_unified` evidence can affect real process-mode ranking instead of only working in direct/unit-level calls.
