# 业绩观察策略设计

日期：2026-04-24

## 目标

新增一个“业绩观察策略”尾部任务，挂在每日 schedule 主流程结束后执行。

策略目标：
- 用现有 `earnings_surprise balanced` 作为“季报不错”的唯一入池标准
- 对入池股票做持续趋势观察
- 每日只展示仍然强势的观察名单
- 转弱时移出每日观察，但保留长期观察历史
- 若后续连续 2 个季报都未通过现有门槛，则彻底剔除

## 第一版边界

第一版不新增新表，复用现有 `kline_signal_snapshot`。

新增两个 `signal_type`：
- `earnings_observation_registry`
- `earnings_observation_active`

第一版只接入 A 股 schedule 尾部任务，不改主分析链，不改现有 `earnings_surprise` 判定逻辑。

## 生命周期规则

### 入池

当股票通过现有 `earnings_surprise balanced` 时进入观察池；若已在池中，则刷新最近一次有效财报信息。

### 每日活跃

每日从观察池中筛出仍满足趋势条件的股票，写入 `earnings_observation_active`。

趋势条件第一版使用偏宽的留存标准，不强绑百日新高，建议至少包含：
- 收盘价站上 `MA20`
- `MA20 >= MA60`
- 距离近 `60~100` 日高点不宜过远

### 转弱

若不再满足趋势条件，则当日不进入 `earnings_observation_active`，但仍保留在 `earnings_observation_registry` 中。

### 重新激活

已在观察池中的股票若后续再次满足趋势条件，可直接重新回到 `active`。

### 彻底剔除

若后续出现连续 2 个季报都未通过现有 `earnings_surprise balanced` 门槛，则标记为永久剔除，不再参与每日观察。

### 观察上限

第一版增加最长观察窗口，避免观察池无限膨胀；建议默认 `180~240` 个交易日，超期后仅保留历史，不再参与每日活跃筛选。

## 持久化方案

每日尾部脚本读取：
- 昨日 `earnings_observation_registry`
- 今日 `earnings_surprise balanced` 命中结果
- 今日趋势状态

然后生成当日两份快照，并使用 `replace_signal_snapshots_for_date(...)` 原子替换：
- `earnings_observation_registry`
- `earnings_observation_active`

### 字段建议

`metrics_payload`：
- `status`
- `trend_score`
- `above_ma20`
- `ma20_above_ma60`
- `distance_to_high_pct`
- `observation_days`
- `bad_quarter_streak`

`history_payload`：
- `first_watch_date`
- `last_qualified_earnings_date`
- `last_active_date`
- `previous_status`
- `removal_reason`

## 调度接入

在 `main.py` 里按现有可选尾部任务模式新增：
- 配置开关
- 非交易日跳过
- 子进程脚本执行
- `fail-open`

## 查询与展示

第一版直接复用现有 `/signals` 查询体系。

如需让前端默认可见，再补 `SignalSnapshotService` 默认类型与标签即可。

## 错误处理

- 单次尾部任务失败不影响主流程
- 非交易日直接跳过
- 若昨日观察池为空，则按今日财报命中初始化
- 若当日趋势数据缺失，单票 fail-open 为 `inactive`，避免误入活跃名单

## 验证范围

- 观察池初始化
- 已入池股票转为 `active`
- 已入池股票转弱后只保留 registry
- 再次转强后重新激活
- 连续两次坏季报后永久剔除
- schedule 尾部任务配置开关与子进程命令拼装

## 不做的事

- 不改 `earnings_surprise` 分数模型
- 不新增独立 registry 数据表
- 不在第一版做复杂前端页面
- 不改现有主策略顺序与结果语义

## 实施状态更新（2026-04-25）

- 该设计已落地到脚本与调度链路：
  - `scripts/collect_earnings_observation_snapshots.py`
  - `main.py`（schedule 尾部子任务接线）
  - `src/config.py`（开关与最长观察窗口配置）
- 两个 `signal_type` 已按设计投入使用：
  - `earnings_observation_registry`
  - `earnings_observation_active`
- 生命周期关键规则已实现：
  - 复用 `earnings_surprise balanced` 作为唯一入池标准
  - `active/inactive/removed` 状态机
  - 连续两次坏季报后移除（`two_consecutive_bad_quarters`）
  - 超过最长观察窗口移除（`max_observation_days_exceeded`）
- 回归测试文件已在仓库中并参与本轮策略改造验证：
  - `tests/test_earnings_observation_signal_flow.py`
