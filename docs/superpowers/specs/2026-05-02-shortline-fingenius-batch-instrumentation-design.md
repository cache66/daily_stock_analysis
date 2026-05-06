# 短线 FinGenius 批量留痕与真实批量验证设计

## 1. 背景

当前短线链路已经具备以下能力：

- `WonderTrader` 候选可以通过 `shortline_hub --mode process` 进入仓库编排链路
- `FinGenius` 单票解释已经从纯启发式兜底升级为“`bridge_data` -> upstream 单票工具 -> heuristic fallback”
- `ShortlineHubOrchestrator` 已经会对候选列表逐票调用 `explainer.explain_candidate(...)`

也就是说，“批量候选逐票调用 `FinGenius` 单票真实解释桥”主链路已经存在，不需要再新建一条并行批处理流程。

当前真正缺少的是：

- 运行产物无法明确说明每只票的解释来源
- `run_summary.json` 缺少 `FinGenius` 批量命中统计
- 报告里无法直接看出哪些票命中真实工具，哪些票回退到启发式
- 没有一轮带新留痕能力的真实 `process mode` 批量 smoke 结果可作为基线

## 2. 目标

本次目标分两阶段：

1. 扩展当前 `shortline_hub` 协议和运行产物，让批量运行能显式记录 `FinGenius` 解释来源、命中情况和耗时
2. 基于新留痕能力，运行一次真实 `process mode` 批量 smoke，沉淀真实结果和风险观察

本次完成后，用户应能直接从 `run_summary.json`、`shortline_report.md` 和逐票 JSON 产物中回答这些问题：

- 这轮一共解释了多少只票
- 哪些票命中 `bridge_data`
- 哪些票命中 `FinGenius upstream` 真实工具
- 哪些票只是旧协议兼容回填，尚未提供可信来源判定
- 哪些票回退到 heuristic fallback
- 每只票大致耗时多少
- 每个 upstream 工具大致耗时多少
- 本轮批量解释总体耗时多少

## 3. 非目标

本次不做：

- 不重写 `shortline_hub` 主流程
- 不引入新的独立批处理脚本来替代现有 `scripts/run_shortline_hub.py`
- 不把 `FinGenius` 升级成完整 multi-agent / LLM research 模式
- 不修改 `WonderTrader` 候选协议
- 不为此新增数据库持久化
- 不在本次首版里建设复杂监控系统、重试编排器或并发池
- 不修改外部 `D:\bb\FinGenius\bridge\fg_explain_candidate.py` 协议之外的内部实现约束，除非真实 smoke 暴露出必须同步修复的问题

## 4. 方案比较

### 方案 A：只跑真实 smoke，不改协议

做法：

- 直接使用现有 `process mode` 跑一轮真实批量
- 从生成文案和外部日志人工推断真实工具命中情况

优点：

- 见效快
- 代码改动最少

缺点：

- 结果不可结构化复用
- 需要人工读文案猜测来源
- 后续报告、统计、回归测试都无法稳定依赖

### 方案 B：新增独立统计脚本，不改主协议

做法：

- 保持当前 `shortline_hub` 不变
- 另写脚本解析运行产物或桥接脚本输出，生成单独统计报告

优点：

- 对主链路侵入较小

缺点：

- 形成平行实现
- 统计事实和主报告事实可能漂移
- 不符合“优先复用现有模块、不新增平行实现”的仓库规则

### 方案 C：扩展当前协议和产物，再跑真实 smoke

做法：

- 在现有 `FinGenius` bridge 输出中增加来源与耗时元数据
- 在 `shortline_hub` schema / orchestrator / report builder 中透传并聚合这些信息
- 之后运行真实 `process mode` 批量 smoke，直接复用这些产物判断命中情况

优点：

- 主链路事实就是统计事实
- 结构化产物可测试、可追踪、可复用
- 后续继续做批量化时不用再补第二套留痕

缺点：

- 首次改动面比方案 A 略大

### 推荐

推荐方案 C。

原因：

- 当前用户目标已经从“单票接线”转向“批量化”
- 现有逐票调用链路已存在，最合理的增量是补协议和留痕，而不是再接新线
- 有了结构化来源字段后，真实 smoke 的结果才可复验、可沉淀、可做回归

