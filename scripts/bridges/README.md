# 短线桥接脚本说明

这两个桥接模板用于把当前工程的 `shortline_hub --mode process` 接到本机外部目录：

- `shortline_wondertrader_bridge_template.py`
- `shortline_fingenius_bridge_template.py`

当前工程目录：

- `D:\bb\daily_stock_analysis`

推荐外部脚本位置：

- `D:\bb\WonderTrader\bridge\wt_export_candidates.py`
- `D:\bb\FinGenius\bridge\fg_explain_candidate.py`

## 2026-05-03 更新

- `FinGenius` 桥接默认走轻量模式。
- 默认只调用 `HotMoneyTool` 和 `ChipAnalysisTool`。
- 默认不调用最慢的 `BigDealAnalysisTool`。
- `big_deal_summary` 会用已抓到的资金/量价信息做代理摘要。

如果要保留完整大单链路，请在当前工程显式加：

```powershell
python scripts/run_shortline_hub.py ... --fg-enable-big-deal
```

建议用法：

- 日常批量复盘：默认轻量模式
- 单票深挖或对照验证：打开 `--fg-enable-big-deal`

## 推荐环境（2026-05-03 已验证）

`FinGenius` 当前更推荐使用独立 `Python 3.11` 环境执行 bridge，而不是继续复用当前工程解释器。

- 推荐解释器：
  - `D:\bb\FinGenius\.venv311\Scripts\python.exe`
- 这套环境已验证可直接导入并执行：
  - `HotMoneyTool`
  - `ChipAnalysisTool`
  - `BigDealAnalysisTool`
- 当前最小依赖集：
  - `pydantic~=2.10.6`
  - `pandas~=2.2.3`
  - `numpy`
  - `requests~=2.32.3`
  - `loguru~=0.7.3`
  - `rich~=13.7.1`
  - `efinance~=0.5.5.2`
  - `akshare~=1.16.87`

如需在新机器上补这套环境，可直接执行：

```powershell
py -3.11 -m venv D:\bb\FinGenius\.venv311
D:\bb\FinGenius\.venv311\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
D:\bb\FinGenius\.venv311\Scripts\python.exe -m pip install pydantic~=2.10.6 pandas~=2.2.3 numpy requests~=2.32.3 loguru~=0.7.3 rich~=13.7.1 efinance~=0.5.5.2 akshare~=1.16.87
```

日常轻量批量复盘示例：

```powershell
python scripts/run_shortline_hub.py `
  --mode process `
  --trade-date 2026-05-03 `
  --top-n 5 `
  --run-id shortline_daily_demo_fg_py311_20260503 `
  --output-dir data/manual_runs/shortline_daily_demo_fg_py311_20260503 `
  --wt-python-executable python `
  --wt-script-path D:\bb\WonderTrader\bridge\wt_export_candidates.py `
  --wt-workdir D:\bb\WonderTrader `
  --wt-runtime-dir data/runtime/shortline_hub/wt_daily_demo_fg_py311_20260503 `
  --fg-python-executable D:\bb\FinGenius\.venv311\Scripts\python.exe `
  --fg-script-path D:\bb\FinGenius\bridge\fg_explain_candidate.py `
  --fg-workdir D:\bb\FinGenius `
  --fg-runtime-dir data/runtime/shortline_hub/fg_daily_demo_py311_20260503 `
  --log-level INFO
```

单票全量验证示例：

```powershell
python scripts/run_shortline_hub.py `
  --mode process `
  --trade-date 2026-05-03 `
  --top-n 1 `
  --run-id shortline_fullmode_fg_py311_top1_20260503 `
  --output-dir data/manual_runs/shortline_fullmode_fg_py311_top1_20260503 `
  --wt-python-executable python `
  --wt-script-path D:\bb\WonderTrader\bridge\wt_export_candidates.py `
  --wt-workdir D:\bb\WonderTrader `
  --wt-runtime-dir data/runtime/shortline_hub/wt_fullmode_fg_py311_top1_20260503 `
  --fg-python-executable D:\bb\FinGenius\.venv311\Scripts\python.exe `
  --fg-script-path D:\bb\FinGenius\bridge\fg_explain_candidate.py `
  --fg-workdir D:\bb\FinGenius `
  --fg-runtime-dir data/runtime/shortline_hub/fg_fullmode_py311_top1_20260503 `
  --fg-enable-big-deal `
  --log-level INFO
