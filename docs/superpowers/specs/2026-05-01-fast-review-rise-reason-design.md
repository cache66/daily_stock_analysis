# 快复盘强势股上涨原因标签/摘要设计

## 1. 背景

当前每日快复盘已经能聚合多个主策略结果，并输出：

- `fast_review_summary.md`
- `fast_review_strategy_focus.csv`
- `fast_review_strategy_focus.md`

但对于复盘里已经明显走强的股票，当前输出更偏“选中原因/排序原因”，缺少“上涨原因解释层”。用户希望在每日复盘中，直接看到这类强势股可能为什么上涨，例如：

- 行业爆发
- 业绩释放
- 涨价传导
- 政策催化
- 海外主题映射
- 重组或题材驱动

同时用户要求优先复用现有接口和字段，不新增一套平行的“原因分析策略”。

## 2. 现状与可复用能力

仓库里已经存在可复用的上涨原因分析能力：

- `src/services/signal_cause_analysis_service.py`
  - 已能输出 `reason_summary`
  - 已能输出 `cause_tags`
  - 已能输出 `industry_logic / news_logic / technical_logic`
  - 已能输出 `evidence_points / fact_vs_inference`
- `src/services/signal_snapshot_service.py`
  - 已在读取层兼容 `cause.reason_summary`
  - `strategy_summary` 已可回退到 `cause.reason_summary`
- `scripts/run_fast_review_bundle.py`
  - 已是每日快复盘的统一聚合入口
  - 已生成 `strategy_focus` 焦点层，适合作为首版接入面

结论：

- 不需要新建独立策略
- 不需要首版就改造全量 snapshot 持久化链路
- 最合适的首版是：在每日复盘展示层复用既有原因分析能力

## 3. 目标

首版目标：

1. 在每日复盘里，为强势焦点股自动补充“上涨原因标签/摘要”
2. 字段结构直接复用现有 `reason_summary / cause_tags` 契约
3. 只处理少量复盘焦点股，不扩大到全市场全量扫描
4. 原因分析失败时不影响快复盘主流程

## 4. 非目标

本次不做：

- 不新增独立“上涨原因分析策略”
- 不改造成全量股票统一归因任务
- 不在首版写回 snapshot 持久化
- 不修改 `/signals` API 契约
- 不把新闻抓取范围扩大到所有复盘候选
- 不引入新的数据库表或新的缓存体系

## 5. 方案比较

### 方案 A：仅在复盘展示层补原因

做法：

- 仅在 `run_fast_review_bundle.py` 中处理 `strategy_focus_rows`
- 对缺少原因字段的焦点股，调用 `SignalCauseAnalysisService`
- 输出到复盘 CSV / Markdown / Summary

优点：

- 改动面最小
- 风险最低
- 直接满足复盘场景
- 与现有字段契约兼容

缺点：

- 首版结果主要停留在复盘产物，尚未沉淀到 snapshot

### 方案 B：先写回 snapshot，再由复盘读取

做法：

- 在各信号落库链路里统一补齐 `cause_payload`
- 复盘只读已有原因字段

优点：

- 复用最彻底
- 后续 `/signals`、页面和其它报表都能直接共享

缺点：

- 改动会扩散到多个策略脚本、落库和读层
- 首版风险更大，验证成本更高

### 方案 C：新增独立原因分析策略

做法：

- 单独跑一套“强势股上涨原因分析”流程

缺点：

- 与现有 `signal_cause_analysis_service` 职责重叠
- 字段和入口容易分叉
- 后续维护成本最高

### 推荐

推荐先落地方案 A。

原因：

- 用户当前核心诉求是“每日复盘里自动打上涨原因标签/摘要”
- 仓库已有成熟的原因分析服务和字段契约
- 先在复盘层接入，能最快验证可用性和性能
- 若后续确认稳定，再把同样字段写回 snapshot，演进路径清晰

## 6. 首版设计

### 6.1 触发范围

只处理每日快复盘里的焦点股，不处理全市场。

首版范围：

- `strategy_focus_rows`
- 即 `core / watch / low_priority` 三层焦点股

不处理：

- 全量 `fast_review_candidates.csv`
- 所有 resonance 行
- 未进入焦点层的普通候选

### 6.2 数据来源优先级

对每只焦点股，原因字段按以下优先级生成：

1. 直接复用已有行上的原因字段
   - `reason_summary`
   - `cause_tags`
   - `industry_logic`
   - `news_logic`
   - `technical_logic`
2. 若当前行缺失，则尝试从同组信号行里读取已有摘要
   - 例如趋势、百日新高、业绩腿已有摘要
3. 若仍缺失，再调用 `SignalCauseAnalysisService.analyze_signal(...)` 补算

这样可以最大限度复用已有结果，减少重复计算。

### 6.3 输出字段契约

首版在 `strategy_focus_rows` 上新增或补全以下字段：

- `reason_summary`
- `cause_tags`
- `industry_logic`
- `news_logic`
- `technical_logic`

字段约定：

- `reason_summary`
  - 面向复盘阅读的一句话上涨原因摘要
- `cause_tags`
  - 复用现有 tag 值
  - 首版内部仍使用原始英文枚举，如 `earnings`、`policy`、`sector_rotation`
- `industry_logic / news_logic / technical_logic`
  - 保留结构化解释层，首版可先只在 CSV 中完整输出

展示层可以增加一个轻量中文映射，例如：