## 5. 首版设计

### 5.1 协议扩展

当前 `FinGenius` bridge 只返回 8 个说明字段：

- `candidate_id`
- `hot_money_summary`
- `big_deal_summary`
- `chip_commentary`
- `sentiment_commentary`
- `risk_commentary`
- `short_term_view`
- `confidence_label`

首版在保持这 8 个字段兼容的前提下，新增一组可选元数据字段：

- `protocol_version`
  - 首版固定为 `shortline_fg_v1`
- `explanation_source`
  - 新 bridge 主动输出的枚举值首版固定为：
    - `bridge_data`
    - `upstream_tools`
    - `heuristic_fallback`
  - 仓库内 adapter 对旧外部输出兼容回填：
    - `legacy_unknown`
- `used_upstream_tools`
  - 列表，记录本次成功产出有效结果的 upstream 工具名
  - 轻量模式示例：`["HotMoneyTool", "ChipAnalysisTool"]`
  - 全量模式示例：`["HotMoneyTool", "ChipAnalysisTool", "BigDealAnalysisTool"]`
- `tool_error_count`
  - 整数，记录真实工具执行失败次数
- `tool_errors`
  - 列表，记录简化后的失败摘要，主要用于运行排障
- `explain_elapsed_ms`
  - 单票桥接侧耗时，单位毫秒
- `upstream_tool_elapsed_ms`
  - 字典，记录每个真实 upstream 工具的耗时拆分
  - 示例：`{"HotMoneyTool": 18, "ChipAnalysisTool": 10}`

兼容性要求：

- `FinGeniusProcessAdapter` 必须对这些字段采取“有则读取、无则回填默认值”的策略
- 当旧外部输出缺少 `explanation_source` 时，默认回填为 `legacy_unknown`，而不是伪装成 `heuristic_fallback`
- 外部脚本如果暂时还是旧格式，仓库内编排仍需正常运行
- 旧测试中只校验原 8 个字段的场景不应被破坏

### 5.2 编排层设计

在 `src/shortline_hub/schemas.py` 中扩展：

- `ShortlineExplanation`
  - 新增上述 `FinGenius` 元数据字段
- `ShortlineCombinedResult`
  - 透传解释来源与工具命中信息，方便写入最终 JSON / Markdown
- `ShortlineRunResult.summary_dict()`
  - 增加聚合统计：
    - `explanation_source_counts`
    - `legacy_unknown_count`
    - `upstream_tool_hit_counts`
    - `tool_error_count`
    - `total_explain_elapsed_ms`
    - `avg_explain_elapsed_ms`
    - `max_explain_elapsed_ms`
    - `min_explain_elapsed_ms`
    - `upstream_tool_elapsed_totals_ms`
    - `orchestrator_explain_elapsed_ms`

在 `src/shortline_hub/orchestrator.py` 中：

- 维持当前“逐票串行调用”行为，不在本次引入并发
- 记录 orchestration 视角的总解释耗时
- 将解释元数据带入 `combined_results`

这里有三类时间口径：

- `explain_elapsed_ms`
  - 单票 bridge 自报耗时
- `total_explain_elapsed_ms`
  - 所有单票 `explain_elapsed_ms` 的聚合求和
  - 主要用于回答“bridge 自报累计耗时”
- `orchestrator_explain_elapsed_ms`
  - orchestrator 本地观测到的整轮解释耗时
  - 主要用于回答“当前进程实际串行跑完这一轮用了多久”

如果单票元数据缺失，则：

- 单票耗时记为 `0`
- 单票来源记为 `legacy_unknown`
- 聚合统计仍可使用 orchestrator 本地总耗时做整体参考

### 5.3 报告与产物设计

`write_shortline_artifacts(...)` 继续输出：

- `shortline_candidates.json`
- `shortline_explanations.json`
- `shortline_combined_results.json`
- `shortline_report.md`
- `run_summary.json`

其中新增内容如下。

#### `shortline_explanations.json`

每条 explanation 增加以下字段，成为结构化事实源：

- `protocol_version`
- `explanation_source`
- `used_upstream_tools`
- `tool_error_count`
- `tool_errors`
- `explain_elapsed_ms`
- `upstream_tool_elapsed_ms`

