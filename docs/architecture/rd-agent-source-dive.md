# RD-Agent 源码深挖记录
最后更新：2026-04-27

## 1. 仓库定位

`RD-Agent` 不是一个单纯的量化策略仓库，而是一个“通用 R&D 框架 + 场景化研究入口”的多智能体研发系统。它覆盖数据科学、Kaggle、finetune、RL、quant 等多个场景，quant 只是其中一条场景分支。

对我们最重要的判断是：

- 它不是拿来直接替换本地 4 条主策略的
- 它最有价值的部分不是“给出买什么股票”
- 它真正强的地方在“如何组织研究循环、实验编排、反馈迭代和 factor/model 共优化”

这意味着它更像我们未来的“研究自动化层”参考对象，而不是“信号生产层”替代品

参考入口：

- GitHub 仓库：<https://github.com/microsoft/RD-Agent>
- quant 文档：<https://rdagent.readthedocs.io/en/latest/scens/quant_agent_fin.html>

## 2. 核心结构与入口

本轮优先看的真实入口文件如下：

- CLI 入口：`rdagent/app/cli.py`
- quant 命令入口：`rdagent/app/qlib_rd_loop/quant.py`
- quant 配置装配：`rdagent/app/qlib_rd_loop/conf.py`
- 通用循环骨架：`rdagent/components/workflow/rd_loop.py`
- 通用演化抽象：`rdagent/core/evolving_framework.py`
- quant 场景定义：`rdagent/scenarios/qlib/experiment/quant_experiment.py`
- quant 假设生成：`rdagent/scenarios/qlib/proposal/quant_proposal.py`
- quant 执行与反馈：`rdagent/scenarios/qlib/developer/factor_runner.py`、`model_runner.py`、`feedback.py`

可以先把它理解成 4 层：

1. 命令入口层
   - `rdagent fin_quant` 从 CLI 进入 quant loop
2. 通用循环层
   - `RDLoop` 提供 `hypothesis -> experiment -> coding -> running -> feedback -> record` 的统一骨架
3. quant 场景层
   - `QlibQuantScenario`、`QlibQuantHypothesisGen`、factor/model proposal 负责把通用框架落到 quant 任务
4. 执行与评估层
   - factor runner / model runner 把生成的代码和 `Qlib` 模板拼起来执行，再交给 feedback 做总结

## 3. 真正的优点

### 3.1 它不是单点策略脚本，而是“研究循环框架”

这是 `RD-Agent` 和很多开源量化仓库最大的区别。它的目标不是手写一批固定策略，而是把“提出想法、转实验、写代码、执行、评估、记录”组织成可重复迭代的闭环。

这对我们最有价值，因为我们当前最缺的正是这一层：

- 现在我们有 4 条主策略
- 但缺统一研究迭代骨架
- 新增因子、加规则、调门槛、做验证，很多还靠脚本堆叠和人工来回试

`RD-Agent` 给我们的启发是：不要再把“继续加策略名”当升级方向，而要把“研究动作标准化”当升级方向。

### 3.2 它把 factor 和 model 放进同一条 quant 研发链

`quant.py` 里的 `QuantRDLoop` 不是只做 factor，也不是只做 model，而是：

- factor hypothesis2experiment
- model hypothesis2experiment
- factor coder / runner / summarizer
- model coder / runner / summarizer

也就是说，它把 quant 研发拆成两个可切换动作：

- 做新 factor
- 做新 model

然后在一个统一 trace 里共同演化，而不是两个完全分离的世界。

这点比“只会扫因子”更有价值，因为它天然支持：

- 先扩特征，再调模型
- 或先发现模型瓶颈，再反过来扩特征

对我们来说，虽然当前 4 条主策略大多还是规则和评分驱动，但这套思想可以迁移为：

- 先生成候选因子
- 再统一做排序器实验
- 最后用统一评估口径反馈下一轮因子筛选

### 3.3 它不是盲目轮流试，而是有动作选择器

