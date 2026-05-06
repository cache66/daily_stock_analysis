# FinGenius 源码深挖记录
最后更新：2026-04-27

## 1. 仓库定位

`FinGenius` 是一个非常明确面向 A 股用户的 AI 多智能体金融分析产品。它不是量化研究底座，也不是以回测绩效为中心的策略仓库，而是把：

- A 股短线语境
- 多专家协作
- 研究报告
- 辩论投票

做成一套产品化分析流程。

对我们最重要的判断是：

- 它最强的不是“可验证 alpha 生产能力”
- 它最强的是 A 股短线解释层、博弈语言和多角色分析组织方式
- 它适合作为 `trend_leader_unified` 等候选结果的解释增强层，不适合作为主信号引擎

参考入口：

- GitHub 仓库：<https://github.com/HuaYaoAI/FinGenius>
- 本轮查看源码基于本地临时克隆：`2133a03`

## 2. 核心结构与入口

本轮优先看的真实入口包括：

- 项目总览：`README.md`
- 主入口：`main.py`
- 环境基类：`src/environment/base.py`
- 研究环境：`src/environment/research.py`
- 辩论环境：`src/environment/battle.py`
- 热钱 agent：`src/agent/hot_money.py`
- 筹码 agent：`src/agent/chip_analysis.py`
- 筹码工具：`src/tool/chip_analysis.py`
- 大单异动工具：`src/tool/big_deal_analysis.py`

可以先把它理解成 4 层：

1. 产品编排层
   - `main.py` 先跑研究，再跑辩论，再生成 HTML 报告
2. 环境层
   - `ResearchEnvironment`
   - `BattleEnvironment`
3. 专家 agent 层
   - 舆情、风控、游资、技术、筹码、大单异动
4. 工具与数据层
   - `akshare` 数据接口
   - MCP / 搜索 / HTML 报告 / 缓存

## 3. 真正的优点

### 3.1 它的短线语境是明确站在 A 股用户侧的

这一点和很多“海外逻辑迁过来”的 agent 项目很不一样。

从 README、agent 命名和工具命名看，它强调的是：

- 游资
- 龙虎榜
- 筹码
- 大单异动
- 风险公告
- 舆情博弈

这类语境对我们有价值，因为我们现在 4 条主策略里，最欠缺的不是再多一个扫描器，而是对候选结果提供更贴近 A 股短线语言的解释层。

### 3.2 它把“研究”和“辩论”拆成了两个显式阶段

这是 `FinGenius` 最值得借的产品结构点。

从 `main.py` 和 `research.py / battle.py` 可以看到，它不是让多个 agent 同时乱跑，而是：

1. 研究阶段
   - 6 个专家顺序分析
   - 每个专家拿到同一份股票基本信息上下文
   - 产出分门别类的研究结果
2. 辩论阶段
   - 把研究结果喂给 `BattleEnvironment`
   - 专家发言、投票、汇总结论
3. 报告阶段
   - 生成 HTML 报告和辩论记录

这套结构对我们特别重要，因为它说明：

- agent 不一定要参与第一阶段筛股
- 更适合参与第二阶段解释与分歧整合

这和我们当前“先规则/因子筛候选，再做解释增强”的方向是一致的。

### 3.3 它不是空泛 agent 壳，而是有具体 A 股工具主题

从 `research.py` 和工具层可以看到，当前开源的 6 个 agent 已经有明确分工：

- `sentiment_agent`
- `risk_control_agent`
- `hot_money_agent`
- `technical_analysis_agent`
- `chip_analysis_agent`
- `big_deal_analysis_agent`

这说明它不是只做一个“万能分析师”，而是把 A 股用户常关心的维度拆开。

对我们最有价值的不是这些 agent 最终怎么投票，而是这些维度本身就可以映射成我们自己的解释字段：

- 热钱/游资视角
- 筹码视角
- 大单异动视角
- 风险公告视角

### 3.4 它的筹码与大单工具是比较务实的

`src/tool/chip_analysis.py` 和 `src/tool/big_deal_analysis.py` 显示，这个项目没有完全依赖纯文本推理，而是会直接抓结构化数据：

- `ChipAnalysisTool`
  - 优先调 `ak.stock_cyq_em`
  - 失败时尝试用历史行情估算筹码分布
  - 再失败时也给默认兜底，避免整条链路彻底报废
- `BigDealAnalysisTool`
  - 调 `stock_fund_flow_big_deal`
  - 调 `stock_fund_flow_individual`
  - 调 `stock_individual_fund_flow`
  - 调 `stock_zh_a_hist`
  - 会清洗、聚合、排序市场和个股的大单流入流出

这说明它的价值不只是 prompt，而是“结构化证据 + agent 解释”的结合。

### 3.5 它在产品层已经形成“研究报告 -> 辩论记录 -> HTML 输出”的闭环

`main.py` 里能看到：

- 研究摘要
- 辩论过程
- 投票统计
- HTML 报告生成

这对我们不是主信号能力，但对后面的用户侧展示很有价值。因为我们现在已经有 `/signals` 快照和字段，但解释组织仍偏功能页，不够“分析完成态”。

## 4. 我们能直接用的能力

### 4.1 把它当 `trend_leader_unified` 的解释层增强来源

这是它最明确的适配方向。

可以借的不是“让它替我们选股”，而是把它的维度拆成解释层：

- 游资是否活跃
- 是否有明显大单净流入
- 筹码是否集中
- 舆情与风险是否矛盾

