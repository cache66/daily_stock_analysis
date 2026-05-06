# Shortline Hub V1 Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把当前 `shortline_hub` 从“能跑一次的短线编排”扩展成“5 月 5 日起可连续使用的短线研究台 v1”，同时把最有价值的统一查询能力并入主工程。

**Architecture:** 继续复用现有 `scripts/run_shortline_hub.py -> ShortlineHubOrchestrator -> WonderTrader/FinGenius adapters -> write_shortline_artifacts(...)` 主链路，不新增平行主流程。第一阶段先补齐 `历史跟踪 / 自选股批量解释 / explain 缓存 / 日报聚合` 四个使用闭环；第二阶段再把短线结果作为新的 `signal_type` 并入既有 `kline_signal_snapshot -> SignalSnapshotService -> /api/v1/signals` 查询体系，并在 fast review 中做轻量读取整合。

**Tech Stack:** Python, pytest, existing `shortline_hub` modules, existing `DatabaseManager.upsert_signal_snapshot(...)`, `SignalSnapshotService`, FastAPI `/api/v1/signals`, Markdown/JSON artifact export under `data/manual_runs`

---

## File Map

- Modify: `scripts/run_shortline_hub.py`
  - 增加自选股批量解释、tracking/cache/snapshot 开关
- Modify: `src/shortline_hub/schemas.py`
  - 扩展短线结果、跟踪摘要和快照载荷字段
- Modify: `src/shortline_hub/orchestrator.py`
  - 接入 explain cache、tracking 元数据和手工候选解释路径
- Modify: `src/shortline_hub/report_builder.py`
  - 输出主方向摘要、板块主票/跟随、历史跟踪摘要
- Create: `src/shortline_hub/tracking.py`
  - 读写 `shortline_tracking_history.json/csv`，计算连续出现、层级变化、最近命中历史
- Create: `src/shortline_hub/watchlist_loader.py`
  - 把 `--symbols` / `--symbols-file` 转成可解释的 `ShortlineCandidate`
- Create: `src/shortline_hub/explain_cache.py`
  - 同日 explain 读写缓存，键控 `trade_date + symbol + mode`
- Create: `src/shortline_hub/snapshot_sync.py`
  - 把短线结果落到 `kline_signal_snapshot`
- Modify: `src/services/signal_snapshot_service.py`
  - 注册 `shortline_hub` / `shortline_top_pick` / `shortline_watchlist` 等 signal types，保证 `/signals` 可读
- Modify: `api/v1/schemas/signals.py`
  - 如现有字段不够，补短线专用 list/history 展示字段
- Modify: `api/v1/endpoints/signals.py`
  - 保持现有接口不变，只确保新 signal types 查询与错误路径可用
- Modify: `scripts/run_fast_review_bundle.py`
  - 可选读取最近短线快照/产物，在 `fast_review_summary.md` 中附加短线观察摘要
- Create: `tests/test_shortline_hub_tracking.py`
  - 覆盖 tracking history 聚合、连续出现、层级变化
- Create: `tests/test_shortline_watchlist_loader.py`
  - 覆盖手工股票列表到候选的转换
- Create: `tests/test_shortline_explain_cache.py`
  - 覆盖 explain cache 命中与失效
- Modify: `tests/test_shortline_hub_orchestrator.py`
  - 覆盖 cache、tracking、manual watchlist explain
- Modify: `tests/test_shortline_daily_review_layers.py`
  - 覆盖主方向摘要和主票/跟随分组
- Modify: `tests/test_shortline_hub_cli.py`
  - 覆盖新增 CLI 参数、tracking/cache/snapshot artifacts
- Modify: `tests/test_signal_snapshot_service.py`
  - 覆盖短线 signal types 查询
- Modify: `tests/test_signal_snapshot_api.py`
  - 覆盖 `/api/v1/signals` 对短线快照的查询
- Modify: `tests/test_fast_review_daily_bundle.py`
  - 覆盖 fast review 读取短线摘要
- Modify: `docs/architecture/2026-05-02-shortline-hub-single-machine-setup.md`
  - 更新 daily 使用口径
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
  - 注册短线 v1 的入口和定位
- Modify: `docs/AI_MODIFICATION_LOG.md`
  - 记录实现与验证
- Modify: `docs/CHANGELOG.md`
  - 在 `[Unreleased]` 下追加扁平变更摘要

## Phase Scope