- `earnings` -> `业绩`
- `policy` -> `政策`
- `price_increase` -> `涨价`
- `supply_demand` -> `供需`
- `sector_rotation` -> `板块轮动`
- `overseas_theme` -> `海外映射`
- `other` -> `其他`

### 6.4 输出位置

#### `fast_review_strategy_focus.csv`

新增列：

- `reason_summary`
- `cause_tags`
- `industry_logic`
- `news_logic`
- `technical_logic`

用途：

- 作为结构化复盘导出主表
- 便于后续做筛选、排序和人工复盘

#### `fast_review_strategy_focus.md`

在焦点表格中增加精简展示：

- `上涨原因`
- `标签`

原则：

- `上涨原因` 展示 `reason_summary`
- `标签` 展示中文映射后的简短标签串
- 避免在 markdown 表格里塞入过长结构化文本

#### `fast_review_summary.md`

新增一个简洁小节，例如：

- `强势股上涨原因摘要`

内容形态：

- 只列 `core` 和部分 `watch`
- 每行展示：
  - `code`
  - `name`
  - `signals`
  - `上涨原因摘要`
  - `标签`

目标是让每日复盘先能快速扫一眼“强势股为何涨”。

## 7. 代码落点

首版只改动以下链路：

- `scripts/run_fast_review_bundle.py`
  - 在 `strategy_focus_rows` 构建阶段补充原因字段
  - 在 CSV / Markdown / Summary 输出阶段展示
- 复用 `src/services/signal_cause_analysis_service.py`

首版不改：

- snapshot 持久化逻辑
- `src/services/signal_snapshot_service.py`
- `/signals` API

## 8. 数据流

### 输入

- 已有 `signal_results`
- `trend_watch_rows`
- 焦点层分组结果 `grouped / rows_by_signal`

### 处理流程

1. `run_fast_review_bundle.py` 构建 `strategy_focus_rows`
2. 对每个焦点股检查是否已有 `reason_summary / cause_tags`
3. 若已有，则直接复用
4. 若缺失，则基于优先信号行构造：
   - `stock_code`
   - `stock_name`
   - `signal_type`
   - `metrics_payload`
5. 调用 `SignalCauseAnalysisService.analyze_signal(...)`
6. 将返回字段合并进 `strategy_focus_rows`
7. 写出 CSV / Markdown / Summary

### 信号优先级

在同一只股票命中多个信号时，补算原因所用的主信号按以下优先级选择：

1. `trend_leader`
2. `hundred_day_high`
3. `earnings`
4. 其它复盘信号

原因：

- `trend_leader` 和 `hundred_day_high` 更贴近“强势上涨”场景
- `earnings` 可以作为重要补充，但不一定是所有强势股的主触发

## 9. 失败处理

必须保持 fail-open。

规则：

- 原因分析失败不能中断快复盘主流程
- 若补算失败：
  - `reason_summary` 回退为空或现有 `focus_reason`
  - `cause_tags` 回退为空或 `other`
- 日志记录 warning，但不 raise

这样可以保证“解释层增强”不会反向拖垮每日复盘。

## 10. 性能约束

首版性能约束如下：

- 只对焦点股补算，不对全量候选补算
- 优先复用已有 `reason_summary`
- 只在缺字段时触发 `SignalCauseAnalysisService`
- 默认不额外扩大新闻检索范围

预期结果：

- 对每日复盘总时长影响可控
- 不改变主扫描链路的复杂度级别

如果后续发现 `analyze_signal(...)` 仍偏重，可追加两类优化：

1. 焦点股数量上限控制
2. `enable_news_search` 或 LLM 压缩开关的复盘专用降级模式

这两项不属于首版必做项。

## 11. 测试设计

至少补以下验证：

1. 单元测试
   - 当焦点行已有 `reason_summary` 时，应直接复用，不重复补算
   - 当焦点行缺失原因字段时，应触发补算并合并结果
   - 当补算失败时，复盘仍可正常输出
2. 导出测试
   - `fast_review_strategy_focus.csv` 新增列存在
   - `fast_review_strategy_focus.md` 能展示“上涨原因/标签”
   - `fast_review_summary.md` 新增原因摘要小节
3. 回归测试
   - 不破坏现有焦点排序
   - 不破坏现有 watchlist-only 接入逻辑

## 12. 文档与留痕

若后续进入实现并改动本地策略资产，需要同步更新：

- `docs/LOCAL_STRATEGY_CATALOG.md`
- `docs/LOCAL_STRATEGY_BASELINE.md`
- `docs/AI_MODIFICATION_LOG.md`
- `docs/CHANGELOG.md`

本 spec 本身只描述设计，不视为功能实现完成。

## 13. 后续演进

若首版验证稳定，第二阶段可以考虑：

1. 把同样的原因字段写回 snapshot
2. 让 `/signals` 和复盘共用同一份原因结果
3. 再评估是否给原因标签做更细中文分层或置信度展示

当前不提前做，以避免范围扩散。

## 14. 实施结论

本次功能应被定义为：

- “每日复盘解释层增强”

而不是：

- “新增一个独立策略”

最终实施建议：

- 先在 `run_fast_review_bundle.py` 中为焦点股补 `reason_summary / cause_tags`
- 先把能力落到复盘 CSV / Markdown / Summary
- 验证每日复盘可用、性能可接受后，再决定是否推进 snapshot 级复用