落到我们框架里，可以形成：

- `hot_money_summary`
- `big_deal_summary`
- `chip_commentary`
- `risk_overlay_commentary`

先给已经入池的候选做二阶段解释，而不是全市场主扫描。

### 4.2 给 `trend_leader_unified` 补“游资 / 大单 / 博弈”辅助因子

`BigDealAnalysisTool` 里已经把：

- 全市场买卖盘汇总
- 个股逐笔大单净流入
- 排行窗口

做成可复用结构。

这对我们当前框架最有价值的接法是：

- 不是直接让 agent 说“这是龙头”
- 而是把大单与游资行为变成结构化辅助字段

例如：

- `big_deal_net_inflow`
- `big_deal_trade_count`
- `market_big_deal_context`
- `hot_money_participation_hint`

### 4.3 给 `earnings_surprise` 补事件解释层，而不是补主判断层

`FinGenius` 不适合作为 `earnings_surprise` 的核心筛选引擎，但适合用在：

- 公告出来后，解释市场在看什么
- 风险 agent 补充公告风险点
- 舆情 agent 补充市场情绪反应

也就是说，它可以给 `earnings_surprise` 做：

- 事件摘要
- 风险摘要
- 市场反应语言层解释

但不能替代我们自己的财务门槛、连续性判断和价格反应因子。

### 4.4 借它的 “Research -> Battle” 结构做我们的解释产品化

这是中期最值钱的能力。

对我们未来最合理的迁移方式是：

1. 第一阶段：主策略先出结构化候选
2. 第二阶段：按股票调用解释模块
3. 第三阶段：必要时让多视角解释做冲突整合

这比一开始就让多 agent 介入主扫描要靠谱得多。

## 5. 只适合参考、不建议直接接入的部分

### 5.1 不建议把它当主选股器

它的强项不在：

- 全市场统一扫描
- 统一硬门槛
- 统一排序器评估
- 长期可回测 alpha 归因

所以不能让它替代我们 4 条主策略的主信号层。

### 5.2 它的很多优势在产品表达，不在量化验证

比如：

- HTML 报告
- 辩论展示
- 多专家发言

这些非常适合用户侧，但不能自动等同于策略有效性。

我们不能把“解释很像那么回事”误判为“信号质量更高”。

### 5.3 它的数据工具链仍以在线抓取为主，不适合高频全市场重扫

从 `chip_analysis.py` 和 `big_deal_analysis.py` 可以看到，它明显偏：

- 单票分析
- 在线抓取
- 带 fallback 的交互式使用

这更适合对少量候选做深挖，不适合我们当前每天多策略、全市场、批量历史回填的运行形态。

## 6. 不建议引入的部分

### 6.1 不建议照搬它的 agent 投票机制进主策略闭环

`BattleEnvironment` 的价值在展示和观点整合，不在可验证选股绩效。

我们不应该把“多数 agent 看涨”直接当信号。

### 6.2 不建议直接沿用整套 MCP / 报告 / App 结构

这个项目的很多结构是为它自己的产品壳服务的。我们可以借：

- 任务组织方式
- 解释维度拆分

但没必要迁移整套交互和报告体系。

### 6.3 不建议把筹码和大单分析直接放进全市场主扫描

这些工具对单票解释很有用，但对全市场批量扫描来说：

- 成本高
- 在线依赖重
- 数据稳定性要求高

更适合只在候选池收缩后触发。

## 7. 映射到我们 4 条主策略

### 7.1 `trend_leader_unified`

这是最适合吃它能力的一条。

最值得借：

- 游资分析语境
- 大单异动证据
- 筹码解释
- 研究后再辩论的组织方式

最适合落到：

- 候选解释卡
- 风险提示卡
- 资金博弈补充字段

### 7.2 `earnings_surprise`

只适合作为二阶段解释补充。

可借：

- 公告/风险解释
- 舆情反应描述
- 事件摘要生成

不适合借：

- 主筛选门槛
- 财务质量判断
- surprise ranking

### 7.3 `hundred_day_high`

可借：

- 大单异动解释
- 筹码结构解释
- 短线博弈语言

但仍然不是它的核心受益策略，价值低于 `trend_leader_unified`。

### 7.4 `monthly_slow_rise`

直接价值最小。

最多可借：

- 报告表达
- 风险摘要

但它不是月线慢牛策略的主要外部参考对象。

## 8. 下一步是否值得做最小实验

值得，但应严格控范围。

最合理的两个最小实验是：

1. 给 `trend_leader_unified` 追加轻量解释层原型
   - 只对 TopN 候选调用
   - 先不引入多 agent 辩论
   - 只产出热钱/大单/筹码三段解释摘要
2. 抽象一版“Research -> Summary” 而非 “Research -> Battle -> HTML”
   - 先证明解释信息密度确实比当前字段堆叠更高
   - 再考虑是否需要辩论层

## 9. 当前结论

`FinGenius` 对我们最值钱的不是“多智能体很酷”，而是下面 4 类可迁移能力：

1. A 股短线用户语境下的解释维度拆分
2. 热钱 / 筹码 / 大单 / 风险的专职化组织方式
3. `Research -> Battle` 的两阶段分析结构
4. 结构化数据证据和 agent 解释结合的产品思路

如果只用一句话概括：

- `FinGenius` 最适合成为我们 `trend_leader_unified` 等候选信号的短线解释增强层，而不是主选股引擎或统一研究底座。
