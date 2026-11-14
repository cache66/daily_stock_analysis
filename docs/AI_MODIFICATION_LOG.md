# 兼容入口：个人策略修改记录

这个旧路径只为兼容仓库内已有引用保留。

最新工作台设计见[个人策略修改记录](./个人策略文档/个人策略修改记录.md)“2026-11-10（工作台方向校准：长线公司逻辑＋可扩展短线方法，仅文档）”：长线围绕业绩/产业/龙头，短线支持热点、股东户数、超跌等独立研究过程；每日页面看变化与待办。目标方案、导航及旧文档适用范围已同步，本轮未改代码、配置、页面、数据库或策略状态，具体验证与回退只在该记录维护。

历史实现修复见[个人策略修改记录](./个人策略文档/个人策略修改记录.md)“2026-11-09（工作台复审：批次准入、证据版本与交易日测量）”：完成批次与版本贯通导入，公开时间未知留空，交易日检查点、行情修订与原子关闭补回归。具体实施范围以该次记录为准，不代表当前合同或策略收益已验收。

当前个人策略修改记录请改看：

- [`docs/个人策略文档/个人策略修改记录.md`](./个人策略文档/个人策略修改记录.md)

历史方案修订见该记录“2026-11-08（生产编排复审：时区、完成证据与健康边界）”：核对五条既有 cron、11-06 日志/审计和 screening UTC 字段，补现有编排登记、按完成证据读取及跨时区语义；保留 matrix 后处理失败/筛选降级限制。此前“扩展接入设计与三层方案评审收敛”“三层投研重构：策略执行地图与主工程复用”等记录继续作为历史过程；当前业务结构以 11-10 双线校准方案为准，历史状态不代表现阶段实现验收。

2026-11-07 的资格分离、描述状态边界、规则登记与阳光电源选举事件核验，见该记录“第四轮：资格分离与纵向事件核验”条目；此前见“分层落地与首批 L0”“口径复审与数据可行性”“评审补充”“架构修订”条目。本文件继续只作兼容导航，不另维护一份变更记录。

## 2026-11-11 炸板窗口口径更正（first_seal 缺失 → 56% 假值）

- 文件：`src/services/break_window_service.py`（重写，SCHEMA=2）、`src/services/broken_followup_service.py`、
  `src/services/broken_score.py`、`apps/dsa-web/src/pages/WorkbenchPage.tsx`、`apps/dsa-web/src/api/workbench.ts`、
  `tests/test_break_window.py`、`tests/test_broken_score.py`
- 根因：`broken_followup_service` 调用 `analyze_break_window` 时未传 `first_seal`；
  `seal_time_str(None)` 返回 `09:30:00`；且原指标是「开板至收盘累计成交占比」。
- 处置：改「炸板瞬间 5 分钟」为 `moment_ratio`；`open_ratio` 仅参考；分档净额限定在开板后；
  打分新增 30/40/45/50 封顶硬规则；占比特征系数置 0 待重拟合；缓存 schema 升级。
- 命令：`pytest tests/test_break_window.py tests/test_broken_score.py tests/test_broken_followup.py`
  → 全部通过；`npm run lint && npm run build` → 通过；`curl /api/v1/workbench/broken-followup?...&refresh=true`
  验证 8 只 11-11 炸板全部 ≤39 分（不跟）。

