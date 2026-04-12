import type React from 'react';
import { useEffect, useMemo, useRef, useState } from 'react';
import axios from 'axios';
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
  SignalSnapshotCountsResponse,
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
  return formatIsoDateLocal(new Date());
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

function diffDaysInclusive(fromIsoDate: string, toIsoDate: string): number | null {
  if (!fromIsoDate || !toIsoDate) return null;
  const from = parseIsoDate(fromIsoDate);
  const to = parseIsoDate(toIsoDate);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) return null;
  return Math.floor((to.getTime() - from.getTime()) / (24 * 60 * 60 * 1000));
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

function summarizeYtd(items: SignalSnapshotListItem[]): { avg: number | null; median: number | null } {
  const values = items
    .map((item) => item.ytdReturnPct)
    .filter((value): value is number => value != null)
    .sort((left, right) => left - right);
  if (!values.length) {
    return { avg: null, median: null };
  }
  const avg = values.reduce((sum, value) => sum + value, 0) / values.length;
  const middle = Math.floor(values.length / 2);
  const median = values.length % 2 === 1
    ? values[middle]
    : (values[middle - 1] + values[middle]) / 2;
  return {
    avg: Number(avg.toFixed(2)),
    median: Number(median.toFixed(2)),
  };
}

function isRequestCanceled(error: unknown): boolean {
  return axios.isCancel(error)
    || (error instanceof Error && error.name === 'CanceledError')
    || (typeof error === 'object' && error !== null && 'code' in error && (error as { code?: string }).code === 'ERR_CANCELED');
}

type SortOption = 'latestHighDesc' | 'previousHitCountDesc' | 'closeDesc' | 'ytdReturnDesc' | 'eventDateDesc' | 'signalDateDesc' | 'codeAsc';
type StreakGroupBy = 'none' | 'theme' | 'industry';
type YtdFilterOption = 'all' | 'gt0' | 'gt20' | 'gt50';
type ActionStatus = {
  type: 'success' | 'error';
  message: string;
} | null;

const COMPARE_SUMMARY_PREVIEW_DAYS = 10;
const SIGNALS_INPUT_CLASS =
  'input-surface input-focus-glow h-10 rounded-xl border bg-transparent px-3 text-sm transition-all focus:outline-none disabled:cursor-not-allowed disabled:opacity-60';

type SignalTypeMeta = {
  title: string;
  description: string;
  streakLabel: string;
  itemGroupTitle: string;
  nonStreakTitle: string;
  emptyHint: string;
  tabAccent: string;
  countBadgeClass: string;
  activeCountBadgeClass: string;
};

