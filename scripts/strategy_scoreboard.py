#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""策略成功度总览：一页看清个人策略全家桶的现状与表现。

数据来源：
- 策略档案：`src/services/personal_strategy_matrix_service.py` 的 PERSONAL_STRATEGY_CATALOG
- 全样本绩效：`data/strategy_review/leaderboard.md`
- 近月实盘绩效：`data/strategy_review/daily_review_latest.csv`（扣除 31bps 成本口径）
- 当前开关：`config/local_strategy_profile.json`

产物：`data/strategy_review/strategy_scoreboard.md`
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.signal_recent_stats import fmt_stat, load_recent_stats  # noqa: E402

DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "strategy_review" / "strategy_scoreboard.md"
LEADERBOARD = PROJECT_ROOT / "data" / "strategy_review" / "leaderboard.md"
RECENT_CSV = PROJECT_ROOT / "data" / "strategy_review" / "daily_review_latest.csv"
PROFILE = PROJECT_ROOT / "config" / "local_strategy_profile.json"

SIDE_LINES = [
    ("吃股息（防守线）", "scripts/select_dividend_income_candidates.py", "合格池年化 +9.7%（含息）；暂停迭代"),
    ("基金定投候选池", "docs/个人策略文档/基金定投候选与体检流程.md", "49 只季度候选；单只体检清单"),
    ("screening 引擎", "scripts/collect_screening_snapshots.py", "动量质量/缩量回踩/双低；前向积累中"),
    ("低位业绩观察线", "scripts/scan_earnings_lowbase_watch.py", "10,842 事件回测：B 态唯一正期望买入态"),
    ("次新观察清单", "scripts/scan_sub_new_watchlist.py", "候选扫描，不落库"),
    ("深跌企稳扫描", "scripts/screen_deep_recovery.py", "已进每日 17:05 链路"),
    ("红利 T 提示器", "scripts/dividend_t_watch.py", "已进每日 17:05 链路"),
    ("ETF 做 T 面板", "scripts/etf_t_dashboard.py", "腾讯 qfq 口径"),
]

AUTOMATION_LINES = [
    ("18:10", "screening_daily.sh", "screening Top-N 采集（动量质量/缩量回踩/双低）"),
    ("18:40", "warm_daily.sh", "缓存补数 + K线/业绩预热"),
    ("19:00", "structure_scan_daily.sh", "000300 补数 + 上涨结构扫描"),
    ("19:05", "matrix_daily.sh", "矩阵复盘 + stock_daily 同步 + 红利T + 深跌企稳 + 复盘导出 + 作战计划"),
    ("19:52 左右", "matrix_daily.sh（后处理）", "A1-a 只读总览 `today_board.md` + A2-a 每日建 track"),
]


def load_catalog() -> list[dict]:
    try:
        from src.services.personal_strategy_matrix_service import PERSONAL_STRATEGY_CATALOG

        return [dict(item) for item in PERSONAL_STRATEGY_CATALOG]
    except Exception as exc:  # noqa: BLE001
        print(f"[scoreboard] warn: 无法导入策略目录（{exc}），仅输出基础信息")
        return []


def load_profile() -> dict:
    try:
        return json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def parse_leaderboard() -> dict[str, dict]:
    """解析 leaderboard.md 表格 → {策略id: {period, samples, w1, w3, w5, w10}}"""
    result: dict[str, dict] = {}
    if not LEADERBOARD.exists():
        return result
    for line in LEADERBOARD.read_text(encoding="utf-8").splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            continue
        m = re.search(r"`([a-z0-9_]+)`", cells[0])
        if not m:
            continue
        result[m.group(1)] = {
            "name": re.sub(r"`[^`]+`", "", cells[0]).strip(),
            "period": cells[1],
            "samples": cells[2],
            "w1": cells[3],
            "w3": cells[4],
            "w5": cells[5],
            "w10": cells[6],
        }
    return result


def load_recent_stats(csv_path: Path | None = None) -> dict[str, dict]:  # noqa: F811 - 保留旧名，内部复用公共实现
    from src.services.signal_recent_stats import load_recent_stats as _load

    return _load(csv_path)