`QlibQuantHypothesisGen` 里最值得注意的是 action selection：

- `bandit`
- `llm`
- `random`

默认是 `bandit`，并通过 `EnvController` + `LinearThompsonTwoArm` 根据上一轮结果决定下一轮更偏向 `factor` 还是 `model`。

这说明它不是简单“factor 一轮、model 一轮”地机械交替，而是试图根据收益反馈做研究资源调度。

这对我们最值得借的不是它当前的 bandit 细节，而是这个思想：

- 把“下一步优先研究什么”显式化
- 把“研究预算往哪里倾斜”显式化
- 让研究顺序本身成为可优化对象

### 3.4 它把 baseline / SOTA / 本轮实验放在同一评估链上

不管是 `factor_runner.py` 还是 `model_runner.py`，都不是孤立执行一轮代码，而是会显式考虑：

- baseline
- 当前最优因子 / 模型
- 本轮新增因子 / 模型

例如 factor runner 会：

- 处理已有 SOTA factor
- 处理本轮 new factors
- 做去重和相似度过滤
- 合并后再喂给 `Qlib` 执行

model runner 也会：

- 看是否已有 SOTA factor 结果
- 决定用 baseline feature 还是 SOTA feature
- 再执行本轮 model

这套组织方式很适合我们后面做：

- 因子增量实验
- 统一排序器迭代
- “新规则是否真的比旧规则更好”的持续验证

### 3.5 它把执行结果重新变成下一轮可用信息

`feedback.py` 的本质不是单纯打分，而是把结果重新整理成：

- observations
- hypothesis_evaluation
- new_hypothesis
- reason
- decision

再回写进 `Trace`。

也就是说，它至少尝试把“实验结果”重新结构化成“下一轮研究输入”。这正是研究闭环的关键，而不只是把回测结果打印出来。

## 4. 我们能直接用的能力

### 4.1 统一研究循环骨架

最值得直接借的是这一层思路，而不是具体 prompt：

- 提出研究假设
- 转成实验任务
- 自动执行
- 自动评估
- 回写反馈
- 进入下一轮

落到我们框架里，最适合先服务：

- `earnings_surprise`
  - 做多季度 surprise / 质量因子 / 价格反应因子的统一实验
- `monthly_slow_rise`
  - 做质量层、稳定性层、流动性层的组合试验
- `trend_leader_unified`
  - 做统一排序器实验，而不是继续横向叠子规则

### 4.2 “动作选择”思想

我们不一定需要照搬它的 bandit 实现，但可以直接借它的研究调度思想：

- 这轮先补行业强度，还是先补质量层
- 这轮先补形态过滤，还是先补排序因子
- 这轮先扩候选覆盖率，还是先收紧误报

这可以在我们后续的研究自动化层里变成：

- 因子优先级调度器
- 参数实验优先级调度器
- 策略补强优先级调度器

### 4.3 baseline / SOTA / 增量实验组织方式

这套方式非常值得借给我们后面的统一评估链路：

- 当前线上版本
- 当前最好版本
- 本轮候选版本

然后不只比较单一收益，还比较：

- 收益
- 回撤
- 稳定性
- 覆盖率
- 信号质量

这比现在“单次脚本跑一轮，人工看结果”更容易沉淀成系统化研究流程。

### 4.4 场景注入式架构

`conf.py + Scenario + Proposal + Runner + Feedback` 这套装配结构是清晰的。

对我们的直接借鉴是：

- 研究骨架和市场/策略场景分开
- 通用流程和策略细节分开
- 未来如果要做 `A 股短线研究层`、`业绩研究层`、`趋势研究层`，可以沿类似分层设计

## 5. 只适合参考、不建议直接接入的部分

### 5.1 Qlib 强绑定

`RD-Agent` 的 quant 分支明显强绑定 `Qlib`：

- `QlibQuantScenario`
- `qlib_rd_loop`
- factor/model template
- `Qlib` 数据与回测配置

这意味着它的 quant 研发默认假设是：

