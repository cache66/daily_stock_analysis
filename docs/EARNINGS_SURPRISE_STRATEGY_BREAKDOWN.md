# 业绩线专章：`earnings_surprise` 判断字段、权重、门槛与快照字段

本文档只讲一件事：

- 当前 `earnings_surprise` 到底是怎么判定通过/拦截的
- 混合评分各因子怎么加权
- 默认门槛是什么
- 快照里到底会落哪些字段

如果你只想先看总表，请看：

- `docs/LOCAL_STRATEGY_CATALOG.md`

如果你想看业绩线的使用方式、常用命令和回测入口，请看：

- `docs/EARNINGS_SURPRISE_TRACKING.md`

如果你想看更偏复盘和筛票的实战判读，请看：

- `docs/EARNINGS_SURPRISE_PLAYBOOK.md`

## 1. 真源文件

当前这条业绩线的真实实现主要以下面几处为准：

- 扫描入口：`scripts/select_earnings_surprise_candidates.py`
- 快照查询抽取：`src/services/signal_snapshot_service.py`
- API 字段定义：`api/v1/schemas/signals.py`
- Web 字段定义：`apps/dsa-web/src/types/signals.ts`
- 回归测试：`tests/test_earnings_surprise_signal_flow.py`

## 2. 先看总流程

当前 `earnings_surprise` 不是“只看财报文本关键字”的简单筛选，而是四段式流程：

1. 先取原始输入字段
2. 先做文本正负向识别
3. 再复用 `earnings_quality` 生成多因子质量画像
4. 计算混合策略分数 `earnings_strategy_score`
5. 先过硬门槛，再看是否满足通过阈值
6. 通过后再按 `event_key` 做历史去重
7. 最终把结果写入快照，供 `/signals` 和历史回看使用

可以粗略理解成：

- 负向文本先一票否决
- 没有被硬门槛拦住时，再靠“文本 / 增长 / 质量”做确认
- 最后不是简单 `OR`，而是用 100 分制混合打分来排序和决定是否通过

## 3. 输入字段与默认门槛

### 3.1 默认阈值

`EarningsSurpriseCriteria` 当前默认值如下：

| 字段 | 默认值 | 含义 |
| --- | --- | --- |
| `min_revenue_yoy` | `10.0` | 营收同比阈值 |
| `min_net_profit_yoy` | `20.0` | 净利润同比阈值 |
| `min_roe` | `None` | 默认不启用 ROE 硬阈值 |
| `require_positive_text` | `False` | 默认不强制必须命中正向文本 |
| `require_growth_thresholds` | `False` | 默认不强制必须同时满足增长阈值 |
| `dedupe_by_event_key` | `True` | 默认开启按事件去重 |

### 3.2 默认正负向关键词

默认正向关键词：

- `预增`
- `扭亏`
- `增长`
- `大增`
- `高增`
- `向好`
- `超预期`
- `improve`
- `improved`
- `beat`
- `beats`
- `better than expected`
- `better-than-expected`
- `strong earnings`

默认负向关键词：

- `预减`
- `预亏`
- `首亏`
- `续亏`
- `转亏`
- `下滑`
- `下降`
- `亏损`
- `不及预期`
- `miss`
- `missed`
- `below expectation`
- `below expectations`
- `warning`

### 3.3 主要输入来源

脚本主要用到以下输入：

| 模块 | 关键字段 | 用途 |
| --- | --- | --- |
| `growth` | `revenue_yoy` `net_profit_yoy` `roe` | 判断增长阈值是否命中 |
| `earnings` | `forecast_summary` `quick_report_summary` | 判断正负向文本 |
| `earnings.financial_report` | `report_date` `revenue` `net_profit_parent` `operating_cash_flow` | 事件日期、财报质量和基础快照字段 |
| `earnings.financial_report_series` | 多季度序列 | 用于 `earnings_quality` 连续性分析 |
| `earnings_quality` | `score_total` `verdict` `risk_flags` 等 | 混合打分和硬风险判断 |

## 4. 三类确认信号

当前通过逻辑里，会用到三类“确认信号”：

| 信号 | 条件 | 说明 |
| --- | --- | --- |
| `positive_text_signal` | 命中正向文本关键词 | 偏公告语义确认 |
| `growth_signal` | 所有已启用增长阈值同时满足 | 偏财务数值确认 |
| `earnings_quality_signal` | `verdict in {good, strong}` 或 `score_total >= 65` | 偏多季度质量确认 |

其中：

