# `earnings_surprise` 三档扫描深度与快扫优先设计

日期：2026-04-21  
状态：已完成设计讨论，待用户审阅  
适用范围：A 股 `earnings_surprise` 本地扫描与快复盘入口  
设计语言：中文

## 1. 背景

当前仓库中的 `earnings` 默认对应本地策略 `earnings_surprise`，主入口为：

- `scripts/select_earnings_surprise_candidates.py`
- `scripts/run_fast_review_bundle.py`

这条策略的价值很明确：它不是单纯看财务增速，而是尝试识别“业绩公告后、基本面质量和后续兑现概率更高”的股票。

但当前实现存在一个现实问题：

- 在日常快复盘场景中，`earnings` 常常成为最慢的一条策略
- 一旦它串行跑得过久，会拖慢整个 `run_fast_review_bundle.py`
- 从已有实现看，主要慢点不是最终算分，而是每只股票的基础面抓取链路过重

当前每只股票的扫描链路大致是：

1. 获取 A 股候选池
2. 读取最近业绩事件目录
3. 对每只股票执行 `load_or_fetch_signal_fundamental_snapshot(...)`
4. 若当天无缓存，则调用 `AkshareFundamentalAdapter.get_fundamental_bundle(...)`
5. `get_fundamental_bundle(...)` 会抓取多块内容：
   - 财务指标
   - 业绩预告
   - 业绩快报
   - 分红明细
   - 机构持仓
   - 前十大股东/股东户数
6. 再执行 `evaluate_earnings_surprise_candidate(...)`
7. 对通过股票补 `capital_profile`
8. 写入快照、检查历史去重、导出结果

这套链路在“完整性优先”时是合理的，但在“日常快复盘”场景下明显偏重。

## 2. 目标

本设计的目标是为 `earnings_surprise` 新增一个独立的“扫描深度”配置维度，支持：

- `low`
- `medium`
- `high`

并优先实现 `low` 档作为“提速优先”的快扫模式。

第一阶段目标：

- 保持现有 `strict / balanced / relaxed` 业绩门槛体系不变
- 新增 `scan-depth` 维度来控制“抓多深、跑多重”
- 让 `run_fast_review_bundle.py` 的默认 `earnings` 子任务改用 `scan-depth=low`
- 让 `low` 档在不完全放弃业绩线价值的前提下，显著降低耗时

建议性能目标：

- `earnings` 单策略耗时下降 `40%~60%`
- 快复盘默认链路不再被 `earnings` 长时间阻塞
- 命中数量允许下降，但前部明显强票不能大面积丢失

## 3. 非目标

第一阶段明确不做以下事项：

- 不重写现有 `earnings_strategy_score` 主体评分框架
- 不推翻现有 `strict / balanced / relaxed` 档位定义
- 不修改 `trend_leader`、`hundred_day_high`、`monthly_slow_rise` 的行为
- 不引入新的外部数据源
- 不在第一阶段追求“最高命中率 + 最高完整度”
- 不直接做全链路异步任务队列改造

## 4. 已确认的用户偏好

基于本轮讨论，已确认：

- 需要把 `earnings` 做成“高 / 中 / 低”三档配置
- 第一阶段先实现“提速优先”
- 日常重点场景是快复盘，而不是完整专题研究

因此本设计采用：

- 三档扫描深度：`low / medium / high`
- 第一阶段优先落地 `low`

## 5. 备选方案比较

### 5.1 方案 A：新增 `scan-depth=low|medium|high`，推荐

保留现有：

- `--strategy-profile strict|balanced|relaxed`

新增：

- `--scan-depth low|medium|high`

语义划分为：

- `strategy-profile` 决定“门槛和评分口径”
- `scan-depth` 决定“抓多深和补多全”

优点：

- 语义清楚
- 后续扩展性最好
- 快复盘和单独运行可以共用同一策略入口

缺点：

- 需要显式拆分“首轮必需抓取”和“通过后补抓”

### 5.2 方案 B：新增 `--scan-mode fast|full`

优点：

- 改动相对小

缺点：

- 后续如果再分层，会变成 profile + mode 的交叉组合，理解成本较高

### 5.3 方案 C：只在 `run_fast_review_bundle.py` 里做轻量特判

优点：

- 对独立脚本侵入最小

缺点：

- 同一个策略会出现两套行为
- 后续维护最容易漂

### 5.4 结论

采用方案 A。

## 6. 总体设计

### 6.1 配置层

在 `scripts/select_earnings_surprise_candidates.py` 中新增：

- `--scan-depth`
- 可选值：`low`、`medium`、`high`
- 默认值：`high`

设计意图：

- 直接运行独立脚本时，默认保持现有完整行为，避免静默破坏
- 快复盘入口显式透传 `low`

在 `scripts/run_fast_review_bundle.py` 中新增：

- `--earnings-scan-depth`
- 默认值：`low`

并允许它写入 `config/local_strategy_profile.json` 作为本地默认配置项。

### 6.2 三档语义定义

#### `high`

目标：

- 尽量保留当前完整实现

行为：

