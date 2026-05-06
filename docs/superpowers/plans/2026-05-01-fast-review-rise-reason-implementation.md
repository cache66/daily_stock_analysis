# Fast Review Rise Reason Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在每日快复盘焦点股输出里自动补充上涨原因摘要与标签，优先复用现有 `reason_summary / cause_tags` 字段和 `SignalCauseAnalysisService`。

**Architecture:** 仅增强 `scripts/run_fast_review_bundle.py` 的复盘解释层，不改 snapshot 落库和 `/signals`。实现时先从焦点股已有信号行复用原因字段，缺失时才调用 `SignalCauseAnalysisService.analyze_signal(...)` 补算，再把结果写入 `fast_review_strategy_focus.csv/.md` 和 `fast_review_summary.md`。

**Tech Stack:** Python, pytest, pandas, existing fast review bundle helpers, `SignalCauseAnalysisService`

---

## File Map

- Modify: `scripts/run_fast_review_bundle.py`
  - 新增焦点股上涨原因补全 helper
  - 扩展 `strategy_focus_rows` 字段
  - 扩展 Markdown / Summary 输出
- Modify: `tests/test_fast_review_daily_bundle.py`
  - 为原因复用、原因补算、导出展示补测试
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
  - 记录每日复盘新增上涨原因摘要能力
- Modify: `docs/LOCAL_STRATEGY_BASELINE.md`
  - 记录快复盘焦点层输出口径变化
- Modify: `docs/AI_MODIFICATION_LOG.md`
  - 记录本次实现留痕
- Modify: `docs/CHANGELOG.md`
  - 在 `[Unreleased]` 下追加扁平变更项

### Task 1: Add Failing Tests For Rise Reason Enrichment

**Files:**
- Modify: `tests/test_fast_review_daily_bundle.py`
- Verify: `tests/test_fast_review_daily_bundle.py`

- [ ] **Step 1: 写一个“已有 reason_summary 时直接复用”的失败测试**

```python
def test_build_strategy_focus_rows_reuses_existing_reason_fields() -> None:
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[
                {
                    "code": "600001",
                    "name": "ReuseA",
                    "overall_score": "36",
                    "reason_summary": "板块轮动带动走强",
                    "cause_tags": "sector_rotation,policy",
                    "industry_logic": "行业强势",
                    "news_logic": "消息面催化",
                    "technical_logic": "新高延续",
                }
            ],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["reason_summary"] == "板块轮动带动走强"
    assert rows[0]["cause_tags"] == "sector_rotation,policy"
    assert rows[0]["industry_logic"] == "行业强势"
    assert rows[0]["news_logic"] == "消息面催化"
    assert rows[0]["technical_logic"] == "新高延续"
```

- [ ] **Step 2: 写一个“缺失时触发补算”的失败测试**

```python
def test_build_strategy_focus_rows_enriches_missing_reason_fields(monkeypatch) -> None:
    class _FakeCauseService:
        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            return {
                "reason_summary": "业绩释放叠加板块共振",
                "cause_tags": ["earnings", "sector_rotation"],
                "industry_logic": "行业景气上行",
                "news_logic": "未检索到稳定新闻，按中性处理",
                "technical_logic": "强势突破延续",
            }

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _FakeCauseService)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[{"code": "600001", "name": "EnrichA", "overall_score": "36"}],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["reason_summary"] == "业绩释放叠加板块共振"
    assert rows[0]["cause_tags"] == "earnings,sector_rotation"
    assert rows[0]["industry_logic"] == "行业景气上行"
```

- [ ] **Step 3: 写一个“补算失败时 fail-open”的失败测试**

```python
def test_build_strategy_focus_rows_fails_open_when_reason_enrichment_errors(monkeypatch) -> None:
    class _BoomCauseService:
        def analyze_signal(self, stock_code, stock_name, *, signal_type, metrics_payload=None):
            raise RuntimeError("boom")

    monkeypatch.setattr(fast_bundle, "SignalCauseAnalysisService", _BoomCauseService)
    signal_results = [
        fast_bundle.SignalResult(
            key="trend_leader",
            signal_type="trend_leader_unified",
            label="trend",
            rows=[{"code": "600001", "name": "FallbackA", "overall_score": "36"}],
            csv_path=Path("trend.csv"),
        )
    ]

    rows = fast_bundle._build_strategy_focus_rows(signal_results)

    assert rows[0]["code"] == "600001"
    assert rows[0]["reason_summary"] in {"", rows[0]["focus_reason"]}
```

- [ ] **Step 4: 写一个 Markdown / Summary 展示失败测试**