### Phase 1: 5 月 5 日前必须完成

- 短线历史跟踪
- 自选股批量解释入口
- 同日 explain cache
- 日报聚合摘要与板块主票/跟随展示

### Phase 2: 一起推进，性价比高

- 短线结果落 snapshot
- `/api/v1/signals` 可查询
- fast review 读取短线摘要

### Phase 3: 只有前两阶段稳定后再做

- 基于 snapshot 的短线回看/命中统计
- 更复杂的后验评分和误伤分析

本计划只覆盖 Phase 1 与 Phase 2；Phase 3 只预留接口，不在本轮实现。

### Task 1: Add Failing Tests And Data Contract For Shortline Tracking

**Files:**
- Create: `tests/test_shortline_hub_tracking.py`
- Modify: `src/shortline_hub/schemas.py`
- Create: `src/shortline_hub/tracking.py`

- [ ] **Step 1: 写 tracking 失败测试**

在 `tests/test_shortline_hub_tracking.py` 中新增：

```python
from src.shortline_hub.schemas import ShortlineCombinedResult, ShortlineRunResult
from src.shortline_hub.tracking import build_tracking_summary, merge_tracking_history


def test_merge_tracking_history_updates_streak_and_tier_transition(tmp_path):
    previous = [
        {
            "trade_date": "2026-05-02",
            "symbol": "300083",
            "review_tier": "watchlist",
            "composite_score": 128.4,
        }
    ]
    current = [
        {
            "trade_date": "2026-05-03",
            "symbol": "300083",
            "review_tier": "top_pick",
            "composite_score": 139.2,
        }
    ]

    merged = merge_tracking_history(previous_rows=previous, current_rows=current)
    assert merged[0]["appear_streak_days"] == 2
    assert merged[0]["tier_transition"] == "watchlist->top_pick"


def test_build_tracking_summary_groups_repeat_symbols():
    rows = [
        {"symbol": "300083", "name": "创世纪", "appear_streak_days": 3, "review_tier": "top_pick"},
        {"symbol": "688256", "name": "寒武纪", "appear_streak_days": 1, "review_tier": "high_risk_mover"},
    ]
    summary = build_tracking_summary(rows)
    assert summary["repeat_symbol_count"] == 1
    assert summary["longest_streak_days"] == 3
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_shortline_hub_tracking.py -q
```

Expected:

- `ModuleNotFoundError` 或 `ImportError` for `src.shortline_hub.tracking`

- [ ] **Step 3: 写最小 tracking 实现**

在 `src/shortline_hub/tracking.py` 中实现首版：

```python
def merge_tracking_history(*, previous_rows, current_rows):
    previous_by_symbol = {str(row["symbol"]): row for row in previous_rows}
    merged = []
    for row in current_rows:
        symbol = str(row["symbol"])
        previous = previous_by_symbol.get(symbol)
        streak = 1
        tier_transition = ""
        if previous is not None:
            streak = int(previous.get("appear_streak_days") or 1) + 1
            previous_tier = str(previous.get("review_tier") or "").strip()
            current_tier = str(row.get("review_tier") or "").strip()
            if previous_tier and current_tier and previous_tier != current_tier:
                tier_transition = f"{previous_tier}->{current_tier}"
        merged.append({**row, "appear_streak_days": streak, "tier_transition": tier_transition})
    return merged


def build_tracking_summary(rows):
    repeat_rows = [row for row in rows if int(row.get("appear_streak_days") or 0) >= 2]
    return {
        "repeat_symbol_count": len(repeat_rows),
        "longest_streak_days": max([int(row.get("appear_streak_days") or 0) for row in rows] or [0]),
    }
```

并在 `src/shortline_hub/schemas.py` 中为 `ShortlineCombinedResult` 追加：

```python
tracking_appear_streak_days: int = 0
tracking_last_seen_dates: list[str] = field(default_factory=list)
tracking_tier_transition: str = ""
```

- [ ] **Step 4: 重跑测试确认通过**

Run:

```bash
python -m pytest tests/test_shortline_hub_tracking.py -q
```

Expected:

- `2 passed`

- [ ] **Step 5: 扩展回归到 orchestrator/report 消费准备**

Run:

```bash
python -m py_compile src/shortline_hub/schemas.py src/shortline_hub/tracking.py
```

Expected:

- no output

### Task 2: Add Manual Watchlist Batch Explain Entry

