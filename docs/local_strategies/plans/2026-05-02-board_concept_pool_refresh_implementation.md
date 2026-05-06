# Board Concept Pool Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `board_cycle_scan` 增加一层可复用的“题材板块池本地缓存刷新”能力，基于 `Tushare THS/DC` 维护本地板块池清单，并默认按 3 天有效期复用。

**Architecture:** 将“板块池刷新”与“板块扫描”拆开。`data_provider/tushare_fetcher.py` 提供轻量公共接口负责获取 THS/DC 板块列表；`scripts/refresh_board_concept_pool.py` 负责缓存判定、刷新、落盘与 fail-open；`board_cycle_scan` 后续只消费本地板块池文件，不在扫描主链路里再直连远端板块池接口。

**Tech Stack:** Python、pandas、pytest、现有 `TushareFetcher` / `get_config`、本地 CSV/JSON 文件缓存。

---

## File Map

- Create: `scripts/refresh_board_concept_pool.py`
  - 板块池刷新入口，负责 CLI、缓存过期判断、CSV/metadata 写入与 fail-open 输出。
- Create: `tests/test_refresh_board_concept_pool.py`
  - 覆盖首次刷新、未过期跳过、过期刷新、刷新失败保留旧缓存等回归。
- Modify: `data_provider/tushare_fetcher.py`
  - 新增公共方法，统一拉取 `THS/DC` 板块池清单。
- Modify: `docs/local_strategies/topics/board_cycle_scan.md`
  - 补充“板块池来源 / 刷新脚本 / 3 天缓存”的说明。
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
  - 登记新的工具脚本与 `board_cycle_scan` 的板块池依赖关系。
- Modify: `docs/AI_MODIFICATION_LOG.md`
  - 记录实现过程、验证命令、smoke 产物路径。
- Modify: `docs/CHANGELOG.md`
  - 在 `[Unreleased]` 追加一条扁平记录。

## Implementation Notes

- 不新增数据库、不接 `/signals`、不接每日调度。
- 首版只缓存“板块池清单”，不缓存每个板块的成分股。
- 默认输出目录固定为 `data/board_concept_pool/`，包含：
  - `board_concept_pool.csv`
  - `board_concept_pool_meta.json`
- 默认 `expire_after_days=3`。
- fail-open 规则固定为：
  - 无缓存且首次刷新失败：返回非 0，直接失败。
  - 有缓存但已过期且刷新失败：保留旧缓存，metadata 标记 `refresh_status=failed`，脚本仍返回 0。
  - 有缓存且未过期：默认跳过刷新，直接返回 0。

### Task 1: 先写缓存刷新测试并确认失败

**Files:**
- Create: `tests/test_refresh_board_concept_pool.py`
- Verify: `tests/test_refresh_board_concept_pool.py`

- [ ] **Step 1: 写“首次无缓存时刷新成功”失败测试**

```python
import json
from pathlib import Path

import pandas as pd

import scripts.refresh_board_concept_pool as module


class _FakeFetcher:
    def get_board_concept_pool(self, source: str = "auto") -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"board_name": "锂矿", "board_code": "BK001", "board_source": "ths"},
                {"board_name": "猪肉", "board_code": "BK002", "board_source": "ths"},
            ]
        )


def test_refresh_writes_csv_and_meta_on_first_success(tmp_path: Path) -> None:
    output_dir = tmp_path / "board_pool"

    result = module.refresh_board_concept_pool(
        output_dir=output_dir,
        source="auto",
        expire_after_days=3,
        force_refresh=False,
        fetcher=_FakeFetcher(),
    )

    assert result["status"] == "refreshed"
    assert (output_dir / "board_concept_pool.csv").exists()
    assert (output_dir / "board_concept_pool_meta.json").exists()

    exported_df = pd.read_csv(output_dir / "board_concept_pool.csv")
    assert exported_df["board_name"].tolist() == ["锂矿", "猪肉"]

    meta = json.loads((output_dir / "board_concept_pool_meta.json").read_text(encoding="utf-8"))
    assert meta["refresh_status"] == "success"
    assert meta["expire_after_days"] == 3
    assert meta["board_count"] == 2
```

- [ ] **Step 2: 写“缓存未过期时跳过刷新”失败测试**

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import scripts.refresh_board_concept_pool as module


class _ExplodingFetcher:
    def get_board_concept_pool(self, source: str = "auto") -> pd.DataFrame:
        raise AssertionError("should not refresh when cache is still fresh")


