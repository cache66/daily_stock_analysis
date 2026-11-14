import apiClient from './index';

export type WorkbenchSectionStatus = 'ok' | 'pending' | 'limited' | 'missing';

export type WorkbenchMarket = {
  status: WorkbenchSectionStatus;
  as_of?: string;
  universe?: number;
  median_ret20?: number;
  above_ma20_ratio?: number;
  evidence?: string;
  reason?: string;
};

export type WorkbenchMethodSummary = {
  id: string;
  name: string;
  enabled: boolean;
  question?: string;
  horizon?: string;
  confirm_rule?: string;
  invalid_rule?: string;
  default_daily: boolean;
  count: number;
  new_today: number;
  tracking: number;
  confirming: number;
  invalid: number;
  codes: string[];
};

export type WorkbenchIntersection = {
  id: string;
  name: string;
  methods: string[];
  rule: string;
  enabled: boolean;
  count: number;
  codes: string[];
};

export type WorkbenchSectorRow = {
  industry: string;
  limit_up: number;
  broken: number;
  board_change?: number | null;
  up?: number | null;
  down?: number | null;
  leader?: string | null;
  leader_change?: number | null;
};

export type WorkbenchReviewItem = {
  code: string;
  name: string;
  change_pct?: number | null;
  price?: number | null;
  limit_price?: number | null;
  seal_amount?: number | null;
  boards?: number | null;
  break_count?: number | null;
  first_seal?: string | null;
  limit_stat?: string | null;
  industry?: string | null;
};

export type WorkbenchMarketReview = {
  as_of: string;
  status: string;
  source?: string;
  reason?: string;
  generated_at?: string;
  summary: {
    limit_up?: number;
    broken?: number;
    limit_down?: number;
    seal_rate?: number | null;
    highest_boards?: number;
    seal_amount_total_yi?: number | null;
  };
  ladder: Array<{ boards: number; label: string; count: number; names: string[] }>;
  limit_up: WorkbenchReviewItem[];
  broken: WorkbenchReviewItem[];
  industry_limit_up: Array<{ industry: string; count: number; share: number }>;
  industry_broken: Array<{ industry: string; count: number; share: number }>;
  sectors: WorkbenchSectorRow[];
  limitations: string[];
};

export type WorkbenchBrokenRow = {
  code: string;
  name?: string | null;
  industry?: string | null;
  count: number;
  last_date?: string | null;
  last_break_count?: number | null;
  last_change_pct?: number | null;
  close_series: Array<number | null>;
  dates: string[];
  cum_change?: number | null;
  today_change_pct?: number | null;
  main_net?: number | null;
  main_ratio?: number | null;
  super_net?: number | null;
  big_net?: number | null;
  mid_net?: number | null;
  small_net?: number | null;
  main_net_5d?: number | null;
  main_ratio_5d?: number | null;
  fund_available?: boolean;
  tick?: WorkbenchTickFlow;
};

export type WorkbenchFollowupRow = {
  code: string;
  name?: string | null;
  industry?: string | null;
  break_date: string;
  break_count?: number | null;
  change_pct?: number | null;
  price?: number | null;
  limit_price?: number | null;
  amount?: number | null;
  turnover?: number | null;
  limit_stat?: string | null;
  base_close?: number | null;
  funds: {
    source?: string;
    main?: number | null;
    main_ratio?: number | null;
    super?: number | null;
    big?: number | null;
    mid?: number | null;
    small?: number | null;
    retail?: number | null;
    available?: boolean;
  };
  followup: {
    days: string[];
    t1_close?: number | null;
    t2_close?: number | null;
    latest_close?: number | null;
    t1_ret?: number | null;
    t2_ret?: number | null;
    latest_ret?: number | null;
    max_close?: number | null;
    recovered?: boolean;
    held?: boolean;
  };
  label: string;
  amount_ratio?: number | null;
  /** 近 10 个交易日触板天数（封板 + 炸板，同日只算 1 次） */
  touch10?: number;
  seal10?: number;
  break10?: number;
  seal_rate?: number | null;
  /** 炸板日所属行业的封板家数，≥7 视为板块过热 */
  industry_limit?: number;
  break_window?: {
    status?: string;
    method?: string;
    /** 炸板瞬间（首次跌破涨停价后 5 分钟）成交额占全天比例 */
    moment_ratio?: number | null;
    moment_amount?: number | null;
    /** 开板至收盘累计占比（旧口径，仅参考） */
    open_ratio?: number | null;
    open_amount?: number | null;
    break_time?: string;
    /** 炸板后的超大单/大单净额（仅当日分笔可得） */
    super_net?: number | null;
    big_net?: number | null;
    seal_time?: string;
  };
  score?: number;
  prob?: number;
  grade?: string;
  score_reasons?: string[];
  score_missing?: string[];
  tick?: WorkbenchTickFlow;
};