**Files:**
- Create: `src/shortline_hub/watchlist_loader.py`
- Modify: `scripts/run_shortline_hub.py`
- Create: `tests/test_shortline_watchlist_loader.py`
- Modify: `tests/test_shortline_hub_cli.py`

- [ ] **Step 1: 写 watchlist loader 失败测试**

在 `tests/test_shortline_watchlist_loader.py` 中新增：

```python
from src.shortline_hub.watchlist_loader import load_watchlist_candidates


def test_load_watchlist_candidates_from_symbols_argument():
    rows = load_watchlist_candidates(
        trade_date="2026-05-03",
        symbols=["300083", "688256"],
    )
    assert [row.symbol for row in rows] == ["300083", "688256"]
    assert all(row.scan_source == "manual_watchlist" for row in rows)


def test_load_watchlist_candidates_deduplicates_symbols():
    rows = load_watchlist_candidates(
        trade_date="2026-05-03",
        symbols=["300083", "300083", "688256"],
    )
    assert [row.symbol for row in rows] == ["300083", "688256"]
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_shortline_watchlist_loader.py -q
```

Expected:

- import failure for `src.shortline_hub.watchlist_loader`

- [ ] **Step 3: 写最小 watchlist loader 与 CLI 参数**

在 `src/shortline_hub/watchlist_loader.py` 中提供：

```python
def load_watchlist_candidates(*, trade_date: str, symbols: list[str]) -> list[ShortlineCandidate]:
    deduped = []
    seen = set()
    for raw in symbols:
        symbol = str(raw or "").strip()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        deduped.append(
            ShortlineCandidate(
                candidate_id=f"{trade_date}-{symbol}",
                symbol=symbol,
                name=symbol,
                trade_date=trade_date,
                scan_source="manual_watchlist",
                trigger_type="manual_watchlist",
                trigger_reason="manual_watchlist",
                trigger_score=0.0,
                price=0.0,
                change_pct=0.0,
                change_pct_60d=0.0,
                amount=0.0,
                volume_ratio=0.0,
                turnover_rate=0.0,
                board_name="",
                setup_tag="手工观察",
                risk_flags=[],
            )
        )
    return deduped
```

在 `scripts/run_shortline_hub.py` 中追加参数：

```python
parser.add_argument("--symbols", default="")
parser.add_argument("--symbols-file", default="")
parser.add_argument("--manual-watchlist", action="store_true")
```

并让 `_build_orchestrator(...)` 在 `manual_watchlist` 模式下复用现有 `FinGenius` explainer，只替换 scanner。

- [ ] **Step 4: 写 CLI 失败到通过的回归**

在 `tests/test_shortline_hub_cli.py` 中新增：

```python
def test_shortline_hub_cli_supports_manual_watchlist_mode(monkeypatch, tmp_path: Path):
    module = importlib.import_module("scripts.run_shortline_hub")

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_shortline_hub.py",
            "--trade-date",
            "2026-05-03",
            "--manual-watchlist",
            "--symbols",
            "300083,688256",
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )

    exit_code = module.main()
    assert exit_code == 0
    report_text = (tmp_path / "out" / "shortline_report.md").read_text(encoding="utf-8")
    assert "manual_watchlist" in (tmp_path / "out" / "shortline_candidates.json").read_text(encoding="utf-8")
    assert "300083" in report_text
```

Run:

```bash
python -m pytest tests/test_shortline_watchlist_loader.py tests/test_shortline_hub_cli.py -k "watchlist" -q
```

Expected:

- tests pass and output artifact contains `scan_source=manual_watchlist`

- [ ] **Step 5: 编译检查**

Run:

```bash
python -m py_compile scripts/run_shortline_hub.py src/shortline_hub/watchlist_loader.py
```

Expected:

- no output

### Task 3: Add Same-Day Explain Cache

**Files:**
- Create: `src/shortline_hub/explain_cache.py`
- Modify: `src/shortline_hub/orchestrator.py`
- Create: `tests/test_shortline_explain_cache.py`
- Modify: `tests/test_shortline_hub_orchestrator.py`

- [ ] **Step 1: 写 explain cache 失败测试**

在 `tests/test_shortline_explain_cache.py` 中新增：