def test_refresh_skips_when_cache_is_fresh(tmp_path: Path) -> None:
    output_dir = tmp_path / "board_pool"
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"board_name": "锂矿", "board_code": "BK001", "board_source": "ths"}]).to_csv(
        output_dir / "board_concept_pool.csv",
        index=False,
        encoding="utf-8-sig",
    )
    meta = {
        "source": "ths",
        "last_updated_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
        "expire_after_days": 3,
        "refresh_status": "success",
        "board_count": 1,
    }
    (output_dir / "board_concept_pool_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = module.refresh_board_concept_pool(
        output_dir=output_dir,
        source="auto",
        expire_after_days=3,
        force_refresh=False,
        fetcher=_ExplodingFetcher(),
    )

    assert result["status"] == "skipped"
    exported_df = pd.read_csv(output_dir / "board_concept_pool.csv")
    assert exported_df["board_name"].tolist() == ["锂矿"]
```

- [ ] **Step 3: 写“缓存过期但刷新失败时保留旧缓存”失败测试**

```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import scripts.refresh_board_concept_pool as module


class _FailingFetcher:
    def get_board_concept_pool(self, source: str = "auto") -> pd.DataFrame:
        raise RuntimeError("remote failed")


def test_refresh_keeps_stale_cache_when_remote_refresh_fails(tmp_path: Path) -> None:
    output_dir = tmp_path / "board_pool"
    output_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"board_name": "猪肉", "board_code": "BK002", "board_source": "ths"}]).to_csv(
        output_dir / "board_concept_pool.csv",
        index=False,
        encoding="utf-8-sig",
    )
    meta = {
        "source": "ths",
        "last_updated_at": (datetime.now(timezone.utc) - timedelta(days=5)).isoformat(),
        "expire_after_days": 3,
        "refresh_status": "success",
        "board_count": 1,
    }
    (output_dir / "board_concept_pool_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    result = module.refresh_board_concept_pool(
        output_dir=output_dir,
        source="auto",
        expire_after_days=3,
        force_refresh=False,
        fetcher=_FailingFetcher(),
    )

    assert result["status"] == "stale_cache_retained"
    exported_df = pd.read_csv(output_dir / "board_concept_pool.csv")
    assert exported_df["board_name"].tolist() == ["猪肉"]

    failed_meta = json.loads((output_dir / "board_concept_pool_meta.json").read_text(encoding="utf-8"))
    assert failed_meta["refresh_status"] == "failed"
```

- [ ] **Step 4: 跑测试确认先红**

Run:

```bash
python -m pytest tests/test_refresh_board_concept_pool.py -v
```

Expected:

- 失败原因应为 `scripts.refresh_board_concept_pool` 不存在或缺少 `refresh_board_concept_pool(...)`。
- 不应是测试文件自身语法错误。

### Task 2: 给 TushareFetcher 增加板块池公共接口

**Files:**
- Modify: `data_provider/tushare_fetcher.py`
- Verify: `tests/test_refresh_board_concept_pool.py`

- [ ] **Step 1: 先补一个接口级失败测试**

```python
import pandas as pd

from data_provider.tushare_fetcher import TushareFetcher


def test_get_board_concept_pool_normalizes_ths_rows(monkeypatch) -> None:
    fetcher = TushareFetcher()
    sample = pd.DataFrame(
        [
            {"industry": "锂矿", "ts_code": "BK001", "exchange": "A"},
            {"industry": "猪肉", "ts_code": "BK002", "exchange": "A"},
        ]
    )
    monkeypatch.setattr(fetcher, "get_trade_time", lambda early_time="00:00", late_time="15:30": "20260502")
    monkeypatch.setattr(fetcher, "_call_api_with_rate_limit", lambda api_name, **kwargs: sample.copy())

    result = fetcher.get_board_concept_pool(source="ths")

    assert list(result.columns) == ["board_name", "board_code", "board_source"]
    assert result["board_name"].tolist() == ["锂矿", "猪肉"]
    assert result["board_source"].tolist() == ["ths", "ths"]
