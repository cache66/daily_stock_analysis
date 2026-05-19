# 本地策略目录（Local Strategy Catalog）

最后更新：2026-05-19

2026-05-19 addendum (fast review authority structured settle before broad search):

- This round still targets fast-review explanation latency, not candidate-selection thresholds.
- Root cause found in the authority-first explanation chain:
  - `_collect_authority_intel(...)` first pulled structured `announcements / earnings / market_analysis`
  - but if the three dimensions were not all present, it still escalated into a broad `search_comprehensive_intel(...)`
  - in practice, many `strategy_focus` rows already had enough structured evidence to settle `公告确认` or `财报确认`, yet still paid the full fallback-search cost
- Current behavior:
  - if structured authority evidence is already enough to settle one of these levels, fast review now returns early and skips the broad search:
    - strong announcement
    - current-season authoritative earnings confirmation
    - sufficiently strong structured research reinforcement
  - only rows that still cannot be settled from structured evidence continue into the heavier missing-evidence fallback
- Real replay evidence:
  - output dir: `data/manual_runs/fast_review_20260519_today_review_after_authority_settle_20260519`
  - `2026-05-19` signal elapsed improved from `315.7s` to `285.5s`
  - split timing moved to:
    - `trend_leader`: `129.86s`
    - `daily_slow_rise`: `78.51s`
    - `hundred_day_high`: `77.17s`
  - current bottleneck therefore shifts back to upstream signal-stage scan fluctuation, especially `trend_leader`, rather than authority tail search itself

2026-05-19 addendum (fast review shared cause-service cache reuse):

- 这轮继续优化的是快复盘解释尾段，不改任何选股阈值。
- 之前 `strategy_focus` 在逐行补 `reason_summary / authority` 时，会每行新建一个 `SignalCauseAnalysisService`。
- 这会导致同一轮 bundle 内部本来可以共享的缓存失效，包括：
  - 公告日历缓存
  - 研报缓存
  - 业绩目录缓存
- 当前已改为：
  - 同一轮 `strategy_focus` 构建只复用一个 `SignalCauseAnalysisService`
  - 逐行解释继续调用同一个 service
  - 因此 authority-first 解释需要的结构化证据缓存终于能在 bundle 内复用
- 这条改动的目标非常直接：
  - 不改变 `reason_summary / authority_judgement` 的口径
  - 只减少同日同一批焦点股在解释阶段重复预热 authority cache 的成本

2026-05-19 addendum (fast review focus quote-tail dedupe):

- `FastReviewFocusService` 这轮继续处理的不是选股口径，而是快复盘读层/导出层最后一段 `涨幅 / PE` 补全的重复空转。
- 这次收掉了两个真实长尾：
  - 如果东财 `EM` 冷启动首轮就返回空，后续标的不再继续走同一条空缓存路径逐只重试。
  - 如果某只票已经在腾讯批量预抓阶段 miss，后续逐行补全时也不再再打一遍同样的轻量腾讯请求。
- 实际含义是：
  - 不改 `strategy_focus` / `earnings_focus` 的选股结果。
  - 只减少 fast review 读取和导出阶段为了补 `today_change_pct / pe_ratio` 付出的重复请求。
  - 后续我们再看真实回放时，应该更容易把剩余耗时聚焦到真正的大头，而不是被这类重复 quote retry 淹掉。

2026-05-19 addendum (fast review default slow-rise + fallback/perf tightening):

- `daily_slow_rise` is now part of the repo default fast-review signal set, instead of being opt-in only.
- The default fast-review daily profile is now `accelerating`, so the daily replay path is closer to the user-calibrated `30-45 degree` continuation samples.
- `src/services/kline_selector_service.py` now keeps manager-level `Tushare` history fallback enabled for fast A-share scans again.
  - This restores the intended behavior described in code comments: transient `Akshare` history misses should degrade into `Tushare` fallback, not directly shrink same-day candidate coverage.
- `src/search_service.py::search_comprehensive_intel(...)` now short-circuits repeated `SearXNG` retries inside the same stock-level intel call when public-instance bootstrap is clearly unavailable.
  - This is meant to cut the repeated `latest_news / market_analysis / risk_check / announcements ...` failure loop that was inflating same-day fast-review tail latency.
- Practical effect for daily review:
  - default fast review sees more `daily_slow_rise` samples without extra CLI flags
  - fast-scan coverage is less likely to collapse on temporary `Akshare` backoff
  - explanation-stage tail latency is lower when only public `SearXNG` discovery is failing

2026-05-19 addendum (daily_slow_rise fetch-buffer fix):

- `daily_slow_rise` 这轮没有先改图形阈值，先修的是历史窗口边界。
- 根因是首轮真实 probe 里大量样本只拿到 `59/60` 根附近历史，导致还没进入真正的图形判定就被打成 `insufficient daily history`。
- 当前 `DailySlowRiseCriteria.history_days_required` 已从精确规则窗口改成 `62` 根带缓冲的抓取窗口：
  - 30 根底部区 + 30 根推进区仍然是实际判图窗口
  - 多出来的 2 根只用于吸收真实数据源/缓存链路里的 `off-by-one` 返回
- 修复后，真实 probe 的失败分布已回到可解释的图形口径，主要是：
  - `single-day gain too large`
  - `average daily amount too low`
  - `drawdown too deep`
- 这说明后续若要继续把这条线调到更贴近用户想要的“健康 30-45 度慢涨”，下一步应调的是形态阈值，而不是再去怀疑历史取数是否够用。

2026-05-19 addendum (`daily_slow_rise` accelerating profile):

- 在 `balanced` 之外，当前又新增了一档可选 `accelerating` profile。
- 这档不是拿来替换默认慢涨口径，而是专门服务这类样本：
  - 整体仍是日线健康上行
  - 但中途允许出现一次 `10cm` 级加速阳线
  - 且前半段不要求必须是特别紧的“横盘底”
- 当前 `accelerating` 主要放宽了：
  - `max_single_day_gain_pct -> 10.3`
  - `max_advance_return_pct -> 130.0`
  - `max_base_range_pct -> 40.0`
  - `max_base_return_abs_pct -> 12.0`
  - `min_steady_positive_ratio -> 0.55`
- 这档 profile 的真实校准方向，来自 2026-05-19 用户补充的几只样本：
  - `603115`
  - `000811`
  - `000026`
  - `603311`
  - `603283`
- 当前按 `accelerating` 回放结果：
  - `000811`：通过
  - `000026`：通过
  - `603283`：通过
  - `603311`：仍因历史不足未通过
  - `603115`：仍偏过度扩张，暂未纳入这条线
- 也就是说，这档 profile 现在更像“加速慢涨 / 宽底加速”层，而不是纯粹的“底部极紧 + 后段 45 度”层。

2026-05-19 addendum (new optional `daily_slow_rise` 日线慢涨策略):

- 这轮新增的是一条独立可选策略：`daily_slow_rise`，不是去修改 `hundred_day_high`、`trend_leader`、`earnings` 的主筛选阈值。
- 目标是单独抓“日线小 30-45 度慢涨”的票，重点识别两类图形：
  - `base_to_trend`：前面横盘较紧，后面沿 30-45 度缓升突破
  - `steady_rise`：不一定先横盘，但近阶段上涨节奏均匀、回撤浅、修复快
- 当前规则显式压掉两类不好看的走势：
  - `single-day gain too large`：单日脉冲/吹拔过猛
  - `drawdown too deep`：上行过程中回撤过深、锯齿感太强
- 产物：
  - `scripts/select_daily_slow_rise_candidates.py`
  - `daily_slow_rise_candidates.csv/.md/.txt`
  - 可选落库 `signal_type=daily_slow_rise`
- 快复盘接线：
  - `scripts/run_fast_review_bundle.py` 现支持 `--include-signals daily_slow_rise`
  - 这条信号暂时不进入 repo 默认 `include_signals`
  - 但如果显式带上，会在 `fast_review_summary.md` 里新增 `日线慢涨候选` 区块，直接展开前排样本
- 这一轮的目的，是把“健康慢涨 / 横盘后 45 度上行”从现有 `hundred_day_high` 阅读层里拆出来，形成一条可独立复盘、可单独回放的日线结构策略。

2026-05-19 addendum (fast review 纯趋势延续阅读分组):

- 这轮没有修改 `trend_leader`、`hundred_day_high`、`earnings` 三条策略本身的入选阈值；处理的是快复盘阅读层里“扫到了但不够显眼”的问题。
- 之前像 `002281 光迅科技` 这类 `trend_leader only` 样本，实际已经进入 `strategy_focus`，但因为不属于 `hundred_day_high` 分组，用户从首屏摘要里容易体感成“没有扫描到”。
- 当前 `fast_review_strategy_focus.csv` 新增：
  - `review_display_group`
  - `review_display_group_label`
- 当前阅读分组口径收敛为：
  - `intersection` -> `交叉强样本`
  - `hundred_strong_chart` -> `纯百日新高`
  - `trend_continuation` -> `纯趋势延续`
- `fast_review_summary.md` / `fast_review_summary_latest.md` 现在会在 `百日新高 Top N` 之后额外展开 `纯趋势延续 Top N`，专门展示 `trend_leader_unified only` 的强趋势票。
- 这样像 `002281` 这类票会被显式标出来；而像 `603938 三孚股份` 这种本来属于 `hundred_only` 的样本，仍留在 `纯百日新高` 组，不和纯趋势票混读。

2026-05-19 addendum (fast review 业绩空窗期模式 + 强图形百日新高前置):

