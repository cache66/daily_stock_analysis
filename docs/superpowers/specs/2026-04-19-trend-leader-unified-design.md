# A 股强趋势龙头统一策略设计

日期：2026-04-19  
状态：已完成设计评审，待实现规划  
适用范围：A 股，统一候选池，总榜输出  
设计语言：中文

## 1. 背景

当前仓库已经具备多条较成熟的本地策略主线，包括：

- `hundred_day_high`：偏日线强趋势、新高突破
- `monthly_slow_rise`：偏中期月线结构漂亮、慢牛走势
- `earnings_surprise`：偏业绩驱动与兑现验证
- `dragon_head_candidate`：偏主线龙头、辨识度、资金与板块核心地位
- `commodity_price_pass_through`：偏涨价与产业链传导逻辑
- `capital_profile`：偏资金承接、相对强度与流动性确认

这些能力已经形成“扫描脚本 + 快照落库 + `/signals` 查询/展示”的基础框架，但仍缺少一个真正贴合“强趋势龙头”交易风格的统一入口。

本设计的目标不是新增一堆平行策略，而是在现有能力基础上新增一个统一编排层，回答同一个问题：

`这只股票是否同时具备龙头资格、强趋势结构、资金承接，以及足够可信的逻辑强化因素，并能进入一个每天可复盘的统一总榜？`

## 2. 目标

第一版要实现一个新的 A 股统一主策略，用于每天产出“强趋势龙头总榜”。

该总榜具备以下特征：

- 明确偏向主线龙头、高辨识度核心股
- 先看龙头资格与趋势结构，再看资金确认
- 业绩与涨价逻辑不作为唯一入口，但要参与硬筛与加分
- 总榜只输出一个统一候选池，不拆成多个独立榜单
- 每只股票必须带有 `breakout / pullback / hybrid` 相关评分与主标签
- 结果能够进入 `/signals` 做历史回看与复盘

## 3. 非目标

第一版明确不做以下事项：

- 不覆盖港股、美股
- 不做日内级别买卖点策略
- 不做自动交易建议价位、止损价位、目标价位
- 不替换现有 `bull_trend` 作为 Agent 默认主策略
- 不重写既有 `hundred_day_high`、`earnings_surprise`、`dragon_head_candidate`
- 不做高度复杂的板块联动时序建模

## 4. 用户偏好约束

本设计基于以下已确认偏好：

- 第一优先级：`强趋势龙头`
- 策略 profile：同时支持三种风格
  - `breakout`：突破追强
  - `pullback`：强中低吸
  - `hybrid`：兼容型综合视角
- 市场范围：`只做 A 股`
- 非纯技术因素处理方式：`龙头/趋势先做硬筛，业绩/涨价/资金做硬筛与加分混合`
- 最终输出形态：`统一候选池`

## 5. 总体方案

推荐采用“统一策略编排方案”，即：

- 复用已有龙头、趋势、资金、业绩、涨价能力
- 新增一个统一策略编排服务
- 对 A 股全市场做一次统一扫描
- 在统一上下文内完成硬筛、评分、标签与总榜排序
- 以新的 `signal_type` 写入快照，并接入 `/signals`

该方案避免了两个问题：

- 避免仅拼接现有榜单，导致时间粒度和口径不一致
- 避免推翻现有体系重做，造成平行实现和长期维护负担

## 6. 分层决策框架

统一策略沿用仓库当前总纲“逻辑 + 资金 + 趋势 + 兑现”的大方向，但在第一版中强调优先级：

1. 龙头资格
2. 趋势结构
3. 资金承接
4. 逻辑强化
5. 风险扣分

对“强趋势龙头”而言，第一版排序原则是：

- 先排除没有龙头资格、没有趋势结构的票
- 在剩余候选里优先“龙头更强 + 趋势更强 + 资金更强”
- 业绩与涨价逻辑用于提升优先级和过滤明显不合格样本

