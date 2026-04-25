# Trend Leader Unified Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first A-share `trend_leader_unified` strategy that produces a single ranked snapshot list with `breakout / pullback / hybrid` scoring and exposes it through the existing `/signals` flow.

**Architecture:** Add one backend scoring/orchestration service that reuses dragon-head, capital-profile, earnings, and K-line data; wire it to a new daily selector script that persists `trend_leader_unified` snapshots; then extend signal query schemas and the web Signals page to display the new signal type without replacing existing strategy tabs.

**Tech Stack:** Python, FastAPI, SQLAlchemy/SQLite, pytest/unittest, React + TypeScript + Vitest

---

## File Structure

### New files

- `src/services/trend_leader_strategy_service.py`
  - Unified orchestration service for hard filters, `breakout_score`, `pullback_score`, `hybrid_score`, `overall_score`, `primary_profile`, and snapshot payload assembly.
- `scripts/select_trend_leader_candidates.py`
  - A-share runner that scans the universe, calls `TrendLeaderStrategyService`, exports results, and persists `trend_leader_unified` snapshots.
- `tests/test_trend_leader_strategy_service.py`
  - Focused unit tests for scoring, profile resolution, and hard-filter gates.
- `tests/test_trend_leader_signal_flow.py`
  - Integration-style tests for selector output and snapshot persistence.
- `docs/TREND_LEADER_UNIFIED_STRATEGY.md`
  - User-facing Chinese documentation for the new strategy.

### Existing files to modify

- `src/services/signal_snapshot_service.py`
  - Add the new signal type metadata, list/history item mapping, and count metadata.
- `api/v1/schemas/signals.py`
  - Add the new response fields for unified strategy scores and profile labels.
- `tests/test_signal_snapshot_api.py`
  - Extend API contract tests for `trend_leader_unified`.
- `apps/dsa-web/src/types/signals.ts`
  - Add TS fields for unified strategy payloads.
- `apps/dsa-web/src/pages/SignalsPage.tsx`
  - Add a new tab, labels, and detail rendering for the unified strategy.
- `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`
  - Add UI coverage for the new signal type tab and fields.
- `README.md`
  - Add a brief entry for the new unified strategy and link to the detailed doc.
- `docs/CHANGELOG.md`
  - Add flat `[Unreleased]` lines for feature/docs/tests.

---

### Task 1: Implement Unified Scoring Service

**Files:**
- Create: `src/services/trend_leader_strategy_service.py`
- Test: `tests/test_trend_leader_strategy_service.py`

- [ ] **Step 1: Write the failing unit tests**

