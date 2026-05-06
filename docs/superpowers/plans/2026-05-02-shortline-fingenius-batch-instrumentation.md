# Shortline FinGenius Batch Instrumentation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 扩展 `shortline_hub` 现有批量逐票解释链路，让 `FinGenius` 解释来源、工具命中和耗时成为结构化运行事实，并完成一轮小规模真实 `process mode` smoke 验证。

**Architecture:** 不新增平行批处理流程，继续复用 `scripts/run_shortline_hub.py -> ShortlineHubOrchestrator -> FinGeniusProcessAdapter -> shortline_fingenius_bridge_template.py` 这条既有链路。首版先把来源与耗时元数据做到 bridge 输出、schema、运行产物和 Markdown 报告里，再用 `top_n=3` 的真实外部脚本 smoke 验证跨工程联调结果。缺失新字段的旧 bridge 输出必须视为 `legacy_unknown`，而不是伪装成 `heuristic_fallback`。

**Tech Stack:** Python, pytest, dataclasses, `subprocess` process bridge, existing shortline hub adapters/report builder, repository docs under `docs/`

---

## File Map

- Modify: `scripts/bridges/shortline_fingenius_bridge_template.py`
  - 增加协议版本、解释来源、工具命中、工具错误、bridge 自报耗时元数据
- Modify: `src/shortline_hub/adapters/fingenius_adapter.py`
  - 兼容读取新字段，并对旧输出默认回填 `legacy_unknown`
- Modify: `src/shortline_hub/schemas.py`
  - 扩展 explanation / combined / run summary 数据结构
- Modify: `src/shortline_hub/orchestrator.py`
  - 增加 orchestrator 观测总解释耗时
- Modify: `src/shortline_hub/report_builder.py`
  - 输出来源分布、工具命中、耗时和逐票来源信息
- Modify: `tests/test_shortline_bridge_templates.py`
  - 覆盖 bridge 新协议与部分成功/部分失败场景
- Modify: `tests/test_shortline_hub_orchestrator.py`
  - 覆盖 adapter 兼容、新字段透传和 summary 聚合
- Modify: `tests/test_shortline_hub_cli.py`
  - 覆盖 artifacts 与 Markdown 报告新增字段
- Modify: `scripts/bridges/README.md`
  - 记录新输出字段与真实 smoke 用法
- Modify: `docs/AI_MODIFICATION_LOG.md`
  - 记录实现和验证证据
- Modify: `docs/CHANGELOG.md`
  - 在 `[Unreleased]` 追加扁平 changelog
- Optional modify if smoke exposes drift: `D:\bb\FinGenius\bridge\fg_explain_candidate.py`
  - 仅在真实 smoke 证明外部脚本未同步新协议字段时做最小同步

### Task 1: Add Failing Contract Tests For FinGenius Bridge Metadata

**Files:**
- Modify: `tests/test_shortline_bridge_templates.py`
- Verify: `tests/test_shortline_bridge_templates.py`

- [ ] **Step 1: 为 `bridge_data` 命中场景增加失败测试**

在 `test_fingenius_bridge_template_prefers_local_explanation_file(...)` 之后新增：

```python
def test_fingenius_bridge_template_marks_bridge_data_source(tmp_path: Path) -> None:
    script_dir = tmp_path / "bridge"
    bridge_data_dir = script_dir / "bridge_data"
    script_dir.mkdir()
    bridge_data_dir.mkdir()
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
        script_path,
    )

    request_path = tmp_path / "fg_request.json"
    output_path = tmp_path / "fg_output.json"
    request_path.write_text(
        json.dumps(
            {
                "candidate": {
                    "candidate_id": "2026-05-02-300001",
                    "symbol": "300001",
                    "name": "candidate_x",
                    "trade_date": "2026-05-02",
                    "scan_source": "wondertrader_process",
                    "trigger_type": "momentum_breakout",
                    "trigger_reason": "test",
                    "trigger_score": 87.0,
                    "price": 21.5,
                    "change_pct": 7.6,
                    "volume_ratio": 2.0,
                    "turnover_rate": 5.1,
                    "board_name": "power",
                    "risk_flags": [],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (bridge_data_dir / "fg_explanations_2026-05-02.json").write_text(
        json.dumps(
            {
                "2026-05-02-300001": {
                    "candidate_id": "2026-05-02-300001",
                    "hot_money_summary": "loaded real hot money",
                    "big_deal_summary": "loaded real big deal",
                    "chip_commentary": "loaded real chip",
                    "sentiment_commentary": "loaded real sentiment",
                    "risk_commentary": "loaded real risk",
                    "short_term_view": "loaded real view",
                    "confidence_label": "high",
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(script_path), str(request_path), str(output_path)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["protocol_version"] == "shortline_fg_v1"
    assert payload["explanation_source"] == "bridge_data"
    assert payload["used_upstream_tools"] == []
    assert payload["tool_error_count"] == 0
    assert payload["tool_errors"] == []
    assert payload["explain_elapsed_ms"] >= 0
```

