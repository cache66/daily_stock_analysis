# 2026-04-25 主策略并行处理清单

本文档用于把当前 4 条主策略拆给不同机器并行推进，同时尽量避免互相改同一批文件。

适用范围：

- `trend_leader_unified`
- `earnings_surprise`
- `hundred_day_high`
- `monthly_slow_rise`

当前前提：

- 第一阶段共享层最小方案已落地。
- 已共享的公共因子只有 3 组：
  - `capital/liquidity`
  - `quality overlay`
  - `industry strength`
- 共享层文件当前为：
  - `src/services/shared_signal_factors_service.py`

## 1. 总原则

1. 每台机器默认只处理一条策略，不顺手改别的策略。
2. 除非明确分配，先不要改共享层和总目录文档。
3. 如果某台机器发现“新的公共逻辑”，先在自己的交付说明里记录，不要直接再抽象到共享层。
4. 每台机器优先修改：
   - 自己的策略脚本
   - 自己的测试
   - 自己的专题文档
5. 统一收口时，再由一台机器处理共享层扩展和总文档合并。

## 2. 冻结文件

以下文件在并行阶段默认冻结，只有“集成人员”可以改：

- `src/services/shared_signal_factors_service.py`
- `docs/LOCAL_STRATEGY_CATALOG.md`
- `docs/LOCAL_STRATEGY_BASELINE.md`
- `docs/AI_MODIFICATION_LOG.md`
- `docs/CHANGELOG.md`
- `config/local_strategy_profile.json`
- `scripts/run_fast_review_bundle.py`

说明：

- 这些文件最容易产生 merge 冲突。
- 其他机器如果确实需要改，先单独记录 diff 建议，不要直接提交到主分支版本。

## 3. 分工建议

### 机器 A：`earnings_surprise`

负责目标：

- 继续做业绩线主逻辑
- 强化事件后价格反应、多季度持续性、行业确认、质量过滤

允许修改：

- `scripts/select_earnings_surprise_candidates.py`
- `tests/test_earnings_surprise_signal_flow.py`
- `tests/test_shared_signal_factors_service.py`
- `docs/EARNINGS_SURPRISE_TRACKING.md`
- `docs/EARNINGS_SURPRISE_STRATEGY_BREAKDOWN.md`
- `docs/EARNINGS_SURPRISE_PLAYBOOK.md`
- `docs/EARNINGS_SURPRISE_QUALITY_SIGNAL.md`
- `docs/superpowers/specs/2026-04-24-earnings-observation-design.md`
- `scripts/collect_earnings_observation_snapshots.py`
- `tests/test_earnings_observation_signal_flow.py`

不要改：

- `scripts/select_monthly_slow_rise_candidates.py`
- `scripts/select_hundred_day_high_candidates.py`
- `scripts/select_trend_leader_candidates.py`
- 冻结文件列表中的所有文件

最低验证：

- `python -m pytest tests/test_earnings_surprise_signal_flow.py -q`
- 如改了 observation 链路，再跑：
  - `python -m pytest tests/test_earnings_observation_signal_flow.py -q`

### 机器 B：`monthly_slow_rise`

负责目标：

- 继续做月线慢牛主逻辑
- 强化周线稳定、波动压缩、业绩连续性、流动性过滤

允许修改：

- `scripts/select_monthly_slow_rise_candidates.py`
- `tests/test_monthly_slow_rise_candidates.py`
- `docs/MONTHLY_SLOW_RISE_SCAN.md`
- `scripts/collect_monthly_slow_rise_profile_snapshots.py`

可只读参考：

- `src/services/shared_signal_factors_service.py`
- `docs/CAPITAL_PROFILE_STRATEGY.md`

不要改：

- `scripts/select_earnings_surprise_candidates.py`
- `scripts/select_hundred_day_high_candidates.py`
- `scripts/select_trend_leader_candidates.py`
- 冻结文件列表中的所有文件

最低验证：

- `python -m pytest tests/test_monthly_slow_rise_candidates.py -q`

### 机器 C：`hundred_day_high`

负责目标：

- 继续做趋势确认层
- 补行业相对强度、突破质量、突破后跟随质量、Minervini/VCP 方向

允许修改：

- `scripts/select_hundred_day_high_candidates.py`
- `tests/test_hundred_day_high_signal_flow.py`
- `docs/KLINE_SELECTOR_GUIDE.md`
- `scripts/collect_hundred_day_high_profile_snapshots.py`

可只读参考：

- `scripts/select_earnings_surprise_candidates.py`
- `src/services/shared_signal_factors_service.py`

不要改：

- `scripts/select_earnings_surprise_candidates.py` 的实际逻辑
- `scripts/select_monthly_slow_rise_candidates.py`
- `scripts/select_trend_leader_candidates.py`
- 冻结文件列表中的所有文件

最低验证：

- `python -m pytest tests/test_hundred_day_high_signal_flow.py -q`

### 机器 D：`trend_leader_unified`

负责目标：

