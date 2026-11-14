# -*- coding: utf-8 -*-
"""A1-a 只读总览投影（today_board）。

把**已经存在的产物**投影成一张 Markdown：不建表、不接 API、不调用模型、
不启动生产者。数据只来自三处：

* ``kline_signal_snapshot``（已有信号快照）
* ``data/screening_snapshots/<日期>/*.json``（筛选采集审计）
* ``data/run_logs/*.log`` 与 ``rebuild_stock_daily_qfq_failed_codes.txt``
  （既有编排的完成/失败证据）

设计约束（见 docs/个人策略文档/个人投研工作台重构方案.md）：

1. 对象闸门：运行摘要（``TL_SUMMARY`` / ``is_run_summary``）不得当证券。
2. 零人工输入：所有内容从上述产物推出，无手填项。
3. 降级可解释：条目的降级/回退/重建失败必须显式标注，不能静默丢弃。
4. 未接入的分区显示为缺口，不显示成"无机会"。
5. 同一日期重复渲染结果一致（不含墙钟时间），便于回归与比较。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from src.services.personal_strategy_matrix_service import PROJECT_ROOT
from src.services.market_instruments import is_tradable_stock
from src.services.quote_quality import load_suspicious_codes as _shared_load_suspicious_codes

DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data" / "strategy_review" / "today_board.md"
SCREENING_SNAPSHOT_ROOT = PROJECT_ROOT / "data" / "screening_snapshots"
RUN_LOG_ROOT = PROJECT_ROOT / "data" / "run_logs"
REBUILD_FAILURE_PATH = RUN_LOG_ROOT / "rebuild_stock_daily_qfq_failed_codes.txt"
SUSPICIOUS_CODES_PATH = RUN_LOG_ROOT / "rebuild_stock_daily_qfq_suspicious_codes.txt"
ETF_FLOW_LOG_ROOT = PROJECT_ROOT / "data" / "strategy_review" / "etf_flow"

# DB 中部分表（如 screening_runs.created_at）以无时区 UTC 存储；
# 展示统一按北京时间，禁止把它直接与本地日期比较。
LOCAL_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

NON_SECURITY_CODES = frozenset({"TL_SUMMARY", "SUMMARY", "ALL"})
SECURITY_CODE_RE = re.compile(r"^\d{6}$")
# 完成行：Python 脚本写 "done ... exit=0"，shell 包装器写 "结束 rc=0"。
DONE_LINE_RE = re.compile(r"\]\s+done\b|=====\s*done\b|结束\s*rc=", re.IGNORECASE)
START_LINE_RE = re.compile(r"\]\s+start\b|=====\s*start\b", re.IGNORECASE)
EXIT_RE = re.compile(r"(?:exit|rc)=(-?\d+)")
DATETIME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
REBUILD_RATE_RE = re.compile(r"失败率过高:\s*(\d+)/(\d+)\s*=\s*([\d.]+)%")
CANDIDATE_PREVIEW_LIMIT = 20

# 审计里的纯信息项，不算降级（否则每条都会变“降级”）。
BENIGN_DEGRADATION_MARKERS = ("DSA provider context applied",)
# 行情来源里真正需要提醒的回退标记；disk_cache 本身是正常读路径。
CACHE_FALLBACK_MARKERS = ("stale", "partial", "last_good")

# 现有编排：key -> (标签, 日志相对路径模板, 是否日频)
SOURCE_TASKS: Tuple[Tuple[str, str, str, bool], ...] = (
    ("screening", "筛选预设（三批）", "screening_snapshots.log", True),
    ("warm", "补数/预热", "warm_daily.log", True),
    ("structure", "结构扫描", "structure_scan_daily.log", True),
    ("matrix", "个人策略矩阵", "matrix_daily.log", True),
    ("etf", "ETF 份额周报", "{etf}/run-{date}.log", False),
)


def utc_naive_to_local(value: Optional[datetime]) -> Optional[datetime]:
    """无时区 UTC 时间 -> 北京时间；已有 tzinfo 的原样换算。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
    return value.astimezone(LOCAL_TZ)


def _format_generated_at(value: datetime) -> str:
    """生成时间：无时区视为本地墙钟，不做 +8 平移；带时区的统一转北京时间。"""
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")


def is_security_object(code: str, metrics: Optional[Dict[str, Any]] = None) -> bool:
    """对象闸门：只允许真正的证券对象进入候选。"""
    metrics = metrics or {}
    if not code or code.strip().upper() in NON_SECURITY_CODES:
        return False
    if not SECURITY_CODE_RE.match(code.strip()):
        return False
    if metrics.get("is_run_summary") is True:
        return False
    asset_type = str(metrics.get("asset_type") or "stock").lower()
    return asset_type in {"stock", "etf"}