- [ ] **Step 2: 为真实 upstream 工具全成功场景增加失败测试**

在 `test_fingenius_bridge_template_uses_real_upstream_tools_when_available(...)` 里追加断言：

```python
    assert payload["protocol_version"] == "shortline_fg_v1"
    assert payload["explanation_source"] == "upstream_tools"
    assert payload["used_upstream_tools"] == [
        "HotMoneyTool",
        "ChipAnalysisTool",
        "BigDealAnalysisTool",
    ]
    assert payload["tool_error_count"] == 0
    assert payload["tool_errors"] == []
    assert payload["explain_elapsed_ms"] >= 0
```

- [ ] **Step 3: 为“部分工具成功、部分工具失败”增加失败测试**

新增：

```python
def test_fingenius_bridge_template_tracks_partial_upstream_tool_failures(
    tmp_path: Path,
) -> None:
    fingenius_root = tmp_path / "FinGenius"
    script_dir = fingenius_root / "bridge"
    script_dir.mkdir(parents=True)
    script_path = script_dir / "shortline_fingenius_bridge_template.py"
    shutil.copyfile(
        Path(__file__).resolve().parent.parent / "scripts/bridges/shortline_fingenius_bridge_template.py",
        script_path,
    )

    tool_dir = fingenius_root / "upstream" / "src" / "tool"
    tool_dir.mkdir(parents=True)
    (tool_dir.parent / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "__init__.py").write_text("", encoding="utf-8")
    (tool_dir / "base.py").write_text(
        '''
class ToolResult:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
'''.strip(),
        encoding="utf-8",
    )
    (tool_dir / "hot_money.py").write_text(
        '''
from src.tool.base import ToolResult

class HotMoneyTool:
    async def execute(self, **kwargs):
        return ToolResult(output={
            "stock_latest_info": [{"stock_code": "300001", "turnover_rate": 5.1}],
            "daily_top_list": [{"stock_code": "300001", "reason": "active hot money"}],
            "stock_net_flow": [{"main_net_inflow": "1.20亿"}],
        })
'''.strip(),
        encoding="utf-8",
    )
    (tool_dir / "chip_analysis.py").write_text(
        '''
class ChipAnalysisTool:
    async def execute(self, **kwargs):
        raise RuntimeError("chip tool failed")
'''.strip(),
        encoding="utf-8",
    )
    (tool_dir / "big_deal_analysis.py").write_text(
        '''
from src.tool.base import ToolResult

class BigDealAnalysisTool:
    async def execute(self, **kwargs):
        return ToolResult(output={
            "stock_big_deal_summary": {"net_inflow_wan": 3200.0, "trade_count": 18},
            "individual_rank_stock": [{"stock_code": "300001", "main_net_inflow": "1.20亿"}],
        })
'''.strip(),
        encoding="utf-8",
    )

    request_path = tmp_path / "fg_request.json"
    output_path = tmp_path / "fg_output.json"
    request_path.write_text(
        json.dumps(
            {
                "candidate": {
                    "candidate_id": "2026-05-02-300001",
                    "symbol": "300001",
                    "name": "sample_stock",
                    "trade_date": "2026-05-02",
                    "scan_source": "wondertrader_process",
                    "trigger_type": "momentum_breakout",
                    "trigger_reason": "test",
                    "trigger_score": 92.0,
                    "price": 21.5,
                    "change_pct": 7.6,
                    "volume_ratio": 2.0,
                    "turnover_rate": 5.1,
                    "board_name": "power",
                    "setup_tag": "breakout",
                    "risk_flags": [],
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [sys.executable, str(script_path), str(request_path), str(output_path)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["explanation_source"] == "upstream_tools"
    assert payload["used_upstream_tools"] == ["HotMoneyTool", "BigDealAnalysisTool"]
    assert payload["tool_error_count"] == 1
    assert payload["tool_errors"] == ["ChipAnalysisTool: chip tool failed"]
```

- [ ] **Step 4: 为 heuristic fallback 场景补失败测试**

在 `test_fingenius_bridge_template_falls_back_when_real_upstream_tools_fail(...)` 里追加断言：

```python
    assert payload["protocol_version"] == "shortline_fg_v1"
    assert payload["explanation_source"] == "heuristic_fallback"
    assert payload["used_upstream_tools"] == []
    assert payload["tool_error_count"] == 3
    assert payload["tool_errors"] == [
        "HotMoneyTool: hot money failed",
        "ChipAnalysisTool: chip failed",
        "BigDealAnalysisTool: big deal failed",
    ]
    assert payload["explain_elapsed_ms"] >= 0
```

