# 每日复盘 A/B 清单与驱动标签设计 / Fast Review A-B List And Driver Classification Design

## 1. 背景 / Background

当前每日快复盘默认由三条主策略组成：

- `earnings_surprise`
- `hundred_day_high`
- `trend_leader_unified`

它们已经能完成“筛票”，但还不能稳定回答以下复盘问题：

1. 这只票属于“已经走出来”还是“刚启动”？
2. 它是业绩兑现、基本面拐点、硬事件驱动，还是纯题材情绪？
3. 没有财报密集披露时，应该如何继续使用现有三条主策略做日常复盘？

The current daily fast-review bundle already selects candidates through three core strategies:

- `earnings_surprise`
- `hundred_day_high`
- `trend_leader_unified`

However, the current outputs do not consistently answer these review questions:

1. Is a stock already confirmed, or only starting?
2. Is the move driven by earnings delivery, a business turning point, a hard event, or theme/sentiment?
3. How should the workflow behave outside dense earnings-report windows?

## 2. 目标 / Goals

本设计只做“复盘阅读层分类”，不改三条主策略本身。

目标：

1. 在现有快复盘产物之上，新增一套稳定的阅读顺序。
2. 把每日候选拆成两张清单：
   - `A类`：已经走出来，适合右侧跟随
   - `B类`：刚启动，适合提前观察
3. 为 A/B 两类候选增加统一驱动标签：
   - `业绩兑现型`
   - `拐点观察型`
   - `事件驱动型`
   - `题材情绪型`
4. 明确“无财报密集披露期”时，如何继续看业绩，而不是简单忽略业绩。

This design is intentionally limited to the review-reading layer. It does not change the three strategy selectors themselves.

Goals:

1. Define a stable daily reading order on top of current outputs.
2. Split daily candidates into two operating lists:
   - `A-list`: already confirmed, suitable for right-side follow-through
   - `B-list`: early-stage, suitable for observation
3. Add a unified driver taxonomy:
   - `Earnings Delivery`
   - `Turning Point Watch`
   - `Event Driven`
   - `Theme/Sentiment Driven`
4. Clarify how to keep using earnings evidence even when the market is outside a dense reporting window.

## 3. 非目标 / Non-Goals

本次不做：

- 不修改 `earnings_surprise`、`hundred_day_high`、`trend_leader_unified` 的筛选逻辑
- 不新增第四条默认每日主策略
- 不要求首版就把专题扫描并入默认主链路
- 不把新分类直接写回 `/signals` snapshot
- 不把“驱动标签”误当成新的交易信号

Not in scope:

- No changes to selector logic for the three daily strategies
- No fourth default daily strategy
- No requirement to merge topic scanners into the default mainline in v1
- No immediate snapshot persistence for the new review labels
- No treating driver labels as standalone trading signals

## 4. 现状与可复用能力 / Current State And Reusable Assets

现有阅读层已经具备一部分必要字段：

- `fast_review_summary.md`
- `fast_review_strategy_focus.csv/.md`
- `fast_review_earnings_focus.csv/.md`
- `trend_leader_unified_watchlist.csv/.md`

现有字段里，已经可直接复用：

- `trend_hundred_relation`
- `earnings_strategy_score`
- `earnings_strategy_gate_status`
- `event_date`
- `market_expectation_summary`
- `market_expectation_reference_label`
- `reason_summary`
- `cause_tags`
- `cause_tags_zh`

Existing review outputs already expose enough signals to support a first version:

- `fast_review_summary.md`
- `fast_review_strategy_focus.csv/.md`
- `fast_review_earnings_focus.csv/.md`
- `trend_leader_unified_watchlist.csv/.md`

Reusable fields already available:

- `trend_hundred_relation`
- `earnings_strategy_score`
- `earnings_strategy_gate_status`
- `event_date`
- `market_expectation_summary`
- `market_expectation_reference_label`
- `reason_summary`
- `cause_tags`
- `cause_tags_zh`

结论：

- `A/B` 分类可以先做阅读层规则，不需要先改三条策略。
- 驱动标签也可以先做阅读层解释，不必先改快照结构。
- `B类` 的质量上限会受限于当前默认只跑三条主策略；若后续要提高“早启动”识别能力，再按需借助专题扫描。

Conclusions:

- A/B classification can be added at the reading layer first.
- Driver labels can also start as reading-layer interpretation.
- B-list quality will still be bounded by the fact that only three strategies run by default; topic scanners can be added later if needed.

## 5. 总体方案 / Proposed Approach

推荐方案：保留现有三条主策略不动，在 `run_fast_review_bundle.py` 已经输出的阅读层结果之上，增加一套“二层分类口径”。

第一层：阶段分类

- `A类 / A-list`
- `B类 / B-list`

第二层：驱动标签