- 因子研究
- 模型研究
- 组合/回测评估

而不是我们当前偏向：

- A 股短线主策略扫描
- 每日快复盘
- 事件驱动 + 结构打分 + 候选解释

所以不能把它当“现成 A 股短线策略底座”直接接。

### 5.2 长周期、组合化口径过强

官方 quant 文档和 `conf.py` 里默认配置明显偏中长期研究：

- `train_start=2008-01-01`
- `train_end=2014-12-31`
- `valid_end=2016-12-31`
- `test_end=2020-08-01`

这说明它默认在做的是标准量化研究 / 回测口径，而不是我们当前更强调的：

- 最近交易日快照
- 短线候选筛选
- 高频人工复盘辅助

所以它更适合借研究框架，不适合直接拿回测窗口和默认设置。

### 5.3 工程依赖偏重

官方 README 明确写了当前主要支持 Linux，并且很多场景依赖：

- Docker
- `Qlib`
- 独立环境
- Web/UI/日志查看链路

这说明它更像一个完整研究系统，而不是轻量模块库。我们不应该一上来就尝试整套接入。

## 6. 不建议引入的部分

### 6.1 不建议把它当成选股主引擎

`RD-Agent` 不适合作为我们当前 4 条主策略的直接上位替代，原因很清楚：

- 它的目标不是“生成今天最该看的 A 股短线候选”
- 它的优势不在日常快扫描
- 它的 quant 默认口径也不是为本地短线复盘设计的

### 6.2 不建议直接搬它的 prompt 和反馈文案

它的 prompt、scenario 描述、feedback JSON 结构都服务于它自己的研究框架和 `Qlib` 场景。可以借组织方式，但不建议直接复制文本层实现。

### 6.3 不建议先做整仓库本地跑通

对我们现在最有价值的是“先看清结构与可借点”，而不是花很多时间在当前环境完整跑通一个 Linux + Docker + Qlib 强依赖系统。

## 7. 映射到我们 4 条主策略

### 7.1 `trend_leader_unified`

最值得借的不是它的 quant 默认因子，而是：

- 统一排序器实验框架
- 研究任务的分轮迭代
- baseline / 当前最好 / 新版本的对照方式

不适合直接借：

- `Qlib` 默认模型或回测窗口
- 直接用其 factor/model 框架替代现有短线候选筛选

### 7.2 `earnings_surprise`

这是当前最适合吃到 `RD-Agent` 思想的一条策略：

- 多季度 surprise history
- 财务质量层
- 公告反应层
- 行业确认层

这些都可以被组织成“研究假设 -> 实验 -> 反馈 -> 下一轮”的统一链路。

### 7.3 `hundred_day_high`

它不直接提供 A 股突破形态答案，但可以服务于：

- 突破后的统一排序器
- 行业强度 / 质量层 / 资金层的实验组合
- “哪些过滤最有效”的系统性验证

### 7.4 `monthly_slow_rise`

这条也很适合借 `RD-Agent`：

- 月线/周线结构之外，再叠质量和连续性因子
- 对慢牛结构做多轮实验，不靠一次拍脑袋定规则

## 8. 下一步是否值得继续本地实验

值得继续，但不是“整套跑起来”那种实验，而是 2 种更小的动作：

1. 继续做第二轮源码深挖
   - 把 `Trace / loop / feedback / workspace` 再拆细一点
   - 补一版“能映射到我们研究自动化层的最小骨架”
2. 抽象它的研究流程
   - 不复制实现
   - 先在我们仓库里总结出一个轻量版“策略研究循环”

## 9. 当前结论

`RD-Agent` 最值得我们借的不是“量化选股答案”，而是下面 4 个东西：

1. 统一研究循环
2. factor / model 共优化思想
3. 动作选择与研究调度思想
4. baseline / SOTA / 增量实验的反馈闭环

如果只用一句话概括：

- `RD-Agent` 对我们的价值，不在替代主策略，而在帮助我们把主策略升级成“可持续研究和迭代”的系统