```python
from src.shortline_hub.explain_cache import ShortlineExplainCache
from src.shortline_hub.schemas import ShortlineExplanation


def test_explain_cache_roundtrip(tmp_path):
    cache = ShortlineExplainCache(tmp_path / "cache.json")
    explanation = ShortlineExplanation(
        candidate_id="2026-05-03-300083",
        hot_money_summary="hot",
        big_deal_summary="big",
        chip_commentary="chip",
        sentiment_commentary="sentiment",
        risk_commentary="risk",
        short_term_view="view",
        confidence_label="high",
    )
    cache.put(trade_date="2026-05-03", symbol="300083", mode="light", explanation=explanation)
    loaded = cache.get(trade_date="2026-05-03", symbol="300083", mode="light")
    assert loaded is not None
    assert loaded.hot_money_summary == "hot"
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_shortline_explain_cache.py -q
```

Expected:

- import failure for `src.shortline_hub.explain_cache`

- [ ] **Step 3: 写最小 cache 实现并接入 orchestrator**

在 `src/shortline_hub/explain_cache.py` 中提供：

```python
class ShortlineExplainCache:
    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, *, trade_date: str, symbol: str, mode: str) -> ShortlineExplanation | None:
        key = f"{trade_date}::{symbol}::{mode}"
        payload = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        row = payload.get(key)
        return ShortlineExplanation(**row) if row else None

    def put(
        self,
        *,
        trade_date: str,
        symbol: str,
        mode: str,
        explanation: ShortlineExplanation,
    ) -> None:
        key = f"{trade_date}::{symbol}::{mode}"
        payload = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        payload[key] = explanation.to_dict()
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
```

在 `src/shortline_hub/orchestrator.py` 中增加可选注入：

```python
def __init__(
    self,
    *,
    scanner: object,
    explainer: object,
    explain_cache: object | None = None,
    explain_cache_mode: str = "light",
) -> None:
    self.scanner = scanner
    self.explainer = explainer
    self.explain_cache = explain_cache
    self.explain_cache_mode = str(explain_cache_mode or "light")
```

逻辑要求：

- cache 命中时不重复调用 `explainer.explain_candidate(...)`
- cache miss 时正常调用，并把结果写回 cache

- [ ] **Step 4: 写 orchestrator 命中测试**

在 `tests/test_shortline_hub_orchestrator.py` 中新增：

```python
def test_shortline_orchestrator_reuses_same_day_explain_cache(tmp_path):
    cache = ShortlineExplainCache(tmp_path / "cache.json")
    scanner = _StaticScanner(["300083"])
    explainer = _CountingExplainer()

    orchestrator = ShortlineHubOrchestrator(
        scanner=scanner,
        explainer=explainer,
        explain_cache=cache,
        explain_cache_mode="light",
    )
    orchestrator.run(run_id="r1", trade_date="2026-05-03", top_n=1)
    orchestrator.run(run_id="r2", trade_date="2026-05-03", top_n=1)

    assert explainer.call_count == 1
```

Run:

```bash
python -m pytest tests/test_shortline_explain_cache.py tests/test_shortline_hub_orchestrator.py -k "cache" -q
```

Expected:

- cache roundtrip test passes
- explainer call count is `1` after two same-day runs

- [ ] **Step 5: 编译检查**

Run:

```bash
python -m py_compile src/shortline_hub/explain_cache.py src/shortline_hub/orchestrator.py
```

Expected:

- no output

### Task 4: Enhance Daily Report With Main Direction And Tracking Summary

**Files:**
- Modify: `src/shortline_hub/report_builder.py`
- Modify: `tests/test_shortline_daily_review_layers.py`
- Modify: `src/shortline_hub/tracking.py`

- [ ] **Step 1: 写报告失败测试**

在 `tests/test_shortline_daily_review_layers.py` 中新增：

```python
def test_shortline_report_includes_main_direction_and_board_leader_grouping():
    result = _build_layered_result_for_report(
        symbols=["300083", "300999"],
        board_names=["机床制造", "机床制造"],
        board_core_ranks=[1, 2],
    )
    report = build_shortline_report_markdown(result)
    assert "## 今日主方向" in report
    assert "机床制造: 主票 300083 创世纪" in report
    assert "跟随" in report


def test_shortline_report_includes_tracking_summary_when_repeat_symbols_exist():
    result = _build_layered_result_for_report(
        symbols=["300083"],
        board_names=["机床制造"],
        appear_streak_days=[3],
    )
    report = build_shortline_report_markdown(result)
    assert "## 历史跟踪摘要" in report
    assert "连续出现 3 天" in report
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_shortline_daily_review_layers.py -k "main_direction or tracking_summary" -q
```