#### `shortline_combined_results.json`

每只票增加：

- `protocol_version`
- `explanation_source`
- `used_upstream_tools`
- `tool_error_count`
- `tool_errors`
- `explain_elapsed_ms`
- `upstream_tool_elapsed_ms`

#### `run_summary.json`

新增批量统计字段：

- `explanation_source_counts`
- `upstream_tool_hit_counts`
- `explanation_success_count`
- `heuristic_fallback_count`
- `bridge_data_hit_count`
- `upstream_tools_hit_count`
- `legacy_unknown_count`
- `tool_error_count`
- `total_explain_elapsed_ms`
- `avg_explain_elapsed_ms`
- `max_explain_elapsed_ms`
- `min_explain_elapsed_ms`
- `upstream_tool_elapsed_totals_ms`
- `orchestrator_explain_elapsed_ms`

统计口径约定：

- `explanation_success_count`
  - 等于 `combined_count`
  - 因为当前流程里只要 explanation 正常返回就会进入 combined 结果
- `heuristic_fallback_count`
  - `explanation_source == "heuristic_fallback"` 的条数
- `bridge_data_hit_count`
  - `explanation_source == "bridge_data"` 的条数
- `upstream_tools_hit_count`
  - `explanation_source == "upstream_tools"` 的条数
- `legacy_unknown_count`
  - `explanation_source == "legacy_unknown"` 的条数
- `upstream_tool_elapsed_totals_ms`
  - 逐工具聚合求和后的耗时，例如 `{"HotMoneyTool": 55, "ChipAnalysisTool": 31}`

#### `shortline_report.md`

新增一个明确命名的批量摘要段：

- `## FinGenius Explain Summary`

其中至少展示：

- `explanation_sources`
- `upstream_tools`
- `bridge_elapsed_total`
- `upstream_tool_elapsed_totals`
- `orchestrator_elapsed_total`

逐票说明区额外展示：

- 解释来源
- 命中的 upstream 工具
- 分工具耗时
- 单票耗时
- 若存在则展示简化工具错误摘要

目标是让用户在不打开 JSON 的情况下，也能快速看出哪些票是真实工具解释，哪些票是回退解释。

### 5.4 Bridge 侧来源判定

`scripts/bridges/shortline_fingenius_bridge_template.py` 首版来源判定规则固定为：

1. 若命中本地 `bridge_data` 文件，则 `explanation_source = "bridge_data"`
2. 若命中任意 upstream 真实工具结果，则 `explanation_source = "upstream_tools"`
3. 否则走 `heuristic_fallback`

工具命中与执行模式规则：

- `used_upstream_tools` 只记录成功返回有效结构化结果的工具
- 某个工具报错不会阻断整条链路
- 默认轻量模式只调用 `HotMoneyTool` 和 `ChipAnalysisTool`
- `BigDealAnalysisTool` 默认关闭，只在显式打开 `--fg-enable-big-deal` 时进入真实调用
- 若未开启 `BigDealAnalysisTool`，`big_deal_summary` 允许用已抓到的资金/量价信息生成代理摘要
- 只要本轮实际启用的 upstream 工具中至少一个返回有效结构化内容，整体就视为 `upstream_tools`

这样既符合当前 bridge 的 fail-open 设计，也能反映“部分真实、部分失败”的中间态。

## 6. 真实 smoke 设计

第二阶段在首版代码完成后执行一次真实 `process mode` 批量 smoke。

目标：

- 验证新增元数据字段在真实外部脚本场景下能正确落盘
- 验证 `run_summary.json` 的命中统计是否符合实际
- 确认当前推荐的独立 `Python 3.11` `FinGenius` 环境下，`FinGenius upstream` 的真实工具命中率和失败表现

执行方式：

- 使用 `scripts/run_shortline_hub.py --mode process`
- 指向真实：
  - `D:\bb\WonderTrader\bridge\wt_export_candidates.py`
  - `D:\bb\FinGenius\bridge\fg_explain_candidate.py`
- 推荐 `FinGenius` 解释器：
  - `D:\bb\FinGenius\.venv311\Scripts\python.exe`
- 输出到新的 `data/manual_runs/<timestamp_or_named_dir>/`

本轮 smoke 至少记录：

