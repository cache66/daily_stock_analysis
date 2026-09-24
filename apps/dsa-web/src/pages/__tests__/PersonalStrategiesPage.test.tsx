import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const { getPersonalStrategyMatrix } = vi.hoisted(() => ({
  getPersonalStrategyMatrix: vi.fn(),
}));

vi.mock('../../api/signals', () => ({
  signalsApi: {
    getPersonalStrategyMatrix,
  },
}));

import PersonalStrategiesPage from '../PersonalStrategiesPage';

const strategies = [
  {
    id: 'earnings_surprise',
    name: '业绩强势 earnings_surprise',
    shortName: '业绩',
    group: 'daily',
    groupLabel: '日复盘默认信号',
    mode: '默认日复盘',
    aliases: ['earnings_surprise'],
    role: '业绩改善。',
    logic: '看业绩改善。',
  },
  {
    id: 'hundred_day_high',
    name: '百日新高 hundred_day_high',
    shortName: '新高',
    group: 'daily',
    groupLabel: '日复盘默认信号',
    mode: '默认日复盘',
    aliases: ['hundred_day_high'],
    role: '新高确认。',
    logic: '看百日新高。',
  },
  {
    id: 'daily_slow_rise',
    name: '日线慢涨 daily_slow_rise',
    shortName: '慢涨',
    group: 'daily',
    groupLabel: '日复盘默认信号',
    mode: '默认日复盘',
    aliases: ['daily_slow_rise'],
    role: '阳线多。',
    logic: '看日线慢涨。',
  },
];

const strategyMatches = Object.fromEntries(strategies.map((strategy) => [
  strategy.id,
  {
    id: strategy.id,
    name: strategy.name,
    shortName: strategy.shortName,
    group: strategy.group,
    groupLabel: strategy.groupLabel,
    mode: strategy.mode,
    role: strategy.role,
    logic: strategy.logic,
  },
]));

const overviewResponse = {
  snapshotDate: '2026-07-10',
  total: 2,
  sourceRunDir: 'data/manual_runs/20260710/review',
  sourceCsvPath: 'data/manual_runs/20260710/review/fast_review_stock_overview.csv',
  laneSummary: { core: 1, watch: 1 },
  signalSummary: { earnings_surprise: 1, daily_slow_rise: 1, hundred_day_high: 1 },
  strategySummary: { earnings_surprise: 1, hundred_day_high: 1, daily_slow_rise: 1 },
  strategies,
  items: [
    {
      code: '300475',
      name: '香农芯创',
      stockReviewLane: 'core',
      stockReviewLaneLabel: '重点复盘',
      tier: 'core',
      abBucket: 'A',
      reviewStageLabel: '观察',
      driverLabel: '业绩',
      priorityScore: 88,
      strategyCount: 2,
      signalKeys: ['earnings_surprise', 'daily_slow_rise'],
      signalTypes: ['earnings_surprise'],
      triggeredStrategies: ['earnings_surprise', 'daily_slow_rise'],
      chartEvidenceSummary: '阳线占比高，近期没有明显走差。',
      earningsEvidenceSummary: '净利润同比明显改善。',
      stockContextSummary: '存储链条受益。',
      todayChangePct: 3.21,
      peRatio: 24.5,
      reportPeriodLabel: '2026Q1',
      revenueYoy: 36.8,
      netProfitYoy: 120.4,
      roe: 8.1,
      earningsQualityScore: 82,
      earningsQualityCyclePhase: '改善',
      capitalProfileScore: 75,
      relativeStrengthScore: 90,
      marketExpectationInstitutionCount: 12,
      netProfitAmount: 120000000,
      primaryBoardName: '半导体',
      preferredIndustryLabel: '存储',
      themeLabel: 'AI 存储',
      mainlineJudgement: '主线偏强',
      reasonSummary: '业绩与图形共振。',
      displayReasonSummary: '业绩与图形共振。',
      causeTagsZh: '业绩,图形',
      latestTradeDate: '2026-07-10',
      pureChartQualityPassed: true,
      matchedStrategies: [strategyMatches.earnings_surprise, strategyMatches.daily_slow_rise],
      matchedStrategyIds: ['earnings_surprise', 'daily_slow_rise'],
      matchedStrategyCount: 2,
      qualityScore: 92,
      qualityBand: 'recommended',
      qualityLabel: '优先看',
      qualitySummary: '双策略共振 / 业绩质量 82 / 相对强度 90',
      qualityFlags: [],
      viewLane: 'long_term',
      viewLaneLabel: '长线发现',
      viewLaneSummary: '长线发现信号，用来沉淀形态、业绩或观察池线索。',
    },
    {
      code: '002025',
      name: '航天电器',
      stockReviewLane: 'watch',
      stockReviewLaneLabel: '图形优先',
      tier: 'watch',
      abBucket: 'B',
      reviewStageLabel: '纯轮动',
      driverLabel: '题材情绪型',
      priorityScore: 72,
      strategyCount: 1,
      signalKeys: ['hundred_day_high'],
      signalTypes: ['hundred_day_high'],
      triggeredStrategies: ['hundred_day_high'],
      chartEvidenceSummary: '接近百日新高。',
      earningsEvidenceSummary: '暂无业绩确认。',
      stockContextSummary: '军工连接器。',
      todayChangePct: -5.34,
      peRatio: 31.2,
      reportPeriodLabel: '2026Q1',
      revenueYoy: null,
      netProfitYoy: null,
      roe: null,
      earningsQualityScore: 0,
      earningsQualityCyclePhase: null,
      capitalProfileScore: 0,
      relativeStrengthScore: 0,
      marketExpectationInstitutionCount: 0,
      netProfitAmount: null,
      primaryBoardName: '军工',
      preferredIndustryLabel: '连接器',
      themeLabel: '商业航天',
      mainlineJudgement: '题材轮动',
      reasonSummary: '图形突破但当日走弱。',
      displayReasonSummary: '图形突破但当日走弱。',
      causeTagsZh: '图形',
      latestTradeDate: '2026-07-10',
      pureChartQualityPassed: false,
      matchedStrategies: [strategyMatches.hundred_day_high],
      matchedStrategyIds: ['hundred_day_high'],
      matchedStrategyCount: 1,
      qualityScore: 18,
      qualityBand: 'weak',
      qualityLabel: '偏弱',
      qualitySummary: '当日明显走弱 -5.34% / 图形质检未通过 / 单策略孤证',
      qualityFlags: ['当日明显走弱 -5.34%', '图形质检未通过', '单策略孤证'],
      viewLane: 'short_term',
      viewLaneLabel: '短线主升',
      viewLaneSummary: '短线主升信号，用来优先看当下强度和资金确认。',
    },
  ],
};

