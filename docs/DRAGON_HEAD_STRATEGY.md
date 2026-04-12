# 龙头策略升级

## 1. 目标

将原有偏“板块强势股识别”的龙头策略，升级为更通用的“高辨识度核心龙头策略”。

升级后的核心定义：

- 龙头不只是涨得快
- 龙头的内核是共识
- 共识来源于 `逻辑共识 + 资金共识`

## 2. 新的默认判断框架

### 龙头类型

固定分为四类：

- `hybrid_leader`
- `logic_leader`
- `capital_leader`
- `pseudo_leader`

其中：

- `hybrid_leader`：逻辑和资金都强
- `logic_leader`：逻辑更强
- `capital_leader`：资金更强
- `pseudo_leader`：看起来强，但辨识度和承接不够

### 核心因子

固定优先级：

`辨识度 > 板块地位 > 相对强度 > 流动性 > 催化 > 其它`

## 3. 辨识度口径

### 逻辑共识

主要看：

- 是否属于行业/题材里最先被想到的票
- 是否具备清晰主营业务和产业链位置
- 是否有白名单样例或强逻辑映射
- 是否在板块中具备“龙头地位”而非普通跟风

### 资金共识

主要看：

- 当日成交额
- 近 20 日平均成交额
- 当日换手率
- 近 20 日平均换手率
- 是否具备持续承接与高流动性

## 4. 明确过滤

默认降权：

- 小成交
- 边缘票
- 杂毛票
- 没有清晰逻辑映射的跟风票
- 即使涨幅很大，但流动性和辨识度不足的票

## 5. 本次实现

这次升级已经补上：

- `DragonHeadAnalysisService`
- `analyze_dragon_head` Agent 工具
- `dragon_head.yaml` 新版策略定义

输出字段包括：

- `leader_probability`
- `leader_type`
- `recognizability_score`
- `logic_consensus_score`
- `capital_consensus_score`
- `sector_leadership_score`
- `relative_strength_score`
- `liquidity_score`
- `catalyst_score`
- `factor_breakdown`
- `ranking_tuple`

## 6. 当前边界

- 当前先做通用龙头分析，不直接接 `/signals` 或单独龙头快照
- 后续如有需要，可以继续扩展成龙头候选池或龙头专题快照