```

- [ ] **Step 2: 实现最小公共方法**

```python
def get_board_concept_pool(self, source: str = "auto") -> pd.DataFrame:
    if self._api is None:
        raise DataFetchError("Tushare API 未初始化")

    resolved_source = str(source or "auto").strip().lower()
    if resolved_source not in {"auto", "ths", "dc"}:
        raise ValueError(f"unsupported board concept pool source: {source}")

    trade_date = self.get_trade_time(early_time="00:00", late_time="15:30")
    if not trade_date:
        raise DataFetchError("unable to resolve trade date for board concept pool")

    errors: list[str] = []
    api_candidates = ["moneyflow_ind_ths", "moneyflow_ind_dc"] if resolved_source == "auto" else [
        "moneyflow_ind_ths" if resolved_source == "ths" else "moneyflow_ind_dc"
    ]

    for api_name in api_candidates:
        try:
            df = self._call_api_with_rate_limit(api_name, trade_date=trade_date)
        except Exception as exc:
            errors.append(f"{api_name}:{exc}")
            continue
        normalized = self._normalize_board_concept_pool_frame(df, api_name=api_name)
        if normalized is not None and not normalized.empty:
            return normalized
        errors.append(f"{api_name}:empty")

    raise DataFetchError(" ; ".join(errors) or "board concept pool fetch failed")
```

- [ ] **Step 3: 实现标准化 helper**

```python
def _normalize_board_concept_pool_frame(self, df: Optional[pd.DataFrame], *, api_name: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["board_name", "board_code", "board_source"])

    work = df.copy()
    if api_name == "moneyflow_ind_ths":
        name_col = "industry"
        code_col = "ts_code" if "ts_code" in work.columns else None
        source_name = "ths"
    else:
        if "content_type" in work.columns:
            work = work[work["content_type"].astype(str) != ""].copy()
        name_col = "name"
        code_col = "ts_code" if "ts_code" in work.columns else None
        source_name = "dc"

    if name_col not in work.columns:
        return pd.DataFrame(columns=["board_name", "board_code", "board_source"])

    work["board_name"] = work[name_col].astype(str).str.strip()
    work = work[work["board_name"] != ""].copy()
    work["board_code"] = (
        work[code_col].astype(str).str.strip() if code_col and code_col in work.columns else work["board_name"]
    )
    work["board_source"] = source_name
    work = work[["board_name", "board_code", "board_source"]].drop_duplicates(subset=["board_name"], keep="first")
    return work.reset_index(drop=True)
```

- [ ] **Step 4: 跑定向测试转绿**

Run:

```bash
python -m pytest tests/test_refresh_board_concept_pool.py -k "normalizes_ths_rows" -v
```

Expected:

- `get_board_concept_pool(...)` 相关测试通过。

### Task 3: 实现刷新脚本与缓存判定

**Files:**
- Create: `scripts/refresh_board_concept_pool.py`
- Verify: `tests/test_refresh_board_concept_pool.py`

- [ ] **Step 1: 实现路径和 metadata helper**

```python
def get_cache_paths(output_dir: Path) -> dict[str, Path]:
    return {
        "csv": output_dir / "board_concept_pool.csv",
        "meta": output_dir / "board_concept_pool_meta.json",
        "summary": output_dir / "run_summary.txt",
    }


def read_meta(meta_path: Path) -> dict:
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
```

- [ ] **Step 2: 实现过期判断与主刷新函数**

```python
def is_cache_expired(meta: dict, *, expire_after_days: int, now: datetime) -> bool:
    last_updated_at = str(meta.get("last_updated_at") or "").strip()
    if not last_updated_at:
        return True
    try:
        updated_at = datetime.fromisoformat(last_updated_at)
    except Exception:
        return True
    return now - updated_at > timedelta(days=max(1, int(expire_after_days)))


