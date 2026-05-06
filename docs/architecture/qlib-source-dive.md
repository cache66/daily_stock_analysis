# Qlib 源码深挖记录
最后更新：2026-04-27

## 1. 仓库定位

`Qlib` 不是单一选股策略仓库，也不是只提供几个模型的算法包。它本质上是一个面向量化研究的基础设施平台，试图把下面几层打通：

- 数据获取与组织
- 特征/标签处理
- 数据集切分
- 模型训练与推理
- 信号分析
- 组合回测
- 实验记录与任务管理

对我们最重要的判断是：

- `Qlib` 的核心价值不在“帮我们直接选出今天该看的 A 股短线票”
- 它最强的不是某个单模型
- 它真正强的是“统一研究底座”

换句话说，`Qlib` 对我们的价值主要在：

- 统一因子层
- 统一数据集层
- 统一 workflow 层
- 统一 recorder / task management / rolling evaluation 层

参考入口：

- GitHub 仓库：<https://github.com/microsoft/qlib>
- 官方文档：<https://qlib.readthedocs.io/en/latest/>

## 2. 核心结构与入口

本轮优先看的真实入口包括：

- 项目总览：`README.md`
- workflow 文档：`docs/component/workflow.rst`
- task management 文档：`docs/advanced/task_management.rst`
- 配置示例：`examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml`
- 代码式 workflow 示例：`examples/workflow_by_code.py`
- 数据集核心：`qlib/data/dataset/__init__.py`
- 预置 handler：`qlib/contrib/data/handler.py`
- recorder：`qlib/workflow/__init__.py`
- 策略基线：`qlib/contrib/strategy/signal_strategy.py`

可以先把 `Qlib` 理解成 5 层：

1. 数据层
   - `provider_uri`
   - `QlibDataLoader`
   - region / data backend
2. 特征与数据集层
   - `Alpha158` / `Alpha360`
   - `DatasetH` / `TSDatasetH`
   - train / valid / test segments
3. workflow 层
   - `qrun`
   - YAML task config
   - 按 code 构建 workflow
4. 评估与组合层
   - `SignalRecord`
   - `SigAnaRecord`
   - `PortAnaRecord`
   - `TopkDropoutStrategy`
5. 实验管理层
   - `R.start(...)`
   - Recorder
   - TaskGen / TaskManager / RollingGen / Trainer

## 3. 真正的优点

### 3.1 它提供了统一的量化研究工作流

`Qlib` 最有价值的一点，是把量化研究流程做成了统一结构，而不是散落成脚本集合。

从 `workflow.rst` 和 benchmark YAML 可以看到，一个完整执行通常包含：

- 数据加载
- 数据处理
- 数据切分
- 模型训练与推理
- 信号分析
- 回测分析

并且这些步骤可以通过：

- `qrun configuration.yaml`
- 或代码方式 `workflow_by_code.py`

来统一驱动。

这对我们最有价值，因为我们当前 4 条主策略虽然已经成型，但研究动作还没有这么统一：

- 一部分在策略脚本
- 一部分在快复盘
- 一部分在评估脚本
- 一部分靠人工比较

`Qlib` 给的启发是：研究过程本身要可配置、可重放、可比较。

### 3.2 它把“特征层”和“数据集层”分得很清楚

`DatasetH` 的核心思想很重要：

- `handler` 负责准备与处理底层数据
- `dataset` 负责按 segment 组织和准备模型要消费的数据

也就是说，数据预处理和模型训练输入不是混在一起的。

再看 `Alpha158` / `Alpha360`：

- 它们不是一个个零散因子脚本
- 而是标准化 handler 模板
- 自带特征、标签、processor 配置

这正对应我们后面最该补的一层：

- 统一财务质量因子层
- 统一趋势/结构因子层
- 统一行业强度因子层
- 统一“给排序器喂什么”的数据组织方式

### 3.3 它的 config-driven workflow 很强

`workflow_config_lightgbm_Alpha158.yaml` 这个例子非常典型，里面把下面几块写成了统一配置：

- `provider_uri`
- `market`
- `benchmark`
- `data_handler_config`
- `dataset`
- `model`
- `record`
- `TopkDropoutStrategy`
- `backtest`

这说明 `Qlib` 不是单纯“模型仓库”，而是把研究流程抽象成一个标准 task。

这对我们最大的借鉴不在“照抄 YAML”，而在：

- 让研究实验有稳定契约
- 让模型、数据、回测、记录都挂在一份任务描述上
- 让不同实验之间容易横向比较

### 3.4 它的 recorder / experiment 管理值得借

`qlib.workflow` 这一层很重要。

`QlibRecorder` 的设计目标非常明确：

- 不是简单把结果输出到文件
- 而是把实验作为对象管理
- 可以 start / end / resume
- 可以 log params / artifacts / objects
- 可以 search / list records

这意味着它天然支持：

- 多轮实验记录
- 结果追溯
- 统一比较
- 滚动实验与批量任务管理

对我们来说，这比单纯“跑一次脚本生成 CSV”高了一个层级。

### 3.5 它的 task management / rolling evaluation 很适合研究层

`task_management.rst` 说明它不止支持跑一个 task，还支持：

- `TaskGen`
- `RollingGen`
- `TaskManager`
- `Trainer`
- `Collector / Group / Ensemble`

这套东西本质上是在解决：

- 怎么自动生成多轮实验
- 怎么按不同时间段跑
- 怎么收集和合并结果

这对我们非常关键，因为我们后面一定会遇到：

- 多版本策略比较
- 不同时间段表现比较
- 不同因子组合比较
- 多轮回测结果收集

