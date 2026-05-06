# Board Cycle Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reusable board-level cycle scanner that analyzes specified boards, scores whether each board is strengthening, and surfaces board leaders plus earnings-supported stocks with stable `primary_board` routing for multi-board names.

**Architecture:** Add a dedicated service layer in `src/services/board_cycle_scan_service.py` that owns board-universe loading, board-stock scoring, board scoring, and `primary_board` resolution. Add a thin CLI in `scripts/select_board_cycle_candidates.py` for batch execution and artifact writing, then document the new topic strategy in the local-strategy docs and changelog assets.

**Tech Stack:** Python, pandas, pytest, existing `DataFetcherManager`, `SharedSignalFactorsService`, existing board/theme/commodity services, Markdown/CSV artifact writers.

---

## File Map

- Create: `src/services/board_cycle_scan_service.py`
  - New service for board constituent loading, per-stock evaluation, board scoring, and multi-board routing.
- Create: `scripts/select_board_cycle_candidates.py`
  - New CLI entrypoint and artifact writer for `board_cycle_scan`.
- Create: `tests/test_board_cycle_scan_service.py`
  - Service-level scoring and routing tests.
- Create: `tests/test_board_cycle_scan_script.py`
  - CLI parsing and artifact export tests.
- Create: `docs/local_strategies/topics/board_cycle_scan.md`
  - Chinese topic doc for the new board-level scanner.
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
  - Register the new topic entry and entrypoint.
- Modify: `docs/LOCAL_STRATEGY_BASELINE.md`
  - Clarify that this is an on-demand topic scanner, not a default daily strategy.
- Modify: `docs/AI_MODIFICATION_LOG.md`
  - Add implementation trace, validation commands, and smoke evidence placeholder section to be filled after execution.
- Modify: `docs/CHANGELOG.md`
  - Add one flat `[Unreleased]` line for the new feature.

## Implementation Notes

- Do not commit unless the user explicitly asks. This repo rule overrides the default “frequent commits” guidance.
- Keep the first version as an on-demand board/topic tool. Do not add schedule hooks, config flags, or `/signals` integration in this plan.
- Prefer injected dependencies in tests so the service can be exercised without network calls.
- Keep the score system simple and deterministic in v1:
  - board stock: `board_leader_score`, `earnings_support_score`, `logic_match_score`, `business_relevance_score`
  - board: `breadth_score`, `leadership_score`, `earnings_support_score`, `structure_score`

### Task 1: Add Failing Service Tests For Board Scoring And Primary Board Routing

**Files:**
- Create: `tests/test_board_cycle_scan_service.py`
- Verify: `tests/test_board_cycle_scan_service.py`

- [ ] **Step 1: Create a failing test for board-stock role classification**

```python
from types import SimpleNamespace

from src.services.board_cycle_scan_service import BoardCycleScanService


def test_score_board_stock_assigns_leader_role_for_front_rank_name() -> None:
    service = BoardCycleScanService()
    stock = {
        "code": "002001",
        "name": "锂矿龙头A",
        "reason_summary": "锂价上涨叠加板块共振",
        "cause_tags": ["price_increase", "sector_rotation"],
        "recognizability_score": 3,
        "sector_leadership_score": 3,
        "quality_overlay_score": 12.0,
        "earnings_validation_status": "positive",
        "earnings_release_probability": "high",
        "theme_key": "lithium_mining",
        "subtheme_key": "lithium_resource",
        "industry_strength_label": "锂矿",
    }

    result = service._score_board_stock(
        board_name="锂矿",
        board_type="concept",
        stock_row=stock,
    )

    assert result.stock_role == "leader"
    assert result.board_leader_score >= result.earnings_support_score
    assert result.logic_match_score > 0
```

- [ ] **Step 2: Create a failing test for board-level cycle label**