- 请求日期
- `top_n`
- `scan_source_counts`
- `explanation_source_counts`
- `upstream_tool_hit_counts`
- `legacy_unknown_count`
- `tool_error_count`
- `total_explain_elapsed_ms`
- `upstream_tool_elapsed_totals_ms`
- `orchestrator_explain_elapsed_ms`
- 每只票的来源、工具命中和耗时

如果真实 smoke 暴露外部脚本与仓库模板协议不一致，则允许做最小同步修复，但范围仅限：

- 外部 bridge 输出字段与仓库模板保持一致
- 不扩散到无关模块

## 7. 失败处理

必须保持 fail-open。

规则：

- 单个 upstream 工具失败，不中断单票 explanation
- 单票 explanation 若仍能返回 heuristic fallback，不中断整轮批量
- 单票元数据缺失，不中断运行，只回填默认值
- 报告和 `run_summary.json` 只反映事实，不把失败包装成成功

默认值约定：

- `protocol_version` 缺失时为 `""`
- `explanation_source` 缺失时视为 `legacy_unknown`
- `used_upstream_tools` 缺失时为 `[]`
- `tool_error_count` 缺失时为 `0`
- `tool_errors` 缺失时为 `[]`
- `explain_elapsed_ms` 缺失时为 `0`
- `upstream_tool_elapsed_ms` 缺失时为 `{}`

## 8. 测试设计

本次必须先补测试，再实现。

### 8.1 Bridge 模板测试

更新 `tests/test_shortline_bridge_templates.py`，覆盖：

- 新 bridge 输出包含 `protocol_version = "shortline_fg_v1"`
- 命中 `bridge_data` 时返回 `explanation_source = "bridge_data"`
- 命中真实 upstream 工具时返回 `explanation_source = "upstream_tools"`
- 三个工具全部失败时返回 `explanation_source = "heuristic_fallback"`
- 当部分工具失败、部分成功时：
  - 来源仍是 `upstream_tools`
  - `used_upstream_tools` 只含成功工具
  - `tool_error_count` 大于 `0`
  - `tool_errors` 记录失败摘要
- 轻量模式下 `BigDealAnalysisTool` 未启用时，`big_deal_summary` 仍可生成代理摘要

### 8.2 Adapter / Orchestrator 测试

更新 `tests/test_shortline_hub_orchestrator.py`，覆盖：

- `FinGeniusProcessAdapter` 能读取新增字段
- 缺少新增字段时能兼容旧输出，并把来源回填为 `legacy_unknown`
- `combined_results` 透传来源、耗时和分工具耗时
- `run_summary.json` 聚合统计正确

### 8.3 CLI / Artifact 测试

更新 `tests/test_shortline_hub_cli.py`，覆盖：

- `run_summary.json` 包含新增统计字段
- `shortline_report.md` 包含 `FinGenius Explain Summary`
- `shortline_combined_results.json` 包含新增元数据字段

### 8.4 真实 smoke 验证

本地代码测试通过后，再执行一次真实 `process mode` smoke。

这一步不是单元测试替代品，而是额外证据。

## 9. 文档与留痕

本次属于用户可见报告结构和运行产物变化，实施时需要同步更新：

- `scripts/bridges/README.md`
- `docs/AI_MODIFICATION_LOG.md`
- `docs/CHANGELOG.md`

如真实 smoke 产出有代表性目录，可在交付说明中引用，但不要求把运行产物文档化到 README。

## 10. 风险

首版风险主要有：

1. 外部 `FinGenius` 真实脚本尚未同步新增元数据字段
2. 默认轻量模式下 `BigDealAnalysisTool` 不参与真实调用，若用户误以为是全量链路，容易误读结果
3. `FinGenius` 外部环境与当前仓库环境解耦后，Python 版本和依赖漂移仍可能导致真实工具命中率不稳定
4. 报告字段增加后，旧测试断言可能需要同步调整
5. 部分“真实工具命中但字段不完整”的边界情况，来源判定需要保持一致，不可随文案变化漂移

对应策略：

- adapter 层默认兼容旧格式
- 旧格式一律显式标记为 `legacy_unknown`，避免把“未知”误判成“heuristic_fallback”
- 先做测试与结构化字段，再跑 smoke
- 用枚举字段判断来源，不依赖说明文案文本