这类问题靠手工脚本会越来越乱，而 `Qlib` 已经把这类研究调度问题显式化了。

## 4. 我们能直接用的能力

### 4.1 统一因子层与数据组织思想

这是 `Qlib` 对我们最直接的价值。

我们不一定直接引入 `Alpha158/Alpha360`，但可以借它的组织方式：

- 用 handler 抽象底层数据准备
- 用 dataset 抽象训练/推理消费数据
- 用 segment 抽象 train / valid / test

落到我们框架里，最适合先服务：

- `earnings_surprise`
  - surprise history
  - 财务质量因子
  - 公告后反应因子
  - 行业确认因子
- `monthly_slow_rise`
  - 质量层
  - 稳定性层
  - 流动性层
- `trend_leader_unified / hundred_day_high`
  - 行业强度
  - 质量 overlay
  - 统一排序输入层

### 4.2 统一 research workflow 思想

`qrun` 最大的价值不是“命令方便”，而是把研究任务收敛到统一结构。

对我们来说，可以借成：

- 统一研究任务描述
- 统一实验运行入口
- 统一输出字段
- 统一评估流程

这会比继续在每条策略脚本里各自加参数更稳。

### 4.3 recorder / record template 思想

`SignalRecord`、`SigAnaRecord`、`PortAnaRecord` 这套分层很值得借：

- 信号层记录
- 信号分析层记录
- 组合/回测层记录

这正好对应我们现在也该拆开的 3 层：

- 候选信号本身
- 信号质量分析
- 组合化绩效分析

### 4.4 rolling / task management 思想

我们不一定要直接引 MongoDB 和原生 TaskManager，但它的思想值得借：

- 滚动窗口验证
- 多任务批量训练
- 统一结果收集
- 自动化实验管理

这很适合我们未来做：

- 多日期 snapshot 评估
- 不同参数组合对照
- 新旧策略版本比较

## 5. 只适合参考、不建议直接接入的部分

### 5.1 它默认是标准量化研究范式，不是本地短线扫描范式

从 benchmark config 和 docs 可以看出，`Qlib` 默认研究范式是：

- 宽表特征
- 模型预测
- 信号分析
- 组合构建
- 回测评估

而我们当前的本地主策略更偏：

- 事件驱动
- 快照筛选
- A 股短线结构判断
- 每日快复盘

所以它不能直接替代我们的主策略信号层。

### 5.2 它的默认示例偏中长周期与组合口径

典型示例都围绕：

- `train / valid / test`
- `TopkDropoutStrategy`
- benchmark
- 账户规模
- 成本模型

这说明它的默认中心是“研究和回测”，不是“今天该看哪几只票”。

所以适合借“研究方法”和“评估方法”，不适合直接借“选股口径”。

### 5.3 它是底座，不是轻量插件

`Qlib` 的覆盖面很大：

- 数据层
- 模型层
- 策略层
- recorder
- online / task / rolling

这意味着它适合成为研究基础设施参考，但不适合一上来整套接入当前仓库。

## 6. 不建议引入的部分

### 6.1 不建议把 Qlib 当成当前 4 条主策略的直接执行引擎

它不适合直接接管：

- 每日快扫
- 快复盘
- 候选解释
- 本地短线 UI 展示

### 6.2 不建议直接照搬默认策略基线

例如 `TopkDropoutStrategy` 非常适合作为标准回测/组合基线，但不适合作为我们当前用户侧主策略表达。

它更像：

- 标准组合化测试口径
- 对外部研究结果的统一基线

而不是主选股策略本身。

### 6.3 不建议先做整套深集成

现在最合理的是先借思想和边界设计：

- 因子层
- dataset 层
- workflow 层
- recorder 层

而不是直接在当前仓库里硬接 `Qlib` 全套对象模型。

## 7. 映射到我们 4 条主策略

### 7.1 `trend_leader_unified`

最值得借：

- 统一排序输入层
- 因子/标签组织方法
- 研究 workflow
- recorder 和版本比较方式

不适合直接借：

- 默认组合策略表达
- 纯模型化预测范式替代当前短线结构判断

### 7.2 `earnings_surprise`

这是最适合吃到 `Qlib` 的一条。

最值得借：

- 财务质量层
- 多季度 surprise history 组织方式
- 数据集和标签标准化
- 统一排序器实验框架

### 7.3 `hundred_day_high`

最值得借：

- 结构因子与质量因子统一入表
- 滚动验证
- 新旧过滤规则的统一评估

### 7.4 `monthly_slow_rise`

最值得借：

- 质量层 / 稳定性层 / 流动性层统一建模
- 滚动期比较
- recorder 化实验结果管理

## 8. 下一步是否值得继续本地实验

值得，但方向应该很克制。

更合理的后续动作是：

1. 继续源码层分析
   - 再拆 `DataHandlerLP`、processor、record_temp、rolling 模块
2. 抽象成我们的轻量版研究骨架
   - 不直接接 `Qlib`
   - 先借它的分层和任务结构
3. 以后如需实验
   - 只做最小范围验证，例如单一研究任务/单一排序器实验

## 9. 当前结论

`Qlib` 对我们的最大价值，不在提供“新的策略名”，而在提供下面 4 个基础层能力：

1. 统一因子与数据集组织
2. 统一研究 workflow
3. 统一 recorder / record 体系
4. 统一 rolling / task management 思想

如果只用一句话概括：

- `Qlib` 是我们未来“研究底座层”最值得借的外部对象之一，但它不是当前本地短线主策略的直接替代品