```python
from src.services.board_cycle_scan_service import (
    BoardCycleBoardResult,
    BoardCycleScanService,
    BoardStockEvaluation,
)


def test_score_board_marks_board_strengthening_when_breadth_and_leaders_are_present() -> None:
    service = BoardCycleScanService()
    evaluations = [
        BoardStockEvaluation(
            board_name="锂矿",
            board_type="concept",
            stock_code="002001",
            stock_name="锂矿龙头A",
            board_stock_score=16.0,
            board_leader_score=6.0,
            earnings_support_score=4.0,
            logic_match_score=4.0,
            business_relevance_score=2.0,
            stock_role="leader",
            reason_summary="锂价上涨叠加板块共振",
            cause_tags=["price_increase", "sector_rotation"],
            primary_board="",
            related_boards=[],
            primary_board_reason="",
        ),
        BoardStockEvaluation(
            board_name="锂矿",
            board_type="concept",
            stock_code="002002",
            stock_name="锂矿业绩A",
            board_stock_score=12.0,
            board_leader_score=3.0,
            earnings_support_score=5.0,
            logic_match_score=2.0,
            business_relevance_score=2.0,
            stock_role="earnings_supported",
            reason_summary="业绩释放确认",
            cause_tags=["earnings"],
            primary_board="",
            related_boards=[],
            primary_board_reason="",
        ),
    ]

    board = service._score_board(
        board_name="锂矿",
        board_type="concept",
        constituent_count=5,
        evaluations=evaluations,
    )

    assert board.board_cycle_label == "strengthening"
    assert board.leader_count == 1
    assert board.earnings_supported_count == 1
```

- [ ] **Step 3: Create a failing test for multi-board `primary_board` routing**

```python
from src.services.board_cycle_scan_service import BoardCycleScanService, BoardStockEvaluation


def test_resolve_primary_boards_prefers_logic_match_then_board_rank() -> None:
    service = BoardCycleScanService()
    lithium_view = BoardStockEvaluation(
        board_name="锂矿",
        board_type="concept",
        stock_code="300001",
        stock_name="双归属A",
        board_stock_score=14.0,
        board_leader_score=5.0,
        earnings_support_score=3.0,
        logic_match_score=4.0,
        business_relevance_score=1.0,
        stock_role="leader",
        reason_summary="锂价上涨驱动",
        cause_tags=["price_increase"],
        primary_board="",
        related_boards=[],
        primary_board_reason="",
    )
    battery_view = BoardStockEvaluation(
        board_name="固态电池",
        board_type="concept",
        stock_code="300001",
        stock_name="双归属A",
        board_stock_score=13.0,
        board_leader_score=4.0,
        earnings_support_score=3.0,
        logic_match_score=2.0,
        business_relevance_score=2.0,
        stock_role="leader",
        reason_summary="锂价上涨驱动",
        cause_tags=["price_increase"],
        primary_board="",
        related_boards=[],
        primary_board_reason="",
    )

    resolved = service._resolve_primary_boards([lithium_view, battery_view])

    lithium_result = next(item for item in resolved if item.board_name == "锂矿")
    battery_result = next(item for item in resolved if item.board_name == "固态电池")
    assert lithium_result.primary_board == "锂矿"
    assert battery_result.primary_board == "锂矿"
    assert "logic_match" in lithium_result.primary_board_reason
```

- [ ] **Step 4: Create a failing test for missing earnings data fail-open behavior**

```python
from src.services.board_cycle_scan_service import BoardCycleScanService


def test_score_board_stock_keeps_watch_role_when_earnings_fields_are_missing() -> None:
    service = BoardCycleScanService()
    stock = {
        "code": "600100",
        "name": "白酒观察A",
        "reason_summary": "白酒板块回暖",
        "cause_tags": ["sector_rotation"],
        "recognizability_score": 1,
        "sector_leadership_score": 1,
    }

    result = service._score_board_stock(
        board_name="白酒",
        board_type="industry",
        stock_row=stock,
    )

    assert result.stock_role == "watch"
    assert result.earnings_support_score == 0
    assert result.board_stock_score > 0
```

- [ ] **Step 5: Run the focused tests to confirm they fail first**

Run:

```bash
python -m pytest tests/test_board_cycle_scan_service.py -v
```

Expected:

- At least one test fails because `board_cycle_scan_service.py` does not exist yet.
- Failures are import/attribute failures, not syntax failures inside the test file.

### Task 2: Implement The Board Cycle Scan Service

**Files:**
- Create: `src/services/board_cycle_scan_service.py`
- Verify: `tests/test_board_cycle_scan_service.py`