export type WorkbenchTouchWatch = {
  code: string;
  name?: string | null;
  industry?: string | null;
  touch: number;
  seal: number;
  break: number;
  seal_rate?: number | null;
  last_date?: string | null;
  last_kind?: string | null;
};

export type WorkbenchFollowupGroup = {
  break_date: string;
  count: number;
  big_amount: number;
  rows: WorkbenchFollowupRow[];
};

export type WorkbenchBrokenFollowup = {
  schema?: number;
  as_of: string;
  days: number;
  dates?: string[];
  per_day?: number;
  status: string;
  reason?: string;
  generated_at?: string;
  source?: string;
  summary: {
    events?: number;
    groups?: number;
    supported?: number;
    dumped?: number;
    recovered?: number;
    pending?: number;
    labels?: Record<string, number>;
    grades?: Record<string, number>;
    repeat_touch?: number;
    first_touch?: number;
  };
  watchlist?: WorkbenchTouchWatch[];
  hot_industries?: { industry: string; count: number }[];
  groups: WorkbenchFollowupGroup[];
  limitations: string[];
};

export type WorkbenchTickBucket = {
  buy_trades: number;
  sell_trades: number;
  neutral_trades: number;
  buy_amount: number;
  sell_amount: number;
  net_amount: number;
};

export type WorkbenchTickFlow = {
  status: string;
  reason?: string;
  source?: string;
  total?: {
    trades: number;
    amount: number;
    buy_trades: number;
    sell_trades: number;
    neutral_trades: number;
    buy_amount?: number;
    sell_amount?: number;
  };
  buckets?: {
    super: WorkbenchTickBucket;
    big: WorkbenchTickBucket;
    mid: WorkbenchTickBucket;
    small: WorkbenchTickBucket;
  };
  main_net?: number;
  limitations?: string[];
};

export type WorkbenchBrokenTracking = {
  as_of: string;
  days: number;
  dates?: string[];
  status: string;
  source?: string;
  reason?: string;
  summary: {
    stocks?: number;
    shown?: number;
    broken_today?: number;
    fund_covered?: number;
    main_inflow_today?: number;
    main_inflow_5d?: number;
  };
  rows: WorkbenchBrokenRow[];
  limitations: string[];
};

export type WorkbenchHome = {
  as_of: string;
  /** 复盘（涨停/炸板）所用交易日；日线未同步完时与 as_of 不同 */
  review_as_of?: string;
  data_coverage?: { date?: string | null; bars?: number; peak?: number; ratio?: number };
  /** 后端缓存标记：true 表示直接返回了缓存结果（进程内 5 分钟 / 磁盘 30 分钟） */
  cached?: boolean;
  generated_at?: string;
  cache_ttl_seconds?: number;
  market: WorkbenchMarket;
  sections: {
    market_review: WorkbenchMarketReview;
    broken_followup?: WorkbenchBrokenFollowup;
    sector_moves?: Array<{ name: string; hits: number; share: number }>;
    yesterday_strong?: {
      status: string;
      prev_date?: string;
      today?: string;
      cohort?: number;
      continued?: number;
      weakened?: number;
      flat?: number;
      rows?: Array<{ code: string; prev_ret: number; today_ret: number }>;
      limitation?: string;
      reason?: string;
    };
    shortline: WorkbenchMethodSummary[];
    longterm_changes: {
      status: string;
      reason?: string;
      count?: number;
      limitation?: string;
      rows: Array<{
        code: string;
        name?: string | null;
        industry?: string | null;
        hypothesis_key: string;
        hypothesis: string;
        evidence_status?: string;
        earnings?: {
          status?: string;
          report_period?: string;
          ann_date?: string | null;
          revenue_yoy?: number | null;
          profit_yoy?: number | null;
          roe?: number | null;
          reason?: string;
        };
        institution?: {
          status?: string;
          report_period?: string;
          fund_count?: number | null;
          inst_ratio?: number | null;
          inst_chg_pct?: number | null;
          reason?: string;
        };
        holders?: { status?: string; holder_asof?: string; drop_periods?: number | null; reason?: string };
      }>;
    };
    todos: Array<{ type: string; track_id?: string; code: string; hypothesis_key: string; due?: string | null; status: string }>;
  };
  intersections: WorkbenchIntersection[];
  limitations: string[];
};

