# 外部能力集成决策：面向本地 4 条主策略的落地方案
最后更新：2026-04-27

## 1. 这份文档解决什么问题

前面的工作已经完成了两层沉淀：

1. 总地图
   - `docs/architecture/external-capability-map-for-local-strategies.md`
2. 单仓库深挖
   - `RD-Agent / Qlib / myhhub/stock / FinGenius / TradingAgents-CN / OpenBB / WonderTrader / RQAlpha / vn.py / QUANTAXIS / ai-hedge-fund`

这份文档不再回答“谁强”，而是直接回答：

1. 我们到底要接什么。
2. 什么东西现在不该接。
3. 哪些外部能力最适合服务当前 4 条主策略。
4. 接入顺序怎么排，避免重新发散。

当前主策略仍固定为：

- `trend_leader_unified`
- `earnings_surprise`
- `hundred_day_high`
- `monthly_slow_rise`

## 2. 决策原则

### 2.1 不整体替换，只做按层借力

当前结论已经足够明确：

- 没有任何一个外部仓库值得整体替换我们当前系统
- 真正有价值的是分层借力

因此后续默认策略不是：

- “引入某某平台”

而是：

- “引入某个平台在某一层最强的那部分能力”

### 2.2 先补框架短板，不先追新策略名

当前更值得做的是：

- 补扫描层
- 补数据层
- 补评估层
- 补因子/解释层

而不是：

- 再增加新的策略名称
- 再扩更多专题池

### 2.3 主策略判断权保留在本地

即使未来接外部能力，也要保持：

- 主策略字段口径由我们定义
- 候选入选规则由我们定义
- 快照、评估、导出结构由我们定义

外部系统只能提供：

- 数据
- 因子
- 形态实现
- 评估口径
- 解释组织

不能反过来替代主策略语义。

### 2.4 默认选“最小可运行实验”，不做大迁移

这点很关键。

后续执行默认不是：

- 一次性接一个大平台

而是：

- 针对一个具体问题做最小实验
- 跑通后再决定是否扩大

## 3. 总体集成结论

## 3.1 立即值得接入的能力

这是最值得进入实操阶段的部分。

### A. 形态与筹码增强

来源：

- `myhhub/stock`

适合接入：

- `trend_leader_unified`
- `hundred_day_high`

优先接的能力：

- 平台突破质量
- 高而窄旗形 / 压缩结构
- 低 ATR 稳定趋势
- `CYQ` 筹码摘要指标

原因：

- 这是外部项目里最贴近 A 股主选股逻辑的实现
- 接入点清晰
- 对当前结果质量提升最直接

### B. 统一扫描层与高性能扫描壳

来源：

- `WonderTrader`

适合接入：

- `trend_leader_unified`
- `hundred_day_high`
- `monthly_slow_rise`

优先接的能力：

- 扫描层与策略层解耦
- 通用 universe 准备
- 通用 history 准备
- 扫描前轻前筛
- 分片扫描组织

原因：

- 我们当前已经反复暴露出扫描性能与组织问题
- 这是现阶段最影响主策略稳定运行的一层

### C. 标准化评估层

来源：

- `RQAlpha`

适合接入：

- 全部 4 条主策略

优先接的能力：

- 成本口径
- 风险口径
- 分析输出口径
- 组合级验证思路

原因：

- 当前 `signal_snapshot` 还偏轻口径
- 后面如果不补评估层，主策略越做越多，比较就会越来越失真

### D. 统一因子研究与排序层

来源：

- `Qlib`
- `RD-Agent`

适合接入：

- `earnings_surprise`
- `monthly_slow_rise`

优先接的能力：

- 多季度连续性因子整理
- 质量因子层
- 统一排序器实验
- 自动实验闭环

原因：

- 这两条策略后面最需要的是“更稳的排序器”，而不是继续手写更多局部门槛

## 3.2 可以进入二阶段实验，但不建议立即接入的能力

### A. 统一数据接入层

来源：

- `OpenBB`

适合接入：

- `earnings_surprise`
- `monthly_slow_rise`

当前建议：

- 先做单点上游实验
- 不直接替换现有数据层

适合先试的场景：

- 财务字段补全
- 事件目录
- 新闻目录

不建议现在就做的事情：

- 整体迁移到 OpenBB provider
- 让 OpenBB 直接决定策略字段

### B. A 股本地底座重构

来源：

- `QUANTAXIS`
- `vn.py`

当前建议：

- 先借设计思路
- 不做整框架接入

适合先吸收的能力：

- 日历与市场语义统一
- 任务化组织
- 模块边界收口
- 插件化应用边界

### C. 短线解释层

来源：

- `FinGenius`

当前建议：

- 先做解释字段与说明壳
- 不让它进入主筛选硬门槛

适合先接的能力：

- 游资说明
- 大单异动说明
- 短线博弈说明
- Research/Battle 两阶段说明结构

## 3.3 只适合参考，不建议作为近期落地重点的能力

### A. 图式多 agent 工作台

来源：

- `TradingAgents-CN`
- `ai-hedge-fund`

建议：

- 只借解释层和产品壳
- 不作为近期主工程方向

原因：

