# RD-Agent 首轮源码深挖提纲
最后更新：2026-04-26

## 1. 这份提纲解决什么问题

这份提纲不是复述 `RD-Agent` 是什么，而是为“第一轮源码深挖”定一个固定入口、固定顺序和固定产出，避免后面再次发散成大范围随意阅读。

本轮目标只有 3 个：

1. 看清 `RD-Agent` 的 quant 场景是如何接在通用 R&D 框架上的
2. 拆出哪些能力对我们当前 4 条主策略最有借鉴价值
3. 明确哪些部分值得继续深入，哪些部分先不碰

## 2. 已确认的仓库现实结构

基于 `microsoft/RD-Agent` 主仓库当前公开结构，`RD-Agent` 不是单一 quant 脚本，而是“通用框架 + 场景化入口”的组织方式。

第一轮最关键的入口文件如下：

- CLI 入口：`rdagent/app/cli.py`
- quant 命令入口：`rdagent/app/qlib_rd_loop/quant.py`
- quant 配置中心：`rdagent/app/qlib_rd_loop/conf.py`
- 通用循环骨架：`rdagent/components/workflow/rd_loop.py`
- 通用演化抽象：`rdagent/core/evolving_framework.py`
- quant 场景定义：`rdagent/scenarios/qlib/experiment/quant_experiment.py`
- quant 假设生成：`rdagent/scenarios/qlib/proposal/quant_proposal.py`
- quant 文档入口：`docs/scens/quant_agent_fin.rst`

当前已经确认的两个关键事实：

- 官方 quant 入口命令是 `rdagent fin_quant`
- quant 场景底层依赖 `Qlib`，并不是独立于 `Qlib` 的另一套量化底座

## 3. 为什么先看 RD-Agent

对我们来说，`RD-Agent` 最有价值的不是“直接替代现有选股策略”，而是它可能提供一套更强的研究闭环：

- 如何自动生成研究假设
- 如何把假设转成实验计划
- 如何让 coder / runner / evaluator 连起来
- 如何根据反馈继续迭代下一轮实验

这正对应我们后面最缺的一层：不是再加策略名，而是给 `trend_leader_unified / earnings_surprise / hundred_day_high / monthly_slow_rise` 增加统一研究与迭代能力。

## 4. 第一轮深挖顺序

### 4.1 先看运行入口和装配关系

先回答 3 个问题：

1. `fin_quant` 是怎么从 CLI 进入 quant loop 的
2. quant loop 和 model loop / factor loop 是什么关系
3. 配置、场景、proposal、developer、runner、feedback 分别从哪里注入

本轮重点文件：

- `rdagent/app/cli.py`
- `rdagent/app/qlib_rd_loop/quant.py`
- `rdagent/app/qlib_rd_loop/conf.py`

预期产出：

- 一张“入口到循环”的调用路径图
- 一份 quant 运行链路的对象装配清单

### 4.2 再看通用 R&D 循环骨架

先回答 4 个问题：

1. `RDLoop` 的核心阶段有哪些
2. `hypothesis -> experiment -> coding -> running -> feedback -> record` 是怎样串起来的
3. 并行控制、循环次数、历史 trace 是怎么保存的
4. 哪些是通用机制，哪些是 quant 特有逻辑

本轮重点文件：

- `rdagent/components/workflow/rd_loop.py`
- `rdagent/core/evolving_framework.py`
- `rdagent/core/proposal.py`
- `rdagent/core/experiment.py`

预期产出：

- 一张通用循环阶段图
- 一份“可借到我们框架”的通用能力清单

### 4.3 再看 quant 场景本体

先回答 4 个问题：

1. quant scenario 给 agent 注入了哪些背景信息
2. hypothesis 生成时，factor / model / quant 之间如何分工
3. quant experiment 的对象边界是什么
4. 它的“研究对象”到底是单因子、因子组合、模型，还是更上层的组合实验

本轮重点文件：

- `rdagent/scenarios/qlib/experiment/quant_experiment.py`
- `rdagent/scenarios/qlib/proposal/quant_proposal.py`
- `rdagent/scenarios/qlib/proposal/factor_proposal.py`
- `rdagent/scenarios/qlib/proposal/model_proposal.py`
- `rdagent/scenarios/qlib/prompts.yaml`

预期产出：