export type WorkbenchCandidate = {
  code: string;
  name?: string;
  industry?: string;
  close?: number | null;
  chg_today?: number | null;
  ret20?: number;
  excess20_pp?: number;
  dist_ma20?: number;
  drawdown_high120?: number;
  vol_ratio?: number | null;
  stop?: number;
  stop_pct?: number;
  why?: string[];
  source_signal?: string;
  holder_stat_date?: string;
};

export type WorkbenchMethodDetail = {
  as_of: string;
  method: {
    id: string;
    name: string;
    question: string;
    horizon: string;
    confirm_rule: string;
    invalid_rule: string;
    producer?: string;
  };
  excluded_industries: string[];
  count: number;
  new_today: number;
  tracking: number;
  confirming: string[];
  invalid: string[];
  candidates: WorkbenchCandidate[];
};

export type WorkbenchCardSection = {
  status: WorkbenchSectionStatus;
  source?: string;
  as_of?: string;
  reason?: string;
  report_period?: string;
  ann_date?: string | null;
  holder_asof?: string;
  available_rule?: string;
  data?: Record<string, unknown>;
};

export type WorkbenchTrack = {
  track_id: string;
  hypothesis_key: string;
  title: string;
  status: string;
  horizon: string;
  business_layer: string;
  hypothesis: string;
  invalidation: string;
  next_review_at?: string | null;
  events: Array<{ event_type: string; source_type?: string | null; summary?: string | null; observed_at?: string | null }>;
};

export type WorkbenchProfile = {
  code: string;
  as_of: string;
  sections: Record<string, WorkbenchCardSection>;
  limitations: string[];
  tracks: WorkbenchTrack[];
};

export type WorkbenchBar = {
  date: string;
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  volume: number | null;
};

export type WorkbenchKline = {
  code: string;
  period: 'daily' | 'weekly' | 'monthly';
  as_of: string | null;
  adjusted: string;
  bars: WorkbenchBar[];
  limitation: string;
};

export const workbenchApi = {
  async getHome(date?: string, refresh?: boolean): Promise<WorkbenchHome> {
    const { data } = await apiClient.get<WorkbenchHome>('/api/v1/workbench/home', {
      params: { ...(date ? { date } : {}), ...(refresh ? { refresh: true } : {}) },
    });
    return data;
  },

  async getReview(date?: string, refresh?: boolean): Promise<WorkbenchMarketReview> {
    const { data } = await apiClient.get<WorkbenchMarketReview>('/api/v1/workbench/review', {
      params: { ...(date ? { date } : {}), ...(refresh ? { refresh: true } : {}) },
    });
    return data;
  },

  async getMethods(date?: string): Promise<{ as_of: string; methods: WorkbenchMethodSummary[]; intersections: WorkbenchIntersection[]; limitations: string[] }> {
    const { data } = await apiClient.get('/api/v1/workbench/methods', {
      params: date ? { date } : undefined,
    });
    return data;
  },

  async getMethod(methodId: string, date?: string): Promise<WorkbenchMethodDetail> {
    const { data } = await apiClient.get<WorkbenchMethodDetail>(`/api/v1/workbench/method/${methodId}`, {
      params: date ? { date } : undefined,
    });
    return data;
  },

  async getProfile(code: string, asOf?: string): Promise<WorkbenchProfile> {
    const { data } = await apiClient.get<WorkbenchProfile>(`/api/v1/workbench/profile/${code}`, {
      params: asOf ? { as_of: asOf } : undefined,
    });
    return data;
  },

  async getKline(
    code: string,
    period: 'daily' | 'weekly' | 'monthly' = 'daily',
    limit = 120,
    asOf?: string,
  ): Promise<WorkbenchKline> {
    // as_of 必须与首页/方法使用同一截止日，否则历史视角会读到之后的行情
    const { data } = await apiClient.get<WorkbenchKline>(`/api/v1/workbench/kline/${code}`, {
      params: { period, limit, ...(asOf ? { as_of: asOf } : {}) },
    });
    return data;
  },
};
