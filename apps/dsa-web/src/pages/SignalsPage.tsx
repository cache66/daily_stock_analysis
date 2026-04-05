import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import { getParsedApiError } from '../api/error';
import type { ParsedApiError } from '../api/error';
import { signalsApi } from '../api/signals';
import {
  ApiErrorAlert,
  AppPage,
  Badge,
  Button,
  Card,
  EmptyState,
  PageHeader,
  Pagination,
  Select,
  StickyActionBar,
} from '../components/common';
import type {
  SignalSnapshotHistoryResponse,
  SignalSnapshotListItem,
  SignalSnapshotListResponse,
  SignalSnapshotStreakItem,
} from '../types/signals';
import {
  downloadSignalSelectionJson,
  downloadSignalSelectionMarkdown,
  formatSignalSelectionAsMarkdown,
} from '../utils/signalSelectionExport';

function formatPct(value?: number | null): string {
  if (value == null) return '--';
  return `${value.toFixed(2)}%`;
}

function formatPrice(value?: number | null): string {
  if (value == null) return '--';
  return value.toFixed(2);
}

function getTodayIsoDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function parseIsoDate(value: string): Date {
  return new Date(`${value}T00:00:00`);
}

function formatIsoDateLocal(value: Date): string {
  const year = value.getFullYear();
  const month = `${value.getMonth() + 1}`.padStart(2, '0');
  const day = `${value.getDate()}`.padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function buildListCacheKey(params: {
  signalType: string;
  signalDate?: string;
  signalDateFrom?: string;
  signalDateTo?: string;
  codes?: string[];
  page: number;
  pageSize: number;
}): string {
  return JSON.stringify({
    signalType: params.signalType,
    signalDate: params.signalDate ?? null,
    signalDateFrom: params.signalDateFrom ?? null,
    signalDateTo: params.signalDateTo ?? null,
    codes: [...(params.codes ?? [])].sort(),
    page: params.page,
    pageSize: params.pageSize,
  });
}

function buildHistoryCacheKey(params: {
  signalType: string;
  code: string;
  days: number;
  limit: number;
}): string {
  return JSON.stringify(params);
}

function dedupeCodes(items: SignalSnapshotListItem[]): string[] {
  return [...new Set(items.map((item) => item.code).filter(Boolean))];
}

type SortOption = 'latestHighDesc' | 'previousHitCountDesc' | 'closeDesc' | 'codeAsc';
type StreakGroupBy = 'none' | 'theme' | 'industry';
type ActionStatus = {
  type: 'success' | 'error';
  message: string;
} | null;

const COMPARE_SUMMARY_PREVIEW_DAYS = 10;

const SignalsPage: React.FC = () => {
  const [signalType] = useState('hundred_day_high');
  const [dateMode, setDateMode] = useState<'single' | 'range'>('single');
  const [signalDate, setSignalDate] = useState(getTodayIsoDate);
  const [signalDateFrom, setSignalDateFrom] = useState(getTodayIsoDate);
  const [signalDateTo, setSignalDateTo] = useState(getTodayIsoDate);
  const [codeFilter, setCodeFilter] = useState('');
  const [appliedCodeFilter, setAppliedCodeFilter] = useState('');
  const [historyDays, setHistoryDays] = useState('180');
  const [sortBy, setSortBy] = useState<SortOption>('latestHighDesc');
  const [showOnlyStreak, setShowOnlyStreak] = useState(false);
  const [activeLinkedCodes, setActiveLinkedCodes] = useState<string[]>([]);
  const [streakGroupBy, setStreakGroupBy] = useState<StreakGroupBy>('none');
  const [compareSummaryExpanded, setCompareSummaryExpanded] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [actionStatus, setActionStatus] = useState<ActionStatus>(null);
  const [isPushingSelection, setIsPushingSelection] = useState(false);
  const pageSize = 12;
  const [listData, setListData] = useState<SignalSnapshotListResponse | null>(null);
  const [selectedItem, setSelectedItem] = useState<SignalSnapshotListItem | null>(null);
  const [historyData, setHistoryData] = useState<SignalSnapshotHistoryResponse | null>(null);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [pageError, setPageError] = useState<ParsedApiError | null>(null);
  const listCacheRef = useRef<Map<string, SignalSnapshotListResponse>>(new Map());
  const historyCacheRef = useRef<Map<string, SignalSnapshotHistoryResponse>>(new Map());

  useEffect(() => {
    document.title = '信号快照 - DSA';
  }, []);

  const requestedCodes = useMemo(() => {
    if (activeLinkedCodes.length > 0) {
      return activeLinkedCodes;
    }
    if (appliedCodeFilter) {
      return [appliedCodeFilter];
    }
    return undefined;
  }, [activeLinkedCodes, appliedCodeFilter]);

  const loadList = async (options?: {
    force?: boolean;
    requestedPage?: number;
    requestedCodes?: string[];
  }) => {
    const nextPage = options?.requestedPage ?? currentPage;
    const nextCodes = options?.requestedCodes ?? requestedCodes;
    const params = {
      signalType,
      signalDate: dateMode === 'single' ? signalDate : undefined,
      signalDateFrom: dateMode === 'range' ? signalDateFrom : undefined,
      signalDateTo: dateMode === 'range' ? signalDateTo : undefined,
      codes: nextCodes,
      page: nextPage,
      pageSize,
    };
    const cacheKey = buildListCacheKey(params);
    if (!options?.force) {
      const cached = listCacheRef.current.get(cacheKey);
      if (cached) {
        setListData(cached);
        setPageError(null);
        if (cached.items.length > 0) {
          setSelectedItem((prev) => {
            if (!prev) {
              return cached.items[0];
            }
            return cached.items.find((item) => item.code === prev.code && item.signalDate === prev.signalDate)
              ?? cached.items[0];
          });
        } else {
          setSelectedItem(null);
          setHistoryData(null);
        }
        return;
      }
    }

    setIsLoadingList(true);
    try {
      const response = await signalsApi.getSnapshots({
        signalType: params.signalType,
        signalDate: params.signalDate,
        signalDateFrom: params.signalDateFrom,
        signalDateTo: params.signalDateTo,
        code: undefined,
        codes: params.codes,
        page: params.page,
        pageSize: params.pageSize,
      });
      listCacheRef.current.set(cacheKey, response);
      setListData(response);
      setPageError(null);
      if (response.items.length > 0) {
        setSelectedItem((prev) => {
          if (!prev) {
            return response.items[0];
          }
          return response.items.find((item) => item.code === prev.code && item.signalDate === prev.signalDate)
            ?? response.items[0];
        });
      } else {
        setSelectedItem(null);
        setHistoryData(null);
      }
    } catch (error) {
      setPageError(getParsedApiError(error));
      setListData(null);
      setSelectedItem(null);
      setHistoryData(null);
    } finally {
      setIsLoadingList(false);
    }
  };

  const loadHistory = async (item: SignalSnapshotListItem, force = false) => {
    const days = Number.parseInt(historyDays, 10) || 180;
    const limit = 100;
    const cacheKey = buildHistoryCacheKey({
      signalType,
      code: item.code,
      days,
      limit,
    });
    if (!force) {
      const cached = historyCacheRef.current.get(cacheKey);
      if (cached) {
        setHistoryData(cached);
        setPageError(null);
        return;
      }
    }

    setIsLoadingHistory(true);
    try {
      const response = await signalsApi.getHistory(signalType, item.code, { days, limit });
      historyCacheRef.current.set(cacheKey, response);
      setHistoryData(response);
      setPageError(null);
    } catch (error) {
      setPageError(getParsedApiError(error));
      setHistoryData(null);
    } finally {
      setIsLoadingHistory(false);
    }
  };

  const filteredAndSortedItems = useMemo(() => {
    const baseItems = listData?.items ?? [];
    const streakFiltered = showOnlyStreak
      ? baseItems.filter((item) => item.isConsecutiveSignal)
      : baseItems;
    const linkedFiltered = activeLinkedCodes.length > 0
      ? streakFiltered.filter((item) => activeLinkedCodes.includes(item.code))
      : streakFiltered;

    return [...linkedFiltered].sort((left, right) => {
      switch (sortBy) {
        case 'previousHitCountDesc':
          return (right.previousHitCount ?? 0) - (left.previousHitCount ?? 0)
            || (right.latestHigh ?? 0) - (left.latestHigh ?? 0);
        case 'closeDesc':
          return (right.close ?? 0) - (left.close ?? 0)
            || (right.latestHigh ?? 0) - (left.latestHigh ?? 0);
        case 'codeAsc':
          return (left.code || '').localeCompare(right.code || '');
        case 'latestHighDesc':
        default:
          return (right.latestHigh ?? 0) - (left.latestHigh ?? 0)
            || (right.close ?? 0) - (left.close ?? 0);
      }
    });
  }, [activeLinkedCodes, listData?.items, showOnlyStreak, sortBy]);

  useEffect(() => {
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signalDate, signalDateFrom, signalDateTo, signalType, currentPage, dateMode, appliedCodeFilter, activeLinkedCodes.join(',')]);

  useEffect(() => {
    setCompareSummaryExpanded(false);
  }, [signalDate, signalDateFrom, signalDateTo, dateMode]);

  useEffect(() => {
    if (!filteredAndSortedItems.length) {
      setSelectedItem(null);
      setHistoryData(null);
      return;
    }
    if (!selectedItem) {
      setSelectedItem(filteredAndSortedItems[0]);
      return;
    }
    const stillVisible = filteredAndSortedItems.find(
      (item) => item.code === selectedItem.code && item.signalDate === selectedItem.signalDate,
    );
    if (!stillVisible) {
      setSelectedItem(filteredAndSortedItems[0]);
    }
  }, [filteredAndSortedItems, selectedItem]);

  useEffect(() => {
    if (!selectedItem) {
      setHistoryData(null);
      return;
    }
    void loadHistory(selectedItem);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedItem?.code, historyDays]);

  const currentHistoryItem = useMemo(() => historyData?.items?.[0] ?? null, [historyData]);
  const todayIsoDate = getTodayIsoDate();
  const continuousCount = useMemo(
    () => (listData?.items ?? []).filter((item) => item.isConsecutiveSignal).length,
    [listData?.items],
  );
  const groupedItems = useMemo(() => {
    const consecutive = filteredAndSortedItems.filter((item) => item.isConsecutiveSignal);
    const nonConsecutive = filteredAndSortedItems.filter((item) => !item.isConsecutiveSignal);
    return { consecutive, nonConsecutive };
  }, [filteredAndSortedItems]);
  const streakGroups = useMemo(() => {
    const leaderboard = listData?.streakLeaderboard ?? [];
    if (streakGroupBy === 'none') {
      return [{ label: '', items: leaderboard }];
    }

    const grouped = leaderboard.reduce<Record<string, SignalSnapshotStreakItem[]>>((acc, item) => {
      const key = streakGroupBy === 'theme'
        ? (item.themeLabel || '未分组主题')
        : (item.industry || '未分组行业');
      acc[key] = acc[key] || [];
      acc[key].push(item);
      return acc;
    }, {});

    return Object.entries(grouped).map(([label, items]) => ({ label, items }));
  }, [listData?.streakLeaderboard, streakGroupBy]);
  const totalPages = useMemo(() => {
    const total = listData?.total ?? 0;
    return Math.max(1, Math.ceil(total / pageSize));
  }, [listData?.total]);
  const visibleCompareSummary = useMemo(() => {
    const compareSummary = listData?.compareSummary ?? [];
    if (compareSummaryExpanded) {
      return compareSummary;
    }
    return compareSummary.slice(0, COMPARE_SUMMARY_PREVIEW_DAYS);
  }, [compareSummaryExpanded, listData?.compareSummary]);
  const hiddenCompareSummaryCount = Math.max(
    0,
    (listData?.compareSummary?.length ?? 0) - visibleCompareSummary.length,
  );
  const exportableItems = useMemo(() => {
    if (activeLinkedCodes.length > 0) {
      return filteredAndSortedItems.filter((item) => activeLinkedCodes.includes(item.code));
    }
    if (showOnlyStreak) {
      return groupedItems.consecutive;
    }
    return filteredAndSortedItems;
  }, [activeLinkedCodes, filteredAndSortedItems, groupedItems.consecutive, showOnlyStreak]);
  const selectedCodes = useMemo(() => dedupeCodes(exportableItems), [exportableItems]);
  const selectedCodeItems = useMemo(() => {
    const byCode = new Map<string, SignalSnapshotListItem>();
    for (const item of exportableItems) {
      if (!byCode.has(item.code)) {
        byCode.set(item.code, item);
      }
    }
    return selectedCodes.map((code) => byCode.get(code)).filter(Boolean) as SignalSnapshotListItem[];
  }, [exportableItems, selectedCodes]);
  const dateLabel = dateMode === 'single'
    ? signalDate
    : `${signalDateFrom} ~ ${signalDateTo}`;
  const selectionLabel = activeLinkedCodes.length > 0
    ? `联动筛选 ${selectedCodes.length} 只股票 / ${exportableItems.length} 条快照`
    : showOnlyStreak
      ? `当前页连续新高 ${exportableItems.length} 条`
      : `当前页结果 ${exportableItems.length} 条`;
  const groupedByLabel = dateMode === 'range' && streakGroupBy !== 'none'
    ? (streakGroupBy === 'theme' ? '按主题分组' : '按行业分组')
    : undefined;
  const exportMarkdown = useMemo(
    () => formatSignalSelectionAsMarkdown(exportableItems, {
      signalType,
      dateLabel,
      selectionLabel,
      groupedBy: groupedByLabel,
    }),
    [dateLabel, exportableItems, groupedByLabel, selectionLabel, signalType],
  );
  const shouldShowActionBar = exportableItems.length > 0;

  const handleShiftDate = (deltaDays: number) => {
    const nextDate = parseIsoDate(signalDate);
    nextDate.setDate(nextDate.getDate() + deltaDays);
    const nextIsoDate = formatIsoDateLocal(nextDate);
    if (nextIsoDate > todayIsoDate) {
      return;
    }
    setSignalDate(nextIsoDate);
  };

  const handleRefresh = () => {
    const normalizedCodeFilter = codeFilter.trim().toUpperCase();
    const shouldForceDirectRefresh = currentPage === 1 && appliedCodeFilter === normalizedCodeFilter;
    setCurrentPage(1);
    setAppliedCodeFilter(normalizedCodeFilter);
    setActionStatus(null);
    if (shouldForceDirectRefresh) {
      void loadList({
        force: true,
        requestedPage: 1,
        requestedCodes: activeLinkedCodes.length > 0
          ? activeLinkedCodes
          : (normalizedCodeFilter ? [normalizedCodeFilter] : undefined),
      });
    }
  };

  const handleApplyStreakOnly = () => {
    setShowOnlyStreak(true);
    setActionStatus(null);
  };

  const handleClearLinkedFilter = () => {
    setActiveLinkedCodes([]);
    setCodeFilter('');
    setAppliedCodeFilter('');
    setActionStatus(null);
  };

  const handleToggleLinkedCode = (code: string, forceStreakOnly: boolean) => {
    setCodeFilter('');
    setAppliedCodeFilter('');
    setCurrentPage(1);
    setActionStatus(null);
    if (forceStreakOnly) {
      setShowOnlyStreak(true);
    }
    setActiveLinkedCodes((prev) => {
      const exists = prev.includes(code);
      if (exists) {
        return prev.filter((item) => item !== code);
      }
      return [...prev, code];
    });
  };

  const handleSelectCompareItem = (code: string) => {
    setCurrentPage(1);
    setCodeFilter('');
    setAppliedCodeFilter('');
    setActionStatus(null);
    setShowOnlyStreak(false);
    setActiveLinkedCodes([code]);
  };

  const handleSelectGroup = (codes: string[]) => {
    setShowOnlyStreak(true);
    setCodeFilter('');
    setAppliedCodeFilter('');
    setCurrentPage(1);
    setActionStatus(null);
    setActiveLinkedCodes([...new Set(codes)]);
  };

  const handleCopyMarkdown = async () => {
    try {
      await navigator.clipboard.writeText(exportMarkdown);
      setActionStatus({ type: 'success', message: `已复制 ${exportableItems.length} 条快照的 Markdown。` });
    } catch (error) {
      setActionStatus({ type: 'error', message: getParsedApiError(error).message });
    }
  };

  const handleDownloadMarkdown = () => {
    downloadSignalSelectionMarkdown(exportableItems, {
      signalType,
      dateLabel,
      selectionLabel,
      groupedBy: groupedByLabel,
    });
    setActionStatus({ type: 'success', message: `已导出 ${exportableItems.length} 条快照 Markdown。` });
  };

  const handleDownloadJson = () => {
    downloadSignalSelectionJson(exportableItems, {
      signalType,
      dateLabel,
      selectionLabel,
      groupedBy: groupedByLabel,
    });
    setActionStatus({ type: 'success', message: `已导出 ${exportableItems.length} 条快照 JSON。` });
  };

  const handlePushSelection = async () => {
    setIsPushingSelection(true);
    try {
      const response = await signalsApi.sendSelection({
        content: exportMarkdown,
        title: `信号快照推送 ${dateLabel}`,
      });
      if (!response.success) {
        throw new Error(response.message || '推送未成功完成');
      }
      setActionStatus({ type: 'success', message: `已推送 ${exportableItems.length} 条快照到已配置通知渠道。` });
    } catch (error) {
      setActionStatus({ type: 'error', message: getParsedApiError(error).message });
    } finally {
      setIsPushingSelection(false);
    }
  };

  return (
    <AppPage>
      <div className="space-y-6" data-testid="signals-page">
        <PageHeader
          eyebrow="K-Line Signals"
          title="百日新高快照"
          description="按日期查看百日新高信号，支持多日对比、连续新高联动筛选，以及当前选中结果的导出和推送。"
          actions={(
            <>
              <Select
                value={dateMode}
                onChange={(value) => {
                  setDateMode(value as 'single' | 'range');
                  setCurrentPage(1);
                  setActionStatus(null);
                }}
                options={[
                  { value: 'single', label: '单日' },
                  { value: 'range', label: '日期范围' },
                ]}
                className="min-w-[120px]"
                aria-label="date-mode"
              />
              <input
                type="date"
                value={signalDate}
                onChange={(e) => setSignalDate(e.target.value)}
                className="input-terminal h-10 min-w-[160px]"
                aria-label="signal-date"
                disabled={dateMode !== 'single'}
              />
              <Button variant="secondary" onClick={() => handleShiftDate(-1)} aria-label="previous-day">
                上一天
              </Button>
              <Button
                variant="secondary"
                onClick={() => handleShiftDate(1)}
                aria-label="next-day"
                disabled={signalDate >= todayIsoDate}
              >
                下一天
              </Button>
              <Button
                variant="secondary"
                onClick={() => setSignalDate(todayIsoDate)}
                aria-label="go-today"
                disabled={signalDate === todayIsoDate}
              >
                今天
              </Button>
              {dateMode === 'range' ? (
                <>
                  <input
                    type="date"
                    value={signalDateFrom}
                    onChange={(e) => setSignalDateFrom(e.target.value)}
                    className="input-terminal h-10 min-w-[160px]"
                    aria-label="signal-date-from"
                  />
                  <input
                    type="date"
                    value={signalDateTo}
                    onChange={(e) => setSignalDateTo(e.target.value)}
                    className="input-terminal h-10 min-w-[160px]"
                    aria-label="signal-date-to"
                  />
                </>
              ) : null}
              <input
                type="text"
                value={codeFilter}
                onChange={(e) => {
                  setCodeFilter(e.target.value.toUpperCase());
                  setActiveLinkedCodes([]);
                  setActionStatus(null);
                }}
                placeholder="可选股票代码"
                className="input-terminal h-10 min-w-[160px]"
                aria-label="stock-code-filter"
              />
              <Select
                value={sortBy}
                onChange={(value) => setSortBy(value as SortOption)}
                options={[
                  { value: 'latestHighDesc', label: '按最新 high' },
                  { value: 'previousHitCountDesc', label: '按历史命中次数' },
                  { value: 'closeDesc', label: '按收盘价' },
                  { value: 'codeAsc', label: '按代码' },
                ]}
                className="min-w-[180px]"
                aria-label="sort-mode"
              />
              <label className="flex h-10 items-center gap-2 rounded-xl border border-subtle bg-card px-3 text-sm text-secondary-text shadow-soft-card">
                <input
                  type="checkbox"
                  checked={showOnlyStreak}
                  onChange={(e) => {
                    setShowOnlyStreak(e.target.checked);
                    setActionStatus(null);
                  }}
                  aria-label="streak-only"
                />
                只看连续新高
              </label>
              <Button
                variant="secondary"
                onClick={handleRefresh}
                disabled={isLoadingList}
                aria-label="refresh-list"
              >
                {isLoadingList ? '加载中...' : '刷新列表'}
              </Button>
            </>
          )}
        />

        {pageError ? <ApiErrorAlert error={pageError} /> : null}

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
          <Card title="当日命中快照" subtitle={`${dateLabel} · ${signalType}`}>
            {isLoadingList && !listData ? (
              <div className="space-y-3">
                <div className="h-12 animate-pulse rounded-xl bg-card/60" />
                <div className="h-12 animate-pulse rounded-xl bg-card/60" />
                <div className="h-12 animate-pulse rounded-xl bg-card/60" />
              </div>
            ) : listData && filteredAndSortedItems.length > 0 ? (
              <div className="space-y-3">
                <div className="flex items-center justify-between text-xs text-muted-text">
                  <span>命中数量</span>
                  <span className="font-mono text-secondary-text">
                    {filteredAndSortedItems.length} / {listData.total}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <Badge variant="info">连续新高 {continuousCount}</Badge>
                  {showOnlyStreak ? <Badge variant="warning">已启用连续新高过滤</Badge> : null}
                  {activeLinkedCodes.length > 0 ? <Badge variant="history">联动 {activeLinkedCodes.length} 只</Badge> : null}
                  {groupedByLabel ? <Badge variant="default">{groupedByLabel}</Badge> : null}
                </div>
                {dateMode === 'range' && listData.compareSummary.length > 0 ? (
                  <div className="rounded-2xl border border-border/60 bg-card/30 p-3">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <p className="text-xs text-muted-text">多日对比</p>
                      {listData.compareSummary.length > COMPARE_SUMMARY_PREVIEW_DAYS ? (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setCompareSummaryExpanded((prev) => !prev)}
                          aria-label="signals-compare-toggle"
                        >
                          {compareSummaryExpanded ? '收起' : `展开剩余 ${hiddenCompareSummaryCount} 天`}
                        </Button>
                      ) : null}
                    </div>
                    <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-1">
                      {visibleCompareSummary.map((item) => (
                        <div
                          key={item.signalDate}
                          className="flex items-start justify-between rounded-xl border border-border/40 px-3 py-2 text-xs"
                        >
                          <div className="min-w-0">
                            <div className="font-medium text-foreground">{item.signalDate}</div>
                            <div className="text-secondary-text">{item.topCodes.join(' / ') || '--'}</div>
                            {(item.addedItems.length > 0 || item.droppedItems.length > 0) ? (
                              <div className="mt-1 flex flex-wrap gap-1">
                                {item.addedItems.map((entry) => {
                                  const linked = activeLinkedCodes.includes(entry.code);
                                  return (
                                    <button
                                      key={`added-${item.signalDate}-${entry.code}`}
                                      type="button"
                                      aria-label={`link-added-${entry.code}`}
                                      onClick={() => handleSelectCompareItem(entry.code)}
                                      className={linked ? 'rounded-full ring-1 ring-cyan/40' : undefined}
                                    >
                                      <Badge variant="success" className={linked ? 'bg-cyan/10 text-cyan' : ''}>
                                        新增 {entry.name || entry.code}
                                      </Badge>
                                    </button>
                                  );
                                })}
                                {item.droppedItems.map((entry) => {
                                  const linked = activeLinkedCodes.includes(entry.code);
                                  return (
                                    <button
                                      key={`dropped-${item.signalDate}-${entry.code}`}
                                      type="button"
                                      aria-label={`link-dropped-${entry.code}`}
                                      onClick={() => handleSelectCompareItem(entry.code)}
                                      className={linked ? 'rounded-full ring-1 ring-cyan/40' : undefined}
                                    >
                                      <Badge variant="danger" className={linked ? 'bg-cyan/10 text-cyan' : ''}>
                                        掉队 {entry.name || entry.code}
                                      </Badge>
                                    </button>
                                  );
                                })}
                              </div>
                            ) : null}
                          </div>
                          <div className="text-right">
                            <div className="text-foreground">{item.totalCount} 只</div>
                            <div className="text-secondary-text">连续 {item.continuousCount}</div>
                            <div className="text-secondary-text">新增 {item.addedCount} / 掉队 {item.droppedCount}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                {dateMode === 'range' && listData.streakLeaderboard.length > 0 ? (
                  <div className="rounded-2xl border border-border/60 bg-card/30 p-3">
                    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                      <p className="text-xs text-muted-text">streak 排行</p>
                      <div className="flex flex-wrap items-center gap-2">
                        <Select
                          value={streakGroupBy}
                          onChange={(value) => {
                            setStreakGroupBy(value as StreakGroupBy);
                            setActionStatus(null);
                          }}
                          options={[
                            { value: 'none', label: '不分组' },
                            { value: 'theme', label: '按主题' },
                            { value: 'industry', label: '按行业' },
                          ]}
                          className="min-w-[140px]"
                          aria-label="streak-group-mode"
                        />
                        <Button variant="secondary" size="sm" onClick={handleApplyStreakOnly}>
                          只看全部连续新高
                        </Button>
                        {(activeLinkedCodes.length > 0 || appliedCodeFilter) ? (
                          <Button variant="ghost" size="sm" onClick={handleClearLinkedFilter}>
                            清除联动
                          </Button>
                        ) : null}
                      </div>
                    </div>
                    <div className="space-y-3">
                      {streakGroups.map((group, groupIndex) => {
                        const selectedInGroup = group.items.filter((item) => activeLinkedCodes.includes(item.code)).length;
                        return (
                          <div key={`${group.label || 'all'}-${groupIndex}`} className="space-y-2">
                            {group.label ? (
                              <div className="flex items-center justify-between gap-2">
                                <div className="flex min-w-0 flex-wrap items-center gap-2">
                                  <p className="text-xs font-medium text-muted-text">{group.label}</p>
                                  <Badge variant={selectedInGroup > 0 ? 'history' : 'default'}>
                                    已选 {selectedInGroup}/{group.items.length}
                                  </Badge>
                                </div>
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() => handleSelectGroup(group.items.map((item) => item.code))}
                                  aria-label={`select-streak-group-${groupIndex}`}
                                >
                                  选择本组
                                </Button>
                              </div>
                            ) : null}
                            {group.items.map((item, index) => (
                              <button
                                key={`${item.code}-${item.latestSignalDate}`}
                                type="button"
                                aria-label={`link-streak-${item.code}`}
                                onClick={() => handleToggleLinkedCode(item.code, true)}
                                className={`flex w-full items-center justify-between rounded-xl border px-3 py-2 text-left text-xs transition-colors ${
                                  activeLinkedCodes.includes(item.code)
                                    ? 'border-cyan/40 bg-cyan/10 shadow-lg shadow-cyan/10'
                                    : 'border-border/40 hover:border-border hover:bg-hover'
                                }`}
                              >
                                <div className="min-w-0">
                                  <div className="flex flex-wrap items-center gap-2 font-medium text-foreground">
                                    <span>#{index + 1} {item.name || item.code}</span>
                                    {activeLinkedCodes.includes(item.code) ? <Badge variant="history">已联动</Badge> : null}
                                  </div>
                                  <div className="text-secondary-text">
                                    {item.code} · 最新 {item.latestSignalDate || '--'}
                                  </div>
                                  <div className="text-secondary-text">
                                    {item.themeLabel || item.industry || '--'}
                                  </div>
                                </div>
                                <div className="text-right">
                                  <div className="text-foreground">当前 {item.currentStreakCount}</div>
                                  <div className="text-secondary-text">最长 {item.longestStreakCount}</div>
                                </div>
                              </button>
                            ))}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null}
                <div className="space-y-2">
                  {groupedItems.consecutive.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-text">连续新高</p>
                      {groupedItems.consecutive.map((item) => {
                        const isActive = selectedItem?.code === item.code && selectedItem?.signalDate === item.signalDate;
                        return (
                          <button
                            key={`${item.code}-${item.signalDate}`}
                            type="button"
                            onClick={() => setSelectedItem(item)}
                            className={`w-full rounded-2xl border p-4 text-left transition-colors ${
                              isActive
                                ? 'border-cyan/40 bg-cyan/10'
                                : 'border-border/60 bg-card/40 hover:border-border hover:bg-hover'
                            }`}
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="font-semibold text-foreground">{item.name || item.code}</span>
                                  <Badge variant="info">{item.code}</Badge>
                                  {item.themeLabel ? <Badge variant="warning">{item.themeLabel}</Badge> : null}
                                  <Badge variant="success">连续新高</Badge>
                                </div>
                                <p className="mt-2 line-clamp-2 text-sm text-secondary-text">
                                  {item.reasonSummary || '当前未生成归因摘要'}
                                </p>
                              </div>
                              <div className="shrink-0 text-right text-xs text-muted-text">
                                <div>high {formatPrice(item.latestHigh)}</div>
                                <div>close {formatPrice(item.close)}</div>
                              </div>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                  {groupedItems.nonConsecutive.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-text">其他新高</p>
                      {groupedItems.nonConsecutive.map((item) => {
                        const isActive = selectedItem?.code === item.code && selectedItem?.signalDate === item.signalDate;
                        return (
                          <button
                            key={`${item.code}-${item.signalDate}`}
                            type="button"
                            onClick={() => setSelectedItem(item)}
                            className={`w-full rounded-2xl border p-4 text-left transition-colors ${
                              isActive
                                ? 'border-cyan/40 bg-cyan/10'
                                : 'border-border/60 bg-card/40 hover:border-border hover:bg-hover'
                            }`}
                          >
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="font-semibold text-foreground">{item.name || item.code}</span>
                                  <Badge variant="info">{item.code}</Badge>
                                  {item.themeLabel ? <Badge variant="warning">{item.themeLabel}</Badge> : null}
                                </div>
                                <p className="mt-2 line-clamp-2 text-sm text-secondary-text">
                                  {item.reasonSummary || '当前未生成归因摘要'}
                                </p>
                              </div>
                              <div className="shrink-0 text-right text-xs text-muted-text">
                                <div>high {formatPrice(item.latestHigh)}</div>
                                <div>close {formatPrice(item.close)}</div>
                              </div>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
                <Pagination currentPage={currentPage} totalPages={totalPages} onPageChange={setCurrentPage} />
                {shouldShowActionBar ? (
                  <StickyActionBar className="mt-4">
                    <div className="mr-auto flex flex-wrap items-center gap-2" data-testid="signals-selection-summary">
                      <Badge variant="history">已选 {selectedCodes.length} 只</Badge>
                      <Badge variant="info">{selectionLabel}</Badge>
                      {groupedByLabel ? <Badge variant="default">{groupedByLabel}</Badge> : null}
                      {selectedCodeItems.slice(0, 6).map((item) => (
                        <button
                          key={`selected-code-${item.code}`}
                          type="button"
                          onClick={() => handleToggleLinkedCode(item.code, false)}
                          className="rounded-full"
                          aria-label={`selected-code-${item.code}`}
                        >
                          <Badge variant={activeLinkedCodes.includes(item.code) ? 'history' : 'default'}>
                            {item.name || item.code}
                          </Badge>
                        </button>
                      ))}
                      {selectedCodeItems.length > 6 ? (
                        <Badge variant="default">+{selectedCodeItems.length - 6} 只</Badge>
                      ) : null}
                      {actionStatus ? (
                        <Badge variant={actionStatus.type === 'success' ? 'success' : 'danger'}>
                          {actionStatus.message}
                        </Badge>
                      ) : null}
                    </div>
                    <Button variant="secondary" size="sm" onClick={handleCopyMarkdown} aria-label="signals-copy-markdown">
                      复制 Markdown
                    </Button>
                    <Button variant="secondary" size="sm" onClick={handleDownloadMarkdown} aria-label="signals-download-markdown">
                      导出 Markdown
                    </Button>
                    <Button variant="secondary" size="sm" onClick={handleDownloadJson} aria-label="signals-download-json">
                      导出 JSON
                    </Button>
                    <Button
                      variant="primary"
                      size="sm"
                      onClick={handlePushSelection}
                      isLoading={isPushingSelection}
                      loadingText="推送中..."
                      aria-label="signals-push-selection"
                    >
                      推送通知
                    </Button>
                  </StickyActionBar>
                ) : null}
              </div>
            ) : listData && listData.items.length > 0 ? (
              <EmptyState
                title="筛选后没有符合条件的快照"
                description="可以关闭“只看连续新高”，或调整排序和股票代码过滤条件。"
              />
            ) : (
              <EmptyState
                title="当天没有命中快照"
                description="可以切换日期，或先运行百日新高筛选脚本生成当日快照。"
              />
            )}
          </Card>

          <div className="space-y-6">
            <Card
              title={selectedItem ? `${selectedItem.name || selectedItem.code} · 历史观察` : '历史观察'}
              subtitle="连续新高 / 近似回撤"
            >
              <div className="mb-4 flex flex-wrap items-center gap-2">
                <label className="text-sm text-secondary-text" htmlFor="signal-history-days">
                  历史窗口
                </label>
                <input
                  id="signal-history-days"
                  type="number"
                  value={historyDays}
                  min={1}
                  max={1000}
                  onChange={(e) => setHistoryDays(e.target.value)}
                  className="input-terminal h-10 w-28"
                />
              </div>

              {isLoadingHistory && selectedItem ? (
                <div className="space-y-3">
                  <div className="h-12 animate-pulse rounded-xl bg-card/60" />
                  <div className="h-12 animate-pulse rounded-xl bg-card/60" />
                </div>
              ) : historyData && currentHistoryItem ? (
                <div className="space-y-4 text-sm">
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                      <p className="text-xs text-muted-text">当前连续命中</p>
                      <p className="mt-1 text-xl font-semibold text-foreground">{historyData.continuity.currentStreakCount}</p>
                      <p className="mt-1 text-xs text-secondary-text">
                        {historyData.continuity.currentStreakStartDate || '--'} → {historyData.continuity.currentStreakEndDate || '--'}
                      </p>
                    </div>
                    <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                      <p className="text-xs text-muted-text">距最大信号高点</p>
                      <p className="mt-1 text-xl font-semibold text-foreground">
                        {formatPct(historyData.drawdown.distanceFromMaxSignalHighPct)}
                      </p>
                      <p className="mt-1 text-xs text-secondary-text">
                        {historyData.drawdown.maxSignalHighDate || '--'} · high {formatPrice(historyData.drawdown.maxSignalHigh)}
                      </p>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                    <p className="text-xs text-muted-text">结构化归因</p>
                    <div className="mt-3 space-y-3">
                      <div>
                        <p className="text-xs font-medium text-muted-text">行业逻辑</p>
                        <p className="mt-1 text-secondary-text">{currentHistoryItem.industryLogic || '--'}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-muted-text">消息逻辑</p>
                        <p className="mt-1 text-secondary-text">{currentHistoryItem.newsLogic || '--'}</p>
                      </div>
                      <div>
                        <p className="text-xs font-medium text-muted-text">技术逻辑</p>
                        <p className="mt-1 text-secondary-text">{currentHistoryItem.technicalLogic || '--'}</p>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                    <p className="mb-3 text-xs text-muted-text">历史命中</p>
                    <div className="space-y-2">
                      {historyData.items.map((item) => (
                        <div
                          key={`${item.code}-${item.signalDate}`}
                          className="flex items-center justify-between gap-3 rounded-xl border border-border/40 px-3 py-2"
                        >
                          <div className="min-w-0">
                            <p className="font-medium text-foreground">{item.signalDate}</p>
                            <p className="text-xs text-secondary-text">
                              high {formatPrice(item.latestHigh)} · close {formatPrice(item.close)}
                            </p>
                          </div>
                          {item.themeLabel ? <Badge variant="history">{item.themeLabel}</Badge> : null}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ) : (
                <EmptyState
                  title="选择一只股票查看历史"
                  description="先从左侧当天命中的快照里选择一只股票。"
                />
              )}
            </Card>
          </div>
        </div>
      </div>
    </AppPage>
  );
};

export default SignalsPage;