```python
from types import SimpleNamespace

from src.services.trend_leader_strategy_service import TrendLeaderStrategyService


def test_breakout_candidate_prefers_breakout_profile():
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600001",
        stock_name="强势龙头",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 3,
            "relative_strength_score": 3,
            "liquidity_score": 2,
            "catalyst_score": 2,
        },
        trend_payload={
            "is_breakout_candidate": True,
            "is_pullback_candidate": False,
            "near_new_high": True,
            "trend_strength": 88.0,
            "bias_ma5": 2.1,
        },
        capital_payload={
            "capital_consensus_score": 3,
            "capital_profile_score": 84.0,
            "capital_flow_score": 3,
            "relative_strength_score": 3,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_strategy_score",
            "earnings_strategy_score": 68.0,
            "earnings_quality_signal": True,
        },
        commodity_payload=None,
    )

    assert result["passed"] is True
    assert result["primary_profile"] == "breakout"
    assert result["breakout_score"] > result["pullback_score"]
    assert result["overall_score"] == result["hybrid_score"]


def test_pullback_candidate_prefers_pullback_profile():
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600002",
        stock_name="回踩龙头",
        dragon_payload={
            "status": "ok",
            "leader_type": "logic_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
        },
        trend_payload={
            "is_breakout_candidate": False,
            "is_pullback_candidate": True,
            "near_new_high": False,
            "trend_strength": 76.0,
            "bias_ma5": -1.2,
        },
        capital_payload={
            "capital_consensus_score": 2,
            "capital_profile_score": 68.0,
            "capital_flow_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
        },
        earnings_payload={
            "earnings_strategy_gate_status": "passed_watch_with_confirmation",
            "earnings_strategy_score": 54.0,
            "earnings_quality_signal": True,
        },
        commodity_payload=None,
    )

    assert result["passed"] is True
    assert result["primary_profile"] == "pullback"
    assert result["pullback_score"] > result["breakout_score"]


def test_negative_earnings_gate_blocks_candidate():
    service = TrendLeaderStrategyService()

    result = service.score_candidate(
        stock_code="600003",
        stock_name="风险样本",
        dragon_payload={
            "status": "ok",
            "leader_type": "hybrid_leader",
            "leader_probability": "high",
            "recognizability_score": 3,
            "sector_leadership_score": 2,
            "relative_strength_score": 2,
            "liquidity_score": 2,
            "catalyst_score": 1,
        },
        trend_payload={"is_breakout_candidate": True, "is_pullback_candidate": False, "near_new_high": True, "trend_strength": 80.0, "bias_ma5": 1.0},
        capital_payload={"capital_consensus_score": 2, "capital_profile_score": 61.0, "capital_flow_score": 2, "relative_strength_score": 2, "liquidity_score": 2},
        earnings_payload={
            "earnings_strategy_gate_status": "blocked_quality_risk",
            "earnings_strategy_score": 22.0,
            "earnings_quality_signal": False,
        },
        commodity_payload=None,
    )

    assert result["passed"] is False
    assert "blocked_quality_risk" in result["risk_flags"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trend_leader_strategy_service.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'src.services.trend_leader_strategy_service'`

- [ ] **Step 3: Write the minimal scoring service**

```python
# src/services/trend_leader_strategy_service.py
from __future__ import annotations

from typing import Any, Dict, Optional


class TrendLeaderStrategyService:
    NEGATIVE_EARNINGS_GATES = {
        "blocked_negative_text",
        "blocked_missing_positive_text",
        "blocked_missing_growth_thresholds",
        "blocked_quality_risk",
        "blocked_low_strategy_score",
        "blocked_missing_confirmation",
        "blocked_duplicate_event",
    }

    def score_candidate(
        self,
        *,
        stock_code: str,
        stock_name: str,
        dragon_payload: Dict[str, Any],
        trend_payload: Dict[str, Any],
        capital_payload: Dict[str, Any],
        earnings_payload: Optional[Dict[str, Any]],
        commodity_payload: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        risk_flags: list[str] = []
        if str(dragon_payload.get("leader_type") or "") == "pseudo_leader":
            risk_flags.append("pseudo_leader")
        if int(dragon_payload.get("recognizability_score") or 0) < 2:
            risk_flags.append("weak_recognizability")
        if int(dragon_payload.get("sector_leadership_score") or 0) < 1:
            risk_flags.append("weak_sector_leadership")
        if int(capital_payload.get("capital_consensus_score") or 0) < 1:
            risk_flags.append("weak_capital_consensus")

        earnings_gate = str((earnings_payload or {}).get("earnings_strategy_gate_status") or "")
        if earnings_gate in self.NEGATIVE_EARNINGS_GATES:
            risk_flags.append(earnings_gate)

        breakout_score = 0.0
        if bool(trend_payload.get("is_breakout_candidate")):
            breakout_score += 35.0
            breakout_score += float(dragon_payload.get("recognizability_score") or 0) * 10.0
            breakout_score += float(capital_payload.get("capital_consensus_score") or 0) * 8.0
            breakout_score += float(capital_payload.get("capital_flow_score") or 0) * 6.0

        pullback_score = 0.0
        if bool(trend_payload.get("is_pullback_candidate")):
            pullback_score += 35.0
            pullback_score += float(dragon_payload.get("recognizability_score") or 0) * 9.0
            pullback_score += float(capital_payload.get("capital_consensus_score") or 0) * 7.0
            pullback_score += max(0.0, 5.0 - abs(float(trend_payload.get("bias_ma5") or 0.0)))

        logic_bonus = 0.0
        if float((earnings_payload or {}).get("earnings_strategy_score") or 0.0) >= 55.0:
            logic_bonus += 8.0
        if bool((earnings_payload or {}).get("earnings_quality_signal")):
            logic_bonus += 4.0
        if commodity_payload:
            logic_bonus += 6.0

        hybrid_score = max(breakout_score, pullback_score) + logic_bonus
        hybrid_score -= float(len(risk_flags)) * 8.0
        hybrid_score = round(max(hybrid_score, 0.0), 1)

        primary_profile = "breakout" if breakout_score >= pullback_score else "pullback"
        passed = not risk_flags and (breakout_score > 0 or pullback_score > 0)

        return {
            "code": stock_code,
            "name": stock_name,
            "passed": passed,
            "primary_profile": primary_profile,
            "breakout_score": round(breakout_score, 1),
            "pullback_score": round(pullback_score, 1),
            "hybrid_score": hybrid_score,
            "overall_score": hybrid_score,
            "risk_flags": risk_flags,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trend_leader_strategy_service.py -v`  