- [ ] **Step 5: 运行 bridge 针对性测试，确认先红**

Run:

```bash
python -m pytest tests/test_shortline_bridge_templates.py -k "bridge_data_source or upstream_tools_when_available or partial_upstream_tool_failures or falls_back_when_real_upstream_tools_fail" -q
```

Expected:

- 至少 1 个新增断言失败
- 失败原因是新元数据字段尚未实现，而不是测试拼写或导入错误

### Task 2: Implement Bridge Metadata With Protocol Version And Safe Defaults

**Files:**
- Modify: `scripts/bridges/shortline_fingenius_bridge_template.py`
- Verify: `tests/test_shortline_bridge_templates.py`

- [ ] **Step 1: 增加协议常量与来源枚举**

在文件顶部常量区加入：

```python
import time
```

```python
BRIDGE_PROTOCOL_VERSION = "shortline_fg_v1"
SOURCE_BRIDGE_DATA = "bridge_data"
SOURCE_UPSTREAM_TOOLS = "upstream_tools"
SOURCE_HEURISTIC_FALLBACK = "heuristic_fallback"
```

- [ ] **Step 2: 实现统一元数据封装 helper**

在 `_ensure_required_fields(...)` 之前新增：

```python
def _build_metadata_payload(
    *,
    payload: dict[str, Any],
    explanation_source: str,
    started_at: float,
    used_upstream_tools: list[str] | None = None,
    tool_errors: list[str] | None = None,
) -> dict[str, Any]:
    normalized_tools = [str(item).strip() for item in (used_upstream_tools or []) if str(item).strip()]
    normalized_errors = [str(item).strip() for item in (tool_errors or []) if str(item).strip()]
    merged = dict(payload)
    merged["protocol_version"] = BRIDGE_PROTOCOL_VERSION
    merged["explanation_source"] = str(explanation_source).strip()
    merged["used_upstream_tools"] = normalized_tools
    merged["tool_error_count"] = len(normalized_errors)
    merged["tool_errors"] = normalized_errors
    merged["explain_elapsed_ms"] = max(0, int((time.perf_counter() - started_at) * 1000))
    return merged
```

- [ ] **Step 3: 让 `bridge_data` 路径返回结构化来源**

把 `_build_explanation(...)` 改成：

```python
def _build_explanation(script_dir: Path, candidate: dict[str, Any]) -> dict[str, Any]:
    started_at = time.perf_counter()
    local_payload = _load_local_explanation(script_dir, candidate)
    if local_payload is not None:
        _ensure_required_fields(local_payload)
        return _build_metadata_payload(
            payload=local_payload,
            explanation_source=SOURCE_BRIDGE_DATA,
            started_at=started_at,
        )

    real_payload = _build_real_tool_explanation(script_dir, candidate)
    if real_payload is not None:
        _ensure_required_fields(real_payload)
        return _build_metadata_payload(
            payload={key: value for key, value in real_payload.items() if key not in {"_used_upstream_tools", "_tool_errors"}},
            explanation_source=SOURCE_UPSTREAM_TOOLS,
            started_at=started_at,
            used_upstream_tools=list(real_payload.get("_used_upstream_tools") or []),
            tool_errors=list(real_payload.get("_tool_errors") or []),
        )

    payload = _build_heuristic_explanation(candidate)
    _ensure_required_fields(payload)
    return _build_metadata_payload(
        payload=payload,
        explanation_source=SOURCE_HEURISTIC_FALLBACK,
        started_at=started_at,
        tool_errors=list((candidate.get("_tool_errors") or [])),
    )
```

- [ ] **Step 4: 让真实工具路径返回成功工具与错误摘要**

修改 `_build_real_tool_explanation(...)`：

```python
    succeeded_tools: list[str] = []
    tool_errors: list[str] = []
```

在每个工具成功返回有效 `dict` 时追加：

```python
    succeeded_tools.append("HotMoneyTool")
```

同理为 `ChipAnalysisTool`、`BigDealAnalysisTool`。

在每个工具异常或 `_coerce_tool_result` 返回 error 时归一化错误：

```python
    if hot_error:
        tool_errors.append(f"HotMoneyTool: {hot_error}")
```

返回 payload 时加入私有元数据键：

```python
    payload["_used_upstream_tools"] = succeeded_tools
    payload["_tool_errors"] = tool_errors
```

在全部失败时让 fallback 也能拿到错误列表：

```python
    candidate["_tool_errors"] = tool_errors
    if not succeeded:
        return None
```

- [ ] **Step 5: 运行 bridge 针对性测试，确认转绿**

Run:

```bash
python -m pytest tests/test_shortline_bridge_templates.py -k "bridge_data_source or upstream_tools_when_available or partial_upstream_tool_failures or falls_back_when_real_upstream_tools_fail" -q
```