Expected:

- missing section assertions fail

- [ ] **Step 3: 实现主方向摘要与跟踪摘要**

在 `src/shortline_hub/report_builder.py` 中增加两个 section：

```python
## 今日主方向
## 历史跟踪摘要
```

展示规则：

- 主方向按 `board_name` 聚合
- 每个板块只突出 `board_core_rank == 1` 的主票
- 同板块其他票放到跟随描述
- 跟踪摘要只列最近连续出现天数 >= 2 的样本

- [ ] **Step 4: 重跑报告测试**

Run:

```bash
python -m pytest tests/test_shortline_daily_review_layers.py -q
```

Expected:

- all shortline daily review layer tests pass

- [ ] **Step 5: 本地 smoke 复查**

Run:

```bash
python scripts/run_shortline_hub.py --mode stub --trade-date 2026-05-03 --top-n 3 --run-id shortline_stub_v1_report --output-dir data/manual_runs/shortline_stub_v1_report
```

Expected:

- generated `shortline_report.md` contains the two new sections

### Task 5: Persist Shortline Results Into Signal Snapshot And Expose Via API

**Files:**
- Create: `src/shortline_hub/snapshot_sync.py`
- Modify: `scripts/run_shortline_hub.py`
- Modify: `src/services/signal_snapshot_service.py`
- Modify: `api/v1/schemas/signals.py`
- Modify: `api/v1/endpoints/signals.py`
- Modify: `tests/test_signal_snapshot_service.py`
- Modify: `tests/test_signal_snapshot_api.py`

- [ ] **Step 1: 写 snapshot sync 失败测试**

在 `tests/test_signal_snapshot_service.py` 中新增：

```python
def test_shortline_snapshot_rows_are_queryable_by_signal_type():
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'shortline_signal_snapshot.db'}")
    db.upsert_signal_snapshot(
        signal_type="shortline_top_pick",
        signal_date=date(2026, 5, 3),
        code="300083",
        name="创世纪",
        engine_version="shortline_hub_v1",
        metrics_payload={"review_tier": "top_pick", "board_name": "机床制造"},
    )
    service = SignalSnapshotService(db)
    payload = service.get_snapshot_list(signal_type="shortline_top_pick", signal_date="2026-05-03")
    assert payload["total"] == 1
    assert payload["items"][0]["code"] == "300083"
```

- [ ] **Step 2: 运行测试确认当前行为缺失**

Run:

```bash
python -m pytest tests/test_signal_snapshot_service.py tests/test_signal_snapshot_api.py -k "shortline" -q
```

Expected:

- new assertions fail or new signal types are not included in defaults

- [ ] **Step 3: 实现 snapshot sync**

在 `src/shortline_hub/snapshot_sync.py` 中实现：

```python
def persist_shortline_run_to_snapshots(*, db_manager, result, source: str = "run_shortline_hub") -> int:
    persisted = 0
    for item in result.combined_results:
        signal_type = {
            "top_pick": "shortline_top_pick",
            "watchlist": "shortline_watchlist",
            "high_risk_mover": "shortline_high_risk_mover",
        }.get(item.review_tier, "shortline_hub")
        db_manager.upsert_signal_snapshot(
            signal_type=signal_type,
            signal_date=result.trade_date,
            code=item.symbol,
            name=item.name,
            engine_version="shortline_hub_v1",
            metrics_payload={
                "review_tier": item.review_tier,
                "board_name": item.board_name,
                "composite_score": item.composite_score,
                "source": source,
            },
        )
        persisted += 1
    return persisted
```

落库策略：

- `top_pick` -> `signal_type=shortline_top_pick`
- `watchlist` -> `signal_type=shortline_watchlist`
- `high_risk_mover` -> `signal_type=shortline_high_risk_mover`
- 可选额外写一份总表 `signal_type=shortline_hub`

在 `scripts/run_shortline_hub.py` 中加：

```python
parser.add_argument("--persist-snapshot", action="store_true")
```

- [ ] **Step 4: 接入 query service 与 API**

在 `src/services/signal_snapshot_service.py` 的 `DEFAULT_SIGNAL_TYPES` 中追加：