- `confirmation_signal = positive_text_signal or growth_signal or earnings_quality_signal`
- 当策略分数落在观察区间时，是否存在 `confirmation_signal` 会直接影响能不能通过

### 4.1 兼容用的 `signal_score`

脚本里还保留了一个更简单的辅助分数 `signal_score`，它不是主排序逻辑，但仍会落到快照里。

当前加分规则：

| 来源 | 分值 |
| --- | --- |
| 命中正向文本 | `+2` |
| 营收同比过阈值 | `+1` |
| 净利润同比过阈值 | `+2` |
| ROE 过阈值 | `+1` |
| `earnings_quality` 为 `good` / `score_total >= 65` | `+1` |
| `earnings_quality` 为 `strong` / `score_total >= 80` | `+2` |

所以它更像“轻量命中计数”，不是最终放行依据。

## 5. 混合策略评分 `earnings_strategy_score`

### 5.1 主评分结构

当前主评分上限是 `100` 分，计算方式是：

`正向加权分之和 - 风险扣分`

具体结构如下：

| 因子 | 来源字段 | 原始上限 | 权重 | 是否直接计入总分 |
| --- | --- | --- | --- | --- |
| 事件强度 | 文本命中 + 增长确认 + `revenue_yoy/net_profit_yoy` | `20` | `24` | 是 |
| 增长连续性 | `growth_continuity_score` | `35` | `26` | 是 |
| 季度连续性 | `quarterly_continuity_score` | `15` | 无单独权重 | 否，当前只做诊断展示 |
| 利润质量 | `profit_quality_score` | `25` | `22` | 是 |
| 盈利能力 | `profitability_score` | `20` | `14` | 是 |
| 披露信号 | `disclosure_signal_score` | `20` | `8` | 是 |
| 周期位置 | `cycle_phase` -> `cycle_score` | `10` | `10` | 是 |
| 事件新鲜度 | `event_date/report_date` -> `freshness_score` | `10` | `10` | 是 |
| 事件后反应 | `earnings_post_event_reaction_label` + 1D/3D 响应 | `10` | `6` | 是 |
| 多季度持续质量 | `earnings_financial_series_continuity_score` | `20` | `6` | 是 |
| 行业确认 | `earnings_industry_confirmed` + 同组确认度 | `10` | `4` | 是 |
| 风险扣分 | `risk_flags` | 封顶 `30` | 扣分项 | 是 |

一个很重要的实现细节：

- `quarterly_continuity_score` 会落快照，也会出现在分解结构里
- 但从当前脚本实现看，它没有单独进入总分求和
- 它更像“增长连续性”的辅助诊断字段，而不是独立加权因子

### 5.2 各因子如何折算

脚本采用统一折算方式：

`weighted_score = min(max(raw_score, 0), raw_max) / raw_max * weight`

也就是说：

- 原始分超过上限会被截断
- 小于 0 会按 0 处理
- 最终按该因子的目标权重折算到 100 分体系里

## 6. 周期分与新鲜度分

### 6.1 周期位置评分

`cycle_phase` 当前映射如下：

| `cycle_phase` | 对应分数 |
| --- | --- |
| `reaccelerating` | `10` |
| `expanding` | `9` |
| `recovering` | `7` |
| `mature` | `6` |
| `mixed` | `4` |
| `unavailable` | `3` |
| `downcycle` | `0` |
| 其他未知值 | `4` |

### 6.2 事件新鲜度评分

事件日期优先级：

1. `quick_report_announcement_date`
2. `forecast_announcement_date`
3. `financial_report.report_date`

评分规则：

| 距离快照日 | 分数 | 标签 |
| --- | --- | --- |
| `<= 7` 天 | `10` | `very_fresh` |
| `<= 15` 天 | `9` | `fresh` |
| `<= 30` 天 | `8` | `recent` |
| `<= 60` 天 | `6` | `aging` |
| `<= 120` 天 | `4` | `stale` |
| `> 120` 天 | `2` | `very_stale` |
| 无法判断事件日期 | `2` | `unknown` |

## 7. 风险扣分与硬风险拦截

### 7.1 风险扣分

`risk_flags` 当前分三级扣分：

| 级别 | 单项扣分 | 代表 flags |
| --- | --- | --- |
| severe | `12` | `cycle_phase_downcycle` `operating_cash_flow_non_positive` `net_profit_yoy_non_positive` `profit_growth_diverges_from_revenue_growth` |
| medium | `5` | `revenue_yoy_non_positive` `quarterly_growth_trend_deteriorating` `cashflow_conversion_soft` `roe_weak` `gross_margin_thin` `recent_profit_growth_not_consistently_positive` `recent_revenue_growth_not_consistently_positive` `quarterly_dual_growth_streak_missing` |
| mild | `2` | 其他未归类风险标签 |