- 这轮没有改 `trend_leader`、`hundred_day_high`、`earnings` 三条策略本身的选股阈值；处理的是它们汇总到 `fast_review strategy_focus` 时的阅读排序偏置。
- 之前的真实问题不是“空窗期找不到百日新高/图形强票”，而是：
  - 当天即使 `earnings=0`
  - `trend_leader` 行里仍会挂着历史季度财务字段与 `earnings_strategy_score`
  - 导致主焦点前排长期被 `trend_only + 业绩残留解释` 占满
  - 用户体感上会像“还是按业绩期样本在选”
- 当前快复盘口径已收紧为：
  - 若当日 `earnings` 候选为空，则 `strategy_focus` 自动进入“业绩空窗期模式”
  - `trend_leader` 行上残留的 `earnings_strategy_score` 仍保留为信息字段，但不再继续主导 `driver_type / review_stage / priority_score`
  - 这类样本默认回落为 `theme_sentiment_driven + 纯轮动`，避免空窗期继续被误读成 `拐点观察 / 业绩兑现`
  - 同时会优先前置 `hundred_day_high` 中图形更强的 `hundred_only` 样本，重点是 `base_breakout / healthy_trend`
  - 快复盘产物现在还会显式打标：
    - `fast_review_summary.md` / `fast_review_summary_latest.md` 写出 `当前模式：业绩窗口 / 业绩空窗期`
    - `fast_review_strategy_focus.csv` 写出 `review_context_label / review_context_reason`
- 2026-05-19 实跑验证：
  - 目标日期：`2026-05-19`
  - 产物目录：`data/manual_runs/fast_review_20260519_empty_earnings_mode_v3_20260519/`
  - 当日 `earnings=0`
  - `strategy_focus` 前 10 已从“几乎全是 trend_only”调整为：
    - 前 2：`trend_leader ∩ hundred_day_high` 交叉强样本
    - 接着 4 只：`hundred_only` 强图形百日新高
    - 再后才是 `trend_only` 趋势延续票
- 这一轮的目标不是弱化业绩，而是把“业绩窗口”和“业绩空窗期”拆开读：
  - 业绩窗口优先看 `earnings / authority / 兑现`
  - 空窗期优先看 `交叉强样本 + 强图形百日新高 + 健康慢涨/横盘突破`

2026-05-18 addendum (fast review AI 归因收紧 + 百日新高阅读前移):

- 这轮没有修改 `trend_leader`、`hundred_day_high` 的入池阈值，也没有扩大或缩小每日复盘样本池。
- 处理的是阅读层问题：
  - 前排 `strategy_focus` 容易被科技链 `trend_leader` 占满，体感上像“今天全是 AI”
  - `hundred_day_high` 虽然样本里并不全是 AI，但在摘要里的可读提示太弱，非 AI 样本存在感不足
- 当前快复盘口径已收紧为：
  - 当个股已经有明确 `业务锚点 + 业绩锚点` 时，`mainline_judgement` 优先回到 `业绩兑现`
  - `display_reason_summary` 不再默认把 `AI主线扩散 / AI上游材料扩散` 放在行业/业务锚点前面
  - `百日新高 Top N` 前移到 `策略精简焦点` 之前，先让复盘阅读看到 `交叉强样本 / 纯百日新高`
  - `hundred_day_high` 快照优先展示 `业务提示 / 图形标签 / PE`，降低“只有数字、没有方向感”的问题
- 这次的目标不是弱化 AI 主线本身，而是把它从“一级标题”收回到“行业景气背景”，避免 `PCB / 光模块 / 电子材料 / 光通信` 这类有自身主线的票被一把写成 AI。

2026-05-15 addendum (fast review spot cache fallback no longer blocks on listing metadata):

- 这轮处理的不是 `earnings / hundred_day_high / trend_leader` 三条策略的选股口径，而是它们共用的 `spot-enriched universe` 启动慢链路。
- 实盘探针里，三条线都先后卡在同一个共享入口：
  - `src/services/kline_selector_service.py::get_spot_enriched_a_share_universe()`
  - 具体模式是：
    - live `spot` 拉取先失败
    - 然后回退到本地 stale / disk `spot` cache
    - 旧逻辑还会为了补 `listing metadata` 继续尝试 `tushare -> baostock`
    - 当 `tushare` 频控、`baostock` 又长时间无返回时，就会把每日复盘启动阶段继续拖住
- 当前共享服务的降级语义已收紧为：
  - 如果已经决定使用 stale / disk `spot` cache 作为 fast fallback，就直接接受这份 cache
  - 不再为了补 `listing metadata` 继续阻塞到慢兜底链路
  - 只有真正退回 generic `stock list` provider 时，才继续尝试补 `listing metadata`
- 这次的目标是“先保证每日复盘能稳定快速起跑”，不是重新设计 `listed_days` 前筛，也不是更改三条策略本身的入池规则。
- 2026-05-15 本地探针验证：
  - `trend_leader` 共享准备阶段从卡在 `get_spot_enriched_a_share_universe()`，收敛到约 `0.3s` 完成 universe 准备
  - `earnings` 在 `recent_event catalog` 后，现可在约 `7s` 内完成 `limit=1` 全流程
  - `hundred_day_high` 现可穿过 shared scan shell，并在约 `6-7s` 内完成 `limit=1` 准备与导出
  - 根因已从“listing metadata 长尾挂起”收敛回“live spot 失败后约 5-6s 的正常 fallback 成本”

2026-05-15 addendum (fast review 外部脚本 heartbeat watchdog):

- 这轮不是改 `earnings / hundred_day_high / trend_leader` 三条策略本身，而是修快复盘 bundle 对外部脚本的“误杀”问题。
- `2026-05-14` 的真实复跑里，三条外部信号都出现了同一模式：
  - 脚本能正常启动，也会先输出启动日志
  - 但进入长时间扫描/抓取阶段后，会有一段合法静默
  - 原来的 `fast_review bundle` 会把“30 分钟没新 stdout”直接判成 `execution_failed`
- 当前 `scripts/run_fast_review_bundle.py` 的监控语义已收紧为：
  - 如果子进程从启动到结束都没有任何输出，仍按 `idle timeout` 处理
  - 但只要子进程已经输出过首条日志，bundle 就改为每 `5` 分钟输出一次 `external heartbeat`
  - 这种“已证明存活、只是暂时无新日志”的场景，不再因为 `idle timeout` 被提前杀掉
  - 真正的硬性兜底仍保留 `total timeout`
- 这轮的目的很明确：
  - 让每日复盘在长任务阶段能持续看到“还活着”
  - 避免 `earnings / hundred_day_high / trend_leader` 在合法长静默阶段被错误打成 `no-result`
  - 同时保留“完全没启动/完全无输出”的快速失败保护
- 这轮还没有去改三条子脚本内部的细粒度阶段心跳；当前先在 bundle 父进程层补了一个更稳的观察与兜底。

2026-05-14 addendum (hundred_day_high 图形标签分层):

- 这轮不改 `hundred_day_high` 主策略入池口径，也不缩小池子；处理的是“同样是百日新高，图形好不好看要显式读出来”的阅读层问题。
- 当前 `scripts/select_hundred_day_high_candidates.py` 会在原有 `breakout_quality` 之外，额外为每个命中样本写入一组轻量图形字段：
  - `chart_pattern_label`
  - `chart_pattern_score`
  - `chart_pattern_summary`
  - `base_breakout_score`
  - `healthy_trend_score`
- 默认把样本分成三类：
  - `base_breakout` -> `横盘突破型`
  - `healthy_trend` -> `健康慢涨型`
  - `plain_breakout` -> `图形一般`
- 判定顺序仍是“先看横盘突破，再看健康慢涨，判不准就回到普通百日新高”。
  - 也就是说，这不是新策略，不会把“不好看”的样本删掉。
  - 只是让你在看 CSV / Markdown / 每日复盘摘要时，能直接区分“漂亮 45 度走势”和“只是创了百日新高”。
- `hundred_day_high_candidates.csv/.md` 现已透出这组字段；Markdown 结果表新增 `图形标签` 列。
- `fast_review_summary.md` / `fast_review_summary_latest.md` 的 `百日新高 Top N` 现在也会：
  - 在快照里直接显示 `横盘突破型 / 健康慢涨型 / 图形一般`
  - 在不打破现有高优先级规则的前提下，优先把更好看的图形排在前面
  - 现有更高优先级仍保持不变：
    - 已进入 `strategy_focus`
    - `A类/B类`
    - `trend_leader + hundred_day_high` 交叉
- 这轮本质上还是“百日新高阅读层增强”，不是把 `monthly_slow_rise` 并回主默认链，也不是新开独立策略。

2026-05-11 addendum (fast review 百日新高摘要单独展开):

- 这一轮不是放宽主策略，也不是把 `hundred_day_high` 全部塞进 `strategy_focus`。
  - 主排序、A/B 分类、`兑现 / 半兑现 / 拐点 / 纯轮动` 口径都不变。
  - 处理的是“每日复盘首屏看不到足够多百日新高样本”的阅读问题。
- 当前 `fast_review_summary.md` / `fast_review_summary_latest.md` 会额外新增一个独立区块：
  - `百日新高 Top N`
  - 位置放在 `策略精简焦点` 之后、`强势股上涨原因摘要` 之前
  - 作用是让你在不改主焦点逻辑的前提下，直接扫当天这条线前排有哪些票
