# RQAlpha 标准化评估层下一步

最后更新：2026-04-28

## 1. 当前起点

扫描层这一段已经先收口到共享扫描壳：

- `trend_leader_unified`
- `hundred_day_high`
- `monthly_slow_rise`

其中 `monthly_slow_rise` 已完成共享扫描壳接入，并且可通过 `run_fast_review_bundle.py --include-signals monthly_slow_rise` 和另外两条 K 线扫描策略统一观测。

这意味着下一阶段不该继续优先处理“怎么扫”，而该切到“怎么评”。

## 2. 下一阶段目标

借 `RQAlpha` 的价值，不是迁移主策略引擎，而是补一层更标准化的评估语义：

1. 把当前 `signal_snapshot` 评估从“单脚本统计”推进到“标准化评估层原型”。
2. 先服务最需要中期验证的两条策略：
   - `monthly_slow_rise`
   - `earnings_surprise`
3. 在不改现有选股脚本入口的前提下，补清楚：
   - 建仓日
   - 持有窗口
   - 成本/滑点
   - 止盈止损
   - 回撤
   - 胜率 / 盈亏比 / 分布

## 3. 第一轮最小实现

建议先做最小可落地版本，不上完整交易框架：

### 3.1 统一评估配置层

新增一个评估配置对象，统一描述：

- `entry_rule`
- `exit_rule`
- `holding_days`
- `position_sizing`
- `fee_rate`
- `slippage_bp`

第一轮只支持固定仓位、固定窗口和简单费用模型。

### 3.2 统一评估执行层

把现有 `signal_snapshot` 评估逻辑往两层拆：

- `signal selection snapshot`
- `evaluation engine`

让后续不同策略只是喂信号，评估引擎负责统一算收益、回撤、成本和结果汇总。

### 3.3 统一分析输出层

第一轮分析输出至少固定：

- 命中数
- 完成评估数
- 1/3/5/10 日收益
- 最大回撤
- 胜率
- 盈亏比
- 费用前后收益对比

## 4. 推荐实施顺序

1. 先给 `scripts/evaluate_signal_snapshot_performance.py` 外挂标准化评估配置，不改默认行为。
2. 先落 `monthly_slow_rise` 的中期窗口评估样例。
3. 再落 `earnings_surprise` 的事件后短窗评估样例。
4. 最后再考虑组合层、资金曲线和更真实成交模型。

## 5. 边界

这一阶段明确不做：

- 不把 4 条主策略迁到 `RQAlpha`
- 不接实盘交易
- 不先做完整账户系统
- 不先做多策略组合优化

先把“标准化评估层原型”做出来，比继续扩策略名更值钱。