- 调用完整 `get_fundamental_bundle(...)`
- 保留全部基础面块：
  - financial
  - forecast
  - quick_report
  - dividend
  - institution
  - top10
- 通过股票补 `capital_profile`
- 导出与快照字段保持当前最完整口径

用途：

- 独立专题研究
- 验证完整性
- 与旧版结果做对照

#### `medium`

目标：

- 明显减重，但保留较完整的业绩线判断

行为：

- 仅抓取核心基础面块：
  - financial
  - forecast
  - quick_report
- 跳过：
  - dividend
  - institution
  - top10
- 对通过股票仍补 `capital_profile`

用途：

- 日常研究
- 介于快扫与完整扫描之间

#### `low`

目标：

- 快扫优先，尽快给出候选

行为：

- 先基于“最近业绩事件目录 + 核心基础面块”完成首轮筛选
- 核心抓取仅保留：
  - financial
  - forecast
  - quick_report
- 默认跳过：
  - dividend
  - institution
  - top10
- 默认不对全部样本补 `capital_profile`
- 只对最终通过票补 `capital_profile`
- 若需要进一步提速，可增加可选限制：
  - 只对最终 TopN 通过票补 `capital_profile`

用途：

- 快复盘默认入口
- 当天先看强业绩候选池

## 7. `low` 档应重点关注什么

`low` 档不是“把业绩线关掉”，而是只保留最有辨识度、最直接影响结果的部分。

第一阶段保留的核心关注项：

1. 最近是否有业绩相关事件
   - 来自 `stock_yjyg_em`
   - 来自 `stock_yjkb_em`
2. 是否能拿到核心财务指标
   - `revenue_yoy`
   - `net_profit_yoy`
   - `roe`
3. 是否命中明显负向文本
4. 是否具备最基本的正向确认
   - 正向文本
   - 增长阈值
   - `earnings_quality_signal`
5. 当前 `earnings_strategy_score` 是否足以进入候选

第一阶段不作为首轮必需关注项：

1. 分红信息
2. 机构持仓变化
3. 前十大股东/股东户数变化
4. 全量资金画像补抓

原因：

- 这些信息对边缘排序有帮助，但对“先快速找出当天值得看的一批业绩票”不是第一优先级
- 它们在当前链路里属于明显的重抓取项

## 8. 数据抓取拆分设计

### 8.1 当前问题

当前 `get_fundamental_bundle(...)` 是单个统一入口，一次性抓多块基础面内容。

这导致：

- `low` 档无法自然跳过重块
- 每只股票只要 miss 缓存，就容易进入完整重抓

### 8.2 目标拆分

建议把 `AkshareFundamentalAdapter.get_fundamental_bundle(...)` 内部拆成可选块：

- `financial`
- `forecast`
- `quick_report`
- `dividend`
- `institution`
- `top10`

实现方式建议：

- 为 `get_fundamental_bundle(...)` 增加一个 `enabled_blocks` 参数
- 默认 `None` 表示现有完整行为
- `low / medium / high` 分别传不同 block 集合

这样可以在不复制策略逻辑的前提下，把抓取深度真正参数化。

### 8.3 三档对应块配置

| scan-depth | 抓取块 |
| --- | --- |
| `low` | `financial`, `forecast`, `quick_report` |
| `medium` | `financial`, `forecast`, `quick_report` |
| `high` | `financial`, `forecast`, `quick_report`, `dividend`, `institution`, `top10` |

说明：

- `medium` 和 `low` 首轮抓取块相同
- 主要区别在于：
  - `medium` 对通过票一定补 `capital_profile`
  - `low` 对通过票补 `capital_profile`，但允许进一步限制在 TopN

## 9. 首轮筛选与补抓策略

### 9.1 首轮筛选

在 `low` 档中，首轮扫描流程建议是：

1. 构建 `recent_event_catalog`
2. 遍历候选池
3. 优先读取 same-day fundamental cache
4. 若无缓存，只抓核心块
5. 完成 `evaluate_earnings_surprise_candidate(...)`
6. 未通过的股票直接结束
7. 通过股票进入候选池

### 9.2 补抓策略

对通过股票再决定是否补抓：

- `low`
  - 默认仅补 `capital_profile`
  - 可选地只补前 `N` 名
- `medium`
  - 补 `capital_profile`
- `high`
  - 保持现有完整行为

### 9.3 为什么优先补 `capital_profile`

因为当前结果排序已经依赖：

- `capital_consensus_score`
- `capital_profile_score`
- `relative_strength_score`

如果 `low` 档完全不补 `capital_profile`，会让现有排序质量大幅下降。  
因此第一阶段的最稳妥做法不是完全移除它，而是：

- 只对通过样本补
- 不对全部样本补

## 10. 快复盘入口行为

在 `scripts/run_fast_review_bundle.py` 中：

- 为 `earnings` 子任务新增 `--earnings-scan-depth`
- 默认值设为 `low`

行为变为：

- 快复盘默认跑 `balanced + low`
- 独立脚本默认跑 `balanced + high` 或显式由用户指定

这样可以同时满足：

