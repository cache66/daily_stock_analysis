# 资金层策略专章：`capital_profile`

这份文档专门解释当前项目里统一的“资金层”判断口径。

它不是一条单独跑全市场的扫描线，而是一个可复用的公共能力，服务于后续主轴：

- 逻辑是否成立
- 资金是否认可
- 趋势是否确认
- 最终是否兑现到业绩

当前代码入口：

- `src/services/capital_profile_service.py`

当前首批接入的策略：

- `scripts/select_earnings_surprise_candidates.py`
- `scripts/select_monthly_slow_rise_candidates.py`

## 1. 这套资金层在解决什么问题

它主要回答的不是“这票能不能涨”，而是：

- 现在有没有资金承接
- 承接是短线单日脉冲，还是近几天在持续
- 这只票是有结构资金参与，还是只是图形看起来不错

换句话说，它是给“逻辑线”和“趋势线”补一层市场验证。

## 2. 当前输出字段

`CapitalProfileService.build_stock_profile(...)` 当前统一输出这些字段：

### 2.1 核心分数字段

| 字段 | 含义 | 取值 |
| --- | --- | --- |
| `capital_consensus_score` | 资金共识分 | `0~3` |
| `capital_profile_score` | 资金综合分 | `0~100` |
| `capital_flow_score` | 资金流强度分 | `0~3` |
| `relative_strength_score` | 相对强度分 | `0~3` |
| `liquidity_score` | 流动性分 | `0~3` |

### 2.2 原始快照字段

| 字段 | 含义 |
| --- | --- |
| `today_amount` | 当日成交额 |
| `avg_amount_20d` | 近 20 日平均成交额 |
| `today_turnover_rate` | 当日换手率 |
| `avg_turnover_rate_20d` | 近 20 日平均换手率 |
| `volume_ratio` | 当日成交额 / 20 日均额 |
| `turnover_ratio` | 当日换手率 / 20 日均换手率 |
| `today_change_pct` | 当日涨跌幅 |
| `return_5d` | 近 5 日涨跌幅 |
| `return_20d` | 近 20 日涨跌幅 |
| `main_net_inflow` | 主力当日净流入 |
| `inflow_5d` | 近 5 日净流入 |
| `inflow_10d` | 近 10 日净流入 |
| `main_net_inflow_pct_mv` | 当日净流入 / 总市值 |
| `inflow_5d_pct_mv` | 5 日净流入 / 总市值 |
| `inflow_10d_pct_mv` | 10 日净流入 / 总市值 |
| `capital_flow_status` | 资金流接口状态 |
| `capital_profile_summary` | 面向复盘的简短摘要 |

### 2.3 结构化拆分字段

| 字段 | 含义 |
| --- | --- |
| `capital_profile_factor_breakdown.capital_flow` | 资金流子项分与原因 |
| `capital_profile_factor_breakdown.relative_strength` | 相对强度子项分与原因 |
| `capital_profile_factor_breakdown.liquidity` | 流动性子项分与原因 |

## 3. 当前评分逻辑

## 3.1 `capital_flow_score`

主要看三件事：

- 当日主力净流入够不够明显
- 近 5 日是否持续净流入
- 近 10 日累计流入是否继续增强

当前实现中的主要放分口径：

- 当日净流入 `>= 5000 万`，或 `main_net_inflow_pct_mv >= 0.08%`，记 1 分
- 近 5 日净流入 `>= 1.5 亿`，或 `inflow_5d_pct_mv >= 0.25%`，记 1 分
- 近 10 日净流入 `>= 2.5 亿`，或 `inflow_10d_pct_mv >= 0.40%`，记 1 分
- 如果当日和近 5 日都为负流入，会做一次减分保护

最终范围：`0~3`

## 3.2 `relative_strength_score`

主要看价格表现是不是已经体现出强度：

- 当日涨幅 `>= 3%`，记 1 分
- 近 5 日涨幅 `>= 8%`，记 1 分
- 近 20 日涨幅 `>= 15%`，记 1 分
- 如果当日和近 5 日都为负，会做一次减分保护

最终范围：`0~3`

## 3.3 `liquidity_score`

主要看资金能不能进得去、出得来，以及是否有足够承接：

- 近 20 日平均成交额 `>= 15 亿`，记 2 分
- 近 20 日平均成交额 `>= 3 亿`，记 1 分
- 当日成交额高于近 20 日均额 `20%+`，记 1 分
- 近 20 日平均换手率 `>= 1.5`，记 1 分
- 若当日或近 20 日成交额过低，会限制高分上限

最终范围：`0~3`

## 3.4 `capital_profile_score`

当前综合分采用加权方式：

- `capital_flow_score * 0.40`
- `relative_strength_score * 0.35`
- `liquidity_score * 0.25`

再把加权结果折算为 `0~100`：