const SIGNAL_TYPE_META: Record<string, SignalTypeMeta> = {
  hundred_day_high: {
    title: '百日新高快照',
    description: '按日期查看百日新高信号，支持多日对比、连续新高联动筛选，以及当前选中结果的导出和推送。',
    streakLabel: '连续新高',
    itemGroupTitle: '连续新高',
    nonStreakTitle: '其他新高',
    emptyHint: '可以切换日期，或先运行百日新高筛选脚本生成当日快照。',
    tabAccent: 'from-amber-500/25 via-amber-400/10 to-transparent border-amber-400/40 text-amber-100',
    countBadgeClass: 'border-amber-400/25 bg-amber-500/10 text-amber-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  },
  earnings_surprise: {
    title: '业绩超预期快照',
    description: '按日期查看业绩超预期代理事件，支持多日对比、连续命中联动筛选，以及当前选中结果的导出和推送。',
    streakLabel: '连续命中',
    itemGroupTitle: '连续命中',
    nonStreakTitle: '其他命中',
    emptyHint: '可以切换日期，或先运行业绩超预期扫描脚本生成当日快照。',
    tabAccent: 'from-emerald-500/25 via-emerald-400/10 to-transparent border-emerald-400/40 text-emerald-100',
    countBadgeClass: 'border-emerald-400/25 bg-emerald-500/10 text-emerald-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  },
  hundred_day_high_with_earnings: {
    title: '新高且业绩快照',
    description: '按日期查看“百日新高 ∩ 业绩超预期”交集信号，支持多日对比、连续命中联动筛选，以及当前选中结果的导出和推送。',
    streakLabel: '连续交集',
    itemGroupTitle: '连续交集',
    nonStreakTitle: '其他交集',
    emptyHint: '可以先生成百日新高与业绩超预期快照，再在这里查看两类信号的交集。',
    tabAccent: 'from-cyan-500/25 via-sky-400/10 to-transparent border-cyan-400/40 text-cyan-100',
    countBadgeClass: 'border-cyan-400/25 bg-cyan-500/10 text-cyan-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  },
};

const EXTRA_SIGNAL_TYPE_META: Record<string, SignalTypeMeta> = {
  commodity_beneficiary__optical_fiber: {
    title: '光纤涨价快照',
    description: '查看按日期沉淀的光纤涨价受益候选池，重点展示 subtheme、产业链角色、传导方向和业绩释放概率。',
    streakLabel: '连续入池',
    itemGroupTitle: '连续入池',
    nonStreakTitle: '其他入池',
    emptyHint: '可以先运行商品涨价专题快照脚本，生成 optical_fiber 当日专题快照。',
    tabAccent: 'from-sky-500/25 via-cyan-400/10 to-transparent border-sky-400/40 text-sky-100',
    countBadgeClass: 'border-sky-400/25 bg-sky-500/10 text-sky-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  },
  commodity_beneficiary__memory: {
    title: '内存涨价快照',
    description: '查看按日期沉淀的内存涨价受益候选池，重点展示芯片设计、模组封测、分销和下游整机的结构化区分。',
    streakLabel: '连续入池',
    itemGroupTitle: '连续入池',
    nonStreakTitle: '其他入池',
    emptyHint: '可以先运行商品涨价专题快照脚本，生成 memory 当日专题快照。',
    tabAccent: 'from-fuchsia-500/25 via-rose-400/10 to-transparent border-fuchsia-400/40 text-fuchsia-100',
    countBadgeClass: 'border-fuchsia-400/25 bg-fuchsia-500/10 text-fuchsia-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  },
  commodity_beneficiary__hard_disk: {
    title: '硬盘涨价快照',
    description: '查看按日期沉淀的硬盘涨价受益候选池，重点区分企业级存储、渠道分销和安防/整机需求侧。',
    streakLabel: '连续入池',
    itemGroupTitle: '连续入池',
    nonStreakTitle: '其他入池',
    emptyHint: '可以先运行商品涨价专题快照脚本，生成 hard_disk 当日专题快照。',
    tabAccent: 'from-orange-500/25 via-amber-400/10 to-transparent border-orange-400/40 text-orange-100',
    countBadgeClass: 'border-orange-400/25 bg-orange-500/10 text-orange-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  },
};

const DRAGON_HEAD_SIGNAL_META: Record<string, SignalTypeMeta> = {
  dragon_head_candidate: {
    title: '龙头专题快照',
    description: '查看按日期沉淀的高辨识度核心龙头候选池，重点展示 leader type、leader probability、辨识度、板块地位、相对强度、流动性和催化。',
    streakLabel: '连续入池',
    itemGroupTitle: '连续入池',
    nonStreakTitle: '其他入池',
    emptyHint: '可以先运行龙头专题快照脚本，生成 dragon_head_candidate 当日专题快照。',
    tabAccent: 'from-red-500/25 via-orange-400/10 to-transparent border-red-400/40 text-red-100',
    countBadgeClass: 'border-red-400/25 bg-red-500/10 text-red-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  },
};

const ALL_SIGNAL_TYPE_META: Record<string, SignalTypeMeta> = {
  ...SIGNAL_TYPE_META,
  ...EXTRA_SIGNAL_TYPE_META,
  ...DRAGON_HEAD_SIGNAL_META,
};

const BASE_SIGNAL_TYPE_OPTIONS = [
  { value: 'dragon_head_candidate', label: '龙头专题' },
  { value: 'hundred_day_high', label: '百日新高' },
  { value: 'earnings_surprise', label: '业绩超预期' },
  { value: 'hundred_day_high_with_earnings', label: '新高且业绩' },
  { value: 'commodity_beneficiary__optical_fiber', label: '光纤涨价' },
  { value: 'commodity_beneficiary__memory', label: '内存涨价' },
  { value: 'commodity_beneficiary__hard_disk', label: '硬盘涨价' },
];

function isCommoditySignalType(signalType: string): boolean {
  return signalType.startsWith('commodity_beneficiary__');
}

function isDragonHeadSignalType(signalType: string): boolean {
  return signalType === 'dragon_head_candidate';
}

function isBoardRecognizabilitySignalType(signalType: string): boolean {
  return signalType.startsWith('board_recognizability__');
}

function isFactorDrivenSignalType(signalType: string): boolean {
  return isCommoditySignalType(signalType) || isDragonHeadSignalType(signalType) || isBoardRecognizabilitySignalType(signalType);
}

function showsEventDate(signalType: string): boolean {
  return signalType === 'earnings_surprise' || signalType === 'hundred_day_high_with_earnings';
}

function buildBoardRecognizabilityMeta(label: string): SignalTypeMeta {
  return {
    title: `${label}快照`,
    description: `查看 ${label} TopN 结果，重点展示板块内排名、候选数量、来源信号以及历史命中情况。`,
    streakLabel: '连续入榜',
    itemGroupTitle: '连续入榜',
    nonStreakTitle: '其他入榜',
    emptyHint: '可以先运行板块辨识度快照任务，再回来查看各板块 TopN 结果。',
    tabAccent: 'from-teal-500/25 via-emerald-400/10 to-transparent border-teal-400/40 text-teal-100',
    countBadgeClass: 'border-teal-400/25 bg-teal-500/10 text-teal-200',
    activeCountBadgeClass: 'border-white/20 bg-white/10 text-white',
  };
}

const SignalsPage: React.FC = () => {
  const [signalType, setSignalType] = useState('hundred_day_high');
  const [dateMode, setDateMode] = useState<'single' | 'range'>('single');
  const [signalDate, setSignalDate] = useState(getTodayIsoDate);
  const [signalDateFrom, setSignalDateFrom] = useState(getTodayIsoDate);
  const [signalDateTo, setSignalDateTo] = useState(getTodayIsoDate);
  const [codeFilter, setCodeFilter] = useState('');
  const [appliedCodeFilter, setAppliedCodeFilter] = useState('');
  const [historyDays, setHistoryDays] = useState('180');
  const [sortBy, setSortBy] = useState<SortOption>('latestHighDesc');
  const [showOnlyStreak, setShowOnlyStreak] = useState(false);
  const [ytdFilter, setYtdFilter] = useState<YtdFilterOption>('all');
  const [recentEventDays, setRecentEventDays] = useState('0');
  const [activeLinkedCodes, setActiveLinkedCodes] = useState<string[]>([]);
  const [streakGroupBy, setStreakGroupBy] = useState<StreakGroupBy>('none');
  const [compareSummaryExpanded, setCompareSummaryExpanded] = useState(false);
  const [currentPage, setCurrentPage] = useState(1);
  const [actionStatus, setActionStatus] = useState<ActionStatus>(null);
  const [isPushingSelection, setIsPushingSelection] = useState(false);
  const pageSize = 12;
  const [listData, setListData] = useState<SignalSnapshotListResponse | null>(null);
  const [countData, setCountData] = useState<SignalSnapshotCountsResponse | null>(null);
  const [selectedItem, setSelectedItem] = useState<SignalSnapshotListItem | null>(null);
  const [historyData, setHistoryData] = useState<SignalSnapshotHistoryResponse | null>(null);
  const [isLoadingList, setIsLoadingList] = useState(false);
  const [isLoadingHistory, setIsLoadingHistory] = useState(false);
  const [pageError, setPageError] = useState<ParsedApiError | null>(null);
  const listCacheRef = useRef<Map<string, SignalSnapshotListResponse>>(new Map());
  const historyCacheRef = useRef<Map<string, SignalSnapshotHistoryResponse>>(new Map());
  const listRequestIdRef = useRef(0);
  const historyRequestIdRef = useRef(0);
  const listAbortControllerRef = useRef<AbortController | null>(null);
  const historyAbortControllerRef = useRef<AbortController | null>(null);
  const countAbortControllerRef = useRef<AbortController | null>(null);

  const boardRecognizabilityOptions = useMemo(
    () => (countData?.items ?? [])
      .filter((item) => item.group === 'board_recognizability' || isBoardRecognizabilitySignalType(item.signalType))
      .map((item) => ({
        value: item.signalType,
        label: item.displayLabel || item.signalType,
        total: item.total ?? 0,
      }))
      .sort((left, right) => right.total - left.total || left.label.localeCompare(right.label)),
    [countData],
  );

  const signalTypeMetaMap = useMemo(() => {
    const dynamicMeta = boardRecognizabilityOptions.reduce<Record<string, SignalTypeMeta>>((acc, option) => {
      acc[option.value] = buildBoardRecognizabilityMeta(option.label);
      return acc;
    }, {});
    return {
      ...ALL_SIGNAL_TYPE_META,
      ...dynamicMeta,
    };
  }, [boardRecognizabilityOptions]);

  const signalTypeOptions = useMemo(
    () => [
      ...BASE_SIGNAL_TYPE_OPTIONS,
      ...boardRecognizabilityOptions.map((option) => ({ value: option.value, label: option.label })),
    ],
    [boardRecognizabilityOptions],
  );

  const signalMeta = signalTypeMetaMap[signalType] ?? signalTypeMetaMap.hundred_day_high;

  useEffect(() => {
    document.title = `${signalMeta.title} - DSA`;
  }, [signalMeta.title]);

  const requestedCodes = useMemo(() => {
    if (activeLinkedCodes.length > 0) {
      return activeLinkedCodes;
    }
    if (appliedCodeFilter) {
      return [appliedCodeFilter];
    }
    return undefined;
  }, [activeLinkedCodes, appliedCodeFilter]);

  const signalTypeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of countData?.items ?? []) {
      counts.set(item.signalType, item.total ?? 0);
    }
    return counts;
  }, [countData]);

  const loadCounts = async () => {
    countAbortControllerRef.current?.abort();
    const abortController = new AbortController();
    countAbortControllerRef.current = abortController;
    try {
      const response = await signalsApi.getSnapshotCounts(
        {
          signalDate: dateMode === 'single' ? signalDate : undefined,
          signalDateFrom: dateMode === 'range' ? signalDateFrom : undefined,
          signalDateTo: dateMode === 'range' ? signalDateTo : undefined,
          code: undefined,
          codes: requestedCodes,
        },
        { signal: abortController.signal },
      );
      setCountData(response);
    } catch (error) {
      if (!isRequestCanceled(error)) {
        setCountData(null);
      }
    } finally {
      if (countAbortControllerRef.current === abortController) {
        countAbortControllerRef.current = null;
      }
    }
  };

  const loadList = async (options?: {
    force?: boolean;
    requestedPage?: number;
    requestedCodes?: string[];
  }) => {
    const requestId = ++listRequestIdRef.current;
    listAbortControllerRef.current?.abort();
    const abortController = new AbortController();
    listAbortControllerRef.current = abortController;
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
        historyRequestIdRef.current += 1;
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
      }, { signal: abortController.signal });
      if (requestId !== listRequestIdRef.current) {
        return;
      }
      historyRequestIdRef.current += 1;
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
      if (isRequestCanceled(error)) {
        return;
      }
      if (requestId !== listRequestIdRef.current) {
        return;
      }
      historyRequestIdRef.current += 1;
      setPageError(getParsedApiError(error));
      setListData(null);
      setSelectedItem(null);
      setHistoryData(null);
    } finally {
      if (requestId === listRequestIdRef.current) {
        if (listAbortControllerRef.current === abortController) {
          listAbortControllerRef.current = null;
        }
        setIsLoadingList(false);
      }
    }
  };

  const loadHistory = async (item: SignalSnapshotListItem, force = false) => {
    const requestId = ++historyRequestIdRef.current;
    historyAbortControllerRef.current?.abort();
    const abortController = new AbortController();
    historyAbortControllerRef.current = abortController;
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
      const response = await signalsApi.getHistory(signalType, item.code, { days, limit }, { signal: abortController.signal });
      if (requestId !== historyRequestIdRef.current) {
        return;
      }
      historyCacheRef.current.set(cacheKey, response);
      setHistoryData(response);
      setPageError(null);
    } catch (error) {
      if (isRequestCanceled(error)) {
        return;
      }
      if (requestId !== historyRequestIdRef.current) {
        return;
      }
      setPageError(getParsedApiError(error));
      setHistoryData(null);
    } finally {
      if (requestId === historyRequestIdRef.current) {
        if (historyAbortControllerRef.current === abortController) {
          historyAbortControllerRef.current = null;
        }
        setIsLoadingHistory(false);
      }
    }
  };

  const filteredAndSortedItems = useMemo(() => {
    const baseItems = listData?.items ?? [];
    const streakFiltered = showOnlyStreak
      ? baseItems.filter((item) => item.isConsecutiveSignal)
      : baseItems;
    const ytdFiltered = streakFiltered.filter((item) => {
      if (ytdFilter === 'all') {
        return true;
      }
      const value = item.ytdReturnPct;
      if (value == null) {
        return false;
      }
      if (ytdFilter === 'gt0') {
        return value > 0;
      }
      if (ytdFilter === 'gt20') {
        return value > 20;
      }
      if (ytdFilter === 'gt50') {
        return value > 50;
      }
      return true;
    });
    const recentEventDaysValue = Number.parseInt(recentEventDays, 10) || 0;
    const eventFiltered = ytdFiltered.filter((item) => {
      if (signalType !== 'earnings_surprise' || recentEventDaysValue <= 0) {
        return true;
      }
      const eventDate = item.eventDate;
      const anchorDate = item.signalDate;
      const diffDays = diffDaysInclusive(eventDate || '', anchorDate || '');
      return diffDays != null && diffDays >= 0 && diffDays <= recentEventDaysValue;
    });
    const linkedFiltered = activeLinkedCodes.length > 0
      ? eventFiltered.filter((item) => activeLinkedCodes.includes(item.code))
      : eventFiltered;

    return [...linkedFiltered].sort((left, right) => {
      if (isBoardRecognizabilitySignalType(signalType) && sortBy === 'latestHighDesc') {
        return (left.boardRank ?? Number.POSITIVE_INFINITY) - (right.boardRank ?? Number.POSITIVE_INFINITY)
          || (right.previousHitCount ?? 0) - (left.previousHitCount ?? 0)
          || (right.totalMarketCapYi ?? 0) - (left.totalMarketCapYi ?? 0)
          || (right.latestHigh ?? 0) - (left.latestHigh ?? 0)
          || (left.code || '').localeCompare(right.code || '');
      }
      if (isFactorDrivenSignalType(signalType) && sortBy === 'latestHighDesc') {
        return (right.recognizabilityScore ?? 0) - (left.recognizabilityScore ?? 0)
          || (right.sectorLeadershipScore ?? 0) - (left.sectorLeadershipScore ?? 0)
          || (right.relativeStrengthScore ?? 0) - (left.relativeStrengthScore ?? 0)
          || (right.sustainedGrowthScore ?? 0) - (left.sustainedGrowthScore ?? 0)
          || (right.liquidityScore ?? 0) - (left.liquidityScore ?? 0)
          || (right.catalystScore ?? 0) - (left.catalystScore ?? 0)
          || (right.valuationScore ?? 0) - (left.valuationScore ?? 0)
          || (right.dividendScore ?? 0) - (left.dividendScore ?? 0)
          || (right.latestHigh ?? 0) - (left.latestHigh ?? 0)
          || (left.code || '').localeCompare(right.code || '');
      }
      switch (sortBy) {
        case 'previousHitCountDesc':
          return (right.previousHitCount ?? 0) - (left.previousHitCount ?? 0)
            || (right.latestHigh ?? 0) - (left.latestHigh ?? 0);
        case 'closeDesc':
          return (right.close ?? 0) - (left.close ?? 0)
            || (right.latestHigh ?? 0) - (left.latestHigh ?? 0);
        case 'ytdReturnDesc':
          return (right.ytdReturnPct ?? Number.NEGATIVE_INFINITY) - (left.ytdReturnPct ?? Number.NEGATIVE_INFINITY)
            || (right.latestHigh ?? 0) - (left.latestHigh ?? 0);
        case 'eventDateDesc':
          return (right.eventDate || '').localeCompare(left.eventDate || '')
            || (right.signalDate || '').localeCompare(left.signalDate || '');
        case 'signalDateDesc':
          return (right.signalDate || '').localeCompare(left.signalDate || '')
            || (right.eventDate || '').localeCompare(left.eventDate || '');
        case 'codeAsc':
          return (left.code || '').localeCompare(right.code || '');
        case 'latestHighDesc':
        default:
          return (right.latestHigh ?? 0) - (left.latestHigh ?? 0)
            || (right.close ?? 0) - (left.close ?? 0);
      }
    });
  }, [activeLinkedCodes, listData?.items, recentEventDays, showOnlyStreak, signalType, sortBy, ytdFilter]);

  useEffect(() => {
    void loadList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signalDate, signalDateFrom, signalDateTo, signalType, currentPage, dateMode, appliedCodeFilter, activeLinkedCodes.join(',')]);

  useEffect(() => {
    void loadCounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signalDate, signalDateFrom, signalDateTo, dateMode, appliedCodeFilter, activeLinkedCodes.join(',')]);

  useEffect(() => {
    setCompareSummaryExpanded(false);
  }, [signalDate, signalDateFrom, signalDateTo, dateMode]);

  useEffect(() => {
    return () => {
      listAbortControllerRef.current?.abort();
      historyAbortControllerRef.current?.abort();
      countAbortControllerRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (!filteredAndSortedItems.length) {
      historyRequestIdRef.current += 1;
      historyAbortControllerRef.current?.abort();
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
      historyRequestIdRef.current += 1;
      historyAbortControllerRef.current?.abort();
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
      ? `当前页${signalMeta.streakLabel} ${exportableItems.length} 条`
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
  const currentSignalTotal = signalTypeCounts.get(signalType) ?? 0;
  const currentYtdSummary = useMemo(
    () => summarizeYtd(filteredAndSortedItems),
    [filteredAndSortedItems],
  );
  const activeCompareHead = listData?.compareSummary?.[0] ?? null;
  const secondarySummaryItems = useMemo(() => {
    const items: Array<{ label: string; value: string }> = [
      { label: '范围', value: dateLabel },
      { label: '总命中', value: String(currentSignalTotal) },
      { label: '当前筛选', value: String(filteredAndSortedItems.length) },
    ];
    if (currentYtdSummary.avg != null) {
      items.push({ label: '平均 YTD', value: formatPct(currentYtdSummary.avg) });
    }
    if (currentYtdSummary.median != null) {
      items.push({ label: '中位 YTD', value: formatPct(currentYtdSummary.median) });
    }
    if (signalType === 'earnings_surprise' && (Number.parseInt(recentEventDays, 10) || 0) > 0) {
      items.push({ label: '公告过滤', value: `近 ${Number.parseInt(recentEventDays, 10)} 天` });
    }
    if (activeCompareHead?.avgYtdReturnPct != null) {
      items.push({ label: '对比首日均值', value: formatPct(activeCompareHead.avgYtdReturnPct) });
    }
    return items;
  }, [
    activeCompareHead?.avgYtdReturnPct,
    currentSignalTotal,
    currentYtdSummary.avg,
    currentYtdSummary.median,
    dateLabel,
    filteredAndSortedItems.length,
    recentEventDays,
    signalType,
  ]);

  const handleSignalTypeChange = (nextSignalType: string) => {
    setSignalType(nextSignalType);
    setSortBy('latestHighDesc');
    setCurrentPage(1);
    setSelectedItem(null);
    setHistoryData(null);
    setShowOnlyStreak(false);
    setYtdFilter('all');
    setRecentEventDays('0');
    setActiveLinkedCodes([]);
    setCodeFilter('');
    setAppliedCodeFilter('');
    setActionStatus(null);
  };

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
          title={signalMeta.title}
          description={signalMeta.description}
          actions={(
            <>
              <div className="grid min-w-[360px] grid-cols-1 gap-2 rounded-[28px] border border-border/60 bg-card/55 p-2 shadow-soft-card sm:grid-cols-3">
                {[
                  { value: 'hundred_day_high', label: '百日新高' },
                  { value: 'earnings_surprise', label: '业绩超预期' },
                  { value: 'hundred_day_high_with_earnings', label: '新高且业绩' },
                ].map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => handleSignalTypeChange(option.value)}
                    aria-label={`signal-type-tab-${option.value}`}
                    className={`group relative overflow-hidden rounded-2xl border px-4 py-3 text-left transition-all ${
                      signalType === option.value
                        ? `bg-gradient-to-br ${signalTypeMetaMap[option.value]?.tabAccent || signalMeta.tabAccent} shadow-lg`
                        : 'border-transparent bg-transparent text-secondary-text hover:border-border/50 hover:bg-hover hover:text-foreground'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-sm font-semibold">{option.label}</div>
                        <div className="mt-1 text-[11px] opacity-80">
                          {signalTypeMetaMap[option.value]?.streakLabel || '信号'}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {signalType === option.value ? (
                          <>
                            <Badge
                              variant="history"
                              className={signalTypeMetaMap[option.value]?.activeCountBadgeClass}
                              aria-label={`signal-type-filtered-count-${option.value}`}
                            >
                              筛 {filteredAndSortedItems.length}
                            </Badge>
                            <Badge
                              variant="history"
                              className={signalTypeMetaMap[option.value]?.activeCountBadgeClass}
                              aria-label={`signal-type-total-count-${option.value}`}
                            >
                              总 {signalTypeCounts.get(option.value) ?? '--'}
                            </Badge>
                          </>
                        ) : (
                          <Badge
                            variant="default"
                            className={signalTypeMetaMap[option.value]?.countBadgeClass}
                            aria-label={`signal-type-total-count-${option.value}`}
                          >
                            {signalTypeCounts.get(option.value) ?? '--'}
                          </Badge>
                        )}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
              <div className="grid min-w-[360px] grid-cols-1 gap-2 rounded-[28px] border border-border/50 bg-card/35 p-2 shadow-soft-card sm:grid-cols-3">
                {BASE_SIGNAL_TYPE_OPTIONS.filter((option) => isCommoditySignalType(option.value) || isDragonHeadSignalType(option.value)).map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => handleSignalTypeChange(option.value)}
                    aria-label={`signal-type-tab-${option.value}`}
                    className={`group relative overflow-hidden rounded-2xl border px-4 py-3 text-left transition-all ${
                      signalType === option.value
                        ? `bg-gradient-to-br ${signalTypeMetaMap[option.value]?.tabAccent || signalMeta.tabAccent} shadow-lg`
                        : 'border-transparent bg-transparent text-secondary-text hover:border-border/50 hover:bg-hover hover:text-foreground'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-sm font-semibold">{option.label}</div>
                        <div className="mt-1 text-[11px] opacity-80">
                          {signalTypeMetaMap[option.value]?.streakLabel || '专题'}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        {signalType === option.value ? (
                          <>
                            <Badge
                              variant="history"
                              className={signalTypeMetaMap[option.value]?.activeCountBadgeClass}
                              aria-label={`signal-type-filtered-count-${option.value}`}
                            >
                              筛 {filteredAndSortedItems.length}
                            </Badge>
                            <Badge
                              variant="history"
                              className={signalTypeMetaMap[option.value]?.activeCountBadgeClass}
                              aria-label={`signal-type-total-count-${option.value}`}
                            >
                              总 {signalTypeCounts.get(option.value) ?? '--'}
                            </Badge>
                          </>
                        ) : (
                          <Badge
                            variant="default"
                            className={signalTypeMetaMap[option.value]?.countBadgeClass}
                            aria-label={`signal-type-total-count-${option.value}`}
                          >
                            {signalTypeCounts.get(option.value) ?? '--'}
                          </Badge>
                        )}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
              {boardRecognizabilityOptions.length > 0 ? (
                <div className="grid min-w-[360px] grid-cols-1 gap-2 rounded-[28px] border border-border/50 bg-card/30 p-2 shadow-soft-card sm:grid-cols-3">
                  {boardRecognizabilityOptions.map((option) => (
                    <button
                      key={option.value}
                      type="button"
                      onClick={() => handleSignalTypeChange(option.value)}
                      aria-label={`signal-type-tab-${option.value}`}
                      className={`group relative overflow-hidden rounded-2xl border px-4 py-3 text-left transition-all ${
                        signalType === option.value
                          ? `bg-gradient-to-br ${signalTypeMetaMap[option.value]?.tabAccent || signalMeta.tabAccent} shadow-lg`
                          : 'border-transparent bg-transparent text-secondary-text hover:border-border/50 hover:bg-hover hover:text-foreground'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <div className="min-w-0">
                          <div className="text-sm font-semibold">{option.label}</div>
                          <div className="mt-1 text-[11px] opacity-80">
                            {signalTypeMetaMap[option.value]?.streakLabel || '板块辨识度'}
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          {signalType === option.value ? (
                            <>
                              <Badge
                                variant="history"
                                className={signalTypeMetaMap[option.value]?.activeCountBadgeClass}
                                aria-label={`signal-type-filtered-count-${option.value}`}
                              >
                                筛 {filteredAndSortedItems.length}
                              </Badge>
                              <Badge
                                variant="history"
                                className={signalTypeMetaMap[option.value]?.activeCountBadgeClass}
                                aria-label={`signal-type-total-count-${option.value}`}
                              >
                                总 {signalTypeCounts.get(option.value) ?? '--'}
                              </Badge>
                            </>
                          ) : (
                            <Badge
                              variant="default"
                              className={signalTypeMetaMap[option.value]?.countBadgeClass}
                              aria-label={`signal-type-total-count-${option.value}`}
                            >
                              {signalTypeCounts.get(option.value) ?? '--'}
                            </Badge>
                          )}
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              ) : null}
              <Select
                value={signalType}
                onChange={(value) => handleSignalTypeChange(value)}
                options={signalTypeOptions}
                className="min-w-[160px]"
                aria-label="signal-type"
              />
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
                className={`${SIGNALS_INPUT_CLASS} min-w-[160px]`}
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
                    className={`${SIGNALS_INPUT_CLASS} min-w-[160px]`}
                    aria-label="signal-date-from"
                  />
                  <input
                    type="date"
                    value={signalDateTo}
                    onChange={(e) => setSignalDateTo(e.target.value)}
                    className={`${SIGNALS_INPUT_CLASS} min-w-[160px]`}
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
                className={`${SIGNALS_INPUT_CLASS} min-w-[160px]`}
                aria-label="stock-code-filter"
              />
              <Select
                value={sortBy}
                onChange={(value) => setSortBy(value as SortOption)}
                options={[
                  { value: 'latestHighDesc', label: '按最新 high' },
                  { value: 'previousHitCountDesc', label: '按历史命中次数' },
                  { value: 'closeDesc', label: '按收盘价' },
                  { value: 'ytdReturnDesc', label: '按年内涨幅' },
                  { value: 'eventDateDesc', label: '按事件日期' },
                  { value: 'signalDateDesc', label: '按信号日期' },
                  { value: 'codeAsc', label: '按代码' },
                ]}
                className="min-w-[180px]"
                aria-label="sort-mode"
              />
              <Select
                value={ytdFilter}
                onChange={(value) => {
                  setYtdFilter(value as YtdFilterOption);
                  setActionStatus(null);
                }}
                options={[
                  { value: 'all', label: 'YTD 全部' },
                  { value: 'gt0', label: 'YTD > 0%' },
                  { value: 'gt20', label: 'YTD > 20%' },
                  { value: 'gt50', label: 'YTD > 50%' },
                ]}
                className="min-w-[160px]"
                aria-label="ytd-filter"
              />
              {signalType === 'earnings_surprise' ? (
                <input
                  type="number"
                  min={0}
                  max={365}
                  value={recentEventDays}
                  onChange={(e) => {
                    setRecentEventDays(e.target.value);
                    setActionStatus(null);
                  }}
                  placeholder="近N天公告"
                  className={`${SIGNALS_INPUT_CLASS} min-w-[140px]`}
                  aria-label="recent-event-days"
                />
              ) : null}
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
                只看{signalMeta.streakLabel}
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

        <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-border/60 bg-card/35 px-4 py-3 text-xs shadow-soft-card">
          {secondarySummaryItems.map((item) => (
            <div
              key={`${item.label}-${item.value}`}
              className="flex items-center gap-2 rounded-full border border-border/50 bg-card/60 px-3 py-1.5"
            >
              <span className="text-muted-text">{item.label}</span>
              <span className="font-medium text-foreground">{item.value}</span>
            </div>
          ))}
        </div>

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
                  <Badge variant="info">{signalMeta.streakLabel} {continuousCount}</Badge>
                  {showOnlyStreak ? <Badge variant="warning">已启用{signalMeta.streakLabel}过滤</Badge> : null}
                  {ytdFilter !== 'all' ? <Badge variant="warning">已启用 {ytdFilter === 'gt50' ? 'YTD > 50%' : ytdFilter === 'gt20' ? 'YTD > 20%' : 'YTD > 0%'} 过滤</Badge> : null}
                  {signalType === 'earnings_surprise' && (Number.parseInt(recentEventDays, 10) || 0) > 0 ? (
                    <Badge variant="warning">已启用近 {Number.parseInt(recentEventDays, 10)} 天公告过滤</Badge>
                  ) : null}
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
                            <div className="text-secondary-text">{signalMeta.streakLabel} {item.continuousCount}</div>
                            <div className="text-secondary-text">新增 {item.addedCount} / 掉队 {item.droppedCount}</div>
                            <div className="text-secondary-text">均值 YTD {formatPct(item.avgYtdReturnPct)}</div>
                            <div className="text-secondary-text">中位 YTD {formatPct(item.medianYtdReturnPct)}</div>
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
                          只看全部{signalMeta.streakLabel}
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
                      <p className="text-xs font-medium text-muted-text">{signalMeta.itemGroupTitle}</p>
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
                                  {isBoardRecognizabilitySignalType(signalType) && item.boardRank != null ? <Badge variant="warning">#{item.boardRank}</Badge> : null}
                                  {isBoardRecognizabilitySignalType(signalType) && item.boardCandidateCount != null ? <Badge variant="default">板块候选 {item.boardCandidateCount}</Badge> : null}
                                  {item.themeLabel ? <Badge variant="warning">{item.themeLabel}</Badge> : null}
                                  {isCommoditySignalType(signalType) && item.subthemeKey ? <Badge variant="default">{item.subthemeKey}</Badge> : null}
                                  {isCommoditySignalType(signalType) && item.chainRole ? <Badge variant="history">{item.chainRole}</Badge> : null}
                                  {isCommoditySignalType(signalType) && item.earningsReleaseProbability ? <Badge variant="success">{item.earningsReleaseProbability}</Badge> : null}
                                  {isDragonHeadSignalType(signalType) && item.leaderType ? <Badge variant="warning">{item.leaderType}</Badge> : null}
                                  {isDragonHeadSignalType(signalType) && item.leaderProbability ? <Badge variant="success">{item.leaderProbability}</Badge> : null}
                                  <Badge variant="success">{signalMeta.streakLabel}</Badge>
                                </div>
                                {showsEventDate(signalType) ? (
                                  <p className="mt-1 text-xs text-secondary-text">
                                    事件日期 {item.eventDate || '--'}
                                  </p>
                                ) : null}
                                <p className="mt-2 line-clamp-2 text-sm text-secondary-text">
                                  {item.reasonSummary || '当前未生成归因摘要'}
                                </p>
                                {isCommoditySignalType(signalType) ? (
                                  <p className="mt-1 text-xs text-secondary-text">
                                    {item.directness || '--'} | {item.passThroughDirection || '--'} | {item.matchedExampleBucket || '--'}
                                  </p>
                                ) : isDragonHeadSignalType(signalType) ? (
                                  <p className="mt-1 text-xs text-secondary-text">
                                    {item.leaderType || '--'} | {item.leaderProbability || '--'} | recognizability {item.recognizabilityScore ?? '--'}
                                  </p>
                                ) : isBoardRecognizabilitySignalType(signalType) ? (
                                  <p className="mt-1 text-xs text-secondary-text">
                                    {item.boardName || '--'} | 来源 {item.sourceSignalType || '--'} {item.sourceSignalDate || '--'} | 市值 {item.totalMarketCapYi ?? '--'} 亿
                                  </p>
                                ) : null}
                              </div>
                              <div className="shrink-0 text-right text-xs text-muted-text">
                                <div>high {formatPrice(item.latestHigh)}</div>
                                <div>close {formatPrice(item.close)}</div>
                                <div>YTD {formatPct(item.ytdReturnPct)}</div>
                              </div>
                            </div>
                          </button>
                        );
                      })}
                    </div>
                  ) : null}
                  {groupedItems.nonConsecutive.length > 0 ? (
                    <div className="space-y-2">
                      <p className="text-xs font-medium text-muted-text">{signalMeta.nonStreakTitle}</p>
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
                                  {isBoardRecognizabilitySignalType(signalType) && item.boardRank != null ? <Badge variant="warning">#{item.boardRank}</Badge> : null}
                                  {isBoardRecognizabilitySignalType(signalType) && item.boardCandidateCount != null ? <Badge variant="default">板块候选 {item.boardCandidateCount}</Badge> : null}
                                  {item.themeLabel ? <Badge variant="warning">{item.themeLabel}</Badge> : null}
                                  {isCommoditySignalType(signalType) && item.subthemeKey ? <Badge variant="default">{item.subthemeKey}</Badge> : null}
                                  {isCommoditySignalType(signalType) && item.chainRole ? <Badge variant="history">{item.chainRole}</Badge> : null}
                                  {isCommoditySignalType(signalType) && item.earningsReleaseProbability ? <Badge variant="success">{item.earningsReleaseProbability}</Badge> : null}
                                  {isDragonHeadSignalType(signalType) && item.leaderType ? <Badge variant="warning">{item.leaderType}</Badge> : null}
                                  {isDragonHeadSignalType(signalType) && item.leaderProbability ? <Badge variant="success">{item.leaderProbability}</Badge> : null}
                                </div>
                                {showsEventDate(signalType) ? (
                                  <p className="mt-1 text-xs text-secondary-text">
                                    事件日期 {item.eventDate || '--'}
                                  </p>
                                ) : null}
                                <p className="mt-2 line-clamp-2 text-sm text-secondary-text">
                                  {item.reasonSummary || '当前未生成归因摘要'}
                                </p>
                                {isCommoditySignalType(signalType) ? (
                                  <p className="mt-1 text-xs text-secondary-text">
                                    {item.directness || '--'} | {item.passThroughDirection || '--'} | {item.matchedExampleBucket || '--'}
                                  </p>
                                ) : isDragonHeadSignalType(signalType) ? (
                                  <p className="mt-1 text-xs text-secondary-text">
                                    {item.leaderType || '--'} | {item.leaderProbability || '--'} | recognizability {item.recognizabilityScore ?? '--'}
                                  </p>
                                ) : isBoardRecognizabilitySignalType(signalType) ? (
                                  <p className="mt-1 text-xs text-secondary-text">
                                    {item.boardName || '--'} | 来源 {item.sourceSignalType || '--'} {item.sourceSignalDate || '--'} | 市值 {item.totalMarketCapYi ?? '--'} 亿
                                  </p>
                                ) : null}
                              </div>
                              <div className="shrink-0 text-right text-xs text-muted-text">
                                <div>high {formatPrice(item.latestHigh)}</div>
                                <div>close {formatPrice(item.close)}</div>
                                <div>YTD {formatPct(item.ytdReturnPct)}</div>
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
                description={`可以关闭“只看${signalMeta.streakLabel}”，或调整排序和股票代码过滤条件。`}
              />
            ) : (
              <EmptyState
                title="当天没有命中快照"
                description={signalMeta.emptyHint}
              />
            )}
          </Card>

          <div className="space-y-6">
            <Card
              title={selectedItem ? `${selectedItem.name || selectedItem.code} · 历史观察` : '历史观察'}
              subtitle={`${signalMeta.streakLabel} / 近似回撤`}
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
                  className={`${SIGNALS_INPUT_CLASS} w-28`}
                />
              </div>

              {isLoadingHistory && selectedItem ? (
                <div className="space-y-3">
                  <div className="h-12 animate-pulse rounded-xl bg-card/60" />
                  <div className="h-12 animate-pulse rounded-xl bg-card/60" />
                </div>
              ) : historyData && currentHistoryItem ? (
                <div className="space-y-4 text-sm">
                  <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                    <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                      <p className="text-xs text-muted-text">当前{signalMeta.streakLabel}</p>
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
                    <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                      <p className="text-xs text-muted-text">信号日年内涨幅</p>
                      <p className="mt-1 text-xl font-semibold text-foreground">
                        {formatPct(currentHistoryItem.ytdReturnPct)}
                      </p>
                      <p className="mt-1 text-xs text-secondary-text">
                        {currentHistoryItem.yearStartDate || '--'} · close {formatPrice(currentHistoryItem.yearStartClose)}
                      </p>
                    </div>
                  </div>

                  {isCommoditySignalType(signalType) ? (
                    <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                      <p className="text-xs text-muted-text">商品专题结构</p>
                      <div className="mt-3 grid gap-3 sm:grid-cols-2">
                        <div>
                          <p className="text-xs font-medium text-muted-text">Subtheme</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.subthemeKey || '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Chain Role</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.chainRole || '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Pass Through</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.passThroughDirection || '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Earnings Release Probability</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.earningsReleaseProbability || '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Directness</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.directness || '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Matched Example</p>
                          <p className="mt-1 text-secondary-text">
                            {currentHistoryItem.matchedExampleName || '--'} / {currentHistoryItem.matchedExampleBucket || '--'}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Recognizability</p>
                          <p className="mt-1 text-secondary-text">
                            {currentHistoryItem.recognizabilityScore ?? '--'} / logic {currentHistoryItem.logicConsensusScore ?? '--'} / capital {currentHistoryItem.capitalConsensusScore ?? '--'}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Sustained Growth</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.sustainedGrowthScore ?? '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Liquidity</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.liquidityScore ?? '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Valuation</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.valuationScore ?? '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Dividend</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.dividendScore ?? '--'}</p>
                        </div>
                      </div>
                    </div>
                  ) : isDragonHeadSignalType(signalType) ? (
                    <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                      <p className="text-xs text-muted-text">龙头结构</p>
                      <div className="mt-3 grid gap-3 sm:grid-cols-2">
                        <div>
                          <p className="text-xs font-medium text-muted-text">Leader Type</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.leaderType || '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Leader Probability</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.leaderProbability || '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Recognizability</p>
                          <p className="mt-1 text-secondary-text">
                            {currentHistoryItem.recognizabilityScore ?? '--'} / logic {currentHistoryItem.logicConsensusScore ?? '--'} / capital {currentHistoryItem.capitalConsensusScore ?? '--'}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Sector Leadership</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.sectorLeadershipScore ?? '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Relative Strength</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.relativeStrengthScore ?? '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Liquidity</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.liquidityScore ?? '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Catalyst</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.catalystScore ?? '--'}</p>
                        </div>
                      </div>
                    </div>
                  ) : isBoardRecognizabilitySignalType(signalType) ? (
                    <div className="rounded-2xl border border-border/60 bg-card/40 p-4">
                      <p className="text-xs text-muted-text">Board Recognizability</p>
                      <div className="mt-3 grid gap-3 sm:grid-cols-2">
                        <div>
                          <p className="text-xs font-medium text-muted-text">Board</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.boardName || '--'}</p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Board Rank</p>
                          <p className="mt-1 text-secondary-text">
                            {currentHistoryItem.boardRank ?? '--'} / {currentHistoryItem.boardCandidateCount ?? '--'}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Source Signal</p>
                          <p className="mt-1 text-secondary-text">
                            {currentHistoryItem.sourceSignalType || '--'} / {currentHistoryItem.sourceSignalDate || '--'}
                          </p>
                        </div>
                        <div>
                          <p className="text-xs font-medium text-muted-text">Market Cap (Yi)</p>
                          <p className="mt-1 text-secondary-text">{currentHistoryItem.totalMarketCapYi ?? '--'}</p>
                        </div>
                      </div>
                    </div>
                  ) : null}

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
                              high {formatPrice(item.latestHigh)} | close {formatPrice(item.close)}
                            </p>
                            <p className="text-xs text-secondary-text">
                              YTD {formatPct(item.ytdReturnPct)}
                            </p>
                          </div>
                          <div className="flex items-center gap-2">
                            {isBoardRecognizabilitySignalType(signalType) && item.boardRank != null ? (
                              <Badge variant="history">#{item.boardRank}</Badge>
                            ) : null}
                            {item.themeLabel ? <Badge variant="history">{item.themeLabel}</Badge> : null}
                          </div>
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