```python
"shortline_hub",
"shortline_top_pick",
"shortline_watchlist",
"shortline_high_risk_mover",
```

如 `api/v1/schemas/signals.py` 需要额外字段，则只追加字段，不改现有字段语义。

- [ ] **Step 5: 重跑 service/api 测试**

Run:

```bash
python -m pytest tests/test_signal_snapshot_service.py tests/test_signal_snapshot_api.py -k "shortline" -q
```

Expected:

- shortline snapshot tests pass

### Task 6: Integrate Shortline Summary Into Fast Review

**Files:**
- Modify: `scripts/run_fast_review_bundle.py`
- Modify: `tests/test_fast_review_daily_bundle.py`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Modify: `docs/architecture/2026-05-02-shortline-hub-single-machine-setup.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: 写 fast review 失败测试**

在 `tests/test_fast_review_daily_bundle.py` 中新增：

```python
def test_fast_review_summary_can_include_shortline_focus_section(tmp_path: Path):
    shortline_summary = tmp_path / "shortline_summary.json"
    shortline_summary.write_text(
        json.dumps(
            {
                "trade_date": "2026-05-03",
                "top_pick_count": 1,
                "watchlist_count": 1,
                "high_risk_mover_count": 0,
                "top_symbols": ["300083 创世纪"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary_md = tmp_path / "fast_review_summary.md"
    append_shortline_focus_section(summary_md=summary_md, shortline_summary_path=shortline_summary)
    summary_md = tmp_path / "fast_review_summary.md"
    assert "短线观察" in summary_md.read_text(encoding="utf-8")
    assert "创世纪" in summary_md.read_text(encoding="utf-8")
```

- [ ] **Step 2: 运行测试确认失败**

Run:

```bash
python -m pytest tests/test_fast_review_daily_bundle.py -k "shortline_focus_section" -q
```

Expected:

- missing section assertion fail

- [ ] **Step 3: 读取短线快照或最近 manual run 摘要**

在 `scripts/run_fast_review_bundle.py` 中增加轻量读取逻辑：

- 优先读同日 `shortline_*` snapshot
- 若无 snapshot，再尝试读最近 `data/manual_runs/shortline_*` 的 `run_summary.json` / `shortline_report.md`
- fail-open，不阻断 fast review 主流程

- [ ] **Step 4: 重跑 fast review 测试**

Run:

```bash
python -m pytest tests/test_fast_review_daily_bundle.py -k "shortline_focus_section" -q
```

Expected:

- test passes

- [ ] **Step 5: 完整回归与实跑验证**

Run:

```bash
python -m pytest tests/test_shortline_hub_tracking.py tests/test_shortline_watchlist_loader.py tests/test_shortline_explain_cache.py tests/test_shortline_hub_orchestrator.py tests/test_shortline_daily_review_layers.py tests/test_shortline_hub_cli.py tests/test_signal_snapshot_service.py tests/test_signal_snapshot_api.py tests/test_fast_review_daily_bundle.py -q
```

Expected:

- all selected suites pass

Run:

```bash
powershell -ExecutionPolicy Bypass -File .\scripts\run-shortline-daily.ps1 -TradeDate 2026-05-03 -TopN 3 -RunId shortline_daily_v1_ready_20260503 -OutputDir data/manual_runs/shortline_daily_v1_ready_20260503 -LogLevel INFO
```

Expected:

- daily run completes
- report contains direction/tracking summary
- optional `--persist-snapshot` path can be replayed for `/signals` query verification

## Acceptance Checklist

- [ ] `shortline_hub` 支持 `manual_watchlist` 批量解释入口
- [ ] 同日 explain 支持缓存复用
- [ ] `shortline_report.md` 支持主方向摘要与历史跟踪摘要
- [ ] 短线结果可落 `kline_signal_snapshot`
- [ ] `/api/v1/signals` 可查询短线结果
- [ ] fast review 可读到短线观察摘要
- [ ] 所有新增功能保持 fail-open，不拖垮现有 daily 链路

## Notes

- 本计划故意不包含 `git commit` 步骤；当前仓库规则要求未经明确确认不得提交。
- 若 `Phase 2` 中 `/signals` schema 扩展比预期更大，应优先保持接口兼容，采用追加字段而不是改旧字段语义。
- 若时间只够完成一半，优先级严格按 `Task 1 -> Task 2 -> Task 3 -> Task 4 -> Task 5 -> Task 6` 执行。