- 展示口径刻意保持轻量：
  - 标题按实际可见条数动态显示，当前默认展开到 `Top 30`
  - 如果当天同一只票已经进入 `strategy_focus`，则优先复用那边已补好的 `涨幅 / PE / 报告期 / 净利` 快照
  - 只有在 `strategy_focus` 也拿不到时，才回退到 `收盘` 这类原始突破字段做最小快照
  - 同时这块自己的排序也不再完全沿用原始 `hundred_day_high` 导出顺序，而是优先把：
    - 已进入 `strategy_focus` 的票
    - `A类`
    - `trend_leader + hundred_day_high` 交叉样本
    - 分数更高、当天更强的票
    顶到最前面
  - 结构上再拆成两段：
    - `交叉强样本`
    - `纯百日新高`
    先扫交叉，再看纯百日新高，阅读会顺很多
  - 原始总数仍以“分信号结果”里的 `raw_count (export N)` 为准，避免把“真实跑出多少条”和“摘要展开多少条”混为一谈
- 2026-05-11 实跑验证：
  - 产物目录：`data/manual_runs/fast_review_20260511_hundred_summary_spotlight_verify_20260511_b`
  - `分信号结果` 显示：`hundred_day_high = 257 (export 10)`
  - 主摘要现已额外出现：`百日新高 Top 10`
  - 后续增强验证目录：`data/manual_runs/fast_review_20260511_hundred_summary_spotlight_enriched_verify_20260511`
    - 现已直接显示：
      - `603045 福达合金 = 涨幅 9.99% / PE 31.5 / 2026Q1 / 净利 1.81亿`
      - `603618 杭电股份 = 涨幅 -0.85% / PE -107.8 / 2026Q1 / 净利 0.81亿`
  - 排序收紧后，在完整今日复盘目录 `data/manual_runs/fast_review_20260511_today_review_20260511_002148` 中：
    - `百日新高 Top 15` 前两位已调整为
      - `002491 通鼎互联`
      - `002222 福晶科技`
    - 说明 `A类 + 交叉信号` 样本已不再被纯 `hundred_day_high` 导出顺序压到后面
  - 后续分段增强后，同目录现已显示：
    - `百日新高 Top 30`
    - `交叉强样本（top 2 / 2）`
    - `纯百日新高（top 28 / 28）`
  - 可直接看到代表样本：
    - `603045 福达合金`
    - `603618 杭电股份`
    - `688167 炬光科技`
- 这轮本质上是在补“复盘可见性”，不是改 `百日新高` 策略本身。

2026-05-10 addendum (fast review cable wide-industry clause cleanup):

- 这轮继续清理 `Fast Review Focus` 里“已经不在主标签上、但还残留在导出解释句里”的长业务尾句，处理的是 `杭电股份` 这一类。
  - 修复前代表句式是：`宽口径行业标签仍归在 电线电缆的研发、生产、销售和服务`
  - 修复后统一压回：`宽口径行业标签仍归在 电力设备`
- 这次不是再改 `Peer Check`，也不是改主策略口径，而是补导出层自己的归一化缺口。
  - 也就是说，主链路里很多标签已经是短的
  - 但 `industry_logic` 这种持久化解释字段之前仍可能把原始主营整句带进历史 CSV / Markdown
- 当前这批归一化规则同步把下面几类都压回 `电力设备`：
  - `电线电缆`
  - `线缆`
  - `电缆`
  - `电网设备`
- 导出层现在会在写 `fast_review_strategy_focus.csv` / `fast_review_earnings_focus.csv` 前，先统一过一遍轻量字段归一化。
  - 好处是即使后面的 enrichment fail-open
  - 历史复盘产物里这类宽口径长句也不会再漏出来
- 2026-05-10 实跑验证：
  - 产物目录：`data/manual_runs/fast_review_20260510_cable_clause_normalize_verify_20260510_v3`
  - 代表样本：`603618 杭电股份`
    - 现已显示：`宽口径行业标签仍归在 电力设备，但交易辨识度更偏 光通信/电力设备/铜箔`
  - 全局搜索旧句：`电线电缆的研发、生产、销售和服务`
    - 结果：`0` 命中
  - 标签审计：`files=1 findings=0`

2026-05-10 addendum (fast review optics-component normalization + replay label audit):

- 这轮继续清理 `Fast Review Focus` 里的“长主营句直接当行业标签”尾巴，补的是 `光学元器件` 这一类。
  - 之前像 `蓝特光学` 会落成 `光学元器件的研发、生产和销售`
  - 现在主解释链会统一收敛成 `光学元器件`
- 这次刻意把两个层级继续分开：
  - 主解释和业务归因保留更具体的 `光学元器件`
  - `Peer Check` 同业佐证仍压到更宽的 `光学` 组，便于看同日共振
- 这不是新增一套策略，只是继续把每日复盘的标签压回“短、稳、可复用”的桶。
- 同时补了一个复跑质量审计脚本：
  - `scripts/audit_fast_review_label_quality.py`
  - 作用是扫 `fast_review_strategy_focus.csv` 这类产物里是否还残留明显的长业务句标签
  - 默认只检查 `preferred_industry_label / business_summary / peer_group_label`，避免把正常解释文案误报进去
- 2026-05-10 实跑验证：
  - 产物目录：`data/manual_runs/fast_review_20260510_optics_normalize_verify_20260510`
  - `蓝特光学` 现在显示为：
    - `当前更像是 光学元器件 方向走强`
    - `业务更偏 光学元器件`
    - `peer_group_label = 光学`
  - 审计结果：`files=1 findings=0`

2026-05-10 addendum (fast review 导出层同步 display 文案 + 分信号计数口径修正):

- `Fast Review` 这轮又补了一个“读层和导出层不一致”的问题。
  - 之前 UI 已经开始优先显示 `display_reason_summary`
  - 但 `fast_review_strategy_focus.csv/.md`、`fast_review_earnings_focus.csv/.md` 和主 `summary.md` 里，很多地方仍在直接使用原始 `reason_summary`
- 现在导出层也统一改为：
  - 保留原始 `reason_summary` 作为兼容字段
  - 新增并持久化 `display_reason_summary`
  - Markdown 展示优先使用 `display_reason_summary`
- 这意味着你后面看导出文件时，默认会更接近 UI 里的压短口径：
  - 先看行业判断
  - 再看业务定位
  - 再看主线判断
  - 最后看真实触发
  - 而不是再被原始长句、消息中性句、技术废话句干扰
- 同时修正了一个快复盘统计口径问题：
  - 当某个信号源本身跑出很多条，例如 `hundred_day_high=226`
  - 但 bundle 层又按导出上限裁成 `30`
  - 汇总页现在会直接写成 `226 (export 30)`，不再让“真实跑出条数”和“导出展示条数”混在一起
- 这一轮本质上是在修“复盘产物如何被阅读”，不是改三条主策略本身。

2026-05-10 addendum (fast review 首屏减重 + 解释压短优先级收紧):

- `Fast Review Focus` 这一轮主要是把“每日复盘能不能更快扫完”再往前推一步，集中处理 3 个问题：
  - 解释文案太长，但关键句有时反而被挤掉
  - authority 原始字段为空时，界面会显得像“什么都没拿到”
  - 首屏同时铺开太多 explanation 区块，阅读成本偏高
- 当前快复盘解释压短口径改成固定优先级：
  - 先保留 `当前更像是 XXX 方向走强`
  - 再保留 `业务更偏 XXX`
  - 再保留 `主线判断更偏 XXX`
  - 再优先保留真实触发句，例如 `创出 XX 日新高` / `命中 trend_leader_unified 信号`
  - `当日强势池入选理由是 trend leader / 60日新高` 仍保留，但只作为补充，不再优先盖过前面更关键的句子
- 这解决的是两个具体复盘问题：
  - 像 `莱美药业` 这类 fallback 样本，`20 日新高` 不应再被压掉
  - 像 `光模块/光通信` 这类趋势样本，`主线判断更偏 ...` 不应再被泛化的“景气扩散分支走强”挤掉
- authority 这一层继续采用“原始字段不强改、读层先兜底”的策略：
  - 若原始 `authority_judgement` 仍为空或未验证，但 `earnings_evidence_summary + report/net profit` 已足够强，读层会优先展示轻量 `财报确认`
  - 这样做是为了先改善每日复盘阅读，不破坏历史 CSV 的原始语义
- UI 层保持轻量，不扩成研究页：
  - 首屏继续保留紧凑 `Quick Read`
  - `Authority Details` 和 `Peer Check Details` 默认折叠
  - 用户需要时再展开看细节
- 这轮本质上是“快复盘阅读优化”，不是再新增一套重型策略逻辑。

2026-05-10 addendum (peer check resin-business tail normalization):

- `Peer Check` 又收掉了一条“主营整句直接当分组名”的残留尾巴。
  - 当前把 `聚酯树脂系列产品的生产销售` 统一归一到 `树脂/化工材料`。
  - 目标还是同一个：让复盘分组更像稳定短桶，而不是原始主营描述。
- 这意味着像 `神剑股份` 这类样本，后续不会再挂着整句业务文案，而会落到更可扫读、也更可复用的 `树脂/化工材料` 组。

2026-05-10 addendum (peer check residual tail label normalization):

- `Peer Check` 又补了一刀“末级行业尾巴”归一化，处理的是已经不算长文本、但仍然不适合作为复盘分组名的残留标签。
  - 当前把 `环保设备Ⅲ` 这类末级行业名统一收敛到 `再生资源/环保设备`。
  - 目标不是扩充行业知识树，而是继续把同业组名压回“可扫读、可复用”的短桶。
- 这意味着像 `华宏科技` 这类样本，后续历史回看里不再挂一个孤立的 `环保设备Ⅲ`，而会落到更稳定的 `再生资源/环保设备` 组。

