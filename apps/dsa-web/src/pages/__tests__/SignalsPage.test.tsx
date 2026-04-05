import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getSnapshots, getHistory, sendSelection } = vi.hoisted(() => ({
  getSnapshots: vi.fn(),
  getHistory: vi.fn(),
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
      },
    ],
  };
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
    sendSelection.mockResolvedValue({ success: true });
  });

  it('loads the snapshot list and history panel', async () => {
    render(<SignalsPage />);

    expect(await screen.findByTestId('signals-page')).toBeInTheDocument();
    expect((await screen.findAllByText('莱美药业')).length).toBeGreaterThan(0);
    expect(await screen.findByText('当前连续命中')).toBeInTheDocument();

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
