# 短线编排骨架设计稿

最后更新：2026-05-02

## 1. 背景

当前工程适合承担：

- 任务编排
- 结果落库
- 报告输出
- 留痕与文档治理

但不适合直接演化成“短线盘中扫描与执行内核”。

外部能力判断已经比较明确：

- `WonderTrader`
  - 更适合承担短线扫描、调度、回测、执行底座
- `FinGenius`
  - 更适合承担 A 股短线解释层、游资/筹码/大单/风险说明层

因此当前更合理的方向不是新起第三个工程，而是在现有工程中增加一个**独立目录的短线编排骨架**，让当前工程成为：

- 编排层
- 结果汇总层
- 展示与留痕层

## 2. 目标

第一阶段只做“短线编排骨架”，第二阶段补“单机外部进程接法”。

本阶段目标：

1. 在当前工程内建立独立代码目录，不污染现有主策略链路。
2. 定义 `WonderTrader -> 当前工程 -> FinGenius` 的中间协议。
3. 提供一个最小可运行的 orchestrator。
4. 先用 stub/mock adapter 跑通输出，而不是立刻接真实 `WonderTrader` / `FinGenius`。
5. 形成独立文档，持续跟踪这条线的定位、现状和下一步。

## 3. 非目标

本阶段不做：

- 不接真实 `WonderTrader` 引擎
- 不接真实 `FinGenius` agent 流程
- 不做盘中实时服务
- 不做交易执行
- 不改现有 `run_fast_review_bundle.py` 主链路
- 不把这条线纳入现有四条主策略

第二阶段仍然不做：

- 不做 HTTP 服务化接入
- 不做消息队列
- 不做多机部署

## 4. 定位

这个骨架的定位不是“短线主引擎”，而是：

`当前工程中的短线编排中台`

职责拆分：

- 当前工程中的 `shortline_hub`
  - 统一接收候选
  - 调解释模块
  - 合并结果
  - 生成报告
  - 写出标准化产物
- `WonderTrader`
  - 未来提供候选池与扫描结果
- `FinGenius`
  - 未来提供短线解释与多视角说明

## 5. 目录方案

### 5.1 代码目录

新增独立目录：

- `src/shortline_hub/`

建议首版文件：

- `src/shortline_hub/schemas.py`
  - 定义候选、解释结果、聚合结果、运行摘要
- `src/shortline_hub/orchestrator.py`
  - 编排流程主入口
- `src/shortline_hub/report_builder.py`
  - 生成 Markdown / JSON 友好的汇总结果
- `src/shortline_hub/adapters/wondertrader_adapter.py`
  - `WonderTrader` 适配器接口与 stub 实现
- `src/shortline_hub/adapters/fingenius_adapter.py`
  - `FinGenius` 适配器接口与 stub 实现

### 5.2 脚本入口

新增：

- `scripts/run_shortline_hub.py`

用途：

- 用命令行跑最小编排链路
- 首版使用 stub adapter
- 生成独立产物目录，便于后续接真实外部框架前先验证协议与输出格式

### 5.3 数据输出

首版默认输出到独立目录，例如：

- `data/shortline_hub/`

首版产物建议包括：

- `shortline_candidates.json`
- `shortline_explanations.json`
- `shortline_report.md`
- `run_summary.json`

### 5.4 独立文档

新增独立文档：

- `docs/architecture/2026-05-02-shortline-hub-design.md`
  - 设计、边界、协议与职责
- `docs/architecture/2026-05-02-shortline-hub-implementation-plan.md`
  - 实施步骤
- `docs/architecture/shortline-hub-tracking.md`
  - 持续跟踪状态、当前阶段、下一步

## 6. 中间协议

### 6.1 候选输入协议

`WonderTrader` 未来接入时，至少要统一成下面这些字段：

- `candidate_id`
- `symbol`
- `name`
- `trade_date`
- `scan_source`
- `trigger_type`
- `trigger_reason`
- `trigger_score`
- `price`
- `change_pct`
- `volume_ratio`
- `turnover_rate`
- `board_name`
- `risk_flags`

### 6.2 解释输出协议

`FinGenius` 未来接入时，至少要输出：

- `candidate_id`
- `hot_money_summary`
- `big_deal_summary`
- `chip_commentary`
- `sentiment_commentary`
- `risk_commentary`
- `short_term_view`
- `confidence_label`

### 6.3 聚合结果协议

编排层最终统一输出：

- `candidate_id`
- `symbol`
- `name`
- `trade_date`
- `trigger_type`
- `trigger_score`
- `board_name`
- `scan_source`
- `trigger_reason`
- `hot_money_summary`
- `big_deal_summary`
- `chip_commentary`
- `sentiment_commentary`
- `risk_commentary`
- `short_term_view`
- `confidence_label`

## 7. 数据流

首版数据流固定为：

1. `WonderTraderStubAdapter` 产出候选
2. `ShortlineHubOrchestrator` 统一接收候选
3. `FinGeniusStubAdapter` 针对每个候选产出解释
4. `report_builder` 合并生成报告和结构化产物
5. `run_shortline_hub.py` 写入输出目录

后续真实接入时，只替换两侧 adapter，不改中间协议与 orchestrator 主流程。