- [ ] **Step 1: Add the service dataclasses and constructor**

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

import pandas as pd

from data_provider import DataFetcherManager


@dataclass
class BoardStockEvaluation:
    board_name: str
    board_type: str
    stock_code: str
    stock_name: str
    board_stock_score: float
    board_leader_score: float
    earnings_support_score: float
    logic_match_score: float
    business_relevance_score: float
    stock_role: str
    reason_summary: str
    cause_tags: List[str] = field(default_factory=list)
    primary_board: str = ""
    related_boards: List[str] = field(default_factory=list)
    primary_board_reason: str = ""
    recognizability_score: int = 0
    sector_leadership_score: int = 0
    earnings_validation_status: str = ""
    earnings_release_probability: str = ""
    quality_overlay_score: float = 0.0


@dataclass
class BoardCycleBoardResult:
    board_name: str
    board_type: str
    constituent_count: int
    qualified_stock_count: int
    leader_count: int
    earnings_supported_count: int
    breadth_score: float
    leadership_score: float
    earnings_support_score: float
    structure_score: float
    board_cycle_score: float
    board_cycle_label: str
    top_leaders: List[str] = field(default_factory=list)
    top_earnings_supported: List[str] = field(default_factory=list)


class BoardCycleScanService:
    def __init__(
        self,
        *,
        manager: Optional[DataFetcherManager] = None,
    ) -> None:
        self.manager = manager or DataFetcherManager()
```

- [ ] **Step 2: Implement board-universe loading and score helpers**

```python
    def fetch_board_universe(self, *, board_name: str, board_type: str = "auto") -> pd.DataFrame:
        df = self.manager.get_board_constituents(board_name, board_type=board_type)
        if df is None or df.empty:
            raise RuntimeError(f"board constituents empty: {board_name} ({board_type})")
        normalized = df.rename(columns={"代码": "code", "名称": "name"}).copy()
        if "code" not in normalized.columns or "name" not in normalized.columns:
            raise RuntimeError(f"board constituents missing required columns: {board_name} ({board_type})")
        normalized["board_name"] = board_name
        normalized["board_type"] = board_type
        return normalized[["code", "name", "board_name", "board_type"]]

    def _probability_score(self, value: str) -> float:
        mapping = {"": 0.0, "low": 1.0, "medium": 2.0, "high": 3.0}
        return mapping.get(str(value or "").strip().lower(), 0.0)

    def _normalize_tags(self, value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item or "").strip() for item in value if str(item or "").strip()]
        text = str(value or "").strip()
        if not text:
            return []
        return [item.strip() for item in text.split(",") if item.strip()]
```

- [ ] **Step 3: Implement per-stock scoring and role assignment**

```python
    def _score_board_stock(self, *, board_name: str, board_type: str, stock_row: Dict[str, Any]) -> BoardStockEvaluation:
        recognizability = int(stock_row.get("recognizability_score") or 0)
        sector_leadership = int(stock_row.get("sector_leadership_score") or 0)
        quality_overlay = float(stock_row.get("quality_overlay_score") or 0.0)
        earnings_probability = str(stock_row.get("earnings_release_probability") or "")
        earnings_status = str(stock_row.get("earnings_validation_status") or "")
        tags = self._normalize_tags(stock_row.get("cause_tags"))
        reason_summary = str(stock_row.get("reason_summary") or "").strip()
        logic_text = " ".join(
            [
                board_name,
                str(stock_row.get("industry_strength_label") or ""),
                str(stock_row.get("theme_key") or ""),
                str(stock_row.get("subtheme_key") or ""),
                reason_summary,
                " ".join(tags),
            ]
        ).lower()

        board_leader_score = float(recognizability * 1.5 + sector_leadership * 1.5)
        earnings_support_score = 0.0
        if earnings_status in {"positive", "validated", "confirmed"}:
            earnings_support_score += 3.0
        earnings_support_score += self._probability_score(earnings_probability)
        earnings_support_score += min(3.0, quality_overlay / 4.0)

        logic_match_score = 0.0
        if board_name.lower() in logic_text:
            logic_match_score += 2.0
        if any(tag in {"earnings", "sector_rotation", "price_increase"} for tag in tags):
            logic_match_score += 1.0

        business_relevance_score = 0.0
        if str(stock_row.get("industry_strength_label") or "").strip() == board_name:
            business_relevance_score += 1.0
        if str(stock_row.get("theme_key") or "").strip() or str(stock_row.get("subtheme_key") or "").strip():
            business_relevance_score += 1.0

        total = round(board_leader_score + earnings_support_score + logic_match_score + business_relevance_score, 2)
        if board_leader_score >= 5.0 and logic_match_score >= 2.0:
            stock_role = "leader"
        elif earnings_support_score >= 4.0:
            stock_role = "earnings_supported"
        else:
            stock_role = "watch"

        return BoardStockEvaluation(
            board_name=board_name,
            board_type=board_type,
            stock_code=str(stock_row.get("code") or ""),
            stock_name=str(stock_row.get("name") or ""),
            board_stock_score=total,
            board_leader_score=round(board_leader_score, 2),
            earnings_support_score=round(earnings_support_score, 2),
            logic_match_score=round(logic_match_score, 2),
            business_relevance_score=round(business_relevance_score, 2),
            stock_role=stock_role,
            reason_summary=reason_summary,
            cause_tags=tags,
            recognizability_score=recognizability,
            sector_leadership_score=sector_leadership,
            earnings_validation_status=earnings_status,
            earnings_release_probability=earnings_probability,
            quality_overlay_score=round(quality_overlay, 2),
        )
