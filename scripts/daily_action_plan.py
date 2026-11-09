#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日作战计划：市场开关 + 追高纪律 + 持有期纪律 + AI 二次审核。

把个人策略候选（快复盘 strategy_focus）与主工程能力合成为一页作战计划：

- 市场开关（regime）：沪深300（000300）close vs MA20/MA60
  → offensive / balanced / defensive，可 --update-profile 同步写回策略 profile；
- 追高过滤：MA5 乖离（口径对齐 `src/agent/skills/defaults.py`：
  <2% 最佳买点区间 / 2-5% 小仓介入 / >5% 严禁追高；双信号交叉放宽到 7%）；
- 持有期纪律：按 T+5~T+10 设复盘节点；T+1/T+3 是否兑现以近月净口径统计为准，不在文案里写死；
- AI 二次审核：候选池 =「最新快复盘 + 近 N 交易日信号」按股票去重合并，
  调用本机 FastAPI `POST /api/v1/agent/chat` 并发批量审核（需服务运行，可选）。

可调参数：`config/daily_plan_tuning.json`（阈值 / 审核数量 / 技能 / 并发等）。

产物（默认写到 data/strategy_review/）：
- `daily_action_plan_<date>.md` / `daily_action_plan_latest.md`
- `daily_ai_reviews_<date>.md` / `daily_ai_reviews_latest.md`（全量审核文本）
"""

from __future__ import annotations

import argparse
import concurrent.futures
import copy
import csv
import json
import sqlite3
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.signal_recent_stats import (  # noqa: E402
    fmt_stat,
    load_recent_stats,
    merged_values,
    window_of,
)

DEFAULT_REVIEW_ROOT = PROJECT_ROOT / "data" / "manual_runs" / "personal_strategy_matrix"
DEFAULT_DB = PROJECT_ROOT / "data" / "stock_analysis.db"
DEFAULT_PROFILE = PROJECT_ROOT / "config" / "local_strategy_profile.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "strategy_review"

INDEX_CODE = "000300"
REGIME_NAMES = {"offensive": "进攻市", "balanced": "震荡市", "defensive": "防守市"}
REGIME_ADVICE = {
    "offensive": "趋势市：可按纪律正常执行，仍避免 >5% 乖离追高，优先双确认样本。",
    "balanced": "震荡市：半仓以内；只做「双确认 + 不追高」的样本，单信号只观察。",
    "defensive": "防守市：以观察为主、半仓以下；不做单信号追进，等双确认或回踩确认再考虑。",
}
BIAS_BEST = 2.0          # 乖离 < 2%：最佳买点区间
BIAS_STRONG = 7.0        # 双信号交叉时的放宽阈值
BIAS_DEFAULT = 5.0       # 单信号阈值：> 5% 严禁追高
HOLDING_NOTE = (
    "持有期纪律：按 T+5 / T+10 设复盘节点；T+1/T+3 兑现需先看下方近月净口径统计；"
    "追高降级样本等回踩 MA5/MA10 确认再定。"
)

TUNING_FILE = PROJECT_ROOT / "config" / "daily_plan_tuning.json"
RECENT_SIGNAL_CSV = PROJECT_ROOT / "data" / "strategy_review" / "daily_review_latest.csv"

DEFAULT_TUNING: dict = {
    "index_code": INDEX_CODE,
    "candidates_top": 12,
    "bias_best": BIAS_BEST,
    "bias_warn": BIAS_DEFAULT,
    "bias_strong": BIAS_STRONG,
    "defensive_demote_single": ["hundred_day_high"],
    "ai_review": {
        "enabled": True,
        "top": 80,
        "skill": "bull_trend",
        "workers": 4,
        "inline": 3,
        "url": "http://127.0.0.1:8000",
        "recent_signal_days": 10,
    },
}


def load_tuning(path: Path | None = None) -> dict:
    """读取调参文件并深合并到内置默认（缺文件/坏文件时回退默认）。"""
    tuning = copy.deepcopy(DEFAULT_TUNING)
    p = Path(path) if path else TUNING_FILE
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return tuning
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == "ai_review" and isinstance(value, dict):
                tuning["ai_review"].update(value)
            else:
                tuning[key] = value
    return tuning


def resolve_review_dir(root: Path, date_arg: str) -> tuple[Path, str]:
    """解析 review 目录；date_arg=latest/auto 时取最新有 strategy_focus 的日期。"""
    if date_arg and date_arg not in ("latest", "auto"):
        target = root / date_arg / "review"
        if not (target / "fast_review_strategy_focus.csv").exists():
            raise SystemExit(f"找不到指定日期的快复盘产物: {target}")
        return target, date_arg
    candidates = sorted(
        (d for d in root.iterdir() if d.is_dir() and d.name[:4].isdigit()),
        reverse=True,
    )
    for d in candidates:
        if (d / "review" / "fast_review_strategy_focus.csv").exists():
            return d / "review", d.name
    raise SystemExit(f"在 {root} 下找不到任何 fast_review_strategy_focus.csv")


def load_candidates(review_dir: Path, limit: int) -> list[dict]:
    path = review_dir / "fast_review_strategy_focus.csv"
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    picked = []
    for row in rows[:limit]:
        picked.append(
            {
                "code": (row.get("code") or "").strip(),
                "name": (row.get("name") or "").strip(),
                "tier": (row.get("tier") or "").strip(),
                "ab_bucket": (row.get("ab_bucket") or "").strip(),
                "signals": (row.get("signal_keys") or row.get("signal_types") or "").strip(),
                "group": (row.get("review_display_group_label") or "").strip(),
                "reason": (row.get("reason_summary") or "").strip(),
            }
        )
    return picked


def load_overview_pool(review_dir: Path) -> list[dict]:
    """读取快复盘 stock_overview（已按股票聚合 signal_keys / strategy_count）。"""
    path = review_dir / "fast_review_stock_overview.csv"
    entries: list[dict] = []
    if not path.exists():
        return entries
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for i, row in enumerate(csv.DictReader(fh)):
            code = (row.get("code") or "").strip()
            if not code:
                continue
            try:
                priority = float(row.get("priority_score") or 0)
            except ValueError:
                priority = 0.0
            signals = [s for s in (row.get("signal_keys") or "").replace(",", ";").split(";") if s.strip()]
            entries.append(
                {
                    "code": code,
                    "name": (row.get("name") or "").strip(),
                    "sources": ["快复盘"],
                    "signals": signals,
                    "priority": priority,
                    "order": i,
                }
            )
    return entries


def load_recent_signal_pool(csv_path: Path, days: int) -> list[dict]:
    """近 N 个交易日信号按股票去重（合并信号类型，按最近日期与出现次数排序）。"""
    if not csv_path.exists() or days <= 0:
        return []
    with open(csv_path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    dates = sorted(
        {(r.get("signal_date") or "").strip() for r in rows if (r.get("signal_date") or "").strip()},
        reverse=True,
    )[:days]
    date_set = set(dates)
    grouped: dict[str, dict] = {}
    for r in rows:
        d = (r.get("signal_date") or "").strip()
        if d not in date_set:
            continue
        if (r.get("selection_mode") or "").strip().lower() == "fallback":
            continue  # 兜底样本不进候选池（2026-10-05 分层约定）
        code = (r.get("code") or "").strip()
        if not code:
            continue
        g = grouped.setdefault(
            code,
            {
                "code": code,
                "name": (r.get("name") or "").strip(),
                "sources": ["近期信号"],
                "signals": set(),
                "latest": d,
                "count": 0,
            },
        )
        g["count"] += 1
        sig = (r.get("signal_type") or "").strip()
        if sig:
            g["signals"].add(sig)
        if d > g["latest"]:
            g["latest"] = d
            g["name"] = (r.get("name") or "").strip() or g["name"]
    ordered = sorted(grouped.values(), key=lambda g: (g["latest"], g["count"]), reverse=True)
    result: list[dict] = []
    for i, g in enumerate(ordered):
        result.append(
            {
                "code": g["code"],
                "name": g["name"],
                "sources": g["sources"],
                "signals": sorted(g["signals"]),
                "priority": 0.0,
                "order": i,
            }
        )
    return result


def dedupe_pool(*pools: list[dict]) -> list[dict]:
    """按代码去重合并多个来源；先出现的来源优先，合并来源/信号标签。"""
    merged: dict[str, dict] = {}
    for pool in pools:
        for item in pool:
            code = item["code"]
            if code in merged:
                exist = merged[code]
                for src in item.get("sources", []):
                    if src not in exist["sources"]:
                        exist["sources"].append(src)
                for sig in item.get("signals", []):
                    if sig and sig not in exist["signals"]:
                        exist["signals"].append(sig)
                exist["priority"] = max(exist.get("priority", 0.0), item.get("priority", 0.0))
                continue
            merged[code] = {
                "code": code,
                "name": item.get("name", ""),
                "sources": list(item.get("sources", [])),
                "signals": list(item.get("signals", [])),
                "priority": item.get("priority", 0.0),
                "order": item.get("order", 0),
            }
    return list(merged.values())


def _connect(db: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def read_index_regime(db: Path, code: str = INDEX_CODE) -> dict:
    """用指数收盘 vs MA20/MA60 判定市场状态。"""
    try:
        with _connect(db) as con:
            rows = con.execute(
                "select date, close from stock_daily where code = ? order by date desc limit 90",
                (code,),
            ).fetchall()
    except sqlite3.Error as exc:
        return {"ok": False, "reason": f"数据库读取失败: {exc}"}
    if len(rows) < 60:
        return {"ok": False, "reason": f"{code} 数据不足（{len(rows)} 行）"}
    rows = list(reversed(rows))
    closes = [float(r[1]) for r in rows]
    last_date, last_close = rows[-1][0], closes[-1]
    ma20 = sum(closes[-20:]) / 20.0
    ma60 = sum(closes[-60:]) / 60.0
    if last_close > ma20 > ma60:
        regime = "offensive"
    elif last_close < ma20 and ma20 < ma60:
        regime = "defensive"
    else:
        regime = "balanced"
    return {
        "ok": True,
        "regime": regime,
        "code": code,
        "date": last_date,
        "close": last_close,
        "ma20": ma20,
        "ma60": ma60,
    }


def read_bias(db: Path, code: str) -> dict:
    """读取个股最新收盘与 MA5/MA10 乖离（%）。"""
    if not code:
        return {"ok": False, "reason": "无代码"}
    try:
        with _connect(db) as con:
            rows = con.execute(
                "select date, close, ma5, ma10, ma20 from stock_daily where code = ? "
                "order by date desc limit 2",
                (code,),
            ).fetchall()
            if not rows:
                rows = con.execute(
                    "select date, close, ma5, ma10, ma20 from stock_daily where code like ? "
                    "order by date desc limit 2",
                    (f"%{code}%",),
                ).fetchall()
    except sqlite3.Error as exc:
        return {"ok": False, "reason": f"数据库读取失败: {exc}"}
    if not rows:
        return {"ok": False, "reason": "stock_daily 无数据"}
    date, close, ma5, ma10, ma20 = rows[0][:5]
    close = float(close)

    def _bias(ma):
        try:
            ma = float(ma)
        except (TypeError, ValueError):
            return None
        return None if ma <= 0 else (close - ma) / ma * 100.0
    bias5, bias10, bias20 = _bias(ma5), _bias(ma10), _bias(ma20)
    if bias5 is None:
        # ma5 缺失时用最近 5 根收盘现算
        try:
            with _connect(db) as con:
                closes = [float(r[0]) for r in con.execute(
                    "select close from stock_daily where code = ? order by date desc limit 5",
                    (code,),
                ).fetchall()]
            if len(closes) == 5:
                m = sum(closes) / 5.0
                bias5 = (close - m) / m * 100.0 if m > 0 else None
        except sqlite3.Error:
            pass
    if bias5 is None:
        return {"ok": False, "reason": "MA5 数据不足"}
    return {"ok": True, "date": date, "close": close, "bias5": bias5, "bias10": bias10, "bias20": bias20}


def discipline_label(
    bias5: float,
    signal_count: int,
    best: float = BIAS_BEST,
    warn: float = BIAS_DEFAULT,
    strong: float = BIAS_STRONG,
) -> tuple[str, str]:
    """按 MA5 乖离给出纪律判定与动作建议（阈值可调参覆盖）。"""
    relaxed = signal_count >= 2
    threshold = strong if relaxed else warn
    tail = "（双信号交叉已放宽阈值）" if relaxed and threshold > warn else ""
    if bias5 < best:
        return "✅ 可介入区间", f"乖离 {bias5:.1f}% < {best:.0f}%，属最佳买点区间{tail}"
    if bias5 <= threshold:
        return "⚠️ 小仓/回踩再进", f"乖离 {bias5:.1f}%，建议回踩 MA5 确认或小仓介入{tail}"
    return "⛔ 严禁追高（降级）", f"乖离 {bias5:.1f}% 超过阈值 {threshold:.0f}%，等回踩不追{tail}"


SIGNAL_LABELS = {"hundred_day_high": "百日新高", "trend_leader_unified": "趋势龙头", "earnings_surprise": "业绩超预期"}


def _parse_signals(text: str) -> list[str]:
    return [s.strip() for s in (text or "").replace(",", ";").split(";") if s.strip()]


def split_defensive_rows(
    rows: list[dict], regime: str, demote_signals: list[str]
) -> tuple[list[dict], list[dict]]:
    """防守市下把“单一被降权信号”的候选拆到观察区（默认：仅 hundred_day_high）。

    命中多个信号（双确认）的候选不受影响，仍留在主清单。
    """
    if regime != "defensive" or not demote_signals:
        return list(rows), []
    demote = {s.strip() for s in demote_signals if s.strip()}
    main_rows: list[dict] = []
    watch_rows: list[dict] = []
    for r in rows:
        sigs = set(_parse_signals(r.get("signals", "")))
        if sigs and sigs <= demote:
            watch_rows.append(r)
        else:
            main_rows.append(r)
    return main_rows, watch_rows


def ask_agent(url: str, code: str, name: str, skill: str, timeout: int = 200) -> dict:
    """调用本机 Agent /api/v1/agent/chat 做二次审核。"""
    message = (
        f"请用当前策略技能审核候选股 {code} {name}（180字内，输出三点："
        "①关注价值 ②主要风险 ③建议动作；若数据获取失败请直接说明）。"
    )
    payload = json.dumps({"message": message, "skills": [skill]}).encode("utf-8")
    req = urllib.request.Request(
        f"{url.rstrip('/')}/api/v1/agent/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("success") and data.get("content"):
            return {"ok": True, "content": data["content"].strip()}
        return {"ok": False, "reason": data.get("error") or "返回为空"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def update_profile_regime(profile_path: Path, regime: str) -> str | None:
    """把 regime 写回策略 profile（仅当变化时）。返回旧值（未变化返回 None）。"""
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return f"读取失败: {exc}"
    old = payload.get("market_regime")
    if old == regime:
        return None
    payload["market_regime"] = regime
    profile_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return str(old)


def _summary_line(item: dict, limit: int = 60) -> str:
    """从审核全文提取一句话摘要。"""
    text = (item.get("content") or "").replace("\n", " ").strip()
    text = text.lstrip("#* -")
    return text[:limit] + ("…" if len(text) > limit else "")


def _append_ai_inline(lines: list[str], items: list[dict]) -> None:
    if not items:
        return
    lines.append(f"### 重点解读（前 {len(items)} 只）")
    lines.append("")
    for item in items:
        lines.append(f"#### {item['name']}（{item['code']}）")
        lines.append("")
        content = (item.get("content") or "").strip()
        lines.append(content or f"⚠️ 审核未完成：{item.get('reason', '未知原因')}")
        lines.append("")


def build_reviews_markdown(snapshot: str, results: list[dict], meta: dict) -> str:
    """全量 AI 审核文本（单独成档）。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"# AI 二次审核全量文本 · 信号日 {snapshot}",
        "",
        f"> 生成 {now} · 共 {len(results)} 只 · 技能 `{meta.get('skill', '')}` · 来源：{meta.get('sources', '')}",
        "",
    ]
    for item in results:
        lines.append(f"## {item['name']}（{item['code']}）")
        lines.append("")
        if item.get("ok"):
            lines.append(item["content"])
        else:
            lines.append(f"- ⚠️ 审核失败：{item.get('reason')}")
        lines.append("")
    return "\n".join(lines)


