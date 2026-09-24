# -*- coding: utf-8 -*-
"""Stock-centered personal strategy matrix service."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.services.fast_review_focus_service import (
    DEFAULT_MANUAL_RUNS_ROOT,
    FastReviewFocusService,
)


PERSONAL_STRATEGY_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "earnings_surprise",
        "name": "业绩强势 earnings_surprise",
        "short_name": "业绩",
        "group": "daily",
        "group_label": "日复盘默认信号",
        "mode": "默认日复盘",
        "aliases": ["earnings", "earnings_surprise", "earnings_surprise_strict", "earnings_surprise_relaxed"],
        "role": "先筛出业绩明显改善或超预期的股票，作为“业绩好”这一维证据。",
        "logic": "读取业绩快照、公告与财务指标，重点看营收/净利同比、ROE、盈利质量、报告期与机构预期；命中后仍需要结合板块、位置和想象空间二次判断。",
    },
    {
        "id": "hundred_day_high",
        "name": "百日新高 hundred_day_high",
        "short_name": "新高",
        "group": "daily",
        "group_label": "日复盘默认信号",
        "mode": "默认日复盘",
        "aliases": ["hundred_day_high"],
        "role": "识别价格已经摆脱中期平台、市场开始给出强度确认的股票。",
        "logic": "基于最近交易日是否接近或突破百日高点，并结合涨幅、回撤和流动性做基础过滤；适合和业绩、题材共振一起看。",
    },
    {
        "id": "daily_slow_rise",
        "name": "日线慢涨 daily_slow_rise",
        "short_name": "慢涨",
        "group": "daily",
        "group_label": "日复盘默认信号",
        "mode": "默认日复盘",
        "aliases": ["daily_slow_rise"],
        "role": "寻找近阶段阳线多、走势没有明显走差、缓慢抬升的图形。",
        "logic": "当前使用 accelerating 画像，关注近 20-30 个交易日上涨日占比、区间涨幅、回撤、均线结构、量能与最近走势是否破坏；这是纯图形观察的核心入口。",
    },
    {
        "id": "long_base_release",
        "name": "长平台释放 long_base_release",
        "short_name": "平台",
        "group": "daily",
        "group_label": "日复盘默认信号",
        "mode": "默认日复盘",
        "aliases": ["long_base_release"],
        "role": "找长期横盘后开始放量/突破的结构，偏“底部平台被资金重新定价”。",
        "logic": "当前使用 loose 画像，关注长周期压缩、平台区间、近期放量与突破关系；命中后要继续看行业估值、板块风口和个股辨识度。",
    },
    {
        "id": "trend_leader_unified",
        "name": "趋势龙头统一扫描 trend_leader_unified",
        "short_name": "趋势",
        "group": "chart",
        "group_label": "趋势与纯图形观察",
        "mode": "非默认扫描",
        "aliases": ["trend_leader", "trend_leader_unified", "trend_leader_unified_watchlist"],
        "role": "寻找阶段涨幅、强度和流动性都靠前的趋势股。",
        "logic": "以 60 日涨幅、换手率、相对强度、行业共振和风险过滤做预筛；当前不在默认日复盘信号集合里，更多用于专题扫描或候选池补充。",
    },
    {
        "id": "monthly_slow_rise",
        "name": "月线慢涨 monthly_slow_rise",
        "short_name": "月慢",
        "group": "chart",
        "group_label": "趋势与纯图形观察",
        "mode": "非默认扫描",
        "aliases": ["monthly_slow_rise"],
        "role": "从更长周期确认慢牛或长趋势，不追求短期爆发。",
        "logic": "按月线/长周期结构看上涨连续性、回撤、均线与平台抬升；适合辅助判断“位置是否舒服”，不适合单独作为短线买点。",
    },
    {
        "id": "continuous_up_ratio",
        "name": "连续上涨占比 continuous_up_ratio",
        "short_name": "阳线",
        "group": "chart",
        "group_label": "趋势与纯图形观察",
        "mode": "非默认扫描",
        "aliases": ["continuous_up_ratio"],
        "role": "量化最近一段时间阳线占比，过滤阴跌或走势松散的图形。",
        "logic": "统计近 N 日上涨日比例、区间涨幅和回撤，强调“阳线多、占比大、最近没有走差”；可作为纯图形票的加分项。",
    },
    {
        "id": "continuous_up_streak",
        "name": "连续上涨段 continuous_up_streak",
        "short_name": "连涨",
        "group": "chart",
        "group_label": "趋势与纯图形观察",
        "mode": "非默认扫描",
        "aliases": ["continuous_up_streak"],
        "role": "识别短期连续上涨段，用来观察强势延续或过热风险。",
        "logic": "统计连续上涨天数、涨幅和回撤风险；更适合做短线强度提示，不适合替代板块和基本面判断。",
    },
    {
        "id": "earnings_observation",
        "name": "业绩观察 earnings_observation",
        "short_name": "业观",
        "group": "earnings",
        "group_label": "业绩观察",
        "mode": "观察池工具",
        "aliases": ["earnings_observation", "earnings_observation_active", "earnings_observation_registry"],
        "role": "对业绩出来后表现不错的股票持续跟踪，而不是只在公告当天看一次。",
        "logic": "维护观察池状态，记录业绩触发、后续价格表现、观察阶段与是否仍值得跟踪；适合承接“业绩好但不一定马上涨”的情况。",
    },
    {
        "id": "dragon_head_candidate",
        "name": "龙头候选 dragon_head_candidate",
        "short_name": "龙头",
        "group": "theme",
        "group_label": "板块、题材与涨价链",
        "mode": "专题工具",
        "aliases": ["dragon_head_candidate"],
        "role": "从同题材或同板块中找辨识度更高的核心票。",
        "logic": "综合涨幅排名、强度、成交、题材位置、同板块对照和主线证据，判断是否更像板块核心而不是普通跟涨。",
    },
    {
        "id": "board_cycle_scan",
        "name": "板块周期 board_cycle_scan",
        "short_name": "板块",
        "group": "theme",
        "group_label": "板块、题材与涨价链",
        "mode": "专题工具",
        "aliases": ["board_cycle_scan"],
        "role": "判断股票所在板块是否处于可交易周期，而不是孤立看个股业绩。",
        "logic": "扫描板块热度、涨幅扩散、持续性、估值位置和阶段；用于回答“板块垃圾、不在风口”的核心问题。",
    },
    {
        "id": "theme_core_mapper",
        "name": "题材核心映射 theme_core_mapper",
        "short_name": "题材",
        "group": "theme",
        "group_label": "板块、题材与涨价链",
        "mode": "专题工具",
        "aliases": ["theme_core_mapper", "theme_core_candidate", "board_theme_core"],
        "role": "把个股业务映射到题材主线，判断有没有想象空间。",
        "logic": "结合主营、公告、新闻、产业链角色和同题材个股，给出业务标签、主题标签与主线判断；用于补“这家公司到底炒什么”。",
    },
    {
        "id": "commodity_price_pass_through",
        "name": "涨价传导 commodity_price_pass_through",
        "short_name": "涨价",
        "group": "theme",
        "group_label": "板块、题材与涨价链",
        "mode": "专题工具",
        "aliases": ["commodity_price_pass_through", "commodity_beneficiary"],
        "role": "观察商品、原材料或产品涨价是否能传导到公司利润。",
        "logic": "按商品价格、产业链位置、成本/售价弹性和受益方向判断，识别涨价受益股；页面会把 commodity_beneficiary__* 这类专题信号也归到该策略。",
    },
    {
        "id": "shortline_hub",
        "name": "短线中枢 shortline_hub",
        "short_name": "短线",
        "group": "tool",
        "group_label": "短线工具",
        "mode": "工具输出",
        "aliases": ["shortline_hub", "shortline_top_pick", "shortline_watchlist", "shortline_high_risk_mover"],
        "role": "汇总短线强势、观察和高风险移动票，辅助盘中/盘后快速查看。",
        "logic": "聚合短线涨幅、量能、题材、风险标记和候选池状态；第一版页面只展示其命中关系，不单独展开短线交易规则。",
    },
]

CHART_STRATEGY_IDS = {
    "daily_slow_rise",
    "long_base_release",
    "hundred_day_high",
    "trend_leader_unified",
    "monthly_slow_rise",
    "continuous_up_ratio",
    "continuous_up_streak",
}
EARNINGS_STRATEGY_IDS = {"earnings_surprise", "earnings_observation"}
THEME_STRATEGY_IDS = {
    "dragon_head_candidate",
    "board_cycle_scan",
    "theme_core_mapper",
    "commodity_price_pass_through",
}
QUALITY_BAND_LABELS = {
    "recommended": "优先看",
    "watch": "可观察",
    "weak": "偏弱",
}
SHORT_TERM_STRATEGY_IDS = {
    "hundred_day_high",
    "trend_leader_unified",
    "shortline_hub",
    "dragon_head_candidate",
}
LONG_TERM_DISCOVERY_STRATEGY_IDS = {
    "daily_slow_rise",
    "long_base_release",
    "monthly_slow_rise",
    "continuous_up_ratio",
    "continuous_up_streak",
    "earnings_observation",
}
VIEW_LANE_LABELS = {
    "short_term": "短线主升",
    "long_term": "长线发现",
    "watch": "其他观察",
}


def signal_matches_personal_strategy(signal: str, strategy: Dict[str, Any]) -> bool:
    normalized_signal = str(signal or "").strip()
    if not normalized_signal:
        return False
    for alias in strategy.get("aliases", []):
        alias_text = str(alias or "").strip()
        if not alias_text:
            continue
        if (
            normalized_signal == alias_text
            or normalized_signal.startswith(f"{alias_text}__")
            or normalized_signal.startswith(f"{alias_text}:")
        ):
            return True
    return False


class PersonalStrategyMatrixService:
    """Build a stock -> matched personal strategies matrix from review artifacts."""

    def __init__(self, manual_runs_root: Optional[Path] = None) -> None:
        self.manual_runs_root = Path(manual_runs_root or DEFAULT_MANUAL_RUNS_ROOT)
        self.fast_review_service = FastReviewFocusService(manual_runs_root=self.manual_runs_root)

    def get_catalog(self) -> List[Dict[str, Any]]:
        return [dict(strategy) for strategy in PERSONAL_STRATEGY_CATALOG]

    def get_matrix(
        self,
        *,
        snapshot_date: Any,
        code: Optional[str] = None,
        strategy_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        overview = self.fast_review_service.get_stock_overview(snapshot_date=snapshot_date, code=code)
        items = [
            self._attach_view_lane(self._attach_quality(self._attach_matches(item)))
            for item in overview.get("items", [])
        ]
        strategy_summary = self._build_strategy_summary(items)

        selected_strategy_ids = {
            str(strategy_id or "").strip()
            for strategy_id in (strategy_ids or [])
            if str(strategy_id or "").strip()
        }
        if selected_strategy_ids:
            items = [
                item
                for item in items
                if selected_strategy_ids.intersection(set(item.get("matched_strategy_ids") or []))
            ]

        items.sort(key=self._sort_key)

        return {
            "snapshot_date": overview["snapshot_date"],
            "total": len(items),
            "source_run_dir": overview["source_run_dir"],
            "source_csv_path": overview["source_csv_path"],
            "lane_summary": self._build_lane_summary(items),
            "signal_summary": self._build_signal_summary(items),
            "strategy_summary": strategy_summary,
            "strategies": self.get_catalog(),
            "items": items,
        }

    def _attach_matches(self, item: Dict[str, Any]) -> Dict[str, Any]:
        signals = self._collect_signals(item)
        matched: List[Dict[str, Any]] = []
        for strategy in PERSONAL_STRATEGY_CATALOG:
            if any(signal_matches_personal_strategy(signal, strategy) for signal in signals):
                matched.append(self._to_match(strategy))

        enriched = dict(item)
        enriched["matched_strategies"] = matched
        enriched["matched_strategy_ids"] = [strategy["id"] for strategy in matched]
        enriched["matched_strategy_count"] = len(matched)
        return enriched

    def _attach_quality(self, item: Dict[str, Any]) -> Dict[str, Any]:
        matched_strategy_ids = {
            str(strategy_id or "").strip()
            for strategy_id in item.get("matched_strategy_ids") or []
            if str(strategy_id or "").strip()
        }
        matched_strategy_count = len(matched_strategy_ids)
        positives: List[str] = []
        flags: List[str] = []
        score = 35.0

        priority_score = self._to_float(item.get("priority_score"))
        if priority_score is not None:
            score += min(25.0, max(0.0, priority_score) / 10.0)

        if matched_strategy_count >= 3:
            score += 12.0
            positives.append("多策略共振")
        elif matched_strategy_count == 2:
            score += 8.0
            positives.append("双策略共振")
        else:
            score -= 8.0
            flags.append("单策略孤证")

        lane = str(item.get("stock_review_lane") or item.get("stock_review_lane_label") or "").strip().lower()
        if lane.startswith("double_confirm") or "双确认" in lane:
            score += 10.0
            positives.append("双确认分层")
        elif lane == "earnings_first" or "业绩优先" in lane:
            score += 6.0
            positives.append("有业绩确认")
        elif lane == "chart_first" or "图形优先" in lane:
            score += 3.0
        elif lane == "trend_watch" or "趋势补充" in lane:
            score -= 4.0
            flags.append("仍偏观察层")

        today_change_pct = self._to_float(item.get("today_change_pct"))
        if today_change_pct is not None:
            if today_change_pct <= -5.0:
                score -= 18.0
                flags.append(f"当日明显走弱 {today_change_pct:.2f}%")
            elif today_change_pct <= -3.0:
                score -= 10.0
                flags.append(f"当日走弱 {today_change_pct:.2f}%")
            elif today_change_pct < 0:
                score -= 4.0
            elif today_change_pct <= 6.0:
                score += 4.0
                positives.append(f"当日强度 {today_change_pct:.2f}%")
            else:
                score += 2.0

        pure_chart_quality_passed = item.get("pure_chart_quality_passed")
        if pure_chart_quality_passed is True:
            score += 6.0
            positives.append("图形质检通过")
        elif pure_chart_quality_passed is False and matched_strategy_ids.intersection(CHART_STRATEGY_IDS):
            score -= 10.0
            flags.append("图形质检未通过")

        breakout_quality_score = self._to_float(item.get("breakout_quality_score"))
        if breakout_quality_score is not None:
            if breakout_quality_score >= 12.0:
                score += 6.0
                positives.append(f"突破质量 {breakout_quality_score:.0f}")
            elif breakout_quality_score >= 6.0:
                score += 3.0
            else:
                score -= 5.0
                flags.append("突破质量偏弱")

        earnings_strategy_score = self._to_float(item.get("earnings_strategy_score"))
        if earnings_strategy_score is not None:
            if earnings_strategy_score >= 70.0:
                score += 8.0
                positives.append(f"业绩策略分 {earnings_strategy_score:.0f}")
            elif earnings_strategy_score >= 55.0:
                score += 4.0
            elif matched_strategy_ids.intersection(EARNINGS_STRATEGY_IDS):
                score -= 6.0
                flags.append("业绩策略分偏低")

        earnings_gate_status = str(item.get("earnings_strategy_gate_status") or "").strip().lower()
        if "blocked" in earnings_gate_status:
            score -= 10.0
            if "quality" in earnings_gate_status:
                flags.append("业绩质量闸门阻断")
            else:
                flags.append("业绩闸门未通过")

        earnings_quality_score = self._to_float(item.get("earnings_quality_score"))
        if earnings_quality_score is not None:
            if earnings_quality_score >= 75.0:
                score += 10.0
                positives.append(f"业绩质量 {earnings_quality_score:.0f}")
            elif earnings_quality_score >= 60.0:
                score += 6.0
            elif earnings_quality_score >= 50.0:
                score += 2.0
            elif matched_strategy_ids.intersection(EARNINGS_STRATEGY_IDS):
                score -= 10.0
                flags.append("业绩质量偏弱")
        elif matched_strategy_ids.intersection(EARNINGS_STRATEGY_IDS):
            score -= 4.0
            flags.append("缺少业绩确认")

        capital_profile_score = self._to_float(item.get("capital_profile_score"))
        if capital_profile_score is not None and capital_profile_score > 0:
            if capital_profile_score >= 75.0:
                score += 8.0
                positives.append(f"资金画像 {capital_profile_score:.0f}")
            elif capital_profile_score >= 60.0:
                score += 5.0
            elif capital_profile_score < 45.0:
                score -= 6.0
                flags.append("资金承接偏弱")

        relative_strength_score = self._to_float(item.get("relative_strength_score"))
        if relative_strength_score is not None and relative_strength_score > 0:
            if relative_strength_score >= 85.0:
                score += 8.0
                positives.append(f"相对强度 {relative_strength_score:.0f}")
            elif relative_strength_score >= 65.0:
                score += 4.0
            elif relative_strength_score < 40.0:
                score -= 4.0
                flags.append("相对强度不足")

        net_profit_yoy = self._to_float(item.get("net_profit_yoy"))
        if net_profit_yoy is not None:
            if net_profit_yoy >= 80.0:
                score += 6.0
            elif net_profit_yoy >= 25.0:
                score += 4.0
            elif net_profit_yoy < 0 and matched_strategy_ids.intersection(EARNINGS_STRATEGY_IDS):
                score -= 5.0
                flags.append("净利未改善")

        institution_count = self._to_float(item.get("market_expectation_institution_count"))
        if institution_count is not None:
            if institution_count >= 10:
                score += 4.0
            elif institution_count >= 3:
                score += 2.0

        if (
            matched_strategy_count <= 1
            and matched_strategy_ids.intersection(CHART_STRATEGY_IDS)
            and not matched_strategy_ids.intersection(EARNINGS_STRATEGY_IDS | THEME_STRATEGY_IDS)
        ):
            score -= 8.0
            flags.append("缺少业绩或题材共振")

        score = round(min(100.0, max(0.0, score)), 1)
        severe_flags = {
            "图形质检未通过",
            "业绩质量闸门阻断",
        }
        has_severe_flag = any(flag in severe_flags or flag.startswith("当日明显走弱") for flag in flags)
        if score >= 75.0 and not has_severe_flag:
            quality_band = "recommended"
        elif score >= 55.0 and not has_severe_flag:
            quality_band = "watch"
        else:
            quality_band = "weak"

        summary_parts = flags[:3] if quality_band == "weak" else positives[:3]
        if not summary_parts:
            summary_parts = ["按原始策略命中阅读"]

        enriched = dict(item)
        enriched["quality_score"] = score
        enriched["quality_band"] = quality_band
        enriched["quality_label"] = QUALITY_BAND_LABELS[quality_band]
        enriched["quality_summary"] = " / ".join(summary_parts)
        enriched["quality_flags"] = flags
        return enriched

    def _attach_view_lane(self, item: Dict[str, Any]) -> Dict[str, Any]:
        matched_strategy_ids = {
            str(strategy_id or "").strip()
            for strategy_id in item.get("matched_strategy_ids") or []
            if str(strategy_id or "").strip()
        }
        if matched_strategy_ids.intersection(SHORT_TERM_STRATEGY_IDS):
            view_lane = "short_term"
            view_summary = "短线主升信号，用来优先看当下强度和资金确认。"
        elif matched_strategy_ids.intersection(LONG_TERM_DISCOVERY_STRATEGY_IDS | EARNINGS_STRATEGY_IDS):
            view_lane = "long_term"
            view_summary = "长线发现信号，用来沉淀形态、业绩或观察池线索。"
        else:
            view_lane = "watch"
            view_summary = "专题或工具信号，先作为辅助观察。"

        enriched = dict(item)
        enriched["view_lane"] = view_lane
        enriched["view_lane_label"] = VIEW_LANE_LABELS[view_lane]
        enriched["view_lane_summary"] = view_summary
        return enriched

    def _collect_signals(self, item: Dict[str, Any]) -> List[str]:
        values: List[str] = []
        seen: set[str] = set()
        for key in ("triggered_strategies", "signal_keys", "signal_types"):
            for value in item.get(key) or []:
                signal = str(value or "").strip()
                if signal and signal not in seen:
                    values.append(signal)
                    seen.add(signal)
        return values

    def _to_match(self, strategy: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": strategy["id"],
            "name": strategy["name"],
            "short_name": strategy["short_name"],
            "group": strategy["group"],
            "group_label": strategy["group_label"],
            "mode": strategy["mode"],
            "role": strategy["role"],
            "logic": strategy["logic"],
        }

    def _build_strategy_summary(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        summary = {strategy["id"]: 0 for strategy in PERSONAL_STRATEGY_CATALOG}
        for item in items:
            for strategy_id in item.get("matched_strategy_ids") or []:
                if strategy_id in summary:
                    summary[strategy_id] += 1
        return summary

    def _build_lane_summary(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for item in items:
            key = str(item.get("stock_review_lane_label") or item.get("stock_review_lane") or "未分层").strip()
            summary[key] = summary.get(key, 0) + 1
        return summary

    def _build_signal_summary(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        summary: Dict[str, int] = {}
        for item in items:
            for signal in item.get("triggered_strategies") or item.get("signal_keys") or []:
                key = str(signal or "").strip()
                if key:
                    summary[key] = summary.get(key, 0) + 1
        return summary

    def _sort_key(self, item: Dict[str, Any]) -> Any:
        view_lane_order = {"short_term": 0, "long_term": 1, "watch": 2}
        quality_band_order = {"recommended": 0, "watch": 1, "weak": 2}
        return (
            view_lane_order.get(str(item.get("view_lane") or ""), 9),
            quality_band_order.get(str(item.get("quality_band") or ""), 9),
            -(self._to_float(item.get("quality_score")) or 0.0),
            -(self._to_float(item.get("priority_score")) or 0.0),
            item.get("code") or "",
        )

    @staticmethod
    def _to_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