```

- [ ] **Step 4: Implement board scoring, primary-board routing, and batch scan**

```python
    def _score_board(
        self,
        *,
        board_name: str,
        board_type: str,
        constituent_count: int,
        evaluations: Sequence[BoardStockEvaluation],
    ) -> BoardCycleBoardResult:
        leader_items = [item for item in evaluations if item.stock_role == "leader"]
        earnings_items = [item for item in evaluations if item.stock_role == "earnings_supported"]
        qualified = [item for item in evaluations if item.board_stock_score > 0]

        breadth_score = round(min(5.0, len(qualified) / max(1, constituent_count) * 10.0), 2)
        leadership_score = round(
            min(5.0, sum(item.board_leader_score for item in sorted(evaluations, key=lambda x: x.board_stock_score, reverse=True)[:3]) / 3.0),
            2,
        )
        earnings_support_score = round(
            min(5.0, sum(item.earnings_support_score for item in sorted(evaluations, key=lambda x: x.earnings_support_score, reverse=True)[:3]) / 3.0),
            2,
        )
        unique_logic_tags = sorted({tag for item in evaluations for tag in item.cause_tags})
        structure_score = round(min(5.0, 1.0 + min(2.0, len(unique_logic_tags) / 2.0) + min(2.0, len(leader_items))), 2)
        total = round(breadth_score + leadership_score + earnings_support_score + structure_score, 2)
        if total >= 12.0 and leader_items:
            label = "strengthening"
        elif total >= 8.0:
            label = "observing"
        else:
            label = "weak"

        return BoardCycleBoardResult(
            board_name=board_name,
            board_type=board_type,
            constituent_count=constituent_count,
            qualified_stock_count=len(qualified),
            leader_count=len(leader_items),
            earnings_supported_count=len(earnings_items),
            breadth_score=breadth_score,
            leadership_score=leadership_score,
            earnings_support_score=earnings_support_score,
            structure_score=structure_score,
            board_cycle_score=total,
            board_cycle_label=label,
            top_leaders=[f"{item.stock_code}:{item.stock_name}" for item in leader_items[:3]],
            top_earnings_supported=[f"{item.stock_code}:{item.stock_name}" for item in earnings_items[:3]],
        )

    def _resolve_primary_boards(self, evaluations: Sequence[BoardStockEvaluation]) -> List[BoardStockEvaluation]:
        grouped: Dict[str, List[BoardStockEvaluation]] = {}
        for item in evaluations:
            grouped.setdefault(item.stock_code, []).append(item)
        for code_items in grouped.values():
            ordered = sorted(
                code_items,
                key=lambda item: (
                    item.logic_match_score,
                    item.board_stock_score,
                    item.business_relevance_score,
                    1 if item.board_type == "industry" else 0,
                    item.board_name,
                ),
                reverse=True,
            )
            primary = ordered[0]
            related = [item.board_name for item in ordered[1:]]
            for item in code_items:
                item.primary_board = primary.board_name
                item.related_boards = related
                item.primary_board_reason = "logic_match>board_stock_score>business_relevance"
        return list(evaluations)

    def scan_board_rows(
        self,
        *,
        board_name: str,
        board_type: str,
        stock_rows: Sequence[Dict[str, Any]],
    ) -> tuple[BoardCycleBoardResult, List[BoardStockEvaluation]]:
        evaluations = [
            self._score_board_stock(board_name=board_name, board_type=board_type, stock_row=row)
            for row in stock_rows
        ]
        board = self._score_board(
            board_name=board_name,
            board_type=board_type,
            constituent_count=len(stock_rows),
            evaluations=evaluations,
        )
        return board, evaluations