describe('PersonalStrategiesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getPersonalStrategyMatrix.mockResolvedValue(overviewResponse);
  });

  it('loads the latest available matrix and renders the short-term strategy view by default', async () => {
    render(<PersonalStrategiesPage />);

    expect(await screen.findByText('002025')).toBeInTheDocument();
    expect(screen.getByText('航天电器')).toBeInTheDocument();
    expect(screen.queryByText('300475')).not.toBeInTheDocument();
    expect(screen.getByLabelText('002025 百日新高 hundred_day_high 命中')).toBeInTheDocument();

    await waitFor(() => {
      expect(getPersonalStrategyMatrix).toHaveBeenCalledWith(
        { snapshotDate: 'latest', code: undefined },
        expect.objectContaining({ signal: expect.any(Object) }),
      );
    });
    expect(screen.getByLabelText('复盘日期')).toHaveValue('2026-07-10');
  });

  it('switches to the long-term discovery view without hiding weak short-term rows globally', async () => {
    render(<PersonalStrategiesPage />);

    expect(await screen.findByText('002025')).toBeInTheDocument();
    expect(screen.queryByText('300475')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /长线发现/ }));

    expect(await screen.findByText('300475')).toBeInTheDocument();
    expect(screen.getByText('香农芯创')).toBeInTheDocument();
    expect(screen.queryByText('002025')).not.toBeInTheDocument();
    expect(screen.getByLabelText('300475 业绩强势 earnings_surprise 命中')).toBeInTheDocument();
    expect(screen.getByLabelText('300475 日线慢涨 daily_slow_rise 命中')).toBeInTheDocument();
  });

  it('filters stocks by selected strategy chips', async () => {
    render(<PersonalStrategiesPage />);

    expect(await screen.findByText('002025')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /慢涨/ }));

    expect(screen.queryByText('300475')).not.toBeInTheDocument();
    expect(screen.queryByText('002025')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /长线发现/ }));
    expect(screen.getByText('300475')).toBeInTheDocument();
  });

  it('expands inline stock details with strategy evidence and metrics', async () => {
    render(<PersonalStrategiesPage />);

    fireEvent.click(await screen.findByRole('button', { name: /长线发现/ }));
    expect(await screen.findByText('300475')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '展开 300475 策略详情' }));

    const detail = await screen.findByText('统一质量分');
    expect(detail).toBeInTheDocument();
    expect(screen.getAllByText('策略视图').length).toBeGreaterThan(0);
    expect(screen.getAllByText('长线发现').length).toBeGreaterThan(0);
    expect(screen.getByText('92.00')).toBeInTheDocument();
    expect(screen.getByText('营收同比')).toBeInTheDocument();
    expect(screen.getByText('36.80%')).toBeInTheDocument();
    expect(screen.getByText('图形证据')).toBeInTheDocument();
    expect(screen.getByText('阳线占比高，近期没有明显走差。')).toBeInTheDocument();
    expect(screen.getAllByText('业绩强势 earnings_surprise').length).toBeGreaterThan(0);
    expect(screen.getByText('daily_slow_rise')).toBeInTheDocument();
  });

  it('submits date and code filters to the overview endpoint', async () => {
    render(<PersonalStrategiesPage />);

    await screen.findByText('002025');
    fireEvent.change(screen.getByLabelText('复盘日期'), { target: { value: '2026-07-10' } });
    fireEvent.change(screen.getByLabelText('股票代码，可选'), { target: { value: '300475' } });
    fireEvent.click(screen.getByRole('button', { name: '查询' }));

    await waitFor(() => {
      expect(getPersonalStrategyMatrix).toHaveBeenLastCalledWith(
        { snapshotDate: '2026-07-10', code: '300475' },
        expect.objectContaining({ signal: expect.any(Object) }),
      );
    });
  });
});