def _recent_stats() -> dict:
    """读取近月净口径统计；失败返回空 dict，由展示层写“未计算”。"""
    try:
        return load_recent_stats(RECENT_SIGNAL_CSV)
    except Exception:  # noqa: BLE001 - 统计失败不能阻断作战计划
        return {}


def _recent_window_text() -> str:
    window = window_of(_recent_stats())
    return f"{window} " if window else "近月"


def _signal_stats_text(signals: list[str]) -> str:
    """按信号列出 T+1/T+3/T+5 净口径；无样本时明确写未计算。"""
    stats = _recent_stats()
    if not stats:
        return "未计算（缺少 daily_review_latest.csv 可评估样本）"
    parts = []
    for sig in signals:
        bucket = stats.get(sig) or {}
        label = SIGNAL_LABELS.get(sig, sig)
        parts.append(
            f"{label} R1 {fmt_stat(bucket.get('ret1') or [])} / "
            f"R3 {fmt_stat(bucket.get('ret3') or [])} / "
            f"R5 {fmt_stat(bucket.get('ret5') or [])}"
        )
    return "；".join(parts) if parts else "无对应样本"


def _holding_stats_text() -> str:
    stats = _recent_stats()
    window = window_of(stats)
    if not window:
        return "近月净口径：未计算（缺少可评估样本），不引用历史固定数字"
    return (
        f"近月净口径（{window}，剔除兜底）："
        f"T+1 {fmt_stat(merged_values(stats, 'ret1'))} · "
        f"T+3 {fmt_stat(merged_values(stats, 'ret3'))} · "
        f"T+5 {fmt_stat(merged_values(stats, 'ret5'))}"
    )


