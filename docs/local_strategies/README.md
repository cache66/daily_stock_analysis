# 个人策略专题总览

最后更新：2026-09-26

这套目录只服务我个人在当前代码里的本地策略体系，不属于原工程通用文档，也不再承担旧文档兼容说明。

如果你只想看现在仍然在维护的个人策略主入口，优先阅读：

1. [`../个人策略文档/README.md`](../个人策略文档/README.md)
2. [`../个人策略文档/个人策略目录.md`](../个人策略文档/个人策略目录.md)
3. [`../个人策略文档/个人策略基线.md`](../个人策略文档/个人策略基线.md)

如果你只想快速进入“我们自己维护的策略文档”，阅读顺序固定为：

1. 先看本文，确认目录结构和主入口。
2. 再看 `core/` 里的各策略现行文档，理解当前默认口径。
3. 需要补上下文时，再看 supporting 和 topics。
4. 需要查默认参数、变更留痕、用户可见摘要时，再回到根目录治理文档。

## 目录结构

- `core/`
  - 主策略现行中文文档（含观察期与冻结线标注）。
- `supporting/`
  - 主策略共用的方法论、资金层、业绩线拆解文档。
- `topics/`
  - 题材专题、映射器、快照脚本等非默认每日主链路文档。
- `designs/`
  - 个人专题的历史设计归档，保留当时的范围、边界与取舍，不代表当前默认实现。
- `plans/`
  - 个人专题的历史实施归档，保留拆解步骤与验证口径，不代表当前默认实现。

## 主策略一览（2026-09-27）

| 策略 | 是否默认每日 | 角色 | 文档 |
| --- | --- | --- | --- |
| `trend_leader_unified` | 是 | 龙头 + 趋势 + 资金 + 业绩兑现的统一主骨架（2026-09-26 恢复默认） | [`core/trend_leader_unified.md`](./core/trend_leader_unified.md) |
| `earnings_surprise` | 是 | 财报事件驱动的业绩强势筛选 | [`core/earnings_surprise.md`](./core/earnings_surprise.md) |
| `hundred_day_high` | 是 | 新高突破确认层 | [`core/hundred_day_high.md`](./core/hundred_day_high.md) |
| `daily_slow_rise` | 否（2026-09-27 冻结） | 日线 30-45 度慢涨、平台转趋势补充层 | 见 [`../个人策略文档/个人策略目录.md`](../个人策略文档/个人策略目录.md) |
| `monthly_slow_rise` | 否（2026-09-25 冻结） | 中期慢牛结构补充层 | [`core/monthly_slow_rise.md`](./core/monthly_slow_rise.md) |

冻结 / 停用状态以 [`../个人策略文档/策略与脚本冻结登记.md`](../个人策略文档/策略与脚本冻结登记.md) 为准。

## Supporting 文档

| 文档 | 作用 |
| --- | --- |
| [`supporting/main_strategy_blueprint.md`](./supporting/main_strategy_blueprint.md) | 统一说明主策略长期收敛方向。 |
| [`supporting/capital_profile.md`](./supporting/capital_profile.md) | 资金层字段、评分和使用边界。 |
| [`supporting/earnings_strategy_breakdown.md`](./supporting/earnings_strategy_breakdown.md) | 业绩线判断字段、权重、门槛与快照字段。 |
| [`supporting/earnings_playbook.md`](./supporting/earnings_playbook.md) | 业绩线实战判读与复盘手册。 |
| [`supporting/earnings_quality_signal.md`](./supporting/earnings_quality_signal.md) | 业绩质量子评分的独立说明。 |

## Topics 文档

这些文档属于专题扫描、研究映射或专题快照，不进入默认每日主链路；龙头 / 板块 / 题材 / 涨价四组工具已于 2026-09-25 冻结（转为催化参考工具），文档保留备查：

- [`topics/board_cycle_scan.md`](./topics/board_cycle_scan.md)
- [`topics/board_recognizability_ranking.md`](./topics/board_recognizability_ranking.md)
- [`topics/commodity_beneficiary_scan.md`](./topics/commodity_beneficiary_scan.md)
- [`topics/commodity_beneficiary_snapshots.md`](./topics/commodity_beneficiary_snapshots.md)
- [`topics/commodity_price_pass_through.md`](./topics/commodity_price_pass_through.md)
- [`topics/dragon_head_candidate_scan.md`](./topics/dragon_head_candidate_scan.md)
- [`topics/dragon_head_snapshots.md`](./topics/dragon_head_snapshots.md)
- [`topics/dragon_head_strategy.md`](./topics/dragon_head_strategy.md)
- [`topics/theme_core_board_scan.md`](./topics/theme_core_board_scan.md)
- [`topics/theme_core_board_snapshots.md`](./topics/theme_core_board_snapshots.md)
- [`topics/theme_core_candidate_scan.md`](./topics/theme_core_candidate_scan.md)
- [`topics/theme_core_mapper.md`](./topics/theme_core_mapper.md)

## 历史设计与实施归档

- [`designs/2026-05-01-board_cycle_scan_design.md`](./designs/2026-05-01-board_cycle_scan_design.md)
- [`plans/2026-05-01-board_cycle_scan_implementation.md`](./plans/2026-05-01-board_cycle_scan_implementation.md)
- [`designs/2026-05-02-board_concept_pool_refresh_design.md`](./designs/2026-05-02-board_concept_pool_refresh_design.md)
- [`plans/2026-05-02-board_concept_pool_refresh_implementation.md`](./plans/2026-05-02-board_concept_pool_refresh_implementation.md)

## 当前默认每日链路

主入口：

```bash
python scripts/run_fast_review_bundle.py --strategy-profile-file config/local_strategy_profile.json
```

当前默认每日首页主筛只跑三条（2026-09-27 起）：

- `earnings` -> `earnings_surprise`
- `hundred_day_high`
- `trend_leader` -> `trend_leader_unified`（2026-09-26 恢复：统一口径评估为全库最强前向绩效）

`long_base_release` 已于 2026-09-26 停用（评估为稳定负期望，脚本保留可手动单跑）；`daily_slow_rise` 于 2026-09-27 按 ⑤ 判定移出并冻结（脚本保留可按需单跑）；`monthly_slow_rise` 于 2026-09-25 冻结。停用 / 冻结与恢复流程见《策略与脚本冻结登记》。

## 根目录治理文档

- 默认参数与当前基线：[`../个人策略文档/个人策略基线.md`](../个人策略文档/个人策略基线.md)
- 资产清单与脚本入口：[`../个人策略文档/个人策略目录.md`](../个人策略文档/个人策略目录.md)
- 中英对照：[`../个人策略文档/个人策略中英对照.md`](../个人策略文档/个人策略中英对照.md)
- 详细变更与实跑证据：[`../个人策略文档/个人策略修改记录.md`](../个人策略文档/个人策略修改记录.md)
- 用户可见变更摘要：[`../CHANGELOG.md`](../CHANGELOG.md)

如果文档和代码冲突，以脚本与实现为准。
