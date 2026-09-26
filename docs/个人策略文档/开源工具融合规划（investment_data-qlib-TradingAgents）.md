# 开源工具融合规划（investment_data / qlib / TradingAgents）

状态：草案 v1（2026-09-25 起草，2026-09-26 更新现状标注）；定位：**A 股为主**，只借能力，不做第二套系统；每阶段必须有明确产出与验收。

## 0. 分工总览

| 工具 | 借什么 | 不借什么 |
| --- | --- | --- |
| [chenditc/investment_data](https://github.com/chenditc/investment_data) | A 股全市场历史数据（日线 + 基本面，多源合并校验，每日 release / dolt 库） | 不做运行时数据源（运行时仍走 `DataFetcherManager`） |
| [microsoft/qlib](https://github.com/microsoft/qlib) | 因子研究标准流程：数据集、Alpha 表达式、IC / 分层回测 | 不引入其模型做交易、不替换现有选股与流水线 |
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) | **方法论**：决策日志 → 结果回看 → 反思注入；多角色投研组织方式参考 | 不集成其代码（依赖重、美股向、A 股靠 Yahoo 数据质量不足） |

## 1. investment_data（P0：数据底座）

**用途**
1. 回测 / 研究的历史数据底座：替代逐票 Baostock 拉取，让股息线回测更快、更深、可全市场；
2. 为 qlib 准备数据（qlib 官方示例即使用该数据集）。

**落地方式（建议）**
- 目录：`data/external/investment_data/`（`/data/` 已在 .gitignore，不入库）；
- 下载：`wget https://github.com/chenditc/investment_data/releases/latest/download/qlib_bin.tar.gz`（同一 release 取 `qlib_bin.manifest.json` 校验）；
- 已提供薄脚本 `scripts/sync_investment_data.py`：`--check` 先看 release 信息/体积，`--download` 下载 + 校验（清单/sha256）+ 解压，默认不覆盖已有数据；⚠️ 需要能直连 GitHub Release（2026-09-25 本机直连超时，可挂代理/镜像或手动下载归档后 `--extract-only`）；
- 使用：回测脚本（如 `backtest_dividend_income.py`）改为优先读本地数据集，Baostock 仅作缺口兜底。

**验收**：抽 3 只股息池股票（如 宁沪高速 / 长江电力 / 紫金矿业）的 2024–2026 行情与现有缓存对比，复权口径差异 < 0.5%。

**风险**：多源合并口径（wind/caihui/tushare/akshare/baostock）；个别历史字段需抽查；**不要**把它当实时源使用。

**状态（更新至 2026-09-26）**：
- GitHub Release 本机直连超时 → 先落地**等价路径**：`scripts/backfill_history_cache.py` 用 BaoStock 全市场回填共享缓存 `data/cache/history/`（400 天、无配额、合并去重）；**第一轮全市场回填已完成**（回填 + 补漏两轮后覆盖 5,116/5,245，缺口=97 新股 + 32 停牌；2026-09-26 00:37 末轮复检失败 0；缓存现有 5,248 个标的文件）；选股产线优先读本地；investment_data 下载脚本（`sync_investment_data.py`）已备好，等网络可达或手动归档后再切换/对拍；
- 网络恢复后按原验收执行：抽 3 只股息池股票对比 2024–2026 行情，复权口径差异 < 0.5% 再替换为首选数据源。

## 2. qlib（P1：因子检验，依赖 P0）

**Phase 1 打通（半天~1 天）**
- 独立 venv（避免污染 `.venv-linux`）：`python -m venv .venv-qlib && pip install pyqlib`；
- 用 investment_data 的 qlib_bin 跑通 `qrun` LightGBM Alpha158 示例（离线）。

**Phase 2 对齐个人线（1~2 天）**
- 把股息线因子（`dv_ttm`、波动率、最大回撤、ROE 均值、payout、近一年含息回报…）写成 qlib 表达式 / 数据集；
- 输出 IC / 分层曲线，回答"哪些因子真有区分度"（现状：评分排序 RankIC≈0，Top20 跑输池子等权）；
- 与 `backtest_dividend_income.py` 的结论**互验**（两套独立实现得出一致结论才可信）。

**Phase 3 可选**：qlib 排序输出与池子等权组合对比，作为组合构造参考；**不接入实盘**。

**目录**：`research/qlib/`（脚本 + notebook，独立于主链路）。

## 3. TradingAgents（P2：方法论，不集成代码）

**只借三件套**
1. 决策日志：每次看中的票 + 理由 + 预期持有窗口；
2. 结果回看：N 日后计算 realized return / alpha；
3. 反思注入：把最近的经验写回下一次决策上下文。

**落地情况（更新至 2026-09-26）**
1. ✅ 日志表已建：`data/decision_log/decisions.csv`（列：decision_date / code / name / source / reason / expected_window_days；当前为空表）；
2. ✅ 回看脚本已建：`scripts/review_decision_log.py`（入场=决策日收盘、出场=第 k 个交易日收盘（k=预期窗口，默认 5）、±2% 中性带；支持 `--benchmark-code`（如 000905）计算同窗超额，输出列与统一评估器口径对齐）；
3. ⏳ 验收（连续 20 条决策日志 + 一次回看报告）未启动：待日志积累后跑回看；
4. 若将来要跑其框架本体：独立 venv，仅美股 / 港股研究用途（A 股数据质量不足）。

## 4. 时间线建议

| 阶段 | 动作 | 触发条件 |
| --- | --- | --- |
| 现在 | 主线收敛中（见《策略收敛与回测路线图》）；短线圈 / 催化聚合已产品化 | — |
| 下阶段 | P0 investment_data 正式数据源 | 网络可达后按验收对拍（等价路径第一轮回填已完成） |
| 之后 | P1 qlib 因子检验 | P0 完成后，且需要回答"因子是否有增量" |
| 随时 | P2 决策日志（工具已就绪，2026-09-26） | 开始记第一条日志即可（积累 ≥20 条后回看） |

## 5. 明确不做

- 不引入 backtrader / vectorbt / vnpy（避免第三套回测 / 交易体系）；
- 不用 TradingAgents 代码替换 DSA 分析链路；
- investment_data 不做运行时在线数据源，只做研究 / 回测底座。