2026-05-10 addendum (peer check long-text group normalization):

- `Peer Check` 的同业分组继续往“可复盘、可复用”的短桶收紧，不再让整句主营描述直接挂在组名上。
  - 本轮把 `房地产开发业务和建筑施工业务`、`海上运输业务`、`软磁材料及磁心的研发、生产和销售`、`软磁铁氧体磁粉的研发、生产和销售` 归一成：
    - `房地产/建筑施工`
    - `航运`
    - `软磁材料/磁性材料`
  - 同时把 `锂电/精密组件` 显式纳入归一化规则，避免短别名与长业务描述来回漂移。
- 这轮解决的是“组名太长、历史回看不利于扫读”的问题，不是新增更重的行业知识层。
  - 也就是说，`Peer Check` 仍然只是快复盘里的轻量同业佐证。
  - 但现在像 `天通股份 / 招商轮船 / 大龙地产` 这类样本，回看时会更像行业桶，而不是一整句业务说明。

2026-05-10 addendum (authority explanation quality + peer corroboration):

- `Fast Review Focus` 的 authority 首句现在不再只停留在“公告确认 / 财报确认 / 研报强化”四个字上。
  - 若证据文本里出现 `客户导入 / 供不应求 / 产能爬坡 / 订单放量 / 景气上行` 等催化词，`authority_reason_summary` 会把这些线索压进一句更像“上涨逻辑”的解释。
  - 同时会继续拼接 `业务方向更偏 XXX`、`AI 主线景气扩散`、`主线判断更偏 XXX` 等上下文，减少“只有结论，没有味道”的情况。
- 快复盘读层新增了一层轻量 `Peer Check`，只服务每日复盘详情，不扩成完整行业面板。
  - 读层会按 `preferred_industry_label -> chain_role_label -> theme_label -> business_labels` 的优先级给每只票归一个同业组。
  - 然后给出 3 条紧凑摘要：
    - `peer_resonance_summary`：同日该方向有几只进入焦点池、是否更像板块共振。
    - `leader_position_summary`：当前这只票在同组里更像龙头、前列还是跟随。
    - `turning_point_peer_summary`：同组里 `拐点 / 半兑现 / 兑现` 的分布，用来辅助判断“是不是单票异动，还是行业扩散初段”。
- 这轮没有引入新的重型行业数据源，也没有把快复盘变成行业全景页。
  - 当前同行业佐证完全基于当日 `fast_review_strategy_focus` 结果做轻量聚合。
  - 好处是历史回看自动可用，旧 CSV 也能 fail-open；代价是它更偏“复盘共振确认”，不是完整行业覆盖率统计。
- 回归覆盖：
  - `tests/test_signal_cause_analysis_service.py` 新增 authority 摘要质量用例，锁住 `AI / 订单放量 / 产能爬坡 / 供不应求` 这类催化词必须能进 summary。
  - `tests/test_fast_review_focus_api.py` 新增同行组聚合与拐点同业确认用例。
  - `apps/dsa-web/src/pages/__tests__/SignalsPage.fastReview.test.tsx` 新增 `Peer Check` 区块展示断言。

2026-05-10 addendum (authority windows split: announcements 7d / research 21d):

- `Fast Review Focus` 的 authority 解释时间窗现在不再统一卡在 `7` 天。
  - `公告` 继续保持短窗 `7天`，避免把过早的事件公告硬拖进当天上涨解释。
  - `研报` 放宽到 `21天`，专门解决“财报后走趋势、但机构强化发生在 8-21 天内”这类样本被误判成没研报支持。
  - `财报` 仍按“当前报告期是否成立”判断，不改成简单的 `7天/21天` 日历截断。
- 这层调整的核心目标不是放宽 `authority_judgement`，而是修正一个真实复盘偏差：
  - 之前像 `300476 胜宏科技` 这类样本，底层最近研报日期可能是 `2026-04-30`
  - 在 `2026-05-09` 复盘时，旧逻辑会因为超出 `7天` 直接把 `market_analysis` 清空
  - 结果就是 authority 读起来像“只有财报、没有机构强化”，但真实情况更接近“财报确认 + 近三周机构仍在强化同一条逻辑”
- 文案层也同步做了拆窗对齐：
  - `research_evidence_summary` 现在会显式写 `近21天多篇机构分析...`
  - `财报确认` 的补充句也改为 `近21天机构观点仍在强化这条逻辑`
  - `暂无权威验证` 仍保留 `近7天未见足够强的公告/财报/研报验证` 这条短窗主句，但会额外补一句 `近21天机构研报也未形成一致强化`
- 兼容性保持不变：
  - 导出/API 里的 `authority_time_window_days` 先继续保留 `7`
  - 旧产物和前端读层不需要额外迁移
  - 更细的多窗口字段如果后面真要上，再单独扩 schema/UI
- 本轮回归：
  - `tests/test_signal_cause_analysis_service.py` 新增 2 条回归，专门锁住 `4/30 -> 5/9` 研报样本和 `财报确认 + 21天研报强化` 文案
  - `tests/test_fast_review_daily_bundle.py` + `tests/test_fast_review_focus_api.py` 已继续通过

2026-05-09 addendum (direct Eastmoney research fallback for authority intel):

- `Fast Review Focus` 的结构化研报 authority 又补了一层更稳的兜底：
  - 当 `akshare.stock_research_report_em(...)` 正常返回时，继续沿用原链路
  - 当它返回空表、畸形表（如“有行无列”）或直接异常时，改为直连东方财富 `reportapi.eastmoney.com/report/list`
- 这层修复是为了解决一个真实线上问题：
  - 不是东方财富没有研报
  - 而是 Akshare wrapper 在部分样本上会把有效结果吃成空表，导致 `market_analysis` 维度被误判为空
- 实测观察：
  - `002384 东山精密` 直连接口能稳定拿到 `2026-05-05` 的机构研报
  - 单票 authority payload 已能落出：
    - `财报确认`
    - `近7天机构观点仍在强化这条逻辑`
    - `research_evidence_summary=近7天多篇机构分析强化同一逻辑：公司信息更新报告：2026Q1业绩高增，“光模块+AIPCB”打开成长空...`
  - `300476 胜宏科技` 仍为空不是抓取失败，而是底层可得的最新研报日期为 `2026-04-30`，超出当前固定 `7` 天窗口
- 这说明：
  - 研报 authority 的剩余瓶颈已经进一步收窄成“时间窗内是否真的有权威文本”
  - 而不再是“Akshare wrapper 把已有文本吃掉”

2026-05-09 addendum (authority partial-dimension search merge):

- `Fast Review Focus` 的 `authority_intel` 采集链又补了一层“缺维度继续搜”修复。
  - 之前只要结构化层先拿到任意一类证据，例如 `earnings`，整条 authority 采集链就会直接返回
  - 结果是很多 `财报确认` 样本虽然已经有财报锚点，但本来还应该继续补的 `公告 / 研报` 维度被提前短路
- 现在的行为改为：
  - 若 `announcements / earnings / market_analysis` 三类结构化证据都已有，则直接返回
  - 若只拿到其中一部分，则继续走 `search_comprehensive_intel(...)`，只为缺失维度补搜
  - 若 authority 搜索被显式关闭，仍保持原有 fail-open 行为
- 这层修复的目标不是放宽 `authority_judgement`，而是避免“已经知道有季报，但错过了更具体的公告/研报解释”。
- 2026-05-09 实跑验证产物：
  - `data/manual_runs/fast_review_20260509_authority_partial_search_merge_20260509_v21`
  - authority 分布：`公告确认=5 / 财报确认=29 / 暂无权威验证=17`
  - 运行观察：
    - 真实链路中 `authority` 搜索不再被 `earnings` 提前短路
    - 但当天 `research_evidence_summary` 仍然是 `0/51`，说明这轮剩余瓶颈已经不是短路逻辑，而是搜索源本身未返回可用研报文本
    - 当次在线检查里，`search_comprehensive_intel(...)` 已触发，但 `SearXNG` 公共实例发现失败，导致 `机构分析 / 公司公告 / 行业分析` 维度全部失败

2026-05-09 addendum (authority catalyst summary refinement):

- `Fast Review Focus` 的 `authority` 摘要层继续细化，但这次只改“怎么解释”，不改 `authority_judgement` 的优先级和门槛。
  - `公告确认` 仍然优先于 `财报确认 / 研报强化 / 暂无权威验证`
  - `财报确认` 的收紧口径保持不变，本次没有重新放宽
- `src/services/signal_cause_analysis_service.py` 现在会在 `announcement_evidence_summary` 和 `research_evidence_summary` 里优先抽取更具体的催化词：
  - `客户导入`
  - `供不应求`
  - `产能爬坡`
  - `订单放量`
  - `景气上行`
- 输出策略：
  - 对强公告样本，优先保留一层“事件型”标签，如 `重大订单 / 扩产 / 重组 / 回购 / 涨价`
  - 若同一条公告或研报里还能看到更细的经营催化，则再拼一层细标签，例如：
    - `重大订单、客户导入`
    - `扩产、产能爬坡`
    - `供不应求、订单放量`
  - 对研报样本，即使抽到了催化词，也仍保留 headline 主线，避免把 `AI / PCB / 光模块` 这类主线信息洗掉