## 7. 硬筛规则

### 7.1 市场与股票基本过滤

统一策略仅覆盖 A 股普通股票，默认执行以下过滤：

- 排除 B 股
- 排除北交所
- 排除 ETF、LOF、指数、明显非股票标的
- 默认排除 `ST`
- 默认排除极端低流动性样本

该层优先复用现有 `KlineSelectorService` 的 universe 与基础过滤逻辑。

### 7.2 龙头资格硬筛

不是所有强票都允许进入统一总榜，必须先具备“龙头候选资格”。

第一版直接复用 `DragonHeadAnalysisService` 的结构化结果，至少满足：

- `leader_type != pseudo_leader`
- `leader_probability >= medium`
- `recognizability_score >= 2`
- `sector_leadership_score >= 1`

目的不是绝对定义“唯一龙头”，而是保证候选股至少属于主线核心、板块核心或高辨识度承载标的。

### 7.3 趋势资格硬筛

候选股必须满足两类趋势条件之一：

- `breakout 候选`
  - 日线处于明显上升结构
  - 接近新高或刚完成新高突破
  - 近 5 日、20 日价格强度较强
- `pullback 候选`
  - 大趋势仍在
  - 当前为强趋势中的回踩或整理，而非破位
  - 回踩后仍站在中期趋势骨架上方

这一层定义“是否值得进入研究池”，不等价于最终买点建议。

### 7.4 负面基本面与逻辑风险硬筛

第一版不要求所有候选都具备非常漂亮的业绩，但应明确排除明显不合格样本：

- `earnings_strategy_gate_status` 命中强负面阻断
- `earnings_quality` 出现严重质量风险，且伴随现金流或下行周期硬风险
- 涨价传导逻辑被明确识别为弱传导、反例或不具备主线支撑

设计意图是：

- 龙头与趋势决定“能否进门”
- 业绩、涨价、资金决定“是否值得留下并排到前面”

## 8. 三个 Profile 的评分逻辑

### 8.1 总体原则

每只股票计算 3 个分数：

- `breakout_score`
- `pullback_score`
- `hybrid_score`

同时生成两个输出：

- `primary_profile`：当前最匹配的交易形态
- `overall_score`：统一总榜排序分

统一总榜按 `overall_score` 排序，但展示 `primary_profile`，避免把“突破追强”和“强中低吸”混成同一类机会。

### 8.2 breakout_score

`breakout_score` 适合追主升、追新高、追板块核心强化，重点评价：

- 龙头强度
  - `leader_probability`
  - `recognizability_score`
  - `sector_leadership_score`
- 日线趋势强度
  - 接近新高
  - 趋势延续性
  - 近 5 日、20 日相对强度
- 资金确认
  - `capital_consensus_score`
  - `capital_flow_score`
  - `relative_strength_score`

该 profile 允许位置偏高，但不接受纯情绪冲顶且无承接的加速样本。

### 8.3 pullback_score

`pullback_score` 适合龙头中的低吸和回踩确认，重点评价：

- 龙头资格是否成立
- 中期趋势骨架是否完整
- 当前是否属于强趋势中的合理回踩，而非破位反弹
- 短期位置是否相对舒适，不过热
- 资金虽不必最强，但不能明显走坏

该 profile 的关键不是寻找弱票，而是在强票中寻找更优位置。

### 8.4 hybrid_score

`hybrid_score` 不是独立形态，而是总榜的综合质量分。其用途包括：

- 融合 `breakout_score` 与 `pullback_score`
- 兼容强突破型与强趋势回踩型
- 综合反映龙头、趋势、资金、逻辑与风险的整体质量

设计上：

- `primary_profile = argmax(breakout_score, pullback_score)`
- `hybrid_score` 反映全局质量
- `overall_score` 与 `hybrid_score` 在第一版等价

## 9. 总分结构

第一版 `overall_score` 建议拆为 5 块：