def build_markdown(
    snapshot: str,
    regime_info: dict,
    regime_source: str,
    main_rows: list[dict],
    watch_rows: list[dict],
    ai_results: list[dict],
    ai_skipped: str | None,
    ai_meta: dict | None = None,
    ai_inline: int = 3,
) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    regime = regime_info.get("regime", "unknown") if regime_info.get("ok") else "unknown"
    regime_cn = REGIME_NAMES.get(regime, "未知")
    lines: list[str] = []
    lines.append(f"# 每日作战计划 · 信号日 {snapshot}")
    lines.append("")
    lines.append(f"> 生成 {now} · 市场状态 **{regime_cn}**（{regime}）· 由 daily_action_plan.py 自动合成")
    lines.append("")
    lines.append("## 一、市场开关")
    if regime_info.get("ok"):
        lines.append(
            f"- 指数 {regime_info.get('code', '000300')} {regime_info['date']} 收盘 {regime_info['close']:.1f}"
            f" | MA20 {regime_info['ma20']:.1f} | MA60 {regime_info['ma60']:.1f}"
        )
        lines.append(f"- 判定来源：{regime_source}")
    else:
        lines.append(f"- ⚠️ 指数数据不可用（{regime_info.get('reason', '未知原因')}），沿用配置值")
    lines.append(f"- 操作建议：{REGIME_ADVICE.get(regime, '数据不足，保守对待。')}")
    if regime == "defensive":
        lines.append("- 防守市补充：新开仓只考虑「双确认 + 乖离 < 2%」；其余一律进观察清单。")
    lines.append("")
    degraded = sum(1 for r in main_rows if r.get("discipline", "").startswith("⛔"))
    head = f"## 二、候选清单（前 {len(main_rows)} 只"
    if degraded:
        head += f" · 降级 {degraded}"
    if watch_rows:
        head += f"；另有 {len(watch_rows)} 只进防守观察区"
    head += "）"
    lines.append(head)
    lines.append("")
    lines.append("| 代码 | 名称 | 信号 | 分层 | 现价 | MA5乖离 | 纪律判定 |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | --- |")

    def _fmt_bias(r: dict) -> tuple[str, str]:
        bias = r.get("bias")
        if bias and bias.get("ok"):
            return f"{bias['bias5']:+.1f}%", f"{bias['close']:.2f}"
        return "—", "—"

    for r in main_rows:
        bias_txt, price_txt = _fmt_bias(r)
        lines.append(
            f"| {r['code']} | {r['name']} | {r['signals'] or '—'} | {r['group'] or r['tier'] or '—'} "
            f"| {price_txt} | {bias_txt} | {r['discipline']} |"
        )
    if degraded:
        lines.append("")
        lines.append(f"- ⛔ 已降级（严禁追高）：{degraded} 只，等回踩 MA5/MA10 再评估。")
    if watch_rows:
        demote_label = "/".join(SIGNAL_LABELS.get(s, s) for s in sorted({sig for r in watch_rows for sig in _parse_signals(r.get('signals', ''))}))
        lines.append("")
        lines.append(f"### 观察区：{demote_label or '降权信号'}单信号（防守市降权，不参与新开仓）")
        lines.append("")
        lines.append("| 代码 | 名称 | 信号 | 现价 | MA5乖离 | 纪律/说明 |")
        lines.append("| --- | --- | --- | ---: | ---: | --- |")
        for r in watch_rows:
            bias_txt, price_txt = _fmt_bias(r)
            lines.append(
                f"| {r['code']} | {r['name']} | {r['signals'] or '—'} | {price_txt} | {bias_txt} | "
                f"{r.get('discipline', '') or '降权观察'} |"
            )
        lines.append("")
        lines.append(f"- 依据（{_recent_window_text()}净口径，剔除兜底）：{_signal_stats_text(sorted({sig for r in watch_rows for sig in _parse_signals(r.get('signals', ''))}))}；与其它信号交叉（双确认）仍保留在主清单。")
    lines.append("")
    lines.append("## 三、持有期纪律（近月净口径，已扣成本）")
    lines.append("")
    lines.append(f"- {_holding_stats_text()}")
    lines.append(f"- {HOLDING_NOTE}")
    lines.append("- 追高（>5% 乖离）的样本需单独核回撤；本行不复用旧结论，逐次以当日数据重算。")
    lines.append("")
    lines.append("## 四、AI 二次审核（Agent 策略审核）")
    lines.append("")
    if ai_skipped:
        lines.append(f"- AI 审核未运行：{ai_skipped}")
    else:
        meta = ai_meta or {}
        ok_count = sum(1 for a in ai_results if a.get("ok"))
        bad_count = len(ai_results) - ok_count
        lines.append(
            f"- 审核池 **{meta.get('pool_size', len(ai_results))}** 只（去重前 {meta.get('raw_count', '—')} 条来源条目）· "
            f"成功 {ok_count} / 失败 {bad_count} · 技能 `{meta.get('skill', '—')}` · 并发 {meta.get('workers', '—')}"
        )
        if meta.get("sources"):
            lines.append(f"- 来源：{meta['sources']}")
        if meta.get("reviews_file"):
            lines.append(f"- 完整审核文本（全量）：`{meta['reviews_file']}`")
        lines.append("")
        inline_items = [a for a in ai_results if a.get("ok")][: max(0, ai_inline)]
        _append_ai_inline(lines, inline_items)
        lines.append("### 全部审核结论（摘要）")
        lines.append("")
        lines.append("| 代码 | 名称 | 来源 | 结论摘要 |")
        lines.append("| --- | --- | --- | --- |")
        for item in ai_results:
            summary = _summary_line(item) if item.get("ok") else f"⚠️ {item.get('reason', '失败')}"
            src = "/".join(item.get("sources") or [])
            lines.append(f"| {item['code']} | {item['name']} | {src} | {summary} |")
        lines.append("")
    lines.append("## 五、执行检查清单")
    lines.append("")
    lines.append("- [ ] 市场为防守市时：半仓以下，新开仓仅限「双确认 + 乖离 < 2%」")
    lines.append("- [ ] 每笔入场前确认 MA5 乖离（>5% 一律不追；2-5% 仅小仓）")
    lines.append("- [ ] 入场同时写下止损位；预设 T+5 / T+10 复盘节点")
    lines.append("- [ ] 不在 T+1 / T+3 兑现（依据见第三节近月净口径，不引用固定历史数字）")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", type=Path, default=TUNING_FILE, help="调参文件（默认 config/daily_plan_tuning.json）")
    ap.add_argument("--date", default="latest", help="信号日（YYYY-MM-DD 或 latest）")
    ap.add_argument("--top", type=int, default=None, help="候选表数量（默认读调参 candidates_top=12）")
    ap.add_argument("--review-root", type=Path, default=DEFAULT_REVIEW_ROOT)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--update-profile", action="store_true", help="把 regime 写回策略 profile")
    ap.add_argument("--ai-review", action="store_true", help="调用本机 Agent 做 AI 二次审核")
    ap.add_argument("--ai-top", type=int, default=None, help="AI 审核数量（默认读调参 ai_review.top=80）")
    ap.add_argument("--ai-skill", default=None, help="AI 审核技能（默认读调参 ai_review.skill）")
    ap.add_argument("--ai-workers", type=int, default=None, help="AI 审核并发数（默认读调参）")
    ap.add_argument("--ai-inline", type=int, default=None, help="计划内联全文的审核数（默认读调参）")
    ap.add_argument("--ai-pool-days", type=int, default=None, help="近期信号回看交易日数（默认读调参）")
    ap.add_argument("--ai-url", default=None, help="本机服务地址（默认读调参）")
    ap.add_argument("--force-regime", default="", help="跳过计算强制指定 regime（调试用）")
    args = ap.parse_args()

    tuning = load_tuning(args.config)
    ai_cfg = tuning.get("ai_review") if isinstance(tuning.get("ai_review"), dict) else {}
    top_n = args.top if args.top is not None else int(tuning.get("candidates_top", 12))
    ai_top = args.ai_top if args.ai_top is not None else int(ai_cfg.get("top", 80))
    ai_skill = args.ai_skill or str(ai_cfg.get("skill", "bull_trend"))
    ai_workers = args.ai_workers if args.ai_workers is not None else int(ai_cfg.get("workers", 4))
    ai_inline = args.ai_inline if args.ai_inline is not None else int(ai_cfg.get("inline", 3))
    pool_days = args.ai_pool_days if args.ai_pool_days is not None else int(ai_cfg.get("recent_signal_days", 10))
    ai_url = args.ai_url or str(ai_cfg.get("url", "http://127.0.0.1:8000"))
    bias_best = float(tuning.get("bias_best", BIAS_BEST))
    bias_warn = float(tuning.get("bias_warn", BIAS_DEFAULT))
    bias_strong = float(tuning.get("bias_strong", BIAS_STRONG))

    review_dir, snapshot = resolve_review_dir(Path(args.review_root), args.date)
    rows = load_candidates(review_dir, top_n)
    if not rows:
        print("[action-plan] 候选为空")
        return 0

    index_code = str(tuning.get("index_code", INDEX_CODE))
    if args.force_regime:
        regime_info = {"ok": True, "regime": args.force_regime, "code": index_code, "date": "-", "close": 0.0, "ma20": 0.0, "ma60": 0.0}
        regime_source = "强制指定"
    else:
        regime_info = read_index_regime(Path(args.db), index_code)
        regime_source = f"stock_daily {index_code} 收盘 vs MA20/MA60"

    for r in rows:
        bias = read_bias(Path(args.db), r["code"])
        r["bias"] = bias
        if bias.get("ok"):
            sig_count = len(_parse_signals(r["signals"]))
            label, action = discipline_label(
                bias["bias5"], sig_count, best=bias_best, warn=bias_warn, strong=bias_strong
            )
            r["discipline"] = f"{label} · {action}"
        else:
            r["discipline"] = f"数据不足（{bias.get('reason', '')}），人工确认"

    ai_results: list[dict] = []
    ai_skipped: str | None = None
    ai_meta: dict | None = None
    if args.ai_review and not bool(ai_cfg.get("enabled", True)):
        ai_skipped = "调参 ai_review.enabled=false"
    elif args.ai_review:
        overview_pool = load_overview_pool(review_dir)
        recent_pool = load_recent_signal_pool(RECENT_SIGNAL_CSV, pool_days)
        pool = dedupe_pool(overview_pool, recent_pool)[: max(0, ai_top)]
        if not pool:
            ai_skipped = "无可用候选"
        else:
            raw_entries = len(overview_pool) + len(recent_pool)
            print(f"[action-plan] AI 审核池 {len(pool)} 只（去重前 {raw_entries} 条，并发 {ai_workers}）...")
            results_by_code: dict[str, dict] = {}
            done = 0
            with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, ai_workers)) as ex:
                futures = {ex.submit(ask_agent, ai_url, it["code"], it["name"], ai_skill): it for it in pool}
                for fut in concurrent.futures.as_completed(futures):
                    it = futures[fut]
                    try:
                        res = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        res = {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
                    results_by_code[it["code"]] = {
                        "code": it["code"],
                        "name": it["name"],
                        "sources": list(it.get("sources") or []),
                        "signals": list(it.get("signals") or []),
                        **res,
                    }
                    done += 1
                    if done % 10 == 0 or done == len(pool):
                        print(f"[action-plan] AI 审核进度 {done}/{len(pool)}")
            ai_results = [results_by_code[it["code"]] for it in pool]
            ok_count = sum(1 for a in ai_results if a.get("ok"))
            ai_meta = {
                "pool_size": len(pool),
                "raw_count": raw_entries,
                "skill": ai_skill,
                "workers": ai_workers,
                "sources": (
                    f"快复盘 {len(overview_pool)} 只 + 近 {pool_days} 交易日信号 {len(recent_pool)} 只"
                    f" → 按股票去重 {len(pool)} 只（成功 {ok_count}）"
                ),
            }
    else:
        ai_skipped = "未启用（加 --ai-review 运行）"

    main_rows, watch_rows = split_defensive_rows(
        rows,
        regime_info.get("regime") if regime_info.get("ok") else "",
        list(tuning.get("defensive_demote_single") or []),
    )
    print(f"[action-plan] 候选拆分：主清单 {len(main_rows)} 只 / 观察区 {len(watch_rows)} 只")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = snapshot.replace("-", "") if snapshot else datetime.now().strftime("%Y%m%d")
    if ai_results:
        reviews_md = build_reviews_markdown(snapshot, ai_results, ai_meta or {})
        rev_file = out_dir / f"daily_ai_reviews_{stamp}.md"
        rev_file.write_text(reviews_md, encoding="utf-8")
        (out_dir / "daily_ai_reviews_latest.md").write_text(reviews_md, encoding="utf-8")
        ai_meta = dict(ai_meta or {})
        ai_meta["reviews_file"] = "data/strategy_review/daily_ai_reviews_latest.md"

    md = build_markdown(
        snapshot, regime_info, regime_source, main_rows, watch_rows, ai_results, ai_skipped,
        ai_meta=ai_meta, ai_inline=ai_inline,
    )
    out_file = out_dir / f"daily_action_plan_{stamp}.md"
    out_file.write_text(md, encoding="utf-8")
    (out_dir / "daily_action_plan_latest.md").write_text(md, encoding="utf-8")

    if args.update_profile and regime_info.get("ok"):
        old = update_profile_regime(Path(args.profile), regime_info["regime"])
        if old:
            print(f"[action-plan] profile market_regime: {old} -> {regime_info['regime']}")

    regime = regime_info.get("regime", "unknown") if regime_info.get("ok") else "unknown"
    ai_ok = sum(1 for a in ai_results if a.get("ok"))
    degraded_main = sum(1 for r in main_rows if r.get("discipline", "").startswith("⛔"))
    print(
        f"[action-plan] snapshot={snapshot} regime={regime} main={len(main_rows)} watch={len(watch_rows)} "
        f"degraded={degraded_main} ai_reviewed={len(ai_results)} (ok={ai_ok}) -> {out_file}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