- 继续做主骨架策略
- 补 stage-2、行业排名、板块广度、base quality/VCP、过热惩罚

允许修改：

- `scripts/select_trend_leader_candidates.py`
- `src/services/trend_leader_strategy_service.py`
- `tests/test_trend_leader_signal_flow.py`
- `tests/test_trend_leader_strategy_service.py`
- `tests/test_trend_leader_daily_bundle.py`
- `tests/test_trend_leader_sharded_pipeline.py`
- `docs/TREND_LEADER_UNIFIED_STRATEGY.md`
- `scripts/run_trend_leader_daily_bundle.py`
- `scripts/run_trend_leader_sharded_pipeline.py`

可只读参考：

- `src/services/shared_signal_factors_service.py`
- `docs/CAPITAL_PROFILE_STRATEGY.md`

不要改：

- `scripts/select_earnings_surprise_candidates.py`
- `scripts/select_monthly_slow_rise_candidates.py`
- `scripts/select_hundred_day_high_candidates.py`
- 冻结文件列表中的所有文件

最低验证：

- `python -m pytest tests/test_trend_leader_signal_flow.py tests/test_trend_leader_strategy_service.py -q`

## 4. 集成人员

建议保留 1 台机器作为最后集成人员，专门处理这些事情：

- 是否需要继续扩展 `src/services/shared_signal_factors_service.py`
- 合并 `docs/LOCAL_STRATEGY_CATALOG.md`
- 合并 `docs/LOCAL_STRATEGY_BASELINE.md`
- 合并 `docs/AI_MODIFICATION_LOG.md`
- 合并 `docs/CHANGELOG.md`
- 必要时更新 `config/local_strategy_profile.json`
- 必要时调整 `scripts/run_fast_review_bundle.py`

这台机器不负责深挖单条策略细节，主要负责：

- 冲突消解
- 字段统一
- 默认行为收口
- 文档留痕

## 5. 交付格式

每台机器交付时，统一给出这 6 项：

- 改了什么
- 为什么这么改
- 验证情况
- 未验证项
- 风险点
- 建议是否需要进共享层

最后一项必须明确写成二选一：

- `不需要进共享层`
- `建议后续进入共享层，但本轮先不抽`

## 6. 合并顺序

建议按这个顺序合并，冲突最少：

1. `earnings_surprise`
2. `monthly_slow_rise`
3. `hundred_day_high`
4. `trend_leader_unified`
5. 集成人员统一处理共享层和总文档

原因：

- 现在共享层已经先接了 `earnings_surprise` 和 `monthly_slow_rise`
- `hundred_day_high` 下一步大概率只会部分复用
- `trend_leader_unified` 最复杂，最后合并更稳

## 7. 额外约束

并行期间，如果不是集成人员，不要直接修改下面这些横切面：

- `/signals` 聚合口径
- `signal_type` 命名
- `shared_signal_factors_service.py` 的接口签名
- 快复盘默认 include 列表
- 多策略共用缓存键

如果确实需要动，先在自己的交付里写：

- 变更理由
- 影响文件
- 对其他三条策略的影响

然后交给集成人员统一处理。

## 8. 一句话执行版

给其他机器的最短指令可以直接写成：

“只改你负责的那条策略脚本、对应测试和专题文档，不要改 `src/services/shared_signal_factors_service.py`、`docs/LOCAL_STRATEGY_CATALOG.md`、`docs/LOCAL_STRATEGY_BASELINE.md`、`docs/AI_MODIFICATION_LOG.md`、`docs/CHANGELOG.md`、`config/local_strategy_profile.json`、`scripts/run_fast_review_bundle.py`。如果发现新的公共逻辑，先记录，不要这轮直接抽共享层。”  

## 9. 执行状态更新（2026-04-25）

- 当前轮次按“4 条主策略 + 集成人员收口”已完成落地，`trend_leader_unified`、`earnings_surprise`、`hundred_day_high`、`monthly_slow_rise` 均已完成对应升级与回归验证。
- 冻结文件在并行阶段保持了“集中收口”，最终由集成人员统一补齐：
  - `docs/LOCAL_STRATEGY_CATALOG.md`
  - `docs/AI_MODIFICATION_LOG.md`
  - `docs/CHANGELOG.md`
  - `config/local_strategy_profile.json`
  - `scripts/run_fast_review_bundle.py`
- 复测证据（Task 8）已沉淀：
  - `python scripts/run_signal_performance_bundle.py --signal-types trend_leader_unified,earnings_surprise,hundred_day_high,monthly_slow_rise,continuous_up_ratio,continuous_up_streak --start-date 2026-04-01 --end-date 2026-04-24 --windows 1,3,5,10`
  - `python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-24 --limit 200`
  - 输出目录分别为 `data/signal_performance_bundle/2026-04-01_to_2026-04-24/` 与 `data/fast_review_daily/2026-04-24/`
- 当前默认每日核心循环保持为：`earnings` + `hundred_day_high` + `trend_leader`；`monthly_slow_rise` 为低频扩展，`continuous_up_*` 为观察层信号。