```

- [ ] **Step 5: Run the service tests and verify they pass**

Run:

```bash
python -m pytest tests/test_board_cycle_scan_service.py -v
```

Expected:

- All tests in `tests/test_board_cycle_scan_service.py` pass.
- No network access is required for these tests.

### Task 3: Add The CLI And Artifact Writers

**Files:**
- Create: `scripts/select_board_cycle_candidates.py`
- Create: `tests/test_board_cycle_scan_script.py`
- Verify: `tests/test_board_cycle_scan_service.py`, `tests/test_board_cycle_scan_script.py`

- [ ] **Step 1: Create a failing CLI test for argument parsing**

```python
from pathlib import Path

import scripts.select_board_cycle_candidates as board_cycle_script


def test_parse_args_supports_board_list_and_output_dir(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        board_cycle_script.sys,
        "argv",
        [
            "select_board_cycle_candidates.py",
            "--boards",
            "锂矿,白酒",
            "--board-type",
            "concept",
            "--output-dir",
            str(tmp_path),
        ],
    )

    args = board_cycle_script.parse_args()

    assert args.boards == "锂矿,白酒"
    assert args.board_type == "concept"
    assert Path(args.output_dir) == tmp_path
```

- [ ] **Step 2: Create a failing artifact-writing test**

```python
import tempfile
from pathlib import Path

from src.services.board_cycle_scan_service import BoardCycleBoardResult, BoardStockEvaluation
from scripts.select_board_cycle_candidates import write_outputs


def test_write_outputs_emits_summary_and_candidates_files(tmp_path: Path) -> None:
    board_results = [
        BoardCycleBoardResult(
            board_name="锂矿",
            board_type="concept",
            constituent_count=5,
            qualified_stock_count=2,
            leader_count=1,
            earnings_supported_count=1,
            breadth_score=4.0,
            leadership_score=4.0,
            earnings_support_score=3.5,
            structure_score=2.5,
            board_cycle_score=14.0,
            board_cycle_label="strengthening",
            top_leaders=["002001:锂矿龙头A"],
            top_earnings_supported=["002002:锂矿业绩A"],
        )
    ]
    stock_results = [
        BoardStockEvaluation(
            board_name="锂矿",
            board_type="concept",
            stock_code="002001",
            stock_name="锂矿龙头A",
            board_stock_score=16.0,
            board_leader_score=6.0,
            earnings_support_score=4.0,
            logic_match_score=4.0,
            business_relevance_score=2.0,
            stock_role="leader",
            reason_summary="锂价上涨叠加板块共振",
            cause_tags=["price_increase", "sector_rotation"],
            primary_board="锂矿",
            related_boards=[],
            primary_board_reason="logic_match>board_stock_score>business_relevance",
        )
    ]

    paths = write_outputs(
        board_results=board_results,
        stock_results=stock_results,
        output_dir=tmp_path,
    )

    assert paths["board_summary_csv"].exists()
    assert paths["board_summary_md"].exists()
    assert paths["board_stock_candidates_csv"].exists()
    assert paths["board_stock_candidates_md"].exists()
