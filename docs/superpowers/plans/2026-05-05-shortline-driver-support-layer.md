# Shortline Driver Support Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## 中文摘要

**目标：** 给 `shortline_hub` 增加一层“逻辑支撑分层”，让每只短线票除了现有的形态、资金、板块、风险标签之外，再新增：
- `driver_type`
- `driver_confidence`
- `driver_support_score`
- `driver_evidence`

第一阶段先解决“这只票为什么强”的表达问题，不先大改现有短线主流程。  
第二阶段再把这层逻辑支撑接进 `top_pick / watchlist / high_risk_mover` 的分层规则里，避免纯资金接力票长期挤进 `top_pick`。

**核心分类：**
- `earnings_driver`：业绩超预期、订单、利润兑现
- `price_cycle_driver`：涨价链、资源品、价格传导
- `industry_breakout_driver`：产业爆发、政策催化、主线扩散
- `theme_relay_driver`：题材接力，有方向但证据较弱
- `flow_only`：主要是资金接力、形态强，但缺少硬逻辑

**实现原则：**
- 不推翻现有 `WonderTrader + FinGenius + shortline_hub` 流程
- 先用现有字段做一个确定性分类器，不先引入新外部依赖
- 先加标签和报告，再决定是否改 tier 规则
- 用 TDD 做：先写测试，再补实现，再跑真实回放验证

## 中文任务总览

- **Task 1：补 schema 字段**
  在 `ShortlineCombinedResult` 上新增 `driver_type / driver_confidence / driver_support_score / driver_evidence`。

- **Task 2：新增逻辑支撑分类器**
  新建 `src/shortline_hub/driver_support.py`，从 `board_name / short_term_view / sentiment_commentary / hot_money_summary / trigger_reason` 里提取关键词，给每只票打逻辑支撑类型。

- **Task 3：把分类器接入 orchestrator**
  在 `src/shortline_hub/orchestrator.py` 里把分类结果写回 `combined_results`，并把 `driver_support_score` 叠加到当前短线综合分上。

- **Task 4：把新字段写进报告和快照**
  在 `report_builder.py` 和 `snapshot_sync.py` 里把 `driver_type / driver_confidence / driver_evidence` 暴露出来，让报告能直接看出“是业绩驱动还是纯资金接力”。

- **Task 5：第二阶段再改分层**
  只在标签验证靠谱之后，才把 `flow_only` 默认压到 `watchlist`，把有逻辑支撑的票保留在 `top_pick`。

- **Task 6：回归测试 + 实盘回放验证**
  跑定向 pytest、`py_compile`，再用 `2026-05-04` 的真实短线回放做一次验收。

**Goal:** Add a logic-support layer to `shortline_hub` so each shortline candidate is classified as `earnings_driver / price_cycle_driver / industry_breakout_driver / theme_relay_driver / flow_only`, then use that classification to improve shortline reports first and review-tier rules second.

**Architecture:** Keep the existing shortline pipeline intact: `WonderTrader` still finds candidates and `FinGenius` still supplies commentary. Add a deterministic post-explanation classifier inside `shortline_hub` that derives `driver_type`, `driver_confidence`, `driver_evidence`, and `driver_support_score` from existing fields plus optional same-day snapshot context. Roll this out in two stages: first report-only labeling, then tier gating/boosting once the labels look correct on real runs.

**Tech Stack:** Python 3.10, dataclasses, existing `shortline_hub` orchestrator/report builder/test suite, existing signal snapshot store.

---

## File Map

**Create**
- `src/shortline_hub/driver_support.py`
- `tests/test_shortline_driver_support.py`

**Modify**
- `src/shortline_hub/schemas.py`
- `src/shortline_hub/orchestrator.py`
- `src/shortline_hub/report_builder.py`
- `src/shortline_hub/snapshot_sync.py`
- `tests/test_shortline_daily_review_layers.py`
- `tests/test_shortline_hub_orchestrator.py`
- `docs/CHANGELOG.md`
- `docs/AI_MODIFICATION_LOG.md`
- `docs/LOCAL_STRATEGY_CATALOG.md`

**Do not modify in phase 1 unless blocked**
- `src/shortline_hub/adapters/fingenius_adapter.py`
- `src/shortline_hub/wondertrader_real_engine.py`
- `scripts/run_shortline_hub.py`

---

### Task 1: Add driver-support schema fields