- `leader_gate_score`
- `trend_score`
- `capital_score`
- `logic_bonus_score`
- `risk_penalty_score`

计算思想：

`overall_score = 龙头基础 + 趋势主分 + 资金确认 + 逻辑加分 - 风险扣分`

推荐权重方向：

- 龙头基础：最高
- 趋势主分：次高
- 资金确认：次高
- 逻辑加分：中等
- 风险扣分：强约束

第一版的总榜应首先像“强趋势龙头榜”，而不是“价值股综合榜”或“泛基本面评分榜”。

## 10. 非纯技术因素的处理方式

### 10.1 资金

资金同时承担硬筛与核心加分两种职责：

- 硬筛：排除明显无承接、流动性差、资金衰减严重的样本
- 加分：强化真正有承接、有强度、有主线抱团倾向的龙头股

第一版中，资金权重仅次于龙头与趋势本身。

### 10.2 业绩

业绩在第一版中承担“过滤明显不合格 + 正向强化”的职责：

- 负面时可拦截
- 正面时明显加分
- 对 `breakout` 更像确认项
- 对 `pullback` 更像稳定性和持续性增强项

### 10.3 涨价逻辑

涨价逻辑不是所有股票都有，但一旦命中，应该作为主线逻辑强化器：

- 不能独立替代龙头与趋势
- 可以作为行业景气、产业链位置、涨价传导、兑现路径的增强证据

## 11. 复用字段与新增字段

### 11.1 优先复用字段

优先复用现有结构化字段，避免重复造字段：

- 龙头侧
  - `leader_probability`
  - `leader_type`
  - `recognizability_score`
  - `sector_leadership_score`
  - `relative_strength_score`
  - `liquidity_score`
  - `catalyst_score`
- 资金侧
  - `capital_consensus_score`
  - `capital_profile_score`
  - `capital_flow_score`
  - `main_net_inflow`
  - `inflow_5d`
  - `inflow_10d`
- 业绩侧
  - `earnings_strategy_score`
  - `earnings_strategy_gate_status`
  - `earnings_quality_signal`
  - `earnings_quality_score`
  - `earnings_quality_verdict`
- 月线/结构侧
  - `monthly_positive_ratio`
  - `monthly_higher_low_ratio`
  - `monthly_total_return_pct`
  - `monthly_ma_short`
  - `monthly_ma_long`
  - `monthly_worst_drawdown_pct`

### 11.2 建议新增字段

统一策略应新增少量专属字段：

- `primary_profile`
- `breakout_score`
- `pullback_score`
- `hybrid_score`
- `overall_score`
- `trend_label`
- `risk_flags`
- `strategy_summary`

新增字段控制在可解释、可展示、可排序的最小集合内。

## 12. 落库与信号设计

### 12.1 第一版落库方式

不替换既有专题信号，新增统一策略信号类型：

- `signal_type = trend_leader_unified`

每天写入一份统一总榜快照。

### 12.2 为什么使用新 signal_type

新增独立信号类型的好处：

- 不破坏已有 `hundred_day_high`、`earnings_surprise`、`dragon_head_candidate`
- 可以与现有专题并行复盘
- 后续可以继续新增交叉视图，而不影响第一版稳定性

如后续确有需要，可在后续版本再扩展：

- `trend_leader_unified__breakout`
- `trend_leader_unified__pullback`

但第一版不强制拆分子榜单。

## 13. `/signals` 接入方式

第一版继续复用现有 `/signals` 体系，不新增独立页面：

- 后端查询层复用 `SignalSnapshotService`
- API schema 在现有 signals 结构上补统一策略字段
- Web 的 `SignalsPage` 增加新的 `signal_type` 选项

页面展示重点建议包括：

- `overall_score`
- `primary_profile`
- 龙头强度摘要
- 资金确认摘要
- 趋势标签
- 业绩/涨价逻辑增强摘要

用户体验目标：

