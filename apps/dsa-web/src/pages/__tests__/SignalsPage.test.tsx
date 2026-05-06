import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getSnapshots, getHistory, getSnapshotCounts, sendSelection } = vi.hoisted(() => ({
  getSnapshots: vi.fn(),
  getHistory: vi.fn(),
  getSnapshotCounts: vi.fn(),
  sendSelection: vi.fn(),
}));

const writeClipboardText = vi.fn();

vi.stubGlobal('localStorage', {
  getItem: vi.fn(() => null),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn(),
});

Object.defineProperty(navigator, 'clipboard', {
  configurable: true,
  value: {
    writeText: writeClipboardText,
  },
});

vi.mock('../../api/signals', () => ({
  signalsApi: {
    getSnapshots,
    getHistory,
    getSnapshotCounts,
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

function buildSingleResponse() {
  return {
    signalType: 'hundred_day_high',
    signalDate: '2026-04-05',
    signalDateFrom: null,
    signalDateTo: null,
    total: 2,
    page: 1,
    pageSize: 12,
    compareSummary: [],
    streakLeaderboard: [],
    items: [
      {
        code: '300006',
        name: '莱美药业',
        signalDate: '2026-04-05',
        industry: '化学制药',
        reasonSummary: '医药方向延续活跃。',
        industryLogic: '行业景气和主题共振。',
        newsLogic: '消息面中性偏多。',
        technicalLogic: '20 日新高延续。',
        themeLabel: '创新药 / 医疗服务',
        latestPreviousHitDate: '2026-04-04',
        previousHitCount: 1,
        daysSincePreviousHit: 1,
        isConsecutiveSignal: true,
        close: 6.52,
        latestHigh: 6.52,
        windowHigh: 6.52,
        yearStartDate: '2026-01-02',
        yearStartClose: 5.43,
        ytdReturnPct: 20.07,
      },
      {
        code: '688485',
        name: '九州一轨',
        signalDate: '2026-04-05',
        industry: '轨交设备',
        reasonSummary: '设备方向相对强势。',
        industryLogic: '设备更新预期。',
        newsLogic: '暂无显著事件催化。',
        technicalLogic: '20 日新高。',
        themeLabel: '',
        latestPreviousHitDate: null,
        previousHitCount: 0,
        daysSincePreviousHit: null,
        isConsecutiveSignal: false,
        close: 31.3,
        latestHigh: 31.3,
        windowHigh: 31.3,
        yearStartDate: '2026-01-02',
        yearStartClose: 25.2,
        ytdReturnPct: 24.21,
      },
    ],
  };
}

function buildEarningsResponse() {
  return {
    signalType: 'earnings_surprise',
    signalDate: '2026-04-05',
    signalDateFrom: null,
    signalDateTo: null,
    total: 1,
    page: 1,
    pageSize: 12,
    compareSummary: [],
    streakLeaderboard: [],
    items: [
      {
        code: '600488',
        name: '津药药业',
        signalDate: '2026-04-05',
        industry: '化学制药',
        reasonSummary: '业绩预增且近期强势。',
        industryLogic: '医药景气延续。',
        newsLogic: '公告文本偏正向。',
        technicalLogic: '事件后保持强势。',
        themeLabel: '创新药 / 医疗服务',
        latestPreviousHitDate: null,
        previousHitCount: 0,
        daysSincePreviousHit: null,
        isConsecutiveSignal: false,
        close: 7.67,
        latestHigh: 7.8,
        windowHigh: 7.8,
        yearStartDate: '2026-01-02',
        yearStartClose: 4.14,
        ytdReturnPct: 85.27,
      },
    ],
  };
}

function buildCommodityResponse() {
  return {
    signalType: 'commodity_beneficiary__optical_fiber',
    signalDate: '2026-04-10',
    signalDateFrom: null,
    signalDateTo: null,
    total: 1,
    page: 1,
    pageSize: 12,
    compareSummary: [],
    streakLeaderboard: [],
    items: [
      {
        code: '601869',
        name: '长飞光纤',
        signalDate: '2026-04-10',
        subthemeKey: 'preform_and_materials',
        chainRole: 'upstream',
        passThroughDirection: 'positive',
        earningsValidationStatus: 'positive',
        earningsReleaseProbability: 'high',
        directness: 'direct_beneficiary',
        matchedExampleBucket: 'whitelist',
        matchedExampleName: '长飞光纤',
        industry: 'preform_and_materials',
        reasonSummary: '光纤专题候选',
        industryLogic: '上游材料更接近直接受益。',
        newsLogic: '行业供需偏紧。',
        technicalLogic: '',
        themeLabel: 'optical_fiber',
        latestPreviousHitDate: null,
        previousHitCount: 0,
        daysSincePreviousHit: null,
        isConsecutiveSignal: false,
        close: 32.5,
        latestHigh: null,
        windowHigh: null,
        yearStartDate: null,
        yearStartClose: null,
        ytdReturnPct: null,
      },
    ],
  };
}

function buildDragonHeadResponse() {
  return {
    signalType: 'dragon_head_candidate',
    signalDate: '2026-04-11',
    signalDateFrom: null,
    signalDateTo: null,
    total: 1,
    page: 1,
    pageSize: 12,
    compareSummary: [],
    streakLeaderboard: [],
    items: [
      {
        code: '600001',
        name: '混合龙头',
        signalDate: '2026-04-11',
        leaderType: 'hybrid_leader',
        leaderProbability: 'high',
        recognizabilityScore: 3,
        logicConsensusScore: 3,
        capitalConsensusScore: 2,
        sectorLeadershipScore: 3,
        relativeStrengthScore: 2,
        liquidityScore: 2,
        catalystScore: 2,
        industry: 'hybrid_leader',
        reasonSummary: '龙头专题候选',
        industryLogic: 'recognizability=3',
        newsLogic: '板块催化明显。',
        technicalLogic: '',
        themeLabel: 'dragon_head',
        latestPreviousHitDate: null,
        previousHitCount: 0,
        daysSincePreviousHit: null,
        isConsecutiveSignal: false,
        close: 18.6,
        latestHigh: 19.1,
        windowHigh: 19.1,
        yearStartDate: null,
        yearStartClose: null,
        ytdReturnPct: null,
      },
    ],
  };
}

function buildTrendLeaderUnifiedResponse() {
  return {
    signalType: 'trend_leader_unified',
    signalDate: '2026-04-19',
    signalDateFrom: null,
    signalDateTo: null,
    total: 1,
    page: 1,
    pageSize: 12,
    compareSummary: [],
    streakLeaderboard: [],
    items: [
      {
        code: '600001',
        name: '强势龙头',
        signalDate: '2026-04-19',
        primaryProfile: 'breakout',
        breakoutScore: 82,
        pullbackScore: 41,
        hybridScore: 86,
        overallScore: 86,
        trendLabel: 'near_new_high',
        leaderType: 'hybrid_leader',
        leaderProbability: 'high',
        previousHitCount: 0,
        daysSincePreviousHit: null,
        isConsecutiveSignal: false,
        close: 12.3,
        latestHigh: 12.8,
        windowHigh: 12.8,
      },
    ],
  };
}

function buildBoardRecognizabilityResponse() {
  return {
    signalType: 'board_recognizability__board_semiconductor_1234567890',
    signalDate: '2026-04-10',
    signalDateFrom: null,
    signalDateTo: null,
    total: 1,
    page: 1,
    pageSize: 12,
    compareSummary: [],
    streakLeaderboard: [],
    items: [
      {
        code: '688981',
        name: '中芯国际',
        signalDate: '2026-04-10',
        boardName: '半导体',
        boardRank: 1,
        boardCandidateCount: 3,
        sourceSignalType: 'hundred_day_high',
        sourceSignalDate: '2026-04-10',
        industry: '半导体',
        reasonSummary: '半导体板块辨识度第 1 名。',
        industryLogic: '板块内共有 3 只候选，当前个股排第 1。',
        newsLogic: '',
        technicalLogic: '延续强势。',
        themeLabel: '芯片',
        latestPreviousHitDate: '2026-04-05',
        previousHitCount: 2,
        daysSincePreviousHit: 5,
        isConsecutiveSignal: false,
        close: 95.5,
        latestHigh: 96.8,
        windowHigh: 96.8,
        totalMarketCap: 123456789000,
        totalMarketCapYi: 1234.57,
        yearStartDate: null,
        yearStartClose: null,
        ytdReturnPct: null,
      },
    ],
  };
}

function buildMonthlySlowRiseProfileResponse(profileName = 'balanced') {
  const profileConfig = {
    strict: {
      label: '月线慢牛·严格',
      code: '600519',
      name: '贵州茅台',
      positiveRatio: 0.83,
      higherLowRatio: 0.75,
      totalReturnPct: 32.4,
      maxSingleGainPct: 7.2,
      worstDrawdownPct: -4.8,
      maShort: 1688.5,
      maLong: 1598.2,
      latestMonth: '2026-03',
      ytdReturnPct: 15.3,
      reasonSummary: '月线更平滑，回撤更浅。',
    },
    balanced: {
      label: '月线慢牛·均衡',
      code: '600519',
      name: '贵州茅台',
      positiveRatio: 0.75,
      higherLowRatio: 0.67,
      totalReturnPct: 38.5,
      maxSingleGainPct: 9.8,
      worstDrawdownPct: -6.2,
      maShort: 1678.5,
      maLong: 1588.2,
      latestMonth: '2026-03',
      ytdReturnPct: 16.88,
      reasonSummary: '月线连续抬高，涨幅温和。',
    },
    loose: {
      label: '月线慢牛·宽松',
      code: '000858',
      name: '五粮液',
      positiveRatio: 0.67,
      higherLowRatio: 0.58,
      totalReturnPct: 46.2,
      maxSingleGainPct: 12.8,
      worstDrawdownPct: -8.9,
      maShort: 1450.2,
      maLong: 1398.6,
      latestMonth: '2026-03',
      ytdReturnPct: 18.25,
      reasonSummary: '允许波动略大，但仍保持月线抬升。',
    },
  } as const;

  const config = profileConfig[profileName as keyof typeof profileConfig] ?? profileConfig.balanced;
  return {
    signalType: `monthly_slow_rise_profile__${profileName}`,
    signalDate: '2026-04-10',
    signalDateFrom: null,
    signalDateTo: null,
    total: 1,
    page: 1,
    pageSize: 12,
    compareSummary: [],
    streakLeaderboard: [],
    items: [
      {
        code: config.code,
        name: config.name,
        signalDate: '2026-04-10',
        industry: '白酒',
        profileName,
        profileLabel: config.label,
        monthlyPositiveRatio: config.positiveRatio,
        monthlyHigherLowRatio: config.higherLowRatio,
        monthlyTotalReturnPct: config.totalReturnPct,
        monthlyMaxSingleGainPct: config.maxSingleGainPct,
        monthlyWorstDrawdownPct: config.worstDrawdownPct,
        monthlyMaShort: config.maShort,
        monthlyMaLong: config.maLong,
        monthlyLatestMonth: config.latestMonth,
        reasonSummary: config.reasonSummary,
        industryLogic: '高端白酒景气稳定。',
        newsLogic: '需求预期平稳。',
        technicalLogic: 'MA6 持续站上 MA12。',
        themeLabel: '消费白马',
        latestPreviousHitDate: '2026-03-10',
        previousHitCount: 2,
        daysSincePreviousHit: 31,
        isConsecutiveSignal: false,
        close: 1800,
        latestHigh: 1818,
        windowHigh: 1818,
        yearStartDate: '2026-01-02',
        yearStartClose: 1540,
        ytdReturnPct: config.ytdReturnPct,
      },
    ],
  };
}

function buildMonthlySlowRiseResponse() {
  return buildMonthlySlowRiseProfileResponse('balanced');
}

function buildMonthlySlowRiseStrictResponse() {
  return buildMonthlySlowRiseProfileResponse('strict');
}

function buildMonthlySlowRiseLooseResponse() {
  return buildMonthlySlowRiseProfileResponse('loose');
}

function buildRangeResponse() {
  const compareSummary = Array.from({ length: 11 }, (_, index) => {
    const day = `${5 - index}`.padStart(2, '0');
    return {
      signalDate: `2026-04-${day}`,
      totalCount: Math.max(2, 11 - index),
      continuousCount: Math.max(1, 4 - index),
      topCodes: ['300006', '688485'],
      addedCount: index === 0 ? 2 : 0,
      droppedCount: index === 0 ? 1 : 0,
      addedCodes: index === 0 ? ['300006', '688485'] : [],
      droppedCodes: index === 0 ? ['300626'] : [],
      avgYtdReturnPct: index === 0 ? 22.14 : 18.0,
      medianYtdReturnPct: index === 0 ? 20.07 : 18.0,
      addedItems: index === 0
        ? [
          { code: '300006', name: '莱美药业' },
          { code: '688485', name: '九州一轨' },
        ]
        : [],
      droppedItems: index === 0
        ? [{ code: '300626', name: '华瑞股份' }]
        : [],
    };
  });

  return {
    signalType: 'hundred_day_high',
    signalDate: null,
    signalDateFrom: '2026-04-01',
    signalDateTo: '2026-04-05',
    total: 4,
    page: 1,
    pageSize: 12,
    compareSummary,
    streakLeaderboard: [
      {
        code: '300006',
        name: '莱美药业',
        industry: '化学制药',
        currentStreakCount: 3,
        longestStreakCount: 4,
        currentStreakStartDate: '2026-04-03',
        currentStreakEndDate: '2026-04-05',
        latestSignalDate: '2026-04-05',
        latestHigh: 6.52,
        close: 6.52,
        themeLabel: '创新药 / 医疗服务',
      },
      {
        code: '688485',
        name: '九州一轨',
        industry: '轨交设备',
        currentStreakCount: 2,
        longestStreakCount: 2,
        currentStreakStartDate: '2026-04-04',
        currentStreakEndDate: '2026-04-05',
        latestSignalDate: '2026-04-05',
        latestHigh: 31.3,
        close: 31.3,
        themeLabel: '',
      },
    ],
    items: [
      {
        code: '300006',
        name: '莱美药业',
        signalDate: '2026-04-05',
        industry: '化学制药',
        reasonSummary: '医药方向延续活跃。',
        industryLogic: '行业景气和主题共振。',
        newsLogic: '消息面中性偏多。',
        technicalLogic: '20 日新高延续。',
        themeLabel: '创新药 / 医疗服务',
        latestPreviousHitDate: '2026-04-04',
        previousHitCount: 1,
        daysSincePreviousHit: 1,
        isConsecutiveSignal: true,
        close: 6.52,
        latestHigh: 6.52,
        windowHigh: 6.52,
        yearStartDate: '2026-01-02',
        yearStartClose: 5.43,
        ytdReturnPct: 20.07,
      },
      {
        code: '688485',
        name: '九州一轨',
        signalDate: '2026-04-05',
        industry: '轨交设备',
        reasonSummary: '设备方向相对强势。',
        industryLogic: '设备更新预期。',
        newsLogic: '暂无显著事件催化。',
        technicalLogic: '20 日新高。',
        themeLabel: '',
        latestPreviousHitDate: '2026-04-04',
        previousHitCount: 1,
        daysSincePreviousHit: 1,
        isConsecutiveSignal: true,
        close: 31.3,
        latestHigh: 31.3,
        windowHigh: 31.3,
        yearStartDate: '2026-01-02',
        yearStartClose: 25.2,
        ytdReturnPct: 24.21,
      },
    ],
  };
}

function buildHistoryResponse(code = '300006', name = '莱美药业') {
  return {
    signalType: 'hundred_day_high',
    code,
    days: 180,
    total: 2,
    continuity: {
      isCurrentStreak: true,
      currentStreakCount: 2,
      currentStreakStartDate: '2026-04-04',
      currentStreakEndDate: '2026-04-05',
      longestStreakCount: 2,
      longestStreakStartDate: '2026-04-04',
      longestStreakEndDate: '2026-04-05',
    },
    drawdown: {
      anchorClose: 6.52,
      maxSignalHigh: 6.66,
      maxSignalHighDate: '2026-04-04',
      distanceFromMaxSignalHighPct: -2.1,
      latestSignalHigh: 6.52,
      latestSignalDate: '2026-04-05',
      distanceFromLatestSignalHighPct: 0,
    },
    items: [
      {
        code,
        name,
        signalDate: '2026-04-05',
        industry: '化学制药',
        reasonSummary: '医药方向延续活跃。',
        industryLogic: '行业景气和主题共振。',
        newsLogic: '消息面中性偏多。',
        technicalLogic: '20 日新高延续。',
        themeLabel: '创新药 / 医疗服务',
        previousHitCount: 1,
        isConsecutiveSignal: true,
        close: 6.52,
        latestHigh: 6.52,
        windowHigh: 6.52,
        yearStartDate: '2026-01-02',
        yearStartClose: 5.43,
        ytdReturnPct: 20.07,
      },
      {
        code,
        name,
        signalDate: '2026-04-04',
        industry: '化学制药',
        reasonSummary: '延续强势。',
        industryLogic: '行业景气和主题共振。',
        newsLogic: '消息面中性偏多。',
        technicalLogic: '20 日新高延续。',
        themeLabel: '创新药 / 医疗服务',
        previousHitCount: 0,
        isConsecutiveSignal: false,
        close: 6.66,
        latestHigh: 6.66,
        windowHigh: 6.66,
        yearStartDate: '2026-01-02',
        yearStartClose: 5.43,
        ytdReturnPct: 22.65,
      },
    ],
  };
}

function buildMonthlySlowRiseHistoryResponse() {
  return {
    signalType: 'monthly_slow_rise_profile__balanced',
    code: '600519',
    days: 180,
    total: 2,
    continuity: {
      isCurrentStreak: false,
      currentStreakCount: 1,
      currentStreakStartDate: '2026-04-10',
      currentStreakEndDate: '2026-04-10',
      longestStreakCount: 2,
      longestStreakStartDate: '2026-03-10',
      longestStreakEndDate: '2026-04-10',
    },
    drawdown: {
      anchorClose: 1800,
      maxSignalHigh: 1818,
      maxSignalHighDate: '2026-04-10',
      distanceFromMaxSignalHighPct: -0.99,
      latestSignalHigh: 1818,
      latestSignalDate: '2026-04-10',
      distanceFromLatestSignalHighPct: -0.99,
    },
    items: [
      {
        ...buildMonthlySlowRiseResponse().items[0],
      },
      {
        ...buildMonthlySlowRiseResponse().items[0],
        signalDate: '2026-03-10',
        monthlyLatestMonth: '2026-02',
        monthlyPositiveRatio: 0.67,
        monthlyHigherLowRatio: 0.58,
      },
    ],
  };
}

describe('SignalsPage', () => {
beforeEach(() => {
  vi.clearAllMocks();
  writeClipboardText.mockResolvedValue(undefined);
  getSnapshots.mockResolvedValue(buildSingleResponse());
  getHistory.mockResolvedValue(buildHistoryResponse());
  getSnapshotCounts.mockResolvedValue({
    signalDate: '2026-04-05',
    signalDateFrom: null,
    signalDateTo: null,
    items: [
      { signalType: 'trend_leader_unified', total: 1, displayLabel: '强趋势龙头总榜', group: 'strategy' },
      { signalType: 'hundred_day_high', total: 2 },
      { signalType: 'earnings_surprise', total: 1 },
      { signalType: 'monthly_slow_rise', total: 1, displayLabel: '月线慢牛', group: 'strategy' },
      { signalType: 'hundred_day_high_with_earnings', total: 1 },
      { signalType: 'dragon_head_candidate', total: 1 },
      { signalType: 'commodity_beneficiary__optical_fiber', total: 1 },
      { signalType: 'commodity_beneficiary__memory', total: 0 },
      { signalType: 'commodity_beneficiary__hard_disk', total: 0 },
      {
        signalType: 'board_recognizability__board_semiconductor_1234567890',
        total: 1,
        displayLabel: '半导体辨识度',
        group: 'board_recognizability',
      },
      {
        signalType: 'monthly_slow_rise_profile__strict',
        total: 1,
        displayLabel: '月线慢牛·严格',
        group: 'monthly_slow_rise',
      },
      {
        signalType: 'monthly_slow_rise_profile__balanced',
        total: 1,
        displayLabel: '月线慢牛·均衡',
        group: 'monthly_slow_rise',
      },
      {
        signalType: 'monthly_slow_rise_profile__loose',
        total: 1,
        displayLabel: '月线慢牛·宽松',
        group: 'monthly_slow_rise',
      },
    ],
  });
  sendSelection.mockResolvedValue({ success: true });
});

  it('loads the snapshot list and history panel', async () => {
    render(<SignalsPage />);

    expect(await screen.findByTestId('signals-page')).toBeInTheDocument();
    expect((await screen.findAllByText('莱美药业')).length).toBeGreaterThan(0);
    expect(await screen.findByText('当前连续新高')).toBeInTheDocument();
    expect((await screen.findAllByText(/YTD 20\.07%/)).length).toBeGreaterThan(0);
    expect(await screen.findByText('总命中')).toBeInTheDocument();
    expect(await screen.findByText('平均 YTD')).toBeInTheDocument();

    await waitFor(() => expect(getSnapshots).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(getHistory).toHaveBeenCalledWith(
      'hundred_day_high',
      '300006',
      { days: 180, limit: 100 },
      expect.objectContaining({ signal: expect.any(AbortSignal) }),
    ));
  });

  it('refreshes with a stock code filter and can switch history item', async () => {
    render(<SignalsPage />);
    expect(await screen.findByLabelText('signals-mode-short_term')).toBeInTheDocument();
    expect(screen.getByLabelText('signals-mode-long_term')).toBeInTheDocument();

    expect((await screen.findAllByText('莱美药业')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('stock-code-filter'), { target: { value: '688485' } });
    fireEvent.click(screen.getByLabelText('refresh-list'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenLastCalledWith(
        {
          signalType: 'hundred_day_high',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: ['688485'],
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    getHistory.mockResolvedValue(buildHistoryResponse('688485', '九州一轨'));
    fireEvent.click(screen.getByRole('button', { name: /九州一轨/ }));

    await waitFor(() => {
      expect(getHistory).toHaveBeenCalledWith(
        'hundred_day_high',
        '688485',
        { days: 180, limit: 100 },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it('supports switching signal type to earnings surprise', async () => {
    getSnapshots
      .mockResolvedValueOnce(buildSingleResponse())
      .mockResolvedValue(buildEarningsResponse());
    getHistory.mockResolvedValue(buildHistoryResponse('600488', '津药药业'));

    render(<SignalsPage />);
    expect((await screen.findAllByText('莱美药业')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('signal-type'), { target: { value: 'earnings_surprise' } });

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenLastCalledWith(
        {
          signalType: 'earnings_surprise',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: undefined,
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByText('业绩超预期快照')).toBeInTheDocument();
    expect((await screen.findAllByText(/YTD 85\.27%/)).length).toBeGreaterThan(0);
  });

  it('supports quick filtering by YTD threshold', async () => {
    render(<SignalsPage />);
    expect((await screen.findAllByText('莱美药业')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('ytd-filter'), { target: { value: 'gt50' } });

    await waitFor(() => {
      expect(screen.queryByText('莱美药业')).not.toBeInTheDocument();
    });
  });

  it('filters signal-type dropdown options by mode', async () => {
    render(<SignalsPage />);
    expect((await screen.findAllByText('莱美药业')).length).toBeGreaterThan(0);

    const shortModeSelect = screen.getByLabelText('signal-type') as HTMLSelectElement;
    const shortModeValues = Array.from(shortModeSelect.querySelectorAll('option')).map((option) => option.value);
    expect(shortModeValues).toEqual(expect.arrayContaining([
      'trend_leader_unified',
      'earnings_surprise',
      'hundred_day_high',
    ]));
    expect(shortModeValues).not.toContain('monthly_slow_rise');
    expect(shortModeValues).not.toContain('dragon_head_candidate');
    expect(shortModeValues).not.toContain('commodity_beneficiary__optical_fiber');

    fireEvent.click(screen.getByLabelText('signals-mode-long_term'));

    await waitFor(() => {
      const longModeSelect = screen.getByLabelText('signal-type') as HTMLSelectElement;
      const longModeValues = Array.from(longModeSelect.querySelectorAll('option')).map((option) => option.value);
      expect(longModeValues).toEqual(expect.arrayContaining([
        'monthly_slow_rise',
        'dragon_head_candidate',
        'commodity_beneficiary__optical_fiber',
        'board_recognizability__board_semiconductor_1234567890',
        'monthly_slow_rise_profile__balanced',
      ]));
      expect(longModeValues).not.toContain('trend_leader_unified');
      expect(longModeValues).not.toContain('earnings_surprise');
      expect(longModeValues).not.toContain('hundred_day_high');
      expect(longModeValues).not.toContain('hundred_day_high_with_earnings');
    });
  });

  it('renders short-term tabs by default and can switch to long-term tabs', async () => {
    render(<SignalsPage />);
    expect(await screen.findByLabelText('signals-mode-short_term')).toBeInTheDocument();
    expect(screen.getByLabelText('signals-mode-long_term')).toBeInTheDocument();
    expect(await screen.findByLabelText('signal-type-tab-hundred_day_high')).toBeInTheDocument();
    expect(screen.getByLabelText('signal-type-tab-earnings_surprise')).toBeInTheDocument();
    expect(screen.getByLabelText('signal-type-tab-trend_leader_unified')).toBeInTheDocument();
    expect(screen.queryByLabelText('signal-type-tab-monthly_slow_rise')).not.toBeInTheDocument();
    expect(await screen.findByLabelText('signal-type-total-count-hundred_day_high')).toHaveTextContent('总 2');

    fireEvent.click(screen.getByLabelText('signals-mode-long_term'));
    expect(await screen.findByLabelText('signal-type-tab-monthly_slow_rise')).toBeInTheDocument();
    expect(screen.getByLabelText('signal-type-tab-dragon_head_candidate')).toBeInTheDocument();
    expect(screen.getByLabelText('signal-type-tab-commodity_beneficiary__optical_fiber')).toBeInTheDocument();
    expect(screen.getByLabelText('stock-code-filter')).toBeInTheDocument();
    expect(screen.getByLabelText('ytd-filter')).toBeInTheDocument();
    expect(screen.getByLabelText('streak-only')).toBeInTheDocument();

    getSnapshots.mockResolvedValue({
      ...buildMonthlySlowRiseResponse(),
      signalType: 'monthly_slow_rise',
    });
    fireEvent.click(screen.getByLabelText('signal-type-tab-monthly_slow_rise'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenCalledWith(
        expect.objectContaining({
          signalType: 'monthly_slow_rise',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: undefined,
          page: 1,
          pageSize: 12,
        }),
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it('falls back to short-term preferred signal when switching modes', async () => {
    getSnapshots
      .mockResolvedValueOnce(buildSingleResponse())
      .mockResolvedValue(buildDragonHeadResponse());
    getHistory.mockResolvedValue({
      ...buildHistoryResponse('600001', '混合龙头'),
      signalType: 'dragon_head_candidate',
      items: [{ ...buildDragonHeadResponse().items[0] }],
    });

    render(<SignalsPage />);
    fireEvent.click(await screen.findByLabelText('signals-mode-long_term'));
    fireEvent.click(await screen.findByLabelText('signal-type-tab-dragon_head_candidate'));
    expect(await screen.findByText('龙头专题快照')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('signals-mode-short_term'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenCalledWith(
        expect.objectContaining({
          signalType: 'trend_leader_unified',
          page: 1,
          pageSize: 12,
        }),
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });
  });

  it('supports switching to a commodity beneficiary signal tab', async () => {
    getSnapshots
      .mockResolvedValueOnce(buildSingleResponse())
      .mockResolvedValue(buildCommodityResponse());
    getHistory.mockResolvedValue({
      ...buildHistoryResponse('601869', '长飞光纤'),
      signalType: 'commodity_beneficiary__optical_fiber',
      items: [
        {
          ...buildCommodityResponse().items[0],
        },
      ],
    });

    render(<SignalsPage />);
    fireEvent.click(await screen.findByLabelText('signals-mode-long_term'));
    expect(await screen.findByLabelText('signal-type-tab-commodity_beneficiary__optical_fiber')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('signal-type-tab-commodity_beneficiary__optical_fiber'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenLastCalledWith(
        {
          signalType: 'commodity_beneficiary__optical_fiber',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: undefined,
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByText('光纤涨价快照')).toBeInTheDocument();
    expect((await screen.findAllByText('preform_and_materials')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('upstream')).length).toBeGreaterThan(0);
  });

  it('supports switching to a dragon-head signal tab', async () => {
    getSnapshots
      .mockResolvedValueOnce(buildSingleResponse())
      .mockResolvedValue(buildDragonHeadResponse());
    getHistory.mockResolvedValue({
      ...buildHistoryResponse('600001', '混合龙头'),
      signalType: 'dragon_head_candidate',
      items: [
        {
          ...buildDragonHeadResponse().items[0],
        },
      ],
    });

    render(<SignalsPage />);
    fireEvent.click(await screen.findByLabelText('signals-mode-long_term'));
    expect(await screen.findByLabelText('signal-type-tab-dragon_head_candidate')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('signal-type-tab-dragon_head_candidate'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenLastCalledWith(
        {
          signalType: 'dragon_head_candidate',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: undefined,
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByText('龙头专题快照')).toBeInTheDocument();
    expect((await screen.findAllByText('hybrid_leader')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('high')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/Leader Type/i)).length).toBeGreaterThan(0);
  });

  it('supports switching to a trend leader unified signal tab', async () => {
    getSnapshots
      .mockResolvedValueOnce(buildSingleResponse())
      .mockResolvedValue(buildTrendLeaderUnifiedResponse());
    getHistory.mockResolvedValue({
      ...buildHistoryResponse('600001', '强势龙头'),
      signalType: 'trend_leader_unified',
      items: [
        {
          ...buildTrendLeaderUnifiedResponse().items[0],
        },
      ],
    });

    render(<SignalsPage />);
    expect(await screen.findByLabelText('signal-type-tab-trend_leader_unified')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('signal-type-tab-trend_leader_unified'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenLastCalledWith(
        {
          signalType: 'trend_leader_unified',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: undefined,
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByRole('heading', { name: '强趋势龙头总榜' })).toBeInTheDocument();
    expect((await screen.findAllByText('breakout')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('86.00')).length).toBeGreaterThan(0);
  });

  it('supports switching to a board recognizability signal tab', async () => {
    getSnapshots
      .mockResolvedValueOnce(buildSingleResponse())
      .mockResolvedValue(buildBoardRecognizabilityResponse());
    getHistory.mockResolvedValue({
      ...buildHistoryResponse('688981', '中芯国际'),
      signalType: 'board_recognizability__board_semiconductor_1234567890',
      items: [
        {
          ...buildBoardRecognizabilityResponse().items[0],
        },
      ],
    });

    render(<SignalsPage />);
    fireEvent.click(await screen.findByLabelText('signals-mode-long_term'));
    expect(await screen.findByLabelText('signal-type-tab-board_recognizability__board_semiconductor_1234567890')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('signal-type-tab-board_recognizability__board_semiconductor_1234567890'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenLastCalledWith(
        {
          signalType: 'board_recognizability__board_semiconductor_1234567890',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: undefined,
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByText('半导体辨识度快照')).toBeInTheDocument();
    expect((await screen.findAllByText(/#1/)).length).toBeGreaterThan(0);
    expect((await screen.findAllByText(/半导体/)).length).toBeGreaterThan(0);
  });

  it('renders one-click compare view for monthly slow rise profiles', async () => {
    getSnapshots.mockImplementation(async (params: { signalType: string; pageSize?: number }) => {
      if (params.signalType === 'monthly_slow_rise_profile__strict') {
        return buildMonthlySlowRiseStrictResponse();
      }
      if (params.signalType === 'monthly_slow_rise_profile__balanced') {
        return buildMonthlySlowRiseResponse();
      }
      if (params.signalType === 'monthly_slow_rise_profile__loose') {
        return buildMonthlySlowRiseLooseResponse();
      }
      return buildSingleResponse();
    });
    getHistory.mockResolvedValue(buildMonthlySlowRiseHistoryResponse());

    render(<SignalsPage />);
    fireEvent.click(await screen.findByLabelText('signals-mode-long_term'));
    expect(await screen.findByLabelText('signal-type-tab-monthly_slow_rise_profile__balanced')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('signal-type-tab-monthly_slow_rise_profile__balanced'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenCalledWith(
        {
          signalType: 'monthly_slow_rise_profile__balanced',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: undefined,
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByText('月线慢牛·均衡快照')).toBeInTheDocument();
    expect((await screen.findAllByText('月线慢牛·均衡')).length).toBeGreaterThan(0);
    expect(await screen.findByText(/收涨 75\.00% \| 抬低点 67\.00% \| 区间 38\.50%/)).toBeInTheDocument();
    expect(await screen.findByText(/Worst Drawdown/i)).toBeInTheDocument();
    expect(screen.queryByTestId('monthly-slow-rise-compare')).not.toBeInTheDocument();
    expect(await screen.findByLabelText('toggle-monthly-compare')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('toggle-monthly-compare'));
    expect(await screen.findByTestId('monthly-slow-rise-compare')).toBeInTheDocument();
    expect(await screen.findByText('月线慢牛一键对比')).toBeInTheDocument();
    expect(await screen.findByText(/双档以上重合 1/)).toBeInTheDocument();
    expect((await screen.findAllByText('月线慢牛·严格')).length).toBeGreaterThan(0);
    expect((await screen.findAllByText('月线慢牛·宽松')).length).toBeGreaterThan(0);

    fireEvent.click(screen.getByLabelText('toggle-monthly-compare'));
    await waitFor(() => {
      expect(screen.queryByTestId('monthly-slow-rise-compare')).not.toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('toggle-monthly-compare'));
    expect(await screen.findByTestId('monthly-slow-rise-compare')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('monthly-compare-focus-monthly_slow_rise_profile__strict'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenCalledWith(
        {
          signalType: 'monthly_slow_rise_profile__strict',
          signalDate: expect.any(String),
          signalDateFrom: undefined,
          signalDateTo: undefined,
          code: undefined,
          codes: undefined,
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByText('月线慢牛·严格快照')).toBeInTheDocument();
  });

  it('supports range mode compare toggle and group selection', async () => {
    getSnapshots
      .mockResolvedValueOnce(buildSingleResponse())
      .mockResolvedValue(buildRangeResponse());

    render(<SignalsPage />);
    expect((await screen.findAllByText('莱美药业')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('date-mode'), { target: { value: 'range' } });
    fireEvent.change(screen.getByLabelText('signal-date-from'), { target: { value: '2026-04-01' } });
    fireEvent.change(screen.getByLabelText('signal-date-to'), { target: { value: '2026-04-05' } });

    expect(await screen.findByText('多日对比')).toBeInTheDocument();
    expect(screen.getByLabelText('signals-compare-toggle')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('streak-group-mode'), { target: { value: 'industry' } });
    fireEvent.click(screen.getByLabelText('select-streak-group-0'));

    await waitFor(() => {
      expect(getSnapshots).toHaveBeenLastCalledWith(
        {
          signalType: 'hundred_day_high',
          signalDate: undefined,
          signalDateFrom: '2026-04-01',
          signalDateTo: '2026-04-05',
          code: undefined,
          codes: ['300006'],
          page: 1,
          pageSize: 12,
        },
        expect.objectContaining({ signal: expect.any(AbortSignal) }),
      );
    });

    expect(await screen.findByTestId('signals-selection-summary')).toHaveTextContent('已选 1 只');
    expect(screen.getByTestId('signals-selection-summary')).toHaveTextContent('行业组 化学制药');
    expect(screen.getByLabelText('select-streak-group-0')).toHaveTextContent('取消本组');

    fireEvent.click(screen.getByLabelText('select-streak-group-0'));

    await waitFor(() => {
      expect(screen.getByLabelText('select-streak-group-0')).toHaveTextContent('选择本组');
    });

    expect(screen.getByTestId('signals-selection-summary')).not.toHaveTextContent('行业组 化学制药');
  });

  it('can copy markdown and push selected results', async () => {
    getSnapshots
      .mockResolvedValueOnce(buildSingleResponse())
      .mockResolvedValue(buildRangeResponse());

    render(<SignalsPage />);
    expect((await screen.findAllByText('莱美药业')).length).toBeGreaterThan(0);

    fireEvent.change(screen.getByLabelText('date-mode'), { target: { value: 'range' } });
    fireEvent.change(screen.getByLabelText('signal-date-from'), { target: { value: '2026-04-01' } });
    fireEvent.change(screen.getByLabelText('signal-date-to'), { target: { value: '2026-04-05' } });

    fireEvent.click(await screen.findByLabelText('link-added-300006'));

    await waitFor(() => {
      expect(screen.getByTestId('signals-selection-summary')).toHaveTextContent('联动筛选 1 只股票 / 1 条快照');
    });

    fireEvent.click(screen.getByLabelText('signals-copy-markdown'));
    await waitFor(() => expect(writeClipboardText).toHaveBeenCalledTimes(1));
    expect(writeClipboardText.mock.calls[0][0]).toContain('莱美药业');

    fireEvent.click(screen.getByLabelText('signals-push-selection'));
    await waitFor(() => expect(sendSelection).toHaveBeenCalledTimes(1));
    expect(sendSelection.mock.calls[0][0].content).toContain('莱美药业');
    expect(await screen.findByText(/已推送 1 条快照/)).toBeInTheDocument();
  });
});