Expected:

- 新增 bridge 元数据测试全部 PASS

### Task 3: Add Failing Tests For Adapter, Schemas, Summary, And Report

**Files:**
- Modify: `tests/test_shortline_hub_orchestrator.py`
- Modify: `tests/test_shortline_hub_cli.py`
- Verify: `tests/test_shortline_hub_orchestrator.py`
- Verify: `tests/test_shortline_hub_cli.py`

- [ ] **Step 1: 为 adapter 兼容新字段与旧字段增加失败测试**

在 `tests/test_shortline_hub_orchestrator.py` 新增：

```python
def test_fingenius_process_adapter_reads_metadata_from_external_script(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "fg_mock.py"
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": "metadata hot",
    "big_deal_summary": "metadata big",
    "chip_commentary": "metadata chip",
    "sentiment_commentary": "metadata sentiment",
    "risk_commentary": "metadata risk",
    "short_term_view": "metadata view",
    "confidence_label": "high",
    "protocol_version": "shortline_fg_v1",
    "explanation_source": "upstream_tools",
    "used_upstream_tools": ["HotMoneyTool", "BigDealAnalysisTool"],
    "tool_error_count": 1,
    "tool_errors": ["ChipAnalysisTool: chip failed"],
    "explain_elapsed_ms": 47,
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    adapter = FinGeniusProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=sys.executable,
            script_path=script_path,
            runtime_dir=tmp_path / "runtime",
        )
    )
    candidate = ShortlineCandidate(
        candidate_id="2026-05-02-600001",
        symbol="600001",
        name="sample",
        trade_date="2026-05-02",
        scan_source="wondertrader_process",
        trigger_type="limit_up_relay",
        trigger_reason="mock",
        trigger_score=91.0,
        price=8.88,
        change_pct=9.91,
        volume_ratio=2.3,
        turnover_rate=6.2,
        board_name="steel",
        risk_flags=[],
    )

    result = adapter.explain_candidate(candidate)

    assert result.protocol_version == "shortline_fg_v1"
    assert result.explanation_source == "upstream_tools"
    assert result.used_upstream_tools == ["HotMoneyTool", "BigDealAnalysisTool"]
    assert result.tool_error_count == 1
    assert result.tool_errors == ["ChipAnalysisTool: chip failed"]
    assert result.explain_elapsed_ms == 47
```

再新增旧格式兼容测试：

```python
def test_fingenius_process_adapter_defaults_legacy_unknown_for_old_output(
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "fg_mock_old.py"
    script_path.write_text(
        """
import json
import sys
from pathlib import Path

request_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
payload = json.loads(request_path.read_text(encoding="utf-8"))
candidate = payload["candidate"]
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": "old hot",
    "big_deal_summary": "old big",
    "chip_commentary": "old chip",
    "sentiment_commentary": "old sentiment",
    "risk_commentary": "old risk",
    "short_term_view": "old view",
    "confidence_label": "medium",
}
output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
""".strip(),
        encoding="utf-8",
    )

    adapter = FinGeniusProcessAdapter(
        config=ProcessAdapterConfig(
            python_executable=sys.executable,
            script_path=script_path,
            runtime_dir=tmp_path / "runtime",
        )
    )
    candidate = ShortlineCandidate(
        candidate_id="2026-05-02-600001",
        symbol="600001",
        name="sample",
        trade_date="2026-05-02",
        scan_source="wondertrader_process",
        trigger_type="limit_up_relay",
        trigger_reason="mock",
        trigger_score=91.0,
        price=8.88,
        change_pct=9.91,
        volume_ratio=2.3,
        turnover_rate=6.2,
        board_name="steel",
        risk_flags=[],
    )

    result = adapter.explain_candidate(candidate)

    assert result.protocol_version == ""
    assert result.explanation_source == "legacy_unknown"
    assert result.used_upstream_tools == []
    assert result.tool_error_count == 0
    assert result.tool_errors == []
    assert result.explain_elapsed_ms == 0
```

- [ ] **Step 2: 为 orchestrator 聚合 summary 增加失败测试**

新增：