- 原有专题视图继续存在
- 同时新增一个更贴近“强趋势龙头”交易风格的统一总榜入口

## 14. 实现边界

第一版只做最小闭环：

- 新增统一策略扫描入口脚本
- 新增统一策略编排服务
- 新增统一策略快照 `signal_type`
- 新增 `/signals` 接入
- 不替换 Agent 主默认策略

也就是说，第一版把它定位为“扫描与复盘能力”，而不是“全系统默认分析核心”。

## 15. 推荐文件落点

为贴合现有仓库结构，推荐如下文件布局：

- 新脚本
  - `scripts/select_trend_leader_candidates.py`
- 新服务
  - `src/services/trend_leader_strategy_service.py`
- 可选评分模块
  - `src/services/trend_leader_scoring.py`
- 可选快照采集脚本
  - `scripts/collect_trend_leader_snapshots.py`
- 专题文档
  - `docs/TREND_LEADER_UNIFIED_STRATEGY.md`

如果第一版先以单脚本为主，也建议尽早引入服务层，以便后续扩展和测试。

## 16. 验证方案

第一版至少覆盖以下验证层次：

### 16.1 单元测试

- `primary_profile` 判定是否正确
- 硬筛逻辑是否按预期拦截
- `overall_score` 构成是否稳定
- breakout/pullback 评分在典型样本上的方向性是否符合设计

### 16.2 快照流测试

- 新 `signal_type` 是否能正常入库
- 历史查询、日期查询、分页查询是否正常
- 新增字段是否能被正确读取

### 16.3 API 合同测试

- `/signals` 现有结构不被破坏
- 新字段为向后兼容扩展字段

### 16.4 Web 展示测试

- 新 signal type 可显示
- 总榜、profile、关键字段展示正常
- 旧信号类型无回归

### 16.5 本地 smoke

- 以少量股票或 limit 模式跑通一次全流程

建议测试文件包括：

- `tests/test_trend_leader_strategy_service.py`
- `tests/test_trend_leader_signal_flow.py`
- `tests/test_signal_snapshot_api.py`
- `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`

## 17. 成功标准

第一版成功标准建议定义为：

- 能稳定输出 A 股统一总榜
- 每只票都有 `primary_profile`
- 总榜能区分“突破型强龙头”和“强势回踩型龙头”
- `/signals` 可以按日期回看
- 总榜结果具备可解释性，而非纯黑盒

## 18. 风险与回滚

### 18.1 主要风险

- “龙头”和“强票”容易混淆
- `pullback` 容易混入弱反弹票
- 业绩与涨价逻辑覆盖不均，不宜过度依赖
- 权重过于复杂会导致后续难以调参与维护

### 18.2 风险控制原则

- 第一版权重保持简洁
- 规则优先明确、可解释，而不是过度复杂
- 不强行替代原有专题能力

### 18.3 回滚方式

由于第一版全部挂在新的 `signal_type` 下，回滚非常简单：

- 停止生成 `trend_leader_unified`
- 在 `/signals` 中隐藏该信号类型
- 保留既有 `hundred_day_high`、`earnings_surprise`、`dragon_head_candidate` 不受影响

## 19. 后续实现规划建议

在本设计基础上，后续实现规划建议按照以下顺序展开：

1. 明确输入复用策略与数据来源映射
2. 设计硬筛函数与评分函数边界
3. 设计快照 payload 结构
4. 接入 `SignalSnapshotService`、API schema 与 Web
5. 补齐测试与 smoke 验证

## 20. 一句话总结

第一版“强趋势龙头统一策略”的核心，不是做一个新的大而全选股系统，而是把现有龙头、趋势、资金、业绩、涨价能力收敛成一个统一的 A 股总榜入口，让系统每天都能回答：

`这只股票是不是主线核心龙头，趋势是否足够强，资金是否认可，逻辑是否有强化，并且当前更适合突破追强还是强中低吸。`
