# Earnings Fast Scan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 完成 `earnings_surprise` 的 `scan-depth(low/medium/high)` 全链路接线，让快复盘默认走 `low`，并保证缓存口径、测试与文档一致。

**Architecture:** 保留现有 `strategy-profile(strict/balanced/relaxed)` 的评分与放行逻辑，仅新增第二维参数 `scan-depth` 控制基础面抓取深度。`select_earnings_surprise_candidates.py` 负责扫描深度解析与缓存块覆盖判断，`run_fast_review_bundle.py` 负责把快复盘默认值与 CLI/本地配置透传到业绩脚本。实现以最小改动为主，不重构评分体系。

**Tech Stack:** Python, argparse, pandas, pytest/unittest, markdown docs

---

## File Structure Map

- `scripts/run_fast_review_bundle.py`
职责：快复盘入口参数解析、外部脚本命令拼装与执行。
- `scripts/select_earnings_surprise_candidates.py`
职责：业绩线主扫描、同日缓存复用、候选评估与导出。
- `data_provider/fundamental_adapter.py`
职责：按 `enabled_blocks` 抓取基础面块（已具备能力，重点做回归防守）。
- `tests/test_fast_review_daily_bundle.py`
职责：快复盘 CLI 默认值与命令透传的回归保护。
- `tests/test_earnings_surprise_signal_flow.py`
职责：业绩线扫描、缓存块覆盖、`scan-depth` 运行时行为回归。
- `tests/test_fundamental_adapter.py`
职责：基础面 adapter 的块级抓取回归。
- `config/local_strategy_profile.json`
职责：本地快复盘默认策略配置。
- `docs/LOCAL_STRATEGY_CATALOG.md`, `docs/AI_MODIFICATION_LOG.md`, `docs/CHANGELOG.md`
职责：策略口径、AI 改动留痕、Unreleased 变更记录。

---

### Task 1: 打通快复盘参数层（`earnings_scan_depth`）

**Files:**
- Modify: `D:\bb\daily_stock_analysis\tests\test_fast_review_daily_bundle.py`
- Modify: `D:\bb\daily_stock_analysis\scripts\run_fast_review_bundle.py`

- [ ] **Step 1: 先写失败测试（快复盘参数与命令透传）**

```python
def test_parse_args_has_earnings_scan_depth_default_low() -> None:
    args = fast_bundle.parse_args(["--include-signals", "earnings"])
    assert args.earnings_scan_depth == "low"


def test_build_earnings_command_forwards_scan_depth() -> None:
    args = SimpleNamespace(
        earnings_strategy_profile="balanced",
        earnings_scan_depth="medium",
        persist_snapshots=True,
        max_workers=1,
        limit=None,
        log_level="INFO",
    )
    cmd = fast_bundle.build_earnings_command(
        args,
        snapshot_date=date(2026, 4, 21),
        output_dir=Path("tmp/earnings"),
    )
    assert "--scan-depth" in cmd
    assert cmd[cmd.index("--scan-depth") + 1] == "medium"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_fast_review_daily_bundle.py -k "earnings_scan_depth or build_commands_forward_skip_db_persist" -v`  
Expected: FAIL，提示 `Namespace` 缺少 `earnings_scan_depth` 或命令未包含 `--scan-depth`。

- [ ] **Step 3: 实现最小改动（解析 + 透传）**

```python
# scripts/run_fast_review_bundle.py
DEFAULT_EARNINGS_SCAN_DEPTH = "low"

parser.add_argument(
    "--earnings-scan-depth",
    default=DEFAULT_EARNINGS_SCAN_DEPTH,
    choices=["low", "medium", "high"],
    help="Scan depth for earnings script, default low for fast review.",
)

command = [
    sys.executable,
    str(PROJECT_ROOT / "scripts" / "select_earnings_surprise_candidates.py"),
    "--snapshot-date",
    snapshot_date.isoformat(),
    "--strategy-profile", str(args.earnings_strategy_profile),
    "--scan-depth", str(args.earnings_scan_depth),
    "--output-dir", str(output_dir),
    "--max-workers", str(max(1, int(args.max_workers))),
    "--log-level", str(args.log_level),
]
```

- [ ] **Step 4: 重新运行测试确认通过**

Run: `python -m pytest tests/test_fast_review_daily_bundle.py -v`  
Expected: PASS。

