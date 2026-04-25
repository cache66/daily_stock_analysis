# `trend_leader` 热路径前筛按需 hydration 优化设计
日期：2026-04-23  
状态：设计已确认，待用户审阅书面 spec  
适用范围：A 股 `trend_leader_unified` 本地扫描与快复盘入口  
设计语言：中文

## 1. 背景

针对 `2026-04-22` 的“保留持久化全套快复盘”热跑日志，当前最重的信号仍然是 `trend_leader`。其中：

- `signal_trend_leader_elapsed_sec=961.30`
- 准备阶段 `total_prep_elapsed_sec=527.67`
- 主扫描阶段约 `430s`

总日志进一步显示，前筛阶段存在一次明显的全市场逐票 hydration：

- `quote_hydrated_rows=5198`
- `quote_requested_rows=5198`
- `removed_change_60d=0`

这说明当前前筛很可能为了补齐缺失字段，对几乎全市场都执行了 `get_realtime_quote()`，但最终并没有依靠 `change_pct_60d` 实际筛掉股票，存在高概率“网络成本很高、收益很低”的情况。

相关实现主要位于：

- `scripts/select_trend_leader_candidates.py`
- `src/services/kline_selector_service.py`

## 2. 目标

本轮优化目标是只处理 `trend_leader` 热路径里最可疑、最保守的一段性能瓶颈，同时不降低结果质量：

- 降低 `trend_leader` 准备阶段的无效 hydration 成本
- 保持 `trend_leader` CLI 参数、输出结构、持久化语义不变
- 保持已有前筛 fail-open 思路
- 不通过“简单收紧阈值”来换速度

预期收益：

- 明显减少不必要的逐票 `realtime_quote` 请求
- 缩短 `trend_leader` preparation/prefilter 耗时
- 在热跑场景下保持候选质量与结果口径基本稳定

## 3. 非目标

本轮明确不做：

- 不修改 `trend_leader` 的评分模型、深扫规则或输出字段
- 不调整现有默认前筛阈值
- 不引入新的数据库缓存语义
- 不顺手修改 `hundred_day_high`、`continuous_up`、`earnings`
- 不新增新的外部数据源

## 4. 方案对比

### 4.1 方案 A：前筛 hydration 改为字段级按需执行，推荐

核心思路：

- 不再以“前筛需要某列”作为整轮 hydration 的触发条件
- 改成先判断“哪些缺失字段值得补、且 quote 真有机会补出来”
- 只在必要时做 hydration，并且只统计、填充目标字段

优点：

- 改动面最小
- 与现有前筛逻辑最兼容
- 最不容易伤结果质量

缺点：

- 需要把当前的 hydration 判定从布尔值升级为更细的字段集合

### 4.2 方案 B：为前筛引入独立快照/缓存

优点：

- 理论性能空间更大

缺点：

- 需要定义缓存来源、过期策略和一致性
- 会改动持久化语义，超出本轮最小优化范围

### 4.3 方案 C：直接收紧前筛阈值

优点：

- 实现最快

缺点：

- 最容易伤召回和输出质量
- 不能证明是“去掉无效成本”，只是“更早丢候选”

### 4.4 结论

采用方案 A。

## 5. 设计

### 5.1 当前问题

当前链路里，`_needs_scan_prefilter_quote_hydration(...)` 只要发现前筛依赖列缺失，就会触发整轮 `_hydrate_scan_prefilter_quote_fields(...)`。  
这会导致两个问题：

- 某些字段即使缺失，`realtime_quote` 也未必能补出来，例如 `change_pct_60d`
- 当阈值组合要求多列时，容易退化成“为补一列未知字段，对全市场打一轮 quote”

### 5.2 新行为

前筛 hydration 改为“字段级、按需、可短路”：

- 优先使用 spot universe 已经提供的列
- 仅当某个过滤条件依赖的字段缺失，且 `realtime_quote` 有机会补这个字段时，才触发 hydration
- 对 `realtime_quote` 明显补不出来或历史上极不稳定的字段，不因为它缺失而触发全市场 hydration
- 保持 fail-open：如果字段不可得，不应因为优化而错误剔除候选

### 5.3 实现点

主要改动集中在 `scripts/select_trend_leader_candidates.py`：

1. 新增一个“待补字段解析”层
   - 将现有 `_needs_scan_prefilter_quote_hydration(...)` 从返回布尔值改为返回字段集合或等价结构
   - 该结构明确区分：
     - 哪些字段缺失
     - 哪些字段 quote 可补
     - 哪些字段应继续 fail-open

2. 收紧 `_hydrate_scan_prefilter_quote_fields(...)`
   - 支持按目标字段集合执行
   - 只统计和填充本轮真正需要的字段
   - 保留现有兼容字段映射，例如 `change_pct -> pct_change`

3. 调整 `_prepare_scan_prefilter_universe(...)`
   - 使用新的字段级 hydration 判定
   - 日志中输出更可诊断的 hydration 统计

### 5.4 日志与可观测性

保留现有 `trend leader quote prefilter` 日志框架，并增强以下信息：

- 本轮请求了哪些字段
- 实际填充了哪些字段
- 哪些条件因为字段不可补而直接 fail-open

目标是让下一次热跑可以快速判断：

- hydration 是否还在全市场触发
- 它到底为了什么字段触发
- 这些字段是否真产生了筛选价值

## 6. 测试策略

按 TDD 执行，先写失败用例，再写实现。

至少补以下测试：

1. 当 `change_pct_60d` 缺失，但 `quote` 无法提供该字段时
   - 不应因此对全市场逐票 hydration

2. 当 `pct_change` / `turnover_rate` 缺失且 `quote` 可以提供时
   - 仍应执行按需 hydration
   - 前筛结果与统计应符合预期

3. 已有 spot universe 已经带齐前筛字段时
   - 不应额外触发 hydration

## 7. 验收

本轮至少做以下验证：

- `python -m py_compile scripts/select_trend_leader_candidates.py tests/test_trend_leader_signal_flow.py`
- `python -m pytest tests/test_trend_leader_signal_flow.py tests/test_fast_review_daily_bundle.py -q`

如果实现完成后继续跑热路径验收，优先观察：

- `trend leader preparation timing`
- `trend leader quote prefilter`
- `signal_trend_leader_elapsed_sec`

成功标准：

- `quote_requested_rows` 明显下降，或至少不再因为不可补字段而对全市场触发
- `trend_leader` 结果数量与输出结构未发生明显异常

## 8. 风险与回滚

主要风险：

- 对“quote 可补字段”的判断过于激进，导致本来能补的信息没补到
- 统计逻辑变复杂后，日志解释与真实行为不一致

回滚方式：

- 仅回滚 `scripts/select_trend_leader_candidates.py` 本轮按需 hydration 逻辑
- 恢复为当前整轮 hydration 行为