## 11. 跨工程演进约束

当前短线链路横跨两个独立工程：

- 当前仓库 `daily_stock_analysis`
- 外部 `FinGenius`

现阶段继续使用 `process bridge + JSON 协议` 是合理的，因为这能保持两个工程的代码边界清晰，避免直接源码级耦合；但后续如果两个工程都持续演进，真正的风险会从“能不能跑”转向“协议和环境是否持续同步”。

因此，本线后续演进必须遵守以下约束。

### 11.1 把 bridge 协议视为正式边界

`shortline_hub` 与外部 `FinGenius` 的交互，不应再被视为一次性的临时脚本约定，而应被视为正式、可演进、可回归验证的边界协议。

后续凡是涉及以下内容的变更，都应视为协议变更：

- bridge 输入字段
- bridge 输出字段
- 枚举值
- 默认值
- 缺失字段兼容语义
- 错误字段与失败降级语义

### 11.2 单一事实源

协议真相不能同时散落在模板脚本、真实脚本、adapter 推断逻辑和文档口述里。

后续维护时应以仓库内 bridge 模板和对应 contract tests 作为协议的单一事实源：

- `scripts/bridges/shortline_fingenius_bridge_template.py`
- `tests/test_shortline_bridge_templates.py`
- `tests/test_shortline_hub_orchestrator.py`
- `tests/test_shortline_hub_cli.py`

文档用于解释，不用于定义事实。

### 11.3 模板优先，真实脚本跟随

当前形态下，仓库内 template 与外部真实脚本存在双维护问题，这是可以接受的阶段性状态，但不能长期放任漂移。

后续原则：

- 协议新增或调整时，先修改仓库模板与测试
- 再同步外部真实脚本
- 最后运行真实 `process mode` smoke 验证联调结果

禁止只改外部真实脚本而不回写仓库模板与测试；否则仓库内模板会失去“蓝本”地位。

### 11.4 版本化演进

后续 bridge 协议应开始引入轻量版本字段，例如：

- `protocol_version`

首版不一定要立即强制启用版本门控，但后续如继续增加来源字段、统计字段或失败语义，必须预留版本化能力，避免两边在“看起来都能跑”的情况下悄悄产生兼容歧义。

### 11.5 向后兼容优先

在两个工程并行演进期间，新增字段必须优先采用“追加字段、保留旧字段、旧脚本可继续运行”的方式。

特别是以下场景要避免：

- 直接删除旧字段
- 直接修改字段含义但不改字段名
- 复用旧字段承载新语义
- 让 adapter 只能识别新格式而无法兼容旧格式

如果必须引入不兼容变更，应显式提升协议版本并同步更新 contract tests 和真实 smoke 步骤。

### 11.6 环境漂移要单独看待

协议兼容不代表运行环境兼容。

即使 JSON 协议不变，只要以下任一项变化，也可能导致真实联调失败：

- Python 主版本
- `FinGenius upstream` 依赖
- `loguru`、异步工具依赖、数据抓取依赖
- 外部 bridge 所在工程目录结构

因此，环境升级不能只看本仓库测试通过，还必须看真实外部脚本是否仍可在目标环境下执行。

### 11.7 每次协议改动都要有真实联调证据

单元测试只能证明仓库内推断正确，不能证明跨工程联调仍然成立。

后续只要发生以下情况之一，就应追加至少一次真实 `process mode` smoke：

- bridge 输入输出字段变化
- 新增枚举值或统计字段
- 外部真实脚本同步修改
- `FinGenius upstream` 升级
- Python 解释器或依赖环境变化

真实 smoke 的目标不是覆盖所有行情场景，而是证明当前两个工程在当前机器环境下仍能成功对接。

## 12. 实施结论

本次应被定义为：

- “短线 `FinGenius` 批量解释的结构化留痕增强 + 真实批量验证”

而不是：

- “重做一套批量解释主流程”

最终实施建议：

- 先在现有协议内补结构化来源与耗时字段
- 先让主运行产物可直接回答“哪些票命中真实工具”
- 再跑一轮真实 `process mode` 批量 smoke
- 基于 smoke 结果决定后续是否需要单独 `FinGenius` Python 环境或更进一步的批量优化
