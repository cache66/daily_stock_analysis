# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

> For user-friendly release highlights, see the [GitHub Releases](https://github.com/ZhuLinsen/daily_stock_analysis/releases) page.

> Use this file as the user-visible summary only.
> Current default strategy baseline: [docs/LOCAL_STRATEGY_BASELINE.md](./LOCAL_STRATEGY_BASELINE.md)
> Local strategy entry/catalog: [docs/LOCAL_STRATEGY_CATALOG.md](./LOCAL_STRATEGY_CATALOG.md)
> Detailed internal change log and run evidence: [docs/AI_MODIFICATION_LOG.md](./AI_MODIFICATION_LOG.md)

## [Unreleased]
- [新功能] 股息线新增央国企软偏好：输出 `owner_type`（央企/地方国企/民营/…）与 `soe_bonus` 加分（默认 +3，`--no-prefer-soe` 关闭）；stock_basic 缓存缺 `act_ent_type` 时自动尝试刷新一次，配额受限时降级为未知并继续。
- [新功能] 新增 `scripts/refresh_local_daily_basic_snapshot.py`：Tushare 配额受限时用腾讯行情 + AKShare 分红缓存构造本地 `daily_basic` 代理快照（带 `snapshot_source=tencent_proxy` 来源标记，默认跳过已有文件），配合 `--cache-only` 可离线运行股息线；最近 5 个交易日实盘验证可用。
- [新功能] 新增「股息线（防守线）」筛选 MVP：`src/services/dividend_income_service.py` + `scripts/select_dividend_income_candidates.py`，按 Tushare `daily_basic` 快照计算股息率（本地缓存优先且 30 天复用，配额受限时 `--wait-minutes` 自动等待恢复窗口），分红历史与每股收益改走 AKShare `stock_fhps_em` 按报告期批量（免费无配额，逐票 Tushare `dividend` 仅作兜底，无 `fina_indicator` 权限时 EPS 自动降级），输出高股息率 + 连续分红 + 现金流校验的候选池 CSV/Markdown。
- [文档] 个人策略方案按「打法化」方向重写（v2）：目标结构改为「一守一攻 + 备用线」（吃股息 / 短线=图形×催化 / 业绩条件启用），新增环境开关（regime）、14 条策略去向对照与落地步骤；待确认后实施。
- [文档] 新增 `docs/个人策略文档/个人策略精简方案.md`（草案，待确认）：盘点 14 条页面策略、约 36 个策略脚本与 1.3 万行文档现状，给出「4+2+1」目标形态、分档处置明细、冻结候选清单与三步执行计划。
- [改进] `scripts/run_fast_review_bundle.py` 与 `scripts/select_monthly_slow_rise_candidates.py` 收紧扩展信号读层：`trend_leader` 的 `fallback` / `trend_score<=0` / `weak_trend_structure` 样本不再进入首页聚合；`continuous_up` 改为“仅作节奏观察，需其他主信号共振”并在无附着主信号时直接清空候选；`monthly_slow_rise` 新增 `--cache-only` 与 `--universe-codes-file`，白天可优先按本地 history cache 和白名单收窄 universe，避免缺缓存样本拖慢运行链路。
- [改进] `scripts/run_fast_review_bundle.py` 的 `earnings-only` 读层新增本地缓存价格健康门槛，默认只读 `data/cache/history`，对近端明显回落、20 日回撤过大、跌破中期均线或月线转弱的业绩票标记 `earnings_price_health_*`；其中缺少明确催化的样本降为 `earnings_price_health_weak`，有明确行业催化的样本保留为 `earnings_price_pullback_watch` 回撤观察，减少历史业绩好但当前图形走坏的样本进入前排，同时避免把有主线但短线回撤的票直接隐藏。
- [改进] `daily_slow_rise` 与 `long_base_release` 图形策略新增近 `20` 日上影线压力和近端月线趋势确认字段，筛选时会剔除上影线过多、冲高回落明显或月线未保持向上的纯图形样本，并在导出 CSV 中写出 `upper_shadow_*` 与 `monthly_uptrend_*` 便于复盘误选原因。
- [改进] 新增 `scripts/build_long_term_review_overlay.py` 与 `src/services/long_term_position_service.py`，可对已有 `fast_review_stock_overview.csv` 只读生成长期位置/估值叠加报告，默认只使用本地历史缓存、不触发全市场扫描；脚本支持可选 `--enrich-valuation` 逐票限预算补 PE/PB/市值，并支持 `--enrich-historical-valuation` 通过 Tushare `daily_basic` 缓存真实历史 PE/PB 分位，拿不到正式历史估值时回退展示估算 PB 分位、样本内 PB 相对分位、1-2 年价格分位、MA120/MA250、月线均线、回撤和保守估值标签。
- [改进] 新增 `src/services/industry_catalyst_registry.py` 明确行业催化 registry，首批覆盖存储涨价、AI 算力链、机器人、资源涨价、锂电材料复苏和航运景气；`scripts/run_fast_review_bundle.py` 会复用该 registry 给业绩票补 `cycle_catalyst_*` 与业务标签，让 `天赐材料 / 工业富联 / 紫金矿业 / 中远海特` 这类有明确主线的样本不再仅因预期字段缺失而停留在 `earnings_only_unconfirmed`。
- [改进] `scripts/run_fast_review_bundle.py` 的每日总览新增 `accumulation_watch / 吸筹观察` 分层和 `review_certainty_*` 字段：纯图形走顺、阳线/慢涨/长平台释放但缺少业绩、行业催化、资金或板块确认的样本会保留为观察票，不再被误标为“图形优先/确定性主线”；有存储涨价、业绩兑现等非图形证据的样本仍保留在确定性主线前排。
- [改进] `scripts/run_fast_review_bundle.py` 的轻量复盘读层新增 `cycle_catalyst_*` 字段，并复用本地业务别名识别 `兆易创新 / 德明利` 这类存储链业绩票；当命中“存储涨价/复苏 + 业绩兑现”时，会在 `strategy_focus` 与 `stock_overview` 中显式展示周期催化标签，而不额外触发全市场或外部情报扫描。
- [改进] `scripts/run_fast_review_bundle.py` 现在会把纯 `long_base_release` 图形票的阅读层从单一 `pure_chart_quality_passed` 拆成 `confirmed` 与 `recovery_watch` 两档：前者要求交易日新鲜、站上 `MA10`、近 `10` 日涨幅与回撤仍确认，后者允许类似 `凯莱英` 的底部释放后近端横住但尚未完全确认；同时在 bundle CSV 读层补齐 `recent_return_10d / recent_drawdown_10d / above_ma10` 等质量字段、在 `strategy_focus` 中补充图形类 `focus_reason`，避免 `航天电器 / 中石科技` 这类近期仍确认的长平台释放票被误压到低优先级。
- [改进] `scripts/run_fast_review_bundle.py` 的首页/单票总览现在会把 `earnings-only` 且 `market_expectation_reference_label=unknown`、又缺少价格/相对强度/资金/机构预期确认的样本降为低优先级；`daily_slow_rise` 新增 `review_balanced` profile 并成为默认每日复盘口径，在放宽回撤和单日涨幅限制的同时增加 `10` 日回撤护栏。
- [改进] `scripts/run_fast_review_bundle.py` 在 `--safe-mode` 或 `--skip-persist-snapshots` 快速验证路径下会复用已有解释字段并跳过尾段外部归因/焦点富化，避免缓存优先跑策略时被报表补充查询拖慢或压住机器。
- [修复] `scripts/select_earnings_surprise_candidates.py` 的 `--skip-db-persist` 现在仍会读取已有 `signal_fundamental_snapshot` 缓存，但不会写入基本面快照或候选结果，避免白天只读验证时因缓存读写一起关闭而把 `earnings` 误判为 0 命中。
- [修复] `data_provider/base.py` 读取 `AkshareFetcher` 旧历史缓存时会修复 `volume=0` 且 `amount` 实际存成成交量手数的遗留数据，避免 `daily_slow_rise` 等策略把流动性误判为极低成交额。
- [改进] Web `/personal-strategies` 改为行内下拉查看单票详情，不再使用右侧详情抽屉；后端 `personal-strategy-matrix` 同时新增 `snapshot_date=latest` 最新产物解析、短线主升/长线发现/其他观察分层，以及基于已有复盘字段的阅读提示。
- [改进] Web `/personal-strategies` 个人策略股票列表已补齐单票详情查看能力，可查看策略命中说明、图形证据、业绩证据、股票快照与原始信号键；新增 `docs/个人策略文档/个人策略界面接口关系图.md` 说明个人策略 runner、CSV 产物、Signals API、股票行情/历史接口与 Web 页面之间的关系。
- [新功能] 新增 `scripts/run_personal_strategy_matrix.py` 与 `src/services/personal_strategy_run_service.py`，把个人策略矩阵生成收口为“先预热 K 线/业绩固定数据，再运行 fast-review bundle，再由 `/api/v1/signals/personal-strategy-matrix` 读取”的统一入口，并支持 `--dry-run`、`--extended-signals` 与低并发安全模式。
- [修复] `scripts/run_fast_review_bundle.py` 现在会把 `--limit` 正确下传给 `hundred_day_high` 子策略，避免个人策略矩阵小样本验证时 `earnings/daily/long_base` 被限缩、但百日新高仍意外接近全量扫描。
- [新功能] `apps/dsa-web` 新增 `/personal-strategies` 个人策略股票列表页面，并新增 `/api/v1/signals/personal-strategy-matrix` 后端策略 registry/矩阵读层，支持顶部按策略过滤股票，并以紧凑列表展示每只股票命中的个人策略；新增 `docs/个人策略文档/个人策略界面策略清单.md` 记录页面策略名称、作用与主要判断逻辑。
- [新功能] `/api/v1/signals/fast-review-stock-overview` 新增单票复盘总览读取接口，直接读取 `fast_review_stock_overview.csv` 并支持按 `code` 查询某只股票触发了哪些策略、处在哪个复盘分层以及对应图形/业绩证据，避免只按策略列表翻候选。
- [改进] `scripts/collect_earnings_observation_snapshots.py` 现已补成可直接手工使用的“业绩观察池”工具：新增 `--entry-strategy-profile`（可显式切到 `relaxed`）和 `--output-dir`，可把“还不够首页、但值得持续跟踪”的业绩票单独导出为 `registry/active` CSV 与 Markdown，而不必重新放宽 `earnings_surprise balanced` 主策略。
- [改进] `scripts/select_earnings_surprise_candidates.py` 现已收紧 `balanced/strict` 的 `direct pass` 语义：不再允许“总分够但业绩质量弱、周期不可读”的样本直接通过；这两档现在还要求最小质量底板，否则记为 `blocked_direct_quality_floor`，用于减少 `earnings_surprise` 原始候选里“财报分数过线但并不值得继续看”的票。
- [改进] `scripts/select_earnings_surprise_candidates.py` 现在会在策略层原生写出 `fast_review_focus_gate_passed/reason`，把“业绩过线”和“适合快复盘继续看”拆成两层语义：导出的 CSV/Markdown 会显式标记 `快复盘`，排序也优先保留已通过快复盘确认门槛的业绩票，减少首页继续被纯财报分数票占满。
- [改进] `scripts/run_fast_review_bundle.py` 现在把默认每日复盘进一步收口到“单票视角”：`strategy_focus` 的底层聚合已纳入 `daily_slow_rise` 与 `long_base_release`，不再只在它们与其他主策略重叠时才进入股票焦点；同时新增 `fast_review_stock_overview.csv/.md`，按单只股票汇总当日命中的策略、图形证据、业绩证据与一句话理由，并为纯 `earnings-only` 行增加阅读层确认门槛，减少“财报分数过线但缺少市场确认”的票继续占据首页。
- [改进] `scripts/select_long_base_release_candidates.py` 与 `scripts/run_fast_review_bundle.py` 现为 `long_base_release` 补充最近 `20/30` 日阳线占比、近 `5/10` 日强弱、近 `10` 日回撤/连阴、`MA10` 与最新交易日等图形质量字段，并将纯 `long_base_release` 首页阅读层收紧为“阳线更多、近期未走坏、非停更”的图形观察票。
- [修复] `data_provider/akshare_fetcher.py` 现在会把“`Tushare` 空历史后继续回退到 `Akshare`，但 `Akshare EM` 恰好处于 backoff、其余渠道只返回空结果”的场景归类为 no-data，而不是继续累计成 `AkshareFetcher` 的 manager 级熔断失败，降低 `daily_slow_rise` 等全市场快扫里的误熔断噪音。
- [修复] `data_provider/base.py` 与 `src/services/kline_selector_service.py` 不再在全市场快扫里提前掐掉 `Tushare no data -> Akshare` 的历史回退，次新股/新股窗口的空历史现在会继续尝试免费源，而不是直接把 `no data` 放大成最终失败。
- [改进] `data_provider/tushare_fetcher.py` 现将 `Tushare` 限流改为进程级共享记账，并在上游明确返回频率超限时触发全局冷却；同时快扫专用 `TushareFetcher` 额外收紧到 `35/min` 头部预算，降低多实例叠加后撞上账号侧 `50/min` 的概率。
- [改进] `scripts/run_fast_review_bundle.py` 的主汇总 `fast_review_summary.md` 现收口为单一“每日精选复盘”文档，优先按 `今日最值得看 / 双确认 / 图形优先 / 业绩优先 / 趋势补充` 展示，不再在主文档顶部堆叠各类 `csv/md` 产物路径；原始明细文件仍保留在同目录用于回查。
- [改进] `config/local_strategy_profile.json` 与 `scripts/run_fast_review_bundle.py` 现将默认每日 `include_signals` 收口为 `earnings,hundred_day_high,daily_slow_rise,long_base_release`，`trend_leader` 保留为可选补充观察，不再默认主导首页。
- [文档] 新增 `docs/个人策略文档/个人每日复盘约定.md`，并同步更新个人策略目录、基线与中英对照文档，明确“每日复盘优先服务图形 + 业绩”的后续维护口径。
- [改进] `scripts/run_fast_review_bundle.py` 的每日复盘默认 `long_base_release_profile` 现切到 `loose`，并在 `strategy_focus` 排序里为近期明显偏弱的 `trend_only` 回踩样本增加惩罚、为当日强势趋势样本增加加分；同时纯 `trend_only` 行不再因为“同日别处存在业绩样本”就被 bundle 级 `earnings_signal_active` 顺带抬分，减少 `大族激光 / 中国巨石` 这类短线确认不足但仍被旧排序顶到前排的情况。
- [改进] `src/services/kline_selector_service.py` 新增 `resolve_local_strategy_universe_filters(...)`，并让 `monthly_slow_rise / daily_slow_rise / long_base_release / hundred_day_high / earnings_surprise / trend_leader / warm_local_strategy_cache` 统一复用这套默认样本口径：默认排除 `ST`（含 `PT`）、排除 `科创板`、保留 `创业板`，并继续依赖基础 A 股股票池排除 `北交所`；同时修复部分脚本在关闭 `spot prefilter` 后把 `ST` 又重新带回样本池的问题。
- [改进] `scripts/select_long_base_release_candidates.py` 新增更宽松的 `loose` profile，并在 `2026-07-09` 低并发 `limit=2000` 实扫中稳定产出 `3` 个 `long_base_breakout` 候选（`航天电器 / 探路者 / 日科化学`）；同轮失败摘要中未再出现 `history_fetch_failed`。
- [修复] `src/services/kline_selector_service.py` 与 `data_provider/base.py` 现已收紧本地策略共享扫描入口的活跃样本过滤，并修正历史缓存对次新股“补上市前历史失败即直接报错”的语义；共享快扫现在会避免把 `NaN` 名称误写成 `"nan"` 覆盖原股票名，优先按 `stock_basic` 活跃代码集剔除历史壳/退市样本，让这类标的更多转为 `insufficient_history` 或直接在 universe 前置排除，显著降低 `history_fetch_failed` 噪音；同时 `monthly_slow_rise` 的后置资金画像补充会优先复用 scan 阶段已有 quote snapshot，减少筛选结束后额外触发实时行情限流。
- [改进] `config/local_strategy_profile.json`、`scripts/select_trend_leader_candidates.py` 与多条本地策略共享扫描入口现统一默认样本口径：明确剔除 `ST`（含 `PT`）与 `科创板`，并继续依赖基础 A 股股票池排除 `北交所`，减少不同策略跑出不一致 universe 的情况。
- [改进] `src/services/kline_selector_service.py` 的共享 universe 准备层现在会先按 `code` 对齐最新 `stock_basic` 名称，再执行 `ST/*ST/PT` 过滤，降低个股后期改名为风险标识后仍被旧 spot 名称漏入样本池的概率。
- [改进] `scripts/warm_local_strategy_cache.py` 默认不再额外按代码前缀缩小样本池，且 `--top-n` 默认改为覆盖全部过滤后样本，便于长期本地预热时尽量完整覆盖可运行 universe。
- [改进] `data_provider/base.py` 为 `get_earnings_fundamental_context(...)` 增加按报告期复用 stale 磁盘缓存的能力：当缓存对应的 `report_date / report_period` 仍命中当前报告期、且没有出现更晚的业绩公告/快报/预告日期时，`ok/partial` 业绩上下文可继续本地复用，减少同报告期内的重复抓取。
- [新功能] 新增 `scripts/warm_local_strategy_cache.py`，用于按低并发顺序预热本地 K 线历史与业绩上下文缓存，并将排序样本池与预热结果落盘到 `data/runtime/cache_prewarm/<snapshot-date>/`。
- [改进] `scripts/warm_local_strategy_cache.py` 与 `scripts/select_earnings_surprise_candidates.py` 现在会对“当前有效交易日”的业绩事件目录按时效自动刷新，并支持 `--force-refresh-event-catalog`，降低同日新增业绩公告被旧 catalog 遮住的风险。
- [文档] 新增 `docs/LOCAL_STRATEGY_CHINESE_MAP.md`，集中整理本地策略英文名与中文对应，并同步从 `docs/LOCAL_STRATEGY_CATALOG.md`、`docs/LOCAL_STRATEGY_BASELINE.md` 挂上入口，方便按中文快速识别主策略、观察池与专题工具。
- [修复] `data_provider/base.py` 补回 `DataFetcherManager` 的历史数据磁盘缓存 / 失败缓存 helper，修复 `fast review` 与依赖 `get_daily_data(...)` 的本地策略在 manager 层因缺失方法而大量拉取失败。
- [修复] `data_provider/base.py` 修正 manager 层 `get_daily_data(...)` 对 fetcher 的日线方法调用，并避免把明显的“空结果/无数据”响应错误计入 source failure，减少大范围扫描时的误熔断。
- [修复] `src/config.py` 现支持从 `TUSHARE_PRO_TOKEN` / `TUSHARE_API_TOKEN` 自动复用到 `tushare_token`，并同步恢复带 `tushare` 优先级的实时数据源自动识别，避免本机仅配置 Pro token 时被误判为未配置。
- [改进] `src/services/kline_selector_service.py` 将全市场快扫专用日线 manager 调整为 `Tushare` 优先、`Akshare` 兜底，降低每日复盘在免费源抖动时的卡死概率。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `--safe-mode`，会把 bundle 级并发和各策略 `max_workers` 全部压到 `1`，方便单机长期以低并发安全模式运行每日复盘。
- [改进] `data_provider/tushare_fetcher.py` 现会读取 `TUSHARE_RATE_LIMIT_PER_MINUTE` / `Config.tushare_rate_limit_per_minute`，并将默认限速收紧到 `45` 次/分钟，减少单机全市场复盘时因误判 80 次/分钟而频繁撞上账号侧限流。
- [改进] `scripts/run-local-linux.sh` 现在默认导出 `LITELLM_LOCAL_MODEL_COST_MAP=true`，并同步更新 `docs/local-linux-dev.md` / `docs/local-linux-dev_EN.md`，避免 Linux / WSL 下仓库内 `.venv-linux` 启动时卡在 LiteLLM 远程 model cost map 初始化。
- [文档] 新增 `scripts/run-local-linux.sh` 与 `docs/local-linux-dev.md` / `docs/local-linux-dev_EN.md`，统一 Linux / WSL 下的仓库内 `.venv-linux` 环境准备、FastAPI 自检、API 启动与主程序运行入口。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `利通电子风格跟踪池` 分层阅读与 `Top 50` 导出，按 `最像利通电子 / 次像 / 观察` 展示“走势顺 + 有基本面证据”的样本，并为横盘突破观察补充 `横盘多久 / 突破多少 / 平台位置`。
- [新功能] `scripts/select_long_base_release_candidates.py` 新增长横盘释放信号，并将 `long_base_release` 默认接入每日 `fast review`；同名样本若同时命中 `hundred_day_high` 会在百日新高区块内标注 `长横盘慢推型/长横盘突破型`，未重叠样本则单独展示在 `长横盘释放候选` 区块。
- [改进] `scripts/run_fast_review_bundle.py` 现在会在每日复盘摘要里新增 `百日新高中的30-45度强图形` 分组，专门展示同时命中 `hundred_day_high` 与 `daily_slow_rise` 的强图形样本，并按 `base_breakout / healthy_trend / base_to_trend / steady_rise` 优先级排序。
- [改进] `scripts/select_trend_leader_candidates.py` 现在会随导出结果额外落盘 `trend_leader_unified_run_summary.json`，并支持把 scan checkpoint 一并复制到同日产物目录，便于复盘时直接查看 `processed_count / phase_timing / cache hit`。
- [改进] `scripts/run_fast_review_bundle.py` 现在会为 `trend_leader` 快复盘显式下传 `--checkpoint-path` 到当日 `signals/trend_leader/` 目录，后续慢跑可以直接基于产物做诊断，不再只剩候选 CSV。
- [改进] `src/services/signal_cause_analysis_service.py` 现会在 `Fast Review Focus` 的 authority 解释链路里先用结构化 `公告 / 财报 / 研报` 判断是否已足够形成 `公告确认 / 财报确认 / 研报强化`；若已能成立，则不再因为缺少其他维度而继续触发整轮 `search_comprehensive_intel(...)`，减少快复盘解释尾段耗时且不改变候选股口径。
- [改进] `scripts/run_fast_review_bundle.py` 现在会在同一轮 `strategy_focus` 解释构建里复用单个 `SignalCauseAnalysisService`，让公告/财报/研报目录缓存能在 bundle 内共享，减少逐行重建 cause service 带来的 authority 解释尾段耗时。
- [改进] `src/services/fast_review_focus_service.py` 继续收紧 fast review 尾段行情补全：当东财 `EM` 冷启动首轮就返回空时，不再对后续标的重复做无效 `EM` 读取；同时，对已做过腾讯批量预抓但仍 miss 的标的不再逐票重复打一遍轻量腾讯，减少 `strategy_focus` 读层/导出层的尾部长耗时。
- [改进] `scripts/run_fast_review_bundle.py` 默认 `include_signals` 现已包含 `daily_slow_rise`，并将 fast-review 默认 `daily_profile` 切到 `accelerating`，让每日复盘默认带上更贴近 `30-45 度健康慢涨` 的日线结构样本。
- [修复] `src/services/kline_selector_service.py` 重新启用 fast A-share scan 的 `Tushare` 日线 fallback，修复 `Akshare` 临时历史接口 backoff 时同日样本覆盖被直接缩小的问题。
- [改进] `src/search_service.py` 在 `search_comprehensive_intel(...)` 中为 `SearXNG` 公共实例不可用场景新增同次调用短路，避免单只股票在多个情报维度上重复走无效失败回路，降低快复盘解释尾段耗时。
- [改进] `daily_slow_rise` 新增可选 `accelerating` profile，用于识别“整体慢涨、但中途允许一次 10cm 级加速，且前半段底部不必特别紧”的日线强趋势样本，不改动默认 `balanced` 口径。
- [修复] `daily_slow_rise` 默认历史窗口增加到 `62` 根，修复真实回放里大量样本因只拿到 `59/60` 根附近日线而被误判为 `insufficient daily history`，并让失败分布回到真实形态口径。
- [新功能] 新增独立可选日线结构策略 `daily_slow_rise`：`scripts/select_daily_slow_rise_candidates.py` 会单独筛选“前面横盘、后面 30-45 度健康慢涨”或“整体缓升、回撤小、修复快”的日线样本，并导出 `daily_slow_rise_candidates.csv/.md/.txt`。
- [改进] `scripts/run_fast_review_bundle.py` 现支持 `--include-signals daily_slow_rise`，并会在 `fast_review_summary.md` / `fast_review_summary_latest.md` 里新增 `日线慢涨候选` 区块，直接展开这条线的前排样本。
- [改进] `src/services/signal_snapshot_service.py` 新增 `daily_slow_rise` 信号元数据，默认显示标签为 `日线慢涨`，便于后续按 signal snapshot 回看这条新策略。
- [测试] 新增 `tests/test_daily_slow_rise_candidates.py` 与 `tests/test_daily_slow_rise_fast_review_integration.py`，并通过 `tests/test_fast_review_daily_bundle.py`、`tests/test_signal_snapshot_service.py` 的相关回归，覆盖新策略筛选、快复盘接线与读层兼容。
- [改进] `scripts/run_fast_review_bundle.py` 现会为 `fast_review_strategy_focus.csv` 新增 `review_display_group / review_display_group_label`，并在 `fast_review_summary.md` / `fast_review_summary_latest.md` 里单独展开 `纯趋势延续 Top N`，让 `trend_leader only` 的强趋势票不再因为不属于 `hundred_day_high` 而看起来像漏扫。
- [改进] `scripts/run_fast_review_bundle.py` 现会在 `fast_review_summary.md` / `fast_review_summary_latest.md` 和 `fast_review_strategy_focus.csv` 中显式标记当前复盘模式：`业绩窗口` 或 `业绩空窗期`，便于不看界面时直接判断当天该偏重业绩还是偏重图形/百日新高。
- [改进] `scripts/run_fast_review_bundle.py` 为 fast review 新增“业绩空窗期模式”：当日 `earnings` 候选为空时，`strategy_focus` 会下调趋势票上的业绩残留权重，避免大量 `trend_leader` 因历史财务字段继续被读成业绩驱动。
- [改进] `scripts/run_fast_review_bundle.py` 现会在业绩空窗期优先前置 `hundred_day_high` 中的强图形样本：`base_breakout / healthy_trend` 的 `hundred_only` 票会进入主焦点前排，使每日复盘首页同时看到 `交叉强样本` 与 `纯百日新高强图形`。
- [修复] `scripts/run_fast_review_bundle.py` 补齐了 `hundred_day_high` 导出字段在 bundle 读层的接线，`breakout_quality_score`、`chart_pattern_*`、`base_breakout_score`、`healthy_trend_score` 现已真正参与 `strategy_focus` 排序，不再只停留在百日新高独立摘要。
- [改进] `scripts/run_fast_review_bundle.py`、`src/services/signal_cause_analysis_service.py` 收紧 fast review 的宽口径 AI 归因，默认优先展示行业/业务锚点；同时把 `百日新高 Top N` 前移到策略焦点前，并让 `hundred_day_high` 快照优先显示业务/图形提示，降低“前排看起来全是 AI”的阅读偏差。
- [修复] `src/services/kline_selector_service.py` 现已收紧 `spot-enriched universe` 的降级语义：当 live `spot` 拉取失败并已决定复用 stale / disk `spot` cache 时，不再为了补 `listing metadata` 继续阻塞到 `baostock` 慢链路；这直接修复了 `earnings / hundred_day_high / trend_leader` 在每日复盘里因 `listing metadata` 兜底卡住几十秒以上的问题。
- [修复] `scripts/run_fast_review_bundle.py` 现已收紧外部信号脚本的监控语义：`idle timeout` 仅用于“子进程启动后始终没有任何输出”的场景；一旦子进程已输出首条日志，bundle 会改为每 `5` 分钟输出一次 `external heartbeat`，并仅继续受 `total timeout` 约束，避免 `earnings / hundred_day_high / trend_leader` 在合法长静默阶段被 `30` 分钟无 stdout 误杀。
- [改进] `scripts/select_hundred_day_high_candidates.py` 与 `scripts/run_fast_review_bundle.py` 为 `hundred_day_high` 新增阅读层图形标签：当前会把命中样本区分为 `横盘突破型 / 健康慢涨型 / 图形一般`，并将 `chart_pattern_*` 字段持久化到百日新高导出，同时在 `fast_review_summary.md` 的 `百日新高 Top N` 中直接展示并优先前排更好看的图形。
- [修复] `scripts/select_trend_leader_candidates.py` 与 `scripts/select_earnings_surprise_candidates.py` 现已修正显式 `--snapshot-date` 的交易日解析：当 fast review 明确传入某个交易日时，不再按当日 `00:00` 误判为“尚未收盘”并回退到前一交易日；`2026-05-11` 的每日复盘回放中，`trend_leader` 与 `earnings` 已统一按 `2026-05-11` 运行，避免与 `hundred_day_high` 出现日期口径分裂。
- [改进] `scripts/run_fast_review_bundle.py` 现在会在 `fast_review_summary.md` / `fast_review_summary_latest.md` 里新增独立 `百日新高 Top N` 摘要区块，专门展开当日 `hundred_day_high` 前排样本；该区块按实际可见条数动态命名，并优先复用当天 `strategy_focus` 已补齐的 `涨幅 / PE / 报告期 / 净利` 快照，仅在缺失时才回退到原始突破字段，提升每日复盘对百日新高线的可见性，同时不改变 `strategy_focus` 主排序与 A/B 分类。
- [改进] `scripts/run_fast_review_bundle.py` 继续收紧 `百日新高 Top N` 的摘要排序：当前会优先把 `已进入 strategy_focus`、`A类`、`trend_leader + hundred_day_high` 交叉、`score 更高`、`当日更强` 的样本顶到前排，减少像 `福晶科技 / 通鼎互联` 这类核心交叉样本被原始百日新高导出顺序压到列表后段的情况。
- [改进] `scripts/run_fast_review_bundle.py` 现将 `百日新高` 摘要默认扩到 `Top 30`，并拆成 `交叉强样本` 与 `纯百日新高` 两段展示：前者优先展示 `trend_leader + hundred_day_high` 交叉强样本，后者再补纯百日新高名单，提升每日复盘的扫读效率。
- [改进] `src/services/signal_cause_analysis_service.py` 与 `scripts/run_fast_review_bundle.py` 继续收紧 `Fast Review Focus` 的宽口径行业尾句归一化：`电线电缆的研发、生产、销售和服务` 这类导出层残留文案现会统一收敛到 `电力设备`，避免 `industry_logic` 在历史复盘里继续挂整句业务说明。
- [改进] `src/services/signal_cause_analysis_service.py` 为 `Fast Review Focus` 补齐 `光学元器件` 这类长主营句归一化：`光学元器件的研发、生产和销售` 现统一收敛到 `光学元器件`，避免 `preferred_industry_label / business_summary / reason_summary` 再直接挂原始业务整句。
- [新功能] 新增 `scripts/audit_fast_review_label_quality.py` 与 `tests/test_audit_fast_review_label_quality.py`，用于扫描 `fast_review_strategy_focus.csv` 等复盘产物里的长业务句标签残留，并为后续每日复跑提供轻量质量审计。
- [改进] `src/services/signal_cause_analysis_service.py` 与 `src/services/fast_review_focus_service.py` 继续清理 `Fast Review Focus` 的长主营句尾巴：`特种环保纸的研发、生产及销售` 现会统一收敛为 `特种环保纸`，并同步作用到 `preferred_industry_label / business_summary / reason_summary / peer_group_label`，减少 `顺灏股份` 这类样本把整句主营直接当行业标签。
- [改进] `src/services/signal_cause_analysis_service.py` 现将 `Fast Review Focus` 解释主链路里的部分主营长句同步做归一化：`软磁材料及磁心的研发、生产和销售`、`软磁铁氧体磁粉的研发、生产和销售`、`聚酯树脂系列产品的生产销售` 等不再直接进入 `reason_summary / preferred_industry_label`，而会收敛成 `软磁材料/磁性材料`、`树脂/化工材料`，减少页面和导出里出现整句业务文案充当行业标签。
- [修复] `scripts/run_fast_review_bundle.py` 统一 `Fast Review` 的分信号统计口径：当 `hundred_day_high` 等信号在 bundle 层被裁剪导出时，`fast_review_summary.md` 现显示为 `原始条数 (export 导出条数)`，同时终端摘要改为输出原始条数并在需要时补 `export_count`，避免再出现“日志是 226、汇总写成 30”这类歧义。
- [改进] `scripts/run_fast_review_bundle.py` 现将 `display_reason_summary` 下沉到 `fast_review_strategy_focus.csv`、`fast_review_earnings_focus.csv` 及相关 Markdown 汇总区块；快复盘导出默认优先展示压短后的 `行业判断 / 业务定位 / 主线判断 / 真实触发` 文案，而不是原始长解释。
- [改进] `src/services/signal_cause_analysis_service.py` 与 `src/services/fast_review_focus_service.py` 继续收紧 `Fast Review Focus` 的快复盘解释压短逻辑：`reason_summary / display_reason_summary` 现优先保留“行业判断 / 业务定位 / 主线判断 / 真实触发（创高或命中信号）”，并把 `当日强势池入选理由` 降为补充，减少泛化句和弱证据句抢占首屏。
- [改进] `src/services/fast_review_focus_service.py`、`api/v1/schemas/signals.py` 与 `apps/dsa-web/src/pages/SignalsPage.tsx` 现在会为 `Fast Review Focus` 输出并消费 `display_reason_summary / display_authority_judgement / display_authority_summary / display_peer_summary`；当原始 authority 仍为空但财报证据已足够强时，页面会优先显示轻量 `财报确认` 结论，并把 `Authority Details / Peer Check Details` 默认折叠以减轻首屏负担。
- [测试] 补充 `tests/test_signal_cause_analysis_service.py` 与 `tests/test_fast_review_focus_api.py` 的压短优先级回归，锁定 `主线判断` 与 `真实触发` 不能再被 `trend leader` 或泛化景气句挤掉。
- [改进] `src/services/fast_review_focus_service.py` 继续清理 `Peer Check` 的残留主营句式分组：把 `聚酯树脂系列产品的生产销售` 统一归一到 `树脂/化工材料`，减少快复盘里把整句业务描述直接当作同业组名的情况。
- [改进] `src/services/fast_review_focus_service.py` 继续收紧 `Peer Check` 的尾部长标签归一化：把单票化的行业标签 `环保设备Ⅲ` 统一收敛到 `再生资源/环保设备`，避免历史快复盘里继续出现可读性差、可复用性弱的末级行业名。
- [改进] `src/services/fast_review_focus_service.py` 继续压缩 `Peer Check` 的长文本单票组：把 `房地产开发业务和建筑施工业务`、`海上运输业务`、`软磁材料及磁心/软磁铁氧体磁粉` 等主营长标签归一成 `房地产/建筑施工`、`航运`、`软磁材料/磁性材料`，并显式纳入 `锂电/精密组件`，减少历史复盘里出现整句业务描述直接充当分组名。
- [改进] `src/services/fast_review_focus_service.py` 现在会先把 `Peer Check` 分组名做轻量归一化，并在行业标签缺失时从主营描述关键词兜底补组，减少 `光模块/光通信`、`通信/电力设备`、`AI上游材料` 等链条被误拆成单票组的情况。
- [改进] `scripts/run_fast_review_bundle.py` 现在会把 `peer_group_label / peer_resonance_summary / leader_position_summary / turning_point_peer_summary` 一并持久化到 `fast_review_strategy_focus.csv` 与 `fast_review_earnings_focus.csv`，使 `Peer Check` 不再只存在于读层/API，而能随历史快复盘产物一起回看。
- [改进] `src/services/signal_cause_analysis_service.py` 继续增强 `Fast Review Focus` 的 authority 解释质量：`authority_reason_summary` 现在会把 `客户导入 / 供不应求 / 产能爬坡 / 订单放量 / 景气上行` 等催化词，与 `业务方向 / AI 主线景气 / 主线判断` 一起压成更接近“为什么涨”的一句话。
- [改进] `src/services/fast_review_focus_service.py`、`api/v1/schemas/signals.py` 与 `apps/dsa-web/src/pages/SignalsPage.tsx` 为 `Fast Review Focus` 新增轻量 `Peer Check`：按同日同业组输出 `peer_resonance_summary / leader_position_summary / turning_point_peer_summary`，用于判断板块共振、组内龙头位次，以及 `拐点` 是否被同业跟随确认。
- [测试] 补充 `tests/test_signal_cause_analysis_service.py`、`tests/test_fast_review_focus_api.py` 与 `apps/dsa-web/src/pages/__tests__/SignalsPage.fastReview.test.tsx`，覆盖 authority 催化词解释与 `Peer Check` 展示回归。
- [改进] `src/services/signal_cause_analysis_service.py` 现将 `Fast Review Focus` 的 authority 证据时间窗按来源拆开：`公告` 继续使用 `7天`，`研报` 放宽到 `21天`，`财报` 继续按当前报告期逻辑判断；对应 `research_evidence_summary`、`财报确认` 补充句与 `暂无权威验证` 文案已同步更新，避免像 `胜宏科技` 这类“研报在 8-21 天内仍有效”的样本被误读成没有机构强化。
- [改进] `src/services/signal_cause_analysis_service.py` 为 `Fast Review Focus` 的结构化研报 authority 新增东方财富 `reportapi` 直连兜底：当 `akshare.stock_research_report_em(...)` 返回空表、畸形表或异常时，系统会直接拉取 Eastmoney 研报数据，避免像 `东山精密` 这类本来有近 7 天研报的样本被误判成没有机构强化。
- [修复] `src/services/signal_cause_analysis_service.py` 修正 `Fast Review Focus` 的 authority 采集短路：结构化层若只先拿到 `earnings`，现在不会提前结束，而会继续补搜缺失的 `公告 / 研报` 维度，避免“有财报锚点但错过更具体权威解释”的情况。
- [改进] `src/services/signal_cause_analysis_service.py` 继续细化 `Fast Review Focus` 的 authority 摘要：公告与研报证据现在会优先抽取 `客户导入 / 供不应求 / 产能爬坡 / 订单放量 / 景气上行` 等更具体催化词，并在保留 `AI / PCB / 光模块` 主线信息的同时输出更接近“为什么上涨”的简短结论。
- [改进] `src/services/signal_cause_analysis_service.py` 收紧 `Fast Review Focus` 的 `财报确认` 口径：弱季报、亏损季报、营收/净利不支持上涨逻辑的样本不再因为“有季报摘要”就自动落成 `authority_judgement=财报确认`，仍保留 `earnings_evidence_summary` 供复盘回看。
- [改进] `src/services/signal_cause_analysis_service.py` 为 `authority_judgement=暂无权威验证` 增加紧凑 explanation：明确说明近 7 天缺少足够强的公告/财报/研报验证，并补充当前业务方向、主题映射或主线暂定判断，避免 fast-review 详情里的 authority 区块出现空白。
- [改进] `apps/dsa-web/src/pages/SignalsPage.tsx` 收紧 `Fast Review Focus` 详情首屏展示：把 `Verified Logic / Mainline / Today / PE / Report / Net Profit` 顶到最上方，移除首屏 `Priority / Event Date`，并把 `Authority` 压缩成 `Announcement / Earnings / Research` 三类摘要卡片。
- [测试] 补充 `tests/test_signal_cause_analysis_service.py` 弱业绩误判回归用例，以及 `apps/dsa-web/src/pages/__tests__/SignalsPage.fastReview.test.tsx` 的紧凑详情面板回归断言。
- [??] `src/services/signal_cause_analysis_service.py` ???? `Fast Review Focus` ? `authority_reason_summary` ?? `????` ????? `???? / ???? / ?7?????` ????????? `???? / ???? / ????` ??????????? + ????????????
- [修复] `src/services/fast_review_focus_service.py` 现在会在 `fast_review_strategy_focus` 多标的轻量行情补齐时先尝试预热东财 `EM` 实时行情缓存；若 `EM` 仍返回空，则再并发预抓一轮腾讯轻量行情，避免 `2026-05-09` 实跑中前 17 行有 `today_change_pct / pe_ratio`、后半段因逐只回退耗尽 quote deadline 而整体留空的问题；`v12` 回放里 `fast_review_strategy_focus.csv` 已做到 `50` 行缺失 `0`。
- [改进] `src/services/fast_review_focus_service.py` 与 `scripts/run_fast_review_bundle.py` 现在会先对 `fast_review_strategy_focus.csv` 的全量行做轻量 `today_change_pct / pe_ratio` 回填，再仅对 Markdown 可见焦点行保留重型 `report / valuation` enrichment，减少隐藏行市场快照长期留空。
- [修复] `src/services/fast_review_focus_service.py` 现在会优先从 `authority_reason_summary / earnings_evidence_summary` 本地回填 `report_period_label / report_date / net_profit_amount`，不再把已拿到 `2026Q1、净利润` 文本的快复盘行继续留空。
- [修复] `scripts/run_fast_review_bundle.py` 修正 `strategy_focus` 只补可见行导致的隐藏行快照缺口：导出阶段现会先对全部 `export_rows` 跑轻量字段归一化，再仅对可见行做重型 enrichment；`2026-05-09` 复跑中 `杭电股份` 这类隐藏行也已补出 `2026Q1 / 2026-03-31 / 8084.19万`。
- [修复] `scripts/run_fast_review_bundle.py` 修正 Windows 下 fast-review 外部子进程 stdout 解码不一致的问题：父进程现在显式使用 `utf-8 + errors=replace` 读取日志，并为子进程默认注入 `PYTHONIOENCODING=utf-8`；`2026-05-09` 全量复跑已不再出现 `UnicodeDecodeError` 线程崩溃。
- [改进] `src/services/signal_cause_analysis_service.py` 在 authority 财报证据仍缺失时，会自动复用 `scripts/select_earnings_surprise_candidates.py` 的近期业绩事件目录，补抓当前报告期 `actual report / quick report / forecast` 结构化条目；`2026-05-09` 在线复跑中 `胜宏科技` 也已从 `暂无权威验证` 抬升到 `财报确认`，代表样本 `胜宏科技 / 光迅科技 / 杭电股份 / 中天科技 / 利通电子` 现已全部纠正。
- [修复] `src/services/signal_cause_analysis_service.py` 修正 authority 财报兜底的短路判断：当 `general fundamental` 里只有 `earnings_quality.verdict=unavailable` 和默认占位 metrics（如 `insufficient_history / cycle_phase=unavailable / cycle_score=0`）时，不再误判为“已有有效业绩上下文”，从而允许 `earnings-only` fallback 真正并入 authority 判断链。
- [测试] `tests/test_signal_cause_analysis_service.py` 新增真实形态回归：`unavailable default metrics + valid earnings-only fallback` 现在会稳定落成 `财报确认`，避免 authority 层再次被占位 metrics 短路。
- [改进] 在 `2026-05-09` 的 authority short-circuit 修复首轮复跑中，`光迅科技 / 杭电股份 / 中天科技 / 利通电子` 已先从 `暂无权威验证` 抬升到 `财报确认`；随后 authority 下载补充当前报告期业绩目录后，`胜宏科技` 也已补齐为 `财报确认`。
- [改进] `src/services/signal_cause_analysis_service.py` 为 `Fast Review Focus` 的 authority-first 解释层新增结构化公告与研报兜底：优先读取 `akshare.stock_gsrl_gsdt_em(...)` 作为 `announcements` 补充证据，并读取 `akshare.stock_research_report_em(...)` 压缩近窗机构观点，减少强趋势样本长期落在 `暂无权威验证`。
- [改进] `src/services/signal_cause_analysis_service.py` 新增 earnings-only 定向重抓：当 `get_fundamental_context(...)` 仅返回 `partial` 且缺少有效 `growth / earnings / earnings_quality` 时，会补走 `get_earnings_fundamental_context(...)` 并把结构化财报字段并回 authority 判断链，提升 `财报确认` 的可判定性。
- [测试] `tests/test_signal_cause_analysis_service.py` 补充结构化公告兜底、结构化研报兜底、当季财报确认与 earnings-only refetch 回归覆盖，配套离线回归继续通过。
- [新功能] `src/services/signal_cause_analysis_service.py`、`scripts/run_fast_review_bundle.py`、`src/services/fast_review_focus_service.py`、`api/v1/schemas/signals.py` 与 `apps/dsa-web/src/pages/SignalsPage.tsx` 为 `Fast Review Focus` 新增 authority-first 解释层，按 `公告 > 财报 > 研报 > 普通新闻/题材` 输出 `authority_judgement / authority_level / authority_reason_summary / authority_evidence_digest` 及三类证据摘要，并在 UI 以紧凑 `Authority` 区块展示。
- [测试] 补充 `tests/test_signal_cause_analysis_service.py`、`tests/test_fast_review_daily_bundle.py`、`tests/test_fast_review_focus_api.py` 与 `apps/dsa-web/src/pages/__tests__/SignalsPage.fastReview.test.tsx`，覆盖 authority 判定优先级、bundle 导出、API fail-open 与前端展示。
- [改进] `src/services/signal_cause_analysis_service.py`、`scripts/run_fast_review_bundle.py`、`src/services/fast_review_focus_service.py`、`api/v1/schemas/signals.py` 与 `apps/dsa-web/src/pages/SignalsPage.tsx` 现在会补齐并展示 `preferred_industry_label / mainline_judgement / mainline_evidence_sources`；`Fast Review Focus` 详情可直接看到“主线判断/证据来源”，同时像 `中天科技` 这类宽行业桶样本会优先按更准确的业务标签开头解释。
- [改进] `src/services/signal_cause_analysis_service.py` 将 fast-review 中 AI 链样本的 fallback 解释继续收紧：有稳定 AI 证据时，优先明确写成 `AI主线景气扩散` 或 `AI主线景气向上游材料链扩散`，而不是停在泛化的“结构性走强”。
- [修复] `src/services/signal_cause_analysis_service.py` 为 `车 / 储能 / 电池` 这类主题映射增加低信号业务词抑制：若证据仅来自弱组件文本、而更高层业务身份仍是 `光通信 / 海缆 / 电力设备 / 通信设备`，则不再被强行映射成新能源链主题。
- [文档] 更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充 `2026-05-09` fast-review 解释收紧、主题误伤抑制与代表样本 bundle 回放留痕。
- [改进] `src/services/fast_review_focus_service.py` 将 fast-review 导出补行情的总 quote 预算改为按可见缺失行数动态放大并设置上限，减少 `strategy_focus` 中后排可见样本因 25 秒预算耗尽而缺少 `涨幅/PE` 的情况。
- [修复] `src/services/signal_cause_analysis_service.py` 为 `有色 / 涨价资源` 主题映射增加低置信度业务词抑制：像 `亨通光电` 这类仅因 `product_type/product_name` 出现 `铜导体`、但主营仍是 `海缆/电力设备/光通信` 的样本，不再误映射成资源涨价主线。
- [改进] `src/services/signal_cause_analysis_service.py` 收紧 fast-review 解释中的 `earnings` 偏置判定：只有 `earnings_surprise` 或存在明确 `报告期 + 事件日/业绩关键词` 时才落成 `supply_demand_bias=earnings`，避免 `trend_leader` 样本仅因带有 `earnings` tag 就被误读成业绩主导。
- [改进] `src/services/signal_cause_analysis_service.py` 扩展二线个股业务归因覆盖，补齐 `宏和科技 / 润泽科技 / 烽火通信 / 亨通光电` 的 `business_summary`，并把 `光通信` 从“默认视为 AI 算力供应链”收紧为“需要额外 AI 线索确认”。
- [修复] `src/services/fast_review_focus_service.py` 与 `scripts/run_fast_review_bundle.py` 现在会优先重建 canonical `earnings_anchor=报告期@事件日`；像 `688235 百济神州` 这类旧 CSV 中仅保留事件日的样本，读层与导出层都会自动纠正。
- [改进] `src/services/signal_cause_analysis_service.py`、`scripts/run_fast_review_bundle.py`、`src/services/fast_review_focus_service.py` 与 `/signals` 的 `Fast Review Focus` 现在会补齐一组解释结构字段：`business_labels / business_summary / chain_role_label / theme_label / theme_source / earnings_anchor / supply_demand_bias`；用于把“主营归因、链条位置、题材映射来源、业绩锚点、供需/涨价/业绩偏向”从纯文案里拆出来，便于区分 `东山精密` 这类上游景气驱动、`胜宏科技` 这类业务主线，以及“纯题材映射”样本。
- [改进] `src/services/fast_review_focus_service.py` 现在会对 `quick_report` 样本优先按公告事件日对齐 `report_date / report_period_label`；若发现已有快照仍停留在旧年报、且当前季度财报金额并未实际拿到，则会清空错配的 `revenue_amount / net_profit_amount`，避免 `fast_review_earnings_focus` 与主汇总把旧年报金额误挂到新季度事件上。
- [改进] `scripts/run_fast_review_bundle.py` 现在会在 `fast_review_earnings_focus.csv/.md` 导出阶段复用 `FastReviewFocusService` 补齐缺失的 `涨幅 / PE / 报告期 / 营收 / 净利` 快照字段，仅命中业绩信号的样本也能直接在主汇总里看到完整 snapshot。
- [改进] `scripts/run_fast_review_bundle.py` 现在会在 `fast_review_earnings_focus.md` 与 `fast_review_summary.md` 的“今日业绩焦点”区块直接展示紧凑 `snapshot` 列，默认带出 `涨幅 / PE / 报告期 / 净利`，让主摘要阅读不必再回到 CSV 查当天涨幅和季度净利润量级。
- [改进] `src/services/signal_cause_analysis_service.py` 继续收紧快复盘 `overseas_theme` 误映射：当主题证据只来自概念板块池、没有行业/主营/财务文本共同支持时，不再直接落成最终海外主题映射，优先压掉 `新能源车 / 充电桩 / 电池` 这类 board-only 噪音。
- [改进] `scripts/run_fast_review_bundle.py` 修正 `earnings + hundred_day_high` 交叉样本的上涨原因补充逻辑：若焦点行现有 `reason_summary` 实际来自业绩行，即使排序主来源是 `hundred_day_high`，仍会继续按业绩那条补 `业务侧先按 ... 跟踪 / 当前先按业绩驱动看待`，并同步补齐 `cause_tags / industry_logic / news_logic / technical_logic`。
- [改进] `scripts/run_fast_review_bundle.py` 现在会在 `fast_review_earnings_focus.md` 与 `fast_review_summary.md` 的“今日业绩焦点”区块直接展示 `rise_reason / tags`，让 `福达合金` 这类 `earnings + hundred_day_high` 样本无需再翻 `strategy_focus.csv` 就能看到业务侧解释。
- [改进] `src/services/signal_cause_analysis_service.py` 继续放宽了 `AI上游材料链` 的 fallback 识别：除人工 alias 外，主营/产品文本中出现 `覆铜板 / 载板 / 封装载板 / 高频高速 / 高速材料 / 光模块材料 / 封装材料` 这类高级电子材料线索时，也会更稳定归为 `电子材料，偏AI上游材料链`。
- [改进] `scripts/run_fast_review_bundle.py` 现在会仅对已配置 `business alias` 的高频 AI 链个股，在 `fast_review_strategy_focus` 读取层强制刷新 `reason_summary / cause_tags / logic`，避免历史 CSV 里的旧解释长期覆盖新的主营归因；`2026-05-07` 复盘样本中 `光迅科技 / 华工科技 / 胜宏科技` 已可稳定展示 `光模块/光通信` 或 `PCB，偏AI算力供应链` 这类更贴近业务的说明。
- [改进] `src/services/signal_cause_analysis_service.py` 新增一层轻量 `business alias` 覆盖，先稳定补齐 `东山精密 / 胜宏科技 / 华工科技 / 光迅科技` 这类高频 AI 链公司的业务归因。即使原始主营文本噪音较大，快复盘也能更稳定落到 `AI上游材料链`、`PCB，偏AI算力供应链`、`光模块/光通信，偏AI算力供应链` 这类表达。
- [改进] `src/services/signal_cause_analysis_service.py` 的快复盘 fallback 解释现在会更明确地区分 `AI上游材料链` 与普通 `AI算力供应链`：当主营/产品线更像 `PCB/光模块材料/电子材料/精密组件`，且新闻或证据同时命中 `供需/景气` 线索时，`reason_summary / industry_logic / news_logic` 会优先写成更接近“AI上游材料供需偏紧、景气驱动”的表述，减少把这类票笼统写成题材映射。
- [改进] `src/services/fast_review_focus_service.py` 现在为 fast review 的 `report/valuation` 补充链路增加总预算控制：导出与 `/api/v1/signals/fast-review-focus` 会优先补齐前排焦点股的季度/年报与 PE 信息，但在预算耗尽后直接 fail-open 返回，避免页面读取或复盘重建再次被逐股 fundamental enrichment 拖慢。
- [改进] `src/services/fast_review_focus_service.py` 的 `quote` 兜底补充现改为“热缓存优先、冷启动轻量化”：只有 `Akshare EM` 全市场实时行情缓存已经热起来时才复用批量快照；冷启动默认改走预算内单票轻量 quote，避免只补少量焦点股时也先耗完整轮全市场快照预热。
- [改进] `scripts/run_fast_review_bundle.py` 的 `fast_review_strategy_focus.md` 与 `fast_review_summary.md` 现会在焦点表中直接展示紧凑 `snapshot` 摘要列，默认带出 `今天涨幅 / PE / 报告期 / 营收 / 净利润`；同时导出阶段只对 Markdown 实际展示的焦点行做补充 enrichment，减少同日重建时的长尾耗时。
- [改进] `scripts/run_fast_review_bundle.py`、`src/services/fast_review_focus_service.py`、`/api/v1/signals/fast-review-focus` 与 `/signals` 的 `Fast Review Focus` 现在会补齐 `today_change_pct / PE / report period / net profit / revenue` 这一组复盘快照字段；网页右侧详情新增 `Market & Earnings` 区块，直接显示今天涨幅、PE、季度/年报口径、净利润与营收。
- [改进] `src/services/signal_cause_analysis_service.py` 的快复盘上涨原因 fallback 现优先抽取 `business_labels / business_summary`，用于把 `PCB / 光模块 / 光通信 / AI服务器 / 半导体` 这类主营辨识度写进 `reason_summary`，并提高业务标签在 `overseas_theme` 映射中的权重；若新闻关键词偏 `供需/景气` 或 `涨价`，`news_logic` 也会显式区分催化类型，减少题材误映射。
- [改进] `config/local_strategy_profile.json` 进一步收紧 repo 级日常 `trend_leader` fast-review 共享前筛默认值到 `listed_days>=120 + 60d_change>=17.0% + turnover_rate>=1.5 + positive day`；`2026-05-07` 同口径探针下 prepared universe 约收窄到 `736`，优先解决复盘宽度过大问题，同时保持脚本原始 CLI 默认值不变。
- [改进] `scripts/select_earnings_surprise_candidates.py` 在 fast-review 的 `latest_report_period` 业绩目录准备阶段会显式收窄到“当前报告期”抓取，不再为同一轮快复盘额外扫描旧季度目录；`2026-05-07` 回放中 `earnings` 阶段耗时由 `254.23s` 收敛到 `108.38s`，全 bundle 总耗时由 `619.7s` 降到 `447.5s`，候选数与 A/B/四层结构保持不变。
- [改进] `scripts/run_fast_review_bundle.py` 现在会为 `hundred_day_high` 快复盘入口显式下传更紧的前筛参数：默认 `listed_days>=120 + 60日涨幅>=12.0% + 换手率>=0.8 + 当日涨跌幅为正 + 排除ST`，在不改动 `breakout_loose` 价格规则的前提下收窄扫描宽度并降低历史抓取尾部耗时。
- [改进] `scripts/run_fast_review_bundle.py` 进一步收紧 `strategy_focus` 的 `A类` 口径：仅保留已出现价格确认的强样本，`trend_leader only` 与 `earnings only` 不再因 `tier=core` 或单独业绩强度直接进入 `A类`；`2026-05-06` 回放已收敛到 `A=23 / B=450`，且 `trend_only_A=0`、`earnings_only_A=0`。
- [测试] 新增 `scripts/run_pytest_batches.py` 与 `scripts/run-pytest-batches.ps1` 本地分批 pytest 回归入口，支持按批输出进度、单批耗时、失败批次摘要与 PowerShell 包装调用，便于在 Windows 本机规避整套 `pytest -m "not network"` 单进程内存上涨导致的 `137` 退出。
- [改进] `/signals` 的 `Fast Review Focus` 读层界面收紧为“扫描 + 阅读”结构：顶部 summary 从多张统计卡压缩为紧凑快照与 A/B/阶段组合，左侧焦点列表改为高密度扫描项，右侧详情改为 `Core Takeaway / Logic Breakdown / Risk & Signals` 三段阅读面板。
- [新功能] `/api/v1/signals/fast-review-focus` 新增 fast review 焦点读取接口，返回 `fast_review_strategy_focus.csv` 的 A/B、stage、driver、signals、priority score、summary 统计与来源路径，并供 `/signals` 轻量复盘视图使用。
- [改进] `scripts/run_fast_review_bundle.py` 进一步收紧 `strategy_focus` 的 `半兑现` 口径：仅保留“业绩已出现且趋势已启动但尚未完全确认”的样本，原先大量 `earnings + non-trend` 的观察票现下沉到 `拐点`，便于压缩 `半兑现` 噪音。
- [改进] `scripts/run_fast_review_bundle.py` 现为 `strategy_focus` 额外输出独立的 `兑现/半兑现/拐点/纯轮动` 四层阶段字段，用来把“业绩已确认”与“仍属观察/轮动”拆开；保留原有 `driver_label` 语义不变。
- [改进] `config/local_strategy_profile.json` 将日常 `trend_leader` fast-review 共享前筛默认收紧为 `60日涨幅>=8.0%`、`换手率>=1.2`，用于先压缩 700+ 级别的准备扫描 universe，降低趋势快复盘噪音与耗时。
- [改进] `scripts/select_earnings_surprise_candidates.py` 的 `earnings_surprise` 在 `db=None` 的 `low/medium` 快复盘链路中，现改为“全量轻量资金画像 + 前 15 只重点补全”模式：所有通过样本先走 `lightweight_fast_review`，仅优先级最高的 `15` 只升级到 `full_priority_refresh` 并补抓市场预期参考，非重点样本显式标记 `skipped_fast_review`；`2026-04-30` 实盘回放里 `signal_earnings_elapsed_sec` 已从约 `466.33s` 收敛到 `136.57s`，但主筛选阈值与打分口径不变。
- [改进] `scripts/select_earnings_surprise_candidates.py` 的 `earnings_surprise` 低深度快扫现在会在 `recent_event` overlay 已足够覆盖公告摘要与核心增速字段时直接 bootstrap 轻量 bundle，不再为这批样本逐只 fresh fetch 基础面 bundle；该优化主要收敛财报密集日的 `earnings` 尾部耗时，不改变 `strategy_profile`、分数线或快复盘口径。
- [改进] `scripts/run_fast_review_bundle.py` 现在会在 `fast_review_strategy_focus.*` 与 `fast_review_summary.md` 中输出 `A类/B类` 复盘分层和 `业绩兑现型/拐点观察型/事件驱动型/题材情绪型` 驱动标签，保持三条每日主策略筛选逻辑不变。
- [改进] `scripts/run_fast_review_bundle.py` 新增可选 `--manual-review-labels-file` 人工复盘样本校准入口；当提供标签文件时，bundle 会额外输出 `fast_review_manual_calibration.csv/.md` 并在汇总中展示 `matched / mismatch / missing_in_results`，便于按真实样本持续收紧 A/B 与驱动分类口径。
- [修复] `run_shortline_hub.py` 现已把 `DatabaseManager` 正式注入 `ShortlineHubOrchestrator`；此前真实 CLI/进程链路里 `snapshot_driver_evidence` 实际一直拿到 `db_manager=None`，导致 `earnings_surprise / commodity_beneficiary__* / trend_leader_unified` 的硬逻辑快照虽已接入代码却未真正生效。修复后，`2026-05-04` 这类短线复盘已能把具备近几日业绩硬逻辑的样本从 `flow_only` 抬升到 `top_pick/watchlist`。
- [修复] `select_trend_leader_candidates.py`、`select_earnings_surprise_candidates.py` 与 `collect_commodity_beneficiary_snapshots.py` 现在会把非交易日 `--snapshot-date` 自动回退到最近的 A 股交易日；像 `2026-05-04` 这类休市日会实际按 `2026-04-30` 产出上游快照，不再因硬跑休市日而错过可复用数据。
- [改进] `shortline_hub` 的快照硬逻辑证据层已从“仅同日”扩展为“同日优先、近 3-5 个交易日回看兜底”；当 A 股休市日或当日尚未产出 snapshot 时，`earnings_surprise`、`commodity_beneficiary__*` 与保守 `trend_leader_unified` 的近期有效快照仍可把真有业绩/涨价/产业催化的票从 `flow_only` 抬升出来，证据同时会显式标注命中的 snapshot 日期。
- [改进] `scripts/run-shortline-daily.ps1` 现已调整为先产出 `trend_leader_unified`、`earnings_surprise` 与 `commodity_beneficiary` 快照，再执行 `run_shortline_hub.py`；`-DryRun` 也会按同样顺序打印完整命令，确保短线日跑默认先补齐上游“硬逻辑库存”。
- [改进] `shortline_hub` 新增同日快照“硬逻辑来源”接线：`driver_support` 现在会优先读取 `earnings_surprise`、`commodity_beneficiary__*` 与保守条件下的 `trend_leader_unified` 快照证据，再回退到解释文本关键词；真正有业绩/涨价/产业催化来源的短线票可从 `flow_only` 抬升为强逻辑驱动。
- [改进] `shortline_hub` 新增“逻辑支撑层” `driver_support`：把强逻辑驱动、题材接力和纯资金接力分开处理，避免 `daily` 之类的英文片段误触发 `AI` 规则，也避免模板化“扩散/情绪锚点”被误抬成强产业突破；真实 `2026-05-04` 回放已收敛为 `review_tier_counts={"watchlist":5}`。
- [新功能] 新增 `scripts/run-shortline-replay-validation.ps1`，把短线历史回放验收收口成固定三段：前一交易日基线、目标日 cold run、目标日 warm rerun；三段共用同一份 tracking history 与 explain cache，便于复核 `tracking_repeat_symbol_count`、`quality_verdict` 与 cache warm-up 是否按预期收敛。
- [改进] `shortline_hub` 细化了 `WonderTrader` 的量能重建风险：当仅少量近期历史条目缺失 `volume`、但 `amount` 推导的放量比例与 snapshot `volume_ratio` 一致时，风险标签会降级为 `volume_ratio_validated_by_amount_history`，不再一律按 `volume_reconstructed_from_amount` 压到 `watchlist`；真实 `2026-05-04` 回放已从 `watchlist=5` 收敛到 `top_pick=4, watchlist=1`。
- [修复] `shortline_hub` 的 `WonderTrader` 真实回放在节假日手工 `trade_date` 场景下，现会优先参考 AkShare 交易日历兜底非交易日判断；即使本地 `tushare_trade_cal_sse.csv` 误标或缺失，`2026-05-04` 这类休市日也不再把 `history_asof_*` 误升成 `high_risk_mover`。
- [修复] `scripts/run_fast_review_bundle.py` 不再把共享 `--limit` 下传给 `hundred_day_high` 主筛选；新增 bundle 侧 `--hundred-day-output-limit`（默认 `30`，`0` 表示不裁剪）在载入结果后限制导出行数，避免快复盘误把百日新高输入 universe 压缩成 `no_rows`。
- [改进] `shortline_runs_summary.json/.md`、`bundle_manifest.json` 与 `bundle_report.md` 现会显式落盘质量判定依据，包括 `tracking_history_ready`、`source_real_engine_expected_passed` 与顶层 `quality_failed_due_to`，便于直接判断 `degraded`/`quality_failed` 的来源。
- [改进] `shortline review bundle` 顶层状态现兼容回退：`status` 恢复为旧的执行结果语义，新增 `overall_status` 表达 `quality_failed` 这类总体结论，便于旧脚本继续只认 `success/failed`，同时保留质量失败可见性。
- [改进] `tracking_continuity_weak` 现在只会在 shortline 已具备跨日比较基础时触发；首日运行、只有同日样本或 tracking 冷启动场景不再被默认降级成连续性告警。
- [改进] `shortline review bundle` 顶层状态语义已拆分为 `execution_status` 与 `status`：当步骤执行成功但 `quality_summary.verdict=fail` 时，`bundle_manifest.json` / `bundle_report.md` / pointer 会显式标记 `status=quality_failed`，避免被普通 `success` 掩盖。
- [改进] `scripts/summarize_shortline_runs.py` 与 `scripts/run_shortline_review_bundle.py` 新增 `quality_profile` 与 `quality_summary`，现在可直接在 `shortline_runs_summary.json/.md`、`bundle_manifest.json` 和 `bundle_report.md` 看到 `pass/degraded/fail/off` 质量结论，以及失败项、告警项和排查建议。
- [改进] `scripts/run-shortline-review-bundle.ps1` 现已补齐 wrapper 级 `-TrackingHistoryPath`，可直接从 PowerShell 入口透传共享 tracking history 文件到 `run_shortline_review_bundle.py` / `run_shortline_hub.py`，便于复用同一份跨日跟踪历史做 bundle 复盘验证。
- [修复] `scripts/summarize_shortline_runs.py` 现已纳入 `shortline_review_bundle/*/shortline_run` 子目录，并按 `run_id` 只保留最新一份，避免 bundle 重跑后 `runs_summary` 遗漏当前短线子运行或重复统计同一 `run_id`。
- [修复] `shortline_hub` 的 `WonderTrader` 真实桥接默认源优先级已调整为 `prefer_real_engine`；只有显式传入 `prefer_bridge_data` 时才优先读取本地 `bridge_data`，避免真实日常复盘被示例导出文件劫持。
- [改进] `run_shortline_hub.py`、`run_shortline_review_bundle.py` 以及 `run-shortline-daily.ps1` / `run-shortline-fullcheck.ps1` / `run-shortline-review-bundle.ps1` 现已统一透传 `wt_source_mode`，同时 `shortline_report.md` 结果概览新增 `tracking_repeat_symbol_count / tracking_longest_streak_days / tracking_focus_symbols`。
- [测试] 已完成真实串行验证：`2026-05-03` 与 `2026-05-04` 两次 `run-shortline-daily.ps1` 复盘均稳定命中 `scan_source_counts={"wondertrader_real_engine":5}`，并在 `2026-05-04` 正常识别 `tracking_repeat_symbol_count=5`、`tracking_longest_streak_days=2`。
- [改进] `shortline review bundle` 根报告与 `bundle_manifest.json` 现在会显式汇总 `tracking_repeat_symbol_count` 和 `tracking_longest_streak_days`，便于先在 bundle 根目录判断连续跟踪情况。
- [改进] `scripts/run_shortline_review_bundle.py` 与 `scripts/run-shortline-daily.ps1` 现已支持透传统一的 tracking history 路径，便于真实 `WonderTrader + FinGenius` 串行日跑复用同一份历史跟踪文件。
- [测试] 已完成真实 `run-shortline-daily.ps1` 串行验证：`2026-05-02`/`2026-05-03` 与 `2026-05-03`/`2026-05-04` 两组样本均可落盘 tracking history；本次未出现自然重复股，主要因为上游候选源在 `wondertrader_real_engine` 与 `wondertrader_export` 之间切换。
- [修复] `shortline_hub` 修正真实 `WonderTrader` 历史回放的 `trade_date` 对齐：回放前先截断到 `<= trade_date`，避免误报 `history_asof_*`，并将 `volume_reconstructed_from_amount` 从硬性降级调整为保留扣分的软风险，允许真实强势股在可接受数据质量下进入 `watchlist/top_pick`。
- [修复] `shortline_hub` 继续收紧 `volume_reconstructed_from_amount` 的复盘分层：这类样本现在最多只进入 `watchlist`，不再直接升到 `top_pick`。
- [改进] `shortline_hub` 现已真正接通历史跟踪闭环：同一股票跨日重复进入短线结果时，会写入 `tracking_appear_streak_days / tracking_last_seen_dates / tracking_tier_transition`，并同步落盘 `shortline_tracking_history.json/csv`；同日重跑不会重复抬高 streak。
- [改进] `run_shortline_hub.py`、`run-shortline-daily.ps1`、`run-shortline-fullcheck.ps1` 与 `run-shortline-review-bundle.ps1` 现已打通 `explain cache` 入口；日常短线、fullcheck 与 bundle 默认共享 `data/runtime/shortline_hub/explain_cache/shortline_explain_cache.json`，真实复盘重跑时可直接复用同日解释结果。
- [修复] `shortline fullcheck` 不再误复用 `daily/light` explain cache；`run_shortline_hub.py` 现会在 `--fg-enable-big-deal` 场景下自动切到 `explain_cache_mode=full`，`run-shortline-fullcheck.ps1` 也显式透传 `full` 模式，保证 `BigDealAnalysisTool` 真实执行。
- [改进] `shortline_runs_summary.json/.md` 与 `shortline review bundle` 根层 `Runs Summary Runtime` 新增 recent-run 异常提醒，首版会标记 `cache_hit_ratio_low`、`parallel_workers_single` 与 `tool_errors_present`，便于快速发现解释链路退化。
- [改进] `scripts/summarize_shortline_runs.py` 新增 shortline explain cache / 并发统计汇总，`shortline_runs_summary.json/.md` 现会输出最近几次 run 的 `explain_cache_hit_count`、`explain_cache_miss_count` 与 `explain_parallel_workers`，便于比较日常复盘与 fullcheck 的真实解释成本。
- [改进] `shortline_hub` 的 `run_summary.json`、`shortline_report.md` 与 `shortline review bundle` 根摘要新增 explain cache / 并发统计，现可直接查看 `explain_cache_enabled`、`explain_cache_hit_count`、`explain_cache_miss_count` 与 `explain_parallel_workers`，更快判断当轮耗时主要来自缓存 miss 还是实时解释。
- [改进] 新增 `scripts/shortline-wrapper-common.ps1` 作为短线 PowerShell 包装公共层，`run-shortline-review-bundle.ps1`、`run-shortline-daily.ps1` 与 `run-shortline-fullcheck.ps1` 现统一复用同一套解释器解析、路径检查与 dry-run 前置逻辑。
- [改进] `run-shortline-daily.ps1` 与 `run-shortline-fullcheck.ps1` 新增 wrapper 级 `-DryRun`，可在不触发真实 `WonderTrader + FinGenius` 链路的情况下打印解析后的 `run_shortline_hub.py` 命令，便于安全验证短线入口包装层。
- [改进] `run-shortline-daily.ps1` 与 `run-shortline-fullcheck.ps1` 现已与 `run-shortline-review-bundle.ps1` 统一解释器解析策略：优先解析 `py -3.10`，回退 `Get-Command python`，并让 `WonderTrader` 默认复用同一 repo 解释器，减少同机不同短线入口落到不同 Python 版本的偏差。
- [改进] `shortline review bundle` 的解释器选择已收紧：Python 入口默认跟随当前 `sys.executable`，PowerShell 包装优先解析 `py -3.10`，并让 `WonderTrader` 默认复用同一解释器；同时修复包装脚本在空 `RunId` 下会透传空 `--run-id` 导致 dry-run 报错的问题。
- [改进] `shortline review bundle` 的根报告章节顺序已调整为“先摘要、后明细”：默认先展示 `Shortline Runtime / Runs Summary Runtime / Fast Review Runtime`，再展示各子目录路径、治理指针和步骤日志，日常复盘更适合快速扫读。
- [改进] `shortline review bundle` 的失败根摘要继续增强：`bundle_manifest.json` 与 `bundle_report.md` 现会直接给出失败 step 的 `stdout/stderr` 日志路径、excerpt 与 `pending_steps`，便于根目录快速判断故障位置和未执行链路。
- [改进] `shortline review bundle` 的根报告继续补齐 `Shortline Runtime / Runs Summary Runtime` 两层紧凑摘要，现可直接看到本轮短线解释数量、分层分布、上游工具命中、最近 run 候选数与 `top_symbols`，减少再进入子目录翻 `run_summary.json` 的次数。
- [改进] `shortline review bundle` 的父控制台日志已收敛：成功子步骤不再把整段 stdout/stderr 原样回放到 bundle 父进程，而是只输出 `stdout_lines / stderr_lines / stdout_log / stderr_log` 紧凑摘要，详细内容仍保留在 bundle 根目录 `logs/` 下。
- [改进] `shortline review bundle` 的 `fast_review.runtime_summary` 继续补齐根层紧凑汇总，现会额外输出 `total_signal_count / total_signal_elapsed_sec / skipped_reason_counts`，便于直接从 bundle 根报告判断本轮信号是否产出候选以及跳过主因。
- [改进] `shortline review bundle` 新增 `artifact_governance` 与统一 pointer 索引：每次运行都会在 `data/manual_runs/shortline_review_bundle_index/<trade_date>/<run_id>/bundle_pointer.json` 与 `.../latest.json` 写入指针，便于从固定位置回查 `latest / custom / smoke / dry_run` 各类输出目录。
- [改进] `shortline review bundle` 的 `fast_review.runtime_summary` 现已稳定化，除 `signal_*_elapsed_sec` 外还会汇总 per-signal `count / csv_path`、总耗时与关键输出路径。
- [改进] `shortline review bundle` 新增 `fast_review` 结构化运行摘要抽取：会从 step 日志中汇总 `include_signals / persist_snapshots / signal_*_elapsed_sec / skipped_signal` 到 `bundle_manifest.json` 与 `bundle_report.md`。
- [改进] `shortline review bundle` 在子步骤失败时不再只抛异常退出；现在仍会落盘 `bundle_manifest.json`、`bundle_report.md` 和 step 日志，并附带 `failed_step / error_message / stderr_excerpt` 便于复盘。
- [测试] 扩展 `tests/test_shortline_review_bundle.py`，覆盖 `fast_review` runtime summary 抽取与 step 失败时的 manifest/report 诊断留痕。
- [改进] `scripts/run_shortline_review_bundle.py` 新增 bundle 控制面增强：支持独立控制 `fast_review` snapshot 持久化，并支持 `--dry-run` 只落命令与 manifest、不真正执行子步骤。
- [改进] `shortline review bundle` 的 `bundle_manifest.json` 与 `bundle_report.md` 现会记录每一步的 `status / elapsed_ms / workdir / stdout_log_path / stderr_log_path`，日志统一落在 bundle 目录 `logs/` 下，便于定位哪一步变慢或失败。
- [文档] 更新 `docs/architecture/2026-05-02-shortline-hub-single-machine-setup.md` 与 `docs/LOCAL_STRATEGY_CATALOG.md`，补充 bundle 的 dry-run、fast-review persist toggle 与 per-step telemetry 说明。
- [新功能] 新增 `scripts/run_shortline_review_bundle.py` 与 `scripts/run-shortline-review-bundle.ps1` 单入口短线 bundle，按顺序编排真实 `shortline_hub` 日跑、`shortline_runs_summary` 汇总与 `fast_review` 短线观察，并固定落到 `data/manual_runs/shortline_review_bundle_latest/`。
- [测试] 新增 `tests/test_shortline_review_bundle.py` 与 `tests/test_shortline_review_bundle_script.py`，覆盖短线 bundle 命令编排、manifest/report 产物与单机默认路径约束。
- [修复] `shortline_hub` 的 snapshot 持久化改为按 `shortline_top_pick / shortline_watchlist / shortline_high_risk_mover` 三组做同日整批替换；重复手工重跑同一 `trade_date` 时，快复盘不再混入更早同日 run 的旧短线股票。
- [修复] `scripts/run_fast_review_bundle.py` 的 `短线观察` 摘要在同日 shortline snapshot 只有 `high_risk_mover` 时，不再显示 `top_symbols: none`，而会回退展示高风险异动中的前几只股票。
- [文档] `docs/architecture/2026-05-02-shortline-hub-single-machine-setup.md` 与 `docs/LOCAL_STRATEGY_CATALOG.md` 现已明确：单机 `WonderTrader` bridge 更推荐直接使用当前工程 Python 作为 `--wt-python-executable`，以稳定走通 `wondertrader_real_engine` 路径。
- [新功能] `shortline_hub` 新增 `snapshot_sync` 持久化链路，`scripts/run_shortline_hub.py --persist-snapshot` 现可把 `top_pick / watchlist / high_risk_mover` 结果写入 `kline_signal_snapshot`。
- [改进] `SignalSnapshotService` 与 `/api/v1/signals/kline-snapshot-counts` 现已默认纳入 `shortline_hub / shortline_top_pick / shortline_watchlist / shortline_high_risk_mover`，可直接查询短线快照计数。
- [改进] `scripts/run_fast_review_bundle.py` 现会优先读取同日短线 snapshot，并在 `fast_review_summary.md` 自动附加 `短线观察` 摘要；若无 snapshot，则回退读取最近同日 `shortline_*` 的 `run_summary.json`。
- [改进] `shortline_hub` 新增手工观察批量解释、同日 explain cache、历史跟踪字段与日报 `今日主方向 / 历史跟踪摘要` 层，便于连续日常复盘。
- [测试] 新增 `shortline` snapshot sync、CLI persist、signals shortline counts、fast review shortline summary 的回归覆盖。
- [改进] `shortline_hub` 的 `shortline_report.md` 继续收紧复盘分层展示：当候选全部落入 `high_risk_mover` 时，`今日最强` 不再重复展示同一批高风险名单，而是改为明确提示用户直接查看下方 `高风险异动`。
- [改进] `shortline_hub` 的解释链路改为并行执行；在 `top_n=3` 的真实短线日跑中，`orchestrator_explain_elapsed_ms` 已从约 `108s` 收敛到约 `46s`，更适合每日复盘直接使用。
- [改进] `shortline_hub` 新增 `board_core_rank / board_core_bonus`，并把“板块前排”信息写入短线日报；同板块多只候选同时出现时，真正的相对龙头会获得额外加分。
- [改进] `shortline_hub` 收紧了短线分层打分与层级规则：`trigger_score` 现在做上限截断并重新加权，`volume_reconstructed_from_amount / missing_volume_history / history_asof_*` 等数据质量风险会显式压低层级；真实日跑中的高强度但数据质量存疑样本不再直接进入 `top_pick`。
- [改进] `shortline_hub` 新增短线分层复盘能力：候选现在会自动打上 `涨停接力 / 放量突破 / 高换手博弈 / 板块龙头跟随 / 趋势强势跟随` 等短线类别，并计算综合分、同类排名与 `top_pick / watchlist / high_risk_mover` 复盘层级。
- [改进] `shortline_report.md` 重构为更接近日常短线复盘的日报结构，新增 `今日最强 / 观察名单 / 高风险异动 / 明日观察点`，同时保留 `候选概览 / 候选明细 / 逐票说明`，便于直接盘后阅读。
- [改进] 新增 `scripts/show_shortline_report.py`，用于在本机快速预览 `shortline_report.md`；交互终端下会优先用 UTF-8 输出，减少 PowerShell 中把正常报告误判成乱码的情况。
- [改进] `scripts/check_shortline_bridge_setup.py` / `src/shortline_hub/bridge_check.py` 调整短线桥接健康判定：当 `bridge_data` 缺失但 `WonderTrader / FinGenius` 脚本 smoke 与整链路 orchestrator smoke 都通过时，整体状态不再误报为 `warning`。
- [修复] `shortline_hub` 的 `WonderTrader` 实盘候选现在会对近期 `volume=0 但 amount>0` 的历史缓存行做基于 `amount/close` 的成交量重建，并在检测到 `amount` 掉成 `1/1000` 量级时自动补正，风险提示从笼统的 `missing_volume_history` 收敛为更准确的 `volume_reconstructed_from_amount`。
- [改进] 新增 `scripts/summarize_shortline_runs.py`，可汇总最近 `shortline_*` 手工运行的候选数、解释耗时、上游工具命中与前几只样本代码，便于直接比较 daily / fullcheck 的稳定性与成本。
- [修复] `shortline_hub` 的 `WonderTrader` 实盘候选在周末 `trade_date` 回放时不再误报 `history_asof_*`，保留真正有意义的 `missing_volume_history` 数据质量提示。
- [改进] 新增 `scripts/run-shortline-daily.ps1` 与 `scripts/run-shortline-fullcheck.ps1` 两个短线同机编排快捷入口，默认复用已验证的 `WonderTrader + FinGenius(.venv311)` 路径，并分别固化日常轻量复盘与单票全量核验参数。
- [改进] `shortline_hub` 现已明确推荐把 `FinGenius` 切到独立 `Python 3.11` 环境 `D:\bb\FinGenius\.venv311\Scripts\python.exe` 运行，并补齐了单机接入文档、最小依赖集与真实轻量/全量验证记录。
- [改进] `shortline_hub` 的短线报告与 `FinGenius` bridge 文案已统一重写为可读中文，`shortline_report.md` 中的标题、指标名、风险说明和代理大单摘要不再出现仓内生成文本乱码。
- [修复] 修正 `D:\bb\FinGenius\bridge\fg_explain_candidate.py` 薄包装后的真实 upstream 解析回退问题，外部 bridge 重新以本地 `D:\bb\FinGenius\upstream` 为工具根目录，可再次命中真实 `HotMoneyTool / ChipAnalysisTool / BigDealAnalysisTool`。
- [文档] 重写 `scripts/bridges/README.md`，正式明确短线默认工作流：每日批量复盘走轻量模式，单票深挖或抽样核验再显式加 `--fg-enable-big-deal`。
- [测试] 新增短线中文可读性回归，并完成一轮真实验证：轻量 `top_n=5` 跑出 `total_explain_elapsed_ms=166843`，两只 full 模式抽样都成功命中 `BigDealAnalysisTool`。
- [改进] `shortline_hub` 的 `FinGenius` process mode 新增 `--fg-enable-big-deal` 开关；默认轻量模式不再强制调用最慢的 `BigDealAnalysisTool`，只跑 `HotMoneyTool + ChipAnalysisTool`，并用已抓到的数据代理生成 `big_deal_summary`。
- [测试] 为 `FinGenius` 轻量/全量双模式补充合同回归，并完成真实 smoke；默认轻量模式下 `top_n=3` 的 `total_explain_elapsed_ms` 从 `229198ms` 降到 `101090ms`。
- [改进] `shortline_hub` 的 `FinGenius` 批量解释产物新增 `upstream_tool_elapsed_ms`，并在 `run_summary.json`、`shortline_report.md` 中汇总各 upstream 工具耗时，便于直接定位 `HotMoney/Chip/BigDeal` 的真实慢点。
- [改进] `shortline_hub` 现在会把 `FinGenius` 批量解释链路中的 `protocol_version / explanation_source / used_upstream_tools / tool_error_count / tool_errors / explain_elapsed_ms` 结构化落到 explanation、combined results、`run_summary.json` 与 `shortline_report.md`。
- [测试] 为 `FinGenius` batch instrumentation 新增 bridge、adapter、orchestrator、CLI 合同回归，并完成一轮真实 `process mode` smoke：`data/manual_runs/shortline_fingenius_instrumented_smoke_20260503/`。
- [改进] `scripts/bridges/shortline_fingenius_bridge_template.py` 与 `D:\bb\FinGenius\bridge\fg_explain_candidate.py` 现已支持 `bridge_data -> FinGenius upstream 单票工具(HotMoney/Chip/BigDeal) -> 启发式回退` 的解释顺序；同时兼容 UTF-8 BOM 请求文件，并在当前同机 Python 环境缺少 `loguru` 时自动注入轻量兼容层，让 `FinGenius` 单票真实工具解释可以直接跑通。
- [改进] `shortline_hub` 的 `shortline_report.md` 继续补齐复盘展示层：逐票说明里现在会直接展示 `价格 / 当日涨幅 / 60日涨幅 / 成交额 / 换手 / 量比`，其中 `amount` 会按 `亿/万` 做轻量格式化，便于盘后快速阅读；原始 JSON 数值语义保持不变。
- [改进] `shortline_hub` 的真实 WonderTrader 候选链路继续补齐了市场指标透传：真实引擎、仓内 process adapter、聚合结果以及外部 `D:\bb\WonderTrader\bridge\wt_export_candidates.py` 现在会保留 `change_pct_60d` 与 `amount`；同时当历史缓存最近多日 `volume=0.0` 但 `amount` 仍存在时，会显式打上 `missing_volume_history`，而不是伪造量能确认。
- [改进] `shortline_hub` 的真实 WonderTrader 候选富化补上了 `price/latest snapshot` 回退逻辑：当预筛快照里的 `latest_price` 缺失或为 `0.0` 时，会自动回退到真实引擎信号收盘价/最新历史收盘价，避免真实候选继续输出 `price=0.0`。
- [改进] `shortline_hub` 的真实 WonderTrader 信号规则继续向 WonderTrader 风格靠拢：在保留已验证可运行的股票 `CTA` 回测路径前提下，新增 `dual_thrust_breakout` 动态上轨突破分支；同时确认当前环境下 `ET_SEL + 股票` 原生回测仍会触发底层访问冲突，因此未强行切换到不稳定路径。
- [改进] `shortline_hub` 的 WonderTrader 桥接现已优先支持真实 `wtpy` 引擎路径：当同日 `bridge_data` 不存在时，会自动从当前工程加载 `src/shortline_hub/wondertrader_real_engine.py`，按 `bridge_data -> real engine helper -> repo spot cache -> placeholder` 顺序生成候选，并在历史缓存未追平请求日期时附带 `history_asof_<date>` 风险标记。
- [改进] 新增 `scripts/run_shortline_bridge_data_compare.py`，可自动写入一份 date-specific `bridge_data` 样本，并对照运行“bridge_data 模式”和“fallback 模式”，输出 `compare_summary.json` 与 `compare_report.md`，方便验证外部样本是否真的被当前 shortline 编排层消费。
- [文档] `scripts/bridges/README.md` 与 `docs/architecture/2026-05-02-shortline-hub-single-machine-setup.md` 补充了 `bridge_data` 样本对照命令，降低同机半真实验证门槛。
- [改进] `shortline_hub` 的 `shortline_report.md` 重写为更适合盘后复盘的结构，新增 `结果概览 / 复盘关注点 / 候选表 / 逐票说明`，并同步输出候选来源、板块/形态分布与风险标签汇总。
- [改进] `scripts/check_shortline_bridge_setup.py` 与 `src/shortline_hub/bridge_check.py` 现在会标记本地 `bridge_data` 的新鲜度；超过 3 天未更新的本地导出文件会被标成 `warning`，帮助区分“可用但偏旧”和“刚更新”的桥接数据。
- [改进] `scripts/bridges/shortline_wondertrader_bridge_template.py` 与 `D:\bb\WonderTrader\bridge\wt_export_candidates.py` 继续细化短线 `setup_tag`，新增 `涨停后高换手分歧`、`涨停后分歧承接`、`强势放量抢筹`、`高换手爆量博弈`、`板块核心跟涨` 等更贴近日常复盘的话术。
- [文档] `scripts/bridges/README.md` 更新 shortline 桥接样例与 `bridge_data` 新鲜度约定，避免文档仍停留在旧的 `setup_tag` 示例。
- [改进] `shortline_hub` 新增可选 `setup_tag` 字段；`scripts/bridges/shortline_wondertrader_bridge_template.py` 与 `D:\bb\WonderTrader\bridge\wt_export_candidates.py` 会基于 `trigger_type / change_pct / turnover_rate / volume_ratio` 打轻量短线形态标签，并同步透传到 `shortline_report.md` 与 `FinGenius` 解释层。
- [改进] `shortline_hub` 将初版 `setup_tag` 进一步细化为 `涨停高换手`、`涨停强势延续`、`放量突破`、`强势突破跟进`、`高换手博弈`、`活跃换手拉升`、`活跃换手推进`、`板块跟涨`、`相对强势整理` 等更贴近日常复盘的话术。
- [改进] `scripts/bridges/shortline_fingenius_bridge_template.py` 与 `D:\bb\FinGenius\bridge\fg_explain_candidate.py` 的启发式解释现在会使用真实 `board_name`，并写入 `hot_money_summary`、`risk_commentary` 与 `short_term_view`。
- [改进] `scripts/bridges/shortline_wondertrader_bridge_template.py` 与 `D:\bb\WonderTrader\bridge\wt_export_candidates.py` 在 `spot cache` 缺少行业字段时，会回退读取 `data/cache/reference/tushare_stock_basic_list.csv` 补齐 `board_name`，避免长期停留在 `spot_cache` 占位值。
- [改进] `D:\bb\FinGenius\bridge\fg_explain_candidate.py` 不再只有 placeholder 兜底；当 `bridge_data` 缺失时，会基于候选的 `trigger_type / trigger_score / change_pct / volume_ratio / turnover_rate / board_name / risk_flags` 自动生成启发式短线解释。
- [改进] `D:\bb\WonderTrader\bridge\wt_export_candidates.py` 不再只有 placeholder 兜底；当 `bridge_data` 缺失时，会自动发现同级 `daily_stock_analysis/data/cache/reference/kline_selector_spot_universe.csv` 并基于真实 spot cache 做轻量候选筛选，输出 `scan_source=wondertrader_cache_scan`。
- [改进] 新增 `scripts/check_shortline_bridge_setup.py` 与 `src/shortline_hub/bridge_check.py`，可统一检查 `D:\bb\WonderTrader` / `D:\bb\FinGenius` 的桥接脚本、`bridge_data`、单桥 smoke 与整链路 `process mode` smoke，便于短线编排在真实接线前先做环境自检。
- [改进] `D:\bb\WonderTrader\bridge\` 与 `D:\bb\FinGenius\bridge\` 的外部桥接脚本现在支持优先读取 `bridge_data` 下的本地导出文件，找不到时才回退 placeholder，便于在真框架未接通前先跑半真实整链。
- [修复] `shortline_hub --mode process` 修复了外部 `WonderTrader/FinGenius` 使用独立 `workdir` 时的相对 runtime 路径失效问题，当前工程现在可稳定调用 `D:\bb` 下的外部桥接脚本。
- [改进] 在 `D:\bb\WonderTrader\bridge\` 与 `D:\bb\FinGenius\bridge\` 落地可直接调用的外部桥接骨架脚本，后续只需替换内部占位逻辑即可接入真实短线扫描与解释流程。
- [改进] `shortline_hub` 现在额外导出聚合产物 `shortline_combined_results.json`，便于当前工程作为短线编排层直接沉淀“候选 + 解释”的最终结果。
- [文档] 补充 `docs/architecture/2026-05-02-shortline-hub-single-machine-setup.md`、`scripts/bridges/README.md` 与 `data/templates/shortline_hub/*.json`，明确单机 `WonderTrader + FinGenius` 桥接接线方式与请求样例。
- [改进] 新增 `scripts/bridges/shortline_wondertrader_bridge_template.py` 与 `scripts/bridges/shortline_fingenius_bridge_template.py`，作为 `shortline_hub --mode process` 的单机桥接模板脚本；后续可直接复制到本地 `WonderTrader` / `FinGenius` 目录并替换内部业务逻辑。
- [改进] `shortline_hub` 新增单机 `process mode`：当前工程可通过本机外部脚本 + JSON 文件协议调用 `WonderTrader` 候选输出与 `FinGenius` 解释输出；默认仍保留 `stub` 模式，方便逐步替换真实业务脚本。
- [新功能] 新增独立短线编排骨架 `src/shortline_hub/` 与 `scripts/run_shortline_hub.py`，用 stub 版 `WonderTrader` 候选输入和 `FinGenius` 解释输出先跑通协议、聚合和报告导出；当前尚未接入真实外部框架。
- [文档] 补充 `board_cycle_scan` 当前定位说明：这条线暂按“半自动专题工具”维护，板块池与板块成分仍需人工介入，不作为全自动主策略入口。
- [改进] 新增 `scripts/import_board_universe_seed.py` 与项目内官方板块成分 seed 路径 `data/board_cycle_scan_seed/board_universe.csv`；`scripts/select_board_cycle_candidates.py` 在未显式传 `--board-universe-file` 时会自动复用该 seed，并沿用 3 天人工维护口径。
- [改进] 新增 `scripts/refresh_board_concept_pool.py` 与 `data_provider/tushare_fetcher.py` 中的 `get_board_concept_pool(...)`，用于基于 `Tushare THS/DC` 维护本地题材板块池缓存；默认 3 天有效，远端刷新失败但本地已有旧缓存时保持 fail-open 继续复用。
- [改进] `board_cycle_scan` 新增扫描版本跟踪输出：每次运行除 `board_summary.*`、`board_stock_candidates.*` 外，还会生成 `board_change_summary.csv/md` 与 `board_tracking_history.csv`，自动对比上一轮同板块结果并标记 `label`、分数、龙头名单、业绩支撑数量是否发生更新。
- [文档] 新增 `docs/local_strategies/topics/board_cycle_scan.md`，并同步更新 `docs/LOCAL_STRATEGY_CATALOG.md`、`docs/LOCAL_STRATEGY_BASELINE.md` 与 `docs/AI_MODIFICATION_LOG.md`，登记 `board_cycle_scan` 为按需运行的本地策略专题且不进入默认每日主链路。
- [文档] 将 `board_cycle_scan` 的设计稿与实施计划迁入工程文档目录：新增 `docs/local_strategies/designs/2026-05-01-board_cycle_scan_design.md` 与 `docs/local_strategies/plans/2026-05-01-board_cycle_scan_implementation.md`，避免继续留在 `docs/superpowers/` 下形成双份入口。
- [改进] `scripts/select_board_cycle_candidates.py` 现在会在板块成分股为空时，自动把上游拉取失败 warning 写入 `run_summary.txt` 与 `board_summary.md`，避免把 `board_cycle_label=idle`、`constituent_count=0` 和空候选误读成有效市场结论。
- [改进] `board_cycle_scan` 新增 `--board-universe-file` 与 `--use-local-cache/--board-universe-cache-dir`，支持离线板块成分输入和远端失败后的本地缓存回退；同时新增 `leader_ratio`、`earnings_supported_ratio`、`board_reason_summary`、`board_rank`、`selection_reason` 等解释字段，并补齐一套可复跑的 `board_cycle_scan_golden_smoke_20260502` 离线 smoke 样本与产物。
- [改进] 新增 `scripts/generate_board_universe_template.py` 与固定模板文件 `data/templates/board_cycle_scan/board_universe_template.csv`，用于先生成 `board_cycle_scan` 的本地输入骨架；当远端板块成分接口失败或为空时，仍会输出带 `needs_manual_fill` 占位行的可编辑 CSV，而不是直接失败。
- [改进] `scripts/run_fast_review_bundle.py` 现在会为 `fast_review_strategy_focus.csv/md` 与 `fast_review_summary.md` 中的强势焦点股补充上涨原因摘要与标签，优先复用已有 `reason_summary / cause_tags`，缺失时再做轻量补算；该解释层增强当前不写回 snapshot。
- [改进] `scripts/select_trend_leader_candidates.py` 现在会为 `trend_leader_unified` 显式开启 `stale spot reference cache` 优先复用；`src/services/kline_selector_service.py` 同步增加该 opt-in 快路径，使趋势快扫在本地 `spot` cache 已过 TTL 但结构完整时可直接进入扫描准备，而不再先等待 live `spot` 失败。`2026-04-29 limit=120` 同口径 smoke 中，`prep_universe_elapsed_sec` 已进一步收敛到 `9.77s`，总耗时约 `13.03s`。
- [改进] `scripts/select_trend_leader_candidates.py` 的 `sector_rankings` 预热现在会优先请求 `prefer_stale_cache=True`；`data_provider/base.py` 同步为 `get_sector_rankings(...)` 增加该可选语义，使 `trend_leader_unified` 在板块排行 cache 过期但仍可用时直接复用本地结果，而不是先等待远端预热失败。`2026-04-29 limit=120` 同口径复跑中，`sector_rankings_prefetch_elapsed_sec` 已从约 `5.31s` 收敛到 `0.0005-0.0010s`。
- [改进] `src/services/kline_selector_service.py` 现在允许 `trend_leader_unified` 在 live `spot` 失败时回退使用“已过 TTL 但结构完整”的本地 `spot` reference cache，仅作为失败兜底而非首选新鲜数据；同时当存在这类 disk cache 时，live `spot` 重试保持单次，避免 `prep_universe` 在降级路径上额外空耗。`2026-04-29 limit=120` 实跑中已命中 `disk cached spot snapshot` fallback，`fundamental_fetch≈0.46s`、`capital_profile≈0.35s`，当前剩余热点主要转向 `prep_universe≈18.98s` 与 `sector_rankings_prefetch≈5.31s`。
- [改进] `trend_leader_unified` 快扫现在向 `get_earnings_fundamental_context(...)` 显式收窄到 `enabled_blocks=("financial",)`，不再默认抓取 `forecast/quick_report`；同时 `data_provider/base.py` 为窄 block profile 增加对旧宽 cache 的兼容复用。`2026-04-29 limit=120` 同口径热跑中，`fundamental_fetch` 约从 `17.52s` 进一步收敛到 `0.14s`，总耗时约从 `32.52s` 降到 `16.51s`。
- [改进] `trend_leader_unified` 共享前筛现在只在 `pct_change` 整列都不可用时才触发行情补水；若列内已存在可用值，则不再为少量空值逐票补 `quote`。在 `2026-04-29` 同口径实测中，`prepare_scan_universe(...)` 的单次耗时约从 `19-22s` 收敛到 `5.37s`，`quote_requested_rows` 从 `146` 降到 `0`。
- [改进] `trend_leader_unified` 快扫现在为 `earnings fundamental` 与 `capital_flow` 分别使用更紧的默认 budget（`0.6s / 0.45s`），并新增 `--fundamental-budget-seconds`、`--capital-flow-budget-seconds` 诊断参数与对应运行统计字段，用于收敛慢样本尾部耗时而不改变主信号入口。
- [改进] `scripts/select_trend_leader_candidates.py` 为 `trend_leader_unified` 新增 `DragonHeadAnalysisService` 懒初始化，并将“边缘弱趋势 + 弱当日价量”样本更早短路出 `board/dragon/fundamental` 链路；`scripts/benchmark_trend_leader_v1.py` 同步新增 `--compare-hotpath-round` 诊断模式与 cache-sensitive 提示。
- [改进] `scripts/select_trend_leader_candidates.py` 对 `trend_leader_unified` 的多 worker 主扫描关闭了单票内部 `fundamental/capital` 嵌套线程池；单 worker 仍保留内层并行，减少 `max_workers>1` 时的线程抖动与稳定性风险，并新增 `run_stats.candidate_inner_parallel_enrichment` 观测字段。
- [修复] `scripts/select_hundred_day_high_candidates.py` 新增同目录运行保护：若同一个 `output_dir` 已有进行中的 `hundred_day_high` 任务，新的任务会基于 `hundred_day_high_run.lock` 直接 fail-fast，而不是继续与现有任务共享同一输出目录和 checkpoint。
- [修复] `scripts/select_hundred_day_high_candidates.py` 不再默认把运行中 checkpoint 固定写到仓库根 `data/hundred_day_high_checkpoint.json`；未显式传 `--checkpoint-path` 时，现改为默认跟随各自 `output_dir` 落到 `hundred_day_high_checkpoint.json`，避免 `fast_review_bundle` 与手工 `hundred_day_high` 任务并发时互抢同一 checkpoint 文件。
- [文档] 重整本地策略文档体系：`docs/local_strategies/` 收敛为 `core / supporting / topics` 三层结构，四条主策略中文主入口、`LOCAL_STRATEGY_CATALOG.md` 与 `LOCAL_STRATEGY_BASELINE.md` 全部按当前代码重写，项目自有策略专题统一迁入该目录；同时清理冗余长分析稿、`docs/superpowers/` 临时计划草稿与本地 UI 临时日志。
- [改进] `scripts/select_hundred_day_high_candidates.py` 现在会对 `hundred_day_high` 入选后的 `breakout_quality` 180 日补强复用 `max_workers` 并发抓历史，并新增 `breakout_quality_parallel_enabled/workers/enrichment_elapsed_sec` 观测字段，在不改变信号口径的前提下压缩后处理墙钟时间。
- [改进] `scripts/select_hundred_day_high_candidates.py` 将 `hundred_day_high` 的 `breakout_loose` 质量底线从 `breakout_quality_score>=0` 收紧到 `>=4`，优先剔除没有明显突破质量支撑的弱尾部样本，同时保持当前 `2026-04-29 limit=200` smoke 样本入选结果不变。
- [改进] `scripts/select_earnings_surprise_candidates.py` 为默认快复盘 `earnings_surprise` 新增低深度弱事件预过滤与 `phase_timing_sec` 阶段耗时观测，`2026-04-29` 全市场复跑中 `evaluated_count` 从 `3560` 降到 `3490`，总耗时从约 `2159.20s` 降到约 `1833.01s`，并将 `fundamental_fetch / evaluate_candidate / capital_profile` 耗时写入业绩候选 Markdown 的 `Efficiency Summary`。
- [改进] `scripts/run_fast_review_bundle.py` 现在会把 `trend_leader_unified_watchlist.csv` 仅接入 `fast_review_strategy_focus.csv/md` 与摘要“策略精简焦点”区，并新增 `--trend-watch-top-n` 透传到趋势脚本；这些 watch-only 样本不会进入 `fast_review_candidates.csv`、`fast_review_resonance.csv` 或 `/signals` 快照。
- [改进] `scripts/select_trend_leader_candidates.py` 为 `trend_leader_unified` 增加 review-only `watchlist` sidecar：新增 `--watch-top-n`、`trend_leader_unified_watchlist.csv/txt/md` 与 `run_stats.watch_selected_count`，把被 strict 排除但仍有正分的非结构型样本单独导出，同时保持主结果、`/signals` 落库与快复盘聚合口径不变。
- [修复] `src/services/trend_leader_strategy_service.py` 现在要求 `trend_leader_unified` 只有在真实 `breakout` 或 `pullback` 结构成立时才能进入 `selection_mode=strict`，避免 `trend_neutral` 的正分样本继续污染 strict 排名。
- [改进] `src/services/kline_selector_service.py` 为 `spot` reference cache 增加进程内 memory 复用，避免 `trend_leader_unified` 同一轮准备阶段反复读取同一份 `kline_selector_spot_universe.csv`；同口径 `2026-04-28 limit=200` 诊断下，`prep_prefilter_elapsed_sec` 从约 `7.79s` 降到约 `0.25s`，总耗时降到约 `13.32s`。
- [改进] `data_provider/base.py` 为 `DataFetcherManager.get_daily_data(...)` 增加短 TTL 的 history-failure disk cache，重复的同参 fresh-process rerun 会直接复用前一次超时/失败结果，不再反复等待单票 `20s` history timeout；同时补充 `tests/test_fetcher_logging.py` 回归覆盖跨 manager 失败缓存复用。
- [改进] `scripts/select_trend_leader_candidates.py` 进一步收紧 `trend_leader_unified` 的弱趋势重型 enrichment 短路阈值：对非 breakout/non-pullback 且 `trend_template_score / trend_stage2_score / base_quality_score / return_20d` 均偏弱的样本，更早跳过 `earnings / boards / dragon / capital_flow` 相关抓取；`2026-04-28`、`limit=200` 的同口径诊断中，`capital_flow_fetch_skipped_count` 从 `21` 提升到 `34`，`selected_count` 保持 `20` 不变。
- [改进] `data_provider/base.py` 为 `get_capital_flow_context(...)` 增加内存/磁盘缓存与 `cache_hit/cache_source` 观测字段，`src/services/capital_profile_service.py`、`scripts/select_trend_leader_candidates.py` 同步透传并汇总 `capital_flow_cache_hit_count/source_counts`；`trend_leader_unified` 100 只样本 cold/warm 对比中，`capital_profile` 阶段从约 `12.83s` 降至约 `0.18s`，总耗时从约 `83.62s` 降至约 `21.84s`。
- [改进] `data_provider/base.py` 为 `get_earnings_fundamental_context(...)` 增加了 `cache_hit/cache_source` 观测字段，并把 `failed` 结果也纳入短 TTL 磁盘缓存；`scripts/select_trend_leader_candidates.py` 同步汇总 `fundamental_cache_hit_count` 与 `fundamental_cache_source_counts`，使 `trend_leader_unified` 可以直接区分 fundamentals 的 `memory / disk / fresh` 来源。实测在同口径 `limit=120` fresh-process 复跑中，第二次已达到 `fundamental_cache_hit_count=63`、`fundamental_fetch≈0.166s`。
- [改进] `src/services/kline_selector_service.py` 的 `_prepare_history(...)` 现在会对 `DataFetcherManager.get_daily_data(...)` 已返回的标准化日线走 fast-path，避免 `trend_leader_unified`、`hundred_day_high`、`monthly_slow_rise` 等扫描链路重复执行 `to_datetime / to_numeric / sort`；同时更新 `tests/test_fundamental_context.py` 与 `tests/test_data_fetcher_market_cache.py`，把 earnings-fundamental / sector-rankings 的磁盘缓存测试隔离到临时目录，消除本地缓存污染导致的不稳定回归。
- [改进] `trend_leader_unified` 进一步压缩深扫耗时：`fundamental_fetch` 与 `capital_profile` 现在在单候选内并行执行，trend 用 earnings context 仅请求 `financial + forecast + quick_report`，`capital_profile` 默认走 stock-only 资金流路径，不再为这条评分链路重复拉取 sector rankings。
- [改进] `scripts/select_trend_leader_candidates.py` 为 `trend_leader_unified` 新增 `phase_timing_sec`、`sector_rankings_prefetch_status`、`sector_rankings_prefetch_elapsed_sec` 等运行观测字段，便于直接拆分 `history / fundamentals / capital / sector prewarm` 耗时。
- [改进] `data_provider/base.py` 与 `data_provider/akshare_fetcher.py` 为 `get_sector_rankings(...)` 增加磁盘缓存，重复跑趋势复盘或诊断时可复用最近板块排行结果；`2026-04-29` 新进程诊断中 `sector_rankings_prefetch_elapsed_sec` 从约 `6.11s` 降到约 `0.0006s`。
- [改进] `data_provider/fundamental_adapter.py` 为 `get_market_expectation_snapshot(...)` 增加磁盘缓存，并暴露 `cache_hit/cache_source`，降低 `earnings_surprise` 复盘层 `stock_profit_forecast_ths` 的重复请求。
- [改进] `scripts/select_trend_leader_candidates.py` 为 `trend_leader_unified` 增加弱趋势样本的重型 enrichment 提前短路逻辑：明显弱结构样本会跳过 `earnings/boards/dragon/capital_flow` 相关抓取，并在 `run_stats` 暴露 `capital_flow_fetch_skipped_count` 便于观察命中规模。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `fast_review_earnings_focus.csv/md` 与摘要区“今日业绩焦点 15 只”，把 `earnings_surprise` 的业绩分、市场预期参考摘要、参考标签、事件日期和趋势共振集中到主复盘阅读区；`strategy_focus` 导出同步保留这些业绩预期字段。
- [改进] `scripts/select_earnings_surprise_candidates.py` 为已选中的 `earnings_surprise` 候选补充同花顺 `stock_profit_forecast_ths` 市场预期快照（预测年度、机构数、EPS 均值/区间、行业均值），仅作复盘参考展示，不参与当前策略打分；`scripts/run_fast_review_bundle.py` 同步保留这些字段用于复盘导出。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 默认接入 `KlineSelectorService.prepare_scan_universe(...)` 共享扫描壳，且 `scripts/run_fast_review_bundle.py` 新增可选 `monthly_slow_rise` 外部任务入口，支持把 `hundred_day_high / trend_leader / monthly_slow_rise` 三条扫描链在同一次快复盘中统一观测与汇总。
- [文档] 新增 `docs/architecture/rqalpha-evaluation-layer-next-step.md`，把共享扫描壳收口后的下一阶段固定为 `RQAlpha` 风格的标准化评估层原型，优先服务 `monthly_slow_rise / earnings_surprise`。
- [改进] `scripts/select_hundred_day_high_candidates.py` 默认接入 `KlineSelectorService.prepare_scan_universe(...)` 共享扫描壳，统一 universe 过滤、quote hydration、前筛与分片准备，并新增 `--disable-shared-scan-shell` 诊断开关。
- [改进] `config/local_strategy_profile.json`、`scripts/run_fast_review_bundle.py` 与 `scripts/select_earnings_surprise_candidates.py` 将每日快复盘 `earnings_surprise` 默认口径收敛为 `latest_report_period + recent_event_max_age_days=7`，在保留全财报季模式的同时缩短 `2026-04-28` 全链路冷态耗时。
- [修复] `data_provider/fundamental_adapter.py` 为 AkShare 基本面候选接口增加超时保护并缓存超时失败，避免 `earnings_surprise` 全市场快扫在基本面 endpoint 长时间无返回时卡死。
- [改进] `scripts/select_earnings_surprise_candidates.py` 的全市场 `earnings_surprise` 快扫改用 `KlineSelectorService.build_fast_a_share_manager()`，避免资金画像/日线补充继续走默认全源无超时 fallback 链路导致复盘卡死。
- [修复] `scripts/select_earnings_surprise_candidates.py` 将 `stock_yjbb_em` 正式财报公告纳入 `earnings_surprise` 近期事件目录，并在公告数据表夜间滞后但基础面块已拿到当前报告期正式财报金额/增速时启用实际财报兜底 overlay；`data_provider/fundamental_adapter.py` 同步解析 `stock_financial_abstract` 宽表，避免东山精密这类晚间正式财报被旧预告/快报事件漏判。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `fast_review_strategy_focus.csv/md` 与摘要焦点区，对 `trend_leader_unified` 候选按 `core/watch/low_priority` 分层，并把业绩、资金、板块强度及 `hundred_day_high` 交集纳入排序，减少快复盘大列表噪音。
- [改进] `scripts/select_trend_leader_candidates.py` 将 `trend_leader_unified` 的单日回放 universe 准备日期改为使用 `--snapshot-date`，并新增诊断参数 `--disable-shared-scan-shell`，便于同口径对照共享扫描壳与旧准备路径。
- [改进] `trend_leader_unified` 快扫现在优先复用有效的本地 `spot` reference cache，避免每轮单日回放先等待 live `spot`；2026-04-24 同参数验证中 `universe_elapsed_sec` 从约 `85.81s` 降到约 `21.34s`，总耗时从约 `4m47s` 降到约 `1m38s`。
- [改进] `src/services/kline_selector_service.py` 新增共享扫描壳原型 `prepare_scan_universe(...)`，并让 `scripts/select_trend_leader_candidates.py` 优先走服务层统一的 universe 过滤、quote hydration、前筛与分片准备逻辑，为 `trend_leader_unified / hundred_day_high / monthly_slow_rise` 后续继续收口到同一扫描层铺路。
- [测试] 更新 `tests/test_kline_selector_service.py` 与 `tests/test_trend_leader_signal_flow.py`，补充共享扫描壳准备结果与 `trend_leader_unified` 优先走服务层 scan setup 的回归覆盖。
- [改进] `scripts/select_earnings_surprise_candidates.py` 修复 `earnings_surprise` 近期事件 overlay 被旧 `quick_report_*` 污染的问题，并将 `balanced` 档放宽为“质量确认或正向文本+增长阈值”可通过 watch，重复事件改为按档位冷却后可重入。
- [改进] `scripts/select_trend_leader_candidates.py` 的 `trend_leader_unified` 预筛选阶段现在会先复用 `spot` 参考缓存补齐缺失的 `pct_change/turnover_rate` 等行情字段，再回退到逐票实时补全，减少重复短时行情请求并压缩慢日预处理耗时。
- [改进] `src/services/kline_selector_service.py` 优化 `spot-enriched universe` 回退链路：disk `spot` 快照读取保留前导零代码，live `spot` 失败时优先直接回退本地 `spot` 快照，并在已有 disk cache 时将 live `spot` 重试从 2 次降为 1 次，缩短 `trend_leader_unified` 等全市场扫描的 universe 准备耗时。
- [改进] `src/services/kline_selector_service.py` 在 `spot` 快照已自带完整 `list_date/listed_days` 时不再重复拉取 listing metadata；`scripts/select_trend_leader_candidates.py` 同时把“明显不可能凑够 120 个交易日”的新股在主扫描入队前提前跳过，减少 `trend_leader_unified` 的无效 history fetch 长尾。
- [改进] `data_provider/base.py` 与 `src/services/kline_selector_service.py` 为 `trend_leader_unified` 快扫专用 manager 增加窄范围 history fallback 收缩：当 `AkshareFetcher` 已明确报出“所有渠道获取失败”且请求窗口仍是短历史快扫时，直接跳过后续 `TushareFetcher` 空转，减少尾部重复失败链路。
- [改进] `data_provider/akshare_fetcher.py` 与 `src/services/kline_selector_service.py` 继续收紧 `trend_leader_unified` 快扫 history 链路：快扫专用 `AkshareFetcher` 的 history 内部重试从 `2` 次降到 `1` 次，减少 `EM` 传输失败时的尾部空耗，同时不影响普通 fetcher 默认重试策略。
- [改进] `scripts/select_trend_leader_candidates.py` 优化 `trend_leader_unified` 主扫描入队判断：`listed_days` 现采用“直接淘汰 / 直接放行 / 灰区再补算 business-day”三段式短路，避免为 1800+ 候选重复执行 `pd.bdate_range(...)`，单日全市场验证中 `scan start -> scan queue prepared` 已从约 `80s` 收敛到约 `0.03s`。
- [文档] `docs/LOCAL_STRATEGY_BASELINE.md` 新增 `trend_leader_unified` 最近一轮性能优化过程记录，明确本轮优化顺序、单日实跑结果与当前剩余瓶颈，便于后续继续沿同一路径收口。
- [文档] 新增 `docs/architecture/external-capability-map-for-local-strategies.md`，按“数据 / 基本面 / 短线理解 / 新闻 / agent / 回测 / 研究自动化 / 工程结构”分层整理外部开源系统对本地 4 条主策略的可借力点，并补充源码深挖优先级与后续优化步骤。
- [文档] `docs/architecture/external-capability-map-for-local-strategies.md` 进一步明确外部仓库源码深挖的首轮实操顺序为 `RD-Agent -> Qlib -> myhhub/stock -> FinGenius`，并补充每轮深挖的固定关注点与产出要求。
- [文档] 新增 `docs/architecture/rd-agent-source-dive-outline.md`，基于 `microsoft/RD-Agent` 当前公开仓库结构整理首轮 quant 源码深挖提纲，明确 CLI/quant loop/通用循环/quant 场景/反馈闭环的阅读顺序与固定产出。
- [文档] `docs/architecture/external-capability-map-for-local-strategies.md` 补充执行现实判断，明确外部能力研究应采用“总地图 + 单仓库深挖记录 + 集成决策”三层滚动沉淀，而不是一次性写完所有仓库的大全文档。
- [文档] 新增 `docs/architecture/rd-agent-source-dive.md`，基于 `microsoft/RD-Agent` 的 `fin_quant` 入口、`RDLoop` 循环骨架、`QlibQuantScenario` 场景装配与 factor/model runner/feedback 链路，整理 `RD-Agent` 对本地 4 条主策略最值得借鉴的研究自动化能力与不适合直接迁移的部分。
- [文档] 新增 `docs/architecture/qlib-source-dive.md`，基于 `microsoft/qlib` 的 workflow、benchmark config、`DatasetH`、`Alpha158/Alpha360`、recorder 与 task management 结构，整理 `Qlib` 对本地 4 条主策略最值得借鉴的统一因子层、数据集层、workflow 层与滚动实验能力。
- [文档] 新增 `docs/architecture/myhhub-stock-source-dive.md`，基于 `myhhub/stock`（`InStock`）的 `breakthrough_platform`、`turtle_trade`、`backtrace_ma250`、`high_tight_flag`、`low_atr` 与 `CYQ` 筹码分布实现，整理其对 `trend_leader_unified / hundred_day_high` 最值得迁移的形态质量与筹码增强能力。
- [文档] 新增 `docs/architecture/fingenius-source-dive.md`，基于 `HuaYaoAI/FinGenius` 的 `ResearchEnvironment / BattleEnvironment`、`hot_money / chip_analysis / big_deal_analysis` 结构，整理其对本地主策略最有价值的 A 股短线解释层、游资/大单/筹码维度拆分与两阶段分析组织方式。
- [文档] `docs/architecture/external-capability-map-for-local-strategies.md` 回填 `myhhub/stock` 与 `FinGenius` 首轮正式深挖结论，并补上两份独立源码记录的跳转，形成 `RD-Agent -> Qlib -> myhhub/stock -> FinGenius` 的第一轮闭环。
- [文档] 新增 `docs/architecture/tradingagents-cn-source-dive.md`，基于 `TradingAgents-CN` 的 `TradingAgentsGraph`、`GraphSetup`、`Propagator`、角色分层与数据源文档，整理其对本地框架最有价值的图式任务编排、中文产品壳与解释层组织方式。
- [文档] 新增 `docs/architecture/openbb-source-dive.md`，基于 `OpenBB` 的 `Open Data Platform`、`Provider / Fetcher / Router / OBBject` 抽象、官方开发文档与 MCP 说明，整理其对本地框架最有价值的统一数据接入层与工具层能力。
- [文档] `docs/architecture/external-capability-map-for-local-strategies.md` 回填 `TradingAgents-CN` 与 `OpenBB` 首轮正式深挖结论，并把后续默认深挖顺序固定为 `WonderTrader -> RQAlpha -> vn.py -> QUANTAXIS -> ai-hedge-fund`，避免后续再次重复定节奏。
- [文档] `docs/architecture/external-capability-map-for-local-strategies.md` 进一步明确外部工程研究的默认节奏为“直接分析、直接记录、直接回填”，仅在高成本动作或高风险分叉时中断，减少重复确认和过程汇报。
- [文档] 新增 `docs/architecture/wondertrader-source-dive.md`，基于 `WonderTrader` 的 `SEL` 选股引擎、`wtpy` Python 层与高性能数据/回测/执行分层，整理其对本地 4 条主策略最有价值的全市场扫描组织与执行层结构借鉴。
- [文档] 新增 `docs/architecture/rqalpha-source-dive.md`，基于 `RQAlpha` 的 `mod` 架构、成本/风险/分析分层与官方回测文档，整理其对本地框架最有价值的标准化评估层与组合层参考。
- [文档] 新增 `docs/architecture/vnpy-source-dive.md`，基于 `vn.py` 的插件生态、研究到执行桥接与 `vnpy.alpha` 结构，整理其对本地框架最有价值的模块边界、应用层与工作台组织方式。
- [文档] 新增 `docs/architecture/quantaxis-source-dive.md`，基于 `QUANTAXIS` 的 A 股本地底座、任务化组织、统一账户与桥接层说明，整理其对本地框架最有价值的数据、日历与本地运行基础设施启发。
- [文档] 新增 `docs/architecture/ai-hedge-fund-source-dive.md`，基于 `ai-hedge-fund` 的多 agent 角色分层、CLI/Web/Backtester 展示壳与官方风险声明，整理其对本地框架最有价值的解释层与展示层借鉴。
- [文档] `docs/architecture/external-capability-map-for-local-strategies.md` 回填 `WonderTrader`、`RQAlpha`、`vn.py`、`QUANTAXIS` 与 `ai-hedge-fund` 首轮正式深挖结论，并把第二轮默认顺序从“待分析列表”升级为“已完成首轮闭环”的导航记录。
- [文档] 新增 `docs/architecture/external-capability-integration-decision.md`，把 11 个外部仓库的首轮深挖结论收敛为可执行集成决策，明确“立即接入 / 延后实验 / 只参考不接”以及对本地 4 条主策略的接入顺序。
- [文档] `docs/architecture/external-capability-map-for-local-strategies.md` 新增到 `external-capability-integration-decision.md` 的导航跳转，明确外部能力研究已从“能力地图”进入“集成决策”阶段。
- [改进] `config/local_strategy_profile.json`、`scripts/run_fast_review_bundle.py` 与 `scripts/select_earnings_surprise_candidates.py` 同步优化 `earnings` 快复盘链路：默认 `earnings_max_workers=1`、`external_command_idle_timeout_sec=1800`，快复盘透传 `--earnings-capital-profile-ttl-seconds=86400`，并为 SQLite signal snapshot 写入补充轻量重试，降低长跑时的超时与锁冲突丢写。
- [改进] `scripts/run_fast_review_bundle.py` 为 `earnings` 增加独立并发参数 `--earnings-max-workers`，修复其误复用 `hundred_day_max_workers` 的问题，并新增 `--earnings-capital-profile-ttl-seconds`（默认 86400）透传到业绩脚本以减少回填时重复 `capital_profile` 刷新。
- [改进] `apps/dsa-web/src/pages/SignalsPage.tsx` 将 Signals 页面改为“短线模式/长线模式”双层展示：默认短线仅保留 `trend_leader_unified`、`earnings_surprise`、`hundred_day_high`，长线承载 `monthly_slow_rise` + `monthly_slow_rise_profile__*` + `dragon_head_candidate` + `commodity_beneficiary__*` + `board_recognizability__*`，并将月线档位对比区改为显式按钮展开/收起。
- [文档] 重写 `docs/LOCAL_STRATEGY_BASELINE.md` 与 `docs/LOCAL_STRATEGY_CATALOG.md`，按“当前默认行为优先”重构本地策略文档结构，明确默认每日策略分层、入口脚本、`signal_type` 与关键参数口径。
- [文档] 重写 `docs/TREND_LEADER_UNIFIED_STRATEGY.md`、`docs/EARNINGS_SURPRISE_TRACKING.md`、`docs/MONTHLY_SLOW_RISE_SCAN.md`，统一补齐策略条件、门槛、评分/拦截逻辑与 CLI 参数说明，减少历史叙述对当前口径的干扰。
- [改进] `scripts/evaluate_signal_snapshot_performance.py` 将前瞻可用性判定升级为“交易日口径优先”：优先按 `stock_daily` 市场级交易日计数判断 `insufficient_forward_horizon`，仅在计数不可用时回退自然日判定，减少对本就不可达窗口的无效补数。
- [改进] `scripts/evaluate_signal_snapshot_performance.py` 新增 `--fill-max-attempts`（默认 `200`）并输出 `fill_stats`，限制批量评估中的补数尝试上限，避免长尾网络补抓拖慢整轮评估。
- [改进] `scripts/run_signal_performance_bundle.py` 新增并透传 `--fill-max-attempts` 至单信号评估脚本，支持在 bundle 层统一控制补数预算。
- [测试] 更新 `tests/test_signal_snapshot_performance_report.py` 与 `tests/test_signal_performance_bundle.py`，补充交易日 horizon 跳过补数、补数预算上限生效与命令透传的回归覆盖。
- [修复] `scripts/evaluate_signal_snapshot_performance.py` 的缺失日线回填链路改为非 Tushare 默认顺序（`Efinance/Akshare/Baostock/Yfinance`），并将回填尝试从“按窗口重复”收敛为“同一 `code+signal_date` 仅一次”；同时在前瞻窗口天然不足时跳过无效补抓，降低补数风暴与长尾超时。
- [改进] `scripts/evaluate_signal_snapshot_performance.py` 新增 `insufficient_reason` 与 `insufficient_reason_counts` 统计，`monthly_slow_rise` 等信号在 `completed=0` 时可明确区分 `missing_forward_bars` 与 `insufficient_forward_horizon`，不再是黑箱状态。
- [修复] `data_provider/tushare_fetcher.py`、`data_provider/baostock_fetcher.py`、`data_provider/yfinance_fetcher.py` 扩展 A 股市场前缀识别（`6/9 -> SH`，`0/1/2/3 -> SZ`），覆盖 `001xxx`、`301xxx`、`605xxx` 等代码段，减少“无法确定股票市场，默认使用深市”误告警。
- [测试] 新增并更新 `tests/test_signal_snapshot_performance_report.py`、`tests/test_fetcher_market_prefix_inference.py`、`tests/test_tushare_fetcher_followups.py`，覆盖回填去重、前瞻窗口判定与市场前缀识别回归。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 补充 `weekly_volatility_percentile` 周线波动分位指标，并接入候选导出与排序 tie-break，增强“慢牛 + 周线稳定”排序解释力。
- [测试] `tests/test_monthly_slow_rise_candidates.py` 补充 `weekly_volatility_percentile` 回归断言，覆盖指标存在性与 `[0,1]` 取值范围。
- [改进] `scripts/select_earnings_surprise_candidates.py` 新增多季度 `surprise-history` 指标（正向季度数/占比/连续季度与历史评分），并将 `surprise_history` 作为独立因子接入 `earnings_strategy_factor_breakdown`，同步补齐导出字段与排序 tie-break。
- [测试] `tests/test_earnings_surprise_signal_flow.py` 新增 `test_evaluate_candidate_records_surprise_history_metrics`，覆盖 `earnings_surprise` 的多季度 surprise-history 指标与策略因子接线。
- [改进] `scripts/select_trend_leader_candidates.py` 与 `scripts/select_hundred_day_high_candidates.py` 完成共享因子补齐：统一复用 `SharedSignalFactorsService` 的 `capital/quality/industry` 构建，并把 `quality_overlay_*`、`earnings_continuity_*`、`industry_strength_*` 扩展到快照 metrics 与导出结果。
- [改进] `scripts/run_signal_performance_bundle.py` 默认透传 `--fill-missing-daily-data` 给 `scripts/evaluate_signal_snapshot_performance.py`，并支持显式关闭；当 `stock_daily` 缺失时可按需补齐日线后再评估，避免长期 `completed=0`。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 将 `robust` 档 `min_positive_month_ratio` 从 `0.67` 下调到 `0.60`，减少“稳健档全空”并保留周线稳定与业绩连续性约束；`data_provider/akshare_fetcher.py` 同步支持 `AKSHARE_STOCK_HISTORY_EM_BACKOFF_SECONDS` 环境变量调节历史 EM backoff。
- [文档] 明确 `dragon_head_candidate`、`theme_core_mapper`、`commodity_price_pass_through` 与 `select_kline_candidates.py` 的运行角色为专题/研究工具，不属于默认每日核心选股循环。
- [改进] 完成本轮本地策略 Task 8 复测留痕：补充 `run_signal_performance_bundle` 与 `run_fast_review_bundle` 的实跑命令、输出目录与结果摘要（含当前窗口 `completed=0` 的样本可用性说明）。
- [文档] 完成 Task 8 最终全量复测收口：新增 `--skip-fill-missing-daily-data` 的 6 信号同窗复测结果（`2026-04-01~2026-04-24`），并补充各信号在 `1/3/5/10` 窗口的 `completed` 计数，作为当前数据源限流条件下的稳定基线证据。
- [文档] 补充 Task 8 复测口径说明：修正快复盘核心信号计数为 `earnings=2 / hundred_day_high=5 / trend_leader=2`，并新增覆盖性探针结论（`end_date=2026-04-10` 时 `hundred_day_high` 在 `1/3/5/10` 窗口均 `completed=20`），明确 `2026-04-24` 窗口下 `completed=0` 属于前瞻样本不足而非评估链路失效。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 导出层显式补齐 `industry_strength_score/confirmed/label/confirmation_hint` 字段（含空结果 CSV 头），并在 markdown 入选表新增行业与行业确认列，便于直接复盘“慢牛结构 + 行业确认”。
- [改进] `scripts/select_hundred_day_high_candidates.py` 升级为“质量突破”口径，新增 `minervini_template_score/passed`、`breakout_follow_through_score` 与 `industry_strength_*` 字段，并同步导出到百日新高 CSV/Markdown 及快照 `metrics_payload`。
- [改进] 新增 `src/services/shared_signal_factors_service.py` 作为第一阶段共享层最小方案，统一沉淀 `capital/liquidity`、`quality overlay`、`industry strength` 三类通用因子，并先接入 `scripts/select_earnings_surprise_candidates.py` 与 `scripts/select_monthly_slow_rise_candidates.py`。
- [修复] `data_provider/akshare_fetcher.py` 为 `monthly_slow_rise` fast manager 的历史 `em` 源增加短期 backoff；当 `Akshare EM` 出现 `RemoteDisconnected` / timeout 等可重试传输失败时，会在窗口期内跳过重复 EM 重试，避免全市场扫描对同一失效源逐票重打。
- [测试] `tests/test_kline_fast_manager.py` 新增 `test_akshare_history_em_failure_enables_backoff_and_skips_second_em_attempt`，锁定历史 `em` 源失败后启用 backoff、后续请求跳过第二次 EM 尝试的回归行为。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md`、`docs/LOCAL_STRATEGY_BASELINE.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充 2026-04-25 `monthly_slow_rise` 剩余上游历史源稳定性修复留痕。
- [文档] 补充 2026-04-25 `monthly_slow_rise --profile robust` 单实例全市场续跑至完整收敛的实盘证据：`evaluated=4929`、`skipped_by_listed_days=91`、`network_or_fetch_failures=36`、`total_scan_elapsed_sec=184.6601`，并记录多进程 backoff 仍为进程内状态。
- [文档] 补充 2026-04-25 `monthly_slow_rise --profile robust` 双分片并行续跑至完整收敛的实盘证据：聚合结果与单实例一致，但 shard 级 `history EM source entered backoff` 次数分别为 `2` 和 `4`，进一步确认该 backoff 当前仅在进程内生效。
- [文档] 补充 2026-04-25 `monthly_slow_rise` 同日对照分析：当前 `robust` 全市场实跑虽稳定收敛但结果为 `0`，主因是 `monthly_lookback=15` 下 `min_positive_month_ratio=0.67` 实际更接近要求 `11/15` 月收阳；同日 `balanced` 仍有 `65` 个候选，而仅将 `robust` 的该阈值放宽到 `0.60` 即恢复 `5` 个候选。
- [修复] `src/services/kline_selector_service.py` 的 `spot-enriched universe` generic fallback 现会继续合并上市元数据，避免 `Akshare spot` 失败时丢失 `list_date/listed_days`，让 `monthly_slow_rise --profile robust` 的 `min_listed_days=400` 轻前筛在真实 fallback 路径里仍然生效。
- [测试] 更新 `tests/test_kline_selector_service.py`，补充 `spot-enriched universe` generic fallback 必须保留 `list_date/listed_days` 的回归覆盖。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充 2026-04-25 `monthly_slow_rise --profile robust` 3 分钟真实观测中 `skipped_by_listed_days=91`、`history fetch failed=21` 的实测结论。
- [改进] `scripts/select_earnings_surprise_candidates.py` 继续强化 `earnings_surprise`：新增事件后 `1D/3D` 价格反应、营收/利润多季连续性、`earnings_financial_series_continuity_score` 与持续质量因子，并在可用时补充行业确认字段，降低“单季超预期但持续性弱”标的的排序权重。
- [测试] 更新 `tests/test_earnings_surprise_signal_flow.py`，补充业绩线对事件后反应指标与多季连续性输出的回归覆盖。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 将 `monthly_slow_rise` 进一步升级为“月线形态 + 周线稳定 + 流动性 + 业绩连续性”的稳健筛选：新增 weekly stability/compression 指标、`avg_daily_amount_20d` 过滤与财报连续性后置筛选。
- [测试] 更新 `tests/test_monthly_slow_rise_candidates.py`，补充周线稳定度、低流动性剔除与业绩连续性过滤的回归覆盖。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充 2026-04-25 本地策略收口后的核心日常策略口径，以及 `earnings_surprise` / `monthly_slow_rise` 的本轮升级留痕。
- [改进] `data_provider/base.py` 为 `DataFetcherManager` 增加历史请求日历跨度与派生日线指标开关；`src/services/kline_selector_service.py` 的 `monthly_slow_rise` fast manager 现使用 `1.6x` 自动跨度并跳过缓存命中时未被策略使用的 `ma5/ma10/ma20/volume_ratio` 重建，降低全市场热跑的 `history_fetch` 成本。
- [测试] 更新 `tests/test_fetcher_logging.py` 与 `tests/test_kline_fast_manager.py`，锁定 manager 级历史跨度、跳过派生日线指标，以及 `monthly_slow_rise` fast manager 的新默认配置。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充 2026-04-24 `monthly_slow_rise --profile robust` 真实全市场复测中 `selected=27` 保持不变、`total_scan_elapsed_sec` 从 `319.501` 收敛到 `230.6251` 的实测结果。
- [改进] `src/services/kline_selector_service.py` 将 `MaxMarketCapRule` 改为惰性求值：只有前置 history 规则通过后才补 `total_market_cap` 并执行市值判断，减少 `monthly_slow_rise` 在大量失败样本上的重复实时 quote。
- [测试] 更新 `tests/test_kline_selector_service.py`，锁定“前置规则已失败时不再执行 market-cap resolve”的惰性求值行为。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充惰性市值求值实现及 2026-04-24 全市场实跑耗时改善结果。
- [改进] `src/services/kline_selector_service.py` 为 `evaluate_stock()` 与 `scan_market()` 增加 `selection` 内部耗时拆分，`monthly_slow_rise_run_summary.json` 现在可查看 `history_fetch / history_prepare / market_cap_resolve / rule_evaluate` 的累计与均值指标。
- [测试] 更新 `tests/test_kline_selector_service.py`，锁定单票 K 线评估 timing contract 与全市场 selection-phase 聚合行为。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充月线慢牛 `selection` 深层耗时拆分与 2026-04-24 全市场实跑结论。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 新增 `phase_metrics` 输出与 `monthly_slow_rise_run_summary.json`，每次实跑都会留下 `universe / selection / capital_enrich / total_scan` 分段耗时，便于后续直接定位慢点。
- [测试] 更新 `tests/test_monthly_slow_rise_candidates.py`，锁定月线慢牛脚本的 phase timing 输出契约与 summary artifact 导出行为。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充月线慢牛新的性能拆分产物与使用方式。
- [修复] `src/services/kline_selector_service.py` 的 fast A-share manager 不再在多 worker 全市场扫描里优先走 `sina` 历史接口，改为 `tencent -> em` 并保留 `Tushare` fallback，修复当前 Windows 环境下 `monthly_slow_rise --profile robust` 实盘并发扫描会因 `py_mini_racer` 原生崩溃而中断的问题。
- [测试] 更新 `tests/test_kline_fast_manager.py`，锁定 fast manager 的新历史优先级，避免后续回退到会触发并发崩溃的 `sina` 优先链路。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充本次 fast manager 崩溃修复原因，以及真实 `monthly_slow_rise --profile robust` 并发实跑耗时从约 `817.61s` 收敛到约 `309.50s` 的实测结果。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 将 `monthly_slow_rise` 全市场扫描默认 `--max-workers` 从 `1` 提升到 `4`，减少月线慢牛实盘全量扫描被单线程 history fetch 拖慢的问题。
- [改进] `data_provider/base.py` 新增“covered stale history cache 优先复用”路径，`src/services/kline_selector_service.py` 的 fast A-share manager 在请求区间已被本地磁盘历史覆盖时，会直接复用 `disk_cache_stale_covered:*`，不再每次都先做尾部刷新。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充 `monthly_slow_rise` 的默认并发基线和 covered stale cache 复用说明。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 新增正式 `robust` profile，月线慢牛从 `strict / balanced / loose` 扩展为四档；`robust` 默认使用 `15` 个月观察窗口、`min_change_pct_60d=3`、`min_listed_days=400`，更适合作为“稳健版”月线观察池。
- [文档] 同步 `docs/MONTHLY_SLOW_RISE_SCAN.md`、`docs/LOCAL_STRATEGY_BASELINE.md`、`docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充月线慢牛 `robust` 档位、`monthly_slow_rise_profile__robust` 命名空间与 `/signals` 对比说明。
- [改进] `scripts/collect_earnings_observation_snapshots.py` 的趋势复查阶段改走 `KlineSelectorService.build_fast_a_share_manager()`，减少单票历史数据 multi-provider fallback 对每日业绩观察尾部时延的拖累。
- [新功能] 新增 `scripts/collect_earnings_observation_snapshots.py`，在 schedule 尾部基于现有 `earnings_surprise balanced` 维护 `earnings_observation_registry` 与 `earnings_observation_active` 两类快照，用于长期业绩观察与每日强势名单跟踪。
- [改进] `main.py`、`src/config.py` 与 `.env.example` 新增业绩观察尾部任务开关和最长观察期配置，`/signals` 默认信号列表同步纳入业绩观察池与活跃名单。
- [文档] 同步更新 `docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充业绩观察策略的入口、信号命名空间与留痕说明。
- [改进] `scripts/select_hundred_day_high_candidates.py` 新增 `breakout_balanced_with_earnings` profile，在保留原 `breakout_balanced` 百日新高口径不变的前提下，仅保留通过 `earnings_surprise balanced` 业绩确认的候选，并默认落库到独立 `signal_type=hundred_day_high__earnings_balanced`。
- [文档] 同步 `docs/KLINE_SELECTOR_GUIDE.md`、`docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md`，补充 `breakout_balanced_with_earnings` 的使用方式、落库命名空间与策略留痕。
- [改进] `scripts/run_fast_review_bundle.py` 将快复盘 `earnings` 默认近期事件范围切到 `latest_report_period`，只优先查看当前财报季新公告；`scripts/select_earnings_surprise_candidates.py` 同时保留 `--recent-event-scope lookback/latest_report_period` 供其他策略继续复用 `120` 天业绩事件目录。
- [改进] `scripts/select_trend_leader_candidates.py` 将 `trend_leader` 主扫描历史抓取窗口从 `260` 天收敛到 `140` 天，减少每票未实际使用的 K 线抓取成本。
- [改进] `scripts/run_fast_review_bundle.py` 新增独立 `--hundred-day-max-workers`，并将快复盘默认 `hundred_day_max_workers` 设为 `2`，避免 `hundred_day_high` 继续被全局 `max_workers=1` 限制。
- [改进] `scripts/run_fast_review_bundle.py` 将快复盘默认 `external_parallelism` 从 `2` 提升到 `3`，让 `earnings`、`hundred_day_high`、`trend_leader` 默认同轮并行启动。
- [改进] `scripts/run_fast_review_bundle.py` 将快复盘默认 `continuous_max_workers` 从 `1` 提升到 `2`，减少 `continuous_up_ratio / continuous_up_streak` 共享扫描的单线程尾段耗时。
- [文档] 同步 `config/local_strategy_profile.json`、`docs/LOCAL_STRATEGY_CATALOG.md` 与 `docs/AI_MODIFICATION_LOG.md` 的快复盘默认值口径，补充 2026-04-23 聚合入口性能优化留痕。
- [改进] `scripts/select_trend_leader_candidates.py` 会把 generic fallback 前筛阶段已补齐的 `pct_change/turnover_rate` 反写到 `spot-enriched universe` reference cache，并按缺失行定向补水；在 `Akshare spot` 持续不可用的同条件复跑里，`quote_requested_rows` 可从 `5198` 下降到 `1562`。
- [修复] `src/services/kline_selector_service.py` 为 `spot-enriched universe` 的磁盘 reference cache 增加最小行数护栏，跳过写入/读取被污染的 tiny spot 快照，避免 `trend_leader` 在 generic fallback 时误复用 1 行缓存并继续触发全市场 `quote_requested_rows=5198`。
- [改进] `scripts/select_trend_leader_candidates.py` 在主扫描前新增历史不足样本短路与 `skipped_unscannable_history` 统计，并预热一次 `sector_rankings` 后通过 `scan_context` 透传给 `dragon_head_analysis_service`，减少近期次新股无效主扫与 worker 首批板块排行冷启动。
- [改进] `data_provider/base.py` 新增轻量 `get_earnings_fundamental_context()` 并接入 `scripts/select_trend_leader_candidates.py` 主扫描，避免 `trend_leader` 每票重复拉取整包 fundamental context；`2026-04-22` 对比热跑中，主扫描 `sec_per_processed` 约从 `0.62s` 降到 `0.53s`。
- [改进] `scripts/select_trend_leader_candidates.py` 将 `trend_leader` 前筛逐票 quote hydration 接入受控并发，并新增 `quote_worker_count` 日志字段；对 `2026-04-22` 热跑实测里，`prefilter_elapsed_sec` 约从 `495s` 降至 `242s`。
- [改进] `scripts/select_trend_leader_candidates.py` 将 `trend_leader` 前筛 quote hydration 改为字段级按需补齐，避免因 `change_pct_60d` 缺失而触发整轮全市场实时行情回填。
- [改进] `scripts/run_fast_review_bundle.py` 曾在快复盘包含 `earnings` 且使用 `--skip-persist-snapshots` 时提示该模式会绕过 `signal_fundamental_snapshot` 缓存；当前行为已在 `[Unreleased]` 调整为只跳过写入、仍读取缓存。
- [改进] `src/services/kline_selector_service.py` 为 `spot-enriched universe` 增加轻量重试、进程内缓存回退与上市元数据缓存；`scripts/select_hundred_day_high_candidates.py`、`scripts/select_monthly_slow_rise_candidates.py` 默认接入 `spot-enriched universe + min_listed_days` 轻前筛；`scripts/select_earnings_surprise_candidates.py` 默认改走现货优先 universe，并在 `scan_depth=low` 时先按近期业绩目录缩小 universe 再主扫。
- [测试] 补充 `tests/test_kline_selector_service.py`、`tests/test_hundred_day_high_signal_flow.py`、`tests/test_monthly_slow_rise_candidates.py`、`tests/test_earnings_surprise_signal_flow.py`，覆盖 spot universe 重试/缓存回退、百日新高与月线慢牛的上市天数短路接线，以及业绩线现货优先 universe 接线。
- [文档] 补充快复盘性能优化留痕：记录 `listed_days` 短路、现货优先前筛、`continuous_up` 历史长度收敛的实现与 2026-04-22 小样本实跑结果，详见 `docs/AI_MODIFICATION_LOG.md`、`docs/LOCAL_STRATEGY_BASELINE.md`、`docs/LOCAL_STRATEGY_CATALOG.md`。
- [改进] `src/services/kline_selector_service.py` 新增 `list_date/listed_days` universe 归一化与 `min_listed_days` 轻前筛；`scripts/select_trend_leader_candidates.py` 默认增加 `scan_prefilter_min_listed_days=120` 并记录准备阶段耗时；`scripts/run_fast_review_bundle.py` 将 `continuous_up` 历史长度收敛到 `max(lookback_days, streak_days) + 5`，同时为连续上涨与趋势前筛接入现货优先 universe。
- [文档] 补充 2026-04-22 全量快复盘实跑留痕：`earnings` 约 `81.38s`、`hundred_day_high` 约 `723.76s`、`trend_leader` 约 `1766.19s`、`continuous_up` 每信号约 `314.91s`；当前主要慢点已转为 K 线全量扫描与趋势链路准备/主扫阶段，相关观察已记录到 `docs/AI_MODIFICATION_LOG.md`。
- [文档] 同步 `docs/LOCAL_STRATEGY_BASELINE.md` 与当前快复盘真实默认值及验证结论：`hundred_day_profile=breakout_loose`、`trend_max_workers=2`、`external_parallelism=2`，并补充 2026-04-22 小样本实跑结论（当前最慢外部环节仍是 `earnings`）。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `--trend-max-workers`（默认 2）并将趋势龙头并发与全局 `--max-workers` 解耦；`config/local_strategy_profile.json` 同步补充 `trend_max_workers=2`，避免快复盘默认单线程拖慢趋势扫描。
- [修复] `scripts/run_fast_review_bundle.py` 为外部子策略命令增加超时保护（`--external-command-idle-timeout-sec` / `--external-command-total-timeout-sec`），在 `earnings` 等脚本无输出卡住时会主动终止并标记跳过，避免整轮快复盘无限挂起；`config/local_strategy_profile.json` 同步补充默认超时参数。
- [改进] 快复盘入口提速基线更新：`scripts/run_fast_review_bundle.py` 默认 `--external-parallelism` 提升到 `2`，并新增趋势预过滤参数透传（`--trend-scan-prefilter-*`）；`data_provider/akshare_fetcher.py` 为行业排行东财源增加失败熔断（默认 1200 秒）以减少重复失败请求。
- [改进] `scripts/select_earnings_surprise_candidates.py` 升级“质量优先版”业绩放行：`strict/balanced` 档位下 watch 分数不再仅靠增速/文本确认，默认必须有 `earnings_quality` 正向确认；并新增 `blocked_missing_quality_confirmation` 拦截态与 `mixed>=50` 的温和质量信号兜底，减少“高增速但低质量”误入选。
- [改进] `apps/dsa-web/src/pages/SignalsPage.tsx` 增强 `earnings_surprise` 的策略结果可视化：候选卡片新增 strategy/gate/verdict/score 徽标，列表默认按 `earnings_strategy_score` 优先排序，并在右侧历史详情新增“业绩策略结果”结构化面板。
- [修复] `scripts/run_fast_review_bundle.py` 外部子任务执行新增 SQLite `database is locked` 自动重试，降低并行持久化时的瞬时锁冲突失败概率。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `--hundred-day-profile`（默认 `breakout_loose`）并透传到百日新高脚本，快复盘场景下避免 `hundred_day_high` 长期全空。
- [改进] `trend_leader_unified` 放宽非硬风险业绩 gate 与 fallback 兜底策略，新增 `tier5_safety_net`，并要求 strict 命中满足 `overall_score > 0`，减少 strict 全空与结果不稳定。
- [改进] `earnings_surprise` 扫描链路新增 P0/P1 提速优化：`AkshareFundamentalAdapter._call_df_candidates` 增加同进程结果缓存（成功/失败均缓存），`low/medium` 在近期业绩目录已覆盖时按需跳过 `forecast/quick_report` 重抓，`signal_fundamental_snapshot` 支持跨天事件指纹不变复用，并将近期公告目录报告期数量改为随 `event_lookback_days` 动态计算，降低全量扫描重复抓取成本。
- [修复] 修复 `earnings scan-depth` 接线遗漏：`scripts/run_fast_review_bundle.py` 新增 `--earnings-scan-depth(low/medium/high, default=low)` 并透传给 `select_earnings_surprise_candidates.py --scan-depth`，同时 `scan_market` 运行链路接入 `scan_depth` 与 `required_blocks`，确保低深度按核心基础面块抓取并通过对应回归测试。
- [改进] `earnings_surprise` 新增三项提速闭环：近期业绩目录支持跨运行磁盘缓存（同快照直接复用、跨天增量刷新）、`scan_depth=high` 改为“核心块先筛 + 临近通过再补全量块”、`max_workers=1` 时基础面快照改为共享事务批量写入，并修复 SQLite 下逐条 `flush` 引发的 `database is locked` 问题。
- [文档] 新增 `docs/LOCAL_STRATEGY_BASELINE.md` 作为当前默认本地策略基线文档，按策略名称沉淀实现条件、默认参数、信号落点与 AI 续写位置，并在 `docs/LOCAL_STRATEGY_CATALOG.md` 中补充主入口链接。
- [改进] `trend_leader_unified` 预过滤改为先补齐行情种子再筛选，并在 60d 动量缺失时启用自适应正向门槛与放宽缓冲池，实际 benchmark 同参数提速提升到 59.52%。
- [改进] `trend_leader_unified` 的 fallback 分层进一步收口：`tier4_last_resort` 现在只在 `tier1~3` 仍无候选时触发，并继续排除 `blocked_*` 与 `pseudo_leader`，保证“每天尽量有候选”同时保留显式风险标记。
- [改进] 新增 `scripts/benchmark_trend_leader_v1.py`，可在同参数下输出 `baseline vs optimized` 的趋势龙头扫描耗时、候选数、fallback 数与 prefilter 统计，为 V1 提速验收提供可复现实跑证据。
- [改进] `trend_leader_unified` 三项 V1 优化落地：新增扫描前行情级预过滤（`--scan-prefilter-*`）、分层 fallback 兜底（`fallback_tier`），以及资金流按市值分层阈值打分（small/mid/large cap）。
- [改进] `scripts/run_fast_review_bundle.py` 新增“无结果策略跳过”机制：当子策略执行失败或候选数为 0 时不再中断整轮，统一标记为 `skipped` 并在 `fast_review_summary.md` 与控制台输出 `skipped_signal` 列表，便于快速定位未执行/无结果策略。
- [改进] `trend_leader_unified` 在资金流不可用（`failed/not_supported/unknown/empty`）时，不再因 `weak_capital_consensus/weak_capital_flow/weak_capital_continuity` 直接硬拦截；保留风险标记并新增 `capital_flow_unavailable`，避免“数据缺失=策略否决”。
- [改进] `AkshareFundamentalAdapter.get_capital_flow` 新增板块资金排行 Tushare fallback（`moneyflow_ind_ths`/`moneyflow_ind_dc`）与日级缓存，在 AkShare 板块资金排行缺失时补齐板块资金上下文。
- [改进] `trend_leader_unified` 新增“同板块业绩暴雷联动提示”：扫描阶段写入 `primary_board_name`，并按板块统计 `blocked_*` 业绩风险，命中候选会附加 `board_earnings_risk_*` 字段、风险标记与摘要提示，避免仅看个股分数忽略板块联动折价。
- [改进] `scripts/run_fast_review_bundle.py` 新增本地策略配置文件入口 `--strategy-profile-file`（默认 `config/local_strategy_profile.json`），支持集中管理 include/exclude 与关键参数默认值。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `--continuous-max-workers`，将连续上涨本地扫描并发与外部脚本并发解耦，默认 1 以提升稳定性。
- [改进] `scripts/run_fast_review_bundle.py` 新增共振去重导出：`fast_review_resonance.csv` 与 `fast_review_resonance.md`，按 `code` 聚合多信号重叠。
- [新功能] 新增独立脚本 `scripts/run_signal_performance_bundle.py`（多信号绩效评估聚合）与 `scripts/run_industry_turning_point_daily.py`（行业拐点日更聚合）。
- [改进] 新增 `scripts/run_trend_leader_sharded_pipeline.py` 两段式分片流水线：支持 `download/merge/persist` 分阶段执行，默认将分片下载扫描与合并入库解耦，避免与其他策略同步耦合。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `--exclude-signals`，可在 `--include-signals` 展开后按信号键剔除当前不需要的策略，降低本地策略编排复杂度。
- [改进] 趋势龙头链路新增分片参数：`select_trend_leader_candidates.py` 支持 `--shard-count/--shard-index` 多实例分片扫描，并在分片模式下要求 `--skip-db-persist` 防止快照覆盖；`run_fast_review_bundle.py` 与 `run_trend_leader_daily_bundle.py` 已支持分片参数透传。
- [改进] 趋势龙头链路新增并发参数：`select_trend_leader_candidates.py` 支持 `--max-workers` 并发扫描；`run_fast_review_bundle.py` 与 `run_trend_leader_daily_bundle.py` 均已透传该参数，支持“获取+策略执行”多线程提速并保留 checkpoint/进度日志能力。
- [改进] `scripts/run_fast_review_bundle.py` 新增 `--external-parallelism` 外部信号并行执行（业绩/百日新高/趋势龙头）与分信号耗时输出（`elapsed_sec`），用于缩短快复盘总耗时并提升慢点定位效率。
- [改进] 新增 `scripts/run_fast_review_bundle.py` 两段式快复盘入口：聚合 `业绩/百日新高/趋势龙头/连续上涨(上涨占比+连涨天数)` 的当日导出与快照落库，`全扫描+回测` 保持独立命令按需执行，降低日常入口复杂度。
- [改进] `trend_leader_unified` 新增股票池瘦身能力：`select_trend_leader_candidates.py` 与 `run_trend_leader_daily_bundle.py` 支持 `--exclude-st`、`--exclude-kcb`、`--exclude-cyb` 与 `--universe-codes-file`，可先按外部股票ID名单和板块/ST规则缩小扫描范围，降低全量扫描耗时。
- [改进] `trend_leader_unified` 全量扫描提速：`select_trend_leader_candidates.py` 复用单票实时行情、财务上下文与日线上下文，减少重复调用 `get_realtime_quote/get_fundamental_context/get_daily_data`，在不改变选股口径的前提下降低全市场扫描耗时。
- [文档] 收敛本地策略文档治理：`AGENTS.md`、`docs/LOCAL_STRATEGY_CATALOG.md`、`docs/AI_MODIFICATION_LOG.md` 新增统一留痕约定与检查清单，要求本地策略新增与修改同步记录。
- [改进] `select_trend_leader_candidates.py` 与 `run_trend_leader_daily_bundle.py` 新增 `--progress-every`，支持输出“已处理/总数/百分比/当前代码”的扫描与补抓进度日志，便于观察长任务运行位置。
- [修复] `evaluate_signal_snapshot_performance.py` 修复样本不足分支缺少 `first_hit` 字段导致的汇总异常，`trend_leader_unified` 一键链路在 `insufficient_data` 场景可稳定产出评估报告。
- [改进] `trend_leader_unified` 新增“快扫 + 入选后二阶段补抓”流程：全市场扫描阶段默认关闭新闻/主营抓取，仅对入选池执行补抓；新增 `--disable-second-stage-news-search`、`--disable-second-stage-business-profile`、`--enrich-top-n` 参数，并在快照落库补充 `news_items_count`、`has_business_profile`、`enrichment_stage` 等状态字段。
- [改进] `schedule` 模式下 `hundred_day_high` 的归因补全改为“稳定快扫 + 入选后补抓”：保留阶段 1 全扫稳定性，仅在阶段 2 对入选候选补抓新闻与主营（默认继续关闭 LLM 原因卡）。
- [改进] `DataFetcherManager` 在 `days` 自动区间请求下新增 best-effort 历史缓存复用：当缓存已覆盖请求结束日且行数足够时，直接复用缓存切片，避免周末/节假日头部补齐触发额外联网重试，降低全市场扫描耗时。
- [改进] `schedule` 模式下启用 `TREND_LEADER_UNIFIED_SNAPSHOT_ENABLED=true` 时，现已自动执行 `scripts/run_trend_leader_daily_bundle.py`，每日输出“扫描+绩效+汇总”结果文件。
- [改进] `trend_leader_unified` 新增扫描链路下载优化与断点续跑能力（批量行情预取、`--checkpoint-path/--checkpoint-every/--resume`），并新增 `scripts/run_trend_leader_daily_bundle.py` 一键执行“扫描+评估+日报汇总”入口。
- [改进] `evaluate_signal_snapshot_performance.py` 新增交易成本口径（滑点/费率/换手惩罚）、成本后收益/胜率/回撤/卡玛指标与分数分桶统计；`trend_leader_unified` 资金打分新增持续性与结构维度（`capital_flow_continuity_score`、`capital_structure_score`）。
- [改进] `trend_leader_unified` 同日重跑改为单事务原子替换（删旧+写新+重建汇总），异常时整体回滚避免当日空窗；并对 `TL_SUMMARY` 跳过 YTD 行情回填，减少无效行情抓取。
- [改进] `trend_leader_unified` 新增标签字段与展示：快照透传 `selection_mode`、`is_breakout_candidate`、`is_pullback_candidate`、`near_new_high`，并在 `/signals` 输出可读 `signal_tags`（百日新高/突破形态/回踩形态/严格命中/兜底观察）。
- [改进] `trend_leader_unified` 扫描在当日 0 命中时会落库 `TL_SUMMARY` 运行摘要快照，便于在 `/signals` 确认“已执行/0命中”。
- [改进] `KlineSelectorService.build_fast_a_share_manager` 在保留 Akshare 快路径的同时增加可用 Tushare 兜底，降低单一源失败导致的空跑。
- [改进] `schedule` 模式新增强趋势龙头统一快照日更开关：支持 `TREND_LEADER_UNIFIED_SNAPSHOT_ENABLED` 与 `TREND_LEADER_UNIFIED_SNAPSHOT_LIMIT`，可在每日定时分析后自动执行 `trend_leader_unified` 快照刷新并在 `/signals` 回看。
- [新功能] 新增 A 股 `trend_leader_unified` 强趋势龙头统一策略：新增 `scripts/select_trend_leader_candidates.py` 与 `src/services/trend_leader_strategy_service.py`，按龙头/趋势/资金/业绩混合口径生成统一候选池快照。
- [改进] `/signals` 新增 `trend_leader_unified` 接入：后端快照查询、计数元数据、API schema、前端 tab 与列表/详情字段均支持 `primary_profile`、`breakout_score`、`pullback_score`、`hybrid_score`、`overall_score`、`trend_label`、`strategy_summary`。
- [测试] 新增统一策略单元与链路测试：覆盖 `TrendLeaderStrategyService` 打分/硬筛、快照 payload 透传、`test_signal_snapshot_api.py` 合同校验与 `SignalsPage` 新 tab 渲染。
- [改进] `earnings_surprise / fundamental_context / capital_profile` 同日复跑链路新增更强的 same-day cache 复用与缓存观测字段：基础面 bundle 默认同日复用、capital profile 按短 TTL 局部刷新，`/signals` 与性能报告同步补充 `cache_source / bundle_refreshed_at / capital_profile_refreshed_at / capital_profile_cache_hit` 等效率指标。
- [改进] 新增可复用的 `src/services/capital_profile_service.py`，统一输出 `capital_consensus_score / capital_profile_score / capital_flow_score / relative_strength_score / liquidity_score / main_net_inflow / inflow_5d / inflow_10d / capital_profile_summary` 等资金画像字段。
- [改进] `scripts/select_earnings_surprise_candidates.py` 现在会为通过的 `earnings_surprise` 候选补充资金画像，并把 `capital_consensus_score`、`relative_strength_score`、`capital_profile_score` 作为同分排序的优先参考，同时随快照一并落库，便于后续按快照复盘。
- [改进] `scripts/select_monthly_slow_rise_candidates.py` 现在会为 `monthly_slow_rise` 候选补充资金画像，把结构趋势与资金承接一起写入快照与导出结果，并在排序时把资金强弱作为月线结构之后的优先比较项。
- [文档] 新增 `docs/CAPITAL_PROFILE_STRATEGY.md`，把统一资金层 `capital_profile` 的字段、分数、原始快照字段、当前评分口径，以及在 `earnings_surprise`、`monthly_slow_rise` 中的接入方式单独整理成专题文档，并从主策略总纲与本地策略总表补充跳转。
- [文档] 新增 `docs/MAIN_STRATEGY_BLUEPRINT.md`，把后续策略开发主轴固定为“逻辑 + 资金 + 趋势 + 业绩兑现”，并在 `docs/LOCAL_STRATEGY_CATALOG.md` 顶部补上主策略入口，方便后续所有策略围绕同一判断框架收敛。
- [改进] `TushareFetcher` 继续补齐 `trade_cal` 本地参考缓存：优先复用 `data/cache/reference/tushare_trade_cal_sse.csv`，接口失败或无权限时回退到旧缓存；若首次即无权限且本地无缓存，则降级用近 20 天工作日近似交易日历，尽量保证低积分 Tushare 环境下的交易日判断和日线策略可继续运行。
- [改进] `TushareFetcher` 针对低积分场景补上 `stock_basic` 本地缓存：首次成功拉取后会写入 `data/cache/reference/tushare_stock_basic_list.csv`，后续优先复用缓存，接口失败时也可回退到本地缓存，减少 120 积分账号重复消耗在股票列表/名称查询上。
- [修复] 修复 `scripts/select_earnings_surprise_candidates.py` 在季度尚未结束时推导最近已完成财报期会递归卡死的问题，最近业绩公告目录现在可在 1 月、4 月等时点正常构建。
- [改进] `earnings_surprise` 新增 `strict / balanced / relaxed` 三档 `--strategy-profile` 预设，并按档位分别落库 `earnings_surprise`、`earnings_surprise_strict`、`earnings_surprise_relaxed`，便于同日横向比较业绩线策略表现。
- [改进] `earnings_surprise` 扫描会把拉取到的基础面、行情与近期业绩公告覆盖层写入 `signal_fundamental_snapshot`，同日重跑可复用快照并按最新覆盖层补写，减少重复抓取并固定当日判断口径。
- [文档] 新增 `docs/EARNINGS_SURPRISE_PLAYBOOK.md`，补充 `earnings_surprise` 的实战判读手册，整理通过/拦截状态的解读方式、筛选顺序以及与趋势线、题材线的联动使用建议。
- [文档] 新增 `docs/EARNINGS_SURPRISE_STRATEGY_BREAKDOWN.md`，把 `earnings_surprise` 的判断字段、加权结构、放行门槛、`gate_status` 与快照字段拆成独立业绩线专章，并在相关文档中补充跳转。
- [文档] 新增 `docs/LOCAL_STRATEGY_CATALOG.md`，整理本地扫描策略、快照采集脚本、`/signals` 信号类型与 `strategies/` agent 策略总表，便于集中查看当前策略资产。
- [改进] `earnings_surprise` 升级为“硬门槛 + 多因子加权”的业绩策略，新增 `earnings_strategy_score/label/gate_status` 及增长连续性、利润质量、盈利能力、披露文本、周期、新鲜度、风险扣分等快照字段，并整理 `docs/EARNINGS_SURPRISE_TRACKING.md` 便于查看当前判断口径。
- [改进] `earnings_surprise` 扫描现在会接入 `earnings_quality` 评分，允许“强季度连续性 / 周期改善 / 现金流质量较好”的业绩信号参与命中与排序，并把 `earnings_quality_verdict/score/cycle_phase` 等字段落入快照与导出结果。
- [改进] `earnings_quality` 进一步补齐多季度财报序列与连续性判断，新增 `financial_report_series`、`quarterly_continuity_score`、`quarterly_evidence` 等字段，可更严格地区分“连续改善”“持续为正”和“走弱”。
- [改进] 基本面聚合新增 `fundamental_context.earnings_quality` 业绩质量评分块，并同步暴露到 `get_stock_info`、分析/历史详情 API、信号归因摘要和商品涨价受益服务，便于判断增长、现金流和盈利质量是否真实改善。
- [改进] `DataFetcherManager` 新增本地日线磁盘缓存，默认复用 `data/cache/history/` 历史数据，并支持 TTL、强制刷新透传与按股票尾部增量补齐，减少多策略扫描时的重复联网抓取压力。
- [新功能] 新增 `scripts/select_monthly_slow_rise_candidates.py` 与文档 `docs/MONTHLY_SLOW_RISE_SCAN.md`，可先抓日线再聚合月线，按“收涨月份占比、低点抬高占比、MA6/MA12 结构、区间总涨幅、单月最大涨幅、最大回撤”等口径筛选“月线一点点上涨”的 A 股候选。
- [新功能] 新增 `scripts/collect_monthly_slow_rise_profile_snapshots.py`，支持按 `strict / balanced / loose` 三套 profile 批量采集“月线慢牛”快照并写入 `kline_signal_snapshot`。
- [改进] `/signals` 新增动态月线慢牛 profile tab，可直接查看 `monthly_positive_ratio`、`monthly_higher_low_ratio`、`monthly_total_return_pct`、`monthly_max_single_gain_pct`、`monthly_worst_drawdown_pct`、`monthly_ma_short / monthly_ma_long` 等字段。
- [改进] `/signals` 月线慢牛视图新增“严格 / 均衡 / 宽松”一键对比卡片，可同屏查看三档候选池的并集、共识、独有股票与月线指标均值，并从对比卡直接切换到目标 profile。
- [改进] `/signals` 的 `streak` 分组批量联动补上了更明确的反馈：点击“选择本组”后会显示当前联动的主题组/行业组，并支持再次点击“取消本组”；导出与推送摘要也会带上该分组上下文。
- [改进] `schedule` 模式新增可选板块辨识度 TopN 快照调度；开启 `BOARD_RECOGNIZABILITY_SNAPSHOT_ENABLED` 后，会在定时分析后自动刷新 `/signals` 动态板块辨识度 tab 所需快照。
- [新功能] 新增 `scripts/collect_board_recognizability_rankings.py` 与文档 `docs/BOARD_RECOGNIZABILITY_RANKING.md`，可从已落库的 `hundred_day_high` 等信号快照中提取“各板块辨识度 TopN”结果，导出汇总文件并按板块命名空间再次写入 `kline_signal_snapshot`，便于后续查询和回溯。
- [新功能] 新增模块主题核心快照脚本 `scripts/collect_board_theme_core_snapshots.py` 与文档 `docs/THEME_CORE_BOARD_SNAPSHOTS.md`，可按日期保存模块成分股与模块内部子主题核心股结果，便于后续周期性更新与回看。
- [新功能] 新增“按模块分析模块下股票”的能力：`DataFetcherManager` / `AkshareFetcher` 现支持获取概念板块与行业板块成分股，并新增 `scripts/select_board_theme_core_candidates.py` 在模块内部按 `theme_core_mapper` 拆分子主题并筛出核心股。
- [新功能] 新增 `theme_core_mapper` 主题核心映射能力：按 `大主题 / 真子主题 / 个股角色` 三层拆解股票，并结合龙头分析输出 `subtheme_core_probability`，同时新增批量脚本 `scripts/select_theme_core_candidates.py` 用于按子主题自动筛出核心股。
- [改进] `commodity_price_pass_through` 新增显式 `combo_reinforcement` 规则：对“涨价映射强 + 强逻辑 + 资金集中”的股票单独打组合强化分，并同步写入候选池导出、专题快照与服务返回结构，避免这类票只停留在隐含叠加加分。
- [改进] `dragon_head` 候选池扫描新增第二阶段“快速模式”：批量扫描默认优先复用实时行情、板块排行和日线数据，只对更像候选的股票按需补抓主营资料与新闻催化，并新增 `--full-analysis` 用于切回更完整的证据抓取。
- [改进] 优化 `dragon_head` 候选池的数据抓取链路：`DragonHeadAnalysisService` 单票分析合并重复日线请求，`DataFetcherManager` 为板块排行与所属板块增加进程内 TTL 缓存，批量扫描前预热实时行情与板块排行，减少重复联网请求与重试耗时。
- [新功能] `/signals` 正式接入 `dragon_head_candidate` 龙头专题快照，新增龙头专题 tab，并展示 `leader_type / leader_probability / recognizability_score / logic_consensus_score / capital_consensus_score / sector_leadership_score / relative_strength_score / liquidity_score / catalyst_score` 等结构化字段。
- [新功能] 新增 `scripts/select_dragon_head_candidates.py` 与 `scripts/collect_dragon_head_snapshots.py`，支持批量扫描“高辨识度核心龙头候选池”并按日期落库为 `dragon_head_candidate` 专题快照，同时补充 `docs/DRAGON_HEAD_CANDIDATE_SCAN.md` 与 `docs/DRAGON_HEAD_SNAPSHOTS.md` 说明。
- [新功能] 新增 `DragonHeadAnalysisService` 与 `analyze_dragon_head` Agent 工具，并将 `dragon_head` 从“板块强势股策略”升级为“高辨识度核心龙头策略”，按 `辨识度 > 板块地位 > 相对强度 > 流动性 > 催化 > 其它` 的固定优先级识别 `hybrid / logic / capital / pseudo` 四类龙头。
- [改进] 商品涨价专题新增“辨识度优先”的业绩票固定排序口径：`辨识度 > 持续增长 > 大成交 > 低估值 > 股息率 > 其它`，其中辨识度按“逻辑共识 + 资金共识”实现，并同步用于专题分析结果、候选池排序、专题快照落库与 `/signals` 展示。
- [新功能] `/signals` 正式接入商品涨价专题快照，新增光纤/内存/硬盘三类专题 tab，并展示 `subtheme / chain_role / pass_through_direction / earnings_release_probability / directness / matched_example_bucket` 等结构化字段。
- [新功能] 新增专题快照脚本 `scripts/collect_commodity_beneficiary_snapshots.py` 与文档 `docs/COMMODITY_BENEFICIARY_SNAPSHOTS.md`，可将光纤/内存/硬盘等商品涨价受益候选池按日期落库到 `kline_signal_snapshot`，signal_type 使用 `commodity_beneficiary__{commodity}` 命名空间，并记录历史命中统计。
- [新功能] 新增外置专题配置目录 `config/commodity_pass_through/` 与批量扫描脚本 `scripts/select_commodity_beneficiaries.py`，支持基于光纤/内存/硬盘/铜专题规则与真实 A 股样例白名单/反例，直接导出“商品涨价受益候选池”的 `csv/txt/md` 结果。
- [改进] `CommodityPassThroughService` 第三版把光纤/内存/硬盘进一步拆成更细的 `subtheme` 映射表，并新增一批真实 A 股样例白名单与反例（如长飞光纤、兆易创新、佰维存储、同有科技，以及中际旭创、工业富联、海康威视等），用于在策略问答里对精确代码做优先纠偏。
- [改进] 新增结构化分析工具 `analyze_commodity_pass_through` 与 `CommodityPassThroughService`，内置光纤/内存/硬盘/铜的小型商品映射表和 `upstream / midstream / downstream / distribution / weak_proxy` 角色标签，用于在策略问答中更稳定地区分直接受益、间接受益与成本承压公司。
- [新功能] 新增 Agent 内置策略 `commodity_price_pass_through` 与专题文档 `docs/COMMODITY_PRICE_PASS_THROUGH.md`，按“商品涨价 -> 产业链位置 -> 利润传导 -> 财报验证 -> 技术确认”的链路识别更可能真正释放业绩的 A 股公司，适用于光纤、内存、硬盘等涨价题材的结构化研判。
- [改进] `/signals` 顶部主导航下新增二级摘要条，按当前视图直接展示日期范围、总命中、当前筛选数量、平均 YTD / 中位 YTD，以及业绩公告日过滤等关键信息，减少切换信号后的二次确认成本。
- [改进] `/signals` 顶部新增三种快捷视图切换按钮：`百日新高`、`业绩超预期`、`新高且业绩`，保留原有下拉选择的同时，减少在常用信号之间反复切换的操作成本。
- [改进] `/signals` 新增 `新高且业绩` 组合视图，可直接查看 `hundred_day_high ∩ earnings_surprise` 的同日交集；组合视图支持历史观察、YTD 排序、YTD 快速筛选以及多日对比摘要。
- [改进] `/signals` 进一步补齐 YTD 维度：多日对比卡片新增 `平均 YTD / 中位 YTD`，当前页支持 `YTD > 0% / 20% / 50%` 快速筛选，并可在页面内切换 `hundred_day_high / earnings_surprise` 两类信号快照。
- [改进] `earnings_surprise` 快照新增更贴近公告节奏的 `event_date` 口径：优先取业绩快报公告日，其次取业绩预告公告日，最后才回退到报告期；事件去重与 `/signals` 展示同步基于该口径增强。
- [改进] `/signals` 页面与信号查询接口现在直接带出年内涨幅：`SignalSnapshotService` 会为列表和历史记录补充 `year_start_date / year_start_close / ytd_return_pct`，前端卡片、历史观察与导出内容可直接查看 `hundred_day_high` 等信号在命中日的年内位置。
- [改进] 新增 `scripts/report_signal_snapshot_ytd.py` 与专题文档 `docs/SIGNAL_SNAPSHOT_YTD.md`，可直接基于已落库 `kline_signal_snapshot` 统计 `hundred_day_high` 等信号在命中日的年内涨幅，默认不传日期时自动取该信号类型最新快照日，无需重新全市场扫描。
- [改进] 新增 `scripts/select_earnings_surprise_candidates.py` 与专题文档 `docs/EARNINGS_SURPRISE_TRACKING.md`，可按“正向业绩文本 + 同比增长阈值”的透明规则扫描 A 股业绩超预期代理事件，默认按事件键去重落库为 `signal_type=earnings_surprise`，并可直接复用现有 `scripts/evaluate_signal_snapshot_performance.py` 做 `1/3/5/10` 日后续涨跌汇总。
- [改进] 新增 `scripts/collect_hundred_day_high_profile_snapshots.py`，可按 `hundred_day_high_profile__{profile}` 这类独立 `signal_type` 批量采集多套 profile 的同日快照，避免 `breakout_balanced / momentum_strict / breakout_loose` 在同一天互相覆盖，便于后续按 `3D/5D/10D` 表现正式比较。
- [改进] 百日新高脚本新增 `--profile` 预设能力，当前内置 `breakout_balanced`、`momentum_strict`、`breakout_loose` 三套口径；落库快照会同步记录 `profile_name`，后续可直接按 profile 做表现对比。
- [改进] 新增 `scripts/evaluate_signal_snapshot_performance.py`，可直接基于已落库的 `kline_signal_snapshot` 计算 `1/3/5/10` 日等 forward return、胜率、中位数收益、平均最大冲高与平均最深回撤，帮助持续评估 `hundred_day_high` 等信号策略质量。
- [改进] `--cause-analysis-only` 现在默认只补同日仍为空的归因结果，并新增 `--force-cause-refresh` 用于在需要时显式重跑已有归因，避免日常补全重复处理已完成股票。
- [改进] 百日新高归因补全新增快路径：可跳过新闻搜索并禁用 LLM 原因卡，仅用已有结构化证据生成 fallback 摘要；现有定时模式已默认使用该快路径控制每日更新耗时。
- [改进] 现有 `schedule` 模式可选接入百日新高快照日更：开启 `SIGNAL_SNAPSHOT_HUNDRED_DAY_HIGH_ENABLED` 后，会在每日定时分析后自动刷新 `/signals` 所需快照，并可选继续补全归因。
- [改进] 百日新高脚本改为“先快照、后归因”的两阶段流程：扫描过程中会先增量落库同日快照，后续可用 `--cause-analysis-only` 仅对已落库结果补全归因，不必再次重扫全市场。
- [改进] Web 前端新增路由级懒加载，并拆分 `charts`、`markdown`、`router`、`http`、`motion-ui`、`icons` 等构建 chunk，降低首页与 `/signals` 等工作台页面的初始包体压力。
- 📉 **新增信号汇总趋势查看脚本** — 新增 `scripts/report_signal_summary_perf_trend.py`，可直接读取 `signal_summary_perf_history.jsonl`，输出最近记录的规模/耗时/吞吐摘要，并生成 Markdown 小报表，方便后续观察重建耗时趋势。
- 📈 **信号汇总观测支持追加时间序列日志** — `scripts/rebuild_signal_summary_tables.py` 新增 `--append-jsonl`，可将每次观测/重建结果按 JSON Lines 追加到长期日志文件，便于后续直接对比 `elapsed_seconds`、`throughput`、`snapshot_count` 等趋势。
- 📊 **信号汇总重建脚本补齐性能观测模式** — `scripts/rebuild_signal_summary_tables.py` 新增 `--report-only` 与 `--report-json`，可直接输出 `snapshot_count / day_count / code_count / daily_summary_count / streak_snapshot_count / elapsed_seconds / throughput` 等观测指标，便于后续在真实库上持续记录重建基线。
- 🧱 **新增信号汇总表全量重建脚本** — 新增 `scripts/rebuild_signal_summary_tables.py`，可针对全部或指定 `signal_type / 日期范围 / 股票代码` 重建 `kline_signal_daily_summary` 与 `kline_signal_streak_snapshot` 两张预计算表，适合用于老数据补齐、恢复备份后重刷、或后续规则升级后的汇总重算。
- 🚄 **`/signals` 服务端补齐预计算汇总表** — 新增 `kline_signal_daily_summary` 与 `kline_signal_streak_snapshot` 两张热点汇总表，`upsert_signal_snapshot(...)` 时会同步刷新日度命中摘要与窄字段 streak 快照；范围查询现在优先走预计算结果来生成 `compare_summary` 和 `streak_leaderboard`，进一步减少长范围下的 payload 反序列化与 Python 侧重复聚合开销。
- 🚀 **`/signals` 页面补齐导出 / 推送与长范围优化** — `/signals` 现在可直接把当前联动或连续新高结果导出为 `Markdown / JSON`，也可复用现有通知渠道一键推送选中快照；页面同时补上了更明显的“已选 N 只”状态条、分组内选中计数、选中代码 badge，以及长日期范围下的前端缓存、重复请求收敛、对比卡片折叠展示等优化，降低多日复盘时的等待和滚动开销。
- 🔎 **百日新高快照查询能力补齐到 API + Web** — 新增 `SignalSnapshotService` 与 `/api/v1/signals/kline-snapshots` 查询接口，可按 `signal_type + signal_date` 查看当天命中的 K 线信号快照，并返回 `industry / reason_summary / industry_logic / news_logic / technical_logic / theme_label / latest_previous_hit_date / previous_hit_count` 等结构化字段；同时新增 `/api/v1/signals/kline-snapshots/{signal_type}/{code}`，用于查看某只股票最近一段时间的同口径命中历史，补充连续命中统计以及基于已落库快照的近似回撤摘要。Web 端同步新增 `/signals` 页面与侧边导航入口，支持按日期查看当天快照、按股票查看最近历史、连续新高与近似回撤；后续增强中又补上了日期范围、多日对比、连续新高显式分组、分页，以及多日对比里的 `新增 / 掉队 / streak 排行`，便于后续直接观察“哪些票新进来了、哪些票掉队了、哪些票在持续连创新高”。
- 📈 **百日新高信号跟踪与归因闭环** — `scripts/select_hundred_day_high_candidates.py` 现已从单纯筛选扩展为“筛选 -> 日度落库 -> 上涨原因归因 -> 历史复现回看”的完整链路：新增通用 `KlineSignalSnapshot` 日线信号快照模型与查询接口，用 `(signal_type, signal_date, code)` 唯一约束保存 `hundred_day_high` 命中结果；脚本默认会回看近 180 天同口径信号的复现次数/最近命中日期/距上次间隔，并结合基本面、新闻、所属板块和主题级海外映射生成 `reason_summary`、`industry_logic`、`news_logic`、`technical_logic`、`cause_tags`、`theme_label` 等摘要字段，同时保持 `csv/txt/md` 导出不变，便于后续继续扩展更多独立 K 线条件入口。后续跟进中又收紧了 `theme_label` 的判定逻辑：优先使用当日强势池行业、主营业务和高置信新闻线索，证据不足时宁可留空，也避免把普通行业票误贴成 AI / 创新药 / 资源涨价等海外主题。
- 📌 **新增“次日确认策略”批量扫描入口** — 新增 `scripts/scan_next_day_setups.py` 与 `src/services/next_day_setup_service.py`，可在全市场 A 股（自动排除北交所）批量扫描 `inside_day / nr7 / reversal / all` 四种模式；底层复用现有 `KlineSelectorService` 的股票池、并发、分片、现货预过滤与 checkpoint / `--resume` 能力，输出 `next_day_setup_candidates.csv/txt/md`，用于“第一天复盘选信号，第二天盘中做突破确认”的候选池流程。
- 🤖 **新增 3 个“复盘后次日确认买”内置策略 skill** — `strategies/` 新增 `inside_day_breakout.yaml`、`nr7_breakout.yaml`、`reversal_confirmation.yaml`，分别覆盖内包日突破、NR7 窄幅突破、吞没/早晨之星等反转K线的次日确认模型。三者统一强调“信号日只做复盘，次日突破信号K高点后才确认买点”，避免把单一K线形态直接当作收盘盲买信号；README 与双语说明中的内置策略数量同步从 11 更新为 14。
- 🚀 **K 线分片并行运行与结果合并** — `scripts/select_kline_candidates.py` 与 `scripts/select_hundred_day_high_candidates.py` 新增 `--shard-count` / `--shard-index`，可将标准化后的 A 股股票池按分片拆给多个独立进程执行；分片运行时会自动为输出目录与 checkpoint 追加 shard 编号，避免互相覆盖，并在输出目录内落一份策略专用 checkpoint。同步新增 `scripts/merge_kline_shard_results.py`，可将多个分片目录重新合并成与当前单次运行一致的 `csv/txt/md` 输出，便于在当前 Windows 环境下保持单进程 `--max-workers 1` 的稳定性，同时通过多进程分片缩短全市场扫描总耗时。

- 📈 **百日新高策略新增独立入口** — 新增 `scripts/select_hundred_day_high_candidates.py`，可单独运行“最新 K 线 `high` 创近 N 日新高”的 A 股筛选，不再与当前“上涨占比 + 涨停 + 百日新高”的组合 K 线策略混用；底层同时为 `KlineSelectorCriteria` 增加了 `require_up_day_ratio` 开关，便于后续扩展更多独立规则入口。
- 📈 **大盘复盘新增今日涨停股复盘表** — `MarketAnalyzer` 现已在 A 股大盘复盘中追加“今日涨停股复盘表”，按连板数与封板资金展示代表性涨停股，并补充涨停原因、涨停统计与近 250 个交易日历史涨停次数，保持现有 Markdown 报告输出链路不变，可直接复用于 `/market`、`--market-review` 与通知推送。

### 新功能

- 📈 **独立 K 线条件筛选器** — 新增 `src/services/kline_selector_service.py` 与 `scripts/select_kline_candidates.py`，可对全市场 A 股执行独立扫描并自动排除北交所；当前内置规则包括最近 10 个交易日上涨占比大于 70%、最近 10 日至少 1 次涨停、最新 K 线 `high` 创 100 日新高、总市值不超过 500 亿。脚本会输出 A 股样本列表以及候选结果的 `csv/txt/md` 文件，便于后续回看和扩展更多 K 线规则；同时新增保守并发参数 `--max-workers`、现货预过滤参数以及 checkpoint / `--resume` 断点续跑能力，并默认走更精简的 `Akshare` 单链路抓取。基于当前 Windows 环境的真实联网验证，组合筛选脚本现已默认使用 `--max-workers 1` 作为稳定基线；如需提速，更推荐使用分片而不是继续拉高 worker。使用说明见 `docs/KLINE_SELECTOR_GUIDE.md`。
- [修复] WebUI 启动时显式 `--host` / `--port` 不再被 `.env` 中的 `WEBUI_HOST` / `WEBUI_PORT` 覆盖，未传 CLI 参数时统一使用解析后的运行时配置。
- [改进] GitHub Actions: 每日分析工作流（`00-daily-analysis.yml`）新增钉钉通知环境变量映射，支持在云端定时任务中直接使用钉钉机器人。
- [修复] Web 持仓页首屏快照改用 `include_realtime=false` 快速估值，跳过逐票实时行情预取后先展示持仓列表，避免外部实时行情源变慢时长时间空白等待。
- [修复] 修复任务状态接口重建报告动作字段时把合法情绪分 `0` 当成空值的问题，确保低分报告能按评分口径纠正为卖出建议。
- [修复] 修复 Agent 流式回复在未收到完成事件就断开时被显示为“（无内容）”的问题，改为提示流式响应中断并保留用户消息，避免误判为空回答。
- [修复] 修复桌面端 `WEBUI_HOST=*` / `WEBUI_HOST=[::]` 会被原样传给端口探测和后端启动导致无法监听的问题，启动前分别规范化为 `0.0.0.0` / `::`。
- [改进] `STOCK_LIST` 自选股解析支持中文逗号、顿号、分号、空格和换行等常见粘贴分隔符，运行时、定时热刷新、CLI `--stocks`、Web 设置保存和自选 API 统一识别，并在写回时规范为英文逗号。
- [改进] 新增 `NEWS_INTEL_AUTO_FETCH_ENABLED` 单开关，开启后个股分析、Agent 分析和大盘复盘会 fail-open 自动初始化并刷新 RSS/Atom/NewsNow 本地资讯池。
- [改进] Web AI 建议页新增主股票上下文，复用最近分析和股票索引候选，并改进表现统计零样本说明。
- [改进] 补充本次设置页布局收敛：移动端分类导航改为横向滚动列表并保证设置内容首屏可见，桌面端保留分类说明并收紧字段布局层级与间距，提升首屏效率与可配置信息密度。
- [文档] 在 README 快速开始中补充行情数据源配置说明（TUSHARE_TOKEN / Longbridge），明确未配置时仍可走 AkShare、Baostock、YFinance 等免费兜底源，日志中相关提示不影响运行。同步更新docs下的中英双份 README

<!-- 新条目格式：- [类型] 描述（类型取值：新功能/改进/修复/文档/测试/chore）-->
<!-- 每条独立一行追加到本段末尾，无需分类标题，合并时冲突最小 -->

## [3.25.0] - 2026-07-03

### 发布亮点

- feat: 新增 `claude_code_cli`、`opencode_cli` generation-only 本地 CLI backend，并补齐生成后端状态诊断、预览、冒烟测试 API 和 Web 状态面板。
- feat: 台股报告完整接入三大法人资料，覆盖报告渲染、LLM prompt、TWD 币别标示、收盘集合竞价识别和 fetcher 韧性加固。
- feat: 新增钉钉群机器人通知、韩语报告输出和 AI 建议决策风格重评估预览。
- feat: Agent `/chat/stream` 标准化 progress event，新增阶段开始/完成、pipeline timeout 和预算跳过语义。
- fix: 修复桌面端 WebUI host/port 绑定、macOS Homebrew CLI PATH 诊断、Discord 长报告分片、AlphaSift 超时、yfinance 分红解析、A 股回测代码归一化等稳定性问题。

### 新功能

- 钉钉群机器人通知支持 `DINGTALK_WEBHOOK_URL` 和 `DINGTALK_SECRET`，并对长文本自动切片以适配 20KB 限制。
- 报告输出语言新增韩语（`REPORT_LANGUAGE=ko`），覆盖个股报告、大盘复盘、Prompt 输出语言、决策护栏、通知模板标签与 Web 报告详情页文案。
- 新增 `claude_code_cli` 与 `opencode_cli` generation-only 本地 CLI backend，保留 LiteLLM 默认路径、Agent 工具调用边界、per-preset extractor、最小 env allowlist 与结构化错误。
- 新增生成后端状态、预览和冒烟测试 API，以及 Web 生成后端状态面板，区分轻量检查与 JSON 冒烟测试，并保持本地 CLI “仅生成、不支持问股工具调用”的边界。
- Agent `/chat/stream` progress event 新增 `stage_start`、`stage_done`、`pipeline_timeout`、`pipeline_budget_skipped`，补齐阶段进度、超时和预算跳过语义。
- 台股个股报告的 institution 区块展示 TWSE T86 / TPEx 三大法人原始买卖超净额，并将三大法人净买卖超表格注入 LLM 分析 prompt 作为台股筹码过滤器。
- 新增 AI 建议决策风格重评估预览接口与页面预览。

### 改进

- 台股三大法人 fetcher 增加并发缓存防击穿、TWSE/TPEx 分市场熔断、TPEx 日期保护和剩余 stage 预算复用，降低限流、端点故障和冷抓取超时带来的降级概率。
- AlphaSift 默认依赖 pin 更新到 `9f522747caafd3c0b1ddb7e14d5cf44c8580b6cf`，接入 wrapper 数据源 caller-side timeout、东财直连限速/抖动、策略目录元数据和防守策略。
- 选股任务状态轮询遇到可恢复超时时提示后台任务仍会自动重试，`.env.example` 补充相关超时调优项。
- 收敛个股分析评分与 DecisionSignal action 口径，统一 80/60/40/20 分段，并在风控降级时记录 raw/adjusted score、final action 与原因。
- Web 设置页左侧分类切换时仅在相关分类展示首次启动检查和 AlphaSift 辅助卡片，减少跨分类残留。

### 修复

- 修复 Windows 桌面端启动后端时固定传入 `--host 127.0.0.1` 导致 `.env` 中 `WEBUI_HOST=0.0.0.0` 不生效、局域网无法访问 WebUI 的问题；桌面端仍默认使用 `127.0.0.1`，仅在显式配置 `WEBUI_HOST` 后按配置绑定。
- 修复桌面端启动时 `.env` 中 `WEBUI_PORT` 与 Electron 自动选择端口不一致，导致窗口继续等待旧端口并连接超时的问题。
- 修复 macOS 桌面端从 Finder/Dock 启动时后端 PATH 看不到 Homebrew Codex CLI 的问题，并明确 Codex CLI 主分析与 Agent LiteLLM 工具调用分流诊断。
- 修复 Discord 长报告推送按 2000 字符上限分片逐段发送，遇到 429 限流会按 `retry_after`/`Retry-After` 有限重试，避免中途失败后只收到前半段报告。
- 修复日股、韩股和台股 `market_phase` 收盘集合竞价识别，避免临近收盘阶段仍被标记为普通 `intraday`。
- 修复 A 股个股分析遇到空 `belong_boards` 占位时不会继续补查所属板块、关联板块模块展示不稳定的问题。
- 修复大盘复盘在 LLM 标题漂移或正文缺少板块段时，Web 与推送报告偶发缺少板块主线的问题。
- 修复 Web 大盘复盘结构化数据成交额、指数点位、涨跌幅和高/低值格式化，避免浮点长尾或缺失值 `0.00` 直接展示。
- 修复 Web 首页个股栏在 stock-bar 摘要字段缺失或动作建议无法归类时隐藏情绪分与建议标识的问题。
- 修复 Web 设置页定时任务“立即执行一次”后台线程未传 `stock_codes` 导致任务崩溃的问题。
- 修复 `opencode_cli` 静态指令，避免全局 JSON-only 约束影响 `generate_text()` 与大盘复盘自由文本输出。
- 修复 yfinance 1.2.x 将 `Ticker.dividends` 返回为单列 DataFrame 时分红解析被丢弃的问题，恢复 TTM 每股分红与分红次数计算。
- 修复台股财务金额币别标示，将 TWD 金额标注为“新台币”，避免在 A 股语境下误读为人民币。
- 修复回测日线补全将 `605066.SH`、`SS605066`、`SS.605066` 等 A 股等价代码误向数据源请求 `SS605066`，导致回测数据不足的问题。

### 文档

- 新增 Agent `/chat/stream` progress event 契约文档，说明新增事件字段语义、Web 兼容边界、验证方式和回滚方式。
- 同步本地 CLI backend 隐私/部署边界，明确 local CLI 不是离线模型，Docker/CI/远端需自行安装登录，DSA 不读取 Claude/OpenCode credential 文件。
- 更新 README 三语入口和市场支持边界，说明台股 `.TW` / `.TWO`、三大法人报告区块、TWD 标注与收盘竞价识别能力边界。

### 测试

- 台股三大法人 fetcher 新增 live-smoke 脚本与 `@pytest.mark.network` 漂移检测测试，用于非阻断 network-smoke 定时任务核对 TWSE T86 / TPEx 核心字段与解析结果。

## [3.24.1] - 2026-06-28

### 修复

- 修正 Longbridge SDK 版本约束为按平台选择可安装版本，避免桌面与 Docker 发布在 `pip install -r requirements.txt` 时因不存在的 `0.2.75` 版本失败。

## [3.24.0] - 2026-06-28

### 发布亮点

- feat: 扩展台股、日股、韩股市场支持，覆盖台股 suffix-only 分析、台股三大法人资料层、JP/KR 大盘复盘和跨服务市场枚举。
- feat: 新增 GenerationBackend 抽象、`codex_cli` 本地 CLI backend、reserved Hermes 本地 HTTP 渠道和 prompt cache capability registry。
- feat: Web/API/Desktop 支持多时间定时推送与 runtime scheduler 热重建，Web 设置页补齐首次启动检查与定时任务面板。
- feat: 报告链路补齐信号归因、单股信号时间线、概念板块排行和通知/报告关联板块展示。
- fix: 修复 Docker/启动探针、静态资源 MIME、回测空结果、组合估值、通知 Markdown、AlphaSift 数据源和测试环境隔离等稳定性问题。

### 新功能

- 新增台股 suffix-only 个股分析 MVP：`.TW`/`.TWO` 代码可走 YFinance 日线与近实时行情，并补齐市场识别、交易日历和 Prompt 能力边界。
- 台股 `tw` 纳入 DecisionSignal、Portfolio、Intelligence 服务层、API 枚举和 Web 筛选，避免台股分析信号被市场归一化静默丢弃。
- 新增台股三大法人资料层 fetcher `TwInstitutionalFetcher`，支持 TWSE/TPEx 来源、日期转换、单日缓存和 fail-open 退化。
- 大盘复盘新增 `jp`/`kr` 市场，支持日经225/TOPIX、KOSPI/KOSDAQ 指数复盘，并扩展 `MARKET_REVIEW_REGION`、交易日过滤和 Web 设置枚举。
- 新增 GenerationBackend Phase 1 抽象和显式 opt-in 的 `codex_cli` 本地 CLI generation backend，提供结构化错误、fallback、stream 降级和 usage unavailable contract。
- 新增 reserved Hermes 本地 HTTP generation 渠道，提供 JSON generation、no-proxy 本地调用和 saved secret endpoint 绑定。
- 新增 Provider Cache Capability Registry，按 provider、API surface、gateway 与 verification status 建模 prompt cache 能力。
- 支持 `SCHEDULE_TIMES` 多时间定时推送，长运行 Web/API/Desktop 进程保存调度配置后可热启停或重建 runtime scheduler。
- 新增信号归因分析和 Web AI 建议页单股信号时间线，并为自动生成与历史回填的 DecisionSignal 写入默认 `decision_profile` metadata。
- 大盘复盘、Web 报告页和通知关联板块补齐概念板块排行与概念信号展示。

### 改进

- TickFlow 扩展为可选 A 股日 K、实时行情、股票列表/名称数据源，并增加 count、完整性校验和批量预取缓存保护。
- 硬化 JP/KR/TW suffix 识别、日韩股票种子索引、YFinance 报价/基本面上下文，以及 JP/KR Portfolio 与 Market Light 边界。
- Web 设置页新增首次启动配置检查卡与定时任务面板，隐藏内部 `SCHEDULE_TIMES` 键，并改善重复任务提示的关闭与自动消失体验。
- Web 历史报告详情不再内嵌 AI 建议卡片，结构化决策信号集中到 AI 建议页，并保留来源报告 ID/URL 参数精确定位。
- `GENERATION_BACKEND=codex_cli` 下普通分析与大盘复盘不再因缺少 LiteLLM API Key 被误判不可用，并改用 `--output-last-message` 文件读取最终响应。
- 本地 CLI backend 对 stdout/stderr 诊断预览和最终响应实行执行期总量上限，并补齐新增 generation backend 数字配置最大值校验。
- AlphaSift 默认依赖 pin 更新到 `0a7b9cd59e81718f851890535241bc105d4ddc64`，并默认走 DSA EastMoney 兜底 provider、暴露 source health 诊断。
- Docker Compose 默认内存建议提升到 1G；每日分析 workflow 兼容误将 `STOCK_LIST` 配到同名 Environment variables 的场景。
- Agent 路径同步 signal attribution prompt，通知报告摘要不再展开 AI 决策信号明细，完整信号保留在个股详情与单股报告。

### 修复

- API 异步批量分析共享概念板块排行缓存，避免同批多股重复拉取全市场概念排行。
- 修复通知 Markdown 表格转换在空单元格后将后续内容错配到错误表头的问题。
- 修复 Market Light 区域归一化拒绝 `jp`/`kr`、日韩历史列表市场阶段摘要误传 `analysis_phase` 和默认通知报告缺少 `dashboard.phase_decision` 的问题。
- 固定 Docker 可安装的 Longbridge SDK 版本为 0.2.75，并修复 Docker 镜像中 efinance 缓存目录属主导致 A 股数据源降级的问题。
- 持仓快照今日估值改为受限并发预取实时价，减少持仓较多时 Web 组合页面刷新超时。
- Web 首页重新分析完成后自动切换到同一股票最新报告，并修复 Windows 环境下 Web/Desktop 静态 JS 资源可能以 `text/plain` 返回导致黑屏的问题。
- 修复 `--serve --schedule` 与 Web/API runtime scheduler 状态脱节、立即执行忙碌状态误提示、重建定时任务重复监听和启动参数语义丢失。
- 修复 `main.py --serve-only` 在低配主机上因惰性 import 应用超出 uvicorn 启动自检窗口而反复重启的问题。
- 修复 Web 回测未传分析日期范围、股票代码未归一化导致成功响应但结果为空的问题，并为空候选、行情不足和非法后缀提供诊断信息。
- 修复 unsupported `GENERATION_BACKEND` 被当成空响应/模板 fallback、`codex_cli` stdout 重复计入输出上限和主分析 JSON schema fallback 语义回退的问题。
- Docker 部署中 Web 设置页保存自定义 Webhook 模板时会转义 `$content_json` 等占位符，并在运行时还原，避免 Compose 重新部署展开为空。

### 文档

- 补齐概念板块排行字段契约、通知报告行业/概念类型列展示和数据源稳定性与故障处理图示。
- 补充 JP/KR/TW suffix-only MVP、`MARKET_REVIEW_REGION` 保存/校验/回退矩阵、Market Light 边界和 PR 提交流程约束。
- 补充本地 CLI backend 隐私边界、非离线模型说明、Docker/CI 登录态限制和 `codex_cli` experimental/limited 状态。
- 补充回测请求链路说明，并同步更新 `docs/full-guide.md` 与 `docs/full-guide_EN.md` 示例。

### 测试

- 新增/更新台股、JP/KR 大盘复盘、GenerationBackend、`codex_cli`、Hermes、本地 CLI、runtime scheduler、回测和概念板块排行相关回归测试。
- 加强 `tests/test_analysis_api_contract.py`、`tests/test_analysis_history.py` 与 `tests/test_backtest_service.py` 的临时 `.env` 隔离，避免本地真实 `.env` 污染系统配置测试。

## [3.23.0] - 2026-06-20

### 发布亮点

- feat: DecisionSignal 贯通报告提取、Web 展示、反馈/后验、告警通知和组合风险，AI 建议信号进入可追踪闭环。
- feat: 新增合规 RSS/Atom 与 NewsNow 资讯源情报池，分析、Agent 和大盘复盘可 fail-open 复用本地资讯 evidence。
- feat: 新增日本/韩国 suffix-only 个股分析 MVP，支持 `.T`、`.KS`、`.KQ` 标的通过 YFinance 获取行情与技术上下文。
- feat: 新增 Token 用量监控看板、legacy LLM usage telemetry 和 message stability audit，增强 LLM 调用可观测性。
- fix: 修复运行流 live 状态、AlphaSift 缓存/字段兼容、发布说明诊断和日韩股票输入/历史展示等稳定性问题。

### 新功能

- 个股分析历史成功保存后会从最终报告 best-effort 提取 `DecisionSignal` 决策信号，复用现有信号去重、计划质量计算和脱敏契约。
- 新增 Web AI 建议页、持仓页 latest active 信号摘要、历史报告信号展示和更完整的信号详情卡片，展示评分、置信度、价格计划、催化、风险与失效条件。
- 新增 DecisionSignal 用户反馈、信号级日线后验评估、统计 API 与 Web 展示，使用 outcome/feedback sidecar 表并保留主信号表契约。
- 将 DecisionSignal 复用到告警、通知和组合风险：告警触发关联 latest active 信号或创建最小 alert 信号，通知追加低敏信号摘要，持仓风险聚合 active sell/reduce/alert 信号并保持 fail-open。
- 新增合规 RSS/Atom 资讯源配置、拉取、去重、入库、查询、retention 与基础安全校验 API，作为个股/市场资讯情报池基线。
- 资讯源新增 `newsnow` 类型、`NEWSNOW_BASE_URL` 配置和 `/api/v1/intelligence/sources/defaults` 默认源初始化接口，内置财联社热门、雪球热门股票、华尔街见闻快讯、金十数据和格隆汇事件等财经热点源。
- 个股分析、Agent 分析和大盘复盘会 fail-open 读取本地资讯/情报池，并把来源链接作为新闻上下文和 evidence 输入。
- 新增日本/韩国 suffix-only 个股分析 MVP：手输 `.T` / `.KS` / `.KQ` 代码可走 YFinance 日线与近实时行情，补充市场识别、交易日历、Prompt 语义、Web/API 类型和能力边界文档。
- 新增 Token 用量监控看板与 `/api/v1/usage/dashboard` 接口，展示 LLM 调用总量、Prompt/Completion 拆分、模型用量、调用类型分布和最近调用明细。

### 改进

- 为 `DecisionSignal` 补齐默认生命周期、同源窄 relaxed 去重、相反 active 信号自动 invalidated、terminal 状态不可 PATCH 复活和低敏 market phase hints 提取。
- 补充 Web decision-signals typed API wrapper 与契约隔离测试，并将历史报告 AI 建议查询收口到精确报告懒提取。
- DSA 数据源链路新增 Tencent 日 K 直连 fetcher、daily source health 短期熔断，并升级 AlphaSift 默认 pin/runtime bridge。
- 默认启用 `DAILY_SOURCE=auto`、Sina snapshot 优先级、候选级 quote context 与 LLM ranking timeout/max tokens 边界。
- 新增 legacy LLM usage provider/cache telemetry、message HMAC 诊断字段和普通个股分析 legacy message stability audit，不改变公开 Usage API、prompt 或 provider 参数。
- 问股页移动端策略选择改为默认收起的按钮入口，展开后仍可多选策略并在发送后自动收起，减少对对话内容的遮挡。

### 修复

- 修复运行流 live SSE 脱敏、后期 LLM/通知卡片重复、数据源聚合卡片过早成功、Web 首页窄侧栏挤压股票信息，以及个股分析自动生成大盘上下文时运行诊断互相串扰的问题。
- 修复 AlphaSift 热点题材 EastMoney 瞬断且无缓存时的空态、桌面更新热点缓存保留，以及 `leader_stocks` / `stocks` 双字段兼容问题。
- 修复 Web AI 建议页筛选/状态更新分页、价格计划单边入场价展示、持仓 latest 信号刷新、详情 JSON 安全渲染和卡片交互语义问题。
- 仅允许历史报告存在明确 `action` 或可解析动作时才触发决策信号懒回填，避免 `decision_type=hold` 等统计口径在建议不明确场景误回填。
- 修复 #1390 P6 DecisionSignal 在组合风险快照语义和默认聚合通知展示中的遗漏。
- 默认禁用 `/api/v1/intelligence/sources/defaults` 新建源，避免公开示例 NewsNow 实例被默认启用，同时统一 500 响应细节仅入日志、响应返回通用错误信息。
- Web 股票自动补全、输入校验、历史/任务展示和筛选补齐日韩 Yahoo 后缀代码、常用日韩股票索引与股票池裸码解析，避免 `000660`、`005930`、`7203.T`、`005930.KS`、`035720.KQ` 等场景崩溃、误入 A 股语义或历史分裂展示。
- 日韩个股分析在本地历史上下文缺失时会用 YFinance 日线兜底构造 K 线与技术指标上下文，避免报告误称日股/韩股核心行情和技术数据不可用。
- 发布说明生成查询 PR 作者失败时保留降级并输出包含 PR 编号和异常类型的 warning，便于排查 token、权限、网络或 GitHub API 异常。

### 文档

- README、完整指南和市场支持文档补充日股/韩股示例（`7203.T`、`005930.KS`），并明确 `.T/.KS/.KQ` 当前为 YFinance-only MVP。
- 新增 DecisionSignal 决策信号专题文档，补齐字段/API/Web/告警通知/组合风险/后验评估、脱敏、迁移与回滚说明，并收口 Web i18n 显示边界。
- 补充 AlphaSift 迁移与回退边界：明确 `ALPHASIFT_INSTALL_SPEC` 显式覆盖语义、`requirements.txt + DEFAULT_ALPHASIFT_INSTALL_SPEC` 与运行时兼容边界。
- 补充资讯源基线文档，说明 `NEWS_INTEL_*` 配置、NewsNow 自建建议、模型/provider/base URL 不变更边界，以及禁用或移除情报源变量的回退路径。

### 测试

- 新增/更新 DecisionSignal 服务、提取、反馈/后验、摘要、文档、通知、告警、持仓风险、Web 展示和 label 的回归覆盖。
- 新增/更新 RSS/Atom / NewsNow 情报源服务、API、安全校验、分析接入和配置兼容测试。
- 新增/更新日韩市场识别、股票索引、YFinance 行情兜底、Web 自动补全和输入校验测试。
- 新增/更新 LLM usage、运行流、AlphaSift、发布说明生成和移动端交互相关回归。


## [3.22.0] - 2026-06-13

### 发布亮点

- feat: 新增 DecisionSignal 独立存储与 API、运行流快照 API 和 Web 运行流视图，补齐建议动作结构化字段与历史/回测展示链路。
- feat: AlphaSift 热点题材链路升级为新版合约，支持热点榜单、题材详情、发酵路线、概念股详情、缓存与兜底数据源。
- feat: 个股分析默认注入当日大盘环境摘要，并在高风险/退潮环境下软化激进买入建议。
- fix: 修复问股历史追问标的上下文、自选股等价代码匹配、低质量新闻过滤、运行流脱敏与 AlphaSift 热点详情展示等稳定性问题。

### 新功能

- 新增独立 `DecisionSignal` 存储、Repository、Service 与 `/api/v1/decision-signals` API，支持来源/市场/股票/动作/期限/阶段去重、查询、续期、状态更新、懒过期、持仓过滤和敏感信息脱敏。
- 新增分析任务与历史报告运行流快照 API，提供 lanes、nodes、edges、events、summary 等统一契约，并从任务队列、运行诊断和 AnalysisContextPack overview 构建脱敏数据流/信息流。
- Web 端为活跃任务、历史报告和大盘复盘报告补充运行流视图入口，支持查看运行摘要、拓扑节点、事件流和基础排障详情。
- 新增 AlphaSift 热点题材链路：后端提供 `/api/v1/alphasift/hotspots` 与 `/api/v1/alphasift/hotspots/{topic}` API，Web 选股页新增热点题材区域并支持发酵路线与概念股查看。

### 改进

- 个股分析新增按当日/市场复用的大盘环境摘要，普通 Pipeline 与 Agent 分析 Prompt 可读取低敏大盘背景；新增默认开启的 `DAILY_MARKET_CONTEXT_ENABLED` 配置，用户仍可显式关闭。
- 个股分析与历史/回测展示新增可选八态 `action` / `action_label` 建议动作字段，保留 `operation_advice` 自由文本和 `decision_type=buy|hold|sell` 统计口径。
- 补充 Web decision-signals typed API wrapper 与契约隔离测试，暂不接入 UI。
- 完善运行时日志上下文，补充 logger name、触发来源、市场统计与实时行情预取链路状态，便于排查调度、API、Bot 和数据源降级路径。
- 持仓管理页新增持仓账户删除入口，复用现有账户软删除接口，误建账户会从默认列表、快照、风险、录入入口和事件列表隐藏且不物理清理历史流水。
- AlphaSift 依赖锁定更新到 `d038c52c468543726fc1fd830b53c27d3f09d6da`，并为新版 last-good snapshot、日线历史、行业/概念 provider cache、hotspot 榜单、题材发酵路线、概念股详情、上次成功热点缓存与 post-analysis 元信息补齐 DSA 运行期和 Web 适配。
- AlphaSift 热点题材读取默认优先使用上次成功缓存，手动刷新才实时拉取并覆盖缓存，实时拉取失败时尽量回退旧缓存。
- AlphaSift 热点题材区域改为默认折叠，展开并选中具体题材后再读取详情；发酵路线改为带时间标记的时间线展示，概念股可点击进入首页并直接启动分析。
- AlphaSift 热点题材数据链路复用同一次东方财富板块异动快照，并从真实涨跌幅、异动次数和高频个股推导趋势分、持续分、阶段与龙头样本。
- AlphaSift 热点题材刷新在合约层返回少量或缺少关键字段时改用 DSA 东方财富板块异动直连榜单，忽略少于 3 条的本地热点缓存，并补齐板块兜底字段。
- AlphaSift 热点题材卡片改为更紧凑的多列布局，概念股列表改为独立“分析”按钮触发个股分析；详情优先合并东方财富成分股、同花顺解析和板块异动龙头兜底并按日聚合发酵时间线。
- AlphaSift 热点题材详情新增 DSA 侧 30 分钟磁盘缓存，重复点开同一题材时复用发酵时间线与概念股详情；题材事件仅展示 AlphaSift 合约时间线、同花顺摘要、已配置新闻搜索或东财板块异动等真实来源。
- AlphaSift 热点题材消息催化改为摘要展示：配置 LLM 时优先压缩为一句题材催化摘要，未配置或调用失败时回退本地短摘要。
- AlphaSift 热点题材列表新增可选 `include_details` 详情预取，Web 默认随热点列表批量带回 Top 题材发酵路线与概念股并复用前端内存缓存；新闻催化在 LLM 不可用时改为本地事件归纳。
- 改造 `main.py --webui-only` 启动行为：若 FastAPI 监听端口已被占用，启动即 fail-fast 抛出明确错误并退出。

### 修复

- 问股从历史报告进入后的追问会持续携带当前标的，切回或重载已有会话时可从历史消息恢复基础当前标的，并由后端阻断未明确切换时的错误股票工具调用、交易所片段和指标缩写误路由。
- 自选股加入和删除按等价股票代码匹配港股及大小写美股变体，避免 `00700`、`HK00700`、`00700.HK` 或 `aapl`、`AAPL` 被误判为不同标的。
- 收紧建议动作 legacy fallback：否定/回避表达、中文金融上下文、`buy or sell`、多 guard 歧义文本以及英文复合词不再误渲染成 action badge；有结构化 `action` 时回测/历史趋势等入口按界面语言显示 action 标签。
- 股票新闻与多维情报搜索在相关度排序后新增域名无关的准入过滤，剔除下载/安装包/应用评分页及成人/招嫖服务垃圾页，并在同批已有有效标的/行业候选时移除 `score=0` 背景填充项。
- 修复历史报告运行流快照在混合时区事件时间戳下返回 500 的问题。
- 修复运行流 live SSE 事件未复用快照层递归脱敏规则的问题，避免本地路径、prompt/raw response、代理头等敏感诊断字段在 refetch 前短暂暴露。
- AlphaSift 热点题材默认加载在无缓存且旧适配层缺少 `alphasift.hotspot` 模块时返回空态，不再一打开选股页就显示 AlphaSift 未就绪；手动刷新仍会提示依赖需更新。
- 为 THS 发酵路线补充列名兜底：当 `stock_board_concept_summary_ths` 返回缺列时仅跳过该来源富化，不影响热点题材详情 API 返回。
- 桌面发布打包改用冻结可执行文件运行时探针校验 `alphasift.dsa_adapter`，避免 macOS PyInstaller 将模块内嵌进可执行文件时被文件系统/zip 扫描误判为缺失。
- AlphaSift 热点题材详情展示改为优先使用后端融合后的 `route`，避免旧 `timeline` 覆盖新闻/LLM 摘要；手动刷新热点榜单时会同步绕过同题材详情缓存。

### 文档

- README 与繁中 README 快速开始入口补充视频教程链接，并将桌面客户端入口文案调整为客户端配置教程。
- 补充 `docs/alphasift-integration.md`：明确 AlphaSift 锁定 commit 来源、Hotspot 契约边界、LLM/LiteLLM 兼容语义与关闭开关下回退路径。
- 补充 #1381 运行时范围、兼容边界、官方语义依据与常规发布回滚说明。

### 测试

- 覆盖 #1381 后端 runtime 与兼容核验：`tests/test_main_schedule_mode.py`、`tests/test_pipeline_daily_market_context.py`、`tests/test_daily_market_context.py`、`tests/test_daily_market_context_guardrail.py`、`tests/test_agent_executor.py`、`tests/test_config_env_compat.py`、`tests/test_config_registry.py` 与 `apps/dsa-web/tests/system_config_i18n.test.ts`。
- 新增/更新 AlphaSift 后端回归：`python -m pytest tests/test_alphasift_api.py -q`、`python -m pytest tests/test_docker_entrypoint.py -q`、`python -m pytest tests/test_main_schedule_mode.py -q -k "start_api_server_fails_before_thread_when_port_is_busy"`。

## [3.21.0] - 2026-06-07

### 发布亮点

- feat: 新增 Web UI 中英文界面语言切换和飞书 App Bot 通知模式，提升多人部署和企业通知场景体验。
- feat: 大盘复盘报告、历史入口和个股栏继续收口到结构化数据与统一 Markdown/GFM 渲染，Web/API 人工触发入口不再被交易日 gate 短路。
- feat: AlphaSift 选股链路改为可恢复后台任务，并完善 DSA LLM runtime bridge、默认适配层预置和兼容回归。
- fix: 修复英文界面残留中文、诊断展示、运行时环境变量展示、健康检查、桌面更新路径、工作流变量读取和多处 Web 窄布局问题。

### 新功能

- WebUI 新增独立界面语言状态与中英文切换入口，覆盖主导航、首页、登录、设置页和通用控件文案；UI 语言与 `report_language` 解耦，不改写报告语言链路。
- 飞书通知新增应用机器人（App Bot）模式，支持通过 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_CHAT_ID` 配置，无需额外创建自定义机器人。
- Web 大盘复盘报告新增专用展示视图，历史入口和首页即时结果统一使用 Markdown/GFM 渲染并隐藏个股专属模块。
- 大盘复盘新增结构化 `market_review_payload`，Web、历史详情和推送统一基于结构化数据渲染，并保留 Markdown 兼容展示。
- 新增默认关闭的 AlphaSift 选股页签，通过 `ALPHASIFT_ENABLED` 明确控制，并保留 `/install` 作为显式修复路径。

### 改进

- Web/API 大盘复盘人工触发入口不再因交易日检查或相关市场休市而短路跳过；定时任务、GitHub Actions 手动运行和 CLI 默认入口仍保持原交易日 gate。
- AlphaSift Web 选股改为后台任务提交与状态轮询，新增可恢复任务状态展示，避免外部快照、行情或 LLM 变慢时浏览器长请求超时。
- AlphaSift 选股 API 与服务层收敛到 `AlphaSiftService`，endpoint 仅做路由参数接收与错误映射。
- AlphaSift 与 DSA 的运行时 LLM 兼容桥接改为调用期注入，保留 `provider/model/base_url/custom headers/fallback` 语义链路，不做持久化迁移。
- Web 首页侧栏不再单独展示大盘复盘历史集合，最新大盘复盘作为 `MARKET` 并入个股栏，按最近分析时间参与排序，并复用个股栏的选择、删除、完整报告与历史趋势查看能力。
- 多股通知报告将市场阶段收敛为总览下方单行 `市场状态`，不再在每只股票摘要下重复展示数据质量和限制详情。
- API 错误响应构造收敛到共享 helper，保持既有错误 envelope 形状并降低 endpoint 重复代码。
- WebUI 绑定公网地址或 CORS 全开放且未启用管理员认证时新增运行时 warning；仅增加可观测性，不阻断启动、不改写配置。
- 数据库初始化新增 `schema_migrations` baseline 标记表与幂等记录，用于后续 schema 演进追踪；不迁移、不清理、不改写既有业务表数据。
- #1386 P6 复用市场阶段与 AnalysisContextPack 公开摘要联动告警、持仓手动分析、历史、回测和通知展示，不新增数据库迁移。

### 修复

- Web 英文界面补齐回测、组合风险与告警规则相关文案本地化，避免英文模式下残留中文筛选器、按钮和枚举标签。
- 综合情报搜索中的机构分析与业绩预期维度改用 180 天 provider 请求窗口，避免默认短新闻窗口漏掉财报、研报等周期性财经材料。
- Web 个股栏和历史卡片在窄布局下不再让市场阶段标签遮挡股票名称。
- 问股自由文本追问不再将 TTM、PE、YOY 等金融缩写误识别为新股票代码。
- [修复] GitHub Actions 每日分析工作流读取 SearXNG 自建实例地址时支持 Variables 优先、Secrets 回退，修复仅配置 Variables 时 URL 不生效的问题。
- Web/桌面端左侧导航选中态改用 border 实现，避免蓝色竖条指示器溢出侧栏边界；侧栏展开宽度 116px -> 136px，新增 rail 紧凑模式。
- Windows 桌面端自动更新安装目录不再预先加引号，避免带空格路径在自动安装时触发“缺少快捷方式 / 找不到 Daily Stock Analysis.exe”的系统弹窗。
- Agent 分析路径生成 AnalysisContextPack overview 前复用已落库日线分析上下文，避免日线已抓取成功仍显示 `daily_bars_missing`。
- 修正大盘复盘结构化 `breadth` 的可用性判断：当市场不支持或抓取失败时不下发 `breadth`，前端展示“暂无数据”，避免误导性 0 值。
- 大盘复盘语言行为遵循全局 `report_language`，并在美股中文场景下本地化市场标签与策略蓝图，避免混入英文策略段落。
- Docker Web 设置页读取配置时在活跃 `.env` 文件缺项时回退展示启动注入的同名环境变量，并补清相关挂载边界文档。
- 报告页运行诊断会区分数据源抓取成功与进入 LLM 分析输入，相关新闻区标注为报告页补充/后续检索资讯，避免与输入数据块状态互相误读。
- `/health` 根路径健康检查现在始终返回 JSON，避免静态 Web fallback 吞掉健康探针；`/api/health` 与 `/api/v1/health` 继续保持兼容。
- `ALPHASIFT_ENABLED` 关闭时不触发 `alphasift` 运行时注入；开启后优先复用已配置的 DSA/provider 配置并注入 `LITELLM_*` 与 `LLM_*` 运行时变量。
- 补齐 openai-compatible 场景下 base URL、`extra_headers` 与 `LITELLM_FALLBACK_MODELS` 的兼容路径与回退链验证。
- 桌面/镜像打包链路保持与运行时一致的 AlphaSift 适配层预置，避免 `pip install` 作为线上修复依赖。

### 文档

- 明确 Issue #777 UI 语言切换采用仓内 `UiLanguageContext` + `uiText` 实现，持久化 key 为 `dsa.uiLanguage`，并补充对应可视化验收指引。
- 明确大盘复盘展示链路、结构化 payload、语言行为、交易日 gate 差异和回滚边界。
- 补充 LLM / LiteLLM 兼容键在 Settings 展示与校验上下文中的回退边界，说明不改写、不迁移、不清理用户现有 provider/model/base URL 持久化配置。
- 补齐 #1602 运行诊断口径修复覆盖范围，说明仅统一输入与展示口径，回滚方式为常规发布回滚。
- 明确 AnalysisContextPack P6 文档、迁移与回滚边界，并同步既有 `SAVE_CONTEXT_SNAPSHOT` 到 `.env.example`、配置注册表、Web 设置帮助和完整指南。
- 补齐 #1386 P7 盘前/盘中/盘后分析的入口、迁移、回滚和用户可见说明。
- 为 AlphaSift runtime bridge 增加官方兼容依据落点，明确 provider/model/base_url/extra_headers/fallback 与回退边界。

### 测试

- Web 方向执行 `npm run lint`、`npm run build`、相关 Vitest 和 smoke 命令；未设置 `DSA_WEB_SMOKE_PASSWORD` 时 smoke 用例按设计 skip。
- Web 测试运行时声明 Node `>=20.19.0 <27` 与 npm `>=10`，并补 localStorage 测试兜底以稳定 Vitest。
- 增补 AlphaSift runtime bridge 与打包脚本静态验证，覆盖 `LLM_CHANNELS`、`LITELLM_FALLBACK_MODELS`、`alphasift.dsa_adapter`、`--collect-all alphasift`。

### chore

- 移除随 issue / PR 验收流程误入库的截图资产，并明确一次性截图证据应保留在 PR 描述、评论、附件或 artifact 中，不作为仓库文件合入。

## [3.20.0] - 2026-06-03

### 发布亮点

- feat: 新增 AlphaSift 选股入口、自动安装与稳定适配层，支持 Web 策略执行、LLM 重排展示和默认关闭的可控启用。
- feat: 完善个股历史、自选队列、市场阶段与 AnalysisContextPack 可见性，增强 Web 报告和 API 的结构化上下文能力。
- feat: MiniMax 默认模型升级到 `MiniMax-M3`，并补齐相关价格、预设和测试覆盖。
- fix: 修复健康检查、Windows 桌面更新与首次运行编码、ETF 日线 secid、LLM base_url 校验和 Agent 日线上下文误判等稳定性问题。

### 新功能

- 新增默认关闭的 AlphaSift 选股页签，通过 `ALPHASIFT_ENABLED` 开启后经由稳定适配层读取策略并执行选股。
- Web 首页左侧栏改为个股栏，按股票去重展示，大盘复盘置顶，点击个股加载最新报告，支持按代码变体（.SZ/.SH/.SS）归一化去重合并。保留全选、批量删除和删除确认入口；新增按股票代码批量删除 API `DELETE /api/v1/history/by-code/{stock_code}`。
- 报告详情右侧栏新增自选操作入口，支持查看当前股票是否在自选队列、一键加入或移除；大盘复盘报告不显示该操作。
- 问股页面输入区上方新增自选操作按钮，用户发送包含股票代码的消息后自动显示加入自选/从自选删除入口。
- Web 报告页新增同股历史趋势抽屉入口，历史列表摘要补充趋势、摘要、模型和分析时行情字段，支持按当前股票查看历史分析并加载更多。
- AnalysisContextPack P4 低敏 overview 接入历史详情、同步分析响应、completed 任务状态和 Web 报告页，展示数据块状态、来源、缺失原因与降级摘要。
- #1386 P5 为个股分析报告新增 `dashboard.phase_decision` 盘中决策护栏，并在保存历史前按市场阶段与数据质量限制高置信盘中买卖结论。
- #1386 P4a 新增 `analysis_phase=auto|premarket|intraday|postmarket` API 参数，并在异步任务 accepted、内存 status、list、SSE 与分析 pipeline 中透传请求阶段。
- #1386 P4b Web 报告页新增最终市场阶段标签，任务面板展示请求阶段，并复用 AnalysisContextPack 低敏数据质量摘要。
- MiniMax 渠道模型列表升级：新增 `MiniMax-M3` 并作为默认，按官方 OpenAI-compatible 文档支持 1M 输入上下文（项目保守注册为 `<=512K` 价格档：context_window 512K、`max_tokens` 128K，对应 $0.6/M 输入、$2.4/M 输出，>512K 输入价格档未建模），保留 `MiniMax-M2.7` 与 `MiniMax-M2.7-highspeed`，并保留 `MiniMax-M2.5` legacy 价格条目以兼容现有用户配置的成本估算。Web 设置页 MiniMax 预设模型与价格按 M3 刷新。
- 新增 AnalysisContextPack P1 内部契约与脱敏序列化测试。
- 市场阶段低敏摘要接入历史详情、同步分析响应和 completed 任务状态的 report metadata。

### 改进

- 首次运行配置校验补充缺失 AI Key、空 STOCK_LIST、Telegram/邮件成对字段和 Webhook URL 前缀诊断。
- AlphaSift 选股入口在 Web 侧边栏中移动到“问股”下方，贴近 Agent/研究辅助工作流。
- Docker 镜像构建阶段预置默认 AlphaSift 适配层，与桌面发布包一样避免运行期额外安装。
- AlphaSift 选股改为依赖 `alphasift.dsa_adapter` 的稳定接口，Web 策略列表由 AlphaSift 动态提供，不再在前端硬编码。
- AlphaSift 选股页补充 Run ID、快照数、过滤后数量、因子和风险详情，展开候选时展示真实明细，并暂时仅开放当前支持的 A 股市场。
- Web 设置页新增 AlphaSift 选股开关卡片，可直接开启或关闭选股页签。
- 开启 AlphaSift 选股时先切换 `ALPHASIFT_ENABLED` 并检查适配层可用性，缺失时自动调用受控安装接口，不再要求用户额外点击安装。
- AlphaSift 已开启但适配层缺失时，策略列表和选股接口会串行化自动安装锁定来源，并强制重装以覆盖旧版 `alphasift` 包。
- AlphaSift 选股页合并重复的快照源 fallback 提示，并保留 AlphaSift 自身的 Tushare 优先快照源逻辑。
- AlphaSift 选股页在 LLM 重排降级时展示 warning/source error/parse error，并避免把本地因子评分误显示为 LLM 判断。
- Web 设置页不再把 `ALPHASIFT_ENABLED` 作为普通数据源配置项重复展示，该值仅作为“开启选股”按钮背后的持久化状态。
- AlphaSift 关闭时隐藏 Web 左侧“选股”导航入口，避免误导未开启用户。
- 补充 AlphaSift 选股自定义策略显示逻辑，避免未匹配预设项时误显示“均衡多因子”。
- 新增 GET /api/v1/history/stocks 端点按 code 分组返回不重复个股列表；新增 GET /api/v1/stocks/watchlist、POST /api/v1/stocks/watchlist/add、POST /api/v1/stocks/watchlist/remove 端点支持自选队列增删查。STOCK_LIST 读写保持原样，不做自动归一化；add/remove 时归一化比较判断等价代码变体。
- 新增 useWatchlist hook 统一管理自选队列前端状态，复用 SystemConfigService 的 STOCK_LIST 配置项实现持久化。
- AnalysisContextPack P5 增加数据质量评分、`fetch_failed` 状态、Prompt 数据限制区块和 Web 低敏质量展示。
- #1386 P2-full 在 AnalysisContextPack Prompt 数据限制中追加市场阶段与降级数据的交叉约束，并修正中文分析 Prompt 的阶段化行情标签。
- 通知报告默认发送路径恢复既有渠道兼容转换与分片逻辑，新增 renderer 能力仅保留为未来扩展基础。
- 关联板块缺少类型数据时改为单行展示板块名称，避免生成整列 `N/A` 的板块表格。
- 优化 Web 报告详情页信息层级，将输入数据块和运行诊断下移为主体内容后的折叠辅助信息。
- 盘中分析补齐实时行情获取时间、provider 时间、stale、fallback 与 partial/estimated 标记，供 AnalysisContextPack 映射输入数据限制。

### 修复

- Agent 分析路径生成 AnalysisContextPack overview 前复用已落库日线分析上下文，避免日线已抓取成功仍显示 `daily_bars_missing`。
- 注册 /api/v1/health 路由并加入认证豁免，修复该路径返回 404 以及开启 ADMIN_AUTH_ENABLED 后健康探针收到 401 的问题。
- Windows 本地首次运行环境检查兼容非 UTF-8 控制台输出，并将 `requirements.txt` 注释改为 ASCII 以降低默认代码页下的依赖安装失败概率。
- AlphaSift DSA 适配层默认开启 LLM 重排，后端显式请求 `use_llm=True`，选股页展示 LLM 分数、判断、覆盖率和关注项。
- AlphaSift 嵌入 DSA 时复用 DSA 已解析的 LLM 模型、渠道和密钥配置，避免 Web 已配置 LLM 但选股 LLM 重排仍因缺少 provider key 降级。
- AlphaSift 选股复用 DSA LLM 路由时过滤未声明的托管 provider 备选模型，并把已声明渠道模型补入回退链，避免残留 Gemini fallback 覆盖可用的 DSA 渠道。
- AlphaSift 默认安装来源改为锁定 commit 的受信任 GitHub 地址；桌面模式自动安装不要求管理员会话，非桌面部署要求管理员认证会话，并继续限制安装来源。
- 修复 Web 开启 AlphaSift 时先安装后写配置导致默认关闭状态无法开启的问题。
- AlphaSift 状态与安装接口不再返回 `install_spec` 明文，仅返回 `install_spec_is_default` 等非敏感状态字段。
- AlphaSift 状态探测区分可选依赖缺失与非预期异常，异常场景记录 warning 并返回非敏感诊断信息。
- 调整 AlphaSift 筛选调用兼容：`screen` 以 `max_results` 为主并支持历史 `max_output` 关键词，同时允许策略透传以对齐前端手动策略参数。
- AlphaSift Web 选股请求使用独立长超时，避免开启 LLM 重排后被通用 30 秒 API 超时提前中断。
- 桌面端打包阶段预置 AlphaSift 并收集适配层，避免发布包运行时再要求管理员自动安装。
- AlphaSift 自动安装仅在 `status` 诊断为 `missing_module` 时触发（仅模块缺失场景）；适配层可导入但运行时异常不再自动 `pip install`，而是返回 `424` 并保留诊断，避免把真实运行时故障掩盖为重装。
- 收口 Web 中文界面残留英文文案与设置页 help 缺口，回测页改为中文展示，并让 Web 设置页仅展示已注册且带说明的配置项。
- Windows 桌面端自动更新静默安装时显式复用当前安装目录，避免自定义安装目录场景下卸载旧版本文件失败。
- Windows 安装器重试旧卸载器时对 `_?=` 安装目录参数加引号，修复旧版本安装在带空格路径时返回 2 导致自动更新失败。
- Windows 桌面端自动更新传给 NSIS 的 `/D=` 目录参数在包含空格时自动加引号，避免安装位置注册表被截断。
- 加固 LLM channel base_url 校验，避免解析差异导致 SSRF 绕过。
- 修正 efinance ETF 日线 Eastmoney secid 路由，避免沪市 ETF 被按深市 quote id 查询导致日线为空。

### 文档

- 明确 AlphaSift 与 LiteLLM 兼容边界：仅桥接 DSA 已声明 provider/model/base URL 为调用期注入，不对 `.env` 做 provider/model 路由迁移；回退方式为关闭 AlphaSift 并恢复原有 `LITELLM_*`/`LLM_*` 配置。
- 明确 AlphaSift 仅复用 DSA 现有 LLM/LiteLLM 配置语义，不新增 `LITELLM_MODEL`、`OPENAI_MODEL`、`OPENAI_BASE_URL`、`LLM_TIMEOUT_SEC` 等模型语义迁移；失败提示与回退路径统一沿用既有系统配置链路，仅影响 AlphaSift 选股能力本身。
- 明确 AlphaSift 自动安装来源锁定、`missing_module` 与运行时异常行为边界，以及 LLM/provider/base URL 与自定义通道回退路径，便于问题溯源与回滚到原有 LLM 配置。
- 明确同股历史趋势新增模型字段为历史快照展示元数据，不影响运行时 LLM Provider/Model/Base URL 路由与配置迁移清理；回退方式为按常规发布回滚本变更。
- 明确 #1311 的兼容性边界：渲染层仅消费分析结果 `model_used` 展示字段，未改动 `wechat/slack/feishu/telegram` sender 发送链路，不触发 provider/model/base_url 兼容迁移。
- 明确 AlphaSift 锁定 commit 的 `alphasift.dsa_adapter` 契约依据，以及当前 DSA API/Web 调用结构的兼容边界。
- 明确 Settings 页面对 LLM 配置仅做展示分组与字段归并，不改写或触发 LLM 迁移/回退路径；兼容现有 `LLM` 配置保存与回退语义。
- 新增 AnalysisContextPack P0 上下文盘点。
- 补齐告警中心 P8 文档与配置收口说明，明确 legacy JSON、高级规则、Web/API、Docker、GitHub Actions 与 Desktop 边界。

### 测试

- 同步更新 `llmProviderTemplates`、LiteLLM fallback pricing 与 MiniMax 预设相关单测，断言新默认模型。
- 补充 ETF 日线数据源路由、输入变体、fallback 与 MA 字段回归覆盖。

### chore

- 新增通知报告渠道能力画像、PreparedMessage 与结构感知 Markdown 分片基础设施，为 #1311 全渠道渲染适配打底。
- 预置企业微信、飞书、Telegram、钉钉、Slack 平台 renderer 元数据，暂不改变默认推送报告入口和可见版式。

## [3.19.0] - 2026-05-29

### 新功能

- 落地 #1391 Phase 1 运行诊断最小链路：任务/SSE 追加 trace_id，并记录日线与实时行情 ProviderRun 快照。
- 告警中心新增 P7 大盘红绿灯结构化规则，支持 `market_light_status` 与 `market_light_score_drop` 并复用现有 worker、触发历史、通知和冷却链路。
- 落地 #1391 Phase 2 运行诊断摘要：生成用户可读 RunDiagnosticSummary，提供历史报告诊断 API 与脱敏复制文本。
- 落地 #1391 Phase 3 运行诊断可见性：报告详情和任务面板默认折叠展示运行状态、trace 与可复制排障信息；后端通过 `api/v1/history/{record_id}/diagnostics` 与 `context_snapshot.diagnostics` 提供历史链路回填。
- 新增 AnalysisContextPack P1 内部契约与脱敏序列化测试。
- 新增 AnalysisContextPack P2 builder，从普通分析 pipeline 已有 artifacts 组装内部上下文包。
- 问股新增默认关闭的可见对话上下文压缩，支持 Web 开关、Agent 高级 preset、滚动摘要和最近轮次原文保护，降低长会话 token 消耗。
- 股票自动补全索引默认支持从 GitHub main 远程刷新并缓存到本地，Web/CLI 分析入口失败时自动降级到内置索引，降低摘帽和更名后旧简称污染分析的概率。
- 普通分析与 Agent 运行时 Prompt 接入 AnalysisContextPack 低敏摘要，保持 history/API/Web 输出兼容。

### 改进

- `scripts/fetch_tushare_stock_list.py` 可对 A 股中带 `XD`/`XR`/`DR`/`N`/`C` 前缀的名称进行回填修正，供自动补全刷新流程默认使用。
- Web 路由页面改为按需加载，降低首包体积并增加路由加载失败恢复提示。
- Web 完整报告 Markdown 抽屉改为按需加载。
- 新增市场阶段推断基线并明确盘前、盘中、午休、临近收盘、盘后和非交易日语义。
- 新增运行态市场阶段上下文构造与降级测试。
- 设置页配置帮助阶段性补齐 Web 设置页实际展示/可配置字段的中英双语文案，覆盖 Agent、回测、报告、通知路由、系统运行时、AI legacy、数据源和通知高级配置。
- P2-min：LLM Prompt 注入市场阶段上下文。

### 修复

- 股票自动补全索引生成缺少 `pypinyin` 时改为直接失败，避免写出缺失拼音字段的降级索引。
- 归一腾讯实时行情成交量为股口径，避免量能变化倍数被放大并误导分析报告。
- Docker 默认部署移除 `.env` 单文件挂载，避免 WebUI 保存配置时因 `os.replace` 更新挂载点触发 `Device or resource busy`。
- 收敛 #1391 Phase 0 A 股代码归属边界：补齐 `SH`/`SZ` 前缀场景的归属一致性，明确 `data_provider/baostock_fetcher.py`、`data_provider/pytdx_fetcher.py`、`data_provider/tushare_fetcher.py` 的本轮修复范围。
- 修复 `STOCK_LIST` 使用裸 A 股代码时 Baostock 等数据源 fallback 的内部格式转换，保持用户配置继续使用 6 位股票编号。
- Windows 桌面端自动更新在用户确认重启安装后改为静默执行安装器，并在停止内置后端后清理进程引用，降低安装器提示“每日股票分析无法关闭”的概率。
- macOS 桌面端将运行时配置迁移到用户数据目录，并在旧 `.app` 包内文件仍可访问时迁移 `.env`、数据库和日志，避免后续替换升级后重新配置。
- 恢复 Agent/历史兼容快照中的关联板块与板块联动字段提取，修复新版首页报告缺少“板块联动”的回归问题。
- 修正 Web 设置帮助中 legacy 告警 JSON 字段名与静默时段投递语义说明。
- 修复 Web 中文设置页在数据源、通知、系统与 Agent 区域的配置标题、说明和关键下拉选项漏翻问题。
- 修复问股会话切换和首页任务重连后可能残留 Agent/分析任务进行中状态的问题。
- 问股 single-agent 新增 provider-aware trace 分轨，跨轮保留 DeepSeek V4 thinking + tool-call 的 `reasoning_content` 与工具协议材料。
- 为 Akshare 新浪/腾讯 A 股历史兜底接口增加调用级超时，并补齐 Tushare `605xxx` 沪市代码路由回归测试，避免定时分析因数据源无响应而挂起。
- 将 `exchange-calendars` 依赖下限提升到 `4.13.0`，避免 pandas 3 环境导入交易日历时因 Timedelta 单位 `T` 失效导致分析失败。
- 交互式命令（钉钉会话、飞书会话、Telegram）触发的分析结果只回到来源会话，不再同时广播到静态通知渠道。
- 适配 Longbridge OAuth 2.0 认证与 token 缓存恢复，避免新后台无 Legacy Access Token 时长桥数据源被误判为未配置。
- Longbridge OAuth 路径在当前 SDK 不支持 `OAuthBuilder` / `Config.from_oauth` 时明确日志降级，避免 Linux/Docker 仅可安装旧 SDK 时构建失败。
- 兼容 YFinance 日线返回未命名日期索引的场景，避免标准化后缺少 `date` 列导致美股日线 fallback 中断。

### 文档

- 新增 #1391 Phase 0 运行诊断契约文档，明确 trace_id、诊断摘要、关键链路范围与脱敏/fail-open/retention 边界。
- 补齐告警中心 P8 文档与配置收口说明，明确 legacy JSON、高级规则、Web/API、Docker、GitHub Actions 与 Desktop 边界。
- 说明本次桌面修复仅覆盖 Windows NSIS 更新安装链路与后端进程生命周期清理；未改动设置项保存/模型运行时清理语义。移除此前误入的 `docker/Dockerfile` `npm registry` 变更，恢复部署构建与更新修复的职责隔离。
- 新增 AnalysisContextPack P0 上下文盘点，明确字段质量状态、现有状态映射和首版 pack 边界。
- 明确 #1391 Phase 2 的结构化检测告警为非配置迁移信号：`agent_max_steps`/`agent_orchestrator_timeout_s` 非法值会 fallback 至默认并产生日志告警，新增诊断链路仅新增 `context_snapshot`/`RunDiagnosticSummary` 读写字段，不改写 `litellm_model`、`agent_litellm_model`、`openai_base_url`、LLM channel 路由或配置迁移语义。
- 补充 #1391 Phase 3 兼容性说明：记录后端诊断持久化、历史查询与通知回写链路变更边界与回滚策略，并补齐后端门禁级验证要求。

### 测试

- 收敛 #1391 Phase 3 后端/API 与 Web 回归检查：`./scripts/ci_gate.sh`、`test_pipeline_market_phase_context.py`、`test_analysis_api_contract.py`、`test_analysis_history.py`、`npm run lint`、`npm run build`。
- 执行 `python -c "import exchange_calendars as xcals; xcals.get_calendar('XSHG'); print('ok')"` 通过验证，以覆盖导入与交易日历初始化兼容性。

## [3.18.0] - 2026-05-21

### 发布亮点

- feat: 告警中心扩展到 P2-P6，补齐后台评估、真实通知结果、业务冷却、技术指标规则，以及自选股 / 持仓 / 账户联动规则。
- feat: 个股分析支持策略选择，新增热点题材、事件驱动、成长质量和预期重估策略，并为 HK/US 报告补充基本面、财务摘要、股东回报和关联板块。
- feat: 新增 Finnhub / AlphaVantage 美股数据源适配器，扩展美股日线 failover 链，提升美股行情获取韧性。
- fix: 修复桌面端发布打包、分析状态接口、AlphaVantage 涨跌幅、持仓实时估值、告警历史去重、数据库冷启动和 fallback pricing 注册等稳定性问题。

### What's Changed

- feat: Add alert-center P2-P6, Web strategy selection, HK/US fundamental context, static-report financial sections, and Finnhub / AlphaVantage US-market fallback.
- improve: Refine LiteLLM parameter recovery, yfinance currency/dividend handling, RSI calculation, market-review presentation, stock-news relevance ranking, and report table rendering.
- fix: Harden desktop packaging/update assets, completed analysis-status responses, AlphaVantage pct_chg routing, portfolio realtime snapshots, alert trigger dedupe, DatabaseManager cold start, and fallback pricing registration.
- docs/tests: Add beginner setup and settings-help docs, document compatibility/rollback boundaries, and extend regression coverage for API, alert, packaging, and release paths.

## [3.17.1] - 2026-05-16

### 发布亮点

- fix: 桌面端 Windows / macOS 打包脚本显式关闭 electron-builder 自动发布，避免 tag 构建时因缺少 `GH_TOKEN` 在本地打包完成后失败；Release workflow 继续负责上传和发布产物。

### What's Changed

- fix: Add `--publish never` to the Windows and macOS Electron packaging scripts so tag builds only create local artifacts and GitHub Actions handles release upload/publish.

## [3.17.0] - 2026-05-16

### 发布亮点

- feat: 新增 Alert API MVP，支持告警规则 CRUD、启停、一次性测试以及触发/通知结果查询，首版覆盖 `price_cross` / `price_change_percent` / `volume_spike` 并保持 legacy 配置兼容。
- feat: 通知网关新增 ntfy 与 Gotify 一等渠道，并补齐通知降噪、静态渠道隔离、诊断、Web 测试和 GitHub Actions env 对照校验。
- feat: Windows 桌面安装版接入自动更新安装链路，支持后台下载、确认重启安装、运行时文件备份/恢复和发布产物元数据校验。
- improve: 大盘复盘新增概念排行、人气股、涨停池等底层数据源，支持指数涨跌颜色语义配置，并将复盘结果写入历史记录。
- improve: Web 设置页支持 `.env` 配置备份导入/导出和通知/Agent 区域局部错误兜底；报告新增 `REPORT_SHOW_LLM_MODEL` 开关控制模型信息展示。
- improve: Docker 启动入口自动修复挂载目录权限并在日志目录不可写时降级到控制台，减少普通部署的手动修复步骤。
- fix: 数据源缺凭据或连接失败时更温和降级，Longbridge / Pytdx 加入冷却，资金流缺失时避免输出高置信买入结论。
- fix: 分析与报告链路兼容 OpenAI-compatible `content_blocks` 响应，归一策略价格字段，并修复大盘复盘滚动和历史记录丢失问题。
- docs: 补齐通知、告警中心、桌面打包、README / 指南和 PR title 治理说明，明确多处配置兼容边界与回滚路径。
- test: 增加 Alert API、通知降噪/路由、Docker entrypoint、数据源预取、桌面更新链路和分析历史等回归覆盖。

### What's Changed

- feat: Add an Alert API MVP with rule CRUD, enable/disable, one-shot testing, trigger history, notification results, and legacy config compatibility.
- feat: Promote ntfy and Gotify to first-class notification channels with Web tests, routing, Actions integration, diagnostics, and noise control.
- feat: Add the Windows desktop auto-update install flow with runtime state backup/restore and release artifact metadata verification.
- improve: Extend market review data sources, add configurable index color semantics, and persist market review results into analysis history.
- improve: Add Web `.env` backup import/export, local settings panel error boundaries, and a report model visibility toggle.
- improve: Harden Docker startup by repairing mounted directory permissions and falling back to console logging when mounted logs are not writable.
- fix: Cool down unavailable optional fetchers, reduce noisy Longbridge/Pytdx retries, and downgrade buy advice when capital flow data is missing.
- fix: Handle OpenAI-compatible `content_blocks`, normalize strategy price fields, and recover market review scrolling/history behavior.
- docs/tests: Update notification, alert, desktop packaging, README/guide, and governance docs; add focused regression coverage for the new release paths.

## [3.16.0] - 2026-05-10

### 发布亮点

- feat: Web 首页新增“大盘复盘”触发入口、任务轮询与完成后报告直出；首次启动配置状态可提示缺口并引导到系统设置。
- feat: 新增通知路由策略，支持按 report、alert、system_error 将通知收窄到指定渠道；Web 设置页支持通知渠道一键测试。
- feat: 系统设置页新增配置项帮助入口与多语言帮助文案基础设施，首批覆盖自选股、LLM 主模型、LLM 渠道、飞书 Webhook 与 WebUI 监听地址。
- improve: 大盘复盘 API、CLI、Bot 共用 `build_market_review_runtime` 装配路径，补齐 `litellm_model` / `llm_model_list` 与 legacy key 回退说明。
- improve: 个股报告操作建议结合支撑/压力、量能、筹码与主力资金流校准，减少买入/卖出剧烈切换，并补强 Agent 决策兜底。
- improve: Docker 镜像支持非 root 用户运行，LiteLLM 依赖约束放宽到后续安全 1.x 修复版本。
- fix: 修正 LLM 渠道测试中 `Model disabled`、provider blocked 等错误分类，避免被误报为网络异常。
- fix: 港股日线跳过不支持港股的内置历史数据源；北交所 `BJ` 前缀与 `.BJ` 后缀代码校验保持一致。
- fix: Web 大盘复盘按钮可观测性、Windows fallback 锁进程探测和催化线索展示更稳健。
- docs: 新增文档中心与配置帮助维护说明，清理 README、完整指南与配置指南中的临时 PR/文档同步说明。

### What's Changed

- feat: Add a Web home market-review trigger with task polling and inline report display; setup status now points users to missing configuration.
- feat: Add notification routing by report, alert, and system_error; add one-click notification channel testing in Web settings.
- feat: Add settings field help infrastructure with multilingual help text for the first batch of core configuration fields.
- improve: Share `build_market_review_runtime` across API, CLI, and Bot market review paths; document `litellm_model` / `llm_model_list` and legacy key fallback behavior.
- improve: Calibrate stock advice with support/resistance, volume, chips, and main-force capital flow; strengthen Agent decision fallback behavior.
- improve: Run Docker images as a non-root user and relax LiteLLM constraints to allow safe future 1.x fixes.
- fix: Classify `Model disabled`, provider blocked, and related LLM channel test errors more accurately instead of reporting them as generic network failures.
- fix: Avoid unsupported built-in historical providers for Hong Kong daily data; align Beijing Stock Exchange `BJ` prefix and `.BJ` suffix validation.
- fix: Improve Web market-review observability, Windows fallback lock probing, and market catalyst snippet rendering.
- docs: Add the documentation index and settings-help maintenance guide; remove temporary PR/doc-sync notes from README and user-facing guides.

## [3.15.0] - 2026-05-05

### 发布亮点

- LLM 渠道配置体验继续升级：新增 Anspire OpenAI-compatible 网关接入，并补齐常用服务商预设、官方来源、能力标签、配置注意事项和 GitHub Actions 显式映射。
- Web LLM 配置检测更可诊断：细分错误 reason，并支持用户显式触发 JSON、tools、vision、stream 运行时 smoke。
- LLM 运行时配置清理更稳健：只清理托管 provider 的失效运行时选择，并保留 `cohere/*`、`google/*`、`xai/*` 等直连 provider 兼容语义。
- 通知与 Bot 状态可观测性增强：自定义 Webhook 支持 JSON body 模板，Bot `/status` 展示更完整的 LLM、Agent 与通知渠道状态。
- 大盘复盘、实时告警、Agent weak 兜底和持仓估值继续补强，降低默认值覆盖、缺价污染和配置排障成本。

### 新功能

- 支持 `ANSPIRE_API_KEYS` 默认接入 Anspire OpenAI-compatible 大模型网关，并在 LLM 渠道编辑器补充 Anspire Open 预设。
- 自定义 Webhook 支持 `CUSTOM_WEBHOOK_BODY_TEMPLATE` JSON body 模板，便于适配 AstrBot、NapCat 和自建推送服务。
- 大盘复盘结构化区块新增大盘红绿灯结论，基于盘面温度输出 green/yellow/red、核心原因和操作建议。
- EventMonitor 支持 `price_change_percent` 涨跌幅阈值规则，可按上涨或下跌方向触发实时告警。
- Web LLM 渠道编辑器新增常用服务商配置模板与预设，覆盖 MiniMax、火山方舟、OpenAI、Claude、Gemini、Kimi、Qwen、GLM、豆包等入口。

### 改进

- Web LLM 配置检测补充细分错误分类，并新增显式触发的 JSON/tools/vision/stream 运行时 smoke；默认测试和保存流程不变，检测结果仅作为当前配置的一次 best-effort 诊断。
- Bot `/status` 展示统一 LLM 主模型、Agent 模型、渠道模式、YAML 配置和更多通知渠道状态。
- Web LLM 渠道编辑器展示 provider 能力标签、官方来源链接和配置注意事项提示；这些标签仅用于配置参考，不代表运行时能力已验证通过。
- 抽出 Web LLM provider preset 单一模板数据源，保持现有配置保存语义不变。
- 补齐 LLM provider channel 在 GitHub Actions 中的显式映射，并同步 `.env` 示例与配置文档。

### 修复

- Agent weak 完整性兜底在模型缺少评分、趋势、操作建议或 dashboard 关键块时优先保留本地趋势分析结果，并只补齐真正缺失的仪表盘字段，避免首页评分被默认 50 覆盖。
- 统一持仓快照输出现价、市值、浮盈亏、收益率与价格元信息，避免缺价或 stale 价格污染持仓估值。
- LLM 渠道测试补充结构化诊断与设置页排障提示，便于定位 provider、模型、Base URL 和鉴权配置问题。
- 明确 runtime 清理兼容边界：仅对托管 provider（`gemini`、`vertex_ai`、`anthropic`、`openai`、`deepseek`）触发保存前失效值清理，`cohere/*`、`google/*`、`xai/*` 直连值按 legacy 兼容路径保留，不做无提示迁移或覆写。
- 将 MiniMax 预设调整为官方 OpenAI-compatible Base URL 和当前模型示例，并补充 MiniMax、火山方舟、LiteLLM 兼容来源与回退说明。
- 移除截图识别对 Gemini 3 Vision 模型的过时降级逻辑，默认推断改用当前 Gemini 模型配置。

### 文档

- 完善 LLM provider 配置文档，补充配置方式选择、Actions 变量对照、运行时检测边界、错误 reason 排障和回滚路径（#1180）。
- 补充 LLM 渠道编辑器的官方来源、依赖兼容窗口、保存时的运行时模型清理规则，以及旧配置回退路径说明。
- 为 `cohere/*`、`google/*`、`xai/*` 直连语义补充官方 provider/model 说明、`litellm>=1.80.10,<1.82.7` 兼容依据引用，并明确示例模型名仅为配置保留行为说明而非可用性背书。
- 明确 `price_change_percent` 事件告警仅为配置与运行时规则扩展，未变更模型/provider/base URL/LiteLLM 兼容语义；回退路径为关闭/移除 Event Monitor 配置。
- 同步 README、DEPLOY、full-guide、Anspire、AIHubMix 与 SerpAPI 相关说明，统一外链、配置口径和评审一致性说明。

### 测试

- 补齐 AI 配置页与 `task_queue` 的 LLM 运行时清理/同步回归证据：恢复渠道模型时保留 fallback、编辑模型列表期间不静默清空运行时选择，渠道无可用模型时清理失效 runtime 引用，并覆盖 legacy key 与 `cohere/*`、`google/*`、`xai/*` 直连 provider 保留语义。
- 覆盖 Web LLM 配置检测的细分错误分类，以及 JSON、tools、vision、stream 运行时 smoke 的显式触发路径。

## [3.14.2] - 2026-04-30

### 发布亮点

- 大盘复盘扩展到港股，并让 Bot `/market` 与 CLI/调度入口使用一致的交易日过滤语义。
- 问股与 Agent 链路增强配置缺失、决策 fallback 和多策略选择体验。
- LLM 与分析报告链路提升稳定性：非法 JSON 响应会继续尝试备用模型，LiteLLM DEBUG 日志默认降噪。
- 新增只读首次启动配置状态接口，为后续配置向导和 smoke run 奠定基础。

### 新功能

- 大盘复盘支持港股市场：`MARKET_REVIEW_REGION` 新增 `hk` 选项；`both` 扩展为 A股+港股+美股，并新增港股指数（HSI/HSTECH/HSCEI）复盘链路。
- 新增只读首次启动配置状态接口 `GET /api/v1/system/config/setup/status`，用于识别 LLM、Agent、自选股、通知和本地存储配置缺口；该接口不会重载运行时、写入 `.env` 或创建数据库文件。

### 改进

- 问股页面支持组合选择多个 Agent 策略。

### 修复

- Bot `/market` 命令复用 `get_open_markets_today()` / `compute_effective_region()` 做交易日过滤：结果作为 `override_region` 透传给 `run_market_review`；若结果为空字符串则跳过复盘并推送“今日相关市场休市”，与 CLI/调度入口行为一致。
- 问股 Agent 在未配置可用 LLM 时保留后端真实错误原因并维持 `done.success=false` 失败语义，避免前端把配置缺失误当成成功回答。
- Agent 模式未生成有效决策仪表盘时保留本地趋势分析的评分、趋势和操作建议，并将强买/强卖 fallback 归一到兼容的 `buy`/`sell` 决策类型，避免首页结果被 `50 / 观望 / 未知` 缺省值覆盖。
- 持仓快照现价缺失时不再静默回退为持仓成本；当天快照优先使用历史收盘价，仅在缺失时使用实时价 fallback，缺价持仓不再污染市值与未实现盈亏汇总，并为持仓明细返回价格来源、日期、stale 与缺价状态。
- 分析 Prompt 在注入 `trend_analysis` 前按最终 `trend_status` / `ma_alignment` 清洗互斥理由：空头结构移除看多理由、多头结构移除空头结构风险，并在事件/技术冲突与异常放量（>10 倍）时强制提示“事件先行、技术待确认”与量能降权。
- LLM 返回非 JSON 响应时同样触发备用模型切换：主模型成功返回但无法解析 JSON 时，不再立即降级为纯文本 fallback，而是依次尝试 `LITELLM_FALLBACK_MODELS` 中的备用模型；所有模型均无法返回合法 JSON 时，再降级为文本 fallback。
- LiteLLM 内部 DEBUG 日志默认压低到 WARNING，避免流式生成时 token 级日志污染 `stock_analysis_debug_*.log`；如需排查 LiteLLM 内部细节，可临时设置 `LITELLM_LOG_LEVEL=DEBUG`（Fixes #1156）。

### 文档

- 补充 LLM 配置指南与 FAQ，明确问股 Agent 对 `LITELLM_CONFIG` / `LLM_CHANNELS` / legacy `GEMINI_*` `OPENAI_*` `ANTHROPIC_*` 的兼容优先级、回退路径与“不静默迁移旧配置”的结论。

### 测试

- 新增 `tests/test_bot_market_command.py`，覆盖 `MARKET_REVIEW_REGION=both` + open markets `{"cn","us"}` / `{"cn","hk"}` 的 `override_region` 透传断言，并覆盖全市场休市跳过与关闭交易日检查路径；新增 `tests/test_yfinance_hk_indices.py` 覆盖港股指数符号映射与部分/全部失败降级路径。
- 补齐 `task_queue` 轻量导入 stub 的股票代码规范化函数，恢复 `tests/test_task_queue_config_sync.py` 收集与运行。

## [3.14.1] - 2026-04-26
- [测试] 修正大盘复盘 prompt 测试对“明日交易计划”标题的断言，并同步桌面端版本号，恢复发布 gate。

## [3.14.0] - 2026-04-26

### 发布亮点

- 📊 **大盘复盘升级为盘后工作台式结构** — A 股复盘固定输出盘面温度、指数明细、板块 Top 表、新闻催化、明日交易计划和风险提示，减少纯文字复盘的重复与空泛。
- 🖥️ **桌面端新增 GitHub Release 更新提醒** — Windows/macOS 桌面端启动后自动检测新版本，也可从设置页手动检查并跳转下载页。
- 🤖 **Pipeline Agent 数据加载大幅降噪** — K 线工具改为 DB-first 并预热 240 天历史数据，避免同一只股票重复 HTTP 请求。
- 🐳 **Docker 发布链路整理** — 发布工作流收敛为正式发布与手动补发两条路径，官方 Docker Hub 镜像名统一为 `zhulinsen/daily_stock_analysis`。
- 🔧 **LLM 渠道与 DeepSeek V4 配置补强** — GitHub Actions 定时分析补齐多渠道变量透传，DeepSeek 官方渠道预设与示例同步到 V4。
- 🧩 **桌面端静态资源一致性校验** — 打包链路和运行时都能更早发现静态资源错配，降低 Release 包白屏排查成本。

### 新功能

- 🏠 **Web 首页历史报告区新增重新分析入口** — 支持基于原始 prompt 重做同一只股票同日期的分析。
- 🖥️ **Windows/macOS 桌面端新增 GitHub Release 更新提醒** — 启动后自动检测新版本，并支持从设置页手动检查后跳转下载页。

### 改进

- 📊 **A 股大盘复盘报告改为结构化盘后工作台版式** — 固定输出盘面温度、指数明细、板块 Top 表、新闻催化和明日交易计划。
- 🐳 **Docker 发布工作流收敛** — 更清晰地区分正式发布与手动补发链路，并统一官方 Docker Hub 镜像名为 `zhulinsen/daily_stock_analysis`。
- 🤖 **Agent 日线工具优先复用本地缓存** — 同时持久化新获取的日线与新闻情报，减少重复数据源调用。

### 修复

- 🤖 **Pipeline Agent K 线工具 DB-first 加载** — `get_daily_history` / `analyze_trend` / `calculate_ma` / `get_volume_analysis` / `analyze_pattern` 改为优先读取本地 DB，消除同一只股票 9x5=45 次重复 HTTP 请求（Fixes #1066）。
- 🤖 **Pipeline Agent 执行前按需预热 240 天 K 线历史到 DB** — 正常情况下 K 线工具调用无需重复网络请求。
- 🕒 **冻结 `target_date` 并通过 ContextVar 透传到 Pipeline Agent K 线工具线程** — 消除跨收盘边界时间漂移。
- 🪟 **Windows 桌面端后端日志转抄编码修复** — 转抄 stdout/stderr 时优先使用 UTF-8，并兼容本地代码页回退，避免中文日志乱码。
- ⚙️ **GitHub Actions 每日分析工作流补齐 LLM 渠道变量透传** — 支持 `LLM_CHANNELS`、多 Key 与常用 `LLM_<NAME>_*`，避免本地可用的多模型配置在云端定时任务中失效（Fixes #1063, #872）。
- 📈 **历史报告详情接口修正 `change_pct` 取值** — 使用 `is None` 判断避免把 0.0（平盘）当作缺失值丢弃，移除错误的 `change_60d` 兜底，并在缺失时回退到原始实时行情字段（Fixes #1084）。
- 🔧 **DeepSeek 官方渠道预设与示例配置同步到 V4** — 保留 legacy `deepseek-chat` 默认值并增加废弃提示，同时修正模型发现后旧运行时选择导致保存失败的问题（Fixes #1108, #1109）。
- 🧩 **桌面端打包链路新增静态资源一致性检查** — `scripts/check_static_assets.py` 会在源 `static/` 与 PyInstaller 产物中校验 `index.html` 引用的资源是否真实存在，运行时也会在错配时写入明确日志，避免重现 Release 包打开后白屏（Refs #1064 / #1065 / #1050）。
- 🧩 **后端 `/assets/*` 改为显式路由托管** — 资源缺失时返回与请求扩展名匹配的 `text/javascript` / `text/css` 404，减少默认 JSON 错误响应带来的排查误导（Refs #1064）。
- 🌙 **`kimi-k2.6` 自动使用固定温度** — 主分析、大盘复盘和 Agent 调用该模型时自动使用 `temperature=1.0`，避免模型拒绝默认温度请求（Fixes #1102）。

### 文档

- 🐳 **补充官方 Docker 镜像使用说明** — 增加镜像拉取、`docker run` 用法与 `.env` / 数据目录映射说明，不再只覆盖 Compose 部署路径。
- 📨 **修正飞书自定义机器人 Webhook 示例** — `feishu_sender.py` 中的示例改为 interactive card JSON，并补充飞书自动化 Webhook 触发器配置教程。
- 📚 **优化根 README 结构** — 保留首页级功能特性、技术栈、快速开始、推送效果、Web、Agent、赞助商和新闻源入口，将细配置、交易纪律和基本面语义收口到完整指南，并将 Docker 徽章指向官方镜像页。
- 🌐 **同步英文与繁中 README 的精简入口结构** — 同时补齐完整指南中的 LLM 用量 API 与持仓管理说明。
- 🤝 **调整 AI 协作与 PR 模板中的 README 维护规则** — 明确 README 非必要不更新，细节优先进入专题文档。

### 测试

- 🧪 **稳定市场复盘相关测试的 LiteLLM stub 行为** — 避免本机安装的 LiteLLM 在测试收集顺序变化时影响市场复盘单元测试。
- 🧪 **pytest 默认跳过前端依赖目录** — 本地存在 `apps/dsa-web/node_modules` 时不再被后端测试递归扫描，避免发布前 gate 被无关目录拖慢。

## [3.13.0] - 2026-04-21

### 发布亮点

- 🌉 **长桥 OpenAPI 数据源接入** — 美股/港股行情优先使用 Longbridge，YFinance / AkShare 自动兜底；未配置时行为不变。
- 📈 **Tushare 港股全链路扩展** — 港股日线通过 `hk_daily` 获取；筹码分布对港股返回 `None`；换算单位跟随港股口径，不再套用 A 股手/千元规则。
- 🔍 **Anspire Search 语义搜索接入** — 配置 `ANSPIRE_*` 后即可使用 Anspire Search 获取实时行情及资讯，未配置时完全透明。
- 🚀 **普通分析链路支持 LLM 流式生成** — 首页任务 SSE 新增 `task_progress` 事件，进度更细化；不支持流式的 provider 自动回退到非流式调用。
- 🤖 **Web 渠道编辑器支持按需拉取可用模型列表** — `/v1/models` 统一模型发现入口，多选写回 `LLM_{CHANNEL}_MODELS`，拉取失败时保留手动输入降级。
- 🛡️ **Agent 稳定性与预算护栏全面补强** — `AGENT_MAX_STEPS` 语义统一、技能降级不中断管线、SSE 异常透传、技能加载 warning 日志补齐。
- 🛠️ **SQLite 写入链路原子化** — 批量原子 upsert + WAL + `busy_timeout` + 有限写入重试，显著降低批量分析并发锁竞争。

### 新功能

- 🌉 **集成 Longbridge OpenAPI 作为美股/港股可选数据源**（fixes #981）— 配置 `LONGBRIDGE_*` 后优先使用长桥获取日线与实时行情，YFinance / AkShare 兜底；未配置时行为与此前一致。联调使用 `tests/longbridge_live_smoke.py`（手动脚本，不参与 pytest 收集）。
- 📈 **Tushare 支持港股日线查询** — 配置 Tushare 凭证后调用 `hk_daily` 接口获取港股数据；权限不足时抛出异常，与原流程一致。
- 🔍 **集成 Anspire Search 可选语义搜索后端** — 配置 `ANSPIRE_*` 可使用 Anspire Search 获取实时行情及新闻资讯；未配置时行为与此前一致。联调使用 `tests/test_anspire_search.py`（手动脚本）。
- 🚀 **普通分析链路支持 LiteLLM 流式生成与更细任务进度** — 股票分析在 LLM 阶段优先尝试 `stream=True` 并在服务端累积 chunk，首页任务 SSE 新增 `task_progress` 事件与更细的 `message/progress` 更新；仅在最终 JSON 解析成功后持久化历史报告；不支持流式的 provider 自动回退到非流式调用。
- 🤖 **Web AI 模型配置支持按渠道获取可用模型列表** — 渠道编辑器支持调用 `/v1/models` 拉取可用模型，并以多选方式写回 `LLM_{CHANNEL}_MODELS`；拉取失败时保留手动输入作为降级路径。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；仅对极少数高位且摘要不足的结果做延迟补抓，优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案统一为"主模型 / Agent 主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键保持兼容。
- 🧠 **IntelAgent 新增公司公告搜索与主力资金流工具** — 增加上交所/深交所/cninfo 公告搜索维度与 `get_capital_flow` 工具，修复 Agent 模式下公告和资金流数据经常缺失的问题。
- 📦 **后端股票名称解析优先复用 `stocks.index.json`** — 懒加载缓存前端静态索引，纯后端/缺失静态资源场景静默降级回 `STOCK_NAME_MAP` 与原有数据源回退链路。
- 📊 **TushareFetcher 港股单位适配** — `get_chip_distribution` 对港股直接返回 `None`（港股暂不支持筹码分布）；`_normalize_data` 对港股（`hk_daily`）不再做 A 股手→股、千元→元的缩放，与 Tushare 港股字段语义一致。
- ⏱️ **Agent 超步数错误增加 `AGENT_MAX_STEPS` 调整提示** — 帮助用户自助排查步数限制问题。
- ⚙️ **GitHub Actions 分析任务超时支持 `vars` 配置** — `daily_analysis.yml` 任务超时从 repository variables 读取，无需修改代码即可调整运行超时上限（fixes #1014）。

### 修复

- 📣 **大盘复盘链路接入 `REPORT_LANGUAGE`** — `REPORT_LANGUAGE=en` 时，A 股/合并复盘的 Prompt、章节标题、模板兜底文案与通知包装标题统一输出英文，避免英文正文搭配中文标题的混排问题。
- 📈 **EfinanceFetcher 指数开盘价映射兼容**（fixes #1043）— `get_main_indices()` 的开盘价映射改为兼容 `今开 → 开盘 → open`，修复部分 efinance 版本下指数开盘价被读成缺失值的问题。
- 🤖 **AGENT_MAX_STEPS 语义统一**（fixes #1026）— 在 orchestrator 多 Agent 模式下明确为"各子 Agent 步数上限而非硬覆盖"；TechnicalAgent 等高默认值 Agent 会被封顶，低默认值 Agent 保持原值；用户主动调高（>10）时统一覆盖所有子 Agent。修复了用户设置 12 但 TechnicalAgent 仍以默认 6 步运行并报 "Agent exceeded max steps" 的问题。
- 🛡️ **Specialist（Skill）Agent 失败改为优雅降级** — 技能 Agent 失败不再中断整个分析管线，与 intel/risk 保持相同的降级策略。
- 🔧 **MiniMax-M2.7 连接测试修复** — 修复 LLM 通道连接测试在 MiniMax-M2.7 下返回 "Empty response" 的问题；将 `max_tokens` 上限从 8 提升至 256 以容纳思考过程，并添加 `content_blocks` 格式解析逻辑。
- 📊 **移除 `sentiment_score` 范围约束**（fixes #942）— 移除 `HistoryItem` 与 `ReportSummary` 响应 Schema 中 `sentiment_score` 的 `ge=0/le=100` 约束，历史库中存储的超范围值不再触发 Pydantic ValidationError。
- 🖥️ **WebUI 前端资源缺失时发出明确警告** — `webui_frontend.py` 在 `static/index.html` 存在但 `static/assets/` 缺失时发出 warning，避免 CSS/JS 资源缺失导致页面异常变大却无从排查（fixes #944）。
- 🔗 **分析管线可选服务降级初始化** — `StockAnalysisPipeline` 搜索服务与社交舆情服务任一初始化异常时，记录 warning 并以禁用状态继续运行，避免外部依赖抖动阻塞主分析链路。
- 🖥️ **桌面端版本展示统一读取 `package.json`** — 统一读取 `apps/dsa-desktop/package.json`，移除 preload 中硬编码的 `0.1.0`，设置页展示真实桌面端版本；修复版本号显示错误（fixes #1048）。
- 🐋 **港股名称获取失败修复**（fixes #940）— 修复主数据源字段缺失时无法正确回退到备用字段获取港股名称的问题。
- 🔄 **SSE 任务流断开时 `CancelledError` 正确 re-raise**（fixes #967）— 修复 SSE 流中断时异常被静默吞掉导致故障无日志可查的问题。
- 🔄 **Agent SSE 清理阶段后台任务异常正确上报**（fixes #969）— 流结束时后台执行器异常现在正确记录并上报，避免错误无法感知。
- 🔇 **技能加载异常补充 `logger.warning` 日志**（fixes #970）— 在 `ask.py`、`skills/aggregator.py`、`skills/router.py` 的静默 except 块补充日志，确保技能列表为空时有日志可查。
- 🛠️ **SQLite 写入链路原子化**（fixes #878）— `stock_daily(code,date)` 使用批量原子 upsert；文件型 SQLite 连接默认启用 WAL + `busy_timeout` + 有限写入重试；"新增数"改按本次真正插入窗口计算。
- 💰 **多 Agent / 单 Agent 预算护栏语义统一** — 剩余预算低于最小阈值时主动跳过并降级；已完成阶段可构建降级报告时返回 `success=True` 并携带非空内容，否则返回 `success=False`。
- ⚙️ **GitHub Actions `daily_analysis.yml` 补齐 `REPORT_LANGUAGE` 注入**（fixes #1013）— 修复用户在 Secrets/Variables 中配置 `REPORT_LANGUAGE` 后不生效的问题。
- 📊 **任务状态 API 补齐实时价格字段**（fixes #983）— `GET /api/v1/analysis/status/{task_id}` 从数据库回填已完成任务时补齐 `current_price` / `change_pct`，修复首页报告股票名旁不显示实时价格的问题。
- 📅 **非交易日数据返回最近交易日**（fixes #1009）— 修复非交易日（周末/节假日）筹码分布与板块排行返回倒数第二个交易日数据的问题，现在正常返回最近交易日数据。
- 🔍 **A 股资讯搜索恢复中文优先** — `search_stock_news()` 在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面；非美股查询不再默认沿用 Brave 的 `en/US` 区域语言偏好。
- 📨 **飞书群机器人通知支持签名校验** — 飞书通知现在支持 `FEISHU_WEBHOOK_SECRET` / `FEISHU_WEBHOOK_KEYWORD`；Web 设置与文档明确区分 Webhook 推送模式和 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 应用模式，降低误配风险。
- ⚡ **LLM 适配层新增 `RateLimitError` 和 `ContextWindowExceeded` 检测** — 识别并处理速率限制与上下文窗口超出错误，提升分析链路在高负载或长文本场景下的健壮性（fixes #1002）。

### 测试

- 🧪 **TushareFetcher 港股相关单元测试** — 新增 `get_chip_distribution` 筹码分布获取与 `_normalize_data` 港股/A 股/ETF 单位处理的单元测试，覆盖港股特殊路径。

### 文档

- 📘 **DEPLOY.md 补充 UI 元素异常变大排查步骤** — 新增重建 Docker 镜像或手动执行 `npm run build` 的排查指南；`deploy-webui-cloud.md` 同步更新。
- 📨 **飞书 Webhook 配置说明补全** — 强调 `FEISHU_WEBHOOK_URL` 是群通知必填项、签名校验须两端同时启用或关闭、`FEISHU_APP_SECRET` 仅用于应用/Stream Bot 模式；`.env.example` 补充内联注释；同步英文指南。
- 🤝 **FAQ 补充 Ollama 连接失败排障条目（Q12c）** — 覆盖服务未启动、URL 配置错误、模型前缀缺失、模型未下载、远程防火墙等 5 个检查点（fixes #854）。
- 🌉 **README 补充长桥数据源使用说明** — 中/英/繁 README 明确长桥"首选 / 兜底 / 未配置不调用"边界；`docs/` 内相对路径链接修复；`LONGBRIDGE_PRINT_QUOTE_PACKAGES` 配置与代码及 `.env.example` 对齐。
- 🐋 **Docker 安装场景版本说明** — 补充最小化文档，明确 Docker 安装场景下应以 Git tag / 镜像 tag 判断版本（fixes #1091）。

## [3.12.0] - 2026-04-01

### 发布亮点

- 📊 **回测页新增"次日验证"视图** — 可按股票与日期范围查看 AI 预测 vs 次日实际涨跌，复用历史分析与 1 日回测结果，快速验证分析准确率。
- 🔧 **LLM 接入体验简化** — 用户侧文案统一收口为"主模型 / 备选模型 / 模型渠道"，不再把 LiteLLM 当作普通用户必学概念，现有配置键保持兼容。
- 🐳 **Docker / WebUI 运行时稳态补强** — 修复系统设置保存后配置不生效、启动早期日志缺失、预构建静态资源复用等问题，降低容器化部署的运维摩擦。
- 🔒 **安全与并发稳定性同步增强** — Discord 入站 Webhook 补齐 Ed25519 验签，修复并发执行时共享状态未加锁、单股推送模式通知并发复用等问题。
- 🖥️ **桌面端与定时任务细节打磨** — Windows 安装器支持自选安装目录，内置定时调度器感知运行中 SCHEDULE_TIME 变更，断点续传改按市场时区判断。

### 新功能

- 📊 **回测页新增"次日验证 / 1 日窗口"视图** — 可按股票代码与分析日期范围查看 AI 预测、次日实际涨跌及筛选区间准确率，复用历史分析与 1 日回测结果实现。
- 🏷️ **Web 设置页新增版本信息卡片** — `apps/dsa-web` 现在会在构建时注入前端包版本与构建时间，系统设置页新增只读"版本信息"区块，展示 `WebUI 版本 / 构建标识 / 构建时间`；当 `package.json` 仍为占位版本 `0.0.0` 时，会自动回退为构建标识，方便 Docker 重建后快速确认当前静态资源是否已经生效。
- 🪟 **Windows 桌面安装器支持自选安装目录** — 安装器改为支持在安装向导中自定义安装目录，安装到非默认盘符后仍沿用现有打包态目录逻辑在安装目录旁读写 `.env`、`data/stock_analysis.db` 和 `logs/desktop.log`，同时保留 `win-unpacked` 免安装分发方式。安装器仅支持当前用户安装、已禁用管理员提权（`allowElevation: false`），并通过 NSIS `.onVerifyInstDir` 阻止选择系统保护目录。

### 改进

- 🔎 **SerpAPI 正文补抓范围收敛** — 自然搜索结果不再逐条同步抓取网页正文；现在仅对极少数高位且摘要明显不足的结果，在更短超时预算内做延迟补抓，并优先复用 SerpAPI 已返回的结构化摘要，降低搜索链路尾延迟与慢站点放大风险。
- 🤖 **LLM 接入体验简化** — 面向用户的 AI 模型接入文案已统一收口为"主模型 / Agent 主模型 / 备选模型 / 模型渠道 / 高级模型路由配置"；Web 设置页、配置元数据、校验提示与中英文文档不再把 LiteLLM 当作普通用户默认必学概念，现有 `LITELLM_*` / `LLM_CHANNELS` 配置键仍保持兼容。

### 修复

- 🚀 **启动早期失败时暴露真实根因** — `python main.py` 现在通过 stderr 暴露真实根因，bootstrap 阶段不再向硬编码 `logs/` 目录写入文件日志，文件日志推迟到 `config.log_dir` 可用后创建，避免健康启动在非预期路径残留日志文件。
- 🐳 **Docker WebUI 运行时优先复用预构建静态资源** — `prepare_webui_frontend_assets()` 现在会先检查镜像内已有的 `static/index.html` 是否可直接复用；当容器运行时不包含 `apps/dsa-web` 源码目录且未安装 `npm` 时，也不会误报"未找到前端项目，无法自动构建"，从而恢复 Docker 部署后的 WebUI 打开能力。
- 🐳 **Docker WebUI 系统设置保存后配置生效** — Docker 场景下 WebUI 保存 `STOCK_LIST`、`SCHEDULE_ENABLED`、`SCHEDULE_TIME`、`SCHEDULE_RUN_IMMEDIATELY`、`RUN_IMMEDIATELY` 后，`Config` 会优先读取持久化 `.env` 中的新值，避免被容器创建时注入的旧环境变量覆盖。
- 📈 **市场复盘 LLM max_tokens 提升** — 市场复盘生成链路将 LLM `max_tokens` 从 `2048` 提升到 `8192`，降低长复盘输出因 `MAX_TOKENS` 提前截断导致内容未完成的概率。
- ⏰ **内置定时调度器感知 SCHEDULE_TIME 运行时变更** — 调度器现在会在运行中感知 WebUI 保存后的 `SCHEDULE_TIME` 变化，并在下一轮检查时重绑 daily job。
- 🪟 **Windows Release 渠道编辑器保留 MiniMax 模型前缀** — 渠道模式下填写 `minimax/<模型名>` 时，后端归一化与 Web 设置页运行时模型列表都会保留该值原样，不再误改写成 `openai/minimax/<模型名>`。
- 🤖 **Discord 入站 Webhook 补齐 Ed25519 验签** — `DiscordPlatform` 现在会基于 `X-Signature-Ed25519`、`X-Signature-Timestamp` 和原始请求体校验 Discord Interaction 签名；缺失签名头、公钥格式非法或签名不匹配时直接拒绝请求，同时对 timestamp 做 ±5 分钟时效窗口校验以防御重放攻击。
- ⚙️ **STOCK_GROUP_N / EMAIL_GROUP_N 配置关系明确化** — 明确与 `STOCK_LIST` 的关系，并在配置校验中对超出 `STOCK_LIST` 的邮件分组给出 warning。
- 🗓️ **断点续传改按市场时区和交易日历判断**（fixes #880）— 股票数据存在性检查不再直接使用服务器自然日，而是按 A 股 / 港股 / 美股各自市场时区解析"最新可复用交易日"。
- 📨 **单股推送模式不再并发复用共享通知实例** — `StockAnalysisPipeline.run()` 现在会保留个股分析并发，但把 `SINGLE_STOCK_NOTIFY=true` 下的即时通知挪到结果收集侧串行发送。
- 🔇 **实时行情降级提示收口为单次告警** — 分析主流程获取股票名称时不再提前触发一次实时行情查询，只有在全部数据源都不可用时才提示已降级为历史收盘价继续分析。
- 🔍 **A 股中文资讯搜索恢复中文优先** — `search_stock_news()` 现在会在首个 provider 主要返回英文资讯时继续尝试后续引擎，并将同批结果中的中文资讯排到前面。
- 🔒 **并发执行时共享状态补齐统一加锁** — 修复并发执行时共享状态缺少统一加锁的问题，避免多线程场景下的数据竞争。

### 测试

- 🧪 **补充设置页版本信息回归测试** — 新增 Web 设置页版本信息渲染断言，并覆盖占位版本 `0.0.0` 自动回退为构建标识的逻辑。
- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

## [3.11.0] - 2026-03-27

### 发布亮点

- 🎨 **Web 工作台完成一轮 UI 统一与双主题升级** — 首页、问股、回测、持仓和设置页进一步收口到统一设计 token、输入表面和状态表达；新增完整浅色主题，并支持浅色 / 深色一键切换与持久化保存。
- 🤖 **Bot / Agent 能力重新补回主分支** — 恢复 `/history`、`/strategies`、`/research` 等命令，`/ask` 继续支持多股对比与组合视角；Deep Research、事件监控与 schedule 轮询链路重新接回主线能力。
- 🔒 **安全性与运行稳态同步补强** — 修复 `X-Forwarded-For` 限流绕过风险，恢复 LiteLLM 官方 PyPI 安装路径，Tushare 初始化不再依赖本地 SDK，降低 Docker、桌面打包和环境重建时的脆弱点。
- 🖥️ **日常使用细节继续打磨** — 修复首页港股自动补全提交、登录页首屏主题闪烁、历史长股票名重叠，以及 Telegram Markdown 解析失败时整条通知发送中断等问题。

### 新功能

- 🎨 **全新浅色主题与双主题切换上线** — Web 工作台新增完整浅色主题，并支持在侧边栏中一键切换浅色 / 深色模式；主题选择会持久化保存，刷新页面后仍保持当前偏好。此次升级不是局部配色微调，而是对卡片层级、边界对比、输入表面、状态提示和页面背景做了一整套 light theme 重绘。
- 🤖 **补回主分支缺失的 Agent / Bot 能力** — `#648` / `#649` 已重新补回 `main`：Bot 恢复 `/history`、`/strategies`、`/research`，`/ask` 保留多股对比与组合视角；Deep Research 与 Event Monitor 的配置重新在 Web 设置页可见并可编辑，schedule 模式也重新接入事件告警轮询。

### 改进

- 🖥️ **核心页面统一到同一套工作台视觉语言** — `Home / Chat / Backtest / Portfolio / Settings` 进一步收口到共享设计 token、`input-surface` 输入体系、空态/错误态表达和抽屉遮罩语义，减少页面之间的视觉割裂与局部私有样式漂移。
- 💬 **问股交互可达性与反馈增强** — 问股页补强了会话导出、通知发送、消息复制、历史删除与追问上下文提示；AI 回复操作不再过度依赖 hover，触屏设备和小屏场景下也能直接触达关键按钮。
- 📊 **回测与持仓页表面和状态表达继续标准化** — 回测页筛选控件、布尔状态、结果表格与汇总卡片统一到共享输入/状态原语；持仓页的导入反馈、汇率刷新提示、空态与警示信息进一步归口到共享组件，减少页面级重复实现。
- 🧭 **导航与页面壳层协同优化** — 侧边栏主题切换、问股完成角标、移动端抽屉遮罩和主内容滚动契约进一步统一，首页、问股和回测在桌面端与移动端的切页体验更稳定。

### 测试

- 🧪 **UI 治理与关键路径回归补强** — 补充 `SidebarNav`、`ChatPage`、`BacktestPage` 等组件测试，并新增 UI governance 守卫，持续防止交互元素重新引入原生 `title` 属性或旧 `input-terminal` 样式回流。同步更新 smoke / markdown drawer 相关验证，覆盖主题升级后的关键主链路。

### 修复

- 🌗 **Web 首屏默认主题预设为深色** — `apps/dsa-web/index.html` 现在会在 React 挂载前读取本地保存的主题偏好；若没有已保存值，则立即给 `<html>` 预设 `dark` 并同步 `color-scheme`，避免首页和登录页首屏先闪出浅色主题。
- 🔐 **登录页独立主题层收口** — 登录页输入框、标签、切换按钮和按钮文案现在使用独立的 `--login-*` 视觉 token，不再继承全局浅/深主题文字色；即使浏览器缓存了浅色主题，登录页仍保持稳定的深色视觉与青色密码输入表现，避免密码圆点和文案落成黑色。
- 🖥️ **首页港股代码输入修复** — Web 首页分析输入框现在可正确接受港股代码与自动完成选中的港股项，补齐 `00700.HK` / `HK00700` 等格式识别，避免提交时误报“请输入有效的股票代码或股票名称”。

- 🔒 **认证限流 X-Forwarded-For 取值修复（CWE-345）**（#841 / #842）— `get_client_ip()` 从取 `X-Forwarded-For` 最左值改为最右值，防止攻击者通过伪造首部旋转限流桶绕过暴力破解保护；仅影响 `TRUST_X_FORWARDED_FOR=true` 且单层可信反向代理的部署场景，多级代理环境需按部署文档评估配置。
- 📦 **恢复 LiteLLM 官方 PyPI 安装并锁定安全上限** — `requirements.txt` 重新使用 `pip install litellm` 的官方 PyPI 安装路径，并在保留历史最低要求 `>=1.80.10` 的同时增加 `<1.82.7` 的安全上限，避免误装已被移除的 `1.82.7` / `1.82.8` 风险版本；Windows 桌面打包脚本也同步回退到标准 `pip install -r requirements.txt` 链路，减少特殊下载分支带来的维护成本。
- 📨 **Telegram Markdown 解析失败回退纯文本**（fixes #850）— `src/notification_sender/telegram_sender.py` 现在会在 Telegram 返回 `HTTP 400` 且包含 `can't parse entities` / Markdown 解析错误时，自动去掉 `parse_mode` 后重试纯文本发送，避免 `*ST` 等正文内容直接导致整条通知失败。
- 🔢 **A 股同码实时行情保留交易所提示**（fixes #852）— `DataFetcherManager` 与 `TushareFetcher` 现在会保留 `SZ000001` / `000001.SZ` 这类显式沪深提示，旧版 Tushare 实时行情降级分支不再把深市 `000001` 误判成 `sh000001` 上证指数。
- 🎯 **多 Agent 次优买点不再盲目复制理想买点**（fixes #851）— 当多智能体结果缺少独立 `secondary_buy` 时，仪表盘现在优先展示 `N/A` 而不是把 fallback 值硬拷贝成与 `ideal_buy` 完全相同，减少误导性的双买点展示。
- 🧩 **Tushare 初始化不再强依赖本地 SDK 包** — `TushareFetcher` 现在直接使用内置 HTTP client 访问 Tushare Pro，不再在启动阶段先 `import tushare` 才能初始化；修复了 Docker、桌面打包或环境重建后因缺少 `tushare` 包而提前报 `No module named 'tushare'` 的问题，并补充对应回归测试。
- ⚙️ **`daily_analysis` 工作流补齐 `DEEPSEEK_API_KEY` 映射** — GitHub Actions 每日分析工作流现在会正确透传 `DEEPSEEK_API_KEY`，避免云端任务配置了密钥却在运行时拿不到对应环境变量。
- 🖥️ **历史列表过长股票名称截断与悬停展示**（fixes #815）— 历史列表中过长的股票名称, 现在会按字符类型自动截断（英文15/中文8/混合10字符），默认显示截断结果，悬停时展示完整名称；解决 1920x1080 分辨率下股票名称与右侧状态标签文字重叠的问题。新增 `stockName.ts` 工具函数并补充对应测试。

### 文档

- 🧾 **README 捐赠入口更新为小红书二维码** — README 及中英文说明中的赞助入口更新为小红书二维码素材，保持展示口径一致。

## [3.10.1] - 2026-03-24

### 新功能

- 🔔 **Web 端分析推送通知开关**（#808）— 首页分析按钮旁新增「推送通知」复选框，默认勾选；取消勾选时本次分析不发送 Telegram/企业微信等推送。API `POST /api/v1/analysis/analyze` 新增 `notify` 字段（`bool`，默认 `true`），不传时行为与修改前一致，Bot 和定时任务不受影响。

### 改进

- 🖥️ **问股 / 回测页面布局与壳层协同优化** — 统一 Chat / Backtest 页面容器、共享 UI 状态和跟随问答交互路径，移除部分硬编码高度限制，让导航框架内的填充与滚动行为更连贯。
- 🎨 **全局视觉与共享组件继续收敛** — Light theme 引入动态 HSL 阴影体系，统一侧边栏激活态、告警组件对比度和聊天气泡样式，并把部分零散内联样式收口为语义化 CSS 变量，提升一致性与可维护性。

### 修复

- 🖼️ **系统设置智能导入文件选择恢复** — 修复了“系统设置 > 基础设置 > 智能导入”模块中 “选择图片 / 选择文件” 两个按钮点击无响应的问题。
- 🖥️ **移动端滚动与交互层级修复** — 解决主题切换菜单在移动端被主内容遮挡的 z-index 冲突，并恢复首页长报告场景下的正常纵向滚动，不影响其他页面现有滚动行为。
- 🧾 **Markdown 纯文本复制清洗增强** — 改进纯文本导出算法，复制分析报告时会更稳定地清除表格分隔符等 Markdown 痕迹，提升分享和归档内容的纯净度。
- 🧠 **Trading philosophy injection 覆盖 legacy + Agent 全链路**（#810）— `GeminiAnalyzer`、单 Agent 模式和 skill-aware Prompt 现在共享同一套策略注入状态；只有隐式回落到内置默认 `bull_trend` 时才保留旧的趋势型提示，显式策略选择或自定义默认 skill 不再被偷偷叠加 `MA5>MA10>MA20` 多头基线。
- 🛠️ **后端 CI 依赖安装链路稳态化**（#835）— 拆分 backend gate 阶段、为依赖安装增加重试，并把 CI 用的 `litellm` 安装来源调整为更稳定的 GitHub 源，降低依赖解析抖动导致的 backend gate 偶发失败。
- 🪟 **Windows 桌面发版构建恢复 LiteLLM 安装兼容性** — `scripts/build-backend.ps1` 现在会先过滤 `requirements.txt` 中的 LiteLLM GitHub 源包，再下载对应 tag 的 zipball 到本地移除上游可选 `enterprise/` 目录后安装，绕过 Windows runner 上 Poetry 构建 wheel 时把目录误当文件打包导致的失败；同时补上 `pip install` 退出码检查，避免依赖安装失败后只在后续 `python-multipart` 校验阶段才暴露成次生报错。

### 测试

- 🧪 **问股 / 回测 / 智能导入回归覆盖补齐** — 同步更新 E2E 冒烟期望，补充 `DashboardStateBlock`、Chat 页、智能导入文件选择与相关交互回归断言，确保近期 UI 调整后的关键路径仍可稳定通过。

## [3.10.0] - 2026-03-24

### 发布亮点

- 🔎 **自动补全与索引工具扩展到三市场** — 补全索引生成链路现在同时覆盖 A 股、港股、美股，配套新增 Tushare 股票列表抓取工具与更完整的静态索引数据，让首页搜索入口从“能用”走向“更全、更稳”。
- 🖥️ **Dashboard 与报告查看体验继续收口** — 首页 Dashboard 面板、状态边界、字体层级和完整报告表格密度完成一轮统一；报告详情也补齐了 Markdown/纯文本复制与更可靠的按钮交互，减少历史报告查看与分享时的摩擦。
- 🤖 **Agent skill 与市场语义边界更清晰** — skill bundle、默认策略、回测汇总语义和兼容接口进一步收敛；同时分析 Prompt 不再默认写死 A 股上下文，美股和港股分析也能按各自市场规则生成更贴切的内容。
- ⏰ **定时与桌面配置能力更贴近真实使用场景** — 桌面端支持 `.env` 导入导出；`python main.py --schedule --stocks ...` 也不再把启动时股票快照错误带入后续计划执行，定时任务会跟随最新保存的 `STOCK_LIST`。
### 新功能

- 💾 **桌面端 `.env` 备份/恢复入口**（#754）— 桌面模式下的系统设置页新增 `导出 .env` / `导入 .env` 按钮，可直接备份当前已保存配置，或把备份文件中的键值合并恢复到当前桌面端 `.env`；导入沿用现有 `config_version` 冲突保护与运行时重载链路，不改变现有桌面端便携模式路径。
- 📊 **Tushare 股票列表获取工具** — 新增 `scripts/fetch_tushare_stock_list.py`，支持从 Tushare Pro 获取 A股、港股、美股列表信息并保存为 CSV，配有分页读取、智能限流、错误处理和进度提示；新增对应使用文档 `docs/TUSHARE_STOCK_LIST_GUIDE.md`。
- 🔎 **索引生成脚本多市场支持** — `generate_index_from_csv.py` 重构为支持 Tushare 和 AkShare 双数据源，同时覆盖 A股、港股、美股三个市场；新增按市场分类的别名映射（A股、港股常见别名，美股常用股票英文缩写）；添加 `--source` 参数切换数据源、`--test` 参数验证模式；严格过滤美股 DUMMY 记录。
- 🔎 **索引生成脚本增强** — `generate_stock_index.py` 新增 `--test`/`-t` 测试模式和 `--verbose`/`-v` 详细输出模式，添加市场分布统计，优化 JSON 输出格式。
- 📋 **首页完整报告支持双模式复制** — 历史报告详情头部新增“复制 Markdown 源码”和“复制纯文本”工具按钮；前者保留原始 Markdown 结构，后者去除常见 Markdown 格式符号，方便分享、归档和跨报告比对。复制按钮文案会跟随 `REPORT_LANGUAGE` 保持中英文一致，避免英文报告页出现中文固定文案。
- 🧩 **个股分析页补齐关联板块展示**（#669）— A 股分析写路径现在会把 `belong_boards` 一次性写入 `fundamental_context` / `fundamental_snapshot`，结构化报告详情同步新增 `belong_boards` 与 `sector_rankings` 字段，Web 个股分析页首屏可直接展示所属板块及其是否命中当日板块涨跌榜；无数据时保持 fail-open 隐藏，不影响现有分析主流程。

### 改进

- 🖥️ **Dashboard 面板统一化（PR7-2）** — 新增 `DashboardPanelHeader` 和 `DashboardStateBlock` 作为历史、报告、资讯、任务和透明度等面板的通用组件；统一了各面板标题层级、加载/空态/错误态和 CSS 变量 token。
- 🖥️ **HomePage 状态边界收口（PR7-2）** — 引入 `useHomeDashboardState` hook，集中 `stockPoolStore` 状态选取逻辑，移除 `HomePage` 中重复的本地状态派生和回调定义。
- 🧭 **Agent skill 统一到单一配置语义** — Multi-Agent runtime、API、Web chat 和配置元数据统一围绕 `skill` 概念收敛；`/api/v1/agent/skills` 成为主发现入口，`AGENT_SKILL_*` 成为主配置面，内置 skill 元数据也开始声明默认启用、排序优先级、market regime tag 等信息，减少默认策略散落在代码里的隐式耦合。
- 🔎 **自动补全索引数据更新** — 重新生成 `stocks.index.json`，涵盖 A股、港股、美股三个市场，提升自动补全覆盖率。
- 🧾 **Dashboard 字体与完整报告表格密度微调** — 收敛首页侧栏、空状态、历史操作区的字体层级，并将完整 Markdown 报告表格 `th/td` 的内边距调整到更紧凑的 4-6px 区间，让信息密度与现有 Dashboard 视觉节奏更一致。

### 修复

- ⏰ **定时模式不再锁定启动时 CLI 股票快照** — `python main.py --schedule --stocks ...` 现在不会让后续计划执行沿用启动时的旧股票列表；定时任务每次触发前都会重新读取最新保存的 `STOCK_LIST`，确保 WebUI 或 `.env` 更新后的自选股配置能参与后续推送。
- 🌍 **LLM Prompt 按股票市场动态注入上下文** — 分析链路不再把市场规则写死成 A 股；系统 Prompt 会根据股票代码识别 A 股、港股或美股，并注入对应的角色描述与交易规则提示，减少跨市场分析出现口径错位或结论失真的问题。
- 🔎 **美股自动补全复用 ticker 去重** — `generate_index_from_csv.py` 在导入 Tushare `us_basic` CSV 时会先按 `ts_code` 折叠复用的美股 ticker，优先保留更可能仍在使用的记录，避免 `stocks.index.json` 出现重复 `canonicalCode` 后让 Web 自动补全展示历史名称或提交歧义代码。
- 🧾 **Web 报告详情复制交互稳定性修复**（#749）— `ReportDetails` 中“原始分析结果 / 分析快照”的复制按钮补齐可点击层级，避免被下方 JSON 内容覆盖；两个面板的复制提示也改为各自独立，不再出现复制一个后两个按钮同时显示“已复制”的误导反馈。
- 📊 **Agent skill 回测与兼容接口语义收敛** — `get_skill_backtest_summary` 现在要求显式传入 `skill_id`，缺失时返回明确校验提示；仓库尚未持久化真实 skill 级汇总时会返回明确的 unsupported/info 响应，并保留 `normalized` 与 `*_pct` 兼容字段，避免沿用 overall 指标误导 Agent 或用户。
- 🔧 **Skill 默认选择与兼容层行为加固** — `allowed-tools` 会继续仅作为 `SKILL.md` bundle 元数据保留，不再泄露到运行时工具选择；`/api/v1/agent/strategies` 恢复旧 payload 形状；显式传入 `skills: []` 时会清空陈旧上下文；当用户明确选择策略 skill 时不再偷偷叠加默认 bull-trend，而在 `AGENT_SKILLS` 为空时则统一只回落到单一主默认 skill。

### 测试

- 🧪 **Dashboard 组件测试覆盖率扩展（PR7-2）** — 新增 `ReportNews` 和 `TaskPanel` 测试；对 `HistoryList`、`ReportDetails`、`HomePage`、`useDashboardLifecycle` 和 `stockPoolStore` 增强了断言覆盖，包括删除回退、移动端抽屉和任务生命周期等场景。
- 🧪 **多市场索引生成测试补齐** — 新增 `tests/test_generate_index_from_csv.py`，覆盖 Tushare/AkShare 双数据源解析、多市场判断、美股 DUMMY 过滤与重复 ticker 去重等核心路径。
- 🧪 **关联板块写入与 API 契约回归** — 新增 `tests/test_pipeline_related_boards.py`，并补充分析历史与分析接口契约测试，确保 `belong_boards` / `sector_rankings` 只做增量扩展且保持 fail-open。
- 🧪 **定时模式股票列表语义回归测试** — 新增 `tests/test_main_schedule_mode.py`，覆盖定时模式忽略启动时 `--stocks` 快照、单次运行仍保留 CLI 股票覆盖的边界场景。

### 文档

- 📘 **新增 Tushare 股票列表工具文档** — 新增 `docs/TUSHARE_STOCK_LIST_GUIDE.md`，说明股票列表抓取工具的使用方法、数据格式和常见问题。
- 🌍 **补齐定时模式与关联板块的双语说明** — `docs/full-guide.md` / `docs/full-guide_EN.md` 现在明确说明 scheduled mode 会在每次执行前重新读取 `STOCK_LIST`，并同步补充个股关联板块展示能力说明，减少配置预期偏差。
- 🧭 **调整 Agent 术语兼容文案** — README、双语文档、设置页与问股界面继续以“策略”作为用户入口主称呼，同时补充 `skill` 作为内部统一命名，降低迁移期理解成本。

## [3.9.0] - 2026-03-20

### 发布亮点

- 🤖 **模型链路与报告语言更灵活** — Agent 现在可以通过 `AGENT_LITELLM_MODEL` 独立选择模型链路，普通分析与 Agent 报告也可通过 `REPORT_LANGUAGE=zh|en` 输出统一语言，减少“英文内容 + 中文壳子”这类混排问题，并允许团队分别权衡主分析与 Agent 的成本、速度和能力。
- 🔎 **首页分析体验完成一轮闭环优化** — 首页新增 A 股自动补全，支持代码、中文名、拼音和别名检索；同时 Dashboard 状态收口到统一 store，历史、报告、新闻与 Markdown 抽屉的交互更稳定，“Ask AI” 追问也会优先携带当前报告上下文。
- 💬 **通知与检索能力继续外扩** — 新增 Slack 一等通知渠道；SearXNG 在未配置自建实例时可以自动发现公共实例并按受控轮询降级；Tavily 时效新闻链路修复后，严格时效过滤不再错误丢光有效结果。
- 💼 **持仓与市场复盘链路更稳** — A 股 market review 可选接入 TickFlow 强化指数与涨跌统计；持仓账本写入改为串行化以缩小并发超卖窗口；汇率刷新入口和禁用态提示也更加清晰，减少用户误判。

### 新功能

- 🔎 **Web 股票自动补全 MVP** — 首页分析输入框新增本地索引驱动的自动补全，支持股票代码、中文名、拼音和别名匹配；选中候选后会提交 canonical code，并透传 `stock_name`、`original_query`、`selection_source` 到分析请求、任务状态和 SSE 事件；索引加载失败时自动退回旧输入模式，不阻断原有提交流程。同步补充了静态索引加载器、索引生成脚本和前后端契约测试。分阶段进行开发，第一阶段仅支持 A 股。
- 💬 **Slack 一等通知渠道** — 新增 Slack 原生通知支持，同时支持 Bot Token 和 Incoming Webhook 两种接入方式；同时配置时优先使用 Bot API，确保文本与图片发送到同一频道；Bot Token 模式支持图片上传（raw body POST，不使用 multipart）；新增 `SLACK_BOT_TOKEN`、`SLACK_CHANNEL_ID`、`SLACK_WEBHOOK_URL` 配置项，GitHub Actions 工作流同步补齐对应 Secrets 传递。
- 🌍 **报告输出语言可配置**（Issue #758）— 新增 `REPORT_LANGUAGE=zh|en`，默认 `zh`；语言设置会同步注入普通分析与 Agent Prompt，并覆盖 Markdown/Jinja 模板、通知 fallback、历史/API `report_language` 元数据及 Web 报告页固定文案，避免“英文内容 + 中文壳子”的混合输出。
- 🚀 **Agent 与普通分析模型解耦**（Issue #692）— 新增 `AGENT_LITELLM_MODEL`（留空继承 `LITELLM_MODEL`，无前缀按 `openai/<model>` 归一）；Agent 执行链路与 `/api/v1/agent/models` 的 `is_primary/is_fallback` 标记改为基于 Agent 实际模型链路；系统配置与启动期校验补齐 `AGENT_LITELLM_MODEL` 的 `unknown_model/missing_runtime_source` 检查；Web 设置页新增 Agent 主模型选择并与渠道模式运行时配置同步。
- 🔎 **SearXNG 公共实例自动发现与受控轮询**（#752）— 新增 `SEARXNG_PUBLIC_INSTANCES_ENABLED`，在未配置 `SEARXNG_BASE_URLS` 时默认从 `searx.space` 拉取公共实例列表，并按受控轮询顺序选择实例；同次请求内遇到超时、连接错误、HTTP 非 200 或无效 JSON 会自动切换到下一个实例。已配置自建实例的用户保持原有优先级与语义不变；`daily_analysis` GitHub Actions 工作流也已支持显式透传该开关并在启动日志中展示当前状态。
- 📈 **TickFlow market review enhancement** (#632) — 新增可选 `TICKFLOW_API_KEY`；配置后，A 股大盘复盘的主要指数行情优先尝试 TickFlow；若当前 TickFlow 套餐支持标的池查询，市场涨跌统计也会优先尝试 TickFlow。失败或权限不足时立即回退到现有 `AkShare / Tushare / efinance` 链路；板块涨跌榜回退顺序保持不变。接入层同时适配了真实 SDK 契约：主指数查询按单次请求上限分批拉取，并将 TickFlow 返回的比例型 `change_pct` / `amplitude` 统一转换为项目内部的百分比口径。

### 改进

- **Dashboard state slice and workspace closure** — moved Home / Dashboard state into `stockPoolStore`, consolidated history selection, report loading, task syncing, polling refresh, and markdown drawer handling under a single state slice.
- **Dashboard panel standardization** — kept the current dashboard layout contract stable while unifying history, report, news, and markdown presentation with shared tokens, standardized states, and bounded in-panel scrolling for the history list.
- **Dashboard-to-chat follow-up bridge** — routed “Ask AI” follow-ups through report-context hydration instead of direct cross-page state coupling, while keeping chat sends usable when enriched history context is still loading.
- 💼 **持仓账本并发写入串行化**（#742）— 持仓源事件写入/删除现在会在 SQLite 下先获取串行化写锁，减少并发卖出把超售流水写入账本的窗口；直接持仓写接口在锁竞争时返回 `409 portfolio_busy`，CSV 导入保持逐条提交并把 busy 计入 `failed_count`。
- 💱 **持仓页汇率手动刷新入口补齐**（#748）— Web `/portfolio` 页面现在会在“汇率状态”卡片中展示“刷新汇率”按钮，直接调用现有 `POST /api/v1/portfolio/fx/refresh` 接口；刷新后会仅重载快照与风险数据，并以内联摘要反馈“已更新 / 仍 stale / 刷新失败”的结果，减少用户对 `fxStale` 长时间停留的误解。

### 修复

- 🔎 **Web 自动补全 Enter 提交语义修正** — 股票自动补全在搜索命中候选时不再默认高亮第一项；候选列表展开但用户尚未用方向键或鼠标明确选中时，按 Enter 会继续提交原始输入，避免手动输入被第一条候选静默覆盖。
- 🌍 **补齐 `REPORT_LANGUAGE` 启动解析与历史展示本地化边界** — `Config` 在启动时继续遵循“真实环境变量优先、`.env` 兜底”的既有语义，并在两者冲突时输出显式告警，减少 `REPORT_LANGUAGE` 来源不清带来的误判；同时 `/api/v1/history/{id}` 英文详情响应会同步本地化 `sentiment_label`，历史 Markdown 也会正确识别英文 `bias_status` 的风险等级 emoji，避免出现 `乐观` 或 `🚨Safe` 这类中英混排/误报展示。
- 📰 **Tavily 时效新闻检索发布时间映射修复**（#782）— Tavily 在股票新闻和严格时效的情报维度中现在会显式使用 `topic="news"`，并兼容 `published_date` / `publishedDate` 两种发布时间字段；修复了 Tavily 明明返回结果却在后续硬过滤阶段被全部记为 `drop_unknown` 丢弃的问题，同时将机构分析、业绩预期、行业分析等分析型维度恢复为宽源搜索，不再被统一压缩成新闻模式。
- 💱 **持仓页汇率刷新禁用语义修正**（#772）— 当 `PORTFOLIO_FX_UPDATE_ENABLED=false` 时，`POST /api/v1/portfolio/fx/refresh` 现在会返回显式 `refresh_enabled=false` 与 `disabled_reason`，Web `/portfolio` 页面会明确提示“汇率在线刷新已被禁用”，不再误报“当前范围无可刷新的汇率对”。
- 🤖 **Agent timeout and config hardening** — `AGENT_ORCHESTRATOR_TIMEOUT_S` now also protects the legacy single-agent ReAct loop, parallel tool batches stop waiting once the remaining budget is exhausted, and invalid numeric `.env` values fall back to safe defaults with warnings instead of crashing startup.
- 🌐 **CORS wildcard + credentials compatibility** — `CORS_ALLOW_ALL=true` no longer combines `allow_origins=["*"]` with credentialed requests, avoiding browser-side cross-origin failures in demo/development setups.
- 🧭 **Unavailable Agent settings hidden from Web UI** — Deep Research / Event Monitor controls are now treated as compatibility-only metadata in the current branch and are removed from the Settings page to avoid exposing non-functional toggles.

### 文档

- 新增 Ollama 本地模型配置说明，同步更新 `README.md` 与 `docs/README_EN.md`（Fixes #690）
- 完善 Ollama 配置说明：`docs/full-guide.md` / `docs/full-guide_EN.md` 环境变量表与 Note 补充 `OLLAMA_API_BASE`，避免英文用户误以为 Ollama 不能作为独立配置入口；合并重复的 `OLLAMA_API_BASE` 条目为单一条目
- 明确文档同步治理边界：补充 `README.md`、专题文档、双语文档与交付说明之间的默认同步规则，减少后续文档漂移

## [3.8.0] - 2026-03-17

### 发布亮点

- 🎨 **Web 界面完成一轮骨架升级** — 新的 App Shell、侧边导航、主题能力、登录与系统设置流程已经串成统一体验，桌面端加载背景也完成对齐。
- 📈 **分析上下文继续补强** — 美股新增社交舆情情报，A 股补齐财报与分红结构化上下文，Tushare 新接入筹码分布和行业板块涨跌数据。
- 🔒 **运行稳定性与配置兼容性提升** — 退出登录会立即让旧会话失效，定时启动兼容旧配置，运行中的 `MAX_WORKERS` 调整和新闻时效窗口反馈更清晰。
- 💼 **持仓纠错链路更完整** — 超售会被前置拦截，错误交易/资金流水/公司行为可以直接删除回滚，便于修复脏数据。

### 新功能

- 📱 **美股社交舆情情报** — 新增 Reddit / X / Polymarket 社交媒体情绪数据源，为美股分析提供实时社交热度、情绪评分和提及量等补充指标；完全可选，仅在配置 `SOCIAL_SENTIMENT_API_KEY` 后对美股生效。
- 📊 **A 股财报与分红结构化增强**（Issue #710）— `fundamental_context.earnings.data` 新增 `financial_report` 与 `dividend` 字段；分红统一按“仅现金分红、税前口径”计算，并补充 `ttm_cash_dividend_per_share` 与 `ttm_dividend_yield_pct`；分析/历史 API 的 `details` 追加 `financial_report`、`dividend_metrics` 可选字段，保持 fail-open 与向后兼容。
- 🔍 **接入 Tushare 筹码与行业板块接口** — 新增筹码分布、行业板块涨跌数据获取能力，并统一纳入配置化数据源优先级；默认按上海时间区分盘中/盘后交易日取数，优先使用 Tushare 同花顺接口，必要时降级到东财。
- 🧱 **Web UI 基础骨架升级** — 重建共享设计令牌与通用组件，新增 App Shell、Theme Provider、侧边导航，并同步调整 Electron 加载背景，为 Web / Desktop 的统一体验打底。
- 🔐 **登录与系统设置流程重做** — 重构 Login、Settings 与 Auth 管理流程，补上显式的认证 setup-state 处理，并让 Web 端与运行时认证配置 API 行为对齐。
- 🧪 **前端回归与冒烟覆盖补强** — 新增并扩展登录、首页、聊天、移动端 Shell、设置页、回测入口等关键路径的组件测试与 Playwright smoke coverage。

### 变更

- 🧭 **页面接入新 Shell 布局契约** — Home、Chat、Settings、Backtest 已统一接入新的页面容器、抽屉和滚动约定，降低 UI 迁移期间的页面行为不一致。
- 💾 **设置页状态同步更稳** — 优化草稿保留、直接保存同步与冲突处理，减少模块级保存后前后端配置状态不一致的问题。
- 🎭 **登录页视觉基线回归** — 登录页恢复到既有 `006` 分支的视觉基线，同时保留新的认证状态逻辑和统一表单交互模型。
- 🏛️ **AI 协作治理资产加固** — 收敛并加强 `AGENTS.md`、`CLAUDE.md`、Copilot 指令和校验脚本的一致性约束，降低治理资产长期漂移风险。

### Added

- **Web UI foundation refresh** — rebuilt shared design tokens and common primitives, introduced the app shell, theme provider, sidebar navigation, and Electron loading background alignment for the upgraded desktop/web experience
- **Settings and auth workflow overhaul** — rebuilt the Login, Settings, and Auth management flows, added explicit auth setup-state handling, and aligned the Web UI with the runtime auth configuration APIs
- **UI regression coverage and smoke checks** — expanded targeted frontend tests and added Playwright smoke coverage for login, home, chat, mobile shell, settings, and backtest entry flows

### Changed

- **Shell-driven page integration** — aligned Home, Chat, Settings, and Backtest with the new shell layout contract so routing, drawer behavior, and page-level scrolling are consistent during the UI migration
- **Settings state consistency** — refined draft preservation, direct-save synchronization, and conflict handling so module-level saves no longer leave the page out of sync with backend config state
- **Login visual baseline** — restored the login page visual treatment to the established `006` branch baseline while keeping the newer auth-state logic and unified form interaction model

### 修复

- ⏰ **定时启动立即执行兼容旧配置**（Issue #726）— `SCHEDULE_RUN_IMMEDIATELY` 未设置时会回退读取 `RUN_IMMEDIATELY`，修复升级后旧 `.env` 在定时模式下的兼容性问题；同时澄清 `.env.example` / README 中两个配置项的适用范围，并注明 Outlook / Exchange 强制 OAuth2 暂不支持。
- 🧵 **运行期 `MAX_WORKERS` 配置生效与可解释性增强**（#633）— 修复异步分析队列未按 `MAX_WORKERS` 同步的问题；新增任务队列并发 in-place 同步机制（空闲即时生效、繁忙延后），并在设置保存反馈与运行日志中明确输出 `profile/max/effective`，减少“参数未生效”误解。
- 🔐 **退出登录立即失效现有会话** — `POST /api/v1/auth/logout` 现在会轮换 session secret，避免旧 cookie 在退出后仍可继续访问受保护接口；同浏览器标签页和并发页面会被同步登出。认证开启时，该接口也不再属于匿名白名单，未登录请求会返回 `401`，避免匿名请求触发全局 session 失效。
- 🧮 **Tushare 板块/筹码调用限流与跨日缓存修复** — 新增的 `trade_cal`、行业板块排行、筹码分布链路统一接入 `_check_rate_limit()`；交易日历缓存改为按自然日刷新，避免服务跨天运行后继续沿用旧交易日判断取数日期。
- 💼 **持仓超售拦截与错误流水恢复**（#718）— `POST /api/v1/portfolio/trades` 现在会在写入前校验可卖数量，超售返回 `409 portfolio_oversell`；持仓页新增交易 / 资金流水 / 公司行为删除能力，删除后会同步失效仓位缓存与未来快照，便于从错误流水中直接恢复。
- 📧 **邮件中文发件人名编码**（#708）— 邮件通知现在会对包含中文的 `EMAIL_SENDER_NAME` 自动做 RFC 2047 编码，并在异常路径补充 SMTP 连接清理，修复 GitHub Actions / QQ SMTP 下 `'ascii' codec can't encode characters` 导致的发送失败。
- 🐛 **港股 Agent 实时行情去重与快速路由** — 统一 `HK01810` / `1810.HK` / `01810` 等港股代码归一规则；港股实时行情改为直接走单次 `akshare_hk` 路径，避免按 A 股 source priority 重复触发同一失败接口；Agent 运行期对显式 `retriable=false` 的工具失败增加短路缓存，减少同轮分析中的重复失败调用。
- 📰 **新闻时效硬过滤与策略分窗**（#697）— 新增 `NEWS_STRATEGY_PROFILE`（`ultra_short/short/medium/long`）并与 `NEWS_MAX_AGE_DAYS` 统一计算有效窗口；搜索结果在返回后执行发布时间硬过滤（时间未知剔除、超窗剔除、未来仅容忍 1 天），并在历史 fallback 链路追加相同约束，避免旧闻再次进入“最新动态/风险警报”。

### 文档

- ☁️ **新增云服务器 Web 界面部署与访问教程**（Fixes #686）— 补充从云端部署到外部访问的落地说明，降低远程自托管门槛。
- 🌍 **补齐英文文档索引与协作文档** — 新增英文文档索引、贡献指南、Bot 命令文档，并补充中英双语 issue / PR 模板，方便中英文协作与外部贡献者理解项目入口。
- 🏷️ **本地化 README 补充 Trendshift badge** — 在多语言 README 中同步补上新版能力入口标识，减少中英文说明面不一致。

## [3.7.0] - 2026-03-15

### 新功能

- 💼 **持仓管理 P0 全功能上线**（#677，对应 Issue #627）
  - **核心账本与快照闭环**：新增账户、交易、现金流水、企业行为、持仓缓存、每日快照等核心数据模型与 API 端点；支持 FIFO / AVG 双成本法回放；同日事件顺序固定为 `现金 → 企业行为 → 交易`；持仓快照写入采用原子事务。
  - **券商 CSV 导入**：支持华泰 / 中信 / 招商首批适配，含列名别名兼容；两阶段接口（解析预览 + 确认提交）；`trade_uid` 优先、key-field hash 兜底的幂等去重；前导零股票代码完整保留。
  - **组合风险报告**：集中度风险（Top Positions + A 股板块口径）、历史回撤监控（支持回填缺失快照）、止损接近预警；多币种统一换算 CNY 口径；汲取失败时回退最近成功汇率并标记 stale。
  - **Web 持仓页**（`/portfolio`）：组合总览、持仓明细、集中度饼图、风险摘要、全组合 / 单账户切换；手工录入交易 / 资金流水 / 企业行为；内嵌账户创建入口；CSV 解析 + 提交闭环与券商选择器。
  - **Agent 持仓工具**：新增 `get_portfolio_snapshot` 数据工具，默认紧凑摘要，可选持仓明细与风险数据。
  - **事件查询 API**：新增 `GET /portfolio/trades`、`GET /portfolio/cash-ledger`、`GET /portfolio/corporate-actions`，支持日期过滤与分页。
  - **可扩展 Parser Registry**：应用级共享注册，支持运行时注册新券商；新增 `GET /portfolio/imports/csv/brokers` 发现接口。

- 🎨 **前端设计系统与原子组件库**（#662）
  - 引入渐进式双主题架构（HSL 变量化设计令牌），清理历史 Legacy CSS；重构 Button / Card / Badge / Collapsible / Input / Select 等 20+ 核心组件；新增 `clsx` + `tailwind-merge` 类名合并工具；提升历史记录、LLM 配置等页面可读性。

- ⚡ **分析 API 异步契约与启动优化**（#656）
  - 规范 `POST /api/v1/analysis/analyze` 异步请求的返回契约；优化服务启动辅助逻辑；修复前端报告类型联合定义与后端响应对齐问题。

### 修复

- 🔔 **Discord 环境变量向后兼容**（#659）：运行时新增 `DISCORD_CHANNEL_ID` → `DISCORD_MAIN_CHANNEL_ID` 的 fallback 读取；历史配置用户无需修改即可恢复 Discord Bot 通知；全部相关文档与 `.env.example` 对齐。
- 🔧 **GitHub Actions Node 24 升级**（#665）：将所有 GitHub 官方 actions 升级至 Node 24 兼容版本，消除 CI 日志中的 Node.js 20 deprecation warning（影响 2026-06-02 强制升级窗口）。
- 📅 **持仓页默认日期本地化**：手工录入表单默认日期改用本地时间（`getFullYear/Month/Date`），修复 UTC-N 时区用户在当天晚间出现日期偏移的问题。
- 🔁 **CSV 导入去重逻辑加固**：dedup hash 纳入行序号作为区分因子，确保同字段合法分笔成交不被误折叠；同时在 `trade_uid` 存在时也持久化 hash，防止混合来源重复写入。

### 变更

- `POST /api/v1/portfolio/trades` 在同账户内 `trade_uid` 冲突时返回 `409`。
- 持仓风险响应新增 `sector_concentration` 字段（增量扩展），原有 `concentration` 字段保持不变。
- 分析 API `analyze` 接口异步行为契约文档化；前端报告类型联合更新。

### 测试

- 新增持仓核心服务测试（FIFO / AVG 部分卖出、同日事件顺序、重复 `trade_uid` 返回 409、快照 API 契约）。
- 新增 CSV 导入幂等性、合法分笔成交不误去重、去重边界、风险阈值边界、汇率降级行为测试。
- 新增 Agent `get_portfolio_snapshot` 工具调用测试。
- 新增分析 API 异步契约回归测试。

## [3.6.0] - 2026-03-14

### Added
- 📊 **Web UI Design System** — implemented dual-theme architecture and terminal-inspired atomic UI components
- 📊 **UI Components Refactoring** — integrated `clsx` and `tailwind-merge` for robust class composition across Web UI

- 🗑️ **History batch deletion** — Web UI now supports multi-selection and batch deletion of analysis history; added `POST /api/v1/history/batch-delete` endpoint and `ConfirmDialog` component.
- 🔐 **Auth settings API** — new `POST /api/v1/auth/settings` endpoint to enable or disable Web authentication at runtime and set the initial admin password when needed
- openclaw Skill 集成指南 — 新增 [docs/openclaw-skill-integration.md](openclaw-skill-integration.md)，说明如何通过 openclaw Skill 调用 DSA API
- ⚙️ **LLM channel protocol/test UX** — `.env` and Web settings now share the same channel shape (`LLM_CHANNELS` + `LLM_<NAME>_PROTOCOL/BASE_URL/API_KEY/MODELS/ENABLED`); settings page adds per-channel connection testing, primary/fallback/vision model selection, and protocol-aware model prefixing
- 🤖 **Agent architecture Phase 0+1** — shared protocols (`AgentContext`, `AgentOpinion`, `StageResult`), extracted `run_agent_loop()` runner, `AGENT_ARCH` switch (`single`/`multi`), config registry entries
- 🔍 **Bot NL routing** — two-layer natural-language routing: cheap regex pre-filter (stock codes + finance keywords) → lightweight LLM intent parsing; controlled by `AGENT_NL_ROUTING=true`; supports multi-stock and strategy extraction
- 💬 **`/ask` multi-stock analysis** — comma or `vs` separated codes (max 5), parallel thread execution with 150s timeout (preserves partial results), Markdown comparison summary table at top
- 📋 **`/history` command** — per-user session isolation via `{platform}_{user_id}:{scope}` format (colon delimiter prevents prefix collision); lists both `/chat` and `/ask` sessions; view detail or clear
- 📊 **`/strategies` command** — lists available strategy YAML files grouped by category (趋势/形态/反转/框架) with ✅/⬜ activation status
- 🔧 **Backtest summary tools** — `get_strategy_backtest_summary` and `get_stock_backtest_summary` registered as read-only Agent tools
- ⚙️ **Agent auto-detection** — `is_agent_available()` auto-detects from `LITELLM_MODEL`; explicit `AGENT_MODE=true/false` takes full precedence
- 🏗️ **Multi-Agent orchestrator (Phase 2)** — `AgentOrchestrator` with 4 modes (`quick`/`standard`/`full`/`strategy`); drop-in replacement for `AgentExecutor` via `AGENT_ARCH=multi`; `BaseAgent` ABC with tool subset filtering, cached data injection, and structured `AgentOpinion` output
- 🧩 **Specialised agents (Phase 2-4)** — `TechnicalAgent` (8 tools, trend/MA/MACD/volume/pattern analysis), `IntelAgent` (news & sentiment, risk flag propagation), `DecisionAgent` (synthesis into Decision Dashboard JSON), `RiskAgent` (7 risk categories, two-level severity with soft/hard override)
- 📈 **Strategy system (Phase 3)** — `StrategyAgent` (per-strategy evaluation from YAML skills), `StrategyRouter` (rule-based regime detection → strategy selection), `StrategyAggregator` (weighted consensus with backtest performance factor)
- 🔬 **Deep Research agent (Phase 5)** — `ResearchAgent` with 3-phase approach (decompose → research sub-questions → synthesise report); token budget tracking; new `/research` bot command with aliases (`/深研`, `/deepsearch`)
- 🧠 **Memory & calibration (Phase 6)** — `AgentMemory` with prediction accuracy tracking, confidence calibration (activates after minimum sample threshold), strategy auto-weighting based on historical win rate
- 📊 **Portfolio Agent (Phase 7)** — `PortfolioAgent` for multi-stock portfolio analysis (position sizing, sector concentration, correlation risk, cross-market linkage, rebalance suggestions)
- 🔔 **Event-driven alerts (Phase 7)** — `EventMonitor` with `PriceAlert`, `VolumeAlert`, `SentimentAlert` rules; async checking, callback notifications, serializable persistence
- ⚙️ **New config entries** — `AGENT_ORCHESTRATOR_MODE`, `AGENT_RISK_OVERRIDE`, `AGENT_DEEP_RESEARCH_BUDGET`, `AGENT_MEMORY_ENABLED`, `AGENT_STRATEGY_AUTOWEIGHT`, `AGENT_STRATEGY_ROUTING` — all registered in `config.py` + `config_registry.py` (WebUI-configurable)

### Changed
- 🔐 **Auth password state semantics** — stored password existence is now tracked independently from auth enablement; when auth is disabled, `/api/v1/auth/status` returns `passwordSet=false` while preserving the saved password for future re-enable
- 🔐 **Auth settings re-enable hardening** — re-enabling auth with a stored password now requires `currentPassword`, and failed session creation rolls back the auth toggle to avoid lockout
- ♻️ **AgentExecutor refactored** — `_run_loop` delegates to shared `runner.run_agent_loop()`; removed duplicated serialization/parsing/thinking-label code
- ♻️ **Unified agent switch** — Bot, API, and Pipeline all use `config.is_agent_available()` instead of divergent `config.agent_mode` checks
- 📖 **README.md** — expanded Bot commands section (ask/chat/strategies/history), added NL routing note, updated agent mode description
- 📖 **.env.example** — added `AGENT_ARCH` and `AGENT_NL_ROUTING` configuration documentation
- 🔌 **Analysis API async contract** — `POST /api/v1/analysis/analyze` now documents distinct async `202` payloads for single-stock vs batch requests, and `report_type=full` is treated consistently with the existing full-report behavior

### Fixed
- 🐛 **Analysis API blank-code guardrails** — `POST /api/v1/analysis/analyze` now drops whitespace-only entries before batch enqueue and returns `400` when no valid stock code remains
- 🐛 **Bare `/api` SPA fallback** — unknown API paths now return JSON `404` consistently for both `/api/...` and the exact `/api` path
- 🎮 **Discord channel env compatibility** — runtime now accepts legacy `DISCORD_CHANNEL_ID` as a fallback for `DISCORD_MAIN_CHANNEL_ID`, and the docs/examples now use the same variable name as the actual workflow/config implementation
- 🐛 **Session secret rotation on Windows** — use atomic replace so auth toggles invalidate existing sessions even when `.session_secret` already exists
- 🐛 **Auth toggle atomicity** — persist `ADMIN_AUTH_ENABLED` before rotating session secret; on rotation failure, roll back to the previous auth state
- 🔧 **LLM runtime selection guardrails** — YAML 模式下渠道编辑器不再覆盖 `LITELLM_MODEL` / fallback / Vision；系统配置校验补上全部渠道禁用后的运行时来源检查，并修复 `vertexai/...` 这类协议别名模型被重复加前缀的问题
- 🐛 **Multi-stock `/ask` follow-up regressions** — portfolio overlay now shares the same timeout budget as the per-stock phase and is skipped on timeout instead of blocking the bot reply; `/history` now stores the readable per-stock summary instead of raw dashboard JSON; condensed multi-stock output now renders numeric `sniper_points` values
- 🐛 **Decision dashboard enum compatibility** — multi-agent `DecisionAgent` now keeps `decision_type` within the legacy `buy|hold|sell` contract and normalizes stray `strong_*` outputs before risk override, pipeline conversion, and downstream统计/通知汇总
- 🛟 **Multi-Agent partial-result fallback** — `IntelAgent` now caches parsed intel for downstream reuse, shared JSON parsing tolerates lightly malformed model output, and the orchestrator preserves/synthesizes a minimal dashboard on timeout or mid-pipeline parse failure instead of always collapsing to `50/观望/未知`
- 🐛 **Shared LiteLLM routing restored** — bot NL intent parsing and `ResearchAgent` planning/synthesis now reuse the same LiteLLM adapter / Router / fallback / `api_base` injection path as the main Agent flow, so `LLM_CHANNELS` / `LITELLM_CONFIG` / OpenAI-compatible deployments behave consistently
- 🐛 **Bot chat session backward compatibility** — `/chat` now keeps using the legacy `{platform}_{user_id}` session id when old history already exists, and `/history` can still list / view / clear those pre-migration sessions alongside the new `{platform}_{user_id}:chat` format
- 🐛 **EventMonitor unsupported rule rejection** — config validation/runtime loading now reject or skip alert types the monitor cannot actually evaluate yet, so schedule mode no longer silently accepts permanent no-op rules
- 🐛 **P0 基本面聚合稳定性修复** (#614) — 修复 `get_stock_info` 板块语义回归（新增 `belong_boards` 并保留 `boards` 兼容别名）、引入基本面上下文精简返回以控制 token、为基本面缓存增加最大条目淘汰，并补齐 ETF 总体状态聚合与 NaN 板块字段过滤，保证 fail-open 与最小入侵。
- 🔧 **GitHub Actions 搜索引擎环境变量补充** — 工作流新增 `MINIMAX_API_KEYS`、`BRAVE_API_KEYS`、`SEARXNG_BASE_URLS` 环境变量映射，使 GitHub Actions 用户可配置 MiniMax、Brave、SearXNG 搜索服务（此前 v3.5.0 已添加 provider 实现但缺少工作流配置）
- 🤖 **Multi-Agent runtime consistency** — `AGENT_MAX_STEPS` now propagates to each orchestrated sub-agent; added cooperative `AGENT_ORCHESTRATOR_TIMEOUT_S` budget to stop overlong pipelines before they cascade further
- 🔌 **Multi-Agent feature wiring** — `AGENT_RISK_OVERRIDE` now actively downgrades final dashboards on hard risk findings; `AGENT_MEMORY_ENABLED` now injects recent analysis memory + confidence calibration into specialised agents; multi-stock `/ask` now runs `PortfolioAgent` to add portfolio-level allocation and concentration guidance
- 🔔 **EventMonitor runtime wiring** — schedule mode can now load alert rules from `AGENT_EVENT_ALERT_RULES_JSON`, poll them at `AGENT_EVENT_MONITOR_INTERVAL_MINUTES`, and send triggered alerts through the existing notification service
- 🛠️ **Follow-up stability fixes** — multi-stock `/ask` now falls back to usable text output when dashboard JSON parsing fails; EventMonitor skips semantically invalid rules instead of aborting schedule startup; background alert polling now runs independently of the main scheduled analysis loop
- 🧪 **Multi-Agent regression coverage** — added orchestrator execution tests for `run()`, `chat()`, critical-stage failure, graceful degradation, and timeout handling
- 🧹 **PortfolioAgent cleanup** — `post_process()` now reuses shared JSON parsing and removed stale unused imports
- 🚦 **Bot async dispatch** — `CommandDispatcher` now exposes `dispatch_async()`; NL intent parsing and default command execution are offloaded from the event loop, DingTalk stream awaits async handlers directly, and Feishu stream processing is moved off the SDK callback thread
- 🌐 **Async webhook handler** — new `handle_webhook_async()` function in `bot/handler.py` for use from async contexts (e.g. FastAPI); calls `dispatch_async()` directly without thread bridging
- 🧵 **Feishu stream ThreadPoolExecutor** — replaced unbounded per-message `Thread` spawning with a capped `ThreadPoolExecutor(max_workers=8)` to prevent thread explosion under message bursts
- 🔒 **EventMonitor safety** — `_check_volume()` now safely handles `get_daily_data` returning `None` (no tuple-unpacking crash); `on_trigger` callbacks support both sync and async callables via `asyncio.to_thread`/`await`
- 🧹 **ResearchAgent dedup** — `_filtered_registry()` now delegates to `BaseAgent._filtered_registry()` instead of duplicating the filtering logic
- 🧹 **Bot trailing whitespace cleanup** — removed W291/W293 whitespace issues across `bot/handler.py`, `bot/dispatcher.py`, `bot/commands/base.py`, `bot/platforms/feishu_stream.py`, `bot/platforms/dingtalk_stream.py`
- 🐛 **Dispatcher `_parse_intent_via_llm` safety** — replaced fragile `'raw' in dir()` with `'raw' in locals()` for undefined-variable guard in `JSONDecodeError` handler
- 🐛 **筹码结构 LLM 未填写时兜底补全** (#589) — DeepSeek 等模型未正确填写 `chip_structure` 时，自动用数据源已获取的筹码数据补全，保证各模型展示一致；普通分析与 Agent 模式均生效
- 🐛 **历史报告狙击点位显示原始文本** (#452) — 历史详情页现优先展示 `raw_result.dashboard.battle_plan.sniper_points` 中的原始字符串，避免 `analysis_history` 数值列把区间、说明文字或复杂点位压缩成单个数字；保留原有数值列作为回退
- 🐛 **Session prefix collision** — user ID `123` could see sessions of user `1234` via `startswith`; fixed with colon delimiter in session_id format
- 🐛 **NL pre-filter false positives** — `re.IGNORECASE` caused `[A-Z]{2,5}` to match common English words like "hello"; removed global flag, use inline `(?i:...)` only for English finance keywords
- 🐛 **Dotted ticker in strategy args** — `_get_strategy_args()` didn't recognize `BRK.B` as a stock code, leaving it in strategy text; now accepts `TICKER.CLASS` format
- ⏱️ **efinance 长调用挂起修复** (#660) — 为所有 efinance API 调用引入 `_ef_call_with_timeout()` 包装（默认 30 秒，可通过 `EFINANCE_CALL_TIMEOUT` 配置）；使用 `executor.shutdown(wait=False)` 确保超时后不再阻塞主线程，彻底消除 81 分钟挂起问题
- 🛡️ **类型安全内容完整性检查** (#660) — `check_content_integrity()` 现在将非字符串类型的 `operation_advice` / `analysis_summary` 视为缺失字段，避免下游 `get_emoji()` 因 `dict.strip()` 崩溃
- 📄 **报告保存与通知解耦** (#660) — `_save_local_report()` 不再依赖 `send_notification` 标志触发，`--no-notify` 模式下本地报告照常保存
- 🔄 **operation_advice 字典归一化** (#660) — Pipeline 和 BacktestEngine 现在将 LLM 返回的 `dict` 格式 `operation_advice` 通过 `decision_type`（不区分大小写）映射为标准字符串，防止因模型输出格式变化导致崩溃
- 🛡️ **runner.py usage None 防护** (#660) — `response.usage` 为 `None` 时不再抛出 `AttributeError`，回退为 0 token 计数
- 📋 **orchestrator 静默失败改为日志警告** (#660) — `IntelAgent` / `RiskAgent` 阶段失败现在记录 `WARNING` 而非静默跳过，便于诊断

### Notes
- ⚠️ **Multi-worker auth toggles** — runtime auth updates are process-local; multi-worker deployments must restart/roll workers to keep auth state consistent

## [3.5.0] - 2026-03-12

### Added
- 📊 **Web UI full report drawer** (Fixes #214) — history page adds "Full Report" button to display the complete Markdown analysis report in a side drawer; new `GET /api/v1/history/{record_id}/markdown` endpoint
- 📊 **LLM cost tracking** — all LLM calls (analysis, agent, market review) recorded in `llm_usage` table; new `GET /api/v1/usage/summary?period=today|month|all` endpoint returns aggregated token usage by call type and model
- 🔍 **SearXNG search provider** (Fixes #550) — quota-free self-hosted search fallback; priority: Bocha > Tavily > Brave > SerpAPI > MiniMax > SearXNG
- 🔍 **MiniMax web search provider** — `MiniMaxSearchProvider` with circuit breaker (3 failures → 300s cooldown) and dual time-filtering; configured via `MINIMAX_API_KEYS`
- 🤖 **Agent models discovery API** — `GET /api/v1/agent/models` returns available model deployments (primary/fallback/source/api_base) for Web UI model selector
- 🤖 **Agent chat export & send** (#495) — export conversation to .md file; send to configured notification channels; new `POST /api/v1/agent/chat/send`
- 🤖 **Agent background execution** (#495) — analysis continues when switching pages; badge notification on completion; auto-cancel in-progress stream on session switch
- 📝 **Report Engine P0** — Pydantic schema validation for LLM JSON; Jinja2 templates (markdown/wechat/brief) with legacy fallback; content integrity checks with retry; brief mode (`REPORT_TYPE=brief`); history signal comparison
- 📦 **Smart import** — multi-source import from image/CSV/Excel/clipboard; Vision LLM extracts code+name+confidence; name→code resolver (local map + pinyin + AkShare); confidence-tiered confirmation
- ⚙️ **GitHub Actions LiteLLM config** — workflow supports `LITELLM_CONFIG`/`LITELLM_CONFIG_YAML` for flexible AI provider configuration
- ⚙️ **Config engine refactor & system API** (#602) — unified config registry, validation and API exposure
- 📖 **LLM configuration guide** — new `docs/LLM_CONFIG_GUIDE.md` covering 3-tier config, quick start, Vision/Agent/troubleshooting

### Fixed
- 🐛 **analyze_trend always reports No historical data** (#600) — now fetches from DB/DataFetcher instead of broken `get_analysis_context`
- 🐛 **Chip structure fallback when LLM omits it** (#589) — auto-fills from data source chip data for consistent display across models
- 🐛 **History sniper points show raw text** (#452) — prioritizes original strings over compressed numeric values
- 🐛 **GitHub Actions ENABLE_CHIP_DISTRIBUTION configurable** (#617) — no longer hardcoded, supports vars/secrets override
- 🐛 **`.env` save preserves comments and blank lines** — Web settings no longer destroys `.env` formatting
- 🐛 **Agent model discovery fixes** — legacy mode includes LiteLLM-native providers; source detection aligned with runtime; fallback deployments no longer expanded per-key
- 🐛 **Stooq US stock previous close semantics** — no longer misuses open price as previous close
- 🐛 **Stock name prefetch regression** — prioritizes local `STOCK_NAME_MAP` before remote queries
- 🐛 **AkShare limit-up/down calculation** (#555) — fixed market analysis statistics
- 🐛 **AkShare Tencent source field index & ETF quote mapping** (#579)
- 🐛 **Pytdx stock name cache pagination** (#573) — prevents cache overflow
- 🐛 **PushPlus oversized report chunking** (#489) — auto-segments long content
- 🐛 **Agent chat cancel & switch** (#495) — cancel no longer misreports as failure; fast switch no longer overwrites stream state
- 🐛 **MiniMax search status in `/status` command** (#587)
- 🐛 **config_registry duplicate BOCHA_API_KEYS** — removed duplicate dict entry that silently overwrote config

### Changed
- 🔎 **Fetcher failure observability** — logs record start/success/failure with elapsed time, failover transitions; Efinance/Akshare include upstream endpoint and classified failure categories
- ♻️ **Data source resilience & cleanup** (#602) — fallback chain optimization
- ♻️ **Image extract API response extension** — new `items` field (code/name/confidence); `codes` preserved for backward compatibility
- ♻️ **Import parse error messages** — specific failure reasons for Excel/CSV; improved logging with file type and size

### Docs
- 📖 LLM config guide refactored for clarity (#583)
- 📖 `image-extract-prompt.md` with full prompt documentation
- 📖 AkShare fallback cache TTL documentation
## [3.4.10] - 2026-03-07

### Fixed
- 🐛 **EfinanceFetcher ETF OHLCV data** (#541, #527) — switch `_fetch_etf_data` from `ef.fund.get_quote_history` (NAV-only, no OHLCV, no `beg`/`end` params) to `ef.stock.get_quote_history`; ETFs now return proper open/high/low/close/volume/amount instead of zeros; remove obsolete NAV column mappings from `_normalize_data`
- 🐛 **tiktoken 0.12.0 `Unknown encoding cl100k_base`** (#537) — pin `tiktoken>=0.8.0,<0.12.0` in requirements.txt to avoid plugin-registration regression introduced in 0.12.0
- 🐛 **Web UI API error classification** (#540) — frontend no longer treats every HTTP 400 as the same "server/network" failure; now distinguishes Agent disabled / missing params / model-tool incompatibility / upstream LLM errors / local connection failures
- 🐛 **北交所代码识别失败** (#491, #533) — 8/4/92 开头的 6 位代码现正确识别为北交所；Tushare/Akshare/Yfinance 等数据源支持 .BJ 或 bj 前缀；Baostock/Pytdx 对北交所代码显式切换数据源；避免误判上海 B 股 900xxx
- 🐛 **狙击点位解析错误** (#488, #532) — 理想买入/二次买入等字段在无「元」字时误提取括号内技术指标数字；现先截去第一个括号后内容再提取

### Added
- **Markdown-to-image for dashboard report** (#455, #535) — 个股日报汇总支持 markdown 转图片推送（Telegram、WeChat、Custom、Email），与大盘复盘行为一致
- **markdown-to-file engine** (#455) — `MD2IMG_ENGINE=markdown-to-file` 可选，对 emoji 支持更好，需 `npm i -g markdown-to-file`
- **PREFETCH_REALTIME_QUOTES** (#455) — 设为 `false` 可禁用实时行情预取，避免 efinance/akshare_em 全市场拉取
- **Stock name prefetch** (#455) — 分析前预取股票名称，减少报告中「股票xxxxx」占位符
- 📊 **分析报告模型标记** (#528, #534) — 在分析报告 meta、报告末尾、推送内容中展示 `model_used`（完整 LLM 模型名）；Agent 多轮调用时记录并展示每轮实际使用的模型（支持 fallback 切换）

### Changed
- **Enhanced markdown-to-image failure warning** (#455) — 转图失败时提示具体依赖（wkhtmltopdf 或 m2f）
- **WeChat-only image routing optimization** (#455) — 仅配置企业微信图片时，不再对完整报告做冗余转图，避免误导性失败日志
- **Stock name prefetch lightweight mode** (#455) — 名称预取阶段跳过 realtime quote 查询，减少额外网络开销

## [3.4.9] - 2026-03-06

### Added
- 🧠 **Structured config validation** — `ConfigIssue` dataclass and `validate_structured()` with severity-aware logging; `CONFIG_VALIDATE_MODE=strict` aborts startup on errors
- 🖼️ **Vision model config** — `VISION_MODEL` and `VISION_PROVIDER_PRIORITY` for image stock extraction; provider fallback (Gemini → Anthropic → OpenAI → DeepSeek) when primary fails
- 🚀 **CLI init wizard** — `python -m dsa init` 3-step interactive bootstrap (model → data source → notification), 9 provider presets, incremental merge by default
- 🔧 **Multi-channel LLM support** with visual channel editor (#494)

### Changed
- ♻️ **Vision extraction** — migrated from gemini-3 hardcode to `litellm.completion()` with configurable model and provider fallback; `OPENAI_VISION_MODEL` deprecated in favor of `VISION_MODEL`
- ♻️ **Market analyzer** — uses `Analyzer.generate_text()` for LLM calls; fixes bypass and Anthropic `AttributeError` when using non-Router path
- ♻️ **Config validation refinements** — test_env output format syncs with `validate_structured` (severity-aware ✓/✗/⚠/·); Vision key warning when `VISION_MODEL` set but no provider API key; market_analyzer test covers `generate_market_review` fallback when `generate_text` returns None
- ⚙️ **Auto-tag workflow defaults to NO tag** — only tags when commit message explicitly contains `#patch`, `#minor`, or `#major`
- ♻️ **Formatter and notification refactor** (#516)

### Fixed
- 🐛 **STOCK_LIST not refreshed on scheduled runs** — `.env` or WebUI changes to `STOCK_LIST` now hot-reload before each scheduled analysis (#529)
- 🐛 **WebUI fails to load with MIME type error** — SPA fallback route now resolves correct `Content-Type` for JS/CSS files (#520)
- 🐛 **AstrBot sender docstring misplaced** — `import time` placed before docstring in `_send_astrbot`, causing it to become dead code
- 🐛 **Telegram Markdown link escaping** — `_convert_to_telegram_markdown` escaped `[]()` characters, breaking all Markdown links in reports
- 🐛 **Duplicate `discord_bot_status` field** in Config dataclass — second declaration silently shadowed the first
- 🧹 **Unused imports** — removed `shutil`/`subprocess` from `main.py`
- 🔧 **Config validation and Vision key check** (#525)

### Docs
- 📝 Clarified GitHub Actions non-trading-day manual run controls (`TRADING_DAY_CHECK_ENABLED` + `force_run`) for Issue #461 / PR #466

## [3.4.8] - 2026-03-02

### Fixed
- 🐛 **Desktop exe crashes on startup with `FileNotFoundError`** — PyInstaller build was missing litellm's JSON data files (e.g. `model_prices_and_context_window_backup.json`). Added `--collect-data litellm` to both Windows and macOS build scripts so the files are correctly bundled in the executable.

### CI
- 🔧 Cache Electron binaries on macOS CI runners to prevent intermittent EOF download failures when fetching `electron-vX.Y.Z-darwin-*.zip` from GitHub CDN
- 🔧 Fix macOS DMG `hdiutil Resource busy` error during desktop packaging

### Docs
- 📝 Clarify non-trading-day manual run controls for GitHub Actions (`TRADING_DAY_CHECK_ENABLED` + `force_run`) (#474)

## [3.4.7] - 2026-02-28

### Added
- 🧠 **CN/US Market Strategy Blueprint System** (#395) — market review prompt injects region-specific strategy blueprints with position sizing and risk trigger recommendations

### Fixed
- 🐛 **`TRADING_DAY_CHECK_ENABLED` env var and `--force-run` for GitHub Actions** (#466)
- 🐛 **Agent pipeline preserved resolved stock names** (#464) — placeholder names no longer leak into reports
- 🐛 **Code cleanup** (#462, Fixes #422)
- 🐛 **WebUI auto-build on startup** (#460)
- 🐛 **ARCH_ARGS unbound variable** (#458)
- 🐛 **Time zone inconsistency & right panel flash** (#439)

### Docs
- 📝 Clarify potential ambiguities in code (#343)
- 📝 ENABLE_EASTMONEY_PATCH guidance for Issue #453 (#456)

## [3.4.0] - 2026-02-27

### Added
- 📡 **LiteLLM Direct Integration + Multi API Key Support** (#454, Fixes #421 #428)
  - Removed native SDKs (google-generativeai, google-genai, anthropic); unified through `litellm>=1.80.10`
  - New config: `LITELLM_MODEL`, `LITELLM_FALLBACK_MODELS`, `GEMINI_API_KEYS`, `ANTHROPIC_API_KEYS`, `OPENAI_API_KEYS`
  - Multi-key auto-builds LiteLLM Router (simple-shuffle) with 429 cooldown
  - **Breaking**: `.env` `GEMINI_MODEL` (no prefix) only for fallback; explicit config must include provider prefix

### Changed
- ♻️ **Notification Refactoring** (#435) — extracted 10 sender classes into `src/notification_sender/`

### Fixed
- 🐛 LLM NoneType crash, history API 422, sniper points extraction
- 🐛 Auto-build frontend on WebUI startup — `WEBUI_AUTO_BUILD` env var (default `true`)
- 🐛 Docker explicit project name (#448)
- 🐛 Bocha search SSL retry (#445, #446) — transient errors retry up to 3 times
- 🐛 Gemini google-genai SDK migration (Fixes #440, #444)
- 🐛 Mobile home page scrolling (Fixes #419, #433)
- 🐛 History list scroll reset (#431)
- 🐛 Settings save button false positive (fixes #417, #430)

## [3.3.22] - 2026-02-26

### Added
- 💬 **Chat History Persistence** (Fixes #400, #414) — `/chat` page survives refresh, sidebar session list
- 🎨 Project VI Assets — logo icon set, PSD, vector, banner (#425)
- 🚀 Desktop CI Auto-Release (#426) — Windows + macOS parallel builds

### Fixed
- 🐛 Agent Reasoning 400 & LiteLLM Proxy (fixes #409, #427)
- 🐛 Discord chunked sending (#413) — `DISCORD_MAX_WORDS` config
- 🐛 yfinance shared DataFrame (#412)
- 🐛 sniper_points parsing (#408)
- 🐛 Agent framework category missing (#406)
- 🐛 Date inconsistency & query id (fixes #322, #363)

## [3.3.12] - 2026-02-24

### Added
- 📈 **Intraday Realtime Technical Indicators** (Issue #234, #397) — MA calculated from realtime price, config: `ENABLE_REALTIME_TECHNICAL_INDICATORS`
- 🤖 **Agent Strategy Chat** (#367) — full ReAct pipeline, 11 YAML strategies, SSE streaming, multi-turn chat
- 📢 PushPlus Group Push — `PUSHPLUS_TOPIC` (#402)
- 📅 Trading Day Check (Issue #373, #375) — `TRADING_DAY_CHECK_ENABLED`, `--force-run`

### Fixed
- 🐛 DeepSeek reasoning mode (Issue #379, #386)
- 🐛 Agent news intel persistence (Fixes #396, #405)
- 🐛 Bare except clauses replaced with `except Exception` (#398)
- 🐛 UUID fallback for HTTP non-secure context (fixes #377, #381)
- 🐛 Docker DNS resolution (Fixes #372, #374)
- 🐛 Agent session/strategy bugs — multiple follow-up fixes for #367
- 🐛 yfinance parallel download data filtering

### Changed
- Market review strategy consistency — unified cn/us template
- Agent test assertions updated (`6 -> 11`)


## [3.2.11] - 2026-02-23

### 修复（#patch）
- 🐛 **StockTrendAnalyzer 从未执行** (Issue #357)
  - 根因：`get_analysis_context` 仅返回 2 天数据且无 `raw_data`，pipeline 中 `raw_data in context` 始终为 False
  - 修复：Step 3 直接调用 `get_data_range` 获取 90 日历天（约 60 交易日）历史数据用于趋势分析
  - 改善：趋势分析失败时用 `logger.warning(..., exc_info=True)` 记录完整 traceback

## [3.2.10] - 2026-02-22

### 新增
- ⚙️ 支持 `RUN_IMMEDIATELY` 配置项，设为 `true` 时定时任务触发后立即执行一次分析，无需等待首个定时点

### 修复
- 🐛 修复 Web UI 页面居中问题
- 🐛 修复 Settings 返回 500 错误

## [3.2.9] - 2026-02-22

### 修复
- 🐛 **ETF 分析仅关注指数走势**（Issue #274）
  - 美股/港股 ETF（如 VOO、QQQ）与 A 股 ETF 不再纳入基金公司层面风险（诉讼、声誉等）
  - 搜索维度：ETF/指数专用 risk_check、earnings、industry 查询，避免命中基金管理人新闻
  - AI 提示：指数型标的分析约束，`risk_alerts` 不得出现基金管理人公司经营风险

## [3.2.8] - 2026-02-21

### 修复
- 🐛 **BOT 与 WEB UI 股票代码大小写统一**（Issue #355）
  - BOT `/analyze` 与 WEB UI 触发分析的股票代码统一为大写（如 `aapl` → `AAPL`）
  - 新增 `canonical_stock_code()`，在 BOT、API、Config、CLI、task_queue 入口处规范化
  - 历史记录与任务去重逻辑可正确识别同一股票（大小写不再影响）

## [3.2.7] - 2026-02-20

### 新增
- 🔐 **Web 页面密码验证**（Issue #320, #349）
  - 支持 `ADMIN_AUTH_ENABLED=true` 启用 Web 登录保护
  - 首次访问在网页设置初始密码；支持「系统设置 > 修改密码」和 CLI `python -m src.auth reset_password` 重置

## [3.2.6] - 2026-02-20
### ⚠️ 破坏性变更（Breaking Changes）

- **历史记录 API 变更 (Issue #322)**
  - 路由变更：`GET /api/v1/history/{query_id}` → `GET /api/v1/history/{record_id}`
  - 参数变更：`query_id` (字符串) → `record_id` (整数)
  - 新闻接口变更：`GET /api/v1/history/{query_id}/news` → `GET /api/v1/history/{record_id}/news`
  - 原因：`query_id` 在批量分析时可能重复，无法唯一标识单条历史记录。改用数据库主键 `id` 确保唯一性
  - 影响范围：使用旧版历史详情 API 的所有客户端需同步更新

### 修复
- 修复美股（如 ADBE）技术指标矛盾：akshare 美股复权数据异常，统一美股历史数据源为 YFinance（Issue #311）
- 🐛 **历史记录查询和显示问题 (Issue #322)**
  - 修复历史记录列表查询中日期不一致问题：使用明天作为 endDate，确保包含今天全天的数据
  - 修复服务器 UI 报告选择问题：原因是多条记录共享同一 `query_id`，导致总是显示第一条。现改用 `analysis_history.id` 作为唯一标识
  - 历史详情、新闻接口及前端组件已全面适配 `record_id`
  - 新增后台轮询（每 30s）与页面可见性变更时静默刷新历史列表，确保 CLI 发起的分析完成后前端能及时同步，使用 `silent` 模式避免触发 loading 状态
- 🐛 **美股指数实时行情与日线数据** (Issue #273)
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 ^GSPC）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源
  - 消除重复的美股识别逻辑，统一使用 `is_us_stock_code()` 函数

### 优化
- 🎨 **首页输入栏与 Market Sentiment 布局对齐优化**
  - 股票代码输入框左缘与历史记录 glass-card 框左对齐
  - 分析按钮右缘与 Market Sentiment 外框右对齐
  - Market Sentiment 卡片向下拉伸填满格子，消除与 STRATEGY POINTS 之间的空隙
  - 窄屏时输入栏填满宽度，响应式对齐保持一致

## [3.2.5] - 2026-02-19

### 新增
- 🌍 **大盘复盘可选区域**（Issue #299）
  - 支持 `MARKET_REVIEW_REGION` 环境变量：`cn`（A股）、`us`（美股）、`both`（两者）
  - us 模式使用 SPX/纳斯达克/道指/VIX 等指数；both 模式可同时复盘 A 股与美股
  - 默认 `cn`，保持向后兼容

## [3.2.4] - 2026-02-18

### 修复
- 🐛 **统一美股数据源为 YFinance**（Issue #311）
  - akshare 美股复权数据异常，统一美股历史数据源为 YFinance
  - 修复 ADBE 等美股股票技术指标矛盾问题

## [3.2.3] - 2026-02-18

### 修复
- 🐛 **标普500实时数据缺失**（Issue #273）
  - 修复 SPX、DJI、IXIC、NDX、VIX、RUT 等美股指数无法获取实时行情的问题
  - 新增 `us_index_mapping` 模块，将用户输入（如 SPX）映射为 Yahoo Finance 符号（如 `^GSPC`）
  - 美股指数与美股股票日线数据直接路由至 YfinanceFetcher，避免遍历不支持的数据源

## [3.2.2] - 2026-02-16

### 新增
- 📊 **PE 指标支持**（Issue #296）
  - AI System Prompt 增加 PE 估值关注
- 📰 **新闻时效性筛查**（Issue #296）
  - `NEWS_MAX_AGE_DAYS`：新闻最大时效（天），默认 3，避免使用过时信息
- 📈 **强势趋势股乖离率放宽**（Issue #296）
  - `BIAS_THRESHOLD`：乖离率阈值（%），默认 5.0，可配置
  - 强势趋势股（多头排列且趋势强度 ≥70）自动放宽乖离率到 1.5 倍

## [3.2.1] - 2026-02-16

### 新增
- 🔧 **东财接口补丁可配置开关**
  - 支持 `EFINANCE_PATCH_ENABLED` 环境变量开关东财接口补丁（默认 `true`）
  - 补丁不可用时可降级关闭，避免影响主流程

## [3.2.0] - 2026-02-15

### 新增
- 🔒 **CI 门禁统一（P0）**
  - 新增 `scripts/ci_gate.sh` 作为后端门禁单一入口
  - 主 CI 改为 `backend-gate`、`docker-build`、`web-gate` 三段式
  - CI 触发改为所有 PR，避免 Required Checks 因路径过滤缺失而卡住合并
  - `web-gate` 支持前端路径变更按需触发
  - 新增 `network-smoke` 工作流承载非阻断网络场景回归
- 📦 **发布链路收敛（P0）**
  - `docker-publish` 调整为 tag 主触发，并增加发布前门禁校验
  - 手动发布增加 `release_tag` 输入与 semver/changelog 强校验
  - 发布前新增 Docker smoke（关键模块导入）
- 📝 **PR 模板升级（P0）**
  - 增加背景、范围、验证命令与结果、回滚方案、Issue 关联等必填项
- 🤖 **AI 审查覆盖增强（P0）**
  - `pr-review` 纳入 `.github/workflows/**` 范围
  - 新增 `AI_REVIEW_STRICT` 开关，可选将 AI 审查失败升级为阻断

## [3.1.13] - 2026-02-15

### 新增
- 📊 **仅分析结果摘要**（Issue #262）
  - 支持 `REPORT_SUMMARY_ONLY` 环境变量，设为 `true` 时只推送汇总，不含个股详情
  - 默认 `false`，多股时适合快速浏览

## [3.1.12] - 2026-02-15

### 新增
- 📧 **个股与大盘复盘合并推送**（Issue #190）
  - 支持 `MERGE_EMAIL_NOTIFICATION` 环境变量，设为 `true` 时将个股分析与大盘复盘合并为一次推送
  - 默认 `false`，减少邮件数量、降低被识别为垃圾邮件的风险

## [3.1.11] - 2026-02-15

### 新增
- 🤖 **Anthropic Claude API 支持**（Issue #257）
  - 支持 `ANTHROPIC_API_KEY`、`ANTHROPIC_MODEL`、`ANTHROPIC_TEMPERATURE`、`ANTHROPIC_MAX_TOKENS`
  - AI 分析优先级：Gemini > Anthropic > OpenAI
- 📷 **从图片识别股票代码**（Issue #257）
  - 上传自选股截图，通过 Vision LLM 自动提取股票代码
  - API: `POST /api/v1/stocks/extract-from-image`；支持 JPEG/PNG/WebP/GIF，最大 5MB
  - 支持 `OPENAI_VISION_MODEL` 单独配置图片识别模型
- ⚙️ **通达信数据源手动配置**（Issue #257）
  - 支持 `PYTDX_HOST`、`PYTDX_PORT` 或 `PYTDX_SERVERS` 配置自建通达信服务器

## [3.1.10] - 2026-02-15

### 新增
- ⚙️ **立即运行配置**（Issue #332）
  - 支持 `RUN_IMMEDIATELY` 环境变量，`true` 时定时任务启动后立即执行一次
- 🐛 修复 Docker 构建问题

## [3.1.9] - 2026-02-14

### 新增
- 🔌 **东财接口补丁机制**
  - 新增 `patch/eastmoney_patch.py` 修复 efinance 上游接口变更
  - 不影响其他数据源的正常运行

## [3.1.8] - 2026-02-14

### 新增
- 🔐 **Webhook 证书校验开关**（Issue #265）
  - 支持 `WEBHOOK_VERIFY_SSL` 环境变量，可关闭 HTTPS 证书校验以支持自签名证书
  - 默认保持校验，关闭存在 MITM 风险，仅建议在可信内网使用

## [3.1.7] - 2026-02-14

### 修复
- 🐛 修复包导入错误（package import error）

## [3.1.6] - 2026-02-13

### 修复
- 🐛 修复 `news_intel` 中 `query_id` 不一致问题

## [3.1.5] - 2026-02-13

### 新增
- 📷 **Markdown 转图片通知**（Issue #289）
  - 支持 `MARKDOWN_TO_IMAGE_CHANNELS` 配置，对 Telegram、企业微信、自定义 Webhook（Discord）、邮件发送图片格式报告
  - 邮件为内联附件，增强对不支持 HTML 客户端的兼容性
  - 需安装 `wkhtmltopdf` 和 `imgkit`

## [3.1.4] - 2026-02-12

### 新增
- 📧 **股票分组发往不同邮箱**（Issue #268）
  - 支持 `STOCK_GROUP_N` + `EMAIL_GROUP_N` 配置，不同股票组报告发送到对应邮箱
  - 大盘复盘发往所有配置的邮箱

## [3.1.3] - 2026-02-12

### 修复
- 🐛 修复 Docker 内运行时通过页面修改配置报错 `[Errno 16] Device or resource busy` 的问题

## [3.1.2] - 2026-02-11

### 修复
- 🐛 修复 Docker 一致性问题，解决关键批次处理与通知 Bug

## [3.1.1] - 2026-02-11

### 变更
- ♻️ `API_HOST` → `WEBUI_HOST`：Docker Compose 配置项统一

## [3.1.0] - 2026-02-11

### 新增
- 📊 **ETF 支持增强与代码规范化**
  - 统一各数据源 ETF 代码处理逻辑
  - 新增 `canonical_stock_code()` 统一代码格式，确保数据源路由正确

## [3.0.5] - 2026-02-08

### 修复
- 🐛 修复信号 emoji 与建议不一致的问题（复合建议如"卖出/观望"未正确映射）
- 🐛 修复 `*ST` 股票名在微信/Dashboard 中 markdown 转义问题
- 🐛 修复 `idx.amount` 为 None 时大盘复盘 TypeError
- 🐛 修复分析 API 返回 `report=None` 及 ReportStrategy 类型不一致问题
- 🐛 修复 Tushare 返回类型错误（dict → UnifiedRealtimeQuote）及 API 端点指向

### 新增
- 📊 大盘复盘报告注入结构化数据（涨跌统计、指数表格、板块排名）
- 🔍 搜索结果 TTL 缓存（500 条上限，FIFO 淘汰）
- 🔧 Tushare Token 存在时自动注入实时行情优先级
- 📰 新闻摘要截断长度 50→200 字

### 优化
- ⚡ 补充行情字段请求限制为最多 1 次，减少无效请求

## [3.0.4] - 2026-02-07

### 新增
- 📈 **回测引擎** (PR #269)
  - 新增基于历史分析记录的回测系统，支持收益率、胜率、最大回撤等指标评估
  - WebUI 集成回测结果展示

## [3.0.3] - 2026-02-07

### 修复
- 🐛 修复狙击点位数据解析错误问题 (PR #271)

## [3.0.2] - 2026-02-06

### 新增
- ✉️ 可配置邮件发送者名称 (PR #272)
- 🌐 外国股票支持英文关键词搜索

## [3.0.1] - 2026-02-06

### 修复
- 🐛 修复 ETF 实时行情获取、市场数据回退、企业微信消息分块问题
- 🔧 CI 流程简化

## [3.0.0] - 2026-02-06

### 移除
- 🗑️ **移除旧版 WebUI**
  - 删除基于 `http.server.ThreadingHTTPServer` 的旧版 WebUI（`web/` 包）
  - 旧版 WebUI 的功能已完全被 FastAPI（`api/`）+ React 前端替代
  - `--webui` / `--webui-only` 命令行参数标记为弃用，自动重定向到 `--serve` / `--serve-only`
  - `WEBUI_ENABLED` / `WEBUI_HOST` / `WEBUI_PORT` 环境变量保持兼容，自动转发到 FastAPI 服务
  - `webui.py` 保留为兼容入口，启动时直接调用 FastAPI 后端
  - Docker Compose 中移除 `webui` 服务定义，统一使用 `server` 服务

### 变更
- ♻️ **服务层重构**
  - 将 `web/services.py` 中的异步任务服务迁移至 `src/services/task_service.py`
  - Bot 分析命令（`bot/commands/analyze.py`）改为使用 `src.services.task_service`
  - Docker 环境变量 `WEBUI_HOST`/`WEBUI_PORT` 更名为 `API_HOST`/`API_PORT`（旧名仍兼容）

## [2.3.0] - 2026-02-01

### 新增
- 🇺🇸 **增强美股支持** (Issue #153)
  - 实现基于 Akshare 的美股历史数据获取 (`ak.stock_us_daily()`)
  - 实现基于 Yfinance 的美股实时行情获取（优先策略）
  - 增加对不支持数据源（Tushare/Baostock/Pytdx/Efinance）的美股代码过滤和快速降级

### 修复
- 🐛 修复 AMD 等美股代码被误识别为 A 股的问题 (Issue #153)

## [2.2.5] - 2026-02-01

### 新增
- 🤖 **AstrBot 消息推送** (PR #217)
  - 新增 AstrBot 通知渠道，支持推送到 QQ 和微信
  - 支持 HMAC SHA256 签名验证，确保通信安全
  - 通过 `ASTRBOT_URL` 和 `ASTRBOT_TOKEN` 配置

## [2.2.4] - 2026-02-01

### 新增
- ⚙️ **可配置数据源优先级** (PR #215)
  - 支持通过环境变量（如 `YFINANCE_PRIORITY=0`）动态调整数据源优先级
  - 无需修改代码即可优先使用特定数据源（如 Yahoo Finance）

## [2.2.3] - 2026-01-31

### 修复
- 📦 更新 requirements.txt，增加 `lxml_html_clean` 依赖以解决兼容性问题

## [2.2.2] - 2026-01-31

### 修复
- 🐛 修复代理配置区分大小写问题 (fixes #211)

## [2.2.1] - 2026-01-31

### 修复
- 🐛 **YFinance 兼容性修复** (PR #210, fixes #209)
  - 修复新版 yfinance 返回 MultiIndex 列名导致的数据解析错误

## [2.2.0] - 2026-01-31

### 新增
- 🔄 **多源回退策略增强**
  - 实现了更健壮的数据获取回退机制 (feat: multi-source fallback strategy)
  - 优化了数据源故障时的自动切换逻辑

### 修复
- 🐛 修复 analyzer 运行后无法通过改 .env 文件的 stock_list 内容调整跟踪的股票

## [2.1.14] - 2026-01-31

### 文档
- 📝 更新 README 和优化 auto-tag 规则

## [2.1.13] - 2026-01-31

### 修复
- 🐛 **Tushare 优先级与实时行情** (Fixed #185)
  - 修复 Tushare 数据源优先级设置问题
  - 修复 Tushare 实时行情获取功能

## [2.1.12] - 2026-01-30

### 修复
- 🌐 修复代理配置在某些情况下的区分大小写问题
- 🌐 修复本地环境禁用代理的逻辑

## [2.1.11] - 2026-01-30

### 优化
- 🚀 **飞书消息流优化** (PR #192)
  - 优化飞书 Stream 模式的消息类型处理
  - 修改 Stream 消息模式默认为关闭，防止配置错误运行时报错

## [2.1.10] - 2026-01-30

### 合并
- 📦 合并 PR #154 贡献

## [2.1.9] - 2026-01-30

### 新增
- 💬 **微信文本消息支持** (PR #137)
  - 新增微信推送的纯文本消息类型支持
  - 添加 `WECHAT_MSG_TYPE` 配置项

## [2.1.8] - 2026-01-30

### 修复
- 🐛 修正日志中 API 提供商显示错误 (PR #197)

## [2.1.7] - 2026-01-30

### 修复
- 🌐 禁用本地环境的代理设置，避免网络连接问题

## [2.1.6] - 2026-01-29

### 新增
- 📡 **Pytdx 数据源 (Priority 2)**
  - 新增通达信数据源，免费无需注册
  - 多服务器自动切换
  - 支持实时行情和历史数据
- 🏷️ **多源股票名称解析**
  - DataFetcherManager 新增 `get_stock_name()` 方法
  - 新增 `batch_get_stock_names()` 批量查询
  - 自动在多数据源间回退
  - Tushare 和 Baostock 新增股票名称/列表方法
- 🔍 **增强搜索回退**
  - 新增 `search_stock_price_fallback()` 用于数据源全部失败时
  - 新增搜索维度：市场分析、行业分析
  - 最大搜索次数从 3 增加到 5
  - 改进搜索结果格式（每维度 4 条结果）

### 改进
- 更新搜索查询模板以提高相关性
- 增强 `format_intel_report()` 输出结构

## [2.1.5] - 2026-01-29

### 新增
- 📡 新增 Pytdx 数据源和多源股票名称解析功能

## [2.1.4] - 2026-01-29

### 文档
- 📝 更新赞助商信息

## [2.1.3] - 2026-01-28

### 文档
- 📝 重构 README 布局
- 🌐 新增繁体中文翻译 (README_CHT.md)

### 修复
- 🐛 修复 WebUI 无法输入美股代码问题
  - 输入框逻辑改成所有字母都转换成大写
  - 支持 `.` 的输入（如 `BRK.B`）

## [2.1.2] - 2026-01-27

### 修复
- 🐛 修复个股分析推送失败和报告路径问题 (fixes #166)
- 🐛 修改 CR 错误，确保微信消息最大字节配置生效

## [2.1.1] - 2026-01-26

### 新增
- 🔧 添加 GitHub Actions auto-tag 工作流
- 📡 添加 yfinance 兜底数据源及数据缺失警告

### 修复
- 🐳 修复 docker-compose 路径和文档命令
- 🐳 Dockerfile 补充 copy src 文件夹 (fixes #145)

## [2.1.0] - 2026-01-25

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 📈 **MACD 和 RSI 技术指标**
  - MACD：趋势确认、金叉死叉信号（零轴上金叉⭐、金叉✅、死叉❌）
  - RSI：超买超卖判断（超卖⭐、强势✅、超买⚠️）
  - 指标信号纳入综合评分系统
- 🎮 **Discord 推送支持** (PR #124, #125, #144)
  - 支持 Discord Webhook 和 Bot API 两种方式
  - 通过 `DISCORD_WEBHOOK_URL` 或 `DISCORD_BOT_TOKEN` + `DISCORD_MAIN_CHANNEL_ID` 配置
- 🤖 **机器人命令交互**
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
- 🌡️ **AI 温度参数可配置** (PR #142)
  - 支持自定义 AI 模型温度参数
- 🐳 **Zeabur 部署支持**
  - 添加 Zeabur 镜像部署工作流
  - 支持 commit hash 和 latest 双标签

### 重构
- 🏗️ **项目结构优化**
  - 核心代码移至 `src/` 目录，根目录更清爽
  - 文档移至 `docs/` 目录
  - Docker 配置移至 `docker/` 目录
  - 修复所有 import 路径，保持向后兼容
- 🔄 **数据源架构升级**
  - 新增数据源熔断机制，单数据源连续失败自动切换
  - 实时行情缓存优化，批量预取减少 API 调用
  - 网络代理智能分流，国内接口自动直连
- 🤖 Discord 机器人重构为平台适配器架构

### 修复
- 🌐 **网络稳定性增强**
  - 自动检测代理配置，对国内行情接口强制直连
  - 修复 EfinanceFetcher 偶发的 `ProtocolError`
  - 增加对底层网络错误的捕获和重试机制
- 📧 **邮件渲染优化**
  - 修复邮件中表格不渲染问题 (#134)
  - 优化邮件排版，更紧凑美观
- 📢 **企业微信推送修复**
  - 修复大盘复盘推送不完整问题
  - 增强消息分割逻辑，支持更多标题格式
  - 增加分批发送间隔，避免限流丢失
- 👷 **CI/CD 修复**
  - 修复 GitHub Actions 中路径引用的错误

## [2.0.0] - 2026-01-24

### 新增
- 🇺🇸 **美股分析支持**
  - 支持美股代码直接输入（如 `AAPL`, `TSLA`）
  - 使用 YFinance 作为美股数据源
- 🤖 **机器人命令交互** (PR #113)
  - 钉钉机器人支持 `/分析 股票代码` 命令触发分析
  - 支持 Stream 长连接模式
  - 支持选择精简报告或完整报告
- 🎮 **Discord 推送支持** (PR #124)
  - 支持 Discord Webhook 推送
  - 添加 Discord 环境变量到工作流

### 修复
- 🐳 修复 WebUI 在 Docker 中绑定 0.0.0.0 (fixed #118)
- 🔔 修复飞书长连接通知问题
- 🐛 修复 `analysis_delay` 未定义错误
- 🔧 启动时 config.py 检测通知渠道，修复已配置自定义渠道情况下仍然提示未配置问题

### 改进
- 🔧 优化 Tushare 优先级判断逻辑，提升封装性
- 🔧 修复 Tushare 优先级提升后仍排在 Efinance 之后的问题
- ⚙️ 配置 TUSHARE_TOKEN 时自动提升 Tushare 数据源优先级
- ⚙️ 实现 4 个用户反馈 issue (#112, #128, #38, #119)

## [1.6.0] - 2026-01-19

### 新增
- 🖥️ WebUI 管理界面及 API 支持（PR #72）
  - 全新 Web 架构：分层设计（Server/Router/Handler/Service）
  - 核心 API：支持 `/analysis` (触发分析), `/tasks` (查询进度), `/health` (健康检查)
  - 交互界面：支持页面直接输入代码并触发分析，实时展示进度
  - 运行模式：新增 `--webui-only` 模式，仅启动 Web 服务
  - 解决了 [#70](https://github.com/ZhuLinsen/daily_stock_analysis/issues/70) 的核心需求（提供触发分析的接口）
- ⚙️ GitHub Actions 配置灵活性增强（[#79](https://github.com/ZhuLinsen/daily_stock_analysis/issues/79)）
  - 支持从 Repository Variables 读取非敏感配置（如 STOCK_LIST, GEMINI_MODEL）
  - 保持对 Secrets 的向下兼容

### 修复
- 🐛 修复企业微信/飞书报告截断问题（[#73](https://github.com/ZhuLinsen/daily_stock_analysis/issues/73)）
  - 移除 notification.py 中不必要的长度硬截断逻辑
  - 依赖底层自动分片机制处理长消息
- 🐛 修复 GitHub Workflow 环境变量缺失（[#80](https://github.com/ZhuLinsen/daily_stock_analysis/issues/80)）
  - 修复 `CUSTOM_WEBHOOK_BEARER_TOKEN` 未正确传递到 Runner 的问题

## [1.5.0] - 2026-01-17

### 新增
- 📲 单股推送模式（[#55](https://github.com/ZhuLinsen/daily_stock_analysis/issues/55)）
  - 每分析完一只股票立即推送，不用等全部分析完
  - 命令行参数：`--single-notify`
  - 环境变量：`SINGLE_STOCK_NOTIFY=true`
- 🔐 自定义 Webhook Bearer Token 认证（[#51](https://github.com/ZhuLinsen/daily_stock_analysis/issues/51)）
  - 支持需要 Token 认证的 Webhook 端点
  - 环境变量：`CUSTOM_WEBHOOK_BEARER_TOKEN`

## [1.4.0] - 2026-01-17

### 新增
- 📱 Pushover 推送支持（PR #26）
  - 支持 iOS/Android 跨平台推送
  - 通过 `PUSHOVER_USER_KEY` 和 `PUSHOVER_API_TOKEN` 配置
- 🔍 博查搜索 API 集成（PR #27）
  - 中文搜索优化，支持 AI 摘要
  - 通过 `BOCHA_API_KEYS` 配置
- 📊 Efinance 数据源支持（PR #59）
  - 新增 efinance 作为数据源选项
- 🇭🇰 港股支持（PR #17）
  - 支持 5 位代码或 HK 前缀（如 `hk00700`、`hk1810`）

### 修复
- 🔧 飞书 Markdown 渲染优化（PR #34）
  - 使用交互卡片和格式化器修复渲染问题
- ♻️ 股票列表热重载（PR #42 修复）
  - 分析前自动重载 `STOCK_LIST` 配置
- 🐛 钉钉 Webhook 20KB 限制处理
  - 长消息自动分块发送，避免被截断
- 🔄 AkShare API 重试机制增强
  - 添加失败缓存，避免重复请求失败接口

### 改进
- 📝 README 精简优化
  - 高级配置移至 `docs/full-guide.md`


## [1.3.0] - 2026-01-12

### 新增
- 🔗 自定义 Webhook 支持
  - 支持任意 POST JSON 的 Webhook 端点
  - 自动识别钉钉、Discord、Slack、Bark 等常见服务格式
  - 支持配置多个 Webhook（逗号分隔）
  - 通过 `CUSTOM_WEBHOOK_URLS` 环境变量配置

### 修复
- 📝 企业微信长消息分批发送
  - 解决自选股过多时内容超过 4096 字符限制导致推送失败的问题
  - 智能按股票分析块分割，每批添加分页标记（如 1/3, 2/3）
  - 批次间隔 1 秒，避免触发频率限制

## [1.2.0] - 2026-01-11

### 新增
- 📢 多渠道推送支持
  - 企业微信 Webhook
  - 飞书 Webhook（新增）
  - 邮件 SMTP（新增）
  - 自动识别渠道类型，配置更简单

### 改进
- 统一使用 `NOTIFICATION_URL` 配置，兼容旧的 `WECHAT_WEBHOOK_URL`
- 邮件支持 Markdown 转 HTML 渲染

## [1.1.0] - 2026-01-11

### 新增
- 🤖 OpenAI 兼容 API 支持
  - 支持 DeepSeek、通义千问、Moonshot、智谱 GLM 等
  - Gemini 和 OpenAI 格式二选一
  - 自动降级重试机制

## [1.0.0] - 2026-01-10

### 新增
- 🎯 AI 决策仪表盘分析
  - 一句话核心结论
  - 精确买入/止损/目标点位
  - 检查清单（✅⚠️❌）
  - 分持仓建议（空仓者 vs 持仓者）
- 📊 大盘复盘功能
  - 主要指数行情
  - 涨跌统计
  - 板块涨跌榜
  - AI 生成复盘报告
- 🔍 多数据源支持
  - AkShare（主数据源，免费）
  - Tushare Pro
  - Baostock
  - YFinance
- 📰 新闻搜索服务
  - Tavily API
  - SerpAPI
- 💬 企业微信机器人推送
- ⏰ 定时任务调度
- 🐳 Docker 部署支持
- 🚀 GitHub Actions 零成本部署

### 技术特性
- Gemini AI 模型（gemini-3-flash-preview）
- 429 限流自动重试 + 模型切换
- 请求间延时防封禁
- 多 API Key 负载均衡
- SQLite 本地数据存储

---

[Unreleased]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.25.0...HEAD
[3.25.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.24.1...v3.25.0
[3.24.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.24.0...v3.24.1
[3.24.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.23.0...v3.24.0
[3.23.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.22.0...v3.23.0
[3.22.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.21.1...v3.22.0
[3.21.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.21.0...v3.21.1
[3.21.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.20.0...v3.21.0
[3.20.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.19.0...v3.20.0
[3.19.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.18.0...v3.19.0
[3.18.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.17.1...v3.18.0
[3.17.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.17.0...v3.17.1
[3.17.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.16.0...v3.17.0
[3.16.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.15.0...v3.16.0
[3.15.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.2...v3.15.0
[3.14.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.1...v3.14.2
[3.14.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.14.0...v3.14.1
[3.14.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.13.0...v3.14.0
[3.13.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.12.0...v3.13.0
[3.12.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.11.0...v3.12.0
[3.11.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.1...v3.11.0
[3.10.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.10.0...v3.10.1
[3.10.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.9.0...v3.10.0
[3.9.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.8.0...v3.9.0
[3.8.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.7.0...v3.8.0
[3.7.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.6.0...v3.7.0
[3.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.5.0...v3.6.0
[3.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.10...v3.5.0
[3.4.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.9...v3.4.10
[3.4.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.8...v3.4.9
[3.4.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.7...v3.4.8
[3.4.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.4.0...v3.4.7
[3.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.22...v3.4.0
[3.3.22]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.3.12...v3.3.22
[3.3.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.11...v3.3.12
[3.2.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v3.2.10...v3.2.11
[2.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.5...v2.3.0
[2.2.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.4...v2.2.5
[2.2.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.3...v2.2.4
[2.2.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.2...v2.2.3
[2.2.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.1...v2.2.2
[2.2.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.2.0...v2.2.1
[2.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.14...v2.2.0
[2.1.14]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.13...v2.1.14
[2.1.13]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.12...v2.1.13
[2.1.12]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.11...v2.1.12
[2.1.11]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.10...v2.1.11
[2.1.10]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.9...v2.1.10
[2.1.9]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.8...v2.1.9
[2.1.8]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.7...v2.1.8
[2.1.7]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.6...v2.1.7
[2.1.6]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.5...v2.1.6
[2.1.5]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.4...v2.1.5
[2.1.4]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.3...v2.1.4
[2.1.3]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.2...v2.1.3
[2.1.2]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.1...v2.1.2
[2.1.1]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.1.0...v2.1.1
[2.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.6.0...v2.0.0
[1.6.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ZhuLinsen/daily_stock_analysis/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ZhuLinsen/daily_stock_analysis/releases/tag/v1.0.0
