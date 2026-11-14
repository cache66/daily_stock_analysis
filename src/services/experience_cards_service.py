# -*- coding: utf-8 -*-
"""经验卡登记与渲染（A1-b）。

登记表：`config/experience_cards.json`，每张卡必须有来源（文件/日志/产物），
缺字段或重复 ID 直接报错，避免写入无法核查的“经验”。

渲染产物：`data/strategy_review/experience_cards.md`（可直接阅读，也可作为复盘索引）。
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = PROJECT_ROOT / "config" / "experience_cards.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "data" / "strategy_review" / "experience_cards.md"

REQUIRED_FIELDS = ("id", "date", "type", "title", "facts", "conclusion", "boundary", "source")
TYPE_ORDER = ("机制", "资格", "数据", "口径")


class ExperienceCardError(ValueError):
    """登记表不符合合同（缺字段、重复 ID、来源为空）。"""


def load_cards(path: Path = DEFAULT_REGISTRY) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    cards: List[Dict[str, Any]] = list(data.get("cards") or [])
    if not cards:
        raise ExperienceCardError("登记表没有任何卡片")

    seen: set[str] = set()
    for index, card in enumerate(cards, start=1):
        missing = [field for field in REQUIRED_FIELDS if not str(card.get(field) or "").strip()]
        if missing:
            raise ExperienceCardError(f"第 {index} 张卡缺少字段：{', '.join(missing)}")
        card_id = str(card["id"])
        if card_id in seen:
            raise ExperienceCardError(f"重复的卡片 ID：{card_id}")
        seen.add(card_id)
    cards.sort(key=lambda card: (TYPE_ORDER.index(card["type"]) if card["type"] in TYPE_ORDER else 99, card["date"]))
    return {"version": data.get("version", ""), "note": data.get("note", ""), "cards": cards}


def render_cards(registry: Dict[str, Any]) -> str:
    cards: List[Dict[str, Any]] = list(registry.get("cards") or [])
    lines: List[str] = []
    lines.append("# 经验卡（自动生成）")
    lines.append("")
    lines.append(f"> 版本 {registry.get('version') or '-'} · 共 {len(cards)} 张 · 每张卡必须带来源，未发生的事不登记")
    if registry.get("note"):
        lines.append(f"> {registry['note']}")
    lines.append("")

    lines.append("## 索引")
    lines.append("")
    lines.append("| 类型 | ID | 日期 | 标题 |")
    lines.append("| --- | --- | --- | --- |")
    for card in cards:
        lines.append(f"| {card['type']} | `{card['id']}` | {card['date']} | {card['title']} |")
    lines.append("")

    current_type = None
    for card in cards:
        if card["type"] != current_type:
            current_type = card["type"]
            lines.append(f"## {current_type}")
            lines.append("")
        lines.append(f"### {card['title']}")
        lines.append("")
        lines.append(f"- **ID**：`{card['id']}`　**日期**：{card['date']}")
        lines.append(f"- **事实**：{card['facts']}")
        lines.append(f"- **结论**：{card['conclusion']}")
        lines.append(f"- **适用边界**：{card['boundary']}")
        lines.append(f"- **来源**：`{card['source']}`")
        lines.append("")

    lines.append("## 使用规则")
    lines.append("")
    lines.append("- 卡片只作研究参照，不生成胜率，也不单独构成买入理由。")
    lines.append("- 结果与失败同样登记；结论被推翻时新增卡片，不改原文。")
    lines.append("- 新增卡必须写 `source`，指向可核查的文件、日志或产物。")
    lines.append("")
    return "\n".join(lines)


def build_cards_file(
    registry_path: Path = DEFAULT_REGISTRY,
    output_path: Path = DEFAULT_OUTPUT,
    *, as_of: Optional[str] = None,
) -> Dict[str, Any]:
    registry = load_cards(registry_path)
    markdown = render_cards(registry)
    markdown += render_auto_observations(as_of=as_of)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(markdown, encoding="utf-8")
    return {"count": len(registry["cards"]), "output": str(output_path)}


def render_auto_observations(*, as_of: Optional[str] = None) -> str:
    """从当期体检结果自动归纳观察项（不写回 registry，也不算正式卡片）。"""
    lines: List[str] = ["", "## 自动归纳（机器生成，非正式卡片）", ""]
    lines.append("> 这些是脚本从 `signal_regime.json` 与回填结果里算出来的当期观察，")
    lines.append("> 用来提示“该不该继续用某个信号”；它们不计入上面的卡片数量，也不构成买入理由。")
    lines.append("")
    try:
        from src.services.signal_regime import DEFAULT_OUTPUT as REGIME_PATH, load_cached

        regime = load_cached(REGIME_PATH, as_of=as_of or date.today().isoformat())
    except Exception:  # noqa: BLE001 - 体检结果不可用时不阻断卡片生成
        regime = {"families": {}}
    families = {} if regime.get("error") else regime.get("families") or {}
    if not families:
        lines.append("- 暂无信号体检结果（先跑 `scripts/build_signal_regime.py`）。")
        return "\n".join(lines) + "\n"
    lines.append(f"- 体检基准日：{regime.get('as_of', '未知')}；口径：T+{int((regime.get('window') or {}).get('horizon', 5))} 超额，短窗 {(regime.get('window') or {}).get('short', 20)} 条 / 长窗 {(regime.get('window') or {}).get('long', 60)} 条。")
    order = {"block": 0, "caution": 1, "allow": 2}
    for family, item in sorted(families.items(), key=lambda kv: order.get(kv[1].get("verdict"), 9)):
        short = item.get("short") or {}
        long_item = item.get("long") or {}
        verdict = {"block": "暂停新增研究（近端负超额）", "caution": "谨慎（研究记录）", "allow": "研究观察可用"}.get(
            item.get("verdict"), str(item.get("verdict"))
        )
        lines.append(
            f"- `{family}`：**{verdict}** — 近端 {short.get('n', 0)} 条超额 "
            f"{short.get('excess_pct', '—')}%、胜率 {short.get('win_pct', '—')}%，"
            f"长窗超额 {long_item.get('excess_pct', '—')}%（{item.get('reason', '')}）"
        )
    lines.append("")
    lines.append("- 提醒：“停用”只针对自动择时口径的建池，历史结论仍留在上面的机制/资格卡里，可随时回看推翻过程。")
    lines.append("")
    return "\n".join(lines)