- 一份 quant 场景对象关系说明
- 一份可映射到我们“统一因子层/排序层”的切入点清单

### 4.4 再看执行、评估与反馈闭环

先回答 4 个问题：

1. coder 负责生成什么
2. runner 负责运行什么
3. feedback/summarizer 是如何判定实验是否可接受的
4. 知识库、历史 trace、缓存是否参与下一轮 proposal

本轮重点文件：

- `rdagent/scenarios/qlib/developer/factor_runner.py`
- `rdagent/scenarios/qlib/developer/model_runner.py`
- `rdagent/scenarios/qlib/developer/feedback.py`
- `rdagent/core/knowledge_base.py`
- `rdagent/log/**`

预期产出：

- 一份实验闭环说明
- 一份“哪些反馈机制能借给我们本地策略调参/回测链路”的候选点

### 4.5 最后看 quant 假设与回测口径

先回答 4 个问题：

1. `Qlib` 数据、模板、回测配置在哪里注入
2. 默认市场、时间范围、benchmark、交易成本如何设置
3. 评估输出是偏信号层、组合层还是研究报告层
4. 哪些默认假设不适合直接迁移到 A 股本地短线框架

本轮重点文件：

- `docs/scens/quant_agent_fin.rst`
- `rdagent/app/qlib_rd_loop/conf.py`
- `rdagent/scenarios/qlib/experiment/workspace.py`
- `rdagent/scenarios/qlib/experiment/prompts.yaml`

预期产出：

- 一份 quant 默认回测口径摘要
- 一份“不应直接照搬”的假设清单

## 5. 我们最关心的 5 个问题

这 5 个问题是本轮深挖的主线，后面所有阅读都应该回到这上面：

1. `RD-Agent` 如何把“想法”变成“实验”
2. `RD-Agent` 如何把“实验”变成“可复用知识”
3. `RD-Agent` 如何组织 factor/model 共迭代，而不是单点调参
4. `RD-Agent` 的 quant 部分哪些能服务我们现有 4 条主策略
5. `RD-Agent` 哪些能力值得接入，哪些只适合参考其组织方式

## 6. 对我们框架最可能有价值的借鉴点

第一轮先带着下面这些假设去读源码：

- 借“研究循环”，不是借“现成选股结果”
- 借“统一实验框架”，不是借“整套 quant 默认口径”
- 借“factor/model 迭代组织方式”，不是借“它的全部策略模板”
- 借“反馈闭环”，不是借“它的具体 prompt 文案”

优先映射到我们本地框架的可能位置：

- `earnings_surprise`
  - 多季度 surprise / 质量因子 / 排序实验
- `monthly_slow_rise`
  - 质量层 / 稳定性层 / 参数迭代
- `trend_leader_unified`
  - 统一排序器实验，而不是继续横向加子策略
- `hundred_day_high`
  - 形态过滤后的二次排序与验证闭环

## 7. 第一轮不做什么

为了防止任务失控，本轮明确不做：

- 不尝试在当前 Windows 环境完整跑通 `RD-Agent`
- 不尝试把 `RD-Agent` 直接接入我们仓库
- 不急着复刻其 prompt、前端或 UI
- 不把它的 quant 默认市场、回测窗口、模板配置直接拿来套用

原因很简单：官方 README 明确写了当前主要支持 Linux，且它的 quant 场景是建立在 `Qlib` 口径上的。我们现在先做结构拆解，比直接跑更有价值。

## 8. 第一轮固定产出

完成首轮深挖后，必须沉淀 4 类输出：

1. `RD-Agent` quant 架构摘要
2. 对我们最值得借的 `3-5` 个能力点
3. 可接入我们框架的具体落点
4. 下一步是否值得继续本地拉起最小运行实验

## 9. 建议的下一步动作

按顺序执行：

1. 先写一份 `RD-Agent` quant 调用链摘要
2. 再写一份“可借鉴能力映射到本地 4 条主策略”的清单
3. 最后再决定是否进入第二轮更深的源码拆解，或者切到 `Qlib`

## 10. 参考入口

- GitHub 仓库：`https://github.com/microsoft/RD-Agent`
- quant 文档：`https://rdagent.readthedocs.io/en/latest/scens/quant_agent_fin.html`
- 项目 README：`https://github.com/microsoft/RD-Agent/blob/main/README.md`