```

- [ ] **Step 3: Implement the CLI parser, board list normalizer, and artifact writers**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Dict, List, Sequence

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.board_cycle_scan_service import BoardCycleScanService


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "board_cycle_scan"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze specified boards and surface board-level cycle leaders.")
    parser.add_argument("--boards", required=True, help="Comma-separated board names, e.g. 锂矿,白酒")
    parser.add_argument("--board-type", default="auto", choices=["auto", "concept", "industry"])
    parser.add_argument("--snapshot-date", default=None)
    parser.add_argument("--top-per-board", type=int, default=3)
    parser.add_argument("--limit-per-board", type=int, default=None)
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def parse_board_names(raw_value: str) -> List[str]:
    values = [item.strip() for item in str(raw_value or "").split(",") if item.strip()]
    if not values:
        raise ValueError("at least one board is required")
    deduped: List[str] = []
    for item in values:
        if item not in deduped:
            deduped.append(item)
    return deduped
```

```python
def write_outputs(*, board_results, stock_results, output_dir: Path) -> Dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    board_summary_csv = output_dir / "board_summary.csv"
    board_summary_md = output_dir / "board_summary.md"
    board_stock_candidates_csv = output_dir / "board_stock_candidates.csv"
    board_stock_candidates_md = output_dir / "board_stock_candidates.md"
    run_summary_txt = output_dir / "run_summary.txt"

    pd.DataFrame([item.__dict__ for item in board_results]).to_csv(board_summary_csv, index=False, encoding="utf-8-sig")
    pd.DataFrame(
        [
            {
                **item.__dict__,
                "cause_tags": ",".join(item.cause_tags),
                "related_boards": ",".join(item.related_boards),
            }
            for item in stock_results
        ]
    ).to_csv(board_stock_candidates_csv, index=False, encoding="utf-8-sig")

    board_summary_md.write_text(
        "# Board Cycle Summary\n\n"
        + "\n".join(
            f"- {item.board_name} | {item.board_cycle_label} | score={item.board_cycle_score} | leaders={'; '.join(item.top_leaders)}"
            for item in board_results
        )
        + "\n",
        encoding="utf-8",
    )
    board_stock_candidates_md.write_text(
        "# Board Stock Candidates\n\n"
        + "\n".join(
            f"- {item.board_name} | {item.stock_code} | {item.stock_name} | {item.stock_role} | primary={item.primary_board}"
            for item in stock_results
        )
        + "\n",
        encoding="utf-8",
    )
    run_summary_txt.write_text(
        "\n".join(
            [
                f"boards={len(board_results)}",
                f"stock_candidates={len(stock_results)}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "board_summary_csv": board_summary_csv,
        "board_summary_md": board_summary_md,
        "board_stock_candidates_csv": board_stock_candidates_csv,
        "board_stock_candidates_md": board_stock_candidates_md,
        "run_summary_txt": run_summary_txt,
    }
```

- [ ] **Step 4: Implement the `main()` flow with an injected fake service test seam**

```python
def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level or "INFO").upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    service = BoardCycleScanService()
    board_results = []
    stock_results = []
    for board_name in parse_board_names(args.boards):
        board_df = service.fetch_board_universe(board_name=board_name, board_type=args.board_type)
        if args.limit_per_board:
            board_df = board_df.head(max(1, int(args.limit_per_board))).reset_index(drop=True)
        board_result, evaluations = service.scan_board_rows(
            board_name=board_name,
            board_type=args.board_type,
            stock_rows=board_df.to_dict(orient="records"),
        )
        board_results.append(board_result)
        stock_results.extend(evaluations)

    stock_results = service._resolve_primary_boards(stock_results)
    write_outputs(
        board_results=board_results,
        stock_results=stock_results,
        output_dir=Path(args.output_dir),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the script-focused tests and ensure they pass**

Run:

```bash
python -m pytest tests/test_board_cycle_scan_script.py tests/test_board_cycle_scan_service.py -v
```

Expected:

- All board-cycle tests pass.
- CSV and Markdown artifacts are created in a temp directory during the script test.

### Task 4: Document The Strategy And Run A Local Smoke

**Files:**
- Create: `docs/local_strategies/topics/board_cycle_scan.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Modify: `docs/LOCAL_STRATEGY_BASELINE.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Add the topic strategy doc**

```markdown
# 板块周期扫描