def refresh_board_concept_pool(
    *,
    output_dir: Path,
    source: str,
    expire_after_days: int,
    force_refresh: bool,
    fetcher: object,
) -> dict:
    now = datetime.now(timezone.utc)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = get_cache_paths(output_dir)
    meta = read_meta(paths["meta"])
    csv_exists = paths["csv"].exists()

    if csv_exists and not force_refresh and not is_cache_expired(meta, expire_after_days=expire_after_days, now=now):
        return {"status": "skipped", "paths": paths}

    try:
        df = fetcher.get_board_concept_pool(source=source)
        if df is None or df.empty:
            raise RuntimeError("board concept pool is empty")
        df = df[["board_name", "board_code", "board_source"]].drop_duplicates(subset=["board_name"], keep="first")
        df.to_csv(paths["csv"], index=False, encoding="utf-8-sig")
        next_meta = {
            "source": source,
            "last_updated_at": now.isoformat(),
            "expire_after_days": int(expire_after_days),
            "refresh_status": "success",
            "board_count": int(len(df)),
        }
        paths["meta"].write_text(json.dumps(next_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"status": "refreshed", "paths": paths, "board_count": len(df)}
    except Exception as exc:
        if csv_exists:
            fallback_meta = {
                **meta,
                "source": meta.get("source") or source,
                "expire_after_days": int(expire_after_days),
                "refresh_status": "failed",
                "last_attempted_at": now.isoformat(),
                "notes": str(exc),
            }
            paths["meta"].write_text(json.dumps(fallback_meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return {"status": "stale_cache_retained", "paths": paths, "error": str(exc)}
        raise
```

- [ ] **Step 3: 实现 CLI**

```python
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh local board concept pool cache from Tushare THS/DC.")
    parser.add_argument("--source", default="auto", choices=["auto", "ths", "dc"])
    parser.add_argument("--expire-after-days", type=int, default=3)
    parser.add_argument("--force-refresh", action="store_true")
    parser.add_argument("--output-dir", default="data/board_concept_pool")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fetcher = TushareFetcher()
    result = refresh_board_concept_pool(
        output_dir=Path(args.output_dir),
        source=args.source,
        expire_after_days=args.expire_after_days,
        force_refresh=bool(args.force_refresh),
        fetcher=fetcher,
    )
    return 0
```

- [ ] **Step 4: 跑整组测试转绿**

Run:

```bash
python -m pytest tests/test_refresh_board_concept_pool.py -v
```

Expected:

- 首次刷新、跳过刷新、保留旧缓存这三类行为全部通过。

### Task 4: 补文档留痕并做一次本地验证

**Files:**
- Modify: `docs/local_strategies/topics/board_cycle_scan.md`
- Modify: `docs/LOCAL_STRATEGY_CATALOG.md`
- Modify: `docs/AI_MODIFICATION_LOG.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: 补充 `board_cycle_scan` 题材池来源说明**

```markdown
## 板块池刷新

当前建议先独立维护本地题材板块池：

```powershell
python scripts/refresh_board_concept_pool.py --source auto --expire-after-days 3
```

默认输出：

- `data/board_concept_pool/board_concept_pool.csv`
- `data/board_concept_pool/board_concept_pool_meta.json`

设计原则：

- 优先复用本地缓存
- 默认 3 天刷新一次
- 远端刷新失败时保留旧缓存，不阻断后续复盘
```

- [ ] **Step 2: 在目录与变更记录中登记**

```markdown
- [改进] 新增 `scripts/refresh_board_concept_pool.py`，基于 `Tushare THS/DC` 维护本地题材板块池缓存，默认 3 天有效期；若远端刷新失败且本地已有旧缓存，则保持 fail-open 继续复用旧板块池。
```

- [ ] **Step 3: 运行最小验证**

Run:

```bash
python -m py_compile scripts/refresh_board_concept_pool.py data_provider/tushare_fetcher.py
```

Expected:

- 无语法错误。

Run:

```bash
python -m pytest tests/test_refresh_board_concept_pool.py -v
```

Expected:

- 全绿。

Run:

```bash
python scripts/refresh_board_concept_pool.py --source auto --expire-after-days 3 --output-dir data/manual_runs/board_concept_pool_refresh_verify_20260502
```

Expected:

- 生成：
  - `board_concept_pool.csv`
  - `board_concept_pool_meta.json`
- 若远端失败但目录已有旧缓存，则 metadata 标明 `refresh_status=failed`，脚本仍返回成功。

- [ ] **Step 4: 把验证证据写入 `docs/AI_MODIFICATION_LOG.md`**

```markdown
## 2026-05-02 (board concept pool refresh cache)

- Scope: 为 `board_cycle_scan` 增加独立的题材板块池本地缓存刷新层。
- Verification:
  - `python -m pytest tests/test_refresh_board_concept_pool.py -v`
  - `python -m py_compile scripts/refresh_board_concept_pool.py data_provider/tushare_fetcher.py`
  - `python scripts/refresh_board_concept_pool.py --source auto --expire-after-days 3 --output-dir data/manual_runs/board_concept_pool_refresh_verify_20260502`
```

## Self-Review

- Spec coverage:
  - `THS/DC` 题材板块池来源：Task 2 覆盖。
  - 本地 CSV + metadata 缓存：Task 3 覆盖。
  - 3 天默认过期：Task 1 / Task 3 覆盖。
  - fail-open 复用旧缓存：Task 1 / Task 3 覆盖。
  - 文档与留痕：Task 4 覆盖。
- Placeholder scan:
  - 无 `TODO/TBD/implement later`。
  - 文件路径、测试文件、命令均已具体化。
- Type consistency:
  - `get_board_concept_pool(...)`、`refresh_board_concept_pool(...)`、`board_concept_pool.csv`、`board_concept_pool_meta.json` 命名保持一致。