```python
def test_orchestrator_summary_includes_explanation_source_and_elapsed_metrics() -> None:
    class _Scanner:
        def scan_candidates(self, *, trade_date: str, top_n: int) -> list[ShortlineCandidate]:
            return [
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300001",
                    symbol="300001",
                    name="A",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="momentum_breakout",
                    trigger_reason="mock",
                    trigger_score=91.0,
                    price=21.5,
                    change_pct=7.6,
                    volume_ratio=2.0,
                    turnover_rate=5.1,
                    board_name="power",
                    risk_flags=[],
                ),
                ShortlineCandidate(
                    candidate_id=f"{trade_date}-300002",
                    symbol="300002",
                    name="B",
                    trade_date=trade_date,
                    scan_source="wondertrader_process",
                    trigger_type="momentum_breakout",
                    trigger_reason="mock",
                    trigger_score=88.0,
                    price=18.5,
                    change_pct=5.6,
                    volume_ratio=1.6,
                    turnover_rate=4.1,
                    board_name="power",
                    risk_flags=[],
                ),
            ][:top_n]

    class _Explainer:
        def __init__(self) -> None:
            self.calls = 0

        def explain_candidate(self, candidate: ShortlineCandidate) -> ShortlineExplanation:
            self.calls += 1
            if self.calls == 1:
                return ShortlineExplanation(
                    candidate_id=candidate.candidate_id,
                    hot_money_summary="hot A",
                    big_deal_summary="big A",
                    chip_commentary="chip A",
                    sentiment_commentary="sentiment A",
                    risk_commentary="risk A",
                    short_term_view="view A",
                    confidence_label="high",
                    protocol_version="shortline_fg_v1",
                    explanation_source="bridge_data",
                    used_upstream_tools=[],
                    tool_error_count=0,
                    tool_errors=[],
                    explain_elapsed_ms=12,
                )
            return ShortlineExplanation(
                candidate_id=candidate.candidate_id,
                hot_money_summary="hot B",
                big_deal_summary="big B",
                chip_commentary="chip B",
                sentiment_commentary="sentiment B",
                risk_commentary="risk B",
                short_term_view="view B",
                confidence_label="medium",
                protocol_version="shortline_fg_v1",
                explanation_source="upstream_tools",
                used_upstream_tools=["HotMoneyTool", "BigDealAnalysisTool"],
                tool_error_count=1,
                tool_errors=["ChipAnalysisTool: chip failed"],
                explain_elapsed_ms=34,
            )

    result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(
        run_id="summary_run",
        trade_date="2026-05-02",
        top_n=2,
    )

    summary = result.summary_dict()

    assert summary["explanation_source_counts"] == {
        "bridge_data": 1,
        "upstream_tools": 1,
    }
    assert summary["bridge_data_hit_count"] == 1
    assert summary["upstream_tools_hit_count"] == 1
    assert summary["heuristic_fallback_count"] == 0
    assert summary["legacy_unknown_count"] == 0
    assert summary["upstream_tool_hit_counts"] == {
        "BigDealAnalysisTool": 1,
        "HotMoneyTool": 1,
    }
    assert summary["tool_error_count"] == 1
    assert summary["total_explain_elapsed_ms"] == 46
    assert summary["avg_explain_elapsed_ms"] == 23
    assert summary["max_explain_elapsed_ms"] == 34
    assert summary["min_explain_elapsed_ms"] == 12
    assert summary["orchestrator_explain_elapsed_ms"] >= 0
```

- [ ] **Step 3: 为 CLI artifacts 和 Markdown 新内容增加失败测试**

在 `tests/test_shortline_hub_cli.py` 的 `test_shortline_hub_cli_process_mode_uses_external_scripts(...)` 中，把外部 mock 结果改成：

```python
result = {
    "candidate_id": candidate["candidate_id"],
    "hot_money_summary": "process hot",
    "big_deal_summary": "process big",
    "chip_commentary": "process chip",
    "sentiment_commentary": "process sentiment",
    "risk_commentary": "process risk",
    "short_term_view": "process short view",
    "confidence_label": "high",
    "protocol_version": "shortline_fg_v1",
    "explanation_source": "upstream_tools",
    "used_upstream_tools": ["HotMoneyTool"],
    "tool_error_count": 0,
    "tool_errors": [],
    "explain_elapsed_ms": 18,
}
```

并追加断言：

```python
    summary_payload = json.loads((tmp_path / "out" / "run_summary.json").read_text(encoding="utf-8"))
    combined_payload = json.loads(
        (tmp_path / "out" / "shortline_combined_results.json").read_text(encoding="utf-8")
    )
    assert summary_payload["explanation_source_counts"] == {"upstream_tools": 1}
    assert summary_payload["upstream_tool_hit_counts"] == {"HotMoneyTool": 1}
    assert summary_payload["orchestrator_explain_elapsed_ms"] >= 0
    assert combined_payload[0]["explanation_source"] == "upstream_tools"
    assert combined_payload[0]["used_upstream_tools"] == ["HotMoneyTool"]
    assert combined_payload[0]["explain_elapsed_ms"] == 18
    assert "FinGenius Explain Summary" in report_text
    assert "upstream_tools=1" in report_text
```

- [ ] **Step 4: 运行 adapter / orchestrator / CLI 针对性测试，确认先红**

Run:

```bash
python -m pytest tests/test_shortline_hub_orchestrator.py tests/test_shortline_hub_cli.py -k "metadata or summary_includes_explanation_source or process_mode_uses_external_scripts" -q
```

