# 短线线交接记录

最后更新：2026-05-02

## 1. 当前目标

这条线当前目标不是做完整量化交易底座，而是把当前工程 `daily_stock_analysis` 变成一个可编排的短线研究壳：

- `WonderTrader` 负责候选筛选
- `FinGenius` 负责个股解释
- 当前工程负责：
  - 编排
  - 聚合
  - 报告
  - 留痕
  - 后续批量化

用户当前偏好已经明确：

- 先把单票真实解释跑通
- 后面再做批量分析

## 2. 当前真实状态

### WonderTrader

已经不是纯 stub。

当前已打通：

- 外部 process mode 调用
- 仓库内真实 `WonderTrader` 候选辅助链路
- 真引擎优先 / spot cache 回退 / placeholder 兜底
- 候选结果透传到 `shortline_hub`

当前结论：

- `WonderTrader` 这边已经进入“真实可用”阶段
- 日常单机复盘可用
- 后续更多是策略效果和批量流程优化，不是接线级别问题

### FinGenius

这次已经从“启发式兜底”升级到“真实单票工具优先”。

当前桥接顺序：

1. `bridge_data`
2. `FinGenius upstream` 单票工具
   - `HotMoneyTool`
   - `ChipAnalysisTool`
   - `BigDealAnalysisTool`
3. 启发式解释回退

当前结论：

- 不是完整 `FinGenius` multi-agent / LLM research 模式
- 但已经不是 placeholder-only
- 单票解释已经能优先吃到真实资金/筹码/大单工具输出
- 这已经足够作为后续批量化的基础层

## 3. 这次刚做完的事

本轮刚完成的是 `FinGenius` 单票真实桥接收口。

已完成：

- 仓库模板桥接脚本升级：
  - [scripts/bridges/shortline_fingenius_bridge_template.py](/d:/bb/daily_stock_analysis/scripts/bridges/shortline_fingenius_bridge_template.py)
- 外部真实桥接脚本同步升级：
  - `D:\bb\FinGenius\bridge\fg_explain_candidate.py`
- 修复两个同机真实问题：
  - 兼容 UTF-8 BOM 请求文件
  - 缺少 `loguru` 时自动注入轻量兼容层
- 保留启发式回退，避免 upstream 工具失败时整条链路中断

## 4. 关键验证结论

已验证通过：

- `python -m pytest tests/test_fingenius_bridge_loguru_fallback.py tests/test_fingenius_bridge_bom.py tests/test_shortline_bridge_templates.py tests/test_shortline_hub_orchestrator.py tests/test_shortline_hub_cli.py tests/test_shortline_bridge_check.py tests/test_shortline_bridge_data_compare.py -q`
  - 结果：`27 passed`

- 外部真实脚本单票 smoke 已跑通：
  - `python D:\bb\FinGenius\bridge\fg_explain_candidate.py <request_json> <output_json>`

实跑观察：

- 现在外部桥接脚本已经能成功执行
- 输出不再只是旧的 placeholder 文本
- 在当前机器环境下，已经能命中真实工具层结果

## 5. 当前机器限制

当前机器仍有一个明确限制：

- 本机 Python 主环境是 `3.10`
- `FinGenius upstream` 自己更推荐 `3.11-3.13`

这意味着：

- 现在这版是“实用可用版”
- 不代表 upstream 全能力都稳定
- 但已经够支撑“单票真实解释 + 后续批量编排”

## 6. 如果你切账号后要先看哪里

优先看这几个文件：

1. 本次改动主入口：
   - [scripts/bridges/shortline_fingenius_bridge_template.py](/d:/bb/daily_stock_analysis/scripts/bridges/shortline_fingenius_bridge_template.py)
   - `D:\bb\FinGenius\bridge\fg_explain_candidate.py`

2. 本次测试：
   - [tests/test_shortline_bridge_templates.py](/d:/bb/daily_stock_analysis/tests/test_shortline_bridge_templates.py)
   - [tests/test_fingenius_bridge_bom.py](/d:/bb/daily_stock_analysis/tests/test_fingenius_bridge_bom.py)
   - [tests/test_fingenius_bridge_loguru_fallback.py](/d:/bb/daily_stock_analysis/tests/test_fingenius_bridge_loguru_fallback.py)

3. 留痕：
   - [docs/AI_MODIFICATION_LOG.md](/d:/bb/daily_stock_analysis/docs/AI_MODIFICATION_LOG.md)
   - [docs/CHANGELOG.md](/d:/bb/daily_stock_analysis/docs/CHANGELOG.md)
   - [scripts/bridges/README.md](/d:/bb/daily_stock_analysis/scripts/bridges/README.md)

4. 短线编排入口：
   - [scripts/run_shortline_hub.py](/d:/bb/daily_stock_analysis/scripts/run_shortline_hub.py)
   - [src/shortline_hub](/d:/bb/daily_stock_analysis/src/shortline_hub)

## 7. 建议的继续顺序

如果下个账号接着做，建议顺序如下：

1. 不再折腾单票桥接，默认认为它已可用
2. 直接做“批量候选逐票调用 `FinGenius` 单票真实解释桥”
3. 再补批量运行统计：
   - 成功数
   - 回退数
   - 耗时
   - 哪些票触发真实工具，哪些票触发回退
4. 最后再考虑是否给 `FinGenius` 单独准备更完整的 Python 环境

## 8. 一句话结论

当前短线线最重要的状态是：

- `WonderTrader` 候选已基本真实可用
- `FinGenius` 单票解释已从启发式阶段进入“真实工具优先”阶段
- 下一步不该再反复接线，而该开始做批量化