- [ ] **Step 5: 检查点提交（仅在用户明确同意后）**

```bash
git add tests/test_fast_review_daily_bundle.py scripts/run_fast_review_bundle.py
git commit -m "feat: wire earnings scan-depth in fast review bundle"
```

---

### Task 2: 打通业绩脚本扫描深度运行链路

**Files:**
- Modify: `D:\bb\daily_stock_analysis\tests\test_earnings_surprise_signal_flow.py`
- Modify: `D:\bb\daily_stock_analysis\scripts\select_earnings_surprise_candidates.py`

- [ ] **Step 1: 先写失败测试（CLI + scan_market 入参）**

```python
def test_parse_args_v2_supports_scan_depth(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["select_earnings_surprise_candidates.py", "--scan-depth", "low"],
    )
    args = parse_args_v2()
    assert args.scan_depth == "low"

def test_scan_market_low_depth_uses_core_fundamental_blocks(self) -> None:
    universe_df = pd.DataFrame(
        {"code": ["600031"], "name": ["scan_depth_sample"], "total_mv": [52e8], "latest_price": [10.5]}
    )
    bundle_payload = {
        "growth": {"revenue_yoy": 18.0, "net_profit_yoy": 36.0, "roe": 11.0},
        "earnings": {"financial_report": {"report_date": "2026-03-31"}, "forecast_summary": "业绩预增"},
        "source_chain": ["test"],
    }
    with patch(
        "data_provider.fundamental_adapter.AkshareFundamentalAdapter.get_fundamental_bundle",
        return_value=bundle_payload,
    ) as bundle_fetch, patch(
        "scripts.select_earnings_surprise_candidates.CapitalProfileService.build_stock_profile",
        return_value={"capital_consensus_score": 2, "capital_profile_score": 61.0},
    ), patch(
        "scripts.select_earnings_surprise_candidates.build_recent_earnings_event_catalog",
        return_value={},
    ):
    run_result = scan_market(
        criteria=EarningsSurpriseCriteria(),
        snapshot_date=date(2026, 4, 19),
        signal_type=SIGNAL_TYPE,
        history_lookback_days=365,
        event_lookback_days=120,
        db=self.db,
        limit=None,
        max_workers=1,
        universe_provider=lambda: universe_df.copy(),
        scan_depth="low",
    )
    assert bundle_fetch.call_args.kwargs["enabled_blocks"] == (
        "financial",
        "forecast",
        "quick_report",
    )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_earnings_surprise_signal_flow.py -k "scan_depth or parse_args_v2" -v`  
Expected: FAIL，典型报错为 `scan_market() got an unexpected keyword argument 'scan_depth'` 或 `args.scan_depth` 不存在。

- [ ] **Step 3: 实现最小代码**