Expected:

- 新增测试至少有 1 个失败
- 失败原因是 schema / adapter / report 尚未实现新字段

### Task 4: Implement Schema, Adapter, Orchestrator, And Report Changes

**Files:**
- Modify: `src/shortline_hub/adapters/fingenius_adapter.py`
- Modify: `src/shortline_hub/schemas.py`
- Modify: `src/shortline_hub/orchestrator.py`
- Modify: `src/shortline_hub/report_builder.py`
- Verify: `tests/test_shortline_hub_orchestrator.py`
- Verify: `tests/test_shortline_hub_cli.py`

- [ ] **Step 1: 扩展 `ShortlineExplanation`、`ShortlineCombinedResult` 与 `ShortlineRunResult`**

在 `src/shortline_hub/schemas.py` 中更新 dataclass：

```python
@dataclass
class ShortlineExplanation:
    candidate_id: str
    hot_money_summary: str
    big_deal_summary: str
    chip_commentary: str
    sentiment_commentary: str
    risk_commentary: str
    short_term_view: str
    confidence_label: str
    protocol_version: str = ""
    explanation_source: str = "legacy_unknown"
    used_upstream_tools: list[str] = field(default_factory=list)
    tool_error_count: int = 0
    tool_errors: list[str] = field(default_factory=list)
    explain_elapsed_ms: int = 0
```

```python
@dataclass
class ShortlineCombinedResult:
    ...
    confidence_label: str
    protocol_version: str
    explanation_source: str
    used_upstream_tools: list[str]
    tool_error_count: int
    tool_errors: list[str]
    explain_elapsed_ms: int
```

```python
@dataclass
class ShortlineRunResult:
    ...
    combined_results: list[ShortlineCombinedResult]
    orchestrator_explain_elapsed_ms: int = 0
```

把 `summary_dict()` 改成显式聚合：

```python
    explanation_source_counts = Counter(item.explanation_source for item in self.explanations)
    upstream_tool_hit_counts = Counter(
        tool_name
        for item in self.explanations
        for tool_name in item.used_upstream_tools
    )
    elapsed_values = [max(0, int(item.explain_elapsed_ms or 0)) for item in self.explanations]
    total_explain_elapsed_ms = sum(elapsed_values)
```

并输出：

```python
            "explanation_source_counts": dict(explanation_source_counts),
            "bridge_data_hit_count": int(explanation_source_counts.get("bridge_data", 0)),
            "upstream_tools_hit_count": int(explanation_source_counts.get("upstream_tools", 0)),
            "heuristic_fallback_count": int(explanation_source_counts.get("heuristic_fallback", 0)),
            "legacy_unknown_count": int(explanation_source_counts.get("legacy_unknown", 0)),
            "upstream_tool_hit_counts": dict(upstream_tool_hit_counts),
            "explanation_success_count": len(self.explanations),
            "tool_error_count": sum(max(0, int(item.tool_error_count or 0)) for item in self.explanations),
            "total_explain_elapsed_ms": total_explain_elapsed_ms,
            "avg_explain_elapsed_ms": int(total_explain_elapsed_ms / len(elapsed_values)) if elapsed_values else 0,
            "max_explain_elapsed_ms": max(elapsed_values) if elapsed_values else 0,
            "min_explain_elapsed_ms": min(elapsed_values) if elapsed_values else 0,
            "orchestrator_explain_elapsed_ms": max(0, int(self.orchestrator_explain_elapsed_ms or 0)),
```

- [ ] **Step 2: 让 adapter 读取新字段，并对旧输出保持 `legacy_unknown`**

在 `src/shortline_hub/adapters/fingenius_adapter.py` 的 `FinGeniusProcessAdapter.explain_candidate(...)` 中改为：

```python
        payload = read_json_payload(output_path)
        used_upstream_tools = payload.get("used_upstream_tools") or []
        if isinstance(used_upstream_tools, str):
            used_upstream_tools = [used_upstream_tools]
        tool_errors = payload.get("tool_errors") or []
        if isinstance(tool_errors, str):
            tool_errors = [tool_errors]
        return ShortlineExplanation(
            candidate_id=str(payload["candidate_id"]),
            hot_money_summary=str(payload["hot_money_summary"]),
            big_deal_summary=str(payload["big_deal_summary"]),
            chip_commentary=str(payload["chip_commentary"]),
            sentiment_commentary=str(payload["sentiment_commentary"]),
            risk_commentary=str(payload["risk_commentary"]),
            short_term_view=str(payload["short_term_view"]),
            confidence_label=str(payload["confidence_label"]),
            protocol_version=str(payload.get("protocol_version") or ""),
            explanation_source=str(payload.get("explanation_source") or "legacy_unknown"),
            used_upstream_tools=[str(item).strip() for item in used_upstream_tools if str(item).strip()],
            tool_error_count=max(0, int(payload.get("tool_error_count") or 0)),
            tool_errors=[str(item).strip() for item in tool_errors if str(item).strip()],
            explain_elapsed_ms=max(0, int(payload.get("explain_elapsed_ms") or 0)),
        )
```