## 1. 目标

`scripts/select_board_cycle_candidates.py`

用于按指定板块批量分析：

- 板块是否整体转强
- 板块内谁是龙头
- 哪些股票兼具强势与业绩支撑

## 2. 常用命令

```bash
python scripts/select_board_cycle_candidates.py --boards 锂矿,白酒
python scripts/select_board_cycle_candidates.py --boards 锂矿 --board-type concept --top-per-board 3
```

## 3. 输出目录

`data/board_cycle_scan/<snapshot_date>/`

- `board_summary.csv`
- `board_summary.md`
- `board_stock_candidates.csv`
- `board_stock_candidates.md`
```

- [ ] **Step 2: Register the strategy in catalog/baseline/changelog**

```markdown
| 专题 | `board_cycle_scan` | `scripts/select_board_cycle_candidates.py` | `board_cycle_scan` | 否 | [`docs/local_strategies/topics/board_cycle_scan.md`](./local_strategies/topics/board_cycle_scan.md) |
```

```markdown
- `board_cycle_scan`
  - 按需运行的板块周期扫描器，不进入默认每日主链路。
```

```markdown
- [新功能] 新增 `scripts/select_board_cycle_candidates.py` 与 `src/services/board_cycle_scan_service.py`，可按指定板块输出板块周期评分、板块龙头、业绩支撑股及 `primary_board/related_boards` 归属结果。
```

- [ ] **Step 3: Add the AI modification log entry**

```markdown
## 2026-05-01 - board_cycle_scan

- Summary:
  - Added `src/services/board_cycle_scan_service.py` for board-level scoring and primary-board routing.
  - Added `scripts/select_board_cycle_candidates.py` for on-demand board scans and CSV/Markdown exports.
  - Added tests for stock scoring, board scoring, and CLI artifacts.
- Validation:
  - `python -m pytest tests/test_board_cycle_scan_service.py tests/test_board_cycle_scan_script.py -v`
  - `python -m py_compile src/services/board_cycle_scan_service.py scripts/select_board_cycle_candidates.py`
  - `python scripts/select_board_cycle_candidates.py --boards 锂矿,白酒 --limit-per-board 20 --output-dir data/manual_runs/board_cycle_scan_smoke_20260501`
```

- [ ] **Step 4: Run doc-safe verification and a local smoke**

Run:

```bash
python -m py_compile src/services/board_cycle_scan_service.py scripts/select_board_cycle_candidates.py
```

Expected:

- No syntax errors.

Run:

```bash
python -m pytest tests/test_board_cycle_scan_service.py tests/test_board_cycle_scan_script.py -v
```

Expected:

- All board-cycle tests pass.

Run:

```bash
python scripts/select_board_cycle_candidates.py --boards 锂矿,白酒 --limit-per-board 20 --output-dir data/manual_runs/board_cycle_scan_smoke_20260501
```

Expected:

- `board_summary.csv` and `board_stock_candidates.csv` exist under the smoke directory.
- Markdown outputs are generated.
- No crash if one board has partial or sparse earnings fields.

- [ ] **Step 5: Record the smoke evidence in `docs/AI_MODIFICATION_LOG.md`**

```markdown
- Smoke result:
  - Output dir: `data/manual_runs/board_cycle_scan_smoke_20260501`
  - Boards: `锂矿,白酒`
  - Verified artifacts: `board_summary.csv`, `board_summary.md`, `board_stock_candidates.csv`, `board_stock_candidates.md`
  - Notes: confirm repeated stocks only have one `primary_board`.
```

## Self-Review

- Spec coverage:
  - Independent on-demand board scanner: covered by Tasks 2 and 3.
  - Board score and stock role outputs: covered by Tasks 1 and 2.
  - `primary_board + related_boards`: covered by Tasks 1 and 2.
  - Documentation and local-strategy governance updates: covered by Task 4.
- Placeholder scan:
  - No placeholder markers remain.
  - Commands, file paths, and test names are concrete.
- Type consistency:
  - `BoardStockEvaluation`, `BoardCycleBoardResult`, `_score_board_stock`, `_score_board`, `_resolve_primary_boards`, and `write_outputs` are used consistently across tasks.