- 当前主矛盾不在 agent 不够多
- 当前主矛盾在主策略、扫描、评估、数据口径还没完全收拢

### B. 高频/交易网关/复杂执行栈

来源：

- `WonderTrader`
- `vn.py`

建议：

- 暂不进入当前路线

原因：

- 这不是当前 4 条主策略的主要瓶颈

## 4. 映射到 4 条主策略

## 4.1 `trend_leader_unified`

最优先应该接：

1. `myhhub/stock`
   - 平台突破
   - 高旗形/压缩结构
   - 低 ATR 稳定趋势
   - 筹码集中摘要
2. `WonderTrader`
   - 统一扫描壳
   - 分片与前筛
3. `FinGenius`
   - 解释层补充
4. `RQAlpha`
   - 后续组合评估

明确不建议：

- 让 `TradingAgents-CN` 或 `ai-hedge-fund` 直接做主筛选

## 4.2 `earnings_surprise`

最优先应该接：

1. `Qlib`
   - 财务质量层
   - 多季度连续性层
   - 统一排序器
2. `RD-Agent`
   - 自动实验
   - 因子组合搜索
3. `OpenBB`
   - 财务字段与事件目录补充
4. `RQAlpha`
   - 持有期与组合评估

可作为解释层补充：

- `FinGenius`
- `TradingAgents-CN`

## 4.3 `hundred_day_high`

最优先应该接：

1. `myhhub/stock`
   - 平台突破
   - 海龟 / 高旗形
   - 压缩后突破
2. `WonderTrader`
   - 高性能扫描壳
3. `RQAlpha`
   - 突破型信号的组合评估

明确不建议：

- 继续只围绕“新高”本身扩规则，不补形态质量层

## 4.4 `monthly_slow_rise`

最优先应该接：

1. `Qlib`
   - 质量与连续性排序层
2. `RD-Agent`
   - 自动实验与参数迭代
3. `WonderTrader`
   - 低频全市场扫描组织
4. `RQAlpha`
   - 中期持有验证
5. `OpenBB`
   - 财务与宏观上游数据补充

明确不建议：

- 继续只在月线形态上加门槛，不补质量排序层

## 5. 执行顺序

## 5.1 第一阶段：马上值得开做

按优先级建议固定为：

1. `WonderTrader` 方向
   - 目标：统一扫描层原型
2. `RQAlpha` 方向
   - 目标：标准化评估层原型
3. `myhhub/stock` 方向
   - 目标：形态与筹码增强原型
4. `Qlib` 方向
   - 目标：`earnings_surprise / monthly_slow_rise` 的统一排序原型

原因：

- 这 4 个方向覆盖了我们当前最关键的短板：
  - 扫描
  - 评估
  - 形态
  - 排序

## 5.2 第二阶段：条件成熟后再接

1. `OpenBB`
   - 做单点数据实验
2. `FinGenius`
   - 做解释层增强
3. `vn.py / QUANTAXIS`
   - 做结构与基础设施层吸收

## 5.3 第三阶段：只在前两阶段稳定后再考虑

1. `TradingAgents-CN`
2. `ai-hedge-fund`

这两个方向默认只做：

- 展示层
- 工作台
- 解释壳

## 6. 每个方向推荐的最小实验

### 6.1 `WonderTrader`

- 做一个统一扫描壳原型
- 不改策略逻辑
- 只统一：
  - universe 准备
  - history 准备
  - 前筛
  - 分片

### 6.2 `RQAlpha`

- 给 `signal_snapshot` 加一层成本/风险/分析口径原型
- 优先用 `monthly_slow_rise` 或 `earnings_surprise` 做对照

### 6.3 `myhhub/stock`

- 先把平台突破质量和 `CYQ` 摘要接进：
  - `trend_leader_unified`
  - `hundred_day_high`

### 6.4 `Qlib`

- 先做 `earnings_surprise` 的统一排序器原型
- 因子只选一小批：
  - 多季度 surprise history
  - 利润/营收连续性
  - 质量因子

### 6.5 `OpenBB`

- 只做一个单点上游实验：
  - 财务字段
  - 或事件目录

## 7. 明确不做什么

为了避免后续重新发散，当前阶段明确不做下面这些事情：

1. 不再继续横向扩新的外部仓库名单。
2. 不做整平台迁移。
3. 不把 agent 结论当主筛选真源。
4. 不先做复杂交易执行层。
5. 不让展示层优先于主策略质量层。

## 8. 当前最终判断

把这轮外部研究压缩成一句话，就是：

- 我们下一步不该继续“研究谁更强”，而该开始把 `WonderTrader / RQAlpha / myhhub/stock / Qlib` 这 4 条最有用的能力线，逐步接进当前 4 条主策略。

再压缩一点，就是：

1. `WonderTrader`
   - 先补扫描层
2. `RQAlpha`
   - 再补评估层
3. `myhhub/stock`
   - 再补形态与筹码
4. `Qlib`
   - 再补统一排序层

这 4 步走完之后，再考虑 `OpenBB / FinGenius / vn.py / QUANTAXIS / TradingAgents-CN / ai-hedge-fund` 的外围能力，节奏会更稳。