- 快复盘先求快
- 专题研究仍保留完整性

## 11. 统计与观测

为便于验证提速是否真实，建议新增或细化以下统计：

- `recent_event_catalog_elapsed_sec`
- `fundamental_bundle_calls`
- `fundamental_bundle_elapsed_sec`
- `capital_profile_calls`
- `capital_profile_elapsed_sec`
- `history_dedupe_calls`
- `history_dedupe_elapsed_sec`
- `scan_depth`
- `enabled_blocks`

同时保留现有：

- `bundle_cache_hit_count`
- `fundamental_refresh_count`
- `quote_capital_refresh_count`
- `capital_profile_cache_hit_count`

目标是下次实跑时能直接回答：

- 时间主要花在事件目录、基础面、资金画像还是去重查询
- `low` 档到底减少了多少次重抓取

## 12. 验证方案

第一阶段至少验证以下内容：

### 12.1 正确性验证

- `high` 档默认行为不变
- `medium / low` 档能正常输出候选
- `scan-depth` 能正确透传到快复盘入口
- 缓存与 checkpoint 不因新增维度而错乱

### 12.2 性能验证

同一交易日、同一 `strategy-profile=balanced` 下，对比：

- `high`
- `low`

至少记录：

- 总耗时
- `selected` 数量
- 前 10 名股票代码与顺序
- `fundamental bundle` 调用次数
- `capital_profile` 调用次数

### 12.3 回归验证

重点关注：

- 负向文本阻断仍然有效
- 去重逻辑仍然有效
- same-day cache 仍然有效
- 输出 CSV/MD/checkpoint 字段不崩

## 13. 风险与权衡

### 13.1 命中率风险

`low` 档会更容易漏掉依赖机构、股东、分红等附加信息才能抬升排名的边缘样本。

这属于可接受权衡，因为第一阶段目标是：

- 先让快复盘不被拖死
- 优先保住头部强票

### 13.2 排序稳定性风险

如果 `low` 档对通过票不补 `capital_profile`，则排序会明显偏离现有口径。

因此不建议第一阶段完全去掉 `capital_profile`，而应采用：

- 只对通过样本补

### 13.3 缓存兼容风险

新增 `scan-depth` 后，如果 same-day cache 不区分深度，可能出现：

- `low` 档复用了 `high` 档缓存
- 或 `high` 档复用了 `low` 档不完整缓存

建议：

- 在 cache metadata 中写入 `scan_depth`
- 或至少写入 `enabled_blocks`
- 读取缓存时判断块是否覆盖本次需求

这是第一阶段实现必须处理的兼容点。

## 14. 实施建议

建议分两步落地：

### 第一步：参数化与 `low` 档打通

- 新增 `--scan-depth`
- `get_fundamental_bundle(...)` 支持 `enabled_blocks`
- 将 `run_fast_review_bundle.py` 默认 `earnings` 切到 `low`
- 对通过票补 `capital_profile`
- 增加阶段耗时统计

### 第二步：完善 `medium / high` 与缓存兼容

- 完善三档正式口径
- 细化 cache metadata
- 补充 benchmark/回归测试

## 15. 推荐结论

推荐采用：

- `strategy-profile` 保持当前 `strict / balanced / relaxed`
- 新增 `scan-depth = low / medium / high`
- 优先实现 `low`

`low` 档的核心原则是：

- 只保留对“快复盘当天先找强业绩候选”最关键的基础面块
- 默认跳过重但非首轮必需的块
- 只对通过样本补 `capital_profile`
- 用阶段耗时指标验证提速是否真实有效

这是当前最符合“提速优先”且不至于把业绩线价值直接砍掉的折中方案。

## 16. 参数优先级与默认值矩阵

为避免后续实现时出现“脚本默认值”和“快复盘默认值”混淆，明确如下：

| 场景 | strategy-profile 默认 | scan-depth 默认 | 说明 |
| --- | --- | --- | --- |
| 独立运行 `select_earnings_surprise_candidates.py` | `balanced` | `high` | 保持研究场景完整性优先 |
| 运行 `run_fast_review_bundle.py` 的 `earnings` 子任务 | `balanced` | `low` | 快复盘提速优先 |

参数覆盖优先级（高 -> 低）：

1. CLI 显式参数（如 `--scan-depth`、`--earnings-scan-depth`）
2. `config/local_strategy_profile.json` 本地配置
3. 脚本内置默认值

## 17. 回滚与降级预案

若上线后出现命中质量或排序稳定性明显劣化，按以下顺序快速回滚：

1. 将快复盘入口 `--earnings-scan-depth` 从 `low` 调整为 `high`（或临时 `medium`）
2. 保留 `scan-depth` 代码结构，但将 `local_strategy_profile.json` 中默认值回切
3. 若问题与缓存兼容相关，临时禁用不匹配深度的 cache 复用（强制重抓）

回滚验收标准：

- `selected` 数量与头部样本顺序恢复到历史可接受区间
- `fundamental_bundle` / `capital_profile` 调用统计与预期一致
- 快照与导出字段结构不回退、不丢字段