```python
# scripts/select_earnings_surprise_candidates.py
def parse_args_v2() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="扫描 A 股财报强势代理信号，并落库为 earnings_surprise 快照。",
    )
    parser.add_argument("--limit", type=int, default=None, help="仅扫描前 N 只股票。")
    parser.add_argument("--snapshot-date", default=None, help="信号日期，格式 YYYY-MM-DD，默认今天。")
    parser.add_argument(
        "--scan-depth",
        default=DEFAULT_SCAN_DEPTH,
        choices=list(SCAN_DEPTH_CHOICES),
        help="基础面扫描深度：low / medium / high，默认 high。",
    )
    parser.add_argument("--max-workers", type=int, default=1, help="并发 worker 数。")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="日志级别。")
    return parser.parse_args()


def scan_market(
    *,
    criteria: EarningsSurpriseCriteria,
    snapshot_date: date,
    signal_type: str,
    history_lookback_days: int,
    event_lookback_days: int,
    db: Optional[DatabaseManager],
    limit: Optional[int],
    max_workers: int,
    scan_depth: str = DEFAULT_SCAN_DEPTH,
    shard_count: int = 1,
    shard_index: int = 0,
    checkpoint_path: Optional[Path] = None,
    checkpoint_every: int = 100,
    resume: bool = False,
    universe_provider: Optional[Any] = None,
    bundle_loader: Optional[Any] = None,
    on_evaluation: Optional[Callable[[EarningsSurpriseEvaluation, int, int], None]] = None,
) -> EarningsSurpriseRunResult:
    normalized_scan_depth, required_blocks = resolve_scan_depth_enabled_blocks(scan_depth)
    required_blocks_list = list(required_blocks)
    cached_payload = load_or_fetch_signal_fundamental_snapshot(
        db=db,
        cache_signal_type=cache_signal_type,
        snapshot_date=snapshot_date,
        stock_code=row_payload["code"],
        stock_name=row_payload["name"],
        total_market_cap=row_payload["total_market_cap"],
        latest_price=row_payload["latest_price"],
        adapter=adapter,
        recent_event_payload=recent_event_catalog.get(row_payload["code"]),
        scan_depth=normalized_scan_depth,
        required_blocks=required_blocks,
    )
    evaluation.metrics.update(
        {
            "cache_source": cache_source,
            "bundle_refreshed_at": bundle_refreshed_at,
            "capital_profile_refreshed_at": capital_profile_refreshed_at,
            "capital_profile_cache_hit": capital_profile_cache_hit,
            "fundamental_refreshed": fundamental_refreshed,
            "quote_capital_refreshed": quote_capital_refreshed,
            "scan_depth": normalized_scan_depth,
            "enabled_blocks": required_blocks_list,
        }
    )


def main() -> int:
    args = parse_args_v2()
    run_result = scan_market(
        criteria=criteria,
        snapshot_date=snapshot_date,
        signal_type=signal_type,
        history_lookback_days=max(1, int(args.history_lookback_days)),
        event_lookback_days=max(1, int(args.event_lookback_days)),
        db=db,
        limit=args.limit,
        max_workers=args.max_workers,
        scan_depth=args.scan_depth,
        shard_count=args.shard_count,
        shard_index=args.shard_index,
        checkpoint_path=checkpoint_path,
        checkpoint_every=args.checkpoint_every,
        resume=args.resume,
    )
```

- [ ] **Step 4: 重新运行测试确认通过**

Run: `python -m pytest tests/test_earnings_surprise_signal_flow.py -k "scan_depth or fundamental_snapshot_refreshes_when_cached_blocks_missing" -v`  
Expected: PASS。

- [ ] **Step 5: 检查点提交（仅在用户明确同意后）**

```bash
git add tests/test_earnings_surprise_signal_flow.py scripts/select_earnings_surprise_candidates.py
git commit -m "feat: thread scan-depth through earnings scan runtime"
```

---

### Task 3: 适配器块抓取与缓存兼容回归防守

**Files:**
- Modify: `D:\bb\daily_stock_analysis\tests\test_fundamental_adapter.py`
- Modify: `D:\bb\daily_stock_analysis\tests\test_earnings_surprise_signal_flow.py`
- Modify: `D:\bb\daily_stock_analysis\data_provider\fundamental_adapter.py` (仅当测试暴露缺陷时)

- [ ] **Step 1: 增加回归测试（未知块忽略、核心块可运行）**

```python
def test_fundamental_bundle_ignores_unknown_enabled_blocks() -> None:
    adapter = AkshareFundamentalAdapter()
    with patch.object(adapter, "_call_df_candidates", return_value=(None, None, [])) as call_mock:
        result = adapter.get_fundamental_bundle(
            "600519",
            enabled_blocks=("financial", "forecast", "unknown_block"),
        )
    assert isinstance(result, dict)
    assert call_mock.call_count == 2
```

- [ ] **Step 2: 运行适配器测试确认现状**

Run: `python -m pytest tests/test_fundamental_adapter.py -k "enabled_blocks or unknown_enabled_blocks" -v`  
Expected: 若失败，暴露块过滤逻辑问题；若通过，可保持代码不改，仅保留测试增强。

- [ ] **Step 3: 仅在失败时做最小修复**

```python
# data_provider/fundamental_adapter.py
enabled_block_set = {
    block for block in normalized_blocks if block in _SUPPORTED_FUNDAMENTAL_BLOCKS
}
if not enabled_block_set:
    enabled_block_set = set(_SUPPORTED_FUNDAMENTAL_BLOCKS)
```

- [ ] **Step 4: 回归业绩缓存测试**

Run: `python -m pytest tests/test_earnings_surprise_signal_flow.py -k "cached_blocks_missing or scan_market_low_depth_uses_core_fundamental_blocks" -v`  
Expected: PASS，且 `enabled_blocks` 调用口径正确。