- 这层改动的目的不是“给所有上涨都补故事”，而是把已经存在于公告/研报文本里的强逻辑提炼出来，让快复盘更接近“为什么这票涨得有逻辑，而不是瞎炒”。
- 2026-05-09 实跑验证产物：
  - `data/manual_runs/fast_review_20260509_authority_catalyst_summary_20260509_v20`
  - `fast_review_strategy_focus.csv` authority 分布：`公告确认=5 / 财报确认=26 / 暂无权威验证=17`
  - 说明：
    - 分布和前一轮相比基本稳定，说明这次没有把 `authority` 判定口径冲乱
    - 今日大部分代表股仍然主要落在 `财报确认`，因为近 7 天内并没有足够稳定的公告/研报文本可供进一步提炼
    - 因此本轮收益主要体现在“有文本证据时解释更具体”，而不是“所有财报股都会自动多出细催化”

2026-05-09 addendum (authority earnings gate tightened + fast-review detail compacted):

- `Fast Review Focus` 的 `authority_judgement=财报确认` 现在不再是“只要拿到季报数字就确认”。
  - 新规则更保守：要求当季财报本身对上涨逻辑有支持性证据，才会落成 `财报确认`。
  - 当前收紧口径会优先排除这几类弱样本：
    - `净利润<=0`
    - `净利同比<=0`
    - `营收同比<=0` 且只剩利润单边改善
  - 这样可以把 `中国卫星 / 润建股份 / 宇环数控 / 浪潮信息 / 汇绿生态` 这类“有报表，但业绩并不构成强确认”的样本重新打回 `暂无权威验证`，避免快复盘把它们误读成业绩兑现。
- 同时保留 `earnings_evidence_summary` 作为辅助快照：
  - 即使没有落成 `财报确认`，导出里仍会保留 `报告期 / 报告日 / 净利润 / 同比` 摘要，便于回看“为什么没确认”。
- `Fast Review Focus` 右侧详情 UI 也同步收紧：
  - `Verified Logic + Mainline + Today/PE/Report/Net Profit` 顶到最上方
  - `Priority / Event Date` 从首屏移除
  - `Authority` 区只保留 `Announcement / Earnings / Research` 三类摘要，减少和 `Quick Read` 的重复
  - `Logic` 改成紧凑分段列表，避免三块大卡片堆叠
- `暂无权威验证` 现在也会输出一条紧凑 authority 结论：
  - 固定先说明 `近7天未见足够强的公告/财报/研报验证`
  - 再补 `业务方向 / 主题映射 / 主线暂按...理解`
  - 最后落到 `现阶段更像交易驱动、题材扩散或趋势延续`
  - 目的不是把弱样本“说成有逻辑”，而是把“为什么暂不确认”说清楚，避免 UI 上出现 authority 空块
- 2026-05-09 复跑产物：
  - `data/manual_runs/fast_review_20260509_authority_gate_tighten_ui_20260509_v16`
  - `fast_review_strategy_focus.csv`：`公告确认=5 / 财报确认=25 / 暂无权威验证=16`
  - 代表样本：
    - `东山精密 / 胜宏科技 / 光迅科技 / 杭电股份` 仍保留 `财报确认`
    - `中国卫星 / 润建股份 / 宇环数控 / 浪潮信息 / 汇绿生态` 已收回到 `暂无权威验证`
  - 继续细化后的 `暂无权威验证` 文案产物：
    - `data/manual_runs/fast_review_20260509_unverified_authority_summary_20260509_v19`

2026-05-09 addendum (authority earnings summary now carries mainline context):

- `Fast Review Focus` 的 `authority_reason_summary` 现在不再只停留在“财报确认 + 季度数字”这一句。
  - 当 authority 命中 `财报确认` 时，若同一只股票已经算出 `business_summary / mainline_judgement / research_evidence_summary`，摘要会继续补上：
    - 业务链条方向
    - 主线判断
    - 近 7 天机构观点是否仍在强化
- 这层改动专门服务你前面反复举的样本：
  - `东山精密` 不再只写“净利润 11.10 亿元、业绩兑现”，还会补出 `PCB/光模块/电子材料，偏 AI 上游材料链 + AI上游材料扩散 + 近7天机构观点仍在强化`
  - `胜宏科技 / 光迅科技` 在没有公告但有明确财报兑现时，也会在首句后补出 `PCB / 光模块光通信 + AI 主线扩散`
- 代表性复跑产物：
  - `data/manual_runs/fast_review_20260509_authority_context_enriched_20260509_v14`
  - `fast_review_strategy_focus.csv`：`rows=46`，`today_change_pct / pe_ratio` 缺失仍为 `0`
- 配套回归：
  - `tests/test_signal_cause_analysis_service.py::test_merge_reason_payload_enriches_earnings_authority_summary_with_mainline_context`
  - `E:\Apps\daily_stock_analysis\.venv\Scripts\python.exe -m pytest tests/test_signal_cause_analysis_service.py tests/test_fast_review_daily_bundle.py tests/test_fast_review_focus_api.py -q` -> `106 passed`

2026-05-09 addendum (strategy focus cold EM warmup + Tencent batch prefetch):

- `Fast Review Focus` 的 `strategy_focus` 轻量行情补齐又修了一层“冷缓存预热”问题，专门解决 `2026-05-09` 实跑里前 17 行有 `today_change_pct / pe_ratio`、后 34 行整段一起留空的尾部 starvation：
  - 根因不是后 34 只票都查不到，而是 `FastReviewFocusService._load_batch_quote_payloads(...)` 只有在东财 `EM` 实时行情缓存已经热起来时才复用批量快照。
  - 一旦遇到冷启动，整批导出就会退化成逐只 `tencent / manager realtime fallback`，最终在总 quote deadline 内只来得及补前排行，后排行根本没被真正尝试。
  - 现在只要同一轮里至少有 2 只 A 股需要补 `today_change_pct / pe_ratio`，服务就会主动打一枪 `EM`，先把共享 realtime cache 热起来，再让后续股票直接复用同一份批量快照。
- 若 `EM` 预热本身也失败，导出层现在不会再回到完全串行的尾部补齐：
  - 会在 per-row deadline 生效前先做一轮小批量 Tencent 轻量行情预抓
  - 因此 `2026-05-09` 的代表尾部样本 `东山精密 / 杭电股份 / 春光集团 / 长芯博创 / 气派科技 / 百济神州` 已能稳定带出 `涨幅 / PE`
  - 实跑产物 `data/manual_runs/fast_review_20260509_tencent_batch_prefetch_verify_20260509_v12` 中，`fast_review_strategy_focus.csv` 已做到 `rows=50`、`today_change_pct / pe_ratio` 缺失 `0`
- 这层修复保持了原来的分层原则不变：
  - 全量 `export_rows` 继续只做轻量行情字段补齐
  - 可见焦点行才进入重型 `report / revenue / net profit / valuation` enrichment
  - 目标是先把整张 `fast_review_strategy_focus.csv` 的 `涨幅 / PE` 补齐率拉上来，而不是把所有隐藏行都送回高耗时 fundamentals 链路
- 配套回归已补到读层测试：
  - `tests/test_fast_review_focus_api.py::test_focus_service_warms_em_batch_quote_cache_when_cold_and_multiple_rows_need_quotes`
  - 用冷缓存 + 腾讯不可用场景锁住这次真实线上问题，避免以后再次退化成“前几行有值、后半段整体留空”

2026-05-09 addendum (strategy focus full-row lightweight quote backfill):

- `Fast Review Focus` 的 `strategy_focus` 导出现在又补了一层“全量轻量行情回填、可见行重型补齐”的分层策略，专门解决隐藏行长期缺 `today_change_pct / pe_ratio` 的问题：
  - `FastReviewFocusService` 新增了仅补市场字段的轻量入口，会先对全量 `export_rows` 统一补 `today_change_pct / pe_ratio`；
  - 原有 `enrich_items(...)` 的重型链路仍只保留给 Markdown 实际可见的焦点子集，用于补 `report / revenue / net profit / valuation fallback`，不把整轮导出重新拖回高耗时。
- 这次收紧后的目标不是“所有隐藏行都做完整 fundamentals”，而是先保证全量 CSV 至少把最基础的 `涨幅 / PE` 填出来，避免只有前排可见股有市场快照、后排全空。

2026-05-09 addendum (authority snapshot backfill + hidden-row export fix):

- `Fast Review Focus` 的快照补齐现在又补了一层“本地 authority 摘要回填”兜底，专门解决 `authority_reason_summary / earnings_evidence_summary` 已经拿到季度和净利，但 `report_period_label / report_date / net_profit_amount` 仍空着的问题：
  - `FastReviewFocusService` 现在会在常规网络 enrichment 之前，先尝试从现有 authority 文本里本地提取 `报告期 / 报告日 / 净利润金额`，避免明明已有 `2026Q1，2026-03-31，净利润...` 却还要再走一轮远端抓取。
  - 缺失判定也同步收紧：只要 `report_date / report_period_label` 已补到，且 `营收 / 净利` 至少有一项落地，就不再把该行强行送回重型 refetch。
- `scripts/run_fast_review_bundle.py` 的 `strategy_focus` 导出阶段也修了一个隐藏行范围问题：
  - 之前只有 Markdown 实际可见的前排 focus 行会走 `_normalize_missing_fields + enrich_items(...)`；
  - 现在会先对全部 `export_rows` 统一跑一遍轻量 `_normalize_missing_fields(...)`，再只对可见行做昂贵 enrichment；
  - 这样既保住性能，也避免 `杭电股份` 这类未进入顶部展示区的行长期缺 `报告期 / 净利` 快照。