- `capital_profile_score = weighted / 3 * 100`

这代表当前我们更偏向：

- 先看有没有实际资金流
- 再看价格是否体现为强势
- 最后看流动性是否足够支撑

## 3.5 `capital_consensus_score`

这是快照层最适合直接查看的“简化资金结论”。

当前映射规则：

- `capital_profile_score >= 75` -> `3`
- `capital_profile_score >= 55` -> `2`
- `capital_profile_score >= 30` -> `1`
- 其余 -> `0`

另外还有一个加强规则：

- 如果 `capital_flow_score >= 2` 且 `relative_strength_score >= 2`，至少给到 `2`
- 如果再叠加 `liquidity_score >= 1`，至少给到 `3`

这意味着：

- `3` 更偏“资金、强度、流动性都比较像已经形成承接”
- `2` 更偏“资金和价格已经有一定共识”
- `1` 更偏“有一点参与迹象，但还不够稳”
- `0` 更偏“暂时看不到明确资金层确认”

## 4. 快照里怎么读

如果你在 `/signals` 或数据库快照里看一只票，建议按这个顺序读：

1. 先看 `capital_consensus_score`
2. 再看 `capital_profile_score`
3. 再拆看 `capital_flow_score / relative_strength_score / liquidity_score`
4. 最后看原始字段，比如 `main_net_inflow / inflow_5d / volume_ratio`

简单理解：

- `capital_consensus_score` 看结论
- `capital_profile_score` 看强弱梯度
- 三个子分看原因
- 原始字段看证据

## 5. 当前接入到哪些策略

## 5.1 `earnings_surprise`

当前接入方式：

- 不是先用资金层筛票
- 而是在业绩线先过主判断后，再补资金层

这样做的原因是：

- 业绩线主判断仍然应该由业绩兑现逻辑主导
- 资金层更适合做排序增强和复盘增强

当前在 `earnings_surprise` 中的作用：

- 给通过候选补 `capital_consensus_score`
- 给通过候选补 `capital_profile_score`
- 给通过候选补原始资金流与强度字段
- 排序时在 `earnings_strategy_score` 之后作为同分优先比较项

当前排序优先级大致是：

1. `earnings_strategy_score`
2. `capital_consensus_score`
3. `relative_strength_score`
4. `capital_profile_score`
5. 其他业绩质量与增速字段

这意味着：

- 业绩分是主轴
- 资金层是增强项
- 同样是业绩线通过票，会优先把“业绩 + 资金共振”排在前面

## 5.2 `monthly_slow_rise`

当前接入方式：

- 月线结构先选出“慢趋势成立”的候选
- 再补统一资金层

这样做的原因是：

- 月线慢牛先看结构顺不顺
- 资金层主要用来区分“结构好看但承接弱”和“结构好看且在被市场持续认可”

当前在 `monthly_slow_rise` 中的作用：

- 给命中候选补 `capital_consensus_score`
- 补 `capital_profile_summary`
- 让导出和快照里不止有月线结构，也能看到资金承接
- 排序时在月线结构指标之后，把资金强弱放进优先比较项

当前排序更偏：

1. 月线结构是否顺滑
2. 低点抬高与阳线占比
3. 资金共识和相对强度
4. 回撤和市值

## 6. 和龙头线的关系

这套 `capital_profile` 不是要替代龙头线。

两者区别：

- `dragon_head_candidate` 更强调“辨识度、板块地位、核心承载”
- `capital_profile` 更强调“有没有资金承接、强度是否体现、流动性是否足够”

可以把它理解成：

- 龙头线更像“主线资金中心”
- 资金画像更像“通用资金层体检”

所以后面任何主线，只要需要一套统一的资金层，都可以先接 `capital_profile`。

## 7. 当前限制

目前这套口径仍然是第一版，主要限制有：

- 还没有显式区分大票、小票、微盘票的不同资金阈值
- 还没有接入板块联动、板块强度、同题材同步性
- 还没有区分“机构风格承接”和“游资风格承接”
- 对资金流接口缺数据时采用 fail-open，分数会偏保守

所以它现在更适合：

- 做统一比较
- 做排序增强
- 做快照复盘

还不适合直接当成唯一买点判断。

## 8. 后续可继续细化的方向

如果后面要继续往“资金层专章”深化，建议优先补：

1. 按策略 profile 拆权重
2. 按市值段拆阈值
3. 加入板块联动与题材同步性
4. 加入连续性字段，比如近 3 日/5 日资金方向稳定度
5. 给 `/signals` 做更明确的资金层摘要卡片

## 9. 相关文档

- `docs/local_strategies/supporting/main_strategy_blueprint.md`
- `docs/LOCAL_STRATEGY_CATALOG.md`
- `docs/local_strategies/supporting/earnings_strategy_breakdown.md`
- `docs/local_strategies/core/monthly_slow_rise.md`