@dataclass(frozen=True)
class Candidate:
    source: str
    code: str
    name: str
    reason: str
    flags: Tuple[str, ...] = ()
    source_ref: str = ""
    source_version: str = ""
    batch_ref: str = ""
    available_at: Optional[datetime] = None
    observed_at: Optional[datetime] = None
    quality: str = "limited"
    limitation: str = ""
    batch_ready: bool = False
    source_snapshot: Dict[str, Any] = field(default_factory=dict)

    @property
    def flag_text(self) -> str:
        return "、".join(self.flags) if self.flags else "-"


@dataclass(frozen=True)
class Diagnostic:
    source: str
    code: str
    name: str
    detail: str


@dataclass(frozen=True)
class NewsItem:
    """窗口内的资讯条目（news_intel / intelligence_items）。"""

    published: str
    source: str
    title: str
    scope: str = "-"


@dataclass(frozen=True)
class DueReview:
    """到期复核项：来自 research_tracks 的 next_review_at。"""

    track_id: str
    code: str
    title: str
    branch: str
    next_review_at: str
    overdue_days: int = 0

    @property
    def status_text(self) -> str:
        return f"逾期 {self.overdue_days} 天" if self.overdue_days > 0 else "到期"


@dataclass(frozen=True)
class SourceHealth:
    key: str
    label: str
    status: str
    detail: str
    last_done_local: Optional[str] = None
    exit_code: Optional[int] = None
    last_start_local: Optional[str] = None
    batch_ref: str = ""

    @property
    def status_text(self) -> str:
        return {
            "ok": "正常",
            "degraded": "降级",
            "failed": "失败",
            "running": "进行中",
            "stale": "陈旧",
            "missing": "无完成记录",
        }.get(self.status, self.status)


@dataclass
class Board:
    board_date: str
    market_regime: str
    candidates: List[Candidate] = field(default_factory=list)
    diagnostics: List[Diagnostic] = field(default_factory=list)
    health: List[SourceHealth] = field(default_factory=list)
    due_reviews: List[DueReview] = field(default_factory=list)
    due_review_error: str = ""
    news: List[NewsItem] = field(default_factory=list)
    news_note: str = ""
    rebuild_failure_count: int = 0
    rebuild_failure_rate: Optional[str] = None
    screening_degradations: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# 采集（IO）
# --------------------------------------------------------------------------- #


def load_signal_rows(board_date: str, db_manager: Any = None) -> List[Any]:
    """读取指定信号日的全部快照记录（按来源、代码排序）。"""
    from src.storage import DatabaseManager, KlineSignalSnapshot

    db = db_manager or DatabaseManager.get_instance()
    target = date.fromisoformat(board_date)
    session = db.get_session()
    try:
        return list(
            session.query(KlineSignalSnapshot)
            .filter(KlineSignalSnapshot.signal_date == target)
            .order_by(KlineSignalSnapshot.signal_type, KlineSignalSnapshot.code)
            .all()
        )
    finally:
        session.close()


def _parse_json(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load_screening_audits(board_date: str, root: Path = SCREENING_SNAPSHOT_ROOT) -> List[Dict[str, Any]]:
    """读取筛选采集审计 JSON（缺失即空列表，不报错）。"""
    day_dir = root / board_date
    if not day_dir.is_dir():
        return []
    audits: List[Dict[str, Any]] = []
    for path in sorted(day_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            audits.append({"strategy": path.stem, "read_error": True})
            continue
        if isinstance(data, dict):
            data.setdefault("strategy", path.stem)
            audits.append(data)
    return audits


def _tail_lines(path: Path, max_bytes: int = 20000) -> List[str]:
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - max_bytes))
            raw = handle.read().decode("utf-8", errors="ignore")
    except OSError:
        return []
    return raw.splitlines()