- `2026-05-09` 复跑产物 `data/manual_runs/fast_review_20260509_snapshot_backfill_hidden_rows_fix_20260509_v8` 已验证这层修复真实生效：
  - `fast_review_strategy_focus.csv` 行数 `51`
  - `report_period_label` 缺失从上一轮 `33` 直接降到 `0`
  - `net_profit_amount` 缺失从上一轮 `35` 降到 `8`
  - 代表 hidden row `603618 杭电股份` 已补出 `2026Q1 / 2026-03-31 / 8084.19万`
  - 代表样本 `胜宏科技 / 光迅科技 / 中天科技 / 利通电子` 的 Markdown 快照也都已稳定展示 `涨幅 / PE / 2026Q1 / 净利`

2026-05-09 addendum (fast review bundle stdout encoding alignment):

- `scripts/run_fast_review_bundle.py` 的外部子进程日志读取链路补了 Windows 编码对齐修复：
  - 之前父进程 `subprocess.Popen(..., text=True)` 默认仍按本地 `gbk` 解码；
  - 但快复盘子脚本在 `PYTHONIOENCODING=utf-8` 或 UTF-8 输出场景下会写出非 `gbk` 字节，导致 `_stream_process_stdout(...)` 线程直接 `UnicodeDecodeError`，进而把整轮 bundle 运行打断。
- 现在 `run_fast_review_bundle.py` 会显式：
  - 用 `encoding="utf-8"`、`errors="replace"` 读取子进程 stdout
  - 并给子进程默认注入 `PYTHONIOENCODING=utf-8`
  - 使父子进程日志编码保持一致
- 修复后已重新跑通 `2026-05-09` 全量 fast review：
  - 产物目录：`data/manual_runs/fast_review_20260509_post_authority_downloaded_verify_20260509_v6`
  - `fast_review_strategy_focus.csv` 共 `49` 条
  - authority 分布仍稳定为 `公告确认=5 / 财报确认=44`
  - 本轮未再出现 bundle stdout 解码线程崩溃

2026-05-09 addendum (authority downloaded earnings supplement):

- `Fast Review Focus` 的 authority 财报解释又补了一层“自动下载当前报告期业绩目录”的兜底，不再要求人工介入补证据：
  - 当 `general fundamental`、`earnings-only refetch` 仍拿不到足够的结构化财报字段，或底层单票继续出现 `fundamental_bundle timeout` 时，authority 层会直接复用 `scripts/select_earnings_surprise_candidates.py` 里的近期业绩事件目录，补抓当期 `actual report / quick report / forecast` 条目。
  - 这层补抓只服务 authority 的 `earnings` 证据，不会改写原有快复盘选股口径。
  - 为避免“财报季已披露但不在近 7 天”又被误丢弃，当前报告期目录项现在允许带着原始 `report_date / report_periods / revenue_yoy / net_profit_yoy / net_profit_parent` 穿过 authority 归一化层。
- `2026-05-09` 在线复跑产物 `data/manual_runs/fast_review_20260509_authority_downloaded_earnings_20260509_v4`：
  - `fast_review_strategy_focus.csv` 共 `49` 条，其中 `公告确认=5 / 财报确认=44`
  - 之前最后一只顽固样本 `胜宏科技` 已从 `暂无权威验证` 抬升到 `财报确认`
  - 代表样本 `胜宏科技 / 光迅科技 / 杭电股份 / 中天科技 / 利通电子` 现已全部落到 `财报确认`

2026-05-09 addendum (authority fallback short-circuit fix):

- `Fast Review Focus` 的 authority 财报兜底又修了一层真实线上短路问题：
  - 之前如果 `general fundamental` 里只有 `earnings_quality.verdict=unavailable`，但挂着一组默认占位 metrics（如 `insufficient_history / cycle_phase=unavailable / cycle_score=0`），系统会误判成“已经有有效业绩上下文”，从而提前跳过 `earnings-only` 重抓合并。
- 现在这类 `unavailable + default metrics` 不再算有效业绩证据：
  - 只有带 `revenue_yoy / net_profit_yoy / report_date / latest_single_quarter_* / TTM growth` 这类实质财报字段的 metrics，才会阻止 fallback。
- `2026-05-09` 复跑结果：
  - authority 分布从 `公告确认=5 / 财报确认=1 / 暂无权威验证=44`
  - 改善为 `公告确认=5 / 财报确认=5 / 暂无权威验证=40`
  - 代表样本里 `光迅科技 / 杭电股份 / 中天科技 / 利通电子` 已从 `暂无权威验证` 抬升到 `财报确认`
  - 在这轮仅修 short-circuit 的 `v3` 中间复跑里，`胜宏科技` 仍未抬升；当时确认是单票 `get_earnings_fundamental_context(...)` 底层 `fundamental_bundle timeout`，不是 authority 规则再次漏判。该缺口已在上面的 downloaded-earnings supplement 轮次里补掉。

2026-05-09 addendum (authority structured fallback + earnings-only refetch):

- `Fast Review Focus` 的 authority-first 解释层又补了一层结构化证据兜底，目标是减少 `胜宏科技 / 光迅科技 / 杭电股份 / 中天科技 / 利通电子` 这类强趋势样本长期落在 `暂无权威验证`。
- 公告证据不再只依赖混合搜索结果：
  - 现在会优先尝试按复盘日读取 `akshare.stock_gsrl_gsdt_em(...)` 的公司事项数据，作为 `announcements` 的结构化兜底。
- 研报证据也不再只依赖混合搜索结果：
  - 现在会优先尝试按个股读取 `akshare.stock_research_report_em(...)`，把近窗内机构观点压缩进 `research_evidence_summary`。
- 财报证据补了一次“定向重抓”：
  - 如果 `get_fundamental_context(...)` 只返回 `partial` 且 `growth / earnings / earnings_quality` 基本为空，会自动再走一次 `get_earnings_fundamental_context(...)`
  - 仅重抓 `financial / forecast / quick_report`，把能确认当季业绩的结构化字段并回 authority 判断链。
- 当前效果边界：
  - 这次先解决“解释层拿不到结构化证据”的问题
  - 是否能把代表样本稳定抬升到 `财报确认 / 研报强化 / 公告确认`，仍需下一次在线 bundle 重跑确认

2026-05-09 addendum (Fast Review authority-first explanation):

- `Fast Review Focus` now carries a second compact interpretation layer for “是否有更权威的上涨逻辑证据”.
- export/read/API fields added:
  - `authority_judgement`
  - `authority_level`
  - `authority_reason_summary`
  - `authority_evidence_digest`
  - `announcement_evidence_summary`
  - `earnings_evidence_summary`
  - `research_evidence_summary`
  - `authority_time_window_days`
- Priority is fixed as:
  - `公告`
  - `财报`
  - `研报`
  - `普通新闻 / 题材映射`
- Current scope:
  - only `Fast Review Focus`
  - persisted into `fast_review_strategy_focus.csv` and `fast_review_earnings_focus.csv`
  - exposed through `/api/v1/signals/fast-review-focus`
  - rendered as a compact `Authority` block in the fast-review detail panel
- Raw links are still intentionally hidden. The current goal is to let daily review quickly separate `有权威证据` from `偏题材/偏情绪`.

2026-05-09 addendum:

- `Fast Review Focus` now carries an explicit mainline layer in both export and UI:
  - export/read fields: `preferred_industry_label / mainline_judgement / mainline_evidence_sources`
  - UI detail cards: `Mainline` and `Evidence`
- Reading order is now business-led when wide `industry` buckets conflict with the actual trading identity:
  - `中天科技` style rows now open from `光通信/电力设备`
  - instead of opening from a broad bucket such as `新能源`
- AI boom wording is now tied to explicit evidence:
  - `胜宏科技` style rows can land on `AI主线扩散`
  - `杭电股份` style rows can land on `AI上游材料扩散`
  - exported evidence sources are shown directly in the UI, instead of asking users to infer them from the long explanation text

- `Fast Review` explanation text now distinguishes explicit boom contexts more clearly. When the sample has stable AI evidence (`AI算力供应链` / `AI上游材料链` / explicit AI theme mapping), the fallback explanation will describe it as `AI算力链景气扩散` or `AI算力景气向上游材料链扩散`, instead of stopping at generic `结构性走强`.
- The same tightening now avoids forcing generic `光通信 / 海缆 / 电力设备` names into AI-boom wording. Names like `亨通光电` only keep the infrastructure/business explanation unless there is explicit AI-chain evidence.
- Resource-theme suppression is widened again for business-only low-signal copper wording such as `铜产品 / 铜材 / 铜制品`, so infra names are less likely to be misread as `有色 / 涨价资源`.

2026-05-09 addendum (AI boom wording + theme suppression follow-up):

- `Fast Review` fallback explanations now use a more explicit industry-boom phrasing for AI-chain names:
  - AI supply-chain names are described as `AI mainline industry boom / expansion` instead of only `structure strength`.
  - AI upstream-material names are described as `AI mainline industry boom expanding into upstream materials`, which is closer to the intended reading of `AI ??? -> ????`.
- `?? / ???? / ??` theme mapping now has the same kind of low-signal suppression gate as the earlier resource-theme cleanup:
  - if the match only comes from weak business component text such as `??` / `?????`
  - and the higher-level business identity is still `??? / ???? / ???? / ??`
  - the row no longer gets forced into the EV/storage theme bucket.
- Real `2026-05-09` bundle re-run check:
  - `????` now reads as `PCB??AI?????` plus `AI mainline industry boom` wording.
  - `????` now reads as `AI mainline industry boom expanding into upstream materials`.
  - `????` no longer carries the earlier `?? / ???? / ??` false-positive theme label.

