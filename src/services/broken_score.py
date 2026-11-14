# -*- coding: utf-8 -*-
"""炸板事件打分：用「炸板日资金 + 成交量」给出 0~100 的可解释分数。

## 分数怎么来的

用 `scripts/research_broken_events.py` 在 2026-11-10 对最近 25 个交易日、709 个炸板事件做了
单变量分桶 + 逻辑回归（numpy 手写，无 sklearn），结果保存在
`data/strategy_review/broken_events/research_20261110.json`：

- 单变量分桶（T+1 胜率 / 3 日内收复率）：
  - 成交额 / 前 20 日均额：<1.5x → 61.5% / 79.5%（n=39）；1.5~3x → 43.4% / 63.9%；
    3~5x → 35.5% / 53.2%；>5x → 24.5% / 37.3%（n=102）
  - 炸板次数：1 次 → 44.3% / 58.2%；2~3 次 → 27.1% / 46.5%；≥4 次 → 39.1% / 62.5%
  - 主力净占比：5%~10% 最好（38.2% / 55.9%），<-5% 最差（32.8% / 41.4%），整体区分度弱
  - 小单净额 > +2000 万（散户接盘）→ T+1 -1.9% / 45.7%；散户净卖 → -1.1% / 51.2%
- 逻辑回归（标准化，n=387，AUC=0.628，基准胜率 37.7%）：
  log 量比 -0.383、当日涨跌幅 -0.208、换手率 +0.181、炸板次数 -0.071、
  散户净占比 -0.067、主力净占比 +0.058

结论：**能不能修复主要由"量"决定**（巨量炸板最差），其次是炸板次数与散户是否接盘；
单看大单净额（主力净占比）几乎不构成优势——这与"成交额大就有资金自救"的直觉相反。

## 使用边界

- AUC 0.63 属于弱区分，分数只用于排序与复盘，不能当买入信号；
- 样本只有一个月的单一市场环境，换阶段需用同一脚本重新校准；
- 特征缺失（如新股没有 20 日均额）按模型均值填补，并在 `missing` 里标出。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

MODEL_DATE = "2026-11-10"
MODEL_SAMPLE = 387
MODEL_AUC = 0.634
MODEL_BASE_WIN = 0.377

FEATURES = ["log量比", "主力净占比", "散户净占比", "炸板次数", "换手率", "当日涨跌幅", "log炸板占比"]
# 说明：log炸板占比 的口径在 2026-11-11 由「开板至收盘」改为「炸板瞬间 5 分钟」，
# 原系数（+0.1876）是在旧口径上拟合的，不能直接复用，因此暂时置 0（只展示、不计分），
# 待用新口径重新跑 research_broken_events.py 后再回填。
COEF = [-0.4042, 0.0678, -0.0770, -0.0623, 0.2018, -0.1251, 0.0]
INTERCEPT = -0.5313
MEAN = [1.2613, 8.6653, -8.6653, 2.2946, 12.9171, 6.0563, -0.7847]
STD = [0.6512, 9.1637, 9.1635, 2.3040, 8.2829, 4.4375, 0.5474]

GRADE_HIGH = 0.42
GRADE_MID = 0.36


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number


def _reasons(
    *,
    amount_ratio: Optional[float],
    main_ratio: Optional[float],
    retail_ratio: Optional[float],
    break_count: Optional[float],
    turnover: Optional[float],
    change_pct: Optional[float],
    break_ratio: Optional[float],
    main_source: str = "eastmoney",
) -> List[str]:
    reasons: List[str] = []
    if amount_ratio is not None:
        if amount_ratio < 1.5:
            reasons.append(f"缩量炸板：成交额仅 20 日均额的 {amount_ratio:.1f} 倍（该档历史 T+1 胜率 61.5%）")
        elif amount_ratio < 3:
            reasons.append(f"温和放量 {amount_ratio:.1f} 倍（该档历史 T+1 胜率 43.4%）")
        elif amount_ratio < 5:
            reasons.append(f"明显放量 {amount_ratio:.1f} 倍（该档历史 T+1 胜率 35.5%）")
        else:
            reasons.append(f"巨量炸板 {amount_ratio:.1f} 倍（该档历史 T+1 胜率仅 24.5%）")
    if break_count is not None:
        if break_count <= 1:
            reasons.append("只炸板 1 次，封板质量相对好（历史 T+1 胜率 44.3%）")
        elif break_count <= 3:
            reasons.append(f"炸板 {int(break_count)} 次，反复开板最弱（历史 T+1 胜率 27.1%）")
        else:
            reasons.append(f"炸板 {int(break_count)} 次")
    if break_ratio is not None:
        reasons.append(f"炸板瞬间 5 分钟成交占全天 {break_ratio * 100:.1f}%（仅展示，暂不计分）")
    if main_ratio is not None:
        if main_source == "ths":
            reasons.append(
                f"同花顺主动买卖净额占成交额 {main_ratio * 100:+.1f}%（口径≠东财主力，不可直接比较）"
            )
        else:
            reasons.append(f"炸板日主力净额占成交额 {main_ratio * 100:+.1f}%")
    if retail_ratio is not None:
        if retail_ratio < 0:
            reasons.append(f"散户（中单+小单）净卖出 {abs(retail_ratio) * 100:.1f}% 成交额，筹码向大单集中")
        else:
            reasons.append(f"散户净买入 {retail_ratio * 100:.1f}% 成交额，大单可能在派发")
    if change_pct is not None and change_pct >= 9:
        reasons.append(f"炸板日涨幅仍达 {change_pct:.1f}%，位置偏高")
    if turnover is not None and turnover >= 20:
        reasons.append(f"换手 {turnover:.1f}%，交投过热")
    return reasons


def compute_break_score(
    *,
    amount_ratio: Optional[float],
    main_ratio: Optional[float],
    retail_net: Optional[float],
    amount: Optional[float],
    break_count: Optional[float],
    turnover: Optional[float],
    change_pct: Optional[float],
    break_ratio: Optional[float] = None,
    main_source: str = "eastmoney",
    super_net: Optional[float] = None,
    big_net: Optional[float] = None,
) -> Dict[str, Any]:
    """返回 {score, prob, grade, reasons, missing, model}；缺失特征按均值填补。"""
    retail_ratio = None
    if retail_net is not None and amount:
        retail_ratio = retail_net / amount

    raw: Dict[str, Optional[float]] = {
        "log量比": math.log(max(0.2, min(20.0, amount_ratio))) if amount_ratio else None,
        "主力净占比": (main_ratio * 100) if main_ratio is not None else None,
        "散户净占比": (retail_ratio * 100) if retail_ratio is not None else None,
        "炸板次数": _num(break_count),
        "换手率": _num(turnover),
        "当日涨跌幅": _num(change_pct),
        "log炸板占比": (
            math.log(max(0.05, min(1.0, break_ratio))) if break_ratio is not None else None
        ),
    }

    missing = [name for name, value in raw.items() if value is None]
    logit = INTERCEPT
    for index, name in enumerate(FEATURES):
        value = raw[name]
        if value is None:
            value = MEAN[index]
        logit += COEF[index] * ((value - MEAN[index]) / STD[index])

    prob = 1 / (1 + math.exp(-logit))
    score = int(round(prob * 100))
    reasons = _reasons(
        amount_ratio=amount_ratio,
        main_ratio=main_ratio,
        retail_ratio=retail_ratio,
        break_count=break_count,
        turnover=turnover,
        change_pct=change_pct,
        break_ratio=break_ratio,
        main_source=main_source,
    )

    # ---- 硬规则（覆盖模型）：大单/超大单在卖但散户在接，这种炸板没有意义 ----
    caps: List[int] = []
    if super_net is not None and big_net is not None:
        big_total = super_net + big_net
        if big_total < 0 and (main_ratio is None or main_ratio < 0):
            caps.append(30)
            reasons.insert(
                0,
                f"大单+超大单净卖出 {abs(big_total) / 1e8:.2f} 亿，"
                f"当日资金净流出 {abs(main_ratio or 0) * 100:.1f}% 成交额 → 大单在卖、散户在接，降级",
            )
        elif big_total < 0:
            caps.append(40)
            reasons.insert(0, f"大单+超大单净卖出 {abs(big_total) / 1e8:.2f} 亿，资金结构偏弱")
    else:
        caps.append(45)
        reasons.append("缺分档资金（超大单/大单买不到），最高只能到「观察」")

    if turnover is not None and turnover >= 30:
        caps.append(min(score, 50))
        reasons.append(f"换手 {turnover:.0f}%，情绪过热、接盘盘承接成本高")

    if caps:
        score = min([score] + caps)

    if score >= 70:
        grade = "重点观察"
    elif score >= 45:
        grade = "观察"
    else:
        grade = "不跟"

    return {
        "score": score,
        "prob": round(prob, 4),
        "grade": grade,
        "reasons": reasons,
        "missing": missing,
        "model": {"date": MODEL_DATE, "n": MODEL_SAMPLE, "auc": MODEL_AUC, "base_win": MODEL_BASE_WIN},
    }
