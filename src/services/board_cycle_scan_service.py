# -*- coding: utf-8 -*-
"""Lightweight board cycle scan service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

LEADER_CANDIDATE_SCORE = 5.0
BOARD_MEMBERSHIP_LOGIC_SCORE = 2.0
LEADER_HINT_LOGIC_SCORE = 1.0
FUNDAMENTAL_KEYWORD_LOGIC_SCORE = 1.0
LEADER_MIN_SCORE = 4.0
EARNINGS_SUPPORTED_MIN_SCORE = 3.0


@dataclass
class BoardStockEvaluation:
    stock_code: str
    stock_name: str
    board_name: str
    board_type: str
    stock_role: str
    board_stock_score: float
    logic_match_score: float
    board_leader_score: float
    earnings_support_score: float
    earnings_supported: bool
    board_rank: int = 0
    selection_reason: str = ""
    primary_board: str = ""
    primary_board_reason: str = ""
    related_boards: List[str] = field(default_factory=list)


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
    leader_ratio: float = 0.0
    earnings_supported_ratio: float = 0.0
    board_reason_summary: str = ""
    top_leaders: List[str] = field(default_factory=list)
    top_earnings_supported: List[str] = field(default_factory=list)


class BoardCycleScanService:
    def __init__(self, *, manager: Optional[Any] = None) -> None:
        if manager is None:
            from data_provider import DataFetcherManager

            manager = DataFetcherManager()
        self.manager = manager

    def fetch_board_universe(self, *, board_name: str, board_type: str = "auto") -> Any:
        getter = getattr(self.manager, "get_board_constituents", None)
        if getter is None:
            raise RuntimeError("manager does not support board constituent loading")
        return getter(board_name, board_type=board_type)

    def scan_board_rows(
        self,
        *,
        board_name: str,
        board_type: str,
        stock_rows: Sequence[Dict[str, Any]],
    ) -> tuple[BoardCycleBoardResult, List[BoardStockEvaluation]]:
        evaluations: List[BoardStockEvaluation] = []
        for row in stock_rows or []:
            stock_code = str(row.get("stock_code") or row.get("code") or "").strip()
            stock_name = str(row.get("stock_name") or row.get("name") or "").strip()
            evaluation = self._score_board_stock(
                stock_code=stock_code,
                stock_name=stock_name,
                board_name=board_name,
                board_type=board_type,
                board_context={
                    "board_name": row.get("board_name") or board_name,
                    "board_type": row.get("board_type") or board_type,
                    "logic_keywords": row.get("logic_keywords"),
                    "leader_candidates": row.get("leader_candidates"),
                    "belong_boards_override": row.get("belong_boards"),
                    "fundamental_context_override": self._build_fundamental_context_override(row),
                },
            )
            evaluations.append(evaluation)

        board_result = self._score_board(
            board_name=board_name,
            board_type=board_type,
            constituent_count=len(stock_rows or []),
            evaluations=evaluations,
        )
        return board_result, evaluations

    def resolve_primary_boards_for_results(
        self,
        evaluations: Sequence[BoardStockEvaluation],
    ) -> List[BoardStockEvaluation]:
        grouped: Dict[str, List[BoardStockEvaluation]] = {}
        for item in evaluations or []:
            grouped.setdefault(str(item.stock_code or ""), []).append(item)

        for stock_code, stock_evaluations in grouped.items():
            if not stock_evaluations:
                continue
            self._resolve_primary_boards(
                stock_code=stock_code,
                stock_name=stock_evaluations[0].stock_name,
                evaluations=stock_evaluations,
            )
        return list(evaluations or [])

    def limit_stock_results_per_board(
        self,
        evaluations: Sequence[BoardStockEvaluation],
        *,
        top_per_board: int,
    ) -> List[BoardStockEvaluation]:
        if int(top_per_board or 0) <= 0:
            return list(evaluations or [])

        grouped: Dict[str, List[BoardStockEvaluation]] = {}
        for item in evaluations or []:
            grouped.setdefault(str(item.board_name or ""), []).append(item)

        limited: List[BoardStockEvaluation] = []
        for items in grouped.values():
            ranked = sorted(
                items,
                key=lambda item: (
                    float(getattr(item, "board_stock_score", 0.0) or 0.0),
                    float(getattr(item, "logic_match_score", 0.0) or 0.0),
                    str(getattr(item, "stock_code", "") or ""),
                ),
                reverse=True,
            )
            for rank, item in enumerate(ranked, start=1):
                item.board_rank = rank
                item.selection_reason = (
                    f"rank#{rank} | role={item.stock_role} | total={item.board_stock_score:.2f} | "
                    f"logic={item.logic_match_score:.2f} | leader={item.board_leader_score:.2f} | "
                    f"earnings={item.earnings_support_score:.2f}"
                )
            limited.extend(ranked[: int(top_per_board)])
        return limited

    def _score_board_stock(
        self,
        *,
        stock_code: str,
        stock_name: str,
        board_name: str,
        board_type: str,
        board_context: Optional[Dict[str, Any]] = None,
    ) -> BoardStockEvaluation:
        context = board_context or {}
        logic_keywords = self._normalize_text_list(context.get("logic_keywords"))
        leader_candidates = self._normalize_text_list(context.get("leader_candidates"))
        fundamental_context_override = context.get("fundamental_context_override")
        belong_boards_override = self._normalize_text_list(context.get("belong_boards_override"))
        fundamental_context = (
            fundamental_context_override
            if isinstance(fundamental_context_override, dict) and fundamental_context_override
            else self._safe_get_fundamental_context(stock_code)
        )
        belong_boards = (
            belong_boards_override if belong_boards_override else self._safe_get_belong_boards(stock_code)
        )
        normalized_board_name = str(board_name or "").strip().lower()

        board_leader_score = 0.0
        if stock_name and stock_name in leader_candidates:
            board_leader_score += LEADER_CANDIDATE_SCORE

        logic_match_score = 0.0
        normalized_belong_boards = [item.lower() for item in belong_boards]
        if normalized_board_name and normalized_board_name in normalized_belong_boards:
            logic_match_score += BOARD_MEMBERSHIP_LOGIC_SCORE

        if stock_name and stock_name in leader_candidates:
            logic_match_score += LEADER_HINT_LOGIC_SCORE

        fundamental_text = self._flatten_text(fundamental_context).lower()
        if logic_keywords and fundamental_text:
            for keyword in logic_keywords:
                normalized_keyword = keyword.lower()
                if not normalized_keyword:
                    continue
                if normalized_keyword in fundamental_text:
                    logic_match_score += FUNDAMENTAL_KEYWORD_LOGIC_SCORE
                    break

        earnings_support_score = self._score_earnings_support(fundamental_context)
        earnings_supported = earnings_support_score > 0

        board_stock_score = round(
            board_leader_score + logic_match_score + earnings_support_score,
            2,
        )

        if (
            board_leader_score >= LEADER_MIN_SCORE
            and logic_match_score > 0
            and earnings_support_score > 0
        ):
            stock_role = "leader"
        elif earnings_support_score >= EARNINGS_SUPPORTED_MIN_SCORE:
            stock_role = "earnings_supported"
        else:
            stock_role = "watch"

        return BoardStockEvaluation(
            stock_code=str(stock_code or ""),
            stock_name=str(stock_name or ""),
            board_name=str(board_name or ""),
            board_type=str(board_type or ""),
            stock_role=stock_role,
            board_stock_score=board_stock_score,
            logic_match_score=round(logic_match_score, 2),
            board_leader_score=round(board_leader_score, 2),
            earnings_support_score=round(earnings_support_score, 2),
            earnings_supported=earnings_supported,
            related_boards=belong_boards,
        )

    def _score_board(
        self,
        *,
        board_name: str,
        board_type: str,
        evaluations: Sequence[Any],
        constituent_count: Optional[int] = None,
    ) -> BoardCycleBoardResult:
        items = list(evaluations or [])
        total_count = int(constituent_count if constituent_count is not None else len(items))
        qualified_stock_count = sum(
            1 for item in items if float(getattr(item, "board_stock_score", 0.0) or 0.0) > 0
        )
        leader_count = sum(1 for item in items if getattr(item, "stock_role", "") == "leader")
        earnings_supported_count = sum(
            1 for item in items if bool(getattr(item, "earnings_supported", False))
        )

        breadth_score = round(float(qualified_stock_count), 2)
        leadership_score = round(float(leader_count * 3.0), 2)
        earnings_support_score = round(float(earnings_supported_count * 2.0), 2)
        structure_score = round(
            sum(float(getattr(item, "logic_match_score", 0.0) or 0.0) for item in items),
            2,
        )
        board_cycle_score = round(
            breadth_score + leadership_score + earnings_support_score + structure_score,
            2,
        )

        if leader_count >= 1 and earnings_supported_count >= 1 and board_cycle_score > 0:
            board_cycle_label = "strengthening"
        elif board_cycle_score > 0:
            board_cycle_label = "watch"
        else:
            board_cycle_label = "idle"

        top_leaders = [
            str(getattr(item, "stock_name", "") or "")
            for item in sorted(
                items,
                key=lambda item: float(getattr(item, "board_stock_score", 0.0) or 0.0),
                reverse=True,
            )
            if getattr(item, "stock_role", "") == "leader"
        ][:3]
        top_earnings_supported = [
            str(getattr(item, "stock_name", "") or "")
            for item in sorted(
                items,
                key=lambda item: float(getattr(item, "earnings_support_score", 0.0) or 0.0),
                reverse=True,
            )
            if bool(getattr(item, "earnings_supported", False))
        ][:3]
        leader_ratio = round(leader_count / total_count, 4) if total_count > 0 else 0.0
        earnings_supported_ratio = (
            round(earnings_supported_count / total_count, 4) if total_count > 0 else 0.0
        )
        board_reason_summary = self._build_board_reason_summary(
            board_name=str(board_name or ""),
            total_count=total_count,
            qualified_stock_count=qualified_stock_count,
            leader_count=leader_count,
            earnings_supported_count=earnings_supported_count,
            board_cycle_label=board_cycle_label,
        )

        return BoardCycleBoardResult(
            board_name=str(board_name or ""),
            board_type=str(board_type or ""),
            constituent_count=total_count,
            qualified_stock_count=qualified_stock_count,
            leader_count=leader_count,
            earnings_supported_count=earnings_supported_count,
            breadth_score=breadth_score,
            leadership_score=leadership_score,
            earnings_support_score=earnings_support_score,
            structure_score=structure_score,
            board_cycle_score=board_cycle_score,
            board_cycle_label=board_cycle_label,
            leader_ratio=leader_ratio,
            earnings_supported_ratio=earnings_supported_ratio,
            board_reason_summary=board_reason_summary,
            top_leaders=top_leaders,
            top_earnings_supported=top_earnings_supported,
        )

    def _resolve_primary_boards(
        self,
        *,
        stock_code: str,
        stock_name: str,
        evaluations: Sequence[Any],
    ) -> Dict[str, Any]:
        items = list(evaluations or [])
        if not items:
            return {
                "stock_code": str(stock_code or ""),
                "stock_name": str(stock_name or ""),
                "primary_board": "",
                "primary_board_reason": "no_evaluations",
            }

        ranked = sorted(
            items,
            key=lambda item: (
                float(getattr(item, "logic_match_score", 0.0) or 0.0),
                float(getattr(item, "board_leader_score", 0.0) or 0.0),
                float(getattr(item, "earnings_support_score", 0.0) or 0.0),
                float(getattr(item, "board_stock_score", 0.0) or 0.0),
            ),
            reverse=True,
        )
        winner = ranked[0]
        reason = (
            "logic_match="
            f"{float(getattr(winner, 'logic_match_score', 0.0) or 0.0):.2f}, "
            "leader_score="
            f"{float(getattr(winner, 'board_leader_score', 0.0) or 0.0):.2f}, "
            "earnings_score="
            f"{float(getattr(winner, 'earnings_support_score', 0.0) or 0.0):.2f}"
        )
        primary_board = str(getattr(winner, "board_name", "") or "")
        related_boards = [
            str(getattr(item, "board_name", "") or "")
            for item in items
            if str(getattr(item, "board_name", "") or "")
        ]
        for item in items:
            if hasattr(item, "primary_board"):
                item.primary_board = primary_board
            if hasattr(item, "primary_board_reason"):
                item.primary_board_reason = reason
            if hasattr(item, "related_boards"):
                item.related_boards = [name for name in related_boards if name != primary_board]

        return {
            "stock_code": str(stock_code or ""),
            "stock_name": str(stock_name or ""),
            "primary_board": primary_board,
            "primary_board_reason": reason,
        }

    def _safe_get_fundamental_context(self, stock_code: str) -> Dict[str, Any]:
        try:
            context = self.manager.get_fundamental_context(stock_code)
        except Exception as exc:
            builder = getattr(self.manager, "build_failed_fundamental_context", None)
            if callable(builder):
                return builder(stock_code, str(exc))
            return {"status": "failed", "errors": [str(exc)]}
        return context if isinstance(context, dict) else {}

    def _safe_get_belong_boards(self, stock_code: str) -> List[str]:
        try:
            boards = self.manager.get_belong_boards(stock_code)
        except Exception:
            return []
        names: List[str] = []
        for item in boards or []:
            if isinstance(item, dict):
                name = str(item.get("name") or "").strip()
                if name:
                    names.append(name)
            elif isinstance(item, str):
                name = item.strip()
                if name:
                    names.append(name)
        return names

    def _score_earnings_support(self, fundamental_context: Dict[str, Any]) -> float:
        if not isinstance(fundamental_context, dict):
            return 0.0

        growth_data = self._extract_data_block(fundamental_context.get("growth"))
        earnings_data = self._extract_data_block(fundamental_context.get("earnings"))
        quality_data = self._extract_data_block(fundamental_context.get("earnings_quality"))

        if not earnings_data:
            return 0.0

        financial_report = earnings_data.get("financial_report")
        if not isinstance(financial_report, dict) or not financial_report.get("report_date"):
            return 0.0

        score = 0.0
        revenue_yoy = self._to_float(growth_data.get("revenue_yoy"))
        net_profit_yoy = self._to_float(growth_data.get("net_profit_yoy"))
        if revenue_yoy is not None and revenue_yoy > 0:
            score += 1.0
        if net_profit_yoy is not None and net_profit_yoy > 0:
            score += 1.0

        verdict = str(quality_data.get("verdict") or "").strip().lower()
        if verdict in {"good", "strong", "positive"}:
            score += 1.0

        total_quality_score = self._to_float(quality_data.get("score_total"))
        if total_quality_score is not None and total_quality_score >= 70:
            score += 1.0

        cycle_phase = quality_data.get("cycle_analysis")
        if isinstance(cycle_phase, dict):
            phase_name = str(cycle_phase.get("phase") or "").strip().lower()
            if phase_name in {"reaccelerating", "improving", "expanding"}:
                score += 1.0

        return round(score, 2)

    def _build_fundamental_context_override(self, row: Dict[str, Any]) -> Dict[str, Any]:
        revenue_yoy = self._to_float(row.get("revenue_yoy"))
        net_profit_yoy = self._to_float(row.get("net_profit_yoy"))
        report_date = str(row.get("earnings_report_date") or "").strip()
        verdict = str(row.get("earnings_quality_verdict") or "").strip()
        score_total = self._to_float(row.get("earnings_quality_score_total"))
        cycle_phase = str(row.get("earnings_cycle_phase") or "").strip()

        if (
            revenue_yoy is None
            and net_profit_yoy is None
            and not report_date
            and not verdict
            and score_total is None
            and not cycle_phase
        ):
            return {}

        payload: Dict[str, Any] = {
            "status": "ok",
            "growth": {"data": {}},
            "earnings": {"data": {"financial_report": {}}},
            "earnings_quality": {"data": {}},
        }
        if revenue_yoy is not None:
            payload["growth"]["data"]["revenue_yoy"] = revenue_yoy
        if net_profit_yoy is not None:
            payload["growth"]["data"]["net_profit_yoy"] = net_profit_yoy
        if report_date:
            payload["earnings"]["data"]["financial_report"]["report_date"] = report_date
        if verdict:
            payload["earnings_quality"]["data"]["verdict"] = verdict
        if score_total is not None:
            payload["earnings_quality"]["data"]["score_total"] = score_total
        if cycle_phase:
            payload["earnings_quality"]["data"]["cycle_analysis"] = {"phase": cycle_phase}
        return payload

    def _build_board_reason_summary(
        self,
        *,
        board_name: str,
        total_count: int,
        qualified_stock_count: int,
        leader_count: int,
        earnings_supported_count: int,
        board_cycle_label: str,
    ) -> str:
        if total_count <= 0:
            return f"{board_name} 成分股暂未取到，当前结果仅表示上游板块成分为空。"
        if board_cycle_label == "strengthening":
            return (
                f"{board_name} 当前偏强：{leader_count}/{total_count} 为龙头样本，"
                f"{earnings_supported_count}/{total_count} 具备业绩支撑，"
                f"{qualified_stock_count}/{total_count} 进入有效观察范围。"
            )
        if board_cycle_label == "watch":
            return (
                f"{board_name} 当前处于观察阶段：{qualified_stock_count}/{total_count} 有效样本中，"
                f"龙头样本 {leader_count} 只，业绩支撑样本 {earnings_supported_count} 只。"
            )
        return (
            f"{board_name} 当前信号偏弱：有效样本 {qualified_stock_count}/{total_count}，"
            f"龙头样本 {leader_count} 只，业绩支撑样本 {earnings_supported_count} 只。"
        )

    def _extract_data_block(self, block: Any) -> Dict[str, Any]:
        if isinstance(block, dict):
            data = block.get("data")
            if isinstance(data, dict):
                return data
            return block if any(key in block for key in ("revenue_yoy", "net_profit_yoy", "financial_report")) else {}
        return {}

    def _normalize_text_list(self, value: Any) -> List[str]:
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, Iterable):
            values: List[str] = []
            for item in value:
                text = str(item or "").strip()
                if text:
                    values.append(text)
            return values
        return []

    def _flatten_text(self, value: Any) -> str:
        chunks: List[str] = []

        def _walk(node: Any) -> None:
            if isinstance(node, dict):
                for child in node.values():
                    _walk(child)
                return
            if isinstance(node, (list, tuple, set)):
                for child in node:
                    _walk(child)
                return
            if node is None:
                return
            text = str(node).strip()
            if text:
                chunks.append(text)

        _walk(value)
        return " ".join(chunks)

    def _to_float(self, value: Any) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