总扣分封顶 `30` 分。

### 7.2 硬风险拦截

除了扣分外，还有两类“直接拦截”的硬门槛：

| 拦截原因 | 条件 |
| --- | --- |
| `cycle_phase_downcycle_without_text_or_growth_confirmation` | `cycle_phase == downcycle` 且没有正向文本确认，也没有增长确认 |
| `cashflow_non_positive_and_profit_not_positive` | `operating_cash_flow_non_positive`，同时净利润同比未转正，且没有正向文本确认 |

只要命中任一硬风险拦截，候选会被直接打回：

- `earnings_strategy_gate_status = blocked_quality_risk`

## 8. 放行门槛与标签

### 8.1 策略标签

`earnings_strategy_score` 当前标签规则：

| 分数区间 | 标签 |
| --- | --- |
| `>= 75` | `strong` |
| `>= 60` | `qualified` |
| `>= 35` | `watch` |
| `< 35` | `weak` |

### 8.2 通过规则

真正的放行门槛不是只看标签，而是下面这组逻辑：

| 条件 | 结果 |
| --- | --- |
| `score >= 55` | 直接通过 |
| `35 <= score < 55` 且存在任一确认信号 | 通过 |
| `35 <= score < 55` 且没有确认信号 | 不通过 |
| `score < 35` | 不通过 |

也就是：

- `55` 分是硬通过线
- `35` 到 `55` 是观察区，必须有确认信号辅助
- 低于 `35` 默认不通过

## 9. `earnings_strategy_gate_status` 全部状态

当前脚本里实际会写出的 gate 状态如下：

| 状态 | 含义 |
| --- | --- |
| `blocked_negative_text` | 命中负向业绩文本，直接拦截 |
| `blocked_missing_positive_text` | 开启了 `--require-positive-text`，但没有命中正向文本 |
| `blocked_missing_growth_thresholds` | 开启了 `--require-growth-thresholds`，但增长阈值未全部满足 |
| `blocked_quality_risk` | 命中硬风险拦截 |
| `blocked_low_strategy_score` | 有确认信号，但混合策略分仍低于放行要求 |
| `blocked_missing_confirmation` | 分数处于观察区或偏低，且没有任何确认信号 |
| `blocked_duplicate_event` | 通过后又被事件去重拦下 |
| `passed_strategy_score` | `score >= 55` 直接通过 |
| `passed_watch_with_confirmation` | `35 <= score < 55`，但有确认信号，因此放行 |

## 10. `event_key` 去重逻辑

当前默认按事件去重：

- `dedupe_by_event_key = True`

只有先通过主判断后，才会再做去重检查。

用途是避免：

- 同一季度、同一条公告、同一事件被多天重复落为新信号

如果被识别为历史上已记录的同一事件：

- `duplicate_event = true`
- `earnings_strategy_gate_status = blocked_duplicate_event`

## 11. 快照字段总览

### 11.1 直接落在 `metrics_payload` 的核心字段

| 字段 | 含义 |
| --- | --- |
| `event_date` | 当前优先使用的事件日期 |
| `forecast_announcement_date` | 业绩预告公告日 |
| `quick_report_announcement_date` | 快报公告日 |
| `report_date` | 报告期日期 |
| `revenue_yoy` | 营收同比 |
| `net_profit_yoy` | 净利润同比 |
| `roe` | ROE |
| `positive_text_signal` | 是否命中正向文本 |
| `negative_text_signal` | 是否命中负向文本 |
| `growth_signal` | 是否满足增长阈值确认 |
| `earnings_quality_signal` | 是否满足质量确认 |
| `signal_score` | 轻量辅助分 |
| `earnings_strategy_score` | 混合策略总分 |
| `earnings_strategy_label` | 总分标签 |
| `earnings_strategy_gate_status` | 放行/拦截状态 |
| `reason_summary` | 供 `/signals` 和导出展示的摘要 |

### 11.2 因子层字段