Expected: PASS with `3 passed`

- [ ] **Step 5: Commit**

```bash
git add tests/test_trend_leader_strategy_service.py src/services/trend_leader_strategy_service.py
git commit -m "feat: add unified trend leader scoring service"
```

### Task 2: Add Selector Script and Snapshot Persistence

**Files:**
- Create: `scripts/select_trend_leader_candidates.py`
- Modify: `src/services/signal_snapshot_service.py`
- Test: `tests/test_trend_leader_signal_flow.py`

- [ ] **Step 1: Write the failing signal-flow tests**

```python
from datetime import date

from src.storage import DatabaseManager
from scripts.select_trend_leader_candidates import build_snapshot_metrics_payload


def test_build_snapshot_metrics_payload_contains_unified_fields():
    payload = build_snapshot_metrics_payload(
        result={
            "code": "600001",
            "name": "强势龙头",
            "primary_profile": "breakout",
            "breakout_score": 82.0,
            "pullback_score": 41.0,
            "hybrid_score": 86.0,
            "overall_score": 86.0,
            "risk_flags": [],
            "leader_probability": "high",
            "leader_type": "hybrid_leader",
            "recognizability_score": 3,
            "sector_leadership_score": 3,
            "capital_consensus_score": 3,
        }
    )

    assert payload["primary_profile"] == "breakout"
    assert payload["overall_score"] == 86.0
    assert payload["leader_probability"] == "high"


def test_trend_leader_snapshot_can_be_queried_from_signal_service(tmp_path):
    db = DatabaseManager.get_instance()
    db.upsert_signal_snapshot(
        signal_type="trend_leader_unified",
        signal_date="2026-04-19",
        code="600001",
        name="强势龙头",
        criteria_payload={"profile_scope": "unified"},
        metrics_payload={
            "primary_profile": "breakout",
            "breakout_score": 82.0,
            "pullback_score": 41.0,
            "hybrid_score": 86.0,
            "overall_score": 86.0,
            "leader_probability": "high",
            "leader_type": "hybrid_leader",
            "capital_consensus_score": 3,
        },
        cause_payload={"reason_summary": "统一策略命中"},
        history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
    )

    from src.services.signal_snapshot_service import SignalSnapshotService

    service = SignalSnapshotService(db)
    data = service.get_snapshot_list(signal_type="trend_leader_unified", signal_date="2026-04-19")

    assert data["total"] == 1
    assert data["items"][0]["primary_profile"] == "breakout"
    assert data["items"][0]["overall_score"] == 86.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trend_leader_signal_flow.py -v`  