def read_task_health(key: str, label: str, log_path: Path, board_date: str, daily: bool) -> SourceHealth:
    """从日志尾部推导一次任务的完成状态（只读，不推断未写入的事实）。"""
    owner = {"matrix": "matrix_daily", "screening": "screening", "warm": "warm_daily"}.get(key)
    # 长批次的 start 可能远超 20KB 日志尾；流式保留边界，避免把真实完成判为未知。
    boundaries: deque[str] = deque(maxlen=32)
    try:
        with log_path.open(encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                if owner and not line.startswith(f"[{owner}]"):
                    continue
                if key == "structure" and not line.startswith("====="):
                    continue
                if START_LINE_RE.search(line) or DONE_LINE_RE.search(line):
                    boundaries.append(line.rstrip())
    except OSError:
        return SourceHealth(key, label, "missing", "未找到或无法读取日志文件")
    lines = list(boundaries)
    if not lines:
        return SourceHealth(key, label, "missing", "未找到日志文件")

    last_start = max((i for i, line in enumerate(lines) if START_LINE_RE.search(line)), default=-1)
    last_done = max((i for i, line in enumerate(lines) if DONE_LINE_RE.search(line)), default=-1)
    if last_start > last_done:
        return SourceHealth(key, label, "running", "已 start、未见 done（批次未完成）")
    if last_done < 0:
        return SourceHealth(key, label, "missing", "日志无 done 记录")
    done_line = lines[last_done]

    exit_match = EXIT_RE.search(done_line)
    exit_code = int(exit_match.group(1)) if exit_match else None
    stamp_match = DATETIME_RE.search(done_line)
    done_local = None
    if stamp_match:
        done_local = f"{stamp_match.group(1)} {stamp_match.group(2)}"

    start_match = DATETIME_RE.search(lines[last_start]) if last_start >= 0 else None
    start_local = f"{start_match.group(1)} {start_match.group(2)}" if start_match else None
    batch_ref = f"{key}:{hashlib.sha256(done_line.encode()).hexdigest()}"

    if exit_code not in (None, 0):
        return SourceHealth(key, label, "failed", f"done 返回 exit={exit_code}", done_local, exit_code)
    if daily and done_local and done_local[:10] != board_date:
        return SourceHealth(key, label, "stale", f"最近完成于 {done_local}，非目标日", done_local, exit_code)
    snapshot = re.search(r"snapshot=(\d{4}-\d{2}-\d{2})", done_line)
    if snapshot and snapshot.group(1) != board_date:
        return SourceHealth(key, label, "stale", "完成记录属于其他目标交易日", done_local, exit_code)
    if exit_code is None or done_local is None or (daily and (start_local is None or start_local > done_local)):
        gaps: List[str] = []
        if exit_code is None:
            gaps.append("退出码（done 行需带 exit=<rc>）")
        if done_local is None:
            gaps.append("完成时间")
        if daily and start_local is None:
            gaps.append("对应 start")
        elif daily and start_local > done_local:
            gaps.append("start 晚于 done")
        return SourceHealth(key, label, "degraded", "完成证据缺 " + "、".join(gaps), done_local, exit_code)
    return SourceHealth(key, label, "ok", "done 记录完好", done_local, exit_code, start_local, batch_ref)


def read_rebuild_failures(path: Path = REBUILD_FAILURE_PATH) -> Tuple[Set[str], int, Optional[str]]:
    """读取行情重建失败清单，并尝试从矩阵日志取失败率。"""
    codes: Set[str] = set()
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                token = line.strip().split()[0] if line.strip() else ""
                if token[:6].isdigit():
                    codes.add(token[:6])
        except OSError:
            pass

    rate_text = None
    for line in _tail_lines(RUN_LOG_ROOT / "matrix_daily.log"):
        match = REBUILD_RATE_RE.search(line)
        if match:
            rate_text = f"{match.group(1)}/{match.group(2)} = {match.group(3)}%"
    return codes, len(codes), rate_text


def load_last_bar_dates(codes: Sequence[str], db_manager: Any = None) -> Dict[str, str]:
    """一次查询取回候选票在 stock_daily 的最后行情日（ISO 字符串）。"""
    normalized = sorted({str(c).strip() for c in codes if str(c).strip()})
    if not normalized:
        return {}
    from sqlalchemy import text

    from src.storage import DatabaseManager

    db = db_manager or DatabaseManager.get_instance()
    placeholders = ",".join(f":c{i}" for i in range(len(normalized)))
    params = {f"c{i}": code for i, code in enumerate(normalized)}
    query = text(
        f"SELECT code, MAX(date) AS last_date FROM stock_daily "
        f"WHERE code IN ({placeholders}) GROUP BY code"
    )
    with db.session_scope() as session:
        rows = session.execute(query, params).fetchall()
    return {str(row[0]).strip(): str(row[1])[:10] for row in rows if row[0]}


def load_due_reviews(board_date: str, db_manager: Any = None) -> List[DueReview]:
    """读取到期研究项（next_review_at <= 当日 23:59:59 且状态 tracking）。"""
    from src.repositories.research_tracking_repo import ResearchTrackingRepository
    from src.services.research_tracking_service import ResearchTrackingService

    repo = ResearchTrackingRepository(db_manager) if db_manager is not None else ResearchTrackingRepository()
    service = ResearchTrackingService(repo)
    day = date.fromisoformat(board_date)
    moment = datetime.combine(day, datetime.max.time())
    tracks = service.due_for_review(as_of=moment, limit=100)
    reviews: List[DueReview] = []
    for track in tracks:
        review_at = track.next_review_at
        review_day = review_at.date() if review_at else day
        reviews.append(
            DueReview(
                track_id=track.track_id,
                code=str(track.code),
                title=str(track.title or track.hypothesis_key),
                branch=str(track.branch or "-"),
                next_review_at=review_day.isoformat(),
                overdue_days=max(0, (day - review_day).days),
            )
        )
    reviews.sort(key=lambda item: (item.overdue_days == 0, item.next_review_at, item.code))
    return reviews


def load_recent_news(
    board_date: str,
    *,
    days: int = 3,
    limit: int = 10,
    db_manager: Any = None,
) -> Tuple[List[NewsItem], str]:
    """读窗口内的资讯记录；返回 (条目, 口径说明)。

    只读现有表，不做采集。两表都可能为空或不存在：
    此时返回空列表 + 可审计的条数说明，交给渲染层显示为“未接入”而不是“无变化”。
    """
    from sqlalchemy import text

    from src.storage import DatabaseManager

    db = db_manager or DatabaseManager.get_instance()
    day = date.fromisoformat(board_date)
    start = (day - timedelta(days=max(0, int(days)))).isoformat()

    items: List[NewsItem] = []
    counts: Dict[str, Any] = {}
    errors: List[str] = []

    queries = (
        (
            "news_intel",
            "SELECT code, title, source, published_date FROM news_intel "
            "WHERE published_date >= :start ORDER BY published_date DESC LIMIT :lim",
        ),
        (
            "intelligence_items",
            "SELECT scope_value, title, source_name, published_at FROM intelligence_items "
            "WHERE published_at >= :start ORDER BY published_at DESC LIMIT :lim",
        ),
    )
    for table, sql in queries:
        try:
            with db.session_scope() as session:
                total = session.execute(text(f"SELECT count(*) FROM {table}")).scalar()
                rows = session.execute(
                    text(sql), {"start": start, "lim": max(0, int(limit))}
                ).fetchall()
        except Exception as exc:  # noqa: BLE001 - 表缺失/格式差异不能阻断总览
            errors.append(f"{table}: {type(exc).__name__}")
            continue
        counts[table] = int(total or 0)
        for row in rows:
            published = str(row[3] or "")[:16]
            items.append(
                NewsItem(
                    published=published,
                    source=str(row[2] or "-"),
                    title=str(row[1] or "").strip()[:120],
                    scope=str(row[0] or "-"),
                )
            )

    items.sort(key=lambda item: item.published, reverse=True)
    items = items[: max(0, int(limit))]

    if errors:
        note = "资讯表不可用：" + "、".join(errors) + "（不代表无变化）"
    else:
        count_text = " / ".join(f"{name} {value} 条" for name, value in counts.items())
        note = (
            f"窗口 {start}~{board_date} 内 {len(items)} 条（全表：{count_text}）"
            if items
            else f"窗口 {start}~{board_date} 内无资讯记录（全表：{count_text}）—— 属采集未接入，不代表无变化"
        )
    return items, note


def load_suspicious_codes(path: Path = SUSPICIOUS_CODES_PATH) -> Set[str]:
    """重建后扫描发现的不可能跳变代码；命中则不用于判断。

    实现在 `src.services.quote_quality`（与重建脚本、结果回填共用一份）。
    """
    return _shared_load_suspicious_codes(path)


def build_health(board_date: str) -> List[SourceHealth]:
    health: List[SourceHealth] = []
    for key, label, template, daily in SOURCE_TASKS:
        if key == "etf":
            log_path = ETF_FLOW_LOG_ROOT / f"run-{board_date}.log"
        else:
            log_path = RUN_LOG_ROOT / template
        health.append(read_task_health(key, label, log_path, board_date, daily))
    return health


# --------------------------------------------------------------------------- #
# 组装（纯函数）
# --------------------------------------------------------------------------- #


def _reason_for_hundred_day_high(metrics: Dict[str, Any]) -> str:
    close = metrics.get("close")
    window_high = metrics.get("window_high")
    window = metrics.get("new_high_window")
    source = metrics.get("history_source")
    parts = []
    if close is not None and window_high is not None:
        parts.append(f"收盘 {close} / 窗口高 {window_high}")
    if window:
        parts.append(f"{window} 日新高")
    if source:
        parts.append(f"行情 {source}")
    return "；".join(parts) or "百日新高命中"


def _reason_for_screening(metrics: Dict[str, Any]) -> str:
    rank = metrics.get("rank")
    score = metrics.get("score")
    reason = metrics.get("reason") or ""
    risk = metrics.get("risk_level") or ""
    industry = metrics.get("industry") or ""
    parts = []
    if rank is not None:
        parts.append(f"rank {rank}")
    if isinstance(score, (int, float)):
        parts.append(f"score {score:.2f}")
    if reason:
        parts.append(str(reason))
    if industry:
        parts.append(str(industry))
    if risk:
        parts.append(f"风险 {risk}")
    return "｜".join(parts) or "筛选命中"


def _candidate_evidence(
    row: Any, metrics: Dict[str, Any], board_date: str,
    health: Sequence[SourceHealth], audit: Dict[str, Any], peers: Sequence[Any],
) -> Dict[str, Any]:
    """绑定读到的快照版本；未知公开时间保留空，完成证据不足则只读。"""
    source = str(row.signal_type)
    task = "screening" if source.startswith("screening__") else "matrix" if source == "hundred_day_high" else ""
    status = next((h for h in health if h.key == task), None)
    limits = ["该版本公开可得时间未核实，available_at 为空"]
    ready = bool(status and status.status == "ok" and status.batch_ref and status.last_start_local and status.last_done_local)
    stamp = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
    local_stamp = None
    if isinstance(stamp, datetime):
        local_stamp = stamp.astimezone(LOCAL_TZ).replace(tzinfo=None) if stamp.tzinfo else stamp
    if ready:
        start = datetime.fromisoformat(status.last_start_local)
        done = datetime.fromisoformat(status.last_done_local)
        ready = local_stamp is not None and start <= local_stamp <= done + timedelta(seconds=1)
        if source.startswith("screening__"):
            run_id = str(metrics.get("run_id") or "")
            audit_candidates = audit.get("candidates")
            top_n = audit.get("top_n")
            valid_audit = (
                isinstance(audit_candidates, list) and all(isinstance(c, dict) for c in audit_candidates)
                and type(top_n) is int and top_n > 0
            )
            audit_codes = [str(c.get("code") or "") for c in audit_candidates] if valid_audit else []
            expected = set(audit_codes[:top_n]) if valid_audit else set()
            actual = {str(p.code) for p in peers}
            audit_time = str(audit.get("generated_at") or "")
            ready = bool(
                ready and valid_audit and run_id and audit.get("run_id") == run_id
                and audit.get("as_of") == board_date and expected == actual
                and all(_parse_json(p.metrics_payload).get("run_id") == run_id for p in peers)
                and status.last_start_local <= audit_time <= status.last_done_local
                and not audit.get("read_error")
            )
    if not ready:
        limits.append("未匹配该快照的完整成功批次，仅供阅读，不自动入池")
    batch_ref = status.batch_ref if status else ""
    # 公开可得时间：筛选批次有审计生成时间，能证明“该时点已落库可读”；
    # 矩阵信号没有同等证据，继续留空（不拿 15:00 之类固定值充数）。
    available_at = None
    if ready and source.startswith("screening__"):
        audit_time = str(audit.get("generated_at") or "").strip()
        if audit_time:
            try:
                available_at = datetime.fromisoformat(audit_time).replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                available_at = None
    if available_at is not None:
        limits = [item for item in limits if "available_at 为空" not in item]
    content = {
        "source": source, "date": board_date, "code": str(row.code), "name": str(row.name or ""), "batch_ref": batch_ref,
        "metrics": metrics, "criteria": _parse_json(getattr(row, "criteria_payload", None)),
        "updated_at": str(stamp or ""),
    }
    version = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    native_id = metrics.get("run_id") or getattr(row, "id", None) or source
    return {
        "source_ref": f"kline_signal_snapshot:{native_id}:{row.code}:{version}",
        "source_version": version, "batch_ref": batch_ref, "available_at": available_at,
        "source_snapshot": content,
        "observed_at": (
            local_stamp.replace(tzinfo=LOCAL_TZ).astimezone(timezone.utc).replace(tzinfo=None)
            if local_stamp else None
        ),
        "quality": "limited", "limitation": "；".join(limits), "batch_ready": ready,
    }


def build_board(
    board_date: str,
    *,
    signal_rows: Sequence[Any],
    screening_audits: Sequence[Dict[str, Any]],
    health: Sequence[SourceHealth],
    rebuild_failed_codes: Set[str],
    rebuild_failure_rate: Optional[str] = None,
    market_regime: str = "unknown",
    last_bar_dates: Optional[Dict[str, str]] = None,
    suspicious_codes: Optional[Set[str]] = None,
    due_reviews: Optional[Sequence[DueReview]] = None,
    due_review_error: str = "",
    news: Optional[Sequence[NewsItem]] = None,
    news_note: str = "",
) -> Board:
    """把采集结果组装成 Board（不做 IO，便于测试）。"""
    board = Board(
        board_date=board_date,
        market_regime=market_regime,
        health=list(health),
        due_reviews=list(due_reviews or []),
        due_review_error=due_review_error,
        news=list(news or []),
        news_note=news_note,
        rebuild_failure_count=len(rebuild_failed_codes),
        rebuild_failure_rate=rebuild_failure_rate,
    )

    degraded_sources: Set[str] = set()
    audit_by_strategy: Dict[str, Dict[str, Any]] = {}
    for audit in screening_audits:
        strategy = str(audit.get("strategy") or "")
        audit_by_strategy[strategy] = audit
        if audit.get("degradation") or audit.get("warnings") or audit.get("read_error"):
            degraded_sources.add(f"screening__{strategy}")
        for item in audit.get("degradation") or []:
            text = str(item)
            if any(marker in text for marker in BENIGN_DEGRADATION_MARKERS):
                continue
            entry = f"{strategy}: {text}"
            if entry not in board.screening_degradations:
                board.screening_degradations.append(entry)

    seen_codes: Dict[str, List[str]] = {}
    rows_by_source: Dict[str, List[Any]] = {}
    for row in signal_rows:
        rows_by_source.setdefault(str(row.signal_type), []).append(row)
    for row in signal_rows:
        metrics = _parse_json(getattr(row, "metrics_payload", None))
        code = str(getattr(row, "code", "") or "")
        name = str(getattr(row, "name", "") or "")
        source = str(getattr(row, "signal_type", "") or "")

        if not is_security_object(code, metrics):
            board.diagnostics.append(
                Diagnostic(
                    source=source,
                    code=code,
                    name=name,
                    detail=str(metrics.get("strategy_summary") or metrics.get("run_status") or "非证券对象"),
                )
            )
            continue

        if source == "hundred_day_high":
            reason = _reason_for_hundred_day_high(metrics)
        elif source.startswith("screening__"):
            reason = _reason_for_screening(metrics)
        else:
            reason = str(metrics.get("summary") or metrics.get("reason") or "信号命中")

        flags: List[str] = []
        if code[:6] in (suspicious_codes or set()):
            flags.append("行情异常")
        if code[:6] in rebuild_failed_codes:
            flags.append("行情重建失败")
        if last_bar_dates is not None:
            last_date = last_bar_dates.get(code[:6])
            if not last_date:
                flags.append("无本地行情")
            elif last_date < board_date:
                flags.append(f"行情截至{last_date}")
        else:
            flags.append("行情状态未知")
        if source.startswith("screening__"):
            strategy = source.split("__", 1)[1]
            audit = audit_by_strategy.get(strategy, {})
            snapshot_source = str(audit.get("snapshot_source") or "")
            if snapshot_source == "last_good_cache":
                flags.append("缓存快照")
            if audit.get("degradation"):
                flags.append("采集降级")
        elif source == "hundred_day_high":
            history_source = str(metrics.get("history_source") or "")
            if any(marker in history_source for marker in CACHE_FALLBACK_MARKERS):
                flags.append("缓存兜底")

        evidence = _candidate_evidence(
            row, metrics, board_date, health,
            audit_by_strategy.get(source.split("__", 1)[-1], {}), rows_by_source[source],
        )
        if not evidence["batch_ready"]:
            flags.append("批次未就绪")
        evidence["limitation"] += ("；" + "、".join(flags)) if flags else ""
        board.candidates.append(Candidate(source, code, name, reason, tuple(flags), **evidence))
        seen_codes.setdefault(code, []).append(source)

    # 多来源命中：保留各自样本，只加标记，不合并计数。
    multi = {code for code, sources in seen_codes.items() if len(sources) > 1}
    if multi:
        board.candidates = [
            replace(c, flags=c.flags + (("多来源命中",) if c.code in multi else ()))
            for c in board.candidates
        ]

    if degraded_sources:
        board.notes.append(
            "筛选审计出现降级："
            + "、".join(sorted(s.replace("screening__", "") for s in degraded_sources))
        )
    if board.rebuild_failure_count:
        board.notes.append(
            f"行情重建失败 {board.rebuild_failure_count} 只"
            + (f"（{board.rebuild_failure_rate}）" if board.rebuild_failure_rate else "")
            + "，命中其中的候选已标注"
        )
    return board


# --------------------------------------------------------------------------- #
# 渲染
# --------------------------------------------------------------------------- #


def select_track_candidates(candidates: Sequence[Candidate], limit: int = 30) -> List[Candidate]:
    """从总览候选里挑出值得建 track 的条目（测试期口径）。

    规则：
    1. 仅接收已匹配完整成功批次的版本，剔除“无本地行情”与“行情异常（待复核）”。
    2. 多来源命中优先，其次按来源、代码排序（稳定、可重现）。
    3. 按 limit 截断；返回的条目带着原 flags，建 track 时写进 limitation。
    """
    usable = [
        cand
        for cand in candidates
        if cand.batch_ready and "无本地行情" not in cand.flags and "行情异常" not in cand.flags
        and is_tradable_stock(cand.code)
    ]
    usable.sort(key=lambda c: ("多来源命中" not in c.flags, c.source, c.code))
    return usable[: max(0, int(limit))]


def _render_candidate_table(candidates: Sequence[Candidate], limit: int) -> List[str]:
    lines = ["| 代码 | 名称 | 理由 | 标记 |", "| --- | --- | --- | --- |"]
    for cand in candidates[:limit]:
        lines.append(f"| `{cand.code}` | {cand.name} | {cand.reason} | {cand.flag_text} |")
    if len(candidates) > limit:
        lines.append("")
        lines.append(f"显示 {limit} / 共 {len(candidates)} 条；按来源与原代码排序。")
    lines.extend(["", "<details><summary>来源版本与证据限制</summary>", ""])
    for cand in candidates[:limit]:
        lines.append(
            f"- `{cand.code}` source_ref=`{cand.source_ref or '未知'}`；"
            f"source_version=`{cand.source_version or '未知'}`；batch_ref=`{cand.batch_ref or '未知'}`；"
            f"available_at={cand.available_at or '未知'}；observed_at={cand.observed_at or '未知'} UTC；"
            f"quality={cand.quality}；{cand.limitation or '来源元数据未登记'}"
        )
    lines.extend(["", "</details>"])
    return lines


def render_markdown(board: Board, generated_at: Optional[datetime] = None) -> str:
    """渲染为 Markdown；不传 generated_at 时输出完全确定（便于回归）。"""
    lines: List[str] = []
    lines.append(f"# 今日总览 · {board.board_date}")
    lines.append("")
    lines.append("> A1-a 只读投影：只读取已有产物，不建表、不接 API、不调模型、不启动生产者。")
    lines.append("> 未接入的分区显示为缺口，不代表当天没有机会。")
    if generated_at is not None:
        lines.append(f"> 生成时间：{_format_generated_at(generated_at)}")
    lines.append("")

    ok = sum(1 for h in board.health if h.status == "ok")
    bad = [h for h in board.health if h.status in {"failed", "missing", "stale"}]
    lines.append(
        f"**市场环境**：`{board.market_regime}`（运行配置，不等于牛熊判断）　"
        f"**来源健康**：{ok}/{len(board.health)} 正常"
        + (f"，异常 {', '.join(h.label for h in bad)}" if bad else "")
    )
    codes = {cand.code for cand in board.candidates}
    multi = sum(1 for cand in board.candidates if "多来源命中" in cand.flags)
    lines.append(
        f"**候选**：{len(board.candidates)} 条原始 / {len(codes)} 只证券"
        f"（多来源 {multi} 条）；**诊断**：{len(board.diagnostics)} 条（不计入候选）"
    )
    for note in board.notes:
        lines.append(f"- {note}")
    lines.append("")

    by_source: Dict[str, List[Candidate]] = {}
    for cand in board.candidates:
        by_source.setdefault(cand.source, []).append(cand)

    lines.append("## ① 风险与失效")
    lines.append("")
    if board.rebuild_failure_count:
        hit = [c for c in board.candidates if "行情重建失败" in c.flags]
        lines.append(
            f"- 数据风险：行情重建失败 {board.rebuild_failure_count} 只"
            + (f"（{board.rebuild_failure_rate}）" if board.rebuild_failure_rate else "")
            + f"，其中 {len(hit)} 只进入候选，使用前须核复权与截至日。"
        )
    stale = [
        c for c in board.candidates
        if any(flag == "无本地行情" or flag.startswith("行情截至") for flag in c.flags)
    ]
    if stale:
        lines.append(
            f"- 数据时效：{len(stale)} 只候选的本地行情未到 {board.board_date}"
            "（行内已标截至日/无行情），参与判断前先核停牌与缺口。"
        )
    anomalous = [c for c in board.candidates if "行情异常" in c.flags]
    if anomalous:
        lines.append(
            f"- 行情待核：{len(anomalous)} 只候选的历史价格有跳变线索，复核前不用于收益判断。"
        )
    lines.append("- 持仓/研究反证与规则触发：未接入（需 A2-a 研究记录）。")
    lines.append("")

    lines.append("## ② 重要变化")
    lines.append("")
    if board.news:
        lines.append(board.news_note or f"窗口内 {len(board.news)} 条")
        lines.append("")
        lines.append("| 时间 | 来源 | 标的 | 标题 |")
        lines.append("| --- | --- | --- | --- |")
        for item in board.news:
            lines.append(f"| {item.published} | {item.source} | {item.scope} | {item.title} |")
    else:
        lines.append(f"- {board.news_note or '资讯未接入'}。")
    lines.append("")

    lines.append("## ③ 新研究候选")
    lines.append("")
    if not board.candidates:
        lines.append("- 本日无通过对象闸门的新候选。")
    for source, cands in sorted(by_source.items()):
        lines.append(f"### {source}（{len(cands)} 条）")
        lines.append("")
        lines.extend(_render_candidate_table(cands, CANDIDATE_PREVIEW_LIMIT))
        lines.append("")

    lines.append("## ④ 到期复核")
    lines.append("")
    if board.due_review_error:
        lines.append(f"- 研究跟踪库不可用：{board.due_review_error}（不伪造到期项）")
    elif not board.due_reviews:
        lines.append("- 无到期研究项（今日无 D+3 检查点）。")
    else:
        overdue = sum(1 for item in board.due_reviews if item.overdue_days > 0)
        lines.append(
            f"- 共 {len(board.due_reviews)} 条到期"
            + (f"，其中 {overdue} 条逾期" if overdue else "")
            + "；核对后更新 track 状态，不自动改资格。"
        )
        lines.append("")
        lines.append("| 代码 | 研究项 | 分支 | 到期日 | 状态 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for item in board.due_reviews:
            lines.append(
                f"| `{item.code}` | {item.title} | {item.branch} | {item.next_review_at} | {item.status_text} |"
            )
    lines.append("")

    lines.append("## 来源健康")
    lines.append("")
    lines.append("| 编排 | 状态 | 最近完成（北京时间） | exit | 说明 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for item in board.health:
        lines.append(
            f"| {item.label} | {item.status_text} | {item.last_done_local or '-'} | "
            f"{'-' if item.exit_code is None else item.exit_code} | {item.detail} |"
        )
    lines.append("")

    if board.screening_degradations:
        lines.append("### 筛选采集降级明细")
        lines.append("")
        for detail in board.screening_degradations:
            lines.append(f"- {detail}")
        lines.append("")

    lines.append("## 诊断（非证券对象，不进入候选）")
    lines.append("")
    if board.diagnostics:
        for diag in board.diagnostics:
            lines.append(f"- `{diag.code}` {diag.name}（{diag.source}）：{diag.detail}")
    else:
        lines.append("- 无。")
    lines.append("")

    lines.append("## 复现")
    lines.append("")
    lines.append("```bash")
    lines.append(f"./.venv-linux/bin/python scripts/build_today_board.py --date {board.board_date}")
    lines.append("```")
    return "\n".join(lines) + "\n"


def build_board_for_date(
    board_date: str,
    *,
    db_manager: Any = None,
    output_path: Optional[Path] = DEFAULT_OUTPUT_PATH,
    generated_at: Optional[datetime] = None,
    market_regime: Optional[str] = None,
) -> Tuple[Board, str]:
    """采集 + 组装 + 渲染 + （可选）落盘。返回 (board, markdown)。"""
    before = build_health(board_date)
    rows = load_signal_rows(board_date, db_manager=db_manager)
    audits = load_screening_audits(board_date)
    health = build_health(board_date)
    before_refs = {h.key: h.batch_ref for h in before}
    health = [
        replace(h, status="running", detail="读取期间批次变化，等待稳定完成版本")
        if h.status == "ok" and before_refs.get(h.key) != h.batch_ref else h
        for h in health
    ]
    failed_codes, _, rate = read_rebuild_failures()
    suspicious_codes = load_suspicious_codes()
    candidate_codes = [
        str(getattr(row, "code", "") or "")[:6] for row in rows if getattr(row, "code", "")
    ]
    last_dates: Optional[Dict[str, str]] = None
    try:
        last_dates = load_last_bar_dates(candidate_codes, db_manager=db_manager)
    except Exception:  # noqa: BLE001 - 查询失败=未知；查询成功但无行=确定没数据
        last_dates = None

    due_reviews: List[DueReview] = []
    due_error = ""
    try:
        due_reviews = load_due_reviews(board_date, db_manager=db_manager)
    except Exception as exc:  # noqa: BLE001 - 研究表未建或不可读时显式标注
        due_error = f"{type(exc).__name__}: {exc}"[:160]

    news: List[NewsItem] = []
    news_note = ""
    try:
        news, news_note = load_recent_news(board_date, db_manager=db_manager)
    except Exception as exc:  # noqa: BLE001 - 资讯源异常不阻断总览
        news_note = f"资讯读取失败：{type(exc).__name__}"

    if market_regime is None:
        market_regime = _read_market_regime()

    board = build_board(
        board_date,
        signal_rows=rows,
        screening_audits=audits,
        health=health,
        rebuild_failed_codes=failed_codes,
        rebuild_failure_rate=rate,
        market_regime=market_regime,
        last_bar_dates=last_dates,
        suspicious_codes=suspicious_codes,
        due_reviews=due_reviews,
        due_review_error=due_error,
        news=news,
        news_note=news_note,
    )
    markdown = render_markdown(board, generated_at=generated_at)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(markdown, encoding="utf-8")
    return board, markdown


def _read_market_regime() -> str:
    profile = PROJECT_ROOT / "config" / "local_strategy_profile.json"
    try:
        data = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unknown"
    return str(data.get("market_regime") or "unknown")