| 字段 | 含义 |
| --- | --- |
| `earnings_growth_continuity_score` | 增长连续性原始分 |
| `earnings_quarterly_continuity_score` | 季度连续性原始分 |
| `earnings_profit_quality_score` | 利润质量原始分 |
| `earnings_profitability_score` | 盈利能力原始分 |
| `earnings_disclosure_signal_score` | 披露信号原始分 |
| `earnings_cycle_score` | 周期位置原始分 |
| `earnings_event_freshness_score` | 事件新鲜度分 |
| `earnings_risk_penalty` | 风险扣分 |
| `earnings_days_since_event` | 距离事件的天数 |
| `earnings_event_freshness_label` | 新鲜度标签 |
| `earnings_industry_confirmation_score` | 行业确认原始分（来自行业上下文） |
| `earnings_industry_confirmed` | 是否满足行业确认 |
| `earnings_industry_confirmation_hint` | 行业确认提示（如分组/板块信息） |

### 11.3 质量画像字段

| 字段 | 含义 |
| --- | --- |
| `earnings_quality_verdict` | 质量结论，如 `good` / `strong` |
| `earnings_quality_score` | 质量总分 |
| `earnings_quality_cycle_phase` | 周期阶段 |
| `earnings_quality_quarterly_trend` | 最近季度趋势 |
| `earnings_quality_dual_positive_streak` | 连续双正增长季度数 |
| `earnings_quality_positive_signals` | 正向质量信号列表 |
| `earnings_quality_risk_flags` | 风险标签列表 |

### 11.4 结构化诊断字段

| 字段 | 含义 |
| --- | --- |
| `earnings_strategy_factor_breakdown` | 完整因子拆分结构，含各项 raw/weight/weighted |
| `earnings_hard_risk_blocked` | 是否触发硬风险拦截 |
| `earnings_hard_risk_reasons` | 硬风险原因列表 |
| `event_key` | 当前事件指纹 |
| `duplicate_event` | 是否因去重被拦截 |

## 12. `/signals` 当前能直接看到哪些业绩线字段

从 `SignalSnapshotService`、API schema 和 Web 类型定义看，当前 `/signals` 已直接抽取这些业绩线字段：

- `earnings_quality_signal`
- `earnings_strategy_score`
- `earnings_strategy_label`
- `earnings_strategy_gate_status`
- `earnings_growth_continuity_score`
- `earnings_profit_quality_score`
- `earnings_profitability_score`
- `earnings_disclosure_signal_score`
- `earnings_cycle_score`
- `earnings_event_freshness_score`
- `earnings_risk_penalty`
- `earnings_quality_verdict`
- `earnings_quality_score`
- `earnings_quality_cycle_phase`
- `earnings_quality_quarterly_trend`
- `earnings_quality_dual_positive_streak`

但下面这些更底层的结构化诊断字段，当前并不是 `/signals` 列表项的直接平铺字段：

- `earnings_strategy_factor_breakdown`
- `earnings_hard_risk_reasons`
- `earnings_quality_positive_signals`
- `earnings_quality_risk_flags`
- `event_key`
- `duplicate_event`

也就是说：

- 现在 `/signals` 适合看主结果和关键打分
- 如果你要追更细的“为什么被扣分”“到底是哪个硬风险命中”，还得回到原始快照 payload 或脚本导出结果

## 13. 实操上怎么读一条 `earnings_surprise`

建议按下面顺序看：

1. 先看 `earnings_strategy_gate_status`
2. 再看 `earnings_strategy_score`
3. 再看 `earnings_quality_verdict / score / cycle_phase`
4. 再看 `earnings_event_freshness_score`
5. 最后看 `reason_summary`

最常见的三种读法：

- `passed_strategy_score`：说明这条票本身综合质量就已经够强
- `passed_watch_with_confirmation`：说明分数还在观察区，但文本、增长或质量里至少有一项额外确认
- `blocked_quality_risk`：通常说明这条票虽然可能有局部亮点，但现金流、周期或利润质量出现了结构性风险

## 14. 当前口径最容易误解的点

### 14.1 不是严格的 sell-side “超预期”

`earnings_surprise` 当前更准确的名字是：

- 业绩强势代理信号

它更偏：

- 留样
- 排序
- 历史复盘

而不是严格意义上的一致预期偏差模型。

### 14.2 `quarterly_continuity_score` 不是单独加权项

虽然这个字段名字很像主因子，但当前实现里：

- 它会被记录
- 会被展示
- 但不会单独进入总分求和

### 14.3 负向文本优先级很高

只要命中负向文本：

- 不管增长数字多漂亮
- 也不管质量分是否还行

都会先被 `blocked_negative_text` 拦下。

## 15. 相关文档

- `docs/EARNINGS_SURPRISE_TRACKING.md`
- `docs/EARNINGS_SURPRISE_QUALITY_SIGNAL.md`
- `docs/LOCAL_STRATEGY_CATALOG.md`