```python
def test_build_summary_markdown_includes_rise_reason_section(tmp_path: Path) -> None:
    focus_rows = [
        {
            "code": "600001",
            "name": "CoreA",
            "tier": "core",
            "priority_score": 121.5,
            "signal_keys": "trend_leader,hundred_day_high",
            "trend_hundred_relation": "intersection",
            "focus_reason": "trend+hundred",
            "reason_summary": "行业爆发带动龙头继续走强",
            "cause_tags_zh": "板块轮动/政策",
        }
    ]

    summary = fast_bundle._build_summary_markdown(
        snapshot_date=date(2026, 4, 20),
        signal_results=[],
        skipped_signals=[],
        unified_csv=tmp_path / "fast_review_candidates.csv",
        resonance_csv=tmp_path / "fast_review_resonance.csv",
        resonance_md=tmp_path / "fast_review_resonance.md",
        resonance_rows=[],
        suggested_windows="1,3,5,10",
        strategy_focus_csv=tmp_path / "fast_review_strategy_focus.csv",
        strategy_focus_md=tmp_path / "fast_review_strategy_focus.md",
        strategy_focus_rows=focus_rows,
    )

    assert "强势股上涨原因摘要" in summary
    assert "行业爆发带动龙头继续走强" in summary
    assert "板块轮动/政策" in summary
```

- [ ] **Step 5: 运行针对性测试，确认先红**

Run:

```bash
python -m pytest tests/test_fast_review_daily_bundle.py -k "reason_fields or rise_reason_section" -v
```

Expected:

- 至少 1 个新增测试失败
- 失败原因是字段/逻辑尚未实现，而不是语法错误

### Task 2: Implement Minimal Rise Reason Enrichment In Fast Review Bundle

**Files:**
- Modify: `scripts/run_fast_review_bundle.py`
- Verify: `tests/test_fast_review_daily_bundle.py`

- [ ] **Step 1: 在 bundle 中引入原因服务与标签映射 helper**

```python
from src.services.signal_cause_analysis_service import SignalCauseAnalysisService
```

```python
CAUSE_TAG_LABELS = {
    "earnings": "业绩",
    "policy": "政策",
    "price_increase": "涨价",
    "supply_demand": "供需",
    "sector_rotation": "板块轮动",
    "overseas_theme": "海外映射",
    "other": "其他",
}
```

- [ ] **Step 2: 增加原因字段标准化与复用 helper**

```python
def _normalize_cause_tags_text(value: Any) -> str:
    if isinstance(value, list):
        items = [str(item or "").strip() for item in value if str(item or "").strip()]
        return ",".join(items)
    text = str(value or "").strip()
    return text


def _cause_tags_to_zh_text(value: Any) -> str:
    raw = _normalize_cause_tags_text(value)
    if not raw:
        return ""
    labels = []
    for item in raw.split(","):
        key = str(item or "").strip()
        if not key:
            continue
        labels.append(CAUSE_TAG_LABELS.get(key, key))
    return "/".join(labels)
```

```python
def _pick_focus_reason_source_row(rows_by_signal: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    for signal_key in (SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS):
        row = rows_by_signal.get(signal_key)
        if isinstance(row, dict) and row:
            return row
    for row in rows_by_signal.values():
        if isinstance(row, dict) and row:
            return row
    return {}
```

- [ ] **Step 3: 增加焦点股原因补全 helper，保持 fail-open**

```python
def _resolve_focus_rise_reason_fields(
    *,
    code: str,
    name: str,
    rows_by_signal: Dict[str, Dict[str, Any]],
    focus_reason: str,
) -> Dict[str, str]:
    reason_summary = str(_first_non_empty_field(rows_by_signal, "reason_summary", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS]) or "").strip()
    cause_tags = _normalize_cause_tags_text(
        _first_non_empty_field(rows_by_signal, "cause_tags", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS])
    )
    industry_logic = str(_first_non_empty_field(rows_by_signal, "industry_logic", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS]) or "").strip()
    news_logic = str(_first_non_empty_field(rows_by_signal, "news_logic", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS]) or "").strip()
    technical_logic = str(_first_non_empty_field(rows_by_signal, "technical_logic", [SIGNAL_TREND_LEADER, SIGNAL_HUNDRED_DAY_HIGH, SIGNAL_EARNINGS]) or "").strip()

    if reason_summary and cause_tags:
        return {
            "reason_summary": reason_summary,
            "cause_tags": cause_tags,
            "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
            "industry_logic": industry_logic,
            "news_logic": news_logic,
            "technical_logic": technical_logic,
        }

    source_row = _pick_focus_reason_source_row(rows_by_signal)
    if not source_row:
        return {
            "reason_summary": focus_reason,
            "cause_tags": cause_tags,
            "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
            "industry_logic": industry_logic,
            "news_logic": news_logic,
            "technical_logic": technical_logic,
        }

    try:
        payload = SignalCauseAnalysisService(enable_news_search=False).analyze_signal(
            code,
            name,
            signal_type=str(source_row.get("signal_type") or SIGNAL_TREND_LEADER),
            metrics_payload=dict(source_row),
        )
    except Exception:
        payload = {}

    reason_summary = str(payload.get("reason_summary", "") or reason_summary or focus_reason).strip()
    cause_tags = _normalize_cause_tags_text(payload.get("cause_tags") or cause_tags)
    industry_logic = str(payload.get("industry_logic", "") or industry_logic).strip()
    news_logic = str(payload.get("news_logic", "") or news_logic).strip()
    technical_logic = str(payload.get("technical_logic", "") or technical_logic).strip()
    return {
        "reason_summary": reason_summary,
        "cause_tags": cause_tags,
        "cause_tags_zh": _cause_tags_to_zh_text(cause_tags),
        "industry_logic": industry_logic,
        "news_logic": news_logic,
        "technical_logic": technical_logic,
    }
```