2026-05-08 addendum:

- `Fast Review Focus` export/read enrichment now scales its total quote budget with the number of visible rows still missing `today_change_pct / pe_ratio`, with a hard cap. This is aimed at reducing the common case where later visible watch names lose snapshot fields only because earlier core names consumed the old fixed `25s` budget.

- `Fast Review` 的海外主题映射又补了一层资源误伤抑制：如果证据只来自 `product_type/product_name/main_business` 里的 `铜导体` 这类组件词，而更高层的业务归因仍是 `海缆 / 电力设备 / 光通信 / 通信设备`，则不再把样本误映射成 `有色 / 涨价资源`。
- Fast-review explanation bias is tightened again: `supply_demand_bias=earnings` is now reserved for rows with explicit earnings dominance (`earnings_surprise` context, `报告期@事件日`, or strong earnings-summary wording), and no longer assigned from `cause_tags=earnings` alone.
- Second-tier business aliases are expanded for daily review reading stability: `宏和科技 / 润泽科技 / 烽火通信 / 亨通光电` now have stable `business_summary` fallbacks, while plain `光通信` no longer auto-maps to `偏AI算力供应链` without extra AI-specific evidence.
- `Fast Review Focus` read/export layers now prefer canonical `earnings_anchor=报告期@事件日`; older rows that only carried the event date are corrected automatically during read or bundle rebuild.

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
- 2026-05-08 补充：为避免历史 `strategy_focus` CSV 中的旧文案长期覆盖新的主营归因，`run_fast_review_bundle.py` 现会仅对已配置 `business alias` 的高频 AI 链个股强制刷新 `reason_summary / cause_tags / logic`；当前至少覆盖 `东山精密 / 胜宏科技 / 华工科技 / 光迅科技` 这类反复出现的样本。
- 2026-05-08 补充：`AI上游材料链` 的 fallback 识别又向前推进了一层。除 alias 样本外，只要主营/产品文本里明确出现 `覆铜板 / 载板 / 封装载板 / 高频高速 / 高速材料 / 光模块材料 / 封装材料` 这类高级电子材料线索，快复盘也会更倾向写成 `电子材料，偏AI上游材料链`，不必再严格依赖同时命中 `PCB/光模块/服务器` 等更下游字样。
- 2026-05-08 补充：`overseas_theme` 映射继续收紧为“不能只靠 `board` 命中”。当主题证据仅来自概念板块池、没有行业/主营/财务文本共同支持时，快复盘不再直接把样本映射成海外主线，优先压掉 `新能源车 / 充电桩 / 电池` 这类板块噪音误伤。
- 2026-05-08 补充：`earnings + hundred_day_high` 这类交叉样本现在会额外识别“上涨摘要究竟来自哪条信号”。如果 `reason_summary` 实际来自业绩行，即使焦点行的主排序来源是 `hundred_day_high`，阅读层也会继续按 `earnings` 那条去补 `业务侧先按 ... 跟踪 / 当前先按业绩驱动看待`，避免 `福达合金` 这类样本继续只剩纯数字摘要。
- 2026-05-08 补充：`fast_review_earnings_focus.md` 与 `fast_review_summary.md` 里的“今日业绩焦点”区块现在也会同步带出 `rise_reason + tags`，不再只停留在 `earnings_score / expectation / event_date` 这类数字列；像 `福达合金` 这种 `earnings + hundred_day_high` 样本，已经可以在主汇总里直接看到“业务侧先按 触头材料/复层触头/触头元件 跟踪；当前先按业绩驱动看待”。
- 2026-05-08 补充：`fast_review_earnings_focus.md` 与 `fast_review_summary.md` 里的“今日业绩焦点”区块现在也会和 `strategy_focus` 一样带出紧凑 `snapshot` 摘要列，默认直接展示 `涨幅 / PE / 报告期 / 净利`；这样看主 Markdown 时，不用再回到 CSV 才知道当天涨了多少、对应哪个季度、净利润量级大概多少。
- 2026-05-08 补充：`fast_review_earnings_focus.csv/.md` 的导出阶段现在也会复用 `FastReviewFocusService` 做一次轻量补齐；即使样本只命中 `earnings_surprise`、没有叠加 `trend_leader / hundred_day_high`，也会尽量补出 `涨幅 / PE / 报告期 / 营收 / 净利`，避免主汇总里出现半空 `snapshot`。
- 2026-05-08 补充：`quick_report` 样本的 `snapshot` 现在优先按 `event_date / quick_report_announcement_date` 对齐当前季度；如果读取层发现旧缓存里还挂着上一份年报口径、而当前季度金额尚未真正拿到，则会把错配的 `营收 / 净利` 清空，避免像 `百济神州` 这类样本在 `2026Q1` 事件下误展示 `2025FY` 年报金额。
- 2026-05-08 补充：快复盘解释层现在开始显式输出一组结构字段：`business_labels / business_summary / chain_role_label / theme_label / theme_source / earnings_anchor / supply_demand_bias`。目的不是替换原有 `reason_summary`，而是把“主营是什么、偏产业链哪一段、海外题材映射主要来自主营/新闻/board 还是别的来源、当前有没有明确业绩锚点、现在更像供需景气/涨价传导/业绩驱动”拆成稳定字段，后续可以直接用于界面展示和人工校准。
- 2026-05-07 补充：`fast_review_strategy_focus.csv` 现会额外透出 `today_change_pct / pe_ratio / report_date / report_period_label / revenue_amount / net_profit_amount`，`/signals` 的 `Fast Review Focus` 右侧详情同步新增 `Market & Earnings` 区块，直接显示“今天涨幅、PE、对应季度/年报、营收、净利润”。
- 2026-05-08 补充：`fast_review_strategy_focus.md` 与 `fast_review_summary.md` 的焦点表现已同步带出紧凑 `snapshot` 摘要列，默认直接展示 `涨幅 / PE / 报告期 / 营收 / 净利`，减少只看 Markdown 时还要回到 CSV 或右侧详情补信息；导出阶段只对 Markdown 实际展示的焦点行做补充 enrichment，避免同日重建因为全量逐票补行情/估值而拖得过慢。
- 2026-05-08 补充：`FastReviewFocusService` 现已为读取层与导出层的 `report / valuation` 兜底补充加上总预算控制；会优先补齐前排焦点股的季度/年报与 PE 信息，但预算耗尽后直接 fail-open，避免 `/api/v1/signals/fast-review-focus` 页面读取或同日复盘重建再次被逐股 fundamental enrichment 卡慢。
- 2026-05-08 补充：`FastReviewFocusService` 的 `quote` 兜底补充也改为“热缓存优先、冷启动轻量化”模式：只有 `Akshare EM` 全市场实时行情缓存已经热起来时才复用批量快照；冷启动默认改走预算内的单票轻量 quote 补充，避免只为前排少量焦点股而先付出整轮全市场快照预热成本。
- 2026-05-08 补充：`rise_reason` 的 fallback 解释继续细化了 `AI供应链` 内部分层：当主营/产品线更像 `PCB/光模块材料/电子材料/精密组件` 这类上游材料或组件，同时新闻/证据又命中 `供需/景气` 线索时，快复盘会优先写成更接近“AI上游材料链供需偏紧、景气驱动”的话术，而不是只停留在泛化的 `AI映射` 或普通板块轮动描述。
- 2026-05-08 补充：为减少主营文本质量波动对解释的影响，`rise_reason` 还新增了一层轻量 `business alias` 覆盖，当前先稳定补齐 `东山精密 / 胜宏科技 / 华工科技 / 光迅科技` 这类高频 AI 链公司，使其在快复盘中更稳定落到 `AI上游材料链`、`PCB`、`光模块/光通信` 等业务归因，而不是完全受原始文本噪音摆动。
- 2026-05-07 补充：快复盘 `rise_reason` 的 fallback 解释链路现在会优先抽取 `business_labels / business_summary`，用于把 `PCB / 光模块 / 光通信 / AI服务器 / 半导体` 这类业务辨识度直接写进上涨原因；同时 `overseas_theme` 映射会提高业务标签权重，新闻里若出现 `供需 / 景气 / 涨价` 关键词，也会在 `news_logic` 中显式区分催化类型。
- 每日快复盘阅读层现在还会补充 `A类/B类` 复盘分层，以及 `业绩兑现型 / 拐点观察型 / 事件驱动型 / 题材情绪型` 驱动标签；同时新增独立的 `兑现 / 半兑现 / 拐点 / 纯轮动` 四层阶段字段，用来区分“已经走出来”与“仍在观察”的阅读语义；当前语义进一步收紧为：只有“业绩已出现且趋势已启动、但尚未完全确认”的样本才归 `半兑现`，仅有业绩/百日新高但缺少趋势确认的样本下沉为 `拐点`
- 2026-05-07 补充：`fast_review strategy_focus` 的 `A类` 口径已进一步收紧为“价格确认优先”。当前仅保留三类 `A类`：`trend_leader + earnings`、`earnings + hundred_day_high`、`trend_leader + hundred_day_high`（以及显式事件驱动但已出现价格确认的样本）；`trend_leader only` 与 `earnings only` 不再因为 `tier=core` 或单独业绩强度而直接进入 `A类`，避免把大量纯观察票挤进核心复盘池。
- `trend_leader_unified` 的日常 fast-review 共享前筛默认已收紧为 `listed_days>=120 + 60日涨幅>=8.0% + 换手率>=1.2 + 当日涨跌幅为正`，目标是先把准备扫描的 universe 从 700+ 压回更可复盘的规模；这只影响快复盘默认扫描宽度，不改变脚本原始 CLI 默认值
- `2026-05-07 addendum`: the repo-level daily fast-review defaults for `trend_leader_unified` are tightened again to `listed_days>=120 + 60d_change>=17.0% + turnover_rate>=1.5 + positive day`. On the `2026-05-07` probe this narrowed the prepared universe to about `736`; this is a review-width change first, not a guaranteed cold-start runtime win.
- `hundred_day_high` 的 fast-review 入口现已显式下传前筛，不再只依赖脚本内 profile 默认值；当前默认口径为 `listed_days>=120 + 60日涨幅>=12.0% + 换手率>=0.8 + 当日涨跌幅为正 + 排除ST`，保持 `breakout_loose` 的价格确认规则不变，但优先压缩快复盘扫描宽度与历史抓取尾部耗时
- 如需做规则校准，可额外传入 `--manual-review-labels-file config/manual_fast_review_labels.example.json` 这类人工样本文件；bundle 会额外生成 `fast_review_manual_calibration.csv/.md`，并在 `fast_review_summary.md` 中汇总 `matched / mismatch / missing_in_results`，便于按真实复盘样本反修分类口径
- 2026-05-07 补充：`earnings_surprise` 在 fast-review 使用 `recent_event_scope=latest_report_period` 时，近期业绩事件目录现会直接收窄到“当前报告期”抓取，而不是先扫多个旧季度再回头过滤；这只影响快复盘启动阶段的目录宽度，不改变 `earnings_surprise` 的评分、通过阈值或 `A/B + 兑现/半兑现/拐点/纯轮动` 阅读口径。已验证 `2026-05-07` 全 bundle 回放中 `earnings` 耗时从 `254.23s` 收敛到 `108.38s`，总耗时从 `619.7s` 降到 `447.5s`，候选数与阅读层分布保持不变。
- 2026-05-06 补充：上述 A 股上游快照脚本在手工传入非交易日 `--snapshot-date` 时，现会自动回退到最近一个 A 股交易日；例如 `2026-05-04` 会内部解析为 `2026-04-30`，避免短线日跑前置步骤硬跑休市日而拿不到应复用的上游快照。
- 2026-05-06 补充：`earnings_surprise` 的 `low/medium` 快扫在 `recent_event` overlay 已足够覆盖公告摘要与核心增速字段时，现可直接 bootstrap 轻量 bundle，不再为这批样本逐只 fresh fetch 基础面 bundle；这主要用于收敛财报密集日的 `earnings` 尾部耗时，不改变每日主策略入口或评分口径。
- 2026-05-06 补充：对 `db=None` 的 `earnings_surprise` `low/medium` 快复盘链路，当前还会把重型 enrichment 收敛为“全量轻量、少量重点补全”：所有通过样本先走 `lightweight_fast_review` 资金画像；仅按当前优先级排序前 `15` 只补做 `full_priority_refresh` 资金画像，并补抓市场预期参考。未进入重点补全的样本会标记为 `skipped_fast_review`，这属于复盘阅读层提速，不改变主筛选阈值或最终通过/淘汰规则。

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
- `GET /api/v1/signals/fast-review-focus?snapshot_date=<YYYY-MM-DD>`