- [ ] **Step 3: 在 orchestrator 中记录总解释耗时并透传 explanation 元数据**

在 `src/shortline_hub/orchestrator.py` 中加入：

```python
from time import perf_counter
```

修改 `run(...)`：

```python
        explain_started_at = perf_counter()
        explanations = [self.explainer.explain_candidate(candidate) for candidate in candidates]
        orchestrator_explain_elapsed_ms = max(0, int((perf_counter() - explain_started_at) * 1000))
```

构造 `ShortlineRunResult(...)` 时加入：

```python
            orchestrator_explain_elapsed_ms=orchestrator_explain_elapsed_ms,
```

修改 `_combine_candidate_with_explanation(...)` 末尾追加：

```python
            protocol_version=explanation.protocol_version,
            explanation_source=explanation.explanation_source,
            used_upstream_tools=list(explanation.used_upstream_tools),
            tool_error_count=explanation.tool_error_count,
            tool_errors=list(explanation.tool_errors),
            explain_elapsed_ms=explanation.explain_elapsed_ms,
```

- [ ] **Step 4: 在 report builder 中增加 ASCII 稳定摘要段和逐票来源信息**

在 `src/shortline_hub/report_builder.py` 中新增 helper：

```python
def _format_ms(value: int) -> str:
    return f"{max(0, int(value or 0))}ms"


def _build_explanation_source_mix(result: ShortlineRunResult) -> str:
    return _format_counter(Counter(item.explanation_source for item in result.explanations))


def _build_upstream_tool_mix(result: ShortlineRunResult) -> str:
    return _format_counter(
        Counter(tool_name for item in result.explanations for tool_name in item.used_upstream_tools)
    )
```

在概览段后追加新的 ASCII section：

```python
        "## FinGenius Explain Summary",
        "",
        f"- explanation_sources: {_build_explanation_source_mix(result)}",
        f"- upstream_tools: {_build_upstream_tool_mix(result)}",
        f"- bridge_elapsed_total: {_format_ms(result.summary_dict().get('total_explain_elapsed_ms', 0))}",
        f"- orchestrator_elapsed_total: {_format_ms(result.orchestrator_explain_elapsed_ms)}",
        "",
```

在逐票详情里追加：

```python
                f"- explain_source: {item.explanation_source}",
                f"- upstream_tools: {', '.join(item.used_upstream_tools) if item.used_upstream_tools else '-'}",
                f"- explain_elapsed: {_format_ms(item.explain_elapsed_ms)}",
                f"- tool_errors: {' | '.join(item.tool_errors) if item.tool_errors else '-'}",
```

- [ ] **Step 5: 运行 adapter / orchestrator / CLI 测试，确认转绿**

Run:

```bash
python -m pytest tests/test_shortline_hub_orchestrator.py tests/test_shortline_hub_cli.py -k "metadata or summary_includes_explanation_source or process_mode_uses_external_scripts" -q
```

Expected:

- 新增 adapter / orchestrator / CLI 测试全部 PASS

### Task 5: Update Docs, Run Full Verification, And Execute Real Smoke

**Files:**
- Modify: `scripts/bridges/README.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`
- Optional modify after smoke: `D:\bb\FinGenius\bridge\fg_explain_candidate.py`
- Verify: shortline pytest suite + real smoke command

- [ ] **Step 1: 更新 bridge README 协议说明**

在 `scripts/bridges/README.md` 的 `FinGenius` 输出协议示例中追加：

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
  "explain_elapsed_ms": 28
}
```

并补一段说明：

```markdown
- `explanation_source`
  - `bridge_data`：命中本地 bridge_data
  - `upstream_tools`：命中至少一个真实 upstream 工具
  - `heuristic_fallback`：真实工具未产出有效结果，回退启发式解释
  - `legacy_unknown`：仅存在于仓库内 adapter 对旧外部输出的兼容回填，不应由新 bridge 主动输出
```

- [ ] **Step 2: 更新 AI 修改日志与 changelog**

在 `docs/AI_MODIFICATION_LOG.md` 新增一节，至少记录：

```markdown
## 2026-05-02 (shortline FinGenius batch instrumentation + smoke)

- Scope: add structured explanation-source / tool-hit / elapsed metadata to the existing shortline FinGenius batch path, then validate with a small real process-mode smoke.
- Changes:
  - Updated `scripts/bridges/shortline_fingenius_bridge_template.py`
  - Updated `src/shortline_hub/adapters/fingenius_adapter.py`
  - Updated `src/shortline_hub/schemas.py`
  - Updated `src/shortline_hub/orchestrator.py`
  - Updated `src/shortline_hub/report_builder.py`
  - Updated regression tests for bridge, orchestrator, and CLI artifacts
