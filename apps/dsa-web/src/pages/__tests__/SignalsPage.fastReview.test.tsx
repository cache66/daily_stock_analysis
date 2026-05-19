import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const {
  getSnapshots,
  getHistory,
  getSnapshotCounts,
  getFastReviewFocus,
  sendSelection,
} = vi.hoisted(() => ({
  getSnapshots: vi.fn(),
  getHistory: vi.fn(),
  getSnapshotCounts: vi.fn(),
  getFastReviewFocus: vi.fn(),
  sendSelection: vi.fn(),
}));

vi.stubGlobal('localStorage', {
  getItem: vi.fn(() => null),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
});

Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: {
    writeText: vi.fn().mockResolvedValue(undefined),
  },
});

vi.mock('../../api/signals', () => ({
  signalsApi: {
    getSnapshots,
    getHistory,
    getSnapshotCounts,
    getFastReviewFocus,
    sendSelection,
  },
}));

vi.mock('../../stores/agentChatStore', () => ({
  useAgentChatStore: {
    getState: () => ({
      setCurrentRoute: vi.fn(),
    }),
  },
}));

import SignalsPage from '../SignalsPage';

describe('SignalsPage fast review mode', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getSnapshots.mockResolvedValue({
      signalType: 'hundred_day_high',
      signalDate: '2026-05-06',
      signalDateFrom: null,
      signalDateTo: null,
      total: 0,
      page: 1,
      pageSize: 12,
      compareSummary: [],
      streakLeaderboard: [],
      items: [],
    });
    getHistory.mockResolvedValue({
      signalType: 'hundred_day_high',
      code: '',
      days: 180,
      total: 0,
      continuity: {
        isCurrentStreak: false,
        currentStreakCount: 0,
        currentStreakStartDate: null,
        currentStreakEndDate: null,
        longestStreakCount: 0,
        longestStreakStartDate: null,
        longestStreakEndDate: null,
      },
      drawdown: {
        anchorClose: null,
        maxSignalHigh: null,
        maxSignalHighDate: null,
        distanceFromMaxSignalHighPct: null,
        latestSignalHigh: null,
        latestSignalDate: null,
        distanceFromLatestSignalHighPct: null,
      },
      items: [],
    });
    getSnapshotCounts.mockResolvedValue({
      signalDate: '2026-05-06',
      signalDateFrom: null,
      signalDateTo: null,
      items: [],
    });
    getFastReviewFocus.mockResolvedValue({
      snapshotDate: '2026-05-06',
      total: 2,
      sourceRunDir: 'data/manual_runs/fast_review_focus_case',
      sourceCsvPath: 'data/manual_runs/fast_review_focus_case/2026-05-06/review/fast_review_strategy_focus.csv',
      abSummary: { A: 1, B: 1 },
      stageSummary: { 兑现: 1, 纯轮动: 1 },
      driverSummary: { 业绩兑现型: 1, 题材情绪型: 1 },
      items: [
        {
          code: '603629',
          name: '利通电子',
          tier: 'core',
          abBucket: 'A',
          priorityScore: 329.2,
          signalKeys: ['trend_leader', 'earnings', 'hundred_day_high'],
          signalTypes: ['trend_leader_unified', 'earnings_surprise', 'hundred_day_high'],
          reviewStageLabel: '兑现',
          driverLabel: '业绩兑现型',
          focusReason: 'trend; hundred_day_overlap; earnings=68',
          reasonSummary: 'recent earnings + price confirmation',
          industryLogic: 'board strength remains visible',
          newsLogic: 'no extra event needed',
          technicalLogic: 'near new high with follow-through',
          mainlineJudgement: 'AI主线扩散',
          mainlineEvidenceSources: ['business_summary', 'theme_mapping', 'supply_demand'],
          authorityJudgement: '公告确认',
          authorityLevel: 'announcement',
          authorityReasonSummary: '近7天公告确认订单与扩产逻辑，涨势更偏基本面兑现。',
          displayAuthorityJudgement: '公告确认',
          displayAuthoritySummary: '公告确认：近7天公告确认订单与扩产逻辑，涨势更偏基本面兑现。',
          authorityEvidenceDigest: '公告: 扩产与订单；财报: 2026Q1净利2.6亿；研报: 机构继续强化AI基础设施逻辑',
          announcementEvidenceSummary: '近7天公告涉及扩产和订单，和股价上行逻辑一致',
          earningsEvidenceSummary: '2026Q1净利润2.6亿元，季度业绩保持高增长',
          researchEvidenceSummary: '机构近期持续强化AI基础设施景气和订单兑现逻辑',
          authorityTimeWindowDays: 7,
          peerGroupLabel: 'PCB',
          peerResonanceSummary: '同日 PCB 方向有3只进入焦点池，胜宏科技位列龙头；沪电股份、世运电路同步走强，更像板块共振。',
          leaderPositionSummary: '当前在 PCB 焦点组内位列龙头，属于同日最强之一。',
          turningPointPeerSummary: '同组已有2只处在拐点、1只处在半兑现，说明更像行业扩散初段，不是单票异动。',
          displayPeerSummary: '同日 PCB 方向有3只进入焦点池，胜宏科技位列龙头；沪电股份、世运电路同步走强，更像板块共振。',
          riskFlags: ['weak_capital_flow'],
          trendLabel: 'near_new_high',
          selectionMode: 'strict',
          eventDate: '2026-04-28',
          todayChangePct: 6.8,
          peRatio: 18.4,
          reportDate: '2026-03-31',
          reportPeriodLabel: '2026Q1',
          revenueAmount: 1080000000,
          netProfitAmount: 260000000,
          displayReasonSummary: '当前更像是 算力设备/液冷 方向走强；主线判断更偏 AI主线扩散；业务更偏 算力设备/液冷，偏AI基础设施。',
          businessLabels: ['算力设备', '液冷'],
          businessSummary: '算力设备/液冷，偏AI基础设施',
          chainRoleLabel: 'AI基础设施',
          themeLabel: 'AI算力链映射',
          themeSource: 'business_summary+news_title',
          earningsAnchor: '2026Q1@2026-04-28',
          supplyDemandBias: 'earnings',
        },
        {
          code: '002929',
          name: '润建股份',
          tier: 'watch',
          abBucket: 'B',
          priorityScore: 118.0,
          signalKeys: ['hundred_day_high'],
          signalTypes: ['hundred_day_high'],
          reviewStageLabel: '纯轮动',
          driverLabel: '题材情绪型',
          focusReason: 'hundred_day_only',
          reasonSummary: 'theme rotation without earnings support',
          industryLogic: 'rotation continues',
          newsLogic: 'topic heat only',
          technicalLogic: 'new high but no strong delivery evidence',
          authorityJudgement: null,
          authorityLevel: null,
          authorityReasonSummary: null,
          displayAuthorityJudgement: null,
          displayAuthoritySummary: null,
          authorityEvidenceDigest: null,
          announcementEvidenceSummary: null,
          earningsEvidenceSummary: null,
          researchEvidenceSummary: null,
          authorityTimeWindowDays: null,
          riskFlags: [],
          trendLabel: null,
          selectionMode: null,
          eventDate: null,
          todayChangePct: -1.2,
          peRatio: null,
          reportDate: null,
          reportPeriodLabel: null,
          revenueAmount: null,
          netProfitAmount: null,
          displayReasonSummary: '题材轮动主导，当前先按纯轮动观察。',
          displayPeerSummary: null,
          businessLabels: [],
          businessSummary: null,
          chainRoleLabel: null,
          themeLabel: null,
          themeSource: null,
          earningsAnchor: null,
          supplyDemandBias: null,
          mainlineJudgement: null,
          mainlineEvidenceSources: [],
          preferredIndustryLabel: null,
          reviewStageType: 'pure_rotation',
          reviewStageReason: 'rotation, theme, or event strength is stronger than earnings realization evidence',
          driverType: 'theme_sentiment_driven',
          driverReason: 'theme, policy, or sentiment dominates while hard evidence is limited',
        },
      ],
    });
  });

  it('loads compact fast review focus view and shows condensed detail panel', async () => {
    render(<SignalsPage />);

    fireEvent.click(await screen.findByLabelText('signals-data-mode-fast_review'));

    await waitFor(() => {
      expect(getFastReviewFocus).toHaveBeenCalledWith(
        { snapshotDate: expect.any(String) },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByRole('heading', { name: 'Fast Review Focus' })).toBeInTheDocument();
    expect(await screen.findByLabelText('fast-review-row-603629')).toBeInTheDocument();
    expect(await screen.findByLabelText('fast-review-ab-A')).toHaveTextContent('1');
    expect(await screen.findByLabelText('fast-review-stage-纯轮动')).toHaveTextContent('1');

    expect(await screen.findAllByLabelText(/fast-review-stage-group-/)).toHaveLength(2);
    fireEvent.click(screen.getByLabelText('fast-review-row-603629'));

    expect(await screen.findByText('Quick Read')).toBeInTheDocument();
    expect(await screen.findByText('Logic')).toBeInTheDocument();
    expect(await screen.findByText('Context & Risk')).toBeInTheDocument();
    expect(screen.queryByText('Explanation Structure')).not.toBeInTheDocument();
    expect(screen.queryByText('Market & Earnings')).not.toBeInTheDocument();
    expect(await screen.findByText('Verified Logic')).toBeInTheDocument();
    expect(await screen.findByText('Peer Summary')).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Authority Details' })).toBeInTheDocument();
    expect(await screen.findByRole('button', { name: 'Peer Check Details' })).toBeInTheDocument();
    expect(screen.queryByText('Priority')).not.toBeInTheDocument();
    expect(screen.queryByText('Event Date')).not.toBeInTheDocument();
    expect(await screen.findByText('当前更像是 算力设备/液冷 方向走强；主线判断更偏 AI主线扩散；业务更偏 算力设备/液冷，偏AI基础设施。')).toBeInTheDocument();
    expect(await screen.findByText('公告确认')).toBeInTheDocument();
    expect(await screen.findByText('公告确认：近7天公告确认订单与扩产逻辑，涨势更偏基本面兑现。')).toBeInTheDocument();
    expect(await screen.findByText('AI主线扩散')).toBeInTheDocument();
    expect(await screen.findByText('业务标签 / 主题映射 / 供需景气')).toBeInTheDocument();
    expect(await screen.findByText('同日 PCB 方向有3只进入焦点池，胜宏科技位列龙头；沪电股份、世运电路同步走强，更像板块共振。')).toBeInTheDocument();
    expect(await screen.findByText('board strength remains visible')).toBeInTheDocument();
    expect(await screen.findByText('near new high with follow-through')).toBeInTheDocument();
    expect(await screen.findByText('+6.8%')).toBeInTheDocument();
    expect(await screen.findByText('18.4')).toBeInTheDocument();
    expect(await screen.findByText('2026Q1')).toBeInTheDocument();
    expect(await screen.findByText('2.60亿')).toBeInTheDocument();

    expect(screen.queryByText('近7天公告涉及扩产和订单，和股价上行逻辑一致')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Authority Details' }));
    expect(await screen.findByText('Announcement')).toBeInTheDocument();
    expect(await screen.findByText('Earnings')).toBeInTheDocument();
    expect(await screen.findByText('Research')).toBeInTheDocument();
    expect(await screen.findByText('近7天公告涉及扩产和订单，和股价上行逻辑一致')).toBeInTheDocument();

    expect(screen.queryByText('当前在 PCB 焦点组内位列龙头，属于同日最强之一。')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Peer Check Details' }));
    expect(await screen.findByText('当前在 PCB 焦点组内位列龙头，属于同日最强之一。')).toBeInTheDocument();
    expect(await screen.findByText('同组已有2只处在拐点、1只处在半兑现，说明更像行业扩散初段，不是单票异动。')).toBeInTheDocument();
  });
});