`/signals` 页面现在除了原有 snapshot 模式，还提供一个 `Fast Review Focus` 轻量视图：

- 主列表只保留 `code/name`、`A/B`、`stage`、`driver`、`signals`、`priority_score`
- 右侧详情面板展示 `reason summary`、`industry/news/technical logic`、`risk flags`、`event date`
- 右侧详情面板新增 `Market & Earnings`，显示 `today change % / PE / report period / net profit / revenue`
- 数据直接读取 `fast_review_strategy_focus.csv`，不改写 `kline_signal_snapshot`

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
2026-05-10 addendum (Peer Check persisted into fast-review artifacts):

- `Fast Review Focus` now persists the `Peer Check` fields into new fast-review CSV artifacts.
- Exported files:
  - `fast_review_strategy_focus.csv`
  - `fast_review_earnings_focus.csv`
- Persisted fields:
  - `peer_group_label`
  - `peer_resonance_summary`
  - `leader_position_summary`
  - `turning_point_peer_summary`
- Goal:
  - keep same-day peer corroboration available in raw historical exports, not only in the read/API layer
  - preserve fail-open behavior for old CSV artifacts that still lack these columns
2026-05-10 addendum (peer-group normalization + business-description fallback):

- `Fast Review Focus` now normalizes fragmented `Peer Check` group names before building same-day peer corroboration.
- Current normalization targets the replay lanes that were most visibly over-split in real runs:
  - `光模块 / 光模块/光通信 / 光模块/光通信/半导体` -> `光模块/光通信`
  - `光通信/电力设备 / 海缆/电力设备/光通信 / 通信设备/电力设备 / 电力电子` -> `通信/电力设备`
  - `铜箔 / 电子级玻纤布 / 电子材料 / 电工材料 / 石英材料` -> `AI上游材料/电子材料`
- When structured labels are missing, the read layer now falls back to business-description keywords from fields such as `technical_logic / reason_summary`, so rows with text clues like `卫星` or `机器人、AI机器视觉、智能制造` no longer have to stay group-less.
2026-05-10 addendum (fast review explanation main-path long-text normalization):

- 这轮不是再改 `Peer Check`，而是把同类长文本归一化补进了快复盘解释主链路本身：
  - 之前 `Peer Check` 已经能把一些主营长句压成短标签
  - 但 `reason_summary / preferred_industry_label` 仍可能直接吃到原始主营句子
- 这次先收掉两类最典型、也最影响复盘扫读的样本：
  - `软磁材料及磁心的研发、生产和销售` / `软磁铁氧体磁粉的研发、生产和销售` -> `软磁材料/磁性材料`
  - `聚酯树脂系列产品的生产销售` -> `树脂/化工材料`
- 这意味着像：
  - `天通股份`
  - `神剑股份`
  这类票后续在 `reason_summary` 里不再直接挂整句主营文案，而会落到更稳定、也更适合后续分组复用的短标签。
- 2026-05-10 实跑验证：
  - 产物目录：`data/manual_runs/fast_review_20260510_long_text_normalize_verify_20260510_v1`
  - `天通股份` 已从 `软磁材料及磁心的研发、生产与销售` 收敛为 `软磁材料/磁性材料`
  - `东山精密 / 胜宏科技 / 华工科技 / 杭电股份` 这批 AI 链样本未被误伤，仍保持 `PCB / 光模块 / 光通信 / AI上游材料链` 一类的短标签
- 边界也要明确：
- 这轮是把“最明显、最重复出现”的长主营句先压掉
- 不是说所有长文本单票组都已经清空
- 同一次 2026-05-10 复跑里，仍能看到个别未覆盖尾巴，例如 `顺灏股份`，说明后面还可以继续扩一批归一化规则

2026-05-10 addendum (fast review special-paper tail cleanup):

- 上一轮提到的 `顺灏股份` 尾巴这次已经补掉：
  - `特种环保纸的研发、生产及销售` 现在会统一收敛为 `特种环保纸`
  - 而且不是只改一个字段，是同时作用到：
    - `preferred_industry_label`
    - `business_summary`
    - `reason_summary`
    - `peer_group_label`
- 2026-05-10 复跑产物：
  - `data/manual_runs/fast_review_20260510_special_paper_normalize_verify_20260510_v2`
  - `顺灏股份` 现在显示为：
    - `当前更像是 特种环保纸 方向走强`
    - `业务更偏 特种环保纸`
    - `同日 特种环保纸 方向暂未看到更多焦点股联动`
- 这说明前面剩下的问题不是另外一套链路坏了，而只是“又一类没列入归一化词典的主营句式”：
  - 主解释层缺规则
  - `Peer Check` 也缺规则
  - 这次两边一起补上后，真实产物就统一了
2026-05-11 addendum (fast review 显式交易日日期口径修复):

- 这次不是改选股条件，而是修正快复盘多策略入口的日期一致性。
- 问题现象是：
  - `run_fast_review_bundle.py --snapshot-date 2026-05-11`
  - `hundred_day_high` 按 `2026-05-11` 跑
  - 但 `trend_leader` 和 `earnings` 会偷偷回退到 `2026-05-08`
- 根因在两个子脚本自己的 `parse_snapshot_date(...)`：
  - 它们把显式传入的交易日先拼成当天 `00:00`
  - 再交给 `get_effective_trading_date("cn", ...)`
  - 交易日历因此把它当成“当天尚未收盘”，回退到前一已完成交易日
- 当前已修正：
  - `scripts/select_trend_leader_candidates.py`
  - `scripts/select_earnings_surprise_candidates.py`
  - 对于显式 `date / YYYY-MM-DD`，现在按当天收盘后时点解析，不再错误回退
  - 对于节假日/非交易日，仍然保持原有的“回退到上一交易日”逻辑
- 2026-05-11 实跑验证：
  - 目录：`data/manual_runs/fast_review_20260511_date_fix_verify_20260511_233939`
  - 日志已对齐为：
    - `earnings_surprise_selector: snapshot_date=2026-05-11`
    - `trend_leader_selector: snapshot_date=2026-05-11`
    - `hundred_day_high_selector: snapshot_date=2026-05-11`
  - 汇总结果：
    - `earnings = 2`
    - `trend_leader = 9`
    - `hundred_day_high = 256 (export 30)`
- 这次修复的意义不是“让数量变多”本身，而是保证同一份每日复盘里三条主线使用同一交易日口径，避免你看到混杂了两个交易日的结果。