- Verification:
  - [record the exact pytest commands you actually ran]
  - [record the exact process-mode smoke command and observed output]
```

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 下追加扁平条目：

```markdown
- [改进] `shortline_hub` 现在会在 `FinGenius` 解释产物中输出结构化来源、真实工具命中和耗时统计，并在 `run_summary.json` 与 `shortline_report.md` 中展示批量解释摘要。
```

- [ ] **Step 3: 运行短线相关完整回归**

Run:

```bash
python -m pytest tests/test_fingenius_bridge_loguru_fallback.py tests/test_fingenius_bridge_bom.py tests/test_shortline_bridge_templates.py tests/test_shortline_hub_orchestrator.py tests/test_shortline_hub_cli.py tests/test_shortline_bridge_check.py tests/test_shortline_bridge_data_compare.py -q
```

Expected:

- 全部 PASS

- [ ] **Step 4: 做最小语法检查**

Run:

```bash
python -m py_compile scripts/bridges/shortline_fingenius_bridge_template.py src/shortline_hub/adapters/fingenius_adapter.py src/shortline_hub/schemas.py src/shortline_hub/orchestrator.py src/shortline_hub/report_builder.py
```

Expected:

- 无输出
- 返回码 0

- [ ] **Step 5: 执行首轮小规模真实 smoke**

Run:

```bash
python scripts/run_shortline_hub.py --mode process --trade-date 2026-05-03 --top-n 3 --run-id shortline_fingenius_instrumented_smoke_20260502 --output-dir data/manual_runs/shortline_fingenius_instrumented_smoke_20260502 --wt-python-executable python --wt-script-path D:\bb\WonderTrader\bridge\wt_export_candidates.py --wt-workdir D:\bb\WonderTrader --wt-runtime-dir data/runtime/shortline_hub/wt_instrumented_smoke --fg-python-executable python --fg-script-path D:\bb\FinGenius\bridge\fg_explain_candidate.py --fg-workdir D:\bb\FinGenius --fg-runtime-dir data/runtime/shortline_hub/fg_instrumented_smoke
```

Expected:

- 进程退出码 0
- `data/manual_runs/shortline_fingenius_instrumented_smoke_20260502/run_summary.json` 存在
- `run_summary.json` 至少包含：
  - `explanation_source_counts`
  - `upstream_tool_hit_counts`
  - `orchestrator_explain_elapsed_ms`
- `shortline_combined_results.json` 每条至少包含：
  - `explanation_source`
  - `used_upstream_tools`
  - `tool_errors`
  - `explain_elapsed_ms`

- [ ] **Step 6: 若真实外部脚本未同步新字段，则做最小外部脚本同步并重跑 smoke**

仅在 Step 5 结果证明 `D:\bb\FinGenius\bridge\fg_explain_candidate.py` 仍输出旧协议时执行：

```python
# 外部脚本必须补齐与仓库模板一致的新增字段：
"protocol_version": "shortline_fg_v1",
"explanation_source": "...",
"used_upstream_tools": [...],
"tool_error_count": ...,
"tool_errors": [...],
"explain_elapsed_ms": ...,
```

重跑：

```bash
python scripts/run_shortline_hub.py --mode process --trade-date 2026-05-03 --top-n 3 --run-id shortline_fingenius_instrumented_smoke_20260502_rerun --output-dir data/manual_runs/shortline_fingenius_instrumented_smoke_20260502_rerun --wt-python-executable python --wt-script-path D:\bb\WonderTrader\bridge\wt_export_candidates.py --wt-workdir D:\bb\WonderTrader --wt-runtime-dir data/runtime/shortline_hub/wt_instrumented_smoke_rerun --fg-python-executable python --fg-script-path D:\bb\FinGenius\bridge\fg_explain_candidate.py --fg-workdir D:\bb\FinGenius --fg-runtime-dir data/runtime/shortline_hub/fg_instrumented_smoke_rerun
```

Expected:

- 重跑后产物具备完整新字段

## Self-Review

- Spec coverage:
  - 已覆盖 `legacy_unknown` 来源兼容、bridge 自报耗时、orchestrator 观测耗时、小规模 `top_n=3` 真实 smoke、协议版本字段预留、报告/JSON 留痕、文档更新与外部脚本最小同步。
- Placeholder scan:
  - 无 `TODO` / `TBD` / “implement later”。
- Type consistency:
  - 统一使用 `protocol_version / explanation_source / used_upstream_tools / tool_error_count / tool_errors / explain_elapsed_ms / orchestrator_explain_elapsed_ms`。