Expected: FAIL with `ImportError` for `scripts.select_trend_leader_candidates` and missing unified fields in `SignalSnapshotService`

- [ ] **Step 3: Add the selector script and snapshot mapping**

```python
# scripts/select_trend_leader_candidates.py
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from src.storage import DatabaseManager

SIGNAL_TYPE = "trend_leader_unified"


def build_snapshot_metrics_payload(*, result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "primary_profile": result.get("primary_profile"),
        "breakout_score": result.get("breakout_score"),
        "pullback_score": result.get("pullback_score"),
        "hybrid_score": result.get("hybrid_score"),
        "overall_score": result.get("overall_score"),
        "trend_label": result.get("trend_label"),
        "risk_flags": result.get("risk_flags") or [],
        "strategy_summary": result.get("strategy_summary"),
        "leader_probability": result.get("leader_probability"),
        "leader_type": result.get("leader_type"),
        "recognizability_score": result.get("recognizability_score"),
        "sector_leadership_score": result.get("sector_leadership_score"),
        "capital_consensus_score": result.get("capital_consensus_score"),
        "capital_profile_score": result.get("capital_profile_score"),
        "capital_flow_score": result.get("capital_flow_score"),
        "relative_strength_score": result.get("relative_strength_score"),
        "liquidity_score": result.get("liquidity_score"),
        "earnings_strategy_score": result.get("earnings_strategy_score"),
        "earnings_strategy_gate_status": result.get("earnings_strategy_gate_status"),
    }


def persist_result(db: DatabaseManager, *, snapshot_date: str, result: Dict[str, Any]) -> None:
    db.upsert_signal_snapshot(
        signal_type=SIGNAL_TYPE,
        signal_date=snapshot_date,
        code=str(result["code"]),
        name=str(result.get("name") or ""),
        criteria_payload={"profile_scope": "unified"},
        metrics_payload=build_snapshot_metrics_payload(result=result),
        cause_payload={"reason_summary": result.get("strategy_summary") or "趋势龙头统一策略命中"},
        history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
    )
```

```python
# src/services/signal_snapshot_service.py
DEFAULT_SIGNAL_TYPES: List[str] = [
    "trend_leader_unified",
    "hundred_day_high",
    "earnings_surprise",
    "hundred_day_high_with_earnings",
    "dragon_head_candidate",
    "commodity_beneficiary__optical_fiber",
    "commodity_beneficiary__memory",
    "commodity_beneficiary__hard_disk",
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_trend_leader_signal_flow.py -v`  
Expected: PASS with `2 passed`

- [ ] **Step 5: Commit**

```bash
git add scripts/select_trend_leader_candidates.py src/services/signal_snapshot_service.py tests/test_trend_leader_signal_flow.py
git commit -m "feat: persist unified trend leader snapshots"
```

### Task 3: Expose Unified Fields Through API and UI

**Files:**
- Modify: `api/v1/schemas/signals.py`
- Modify: `tests/test_signal_snapshot_api.py`
- Modify: `apps/dsa-web/src/types/signals.ts`
- Modify: `apps/dsa-web/src/pages/SignalsPage.tsx`
- Modify: `apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx`

- [ ] **Step 1: Write the failing API and UI tests**