def build_markdown(catalog: list[dict], profile: dict, leaderboard: dict, recent: dict) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    regime = profile.get("market_regime", "unknown")
    defaults = profile.get("defaults", {})
    include = set(defaults.get("include_signals", []))
    exclude = set(defaults.get("exclude_signals", []))

    lines: list[str] = []
    lines.append("# 个人策略成功度总览（自动生成）")
    lines.append("")
    lines.append(f"> 更新 {now} · 市场状态 **{regime}** · 全样本口径 leaderboard.md · 近月口径 daily_review（已扣 31bps）")
    lines.append("")

    lines.append("## 一、默认每日（自动跑，重点研究）")
    lines.append("")
    lines.append("| 策略 | 状态 | 全样本 w1/w3/w5/w10（胜率/均值） | 近月 T+1/T+3/T+5（净） |")
    lines.append("| --- | --- | --- | --- |")
    fallback_notes: list[str] = []
    for item in catalog:
        sid = item.get("id", "")
        aliases = set(item.get("aliases", [])) | {sid}
        in_default = bool(aliases & include)
        if not in_default:
            continue
        lb = leaderboard.get(sid) or next((leaderboard.get(a) for a in aliases if a in leaderboard), None)
        recent_item = recent.get(sid) or next((recent.get(a) for a in aliases if a in recent), None)
        lb_txt = (
            f"{lb['w1']} / {lb['w3']} / {lb['w5']} / {lb['w10']}"
            if lb
            else "（见 leaderboard.md）"
        )
        recent_txt = (
            f"{fmt_stat(recent_item['ret1'])} · {fmt_stat(recent_item['ret3'])} · {fmt_stat(recent_item['ret5'])}"
            if recent_item
            else "—"
        )
        if recent_item and recent_item.get("fallback_n"):
            days = "、".join(sorted(recent_item.get("fallback_dates", set())))
            fallback_notes.append(f"{item.get('short_name') or sid} {recent_item['fallback_n']} 条（{days}）")
        lines.append(f"| {item.get('name', sid)} | ✅ 默认每日 | {lb_txt} | {recent_txt} |")
    lines.append("")
    if fallback_notes:
        lines.append(
            f"> 近月统计已剔除兜底样本（`selection_mode=fallback`）：{'；'.join(fallback_notes)}。"
            "兜底=严格 0 命中时的凑数输出，2026-10-05 起已关闭（`trend_fallback_top_n=0`）。"
        )
        lines.append("")

    lines.append("## 二、冻结 / 停用（有评估结论，脚本保留可手动单跑）")
    lines.append("")
    lines.append("| 策略 | 状态 | 评估结论/原因 |")
    lines.append("| --- | --- | --- |")
    frozen_reasons = {
        "daily_slow_rise": "2026-09-27 触发淘汰线（w10 −4.7%），移出默认每日",
        "long_base_release": "稳定负期望（w1 −0.73% / w5 −3.71% / w10 −18.4%），2026-09-26 停用",
        "monthly_slow_rise": "月线低频扩展，2026-09-25 冻结",
        "continuous_up_ratio": "试验期负期望（w1 −2.57%），不单独当主策略",
        "continuous_up_streak": "试验期负期望（w1 −2.84%），不单独当主策略",
    }
    tool_items: list[dict] = []
    for item in catalog:
        sid = item.get("id", "")
        aliases = set(item.get("aliases", [])) | {sid}
        frozen = item.get("line") == "frozen" or bool(aliases & exclude)
        if not frozen:
            continue
        if sid not in frozen_reasons:
            tool_items.append(item)
            continue
        note = frozen_reasons[sid]
        lb = leaderboard.get(sid)
        if lb:
            note += f"（全样本见 leaderboard：w5 {lb['w5']}）"
        lines.append(f"| {item.get('name', sid)} | ❄️ {item.get('mode', '冻结')} | {note} |")
    lines.append("")

    if tool_items:
        lines.append("## 二B、专题工具（按需运行，不进默认每日）")
        lines.append("")
        lines.append("| 工具 | 模式 | 说明 |")
        lines.append("| --- | --- | --- |")
        for item in tool_items:
            role = (item.get("role") or "").strip()
            role = role if len(role) <= 60 else role[:60] + "…"
            lines.append(f"| {item.get('name', item.get('id', ''))} | {item.get('mode', '—')} | {role or '—'} |")
        lines.append("")

    lines.append("## 三、独立支线（手动工具，按需运行）")
    lines.append("")
    lines.append("| 支线 | 入口 | 状态/结论 |")
    lines.append("| --- | --- | --- |")
    for name, entry, note in SIDE_LINES:
        exists = (PROJECT_ROOT / entry).exists()
        flag = "✅" if exists else "⚠️"
        lines.append(f"| {flag} {name} | `{entry}` | {note} |")
    lines.append("")

    lines.append("## 四、自动链路时间线（工作日）")
    lines.append("")
    lines.append("| 时间 | 脚本 | 内容 |")
    lines.append("| --- | --- | --- |")
    for t, script, desc in AUTOMATION_LINES:
        lines.append(f"| {t} | `{script}` | {desc} |")
    lines.append("")

    lines.append("## 五、怎么用（30 秒版）")
    lines.append("")
    lines.append("- 每个交易日 19:55 后先看：`data/strategy_review/today_board.md`（先看缺口/降级/到期复核）")
    lines.append("- 再看：`data/strategy_review/daily_action_plan_latest.md`（作战计划）")
    lines.append("- 想整体回顾看：本文件（成功度总览）")
    lines.append("- 想查个股历史/回测/问股：Web 工作台 `http://127.0.0.1:8000`（回测页 / 问股页 / 历史报告）")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = ap.parse_args()

    catalog = load_catalog()
    profile = load_profile()
    leaderboard = parse_leaderboard()
    recent = load_recent_stats()

    md = build_markdown(catalog, profile, leaderboard, recent)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(md, encoding="utf-8")
    print(
        f"[scoreboard] 策略 {len(catalog)} 条 · leaderboard {len(leaderboard)} 条 · "
        f"近月信号 {len(recent)} 类 -> {args.output}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
