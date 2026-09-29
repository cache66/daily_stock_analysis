#!/usr/bin/env python3
"""从股息池体检 CSV 生成「股息三级清单」markdown（纯本地读取，不联网）。

三层口径（与 `analyze_dividend_top100.py` 体检报告一致）：
- ① 趋势红利：业绩平稳/稳增 + T 战术通过 + 三层月线闸门通过
- ② 吃息+T：  业绩平稳/稳增 + T 战术通过（月线未过闸门，走平观察）
- ③ 纯吃息：  业绩平稳/稳增 + 股息率 >= 4.5%（不看月线，长持收息）

用法：
    ./.venv-linux/bin/python scripts/make_dividend_three_tiers.py \
        --checkup data/strategy_review/dividend_top100_checkup_2026-09-24_top100.csv
输出：
    默认 data/strategy_review/dividend_three_tiers_<运行日>.md
"""
from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = PROJECT_ROOT / "data" / "strategy_review"

DIV_MIN = 4.5
BIZ_OK = {"稳增", "平稳"}

REQUIRED_COLUMNS = {
    "code", "name", "industry", "score", "dv_ttm", "biz_class", "net_yoy",
    "net_cagr3", "roe", "s1_2_annual", "s1_3_annual", "amt20_yi",
    "dist_ma200", "ma200_slope60", "ret6m", "t_good", "trend_ok",
}


def _num(v, nd: int = 2) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):.{nd}f}"


def _str(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return str(v)


def load_checkup(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"code": str})
    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"体检 CSV 缺字段: {sorted(missing)}")
    return df


def build_tiers(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    biz_ok = df["biz_class"].isin(BIZ_OK)
    t_good = df["t_good"].fillna(False).astype(bool)
    trend = df["trend_ok"].fillna(False).astype(bool)
    return {
        "trend": df[biz_ok & t_good & trend].sort_values("score", ascending=False),
        "t_watch": df[biz_ok & t_good & ~trend].sort_values("score", ascending=False),
        "pure": df[biz_ok & (df["dv_ttm"] >= DIV_MIN)].sort_values("score", ascending=False),
    }


def _md_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines.extend("| " + " | ".join(r) + " |" for r in rows)
    return "\n".join(lines)


def _core_table_headers() -> list[str]:
    return ["代码", "名称", "行业", "评分", "股息率%", "净利同比%",
            "S1年化(2%)", "S1年化(3%)", "成交(亿)", "距MA200%", "MA200斜率", "近6月%"]


def _core_rows(sub: pd.DataFrame) -> list[list[str]]:
    return [
        [r.code, _str(r.name), _str(r.industry), _num(r.score, 1), _num(r.dv_ttm),
         _num(r.net_yoy), _num(r.s1_2_annual, 1), _num(r.s1_3_annual, 1),
         _num(r.amt20_yi), _num(r.dist_ma200), _num(r.ma200_slope60), _num(r.ret6m)]
        for r in sub.itertuples()
    ]


def build_markdown(df: pd.DataFrame, checkup_name: str, run_day: str) -> str:
    tiers = build_tiers(df)
    t1, t2, t3 = tiers["trend"], tiers["t_watch"], tiers["pure"]

    pure_headers = ["代码", "名称", "行业", "评分", "股息率%", "业绩类", "净利同比%",
                    "3年CAGR%", "ROE%", "成交(亿)", "距MA200%"]
    pure_rows = [
        [r.code, _str(r.name), _str(r.industry), _num(r.score, 1), _num(r.dv_ttm),
         _str(r.biz_class), _num(r.net_yoy), _num(r.net_cagr3), _num(r.roe),
         _num(r.amt20_yi), _num(r.dist_ma200)]
        for r in t3.itertuples()
    ]

    parts = [
        "# 股息三级清单",
        "",
        f"- 生成日期：{run_day}；数据源：`{checkup_name}`（Top100 股息池体检，数据截至该报告口径）",
        "- 口径：",
        "  - 业绩分级：稳增 / 平稳 = 入选；放缓 / 下滑 / 周期·大波动 = 不入选",
        "  - T 能打：S1 战术（回调买入 → 反弹止盈）样本内通过",
        "  - 月线闸门：距 MA200 ≥ -5% 且 MA200 60 日斜率 > 0 且近 6 月涨幅 ≥ -5%",
        "  - 股息率：滚动 12 个月股息率（dv_ttm）",
        "- 三层为并列视图，成员有重叠：① ⊂ ②；③ 按股息率独立选取",
        "- 样本内历史测试，不代表未来收益；仅作纪律与监控工具，不构成投资建议",
        "",
        f"## ① 趋势红利（{len(t1)} 只）：可做 T",
        "",
        _md_table(_core_table_headers(), _core_rows(t1)),
        "",
        f"## ② 吃息 + 做 T（月线走平的 {len(t2)} 只；连同 ① 共 {len(t1) + len(t2)} 只）",
        "",
        "> 买卖参照 ①，但月线未过闸门，优先等「斜率转正」再动。",
        "",
        _md_table(_core_table_headers(), _core_rows(t2)),
        "",
        f"## ③ 纯吃息池（{len(t3)} 只）：不看月线，长持收息",
        "",
        "> 适合底仓；回踩加仓比追高更稳。业绩变脸（biz_class 掉档）要剔除。",
        "",
        _md_table(pure_headers, pure_rows),
        "",
        "## 用法",
        "",
        "1. 长持收息仓 → ③ 中挑（业绩稳/平 + 股息率 ≥ 4.5%）",
        "2. T 仓 → 只在 ① 里做，回踩不追高",
        "3. 等回踩 → ②/③ 中月线未过闸门的，等体检重跑报「斜率转正」",
        "4. 底仓兜底 → 红利低波 / 银行类 ETF，避免「选不出就不持仓」",
        "",
        "## 刷新方式",
        "",
        "```bash",
        "# 1) 重跑体检（联网抓行情/财务；避免与其它抓取任务并发）",
        "./.venv-linux/bin/python scripts/analyze_dividend_top100.py",
        "# 2) 本地重生成清单（不联网）",
        "./.venv-linux/bin/python scripts/make_dividend_three_tiers.py",
        "```",
        "",
    ]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkup", type=Path, default=None,
                        help="体检 CSV 路径；缺省取 data/strategy_review 下最新一份")
    parser.add_argument("--out", type=Path, default=None,
                        help="输出 markdown 路径；缺省 data/strategy_review/dividend_three_tiers_<运行日>.md")
    args = parser.parse_args()

    checkup = args.checkup
    if checkup is None:
        candidates = sorted(DEFAULT_OUT_DIR.glob("dividend_top100_checkup_*_top100.csv"))
        if not candidates:
            raise SystemExit("未找到体检 CSV，请先运行 scripts/analyze_dividend_top100.py")
        checkup = candidates[-1]
    df = load_checkup(checkup)

    run_day = date.today().isoformat()
    out = args.out or (DEFAULT_OUT_DIR / f"dividend_three_tiers_{run_day.replace('-', '')}.md")
    out.write_text(build_markdown(df, checkup.name, run_day), encoding="utf-8")

    tiers = build_tiers(df)
    print(f"已生成: {out}")
    print(f"① 趋势红利 {len(tiers['trend'])} 只 | ② 吃息+T 走平 {len(tiers['t_watch'])} 只"
          f"（+① 共 {len(tiers['trend']) + len(tiers['t_watch'])}） | ③ 纯吃息 {len(tiers['pure'])} 只")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