```python
def test_list_endpoint_returns_trend_leader_unified_fields(self) -> None:
    self.db.upsert_signal_snapshot(
        signal_type="trend_leader_unified",
        signal_date="2026-04-19",
        code="600001",
        name="强势龙头",
        criteria_payload={"profile_scope": "unified"},
        metrics_payload={
            "primary_profile": "breakout",
            "breakout_score": 82.0,
            "pullback_score": 41.0,
            "hybrid_score": 86.0,
            "overall_score": 86.0,
            "trend_label": "near_new_high",
            "leader_probability": "high",
            "leader_type": "hybrid_leader",
        },
        cause_payload={"reason_summary": "统一策略命中"},
        history_payload={"previous_hit_count": 0, "days_since_previous_hit": None},
    )

    response = self.client.get(
        "/api/v1/signals/kline-snapshots",
        params={"signal_type": "trend_leader_unified", "signal_date": "2026-04-19"},
    )

    self.assertEqual(response.status_code, 200)
    item = response.json()["items"][0]
    self.assertEqual(item["primary_profile"], "breakout")
    self.assertEqual(item["overall_score"], 86.0)
    self.assertEqual(item["trend_label"], "near_new_high")
```

```tsx
it('renders trend leader unified signal tab and score fields', async () => {
  getSnapshotCounts.mockResolvedValue({
    signalDate: '2026-04-19',
    items: [{ signalType: 'trend_leader_unified', total: 1, displayLabel: '强趋势龙头总榜', group: 'strategy' }],
  });
  getSnapshots.mockResolvedValue({
    signalType: 'trend_leader_unified',
    signalDate: '2026-04-19',
    signalDateFrom: null,
    signalDateTo: null,
    total: 1,
    page: 1,
    pageSize: 12,
    compareSummary: [],
    streakLeaderboard: [],
    items: [{
      code: '600001',
      name: '强势龙头',
      signalDate: '2026-04-19',
      primaryProfile: 'breakout',
      breakoutScore: 82,
      pullbackScore: 41,
      hybridScore: 86,
      overallScore: 86,
      trendLabel: 'near_new_high',
      leaderProbability: 'high',
      leaderType: 'hybrid_leader',
      previousHitCount: 0,
      isConsecutiveSignal: false,
    }],
  });

  render(<SignalsPage />);
  await screen.findByText('强趋势龙头总榜');
  expect(screen.getByText('breakout')).toBeInTheDocument();
  expect(screen.getByText('86.00')).toBeInTheDocument();
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_signal_snapshot_api.py -k trend_leader_unified -v`  
Expected: FAIL because response model drops unified fields

Run: `cd apps/dsa-web && npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`  
Expected: FAIL because the new tab and fields are not rendered

- [ ] **Step 3: Extend schemas, types, and Signals page**

```python
# api/v1/schemas/signals.py
class SignalSnapshotListItem(BaseModel):
    primary_profile: Optional[str] = Field(None, description="Primary profile such as breakout/pullback")
    breakout_score: Optional[float] = Field(None, description="Breakout score")
    pullback_score: Optional[float] = Field(None, description="Pullback score")
    hybrid_score: Optional[float] = Field(None, description="Hybrid score")
    overall_score: Optional[float] = Field(None, description="Overall unified strategy score")
    trend_label: Optional[str] = Field(None, description="Trend label")
    strategy_summary: Optional[str] = Field(None, description="Unified strategy summary")
```

```ts
// apps/dsa-web/src/types/signals.ts
export interface SignalSnapshotListItem {
  primaryProfile?: string | null;
  breakoutScore?: number | null;
  pullbackScore?: number | null;
  hybridScore?: number | null;
  overallScore?: number | null;
  trendLabel?: string | null;
  strategySummary?: string | null;
}
```

```tsx
// apps/dsa-web/src/pages/SignalsPage.tsx
const TREND_LEADER_SIGNAL_META: Record<string, SignalTypeMeta> = {
  trend_leader_unified: {
    title: '强趋势龙头总榜',
    description: '查看按统一策略筛出的 A 股强趋势龙头总榜，包含 breakout / pullback / hybrid 评分与主标签。',
    streakLabel: '连续入榜',
    itemGroupTitle: '连续入榜',
    nonStreakTitle: '其他入榜',
    emptyHint: '可以先运行强趋势龙头统一策略脚本，生成 trend_leader_unified 当日快照。',
    tabAccent: 'from-rose-500/25 via-orange-400/10 to-transparent border-rose-400/40 text-rose-100',
    countBadgeClass: 'border-rose-400/25 bg-rose-500/10 text-rose-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  },
};

const BASE_SIGNAL_TYPE_OPTIONS = [
  { value: 'trend_leader_unified', label: '强趋势龙头总榜' },
  { value: 'dragon_head_candidate', label: '龙头专题' },
  { value: 'hundred_day_high', label: '百日新高' },
];
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_signal_snapshot_api.py -k trend_leader_unified -v`  
Expected: PASS