- `业绩兑现型 / Earnings Delivery`
- `拐点观察型 / Turning Point Watch`
- `事件驱动型 / Event Driven`
- `题材情绪型 / Theme-Sentiment Driven`

Recommendation: keep the three daily selectors unchanged, and add a two-layer interpretation model on top of existing review artifacts.

Layer 1: stage classification

- `A-list`
- `B-list`

Layer 2: driver classification

- `Earnings Delivery`
- `Turning Point Watch`
- `Event Driven`
- `Theme-Sentiment Driven`

## 6. A/B 清单定义 / A-B List Definitions

### 6.1 A类 / A-list

定义：

- 已经走出来
- 更偏右侧跟随
- 强调结构确认，而不是猜启动

优先来源：

1. `trend_leader_unified + hundred_day_high + earnings_surprise`
2. `trend_leader_unified`
3. `earnings_surprise + hundred_day_high`
4. 高质量 `hundred_day_high`

Definition:

- Already moved into a confirmed state
- Intended for right-side follow-through
- Emphasizes confirmation over anticipation

Priority sources:

1. Triple overlap of `trend_leader_unified + hundred_day_high + earnings_surprise`
2. `trend_leader_unified`
3. `earnings_surprise + hundred_day_high`
4. High-quality `hundred_day_high`

### 6.2 B类 / B-list

定义：

- 刚启动或刚出现苗头
- 更偏提前观察
- 核心目标是找“未来可能升级成 A 类”的票

优先来源：

1. `trend_leader_unified_watchlist`
2. `fast_review_strategy_focus` 里的 `观察候选`
3. 纯 `hundred_day_high` 中更早期、未形成龙头共识的样本

Definition:

- Early-stage or emerging names
- Intended for anticipation and observation
- The core purpose is to identify names that may later graduate into the A-list

Priority sources:

1. `trend_leader_unified_watchlist`
2. `watch` names inside `fast_review_strategy_focus`
3. Earlier-stage `hundred_day_high` names that have not yet formed trend-leader consensus

## 7. 驱动标签定义 / Driver Label Definitions

### 7.1 业绩兑现型 / Earnings Delivery

定义：

- 财报或业绩事件本身已经给出较强证明
- 价格随后或同步完成确认

强证据：

- 来自 `earnings_surprise`
- `earnings_strategy_score` 较高
- `event_date` 近
- 同时进入 A 类，特别是和 `hundred_day_high`、`trend_leader_unified` 共振

Definition:

- The report or earnings event already provides strong evidence
- Price confirms that evidence

### 7.2 拐点观察型 / Turning Point Watch

定义：

- 历史业绩不强或不稳定
- 最近出现改善迹象
- 价格开始先行反映，但尚未达到“完全兑现”的强度

判定原则：

- 必须同时看到“改善迹象 + 价格配合”
- 只靠价格强，不足以判成拐点

Definition:

- Historical earnings quality was weak or unstable
- Recent evidence suggests improvement
- Price has started to recognize the improvement, but evidence is not yet strong enough to call it full delivery

Rule:

- Require both improvement evidence and price cooperation
- Price strength alone is insufficient

### 7.3 事件驱动型 / Event Driven

定义：

- 不是单纯靠财报
- 也不是纯情绪题材
- 由“硬事件”直接推动

硬事件示例：

- 重组
- 大订单
- 中标
- 产能投放
- 资产注入
- 重大合作
- 明确涨价传导

Definition:

- Not primarily driven by periodic earnings delivery
- Not merely driven by theme or sentiment
- Directly driven by a concrete event

Typical hard-event examples:

- restructuring
- large orders
- winning bids
- capacity release
- asset injection
- major cooperation
- explicit price-pass-through catalysts

### 7.4 题材情绪型 / Theme-Sentiment Driven

定义：

- 板块热度、政策映射、情绪扩散、资金博弈占主导
- 缺乏足够财报证据，也缺乏足够硬事件证据

注意：

- 该标签不是贬义
- 它只是要求用不同的持有、跟踪和风控方式处理

Definition:

- Board/theme heat, policy mapping, sentiment diffusion, and capital-game dynamics dominate
- Evidence is not yet strong enough for either earnings delivery or hard-event classification

Note:

- This is not a negative label
- It simply implies a different handling and risk discipline

## 8. 驱动标签判定顺序 / Driver Classification Order

每日复盘按以下顺序判定，避免互相混淆：

1. 先判 `业绩兑现型`
2. 再判 `拐点观察型`
3. 再判 `事件驱动型`
4. 最后归入 `题材情绪型`

顺序含义：

- 业绩证据最硬，优先级最高
- 拐点必须有改善线索，不能被题材冒充
- 事件驱动要和纯情绪分开
- 前面都不足时，才归 `题材情绪型`

Apply the following classification order:

1. `Earnings Delivery`
2. `Turning Point Watch`
3. `Event Driven`
4. `Theme-Sentiment Driven`

Meaning:

- Earnings evidence is strongest and has top priority
- Turning points require improvement evidence and should not be faked by pure price action
- Event-driven names must be separated from pure theme moves
- Theme/sentiment is the fallback when harder evidence is insufficient

## 9. 无财报密集披露期口径 / Off-Season Earnings Interpretation

无财报密集披露期，不意味着“不看业绩”。

正确口径：

1. 业绩从“主驱动”降为“解释层和验证层”
2. 仍然要看过去一个季度、上个季度、甚至更早季度的质量
3. 对当前强势股，要明确它是：
   - 过去季报已经兑现，现在继续走强
   - 历史弱，但可能出现改善拐点
   - 没有明显财务支持，主要靠事件或题材

Outside dense report windows, the system should not stop looking at earnings.

Correct interpretation:

1. Earnings move from primary trigger to explanation/validation layer
2. Past-quarter and prior-quarter quality still matter
3. Strong names must still be explicitly categorized as:
   - already supported by prior earnings delivery
   - possibly improving from a weak base
   - mainly supported by events or theme/sentiment

## 10. 每日复盘顺序 / Daily Reading Order

推荐固定为四步：

1. 先看 `fast_review_summary_latest.md`
   - 看三条主策略数量
   - 看 `intersection`
   - 判断当天更偏确认还是发散
2. 再看 `fast_review_strategy_focus`
   - 先抽 A 类
   - 再看 B 类
3. 再给每只 A/B 候选打驱动标签
4. 最后看 `fast_review_earnings_focus`
   - 用于解释和验证
   - 不反客为主

Recommended four-step routine:

1. Read `fast_review_summary_latest.md`
   - check strategy counts
   - check overlap/intersection
   - judge whether the day is confirmation-heavy or diffusion-heavy
2. Read `fast_review_strategy_focus`
   - build the A-list first
   - then build the B-list
3. Assign driver labels to each selected name
4. Review `fast_review_earnings_focus`
   - use it for explanation and validation
   - do not let it override the A/B framework

## 11. 与现有三条每日策略的关系 / Relationship With Existing Daily Strategies

本方案与现有三条每日策略不冲突。

分工如下：

- 三条主策略负责“筛票”
- 本方案负责“解释与分层”

因此本方案应被定义为：

- `复盘阅读层增强`

而不是：

- `新增第四条策略`
- `改写原有三条策略的选股逻辑`

This design does not conflict with the current three daily strategies.

Responsibilities:

- the three strategies handle selection
- this design handles interpretation and layering

Therefore, this should be treated as:

- `an enhancement to the review-reading layer`

not as:

- `a fourth daily strategy`
- `a rewrite of the existing selector logic`

## 12. 数据落点建议 / Suggested Output Placement

首版建议只落在复盘阅读层，不写回 snapshot。

推荐承载位置：

- `fast_review_strategy_focus.csv/.md`
- `fast_review_summary.md`
- 如有需要，可补一个更轻量的 A/B 分类摘要文件

For v1, keep the new classification inside the review-reading layer only.

Suggested landing points:

- `fast_review_strategy_focus.csv/.md`
- `fast_review_summary.md`
- optionally, a lighter A/B summary artifact

## 13. 风险与边界 / Risks And Boundaries

主要风险：

1. `拐点观察型` 最容易被误判
2. `事件驱动型` 与 `题材情绪型` 的边界需要依赖较清晰的文本证据
3. 当前默认只跑三条主策略，B 类覆盖面天然有限

控制原则：

- 判不准时，优先降级
- 宁可先标 `题材情绪型` 或 `拐点观察型`
- 不要轻易把纯价格强势解释成基本面拐点

Main risks:

1. `Turning Point Watch` is easiest to over-label
2. the line between `Event Driven` and `Theme-Sentiment Driven` depends on reliable text evidence
3. B-list coverage is naturally limited because only three strategies run by default

Control principles:

- downgrade when uncertain
- prefer `Theme-Sentiment Driven` or `Turning Point Watch` over false certainty
- do not casually reinterpret pure price strength as a business turning point

## 14. 实施结论 / Implementation Conclusion

建议把本设计作为“日常快复盘的解释框架”落地，而不是策略层重构。

最小可行落地方式：

1. 保留现有三条每日策略
2. 在快复盘聚合层按规则生成 A/B 清单
3. 在 A/B 清单上追加四类驱动标签
4. 优先服务人工复盘与观察池维护

This design should be implemented as an interpretation framework for the fast-review workflow, not as a selector refactor.

Minimum viable rollout:

1. keep the three daily strategies unchanged
2. generate A/B lists in the fast-review aggregation layer
3. append the four driver labels to those lists
4. optimize for manual review and watchlist maintenance first