- [ ] **Step 4: 在 `_build_strategy_focus_rows(...)` 合并原因字段**

```python
reason_fields = _resolve_focus_rise_reason_fields(
    code=code,
    name=str(group.get("name") or code),
    rows_by_signal=rows_by_signal,
    focus_reason=focus_reason,
)
```

并把以下字段写入焦点行：

```python
"reason_summary": reason_fields["reason_summary"],
"cause_tags": reason_fields["cause_tags"],
"cause_tags_zh": reason_fields["cause_tags_zh"],
"industry_logic": reason_fields["industry_logic"],
"news_logic": reason_fields["news_logic"],
"technical_logic": reason_fields["technical_logic"],
```

- [ ] **Step 5: 扩展 CSV / Markdown / Summary 输出**

在 `fast_review_strategy_focus.csv` 列集合中新增：

```python
"reason_summary",
"cause_tags",
"cause_tags_zh",
"industry_logic",
"news_logic",
"technical_logic",
```

在焦点 Markdown 表中新增：

```python
| code | name | score | signals | relation | reason | rise_reason | tags |
```

在 summary 中新增一个精简小节：

```python
## 强势股上涨原因摘要
```

只展示 `core` 全部和前几条 `watch`。

- [ ] **Step 6: 运行针对性测试，确认转绿**

Run:

```bash
python -m pytest tests/test_fast_review_daily_bundle.py -k "reason_fields or rise_reason_section" -v
```

Expected:

- 新增测试全部 PASS

### Task 3: Regression Verification And Strategy Docs Trace

**Files:**
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Modify: `docs/LOCAL_STRATEGY_BASELINE.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`
- Verify: `tests/test_fast_review_daily_bundle.py`

- [ ] **Step 1: 追加快复盘原因摘要相关回归测试验证**

Run:

```bash
python -m pytest tests/test_fast_review_daily_bundle.py -v
```

Expected:

- 相关 fast review 测试通过
- 旧的焦点排序 / watchlist-only 行为不回归

- [ ] **Step 2: 记录策略目录与基线**

在 `docs/LOCAL_STRATEGY_CATALOG.md` 追加：

- 每日快复盘焦点股现在会输出上涨原因摘要与标签
- 优先复用已有策略行上的 `reason_summary / cause_tags`
- 缺失时才补算，且不写回 snapshot

在 `docs/LOCAL_STRATEGY_BASELINE.md` 追加：

- `fast_review_strategy_focus.csv/.md`
- `fast_review_summary.md`
- 输出口径新增上涨原因摘要层

- [ ] **Step 3: 记录 AI 修改日志与 changelog**

在 `docs/AI_MODIFICATION_LOG.md` 追加本次实现：

- fast review focus 层接入 rise-reason enrichment
- CSV / Markdown / Summary 输出新增原因字段

在 `docs/CHANGELOG.md` 的 `[Unreleased]` 追加扁平项：

```markdown
- [改进] `scripts/run_fast_review_bundle.py` 现在会为 `fast_review_strategy_focus.csv/md` 和 `fast_review_summary.md` 中的强势焦点股补充上涨原因摘要与标签，优先复用已有 `reason_summary / cause_tags`，缺失时再做轻量补算。
```

- [ ] **Step 4: 做最小语法检查**

Run:

```bash
python -m py_compile scripts/run_fast_review_bundle.py
```

Expected:

- 无输出
- 返回码 0

## Self-Review

- Spec coverage:
  - 焦点股范围、字段复用、缺失补算、fail-open、CSV/Markdown/Summary 输出、策略文档留痕均已覆盖
- Placeholder scan:
  - 无 `TODO/TBD/implement later`
- Type consistency:
  - 统一使用 `reason_summary / cause_tags / cause_tags_zh / industry_logic / news_logic / technical_logic`
