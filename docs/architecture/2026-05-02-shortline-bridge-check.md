# 短线桥接自检说明

最后更新：2026-05-02

## 1. 目的

这份文档只解决一个问题：

在真实 `WonderTrader` / `FinGenius` 业务逻辑尚未完全接入前，如何快速判断当前机器上的桥接环境是不是已经可用。

自检脚本入口：

- [scripts/check_shortline_bridge_setup.py](/d:/bb/daily_stock_analysis/scripts/check_shortline_bridge_setup.py:1)

核心逻辑：

- [src/shortline_hub/bridge_check.py](/d:/bb/daily_stock_analysis/src/shortline_hub/bridge_check.py:1)

## 2. 自检覆盖范围

当前会检查三层内容：

1. 静态环境检查
   - `WonderTrader` / `FinGenius` bridge 脚本是否存在
   - 对应 `workdir` 是否存在
   - `bridge_data` 目录是否存在
   - 是否存在按 `trade_date` 或 `latest` 命名的本地样例文件
2. 单桥 smoke
   - `wt_export_candidates.py` 是否能独立读 request、写 output
   - `fg_explain_candidate.py` 是否能独立读 request、写 output
3. 整链路 smoke
   - 当前工程是否能通过 `shortline_hub --mode process` 串起两边外部脚本

## 2.1 当前 WonderTrader bridge 的候选来源顺序

现在 `D:\bb\WonderTrader\bridge\wt_export_candidates.py` 的候选来源顺序是：

1. `bridge_data/wt_candidates_<trade_date>.json`
2. `bridge_data/wt_candidates_latest.json`
3. `bridge_data/*.csv`
4. 当前工程同级目录下的 `daily_stock_analysis/data/cache/reference/kline_selector_spot_universe.csv`
5. 仍然找不到时才回退 placeholder

这意味着当前虽然还没有接入真正的 `WonderTrader SEL` 扫描器，但已经不是纯假数据：

- 如果当前工程本地 spot cache 存在，WT bridge 会基于它做轻量候选排序
- 输出 `scan_source=wondertrader_cache_scan`
- 这条路径适合现在的单机过渡阶段

## 2.2 当前 FinGenius bridge 的解释来源顺序

现在 `D:\bb\FinGenius\bridge\fg_explain_candidate.py` 的解释来源顺序是：

1. `bridge_data/fg_explanations_<trade_date>.json`
2. `bridge_data/fg_explanations_latest.json`
3. 没有匹配解释文件时，基于候选字段生成启发式短线解释

当前启发式解释会使用：

- `trigger_type`
- `trigger_score`
- `change_pct`
- `volume_ratio`
- `turnover_rate`
- `board_name`
- `risk_flags`

这意味着当前虽然还没有接入真正的 FinGenius 多 agent 解释层，但已经不是纯 placeholder：

- 能区分强势涨停/高换手/量比放大等短线特征
- 能给出热钱、筹码、情绪、风险和短线观察视角
- 适合现在的单机过渡阶段

## 3. 默认路径

脚本默认按当前单机目录约定查找：

- `D:\bb\daily_stock_analysis`
- `D:\bb\WonderTrader`
- `D:\bb\FinGenius`

默认外部脚本：

- `D:\bb\WonderTrader\bridge\wt_export_candidates.py`
- `D:\bb\FinGenius\bridge\fg_explain_candidate.py`

如果你后面改了目录，也可以通过 CLI 参数覆盖。

## 4. 推荐使用顺序

### 第一步：只做静态检查

```powershell
python scripts/check_shortline_bridge_setup.py `
  --trade-date 2026-05-02
```

适合先看：

- 路径有没有配错
- `bridge_data` 有没有准备
- 当前环境是不是还停留在半成品阶段

### 第二步：跑单桥 smoke

```powershell
python scripts/check_shortline_bridge_setup.py `
  --trade-date 2026-05-02 `
  --run-script-smoke
```

适合先确认：

- 外部 bridge 脚本本身能不能跑
- request/output 协议是否还兼容

### 第三步：跑整链路 smoke

```powershell
python scripts/check_shortline_bridge_setup.py `
  --trade-date 2026-05-02 `
  --run-orchestrator-smoke
```

适合确认：

- 当前工程是否仍能正常编排 `WonderTrader + FinGenius`
- 不是单桥能跑、但串起来又断

## 5. 输出产物

默认输出目录：

- `data/manual_runs/shortline_bridge_check_YYYYMMDD/`

关键产物：

- `bridge_setup_summary.json`
- `bridge_setup_report.md`

如果启用了 smoke，还会额外看到：

- `smoke/wondertrader_request.json`
- `smoke/wondertrader_output.json`
- `smoke/fingenius_request.json`
- `smoke/fingenius_output.json`
- `orchestrator_smoke/shortline_report.md`

## 6. 当前判断原则

建议后续接真实外部逻辑时按下面原则执行：

1. 自检不过，不要先改当前工程编排层。
2. 先修外部 bridge 脚本路径、输入输出协议或 `bridge_data`。
3. 单桥 smoke 通过后，再看整链路 smoke。
4. 整链路 smoke 通过后，再往 bridge 内部替换真实业务逻辑。

这样能把问题定位留在最小边界，不会把 `daily_stock_analysis`、`WonderTrader`、`FinGenius` 三边同时搅乱。