```

## 推荐接法

1. 先把模板脚本复制到外部目录。
2. 先在外部目录里单独验证 request/output JSON 协议。
3. 再把模板内部的示例逻辑替换成真实 `WonderTrader` / `FinGenius` 调用。
4. 最后由当前工程统一通过 `shortline_hub --mode process` 编排。

## WonderTrader 协议

输入：

```json
{
  "trade_date": "2026-05-02",
  "top_n": 10
}
```

输出：

```json
[
  {
    "candidate_id": "2026-05-02-300001",
    "symbol": "300001",
    "name": "示例股票",
    "trade_date": "2026-05-02",
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "示例触发原因",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "示例板块",
    "setup_tag": "涨停后分歧承接",
    "risk_flags": []
  }
]
```

## FinGenius 协议

输入：

```json
{
  "candidate": {
    "candidate_id": "2026-05-02-300001",
    "symbol": "300001",
    "name": "示例股票",
    "trade_date": "2026-05-02",
    "scan_source": "wondertrader_process",
    "trigger_type": "momentum_breakout",
    "trigger_reason": "示例触发原因",
    "trigger_score": 87.0,
    "price": 21.5,
    "change_pct": 7.6,
    "volume_ratio": 2.0,
    "turnover_rate": 5.1,
    "board_name": "示例板块",
    "setup_tag": "涨停后分歧承接",
    "risk_flags": []
  },
  "bridge_options": {
    "enable_big_deal": false
  }
}
```

输出最少包含原来 8 个解释字段，并补齐 instrumentation 元数据：

```json
{
  "candidate_id": "2026-05-02-300001",
  "hot_money_summary": "示例热钱说明",
  "big_deal_summary": "示例大单说明",
  "chip_commentary": "示例筹码说明",
  "sentiment_commentary": "示例情绪说明",
  "risk_commentary": "示例风险说明",
  "short_term_view": "示例短线观点",
  "confidence_label": "high",
  "protocol_version": "shortline_fg_v1",
  "explanation_source": "upstream_tools",
  "used_upstream_tools": ["HotMoneyTool", "ChipAnalysisTool"],
  "tool_error_count": 0,
  "tool_errors": [],
  "explain_elapsed_ms": 28,
  "upstream_tool_elapsed_ms": {
    "HotMoneyTool": 18,
    "ChipAnalysisTool": 10
  }
}
```

字段说明：

- `protocol_version`
  - 当前 batch instrumentation 协议版本，现为 `shortline_fg_v1`
- `explanation_source`
  - `bridge_data`：命中本地 `bridge_data`
  - `upstream_tools`：命中至少一个真实 upstream 工具
  - `heuristic_fallback`：真实工具未产出有效结果，回退启发式解释
  - `legacy_unknown`：仅用于仓内 adapter 兼容旧外部输出，新 bridge 不应主动输出
- `used_upstream_tools`
  - 本次解释真实命中的工具名列表
- `tool_error_count` / `tool_errors`
  - 记录部分工具失败但整体仍可用的情况
- `explain_elapsed_ms`
  - 本次 `FinGenius` bridge 自报耗时，单位毫秒
- `upstream_tool_elapsed_ms`
  - 真实执行过的 upstream 工具耗时拆分

## bridge_data 约定

在真实外部策略尚未完全接好前，可以优先使用本地导出样本：

`WonderTrader` 支持：

- `bridge_data/wt_candidates_<trade_date>.json`
- `bridge_data/wt_candidates_latest.json`
- `bridge_data/wt_candidates_<trade_date>.csv`
- `bridge_data/wt_candidates_latest.csv`

`FinGenius` 支持：

- `bridge_data/fg_explanations_<trade_date>.json`
- `bridge_data/fg_explanations_latest.json`