**Files:**
- Modify: `src/shortline_hub/schemas.py`
- Test: `tests/test_shortline_hub_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add a test that builds one `ShortlineCombinedResult` through the orchestrator and asserts the new fields exist on the result and survive `to_dict()`:

```python
assert item.driver_type == "flow_only"
assert item.driver_confidence == "low"
assert item.driver_support_score == 0.0
assert item.driver_evidence == []
payload = item.to_dict()
assert payload["driver_type"] == "flow_only"
assert payload["driver_evidence"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.10 -m pytest tests/test_shortline_hub_orchestrator.py -q -k driver_type`

Expected: FAIL because `ShortlineCombinedResult` has no `driver_type` fields yet.

- [ ] **Step 3: Write minimal implementation**

Add these fields to `ShortlineCombinedResult`:

```python
driver_type: str = "flow_only"
driver_confidence: str = "low"
driver_support_score: float = 0.0
driver_evidence: list[str] = field(default_factory=list)
```

Do not change constructor call sites yet; rely on defaults.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.10 -m pytest tests/test_shortline_hub_orchestrator.py -q -k driver_type`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortline_hub/schemas.py tests/test_shortline_hub_orchestrator.py
git commit -m "feat: add shortline driver support fields"
```

---

### Task 2: Build a deterministic driver-support classifier

**Files:**
- Create: `src/shortline_hub/driver_support.py`
- Test: `tests/test_shortline_driver_support.py`

- [ ] **Step 1: Write the failing tests**

Add table-driven tests for these cases:

```python
def test_classify_driver_support_marks_earnings_driver():
    item = _build_item(
        board_name="半导体",
        short_term_view="业绩超预期，订单高增长",
        sentiment_commentary="一季报净利润大增",
        risk_flags=[],
    )
    result = classify_driver_support(item)
    assert result.driver_type == "earnings_driver"
    assert result.driver_confidence == "high"
    assert "业绩超预期" in result.driver_evidence


def test_classify_driver_support_marks_price_cycle_driver():
    item = _build_item(
        board_name="锂矿",
        short_term_view="涨价链扩散",
        sentiment_commentary="碳酸锂涨价、资源品传导",
        risk_flags=[],
    )
    result = classify_driver_support(item)
    assert result.driver_type == "price_cycle_driver"


def test_classify_driver_support_marks_industry_breakout_driver():
    item = _build_item(
        board_name="半导体",
        short_term_view="AI算力与产业链扩散",
        sentiment_commentary="产业催化持续发酵",
        risk_flags=[],
    )
    result = classify_driver_support(item)
    assert result.driver_type == "industry_breakout_driver"


def test_classify_driver_support_falls_back_to_flow_only():
    item = _build_item(
        board_name="专用机械",
        short_term_view="看承接和换手",
        sentiment_commentary="暂无明确催化",
        risk_flags=[],
    )
    result = classify_driver_support(item)
    assert result.driver_type == "flow_only"
    assert result.driver_confidence == "low"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -3.10 -m pytest tests/test_shortline_driver_support.py -q`

Expected: FAIL because classifier module does not exist.

- [ ] **Step 3: Write minimal implementation**

Create `src/shortline_hub/driver_support.py` with:

```python
from dataclasses import dataclass


@dataclass
class DriverSupportAssessment:
    driver_type: str
    driver_confidence: str
    driver_support_score: float
    driver_evidence: list[str]


def classify_driver_support(item) -> DriverSupportAssessment:
    text = " ".join(
        [
            str(item.board_name or ""),
            str(item.short_term_view or ""),
            str(item.sentiment_commentary or ""),
            str(item.hot_money_summary or ""),
            str(item.trigger_reason or ""),
        ]
    )
    # keyword buckets: earnings / price cycle / industry breakout / theme relay
    ...
```

Use a simple keyword-map approach first:
- earnings: `业绩`, `净利润`, `预增`, `季报`, `订单`
- price cycle: `涨价`, `提价`, `碳酸锂`, `铜`, `稀土`, `化工`, `资源品`
- industry breakout: `AI`, `算力`, `半导体`, `机器人`, `低空`, `光模块`, `政策催化`, `产业链`
- theme relay: `题材`, `扩散`, `主线`, `映射`

Return:
- `earnings_driver` => score `18.0`, confidence `high`
- `price_cycle_driver` => score `16.0`, confidence `high`
- `industry_breakout_driver` => score `14.0`, confidence `medium`
- `theme_relay_driver` => score `8.0`, confidence `medium`
- `flow_only` => score `0.0`, confidence `low`

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -3.10 -m pytest tests/test_shortline_driver_support.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortline_hub/driver_support.py tests/test_shortline_driver_support.py
git commit -m "feat: add shortline driver support classifier"
```

---

### Task 3: Wire driver support into orchestrator results

**Files:**
- Modify: `src/shortline_hub/orchestrator.py`
- Test: `tests/test_shortline_hub_orchestrator.py`

- [ ] **Step 1: Write the failing test**

Add an orchestrator test that verifies the classifier is applied after explanation merge:

```python
result = ShortlineHubOrchestrator(scanner=_Scanner(), explainer=_Explainer()).run(...)
item = result.combined_results[0]
assert item.driver_type == "earnings_driver"
assert item.driver_support_score > 0
assert item.driver_evidence != []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `py -3.10 -m pytest tests/test_shortline_hub_orchestrator.py -q -k driver_support`

Expected: FAIL because orchestrator does not populate the new fields.

- [ ] **Step 3: Write minimal implementation**

In `_rank_and_layer_results(...)`, insert driver-support classification before board bonus / review-tier classification:

```python
from src.shortline_hub.driver_support import classify_driver_support

assessment = classify_driver_support(item)
item.driver_type = assessment.driver_type
item.driver_confidence = assessment.driver_confidence
item.driver_support_score = assessment.driver_support_score
item.driver_evidence = list(assessment.driver_evidence)
item.composite_score = cls._compute_base_composite_score(item) + item.driver_support_score
```

Keep the existing score formula intact; just add the support score after base score.

- [ ] **Step 4: Run test to verify it passes**

Run: `py -3.10 -m pytest tests/test_shortline_hub_orchestrator.py -q -k driver_support`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortline_hub/orchestrator.py tests/test_shortline_hub_orchestrator.py
git commit -m "feat: wire shortline driver support into orchestrator"
```

---

### Task 4: Show driver support in reports and JSON artifacts

**Files:**
- Modify: `src/shortline_hub/report_builder.py`
- Modify: `src/shortline_hub/snapshot_sync.py`
- Test: `tests/test_shortline_daily_review_layers.py`
- Test: `tests/test_shortline_snapshot_sync.py`

- [ ] **Step 1: Write the failing tests**

Add report assertions:

```python
assert "driver_type" in report
assert "industry_breakout_driver" in report
assert "driver_evidence" in report
```

Add snapshot assertions:

```python
assert db.rows[0]["metrics_payload"]["driver_type"] == "theme_relay_driver"
assert db.rows[0]["metrics_payload"]["driver_support_score"] == 8.0
assert db.rows[0]["cause_payload"]["driver_evidence"] == ["主线扩散"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
- `py -3.10 -m pytest tests/test_shortline_daily_review_layers.py -q -k driver`
- `py -3.10 -m pytest tests/test_shortline_snapshot_sync.py -q -k driver`

Expected: FAIL because artifacts do not contain those fields yet.

- [ ] **Step 3: Write minimal implementation**

Update report builder:
- add driver-support distribution to the summary section
- add `driver_type / driver_confidence / driver_evidence` to per-item detail blocks
- optionally add `driver_type` column to the compact candidate table

Update snapshot sync:

```python
"driver_type": item.driver_type,
"driver_confidence": item.driver_confidence,
"driver_support_score": float(item.driver_support_score or 0.0),
"driver_evidence": list(item.driver_evidence or []),
```

Update cause payload:

```python
"driver_evidence": list(item.driver_evidence or []),
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
- `py -3.10 -m pytest tests/test_shortline_daily_review_layers.py -q -k driver`
- `py -3.10 -m pytest tests/test_shortline_snapshot_sync.py -q -k driver`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortline_hub/report_builder.py src/shortline_hub/snapshot_sync.py tests/test_shortline_daily_review_layers.py tests/test_shortline_snapshot_sync.py
git commit -m "feat: expose shortline driver support in reports and snapshots"
```

---

### Task 5: Add phase-2 tier gating for `flow_only`

**Files:**
- Modify: `src/shortline_hub/orchestrator.py`
- Test: `tests/test_shortline_daily_review_layers.py`

- [ ] **Step 1: Write the failing tests**

Add two focused regressions:

```python
def test_flow_only_candidate_caps_to_watchlist():
    item = _build_real_style_candidate(driver_type="flow_only", ...)
    assert item.review_tier == "watchlist"


def test_industry_breakout_driver_can_stay_top_pick():
    item = _build_real_style_candidate(driver_type="industry_breakout_driver", ...)
    assert item.review_tier == "top_pick"
```

Optional third test for continuity exception:

```python
def test_flow_only_repeat_leader_can_keep_top_pick_when_streak_is_strong():
    item.tracking_appear_streak_days = 3
    item.board_core_rank = 1
    assert item.review_tier == "top_pick"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `py -3.10 -m pytest tests/test_shortline_daily_review_layers.py -q -k "flow_only or industry_breakout"`

Expected: FAIL because current tier logic ignores driver support.

- [ ] **Step 3: Write minimal implementation**

Modify `_classify_review_tier(...)` rules:
- keep all existing hard/soft data risk guards first
- after risk checks, add:

```python
if item.driver_type == "flow_only":
    if (
        float(item.composite_score or 0.0) >= 150.0
        and int(item.board_core_rank or 0) == 1
        and int(item.tracking_appear_streak_days or 0) >= 3
    ):
        return "top_pick"
    return "watchlist"
```

Leave `earnings_driver / price_cycle_driver / industry_breakout_driver / theme_relay_driver` on the existing score-based path.

- [ ] **Step 4: Run tests to verify they pass**

Run: `py -3.10 -m pytest tests/test_shortline_daily_review_layers.py -q -k "flow_only or industry_breakout"`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/shortline_hub/orchestrator.py tests/test_shortline_daily_review_layers.py
git commit -m "feat: gate flow-only shortline candidates by driver support"
```

---

### Task 6: Full regression and documentation

**Files:**
- Modify: `docs/CHANGELOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Test: `tests/test_shortline_driver_support.py`
- Test: `tests/test_shortline_hub_orchestrator.py`
- Test: `tests/test_shortline_daily_review_layers.py`
- Test: `tests/test_shortline_snapshot_sync.py`

- [ ] **Step 1: Update docs**

Document:
- new `driver_type / driver_confidence / driver_evidence / driver_support_score`
- rollout policy: report-only first, tier gating second
- meaning of `flow_only`

- [ ] **Step 2: Run targeted regression suite**

Run:

```bash
py -3.10 -m pytest tests/test_shortline_driver_support.py tests/test_shortline_hub_orchestrator.py tests/test_shortline_daily_review_layers.py tests/test_shortline_snapshot_sync.py -q
py -3.10 -m py_compile src/shortline_hub/driver_support.py src/shortline_hub/orchestrator.py src/shortline_hub/report_builder.py src/shortline_hub/schemas.py src/shortline_hub/snapshot_sync.py
```

Expected: all tests PASS, compile succeeds silently.

- [ ] **Step 3: Run one real shortline replay**

Run one known replay, for example:

```bash
py -3.10 scripts/run_shortline_hub.py --mode process --trade-date 2026-05-04 --top-n 5 --run-id shortline_driver_support_verify_20260505 --output-dir data/manual_runs/shortline_driver_support_verify_20260505 --wt-python-executable C:\Users\wenjin227\AppData\Local\Programs\Python\Python310\python.exe --wt-script-path D:\bb\WonderTrader\bridge\wt_export_candidates.py --wt-workdir D:\bb\WonderTrader --wt-source-mode prefer_real_engine --fg-python-executable D:\bb\FinGenius\.venv311\Scripts\python.exe --fg-script-path D:\bb\FinGenius\bridge\fg_explain_candidate.py --fg-workdir D:\bb\FinGenius --log-level INFO --persist-snapshot --enable-explain-cache --explain-cache-path data/runtime/shortline_hub/explain_cache/shortline_driver_support_verify_20260505.json --explain-cache-mode light --tracking-history-path data/runtime/shortline_hub/tracking/validation_real_fix_20260503_20260504.json
```

Expected:
- report contains `driver_type`
- at least one candidate is not `flow_only`
- pure `flow_only` names, if any, no longer dominate `top_pick`

- [ ] **Step 4: Commit**

```bash
git add docs/CHANGELOG.md docs/AI_MODIFICATION_LOG.md docs/LOCAL_STRATEGY_CATALOG.md tests/test_shortline_driver_support.py tests/test_shortline_hub_orchestrator.py tests/test_shortline_daily_review_layers.py tests/test_shortline_snapshot_sync.py src/shortline_hub/driver_support.py src/shortline_hub/orchestrator.py src/shortline_hub/report_builder.py src/shortline_hub/schemas.py src/shortline_hub/snapshot_sync.py
git commit -m "feat: add driver support layer to shortline hub"
```

---

## Self-Review

- Spec coverage:
  - add logic-support labels: covered by Tasks 1-4
  - keep current shortline system stable first: covered by deterministic classifier + report-first rollout
  - later use support quality to affect tier: covered by Task 5
  - persist/report the new fields: covered by Task 4
- Placeholder scan:
  - no `TODO`/`TBD`
  - concrete file paths and test commands included
- Type consistency:
  - canonical names are `driver_type`, `driver_confidence`, `driver_support_score`, `driver_evidence`
  - classifier output object is `DriverSupportAssessment`

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-05-shortline-driver-support-layer.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