## 8. 单机外部进程接法

当前推荐的真实接入方式不是把 `WonderTrader` / `FinGenius` 硬塞进当前 Python 环境，而是：

`当前工程 subprocess 调本机外部脚本 + JSON 文件协议`

推荐目录：

- 当前工程：
  - `D:\bb\daily_stock_analysis`
- `WonderTrader`：
  - `D:\bb\WonderTrader`
- `FinGenius`：
  - `D:\bb\FinGenius`

推荐环境：

- 当前工程一个 venv
- `WonderTrader` 一个独立 venv
- `FinGenius` 一个独立 venv

这样做的好处：

- 依赖不打架
- 当前工程只做编排，不侵入外部框架内部环境
- 后续出问题更容易排查

### 8.1 `WonderTrader` 调用协议

当前工程会写一个 request JSON 给外部脚本，内容最少包括：

```json
{
  "trade_date": "2026-05-02",
  "top_n": 10
}
```

外部 `WonderTrader` 脚本负责写一个 output JSON，内容为候选列表：

```json
[
  {
    "candidate_id": "2026-05-02-300001",
    "symbol": "300001",
    "name": "示例股票",
    "trade_date": "2026-05-02",
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "示例触发原因",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "示例板块",
    "risk_flags": []
  }
]
```

### 8.2 `FinGenius` 调用协议

当前工程会对单个候选写 request JSON：

```json
{
  "candidate": {
    "candidate_id": "2026-05-02-300001",
    "symbol": "300001",
    "name": "示例股票",
    "trade_date": "2026-05-02",
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "示例触发原因",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "示例板块",
    "risk_flags": []
  }
}
```

外部 `FinGenius` 脚本负责写 output JSON：

```json
{
  "candidate_id": "2026-05-02-300001",
  "hot_money_summary": "示例热钱说明",
  "big_deal_summary": "示例大单说明",
  "chip_commentary": "示例筹码说明",
  "sentiment_commentary": "示例情绪说明",
  "risk_commentary": "示例风险说明",
  "short_term_view": "示例短线观点",
  "confidence_label": "high"
}
```

### 8.3 当前工程调用方式

当前 `shortline_hub` 已支持两种模式：

- `--mode stub`
  - 完全本地 stub
- `--mode process`
  - 通过外部脚本接 `WonderTrader` / `FinGenius`

`process` 模式关键参数：

- `--wt-python-executable`
- `--wt-script-path`
- `--wt-workdir`
- `--wt-runtime-dir`
- `--wt-timeout-seconds`
- `--fg-python-executable`
- `--fg-script-path`
- `--fg-workdir`
- `--fg-runtime-dir`
- `--fg-timeout-seconds`

### 8.4 当前推荐节奏

当前建议的真实接入顺序：

1. 先把 `WonderTrader` 外部脚本接通，只输出候选
2. 再把 `FinGenius` 外部脚本接通，只做单票解释
3. 最后再考虑批量解释优化、展示页和历史对比

### 8.5 当前仓库内已提供的模板脚本

为了让单机接法更容易起步，当前仓库已经自带两份模板脚本：

- `scripts/bridges/shortline_wondertrader_bridge_template.py`
- `scripts/bridges/shortline_fingenius_bridge_template.py`

作用：

- 它们不是最终业务脚本
- 但它们已经完全符合 `shortline_hub --mode process` 的输入输出协议
- 你可以先直接复制到：
  - `D:\bb\WonderTrader\bridge\wt_export_candidates.py`
  - `D:\bb\FinGenius\bridge\fg_explain_candidate.py`
- 再把内部示例逻辑替换成真实 `WonderTrader` / `FinGenius` 调用

这样第一轮接入时，当前工程侧完全不用再改协议

## 9. 为什么先用 stub

当前先用 stub 的原因：

1. 真实 `WonderTrader` 接入会牵涉运行方式、环境与数据组织，不适合在第一步就硬接。
2. 真实 `FinGenius` 接入会牵涉 agent 编排、依赖和调用成本，也不适合第一步就硬接。
3. 先把中间协议跑通，后续替换 adapter 才不会把当前工程搅乱。

## 10. 成功标准

第一阶段完成标准：

1. `src/shortline_hub/` 目录建立完成。
2. `scripts/run_shortline_hub.py` 可以独立运行。
3. stub 版本可以稳定产出标准化 JSON 和 Markdown 报告。
4. 有测试覆盖：
   - orchestrator 聚合
   - report 输出
   - CLI 落盘
5. 独立跟踪文档建立完成。

## 11. 下一阶段

骨架稳定后，再进入第二阶段：

1. 真接 `WonderTrader`
   - 先只接候选输出
2. 真接 `FinGenius`
   - 先只接单票解释
3. 再考虑：
   - 短线结果展示页
   - 历史结果对比
   - 日常任务调度

## 12. 结论

当前最合理的做法不是直接把 `WonderTrader` / `FinGenius` 硬接进现有主链路，而是先在当前工程里建立：

`独立目录 + 独立协议 + 独立文档 + 最小可运行编排骨架`

这样后续无论接真实 `WonderTrader` 还是真实 `FinGenius`，都只是在 adapter 层逐步替换，不会破坏当前主策略工程。