Run: `cd apps/dsa-web && npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add api/v1/schemas/signals.py tests/test_signal_snapshot_api.py apps/dsa-web/src/types/signals.ts apps/dsa-web/src/pages/SignalsPage.tsx apps/dsa-web/src/pages/__tests__/SignalsPage.test.tsx
git commit -m "feat: expose unified trend leader signal in api and ui"
```

### Task 4: Document the Feature and Verify End-to-End

**Files:**
- Create: `docs/TREND_LEADER_UNIFIED_STRATEGY.md`
- Modify: `README.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Write the failing documentation checklist**

```text
Checklist:
- README mentions the new unified A-share trend leader strategy
- docs/TREND_LEADER_UNIFIED_STRATEGY.md explains scope, fields, profiles, and command usage
- docs/CHANGELOG.md includes flat Unreleased entries
```

- [ ] **Step 2: Add the user-facing docs**

```md
# docs/TREND_LEADER_UNIFIED_STRATEGY.md
# A 股强趋势龙头统一策略

## 适用范围

- 仅覆盖 A 股
- 输出统一候选池
- 每只股票带有 `primary_profile`、`breakout_score`、`pullback_score`、`hybrid_score`、`overall_score`

## 运行命令

```bash
python scripts/select_trend_leader_candidates.py --snapshot-date 2026-04-19 --limit 100
```
```

```md
# README.md
| **统一选股** | **强趋势龙头总榜** | **新增 `trend_leader_unified`，面向 A 股输出统一总榜，并标记 `breakout/pullback/hybrid` 主 profile，可在 `/signals` 回看** |
```

```md
# docs/CHANGELOG.md
- [新功能] 新增 A 股 `trend_leader_unified` 强趋势龙头统一策略快照与 `/signals` 接入
- [文档] 新增强趋势龙头统一策略说明文档并更新 README
- [测试] 新增强趋势龙头统一策略服务、信号流与 Signals 页面测试
```

- [ ] **Step 3: Run verification commands**

Run: `python -m pytest tests/test_trend_leader_strategy_service.py tests/test_trend_leader_signal_flow.py tests/test_signal_snapshot_api.py -v`  
Expected: PASS

Run: `cd apps/dsa-web && npm run test -- --run src/pages/__tests__/SignalsPage.test.tsx`  
Expected: PASS

Run: `python -m py_compile src/services/trend_leader_strategy_service.py scripts/select_trend_leader_candidates.py`  
Expected: no output

- [ ] **Step 4: Commit**

```bash
git add docs/TREND_LEADER_UNIFIED_STRATEGY.md README.md docs/CHANGELOG.md
git commit -m "docs: document unified trend leader strategy"
```

## Self-Review

### Spec coverage

- Unified A-share strategy: covered by Tasks 1 and 2
- Hard filters and profile scoring: covered by Task 1
- New `trend_leader_unified` snapshot: covered by Task 2
- `/signals` backend/API/web integration: covered by Task 3
- Chinese docs and changelog: covered by Task 4

### Placeholder scan

- No `TODO` / `TBD` / “implement later” placeholders remain.
- Every code-bearing step contains code blocks.
- Every verification step contains concrete commands.

### Type consistency

- Unified field names are consistent across plan tasks:
  - `primary_profile`
  - `breakout_score`
  - `pullback_score`
  - `hybrid_score`
  - `overall_score`
  - `trend_label`
  - `strategy_summary`