- [ ] **Step 5: 检查点提交（仅在用户明确同意后）**

```bash
git add tests/test_fundamental_adapter.py tests/test_earnings_surprise_signal_flow.py data_provider/fundamental_adapter.py
git commit -m "test: harden block-aware fundamental adapter behavior"
```

---

### Task 4: 同步本地默认配置与策略文档留痕

**Files:**
- Modify: `D:\bb\daily_stock_analysis\config\local_strategy_profile.json`
- Modify: `D:\bb\daily_stock_analysis\docs\LOCAL_STRATEGY_CATALOG.md`
- Modify: `D:\bb\daily_stock_analysis\docs\AI_MODIFICATION_LOG.md`
- Modify: `D:\bb\daily_stock_analysis\docs\CHANGELOG.md`

- [ ] **Step 1: 更新本地默认配置到快复盘 `low`**

```json
{
  "defaults": {
    "earnings_strategy_profile": "balanced",
    "earnings_scan_depth": "low"
  }
}
```

- [ ] **Step 2: 更新策略目录文档（新增 scan-depth 轴）**

```md
- `earnings_surprise` 新增 `--scan-depth low|medium|high`：
  - `low`：快复盘优先，核心块抓取 + 通过票补 `capital_profile`
  - `medium`：核心块 + 通过票补全
  - `high`：完整抓取口径
```

- [ ] **Step 3: 更新 `AI_MODIFICATION_LOG` 留痕**

```md
- Scope: earnings fast scan depth rollout
- Why: reduce fast-review latency without changing strategy-profile scoring semantics
- What:
  - wired `--earnings-scan-depth` in fast review
  - wired `--scan-depth` in earnings selector runtime/cache path
  - updated local defaults/docs
```

- [ ] **Step 4: 更新 `CHANGELOG` 的 `[Unreleased]` 扁平条目**

```md
- [改进] `earnings_surprise` 新增 `scan-depth(low/medium/high)` 扫描深度维度，快复盘入口默认切换为 `low` 以降低日常扫描耗时。
- [文档] 补充本地策略目录与 AI 修改日志中的 `earnings scan-depth` 口径与默认值说明。
```

- [ ] **Step 5: 文档一致性检查**

Run: `rg -n "earnings_scan_depth|scan-depth|low|medium|high" config/local_strategy_profile.json docs/LOCAL_STRATEGY_CATALOG.md docs/AI_MODIFICATION_LOG.md docs/CHANGELOG.md`  
Expected: 能检索到一致口径且无冲突描述。

---

### Task 5: 端到端验证与交付前检查

**Files:**
- Verify only

- [ ] **Step 1: 跑目标测试集**

Run: `python -m pytest tests/test_fast_review_daily_bundle.py tests/test_earnings_surprise_signal_flow.py tests/test_fundamental_adapter.py -v`  
Expected: PASS。

- [ ] **Step 2: 执行 Python 语法检查**

Run: `python -m py_compile scripts/run_fast_review_bundle.py scripts/select_earnings_surprise_candidates.py data_provider/fundamental_adapter.py`  
Expected: 无输出（成功）。

- [ ] **Step 3: 关键行为冒烟（不落库）**

Run: `python scripts/run_fast_review_bundle.py --snapshot-date 2026-04-21 --include-signals earnings --earnings-scan-depth low --skip-persist-snapshots --limit 20`  
Expected: 控制台输出包含 `signal_earnings_count=`，且命令链路不报参数错误。

- [ ] **Step 4: Diff 范围检查**

Run: `git diff -- scripts/run_fast_review_bundle.py scripts/select_earnings_surprise_candidates.py data_provider/fundamental_adapter.py tests/test_fast_review_daily_bundle.py tests/test_earnings_surprise_signal_flow.py tests/test_fundamental_adapter.py config/local_strategy_profile.json docs/LOCAL_STRATEGY_CATALOG.md docs/AI_MODIFICATION_LOG.md docs/CHANGELOG.md`  
Expected: 仅包含本计划目标范围改动，无无关重构。

- [ ] **Step 5: 交付说明模板准备**

```md
改了什么：
为什么这么改：
验证情况：
未验证项：
风险点：
回滚方式：
```
