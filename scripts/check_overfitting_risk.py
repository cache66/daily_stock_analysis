#!/usr/bin/env python3
"""防过拟合旁证（Deflated Sharpe / PBO-CSCV）——路线图 Phase 2 候选 F2 的参考指标工具。

定位（先写死）：
- 只产出“参考指标”，补充《策略收敛与回测路线图》§4 判定的证据面；**不改变任何既有判定标准**；
- 方法参考 Bailey & López de Prado (2014) "The Deflated Sharpe Ratio" 与
  Bailey / Borwein / López de Prado / Zhu (2015) "The Probability of Backtest Overfitting"（CSCV）。

口径与简化（同步写进输出 JSON，便于留痕）：
1. 样本来源：评估器统一口径（--entry-mode daily、31bps 成本、entry 可成交过滤、窗口 w3），
   逐样本“成本后收益”按信号日聚合成“日均收益”序列；
2. 日期网格：默认取“窗口内全部交易日”（由 stock_daily 去重得到，单/多配置族对称），
   某配置无信号日按空仓 0 处理；--nan-policy drop 可改为“仅保留全部配置都有信号的日子”；
3. DSR：SR* 取多试验期望最大 Sharpe
     SR0 = sqrt(V) * [ (1-g) * Z^{-1}(1 - 1/N) + g * Z^{-1}(1 - 1/(N*e)) ]，g ≈ 0.5772（Euler–Mascheroni）；
     DSR = PSR(SR0) = Phi( (SR - SR0) * sqrt(T-1) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )；
   SR、V 为“每次观测”口径（未年化）；偏度/峰度用总体矩（正态峰度 = 3）；
4. 试验数 N 与试验间 Sharpe 方差 V：优先取同族配置（>= 2 个）；单配置族退回“全局配置池”
   （输出标注 fallback）；--dsr-trials / --dsr-sr-std 可显式覆盖（全局生效）；
5. PBO：CSCV，时间轴均分为 S 块（默认 8）、枚举 C(S, S/2) 组合；子集排名指标 = 均值日收益
   （文档化简化：日频等权组合近似）；PBO = “IS 最优配置的 OOS 排名低于中位数”的组合占比；
   N 较小时分辨率有限（N=3 时只有 OOS 最差才计过拟合），属参考指标。

用法：
    ./.venv-linux/bin/python scripts/check_overfitting_risk.py \
      --family hdh=sens__hdh_nhw64,hundred_day_high,sens__hdh_nhw96 \
      --family trend=sens__trend_c60d2.4,trend_leader_unified,sens__trend_c60d3.6 \
      --family dsr=daily_slow_rise \
      --start-date 2026-08-04 --end-date 2026-09-24 \
      --output-json data/verification/overfitting_risk_2026-08-04_2026-09-24.json

注：预热/单配置场景（如仅有 3 条主线）亦可运行，DSR 会退回全局配置池并在输出中标注。
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
import statistics
import sys
from collections import Counter
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_random_percentile import _load_returns_by_day  # noqa: E402  （复用同源提取口径）
from src.storage import DatabaseManager  # noqa: E402

logger = logging.getLogger("check_overfitting_risk")

EULER_GAMMA = 0.5772156649015329
DEFAULT_WINDOW = 3
DEFAULT_S_BLOCKS = 8
_NORMAL = statistics.NormalDist()


def _mean_daily_series(by_day: Dict[str, List[float]]) -> Tuple[List[str], List[float]]:
    days = sorted(by_day)
    return days, [statistics.mean(by_day[day]) for day in days]


def _trading_days(db_path: Path, start_date: str, end_date: str) -> List[str]:
    """窗口内全部交易日（来自 stock_daily 去重；库不可用时返回空列表，由调用方回退）。"""
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT DISTINCT date FROM stock_daily WHERE date BETWEEN ? AND ? ORDER BY date",
                (start_date, end_date),
            ).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    return [str(row[0]) for row in rows]


def _family_arrays(
    series_list: Sequence[Tuple[List[str], List[float]]],
    nan_policy: str,
    trading_days: Sequence[str] | None = None,
) -> Tuple[List[str], List[List[float]]]:
    """把族内配置序列对齐到共同日期网格。

    返回 (days, arrays)：arrays[i] 为第 i 个配置在网格上的日收益序列。
    nan_policy=zero：网格 = trading_days（缺省退回全部配置信号日并集），缺失日填 0；
    nan_policy=drop：网格 = 全部配置都有信号的日子。
    """
    if not series_list:
        raise ValueError("空配置列表")
    if nan_policy == "drop":
        common = set(series_list[0][0])
        for days, _ in series_list[1:]:
            common &= set(days)
        grid = sorted(common)
    elif trading_days:
        grid = sorted({str(d) for d in trading_days})
    else:
        grid = sorted({d for days, _ in series_list for d in days})
    if not grid:
        raise ValueError("对齐后网格为空")
    arrays: List[List[float]] = []
    for days, values in series_list:
        mapping = dict(zip(days, values))
        if nan_policy == "drop":
            arrays.append([mapping[d] for d in grid])
        else:
            arrays.append([mapping.get(d, 0.0) for d in grid])
    return grid, arrays


def _sharpe_skew_kurt(values: Sequence[float]) -> Tuple[float, float, float, int]:
    """总体矩口径：SR = mean/sd，skew = m3/sd^3，kurt = m4/sd^4（正态峰度 = 3）。"""
    t = len(values)
    if t == 0:
        raise ValueError("空序列")
    mu = statistics.mean(values)
    m2 = sum((v - mu) ** 2 for v in values) / t
    if m2 <= 0:
        return 0.0, 0.0, 3.0, t
    sd = math.sqrt(m2)
    m3 = sum((v - mu) ** 3 for v in values) / t
    m4 = sum((v - mu) ** 4 for v in values) / t
    return mu / sd, m3 / (sd ** 3), m4 / (sd ** 4), t


def _psr(sr: float, skew: float, kurt: float, t: int, sr_star: float) -> float:
    """PSR(SR*)：Phi( (SR - SR*) * sqrt(T-1) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2) )。"""
    denominator = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denominator <= 0:
        denominator = 1e-12
    z = (sr - sr_star) * math.sqrt(max(t - 1, 1)) / math.sqrt(denominator)
    return float(_NORMAL.cdf(z))


def _sr0(sr_std: float, n_trials: int) -> float:
    """多试验期望最大 Sharpe（Bailey & López de Prado 2014 式）。"""
    if n_trials < 2:
        raise ValueError("试验数 N 需要 >= 2")
    z1 = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return float(sr_std) * ((1.0 - EULER_GAMMA) * z1 + EULER_GAMMA * z2)


def _cscv_pbo(columns: Sequence[Sequence[float]], s_blocks: int = DEFAULT_S_BLOCKS) -> Dict[str, Any]:
    """CSCV 计算 PBO。columns[i] 为第 i 个配置的等长日收益序列（已对齐、无缺失）。"""
    n_configs = len(columns)
    if n_configs < 2:
        return {"available": False, "reason": "需要 >= 2 个配置"}
    t = len(columns[0])
    if any(len(col) != t for col in columns):
        raise ValueError("配置序列长度不一致")
    s = int(s_blocks)
    if s % 2:
        s -= 1
    if s < 2 or t < s:
        return {"available": False, "reason": f"时间点不足（T={t}, S={s}）"}
    size = t // s
    t_used = size * s
    blocks = [
        [list(columns[ci][bi * size:(bi + 1) * size]) for ci in range(n_configs)]
        for bi in range(s)
    ]
    overfit = 0
    counted = 0
    selections: Counter = Counter()
    for is_blocks in combinations(range(s), s // 2):
        is_set = set(is_blocks)
        is_perf: List[float] = []
        oos_perf: List[float] = []
        for ci in range(n_configs):
            is_values = [v for bi in is_set for v in blocks[bi][ci]]
            oos_values = [v for bi in range(s) if bi not in is_set for v in blocks[bi][ci]]
            is_perf.append(statistics.mean(is_values))
            oos_perf.append(statistics.mean(oos_values))
        best = max(range(n_configs), key=lambda ci: is_perf[ci])
        selections[best] += 1
        counted += 1
        x = oos_perf[best]
        worse = sum(1 for v in oos_perf if v < x)
        equal = sum(1 for v in oos_perf if v == x)
        rank_from_bottom = worse + (equal + 1) / 2.0
        if rank_from_bottom < (n_configs + 1) / 2.0:
            overfit += 1
    pbo = (overfit / counted) if counted else None
    return {
        "available": True,
        "pbo": round(pbo, 4) if pbo is not None else None,
        "s_blocks": s,
        "splits": counted,
        "t_periods": t_used,
        "daily_points": t,
        "n_configs": n_configs,
        "metric": "mean_daily_return",
        "selections": {str(k): v for k, v in sorted(selections.items())},
    }


def _parse_families(specs: Sequence[str]) -> List[Tuple[str, List[str]]]:
    families: List[Tuple[str, List[str]]] = []
    for spec in specs:
        if "=" not in spec:
            raise SystemExit(f"--family 格式应为 name=type1[,type2...]，收到: {spec}")
        name, types = spec.split("=", 1)
        type_list = [item.strip() for item in types.split(",") if item.strip()]
        if not name.strip() or not type_list:
            raise SystemExit(f"--family 无效: {spec}")
        families.append((name.strip(), type_list))
    return families


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="防过拟合旁证（DSR / PBO-CSCV，参考指标）")
    parser.add_argument(
        "--family",
        action="append",
        required=True,
        help="配置族，格式 name=signal_type[,signal_type...]；可重复",
    )
    parser.add_argument("--start-date", required=True, help="快照起始日期 YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="快照结束日期 YYYY-MM-DD")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW, help=f"评估窗口，默认 {DEFAULT_WINDOW}")
    parser.add_argument("--benchmark-code", default="000300", help="基准代码（口径一致性），默认 000300")
    parser.add_argument(
        "--db",
        default=str(PROJECT_ROOT / "data" / "stock_analysis.db"),
        help="SQLite 库路径（读取交易日历用），默认 data/stock_analysis.db",
    )
    parser.add_argument(
        "--nan-policy",
        choices=["zero", "drop"],
        default="zero",
        help="缺失日处理：zero=空仓 0（默认），drop=仅保留全部配置都有信号的日子",
    )
    parser.add_argument("--s-blocks", type=int, default=DEFAULT_S_BLOCKS, help=f"CSCV 分块数（偶数），默认 {DEFAULT_S_BLOCKS}")
    parser.add_argument("--dsr-trials", type=int, default=0, help="显式覆盖试验数 N（全局）；0=自动")
    parser.add_argument("--dsr-sr-std", type=float, default=None, help="显式覆盖试验间 SR 标准差（每次观测口径）；缺省=自动")
    parser.add_argument("--output-json", default="", help="输出 JSON；默认 data/verification/overfitting_risk_<起>_<止>.json")
    parser.add_argument("--log-level", default="WARNING", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.WARNING),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if int(args.dsr_trials) == 1:
        raise SystemExit("--dsr-trials 需为 0（自动）或 >= 2")
    families_spec = _parse_families(args.family)

    db = DatabaseManager.get_instance()
    calendar = _trading_days(Path(args.db), args.start_date, args.end_date) if args.nan_policy == "zero" else []
    if args.nan_policy == "zero" and not calendar:
        logger.warning("未取得交易日历（%s），退回配置信号日并集网格", args.db)
    cache: Dict[str, Dict[str, Any]] = {}

    def _series(signal_type: str) -> Dict[str, Any]:
        if signal_type not in cache:
            by_window, _ = _load_returns_by_day(
                db,
                signal_type,
                start_date=args.start_date,
                end_date=args.end_date,
                windows=[int(args.window)],
                benchmark_code=args.benchmark_code,
            )
            by_day = by_window[int(args.window)]
            days, day_means = _mean_daily_series(by_day)
            cache[signal_type] = {
                "days": days,
                "day_means": day_means,
                "samples": [v for day in by_day for v in by_day[day]],
            }
        return cache[signal_type]

    # 第一遍：构建各家族对齐序列与逐配置统计
    family_data: List[Dict[str, Any]] = []
    all_srs: List[float] = []
    for name, types in families_spec:
        try:
            series_list = [(_series(st)["days"], _series(st)["day_means"]) for st in types]
            grid, arrays = _family_arrays(series_list, args.nan_policy, trading_days=calendar)
        except ValueError as exc:
            logger.warning("族 %s 跳过: %s", name, exc)
            family_data.append({"name": name, "types": types, "error": str(exc)})
            continue
        per_config: List[Dict[str, Any]] = []
        srs_raw: List[float] = []
        for st, values in zip(types, arrays):
            sr, skew, kurt, t = _sharpe_skew_kurt(values)
            srs_raw.append(sr)
            sample_values = _series(st)["samples"]
            per_config.append(
                {
                    "signal_type": st,
                    "primary": not st.startswith("sens__"),
                    "days": t,
                    "obs_samples": len(sample_values),
                    "sample_mean_after_cost_pct": (
                        round(statistics.mean(sample_values), 4) if sample_values else None
                    ),
                    "sr_per_day": round(sr, 4),
                    "skew": round(skew, 3),
                    "kurt": round(kurt, 3),
                    "psr_zero": round(_psr(sr, skew, kurt, t, 0.0), 4),
                    "_sr_raw": sr,
                    "_skew_raw": skew,
                    "_kurt_raw": kurt,
                }
            )
            all_srs.append(sr)
        family_data.append(
            {
                "name": name,
                "types": types,
                "grid_days": len(grid),
                "arrays": arrays,
                "srs_raw": srs_raw,
                "per_config": per_config,
            }
        )

    global_n = len(all_srs)
    global_std = statistics.stdev(all_srs) if global_n >= 2 else None

    # 第二遍：DSR（N/V 来源：族内 > 全局回退 > CLI 覆盖）与 PBO
    for fam in family_data:
        if "error" in fam:
            continue
        n_fam = len(fam["types"])
        if int(args.dsr_trials) >= 2:
            n_used = int(args.dsr_trials)
            if args.dsr_sr_std is not None:
                std_used = float(args.dsr_sr_std)
            elif n_fam >= 2:
                std_used = statistics.stdev(fam["srs_raw"])
            else:
                std_used = global_std
            source = "override"
        elif n_fam >= 2:
            n_used = n_fam
            std_used = statistics.stdev(fam["srs_raw"])
            source = "family"
        else:
            n_used = global_n
            std_used = global_std
            source = "global"
        for item in fam["per_config"]:
            if n_used >= 2 and std_used is not None:
                sr0 = _sr0(std_used, n_used)
                item["sr0"] = round(sr0, 4)
                item["dsr"] = round(
                    _psr(item["_sr_raw"], item["_skew_raw"], item["_kurt_raw"], item["days"], sr0),
                    4,
                )
                item["trials"] = {"n": n_used, "sr_std": round(std_used, 4), "source": source}
            else:
                item["sr0"] = None
                item["dsr"] = None
                item["trials"] = {
                    "n": n_used,
                    "sr_std": None if std_used is None else round(std_used, 4),
                    "source": source,
                    "note": "insufficient trials",
                }
        if n_fam >= 2:
            pbo = _cscv_pbo(fam["arrays"], s_blocks=int(args.s_blocks))
            if pbo.get("available"):
                pbo["selections"] = {
                    fam["types"][int(k)]: v for k, v in pbo["selections"].items()
                }
            fam["pbo"] = pbo
        else:
            fam["pbo"] = {"available": False, "reason": "single config"}
        fam.pop("arrays", None)
        fam.pop("srs_raw", None)
        for item in fam["per_config"]:
            for key in ("_sr_raw", "_skew_raw", "_kurt_raw"):
                item.pop(key, None)

    output_path = (
        Path(args.output_json)
        if args.output_json
        else PROJECT_ROOT
        / "data"
        / "verification"
        / f"overfitting_risk_{args.start_date}_{args.end_date}.json"
    )
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "caliber": {
            "source": "evaluator best_cases（entry-mode daily、31bps 成本、entry 可成交过滤）",
            "series": "逐样本 w3 成本后收益 → 日频等权组合收益；zero 模式网格 = 窗口内全部交易日（空仓日 = 0）",
            "nan_policy": args.nan_policy,
            "dsr": {
                "formula": "PSR(SR0)，SR0 = 多试验期望最大 Sharpe（Bailey & Lopez de Prado 2014）",
                "moment_basis": "总体矩（正态峰度 = 3）；SR/V 为每次观测口径（未年化）",
                "trials": "族内配置（>=2）优先；单配置族退回全局池；CLI 可覆盖",
            },
            "pbo": {
                "method": f"CSCV（Bailey et al. 2015），S={int(args.s_blocks)} 块",
                "metric": "均值日收益（文档化简化：日频等权组合近似）",
                "note": "小 N 分辨率有限（N=3 时只有 OOS 最差才计为过拟合）；参考指标",
            },
        },
        "filters": {
            "start_date": args.start_date,
            "end_date": args.end_date,
            "window": int(args.window),
            "benchmark_code": args.benchmark_code,
            "trading_days": len(calendar) if args.nan_policy == "zero" else None,
        },
        "families": family_data,
        "notes": [
            "参考指标，不改变 §4 判定标准；引用结论时必须连同本口径与 N/V 假设一起标注。",
        ],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[overfitting-risk] 生成: {output_path}")
    for fam in family_data:
        if "error" in fam:
            print(f"  [{fam['name']}] skipped: {fam['error']}")
            continue
        pbo = fam.get("pbo") or {}
        pbo_text = pbo.get("pbo") if pbo.get("available") else "--"
        print(f"  [{fam['name']}] grid_days={fam['grid_days']} pbo={pbo_text}")
        for item in fam["per_config"]:
            trials = item.get("trials") or {}
            print(
                "    {st} days={d} obs={obs} sample_mean={sm} sr={sr} psr0={p} dsr={dsr} (N={n}, {src})".format(
                    st=item["signal_type"],
                    d=item["days"],
                    obs=item["obs_samples"],
                    sm=item["sample_mean_after_cost_pct"],
                    sr=item["sr_per_day"],
                    p=item["psr_zero"],
                    dsr=item["dsr"],
                    n=trials.get("n"),
                    src=trials.get("source"),
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
