import type React from 'react';
import { useEffect, useState } from 'react';
import { Activity, CalendarDays, Check, ChevronDown, ChevronUp, FileText, Filter, LineChart, ListChecks, RefreshCw, Search, X } from 'lucide-react';
import { signalsApi } from '../api/signals';
import { AppPage, Badge, Card, Collapsible, InlineAlert, Loading, PageHeader } from '../components/common';
import type {
  PersonalStrategyDefinition,
  PersonalStrategyMatrixItem,
  PersonalStrategyMatrixResponse,
} from '../types/signals';

const LATEST_SNAPSHOT_DATE = 'latest';

type ViewLaneFilter = PersonalStrategyMatrixItem['viewLane'] | 'all';

const VIEW_LANE_FILTERS: Array<{ key: ViewLaneFilter; label: string; icon: React.ReactNode }> = [
  { key: 'short_term', label: '短线主升', icon: <Activity className="h-3.5 w-3.5" /> },
  { key: 'long_term', label: '长线发现', icon: <LineChart className="h-3.5 w-3.5" /> },
  { key: 'watch', label: '其他观察', icon: <ListChecks className="h-3.5 w-3.5" /> },
  { key: 'all', label: '全部', icon: <ListChecks className="h-3.5 w-3.5" /> },
];

function getMatchedStrategies(stock: PersonalStrategyMatrixItem) {
  return stock.matchedStrategies ?? [];
}

function stockMatchesStrategy(stock: PersonalStrategyMatrixItem, strategyId: string): boolean {
  return (stock.matchedStrategyIds ?? []).includes(strategyId);
}

function buildStrategyGroups(strategies: PersonalStrategyDefinition[]) {
  const groups: Array<{ group: string; label: string; strategies: PersonalStrategyDefinition[] }> = [];
  const groupIndexes = new Map<string, number>();
  strategies.forEach((strategy) => {
    const groupKey = strategy.group || 'other';
    const existingIndex = groupIndexes.get(groupKey);
    if (existingIndex === undefined) {
      groupIndexes.set(groupKey, groups.length);
      groups.push({
        group: groupKey,
        label: strategy.groupLabel || groupKey,
        strategies: [strategy],
      });
      return;
    }
    groups[existingIndex].strategies.push(strategy);
  });
  return groups;
}

function buildViewLaneCounts(stocks: PersonalStrategyMatrixItem[]) {
  return stocks.reduce<Record<PersonalStrategyMatrixItem['viewLane'], number>>((counts, stock) => {
    const lane = stock.viewLane || 'watch';
    counts[lane] += 1;
    return counts;
  }, { short_term: 0, long_term: 0, watch: 0 });
}

function formatNumber(value: number | null | undefined, suffix = ''): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '-';
  }
  return `${Number(value).toFixed(2)}${suffix}`;
}

function formatInteger(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '-';
  }
  return String(Math.round(Number(value)));
}

function formatAmount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return '-';
  }
  const amount = Number(value);
  if (Math.abs(amount) >= 100_000_000) {
    return `${(amount / 100_000_000).toFixed(2)} 亿`;
  }
  if (Math.abs(amount) >= 10_000) {
    return `${(amount / 10_000).toFixed(2)} 万`;
  }
  return amount.toFixed(2);
}

function formatText(value: string | null | undefined): string {
  const text = String(value || '').trim();
  return text || '-';
}

function getErrorMessage(error: unknown): string {
  if (typeof error === 'object' && error !== null && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: string; message?: string } } }).response;
    return response?.data?.detail || response?.data?.message || '读取个人策略矩阵失败';
  }
  if (error instanceof Error) {
    return error.message;
  }
  return '读取个人策略矩阵失败';
}

function getQualityBadgeVariant(band: PersonalStrategyMatrixItem['qualityBand']) {
  if (band === 'recommended') {
    return 'success';
  }
  if (band === 'watch') {
    return 'info';
  }
  return 'warning';
}

function getViewLaneBadgeVariant(lane: PersonalStrategyMatrixItem['viewLane']) {
  if (lane === 'short_term') {
    return 'success';
  }
  if (lane === 'long_term') {
    return 'info';
  }
  return 'default';
}

function StrategyFilterPanel({
  stocks,
  strategies,
  strategySummary,
  selectedStrategyIds,
  onToggleStrategy,
  onClear,
}: {
  stocks: PersonalStrategyMatrixItem[];
  strategies: PersonalStrategyDefinition[];
  strategySummary: Record<string, number>;
  selectedStrategyIds: string[];
  onToggleStrategy: (strategyId: string) => void;
  onClear: () => void;
}) {
  return (
    <Card className="space-y-3">
      <div className="flex flex-col gap-2 md:flex-row md:items-end md:justify-between">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <Filter className="h-4 w-4" />
            策略过滤
          </div>
          <p className="mt-1 text-xs text-secondary-text">
            点策略只看命中这些策略的股票。多选按“命中任一策略”过滤，适合快速翻股票列表。
          </p>
        </div>
        {selectedStrategyIds.length > 0 ? (
          <button
            type="button"
            onClick={onClear}
            className="inline-flex items-center gap-1.5 rounded-xl border border-border/70 bg-card/70 px-3 py-1.5 text-xs font-semibold text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
          >
            <X className="h-3.5 w-3.5" />
            清空过滤
          </button>
        ) : null}
      </div>

      <div className="flex flex-wrap gap-2">
        {strategies.map((strategy) => {
          const count = strategySummary[strategy.id] ?? stocks.filter((stock) => stockMatchesStrategy(stock, strategy.id)).length;
          const selected = selectedStrategyIds.includes(strategy.id);
          return (
            <button
              key={strategy.id}
              type="button"
              onClick={() => onToggleStrategy(strategy.id)}
              className={[
                'inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-medium transition-colors',
                selected
                  ? 'border-success/30 bg-success/12 text-success'
                  : count > 0
                    ? 'border-cyan/25 bg-cyan/10 text-cyan hover:bg-cyan/15'
                    : 'border-border/55 bg-elevated/40 text-muted-text hover:bg-hover hover:text-secondary-text',
              ].join(' ')}
              title={strategy.logic}
            >
              {selected ? <Check className="h-3.5 w-3.5" /> : null}
              <span>{strategy.shortName}</span>
              <span className="rounded-full border border-current/20 px-1.5 py-0.5 text-[10px] leading-none">{count}</span>
            </button>
          );
        })}
      </div>
    </Card>
  );
}

function MatchedStrategyChips({ stock }: { stock: PersonalStrategyMatrixItem }) {
  const matchedStrategies = getMatchedStrategies(stock);

  if (matchedStrategies.length === 0) {
    return <span className="text-xs text-muted-text">暂无命中策略</span>;
  }

  return (
    <div className="flex flex-wrap gap-1.5">
      {matchedStrategies.map((strategy) => (
        <span
          key={strategy.id}
          aria-label={`${stock.code} ${strategy.name} 命中`}
          className="inline-flex items-center gap-1 rounded-full border border-success/25 bg-success/10 px-2 py-1 text-xs font-medium text-success"
          title={`${strategy.name}: ${strategy.logic}`}
        >
          <Check className="h-3 w-3" />
          {strategy.shortName}
        </span>
      ))}
    </div>
  );
}

function CompactStockRow({
  stock,
  expanded,
  onToggleDetails,
}: {
  stock: PersonalStrategyMatrixItem;
  expanded: boolean;
  onToggleDetails: (code: string) => void;
}) {
  const matchedStrategies = getMatchedStrategies(stock);
  const summary = stock.displayReasonSummary || stock.reasonSummary || stock.stockContextSummary || '暂无复盘摘要';
  const industry = stock.preferredIndustryLabel || stock.primaryBoardName || stock.themeLabel || '-';

  return (
    <div className="border-b border-subtle last:border-b-0">
      <div className="grid gap-3 px-4 py-3 xl:grid-cols-[minmax(220px,0.8fr)_minmax(260px,1.1fr)_minmax(320px,1.4fr)] xl:items-start">
        <div className="min-w-0">
          <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:justify-between xl:block">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-base font-semibold text-foreground">{stock.code}</span>
              {stock.name ? <span className="text-sm font-medium text-foreground">{stock.name}</span> : null}
              <Badge variant={getViewLaneBadgeVariant(stock.viewLane)}>{stock.viewLaneLabel}</Badge>
              <Badge variant={getQualityBadgeVariant(stock.qualityBand)}>{stock.qualityLabel}</Badge>
              {stock.stockReviewLaneLabel ? <Badge variant="info">{stock.stockReviewLaneLabel}</Badge> : null}
              {stock.tier ? <Badge variant="default">{stock.tier}</Badge> : null}
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-secondary-text">
              <span>命中 {matchedStrategies.length}</span>
              <span>质量 {formatNumber(stock.qualityScore)}</span>
              <span>涨幅 {formatNumber(stock.todayChangePct, '%')}</span>
              <span>净利同比 {formatNumber(stock.netProfitYoy, '%')}</span>
            </div>
          </div>
        </div>

        <div className="min-w-0">
          <MatchedStrategyChips stock={stock} />
          <div className="mt-2 flex flex-wrap gap-1.5 text-xs text-secondary-text">
            <span className="rounded-full border border-border/55 bg-elevated/40 px-2 py-1">行业/题材：{industry}</span>
            {stock.driverLabel ? <span className="rounded-full border border-border/55 bg-elevated/40 px-2 py-1">{stock.driverLabel}</span> : null}
            {stock.reviewStageLabel ? <span className="rounded-full border border-border/55 bg-elevated/40 px-2 py-1">{stock.reviewStageLabel}</span> : null}
          </div>
        </div>

        <button
          type="button"
          onClick={() => onToggleDetails(stock.code)}
          className="min-w-0 rounded-2xl border border-border/60 bg-card/70 px-3 py-2 text-left transition-colors hover:bg-hover"
          aria-expanded={expanded}
          aria-controls={`personal-strategy-detail-${stock.code}`}
          aria-label={`${expanded ? '收起' : '展开'} ${stock.code} 策略详情`}
        >
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0 text-sm text-secondary-text">
              <p className="line-clamp-2 text-foreground">{summary}</p>
              <p className="mt-1 line-clamp-1 text-xs text-muted-text">
                {stock.viewLaneSummary || stock.viewLaneLabel} / 质检：{stock.qualitySummary || '暂无'}{stock.qualityFlags.length > 0 ? ` / 风险：${stock.qualityFlags.slice(0, 2).join(' / ')}` : ''}
              </p>
              <p className="mt-1 line-clamp-1 text-xs text-muted-text">
                图形：{stock.chartEvidenceSummary || '暂无'} / 业绩：{stock.earningsEvidenceSummary || '暂无'}
              </p>
            </div>
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-border/60 px-2.5 py-1.5 text-xs font-semibold text-secondary-text">
              {expanded ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
              {expanded ? '收起详情' : '展开详情'}
            </span>
          </div>
        </button>
      </div>

      {expanded ? (
        <div id={`personal-strategy-detail-${stock.code}`} className="border-t border-subtle bg-elevated/15 px-4 py-4">
          <PersonalStrategyStockDetail stock={stock} />
        </div>
      ) : null}
    </div>
  );
}

function SummaryCard({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <Card padding="sm">
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-secondary-text">{label}</p>
      <div className="mt-2 text-2xl font-semibold text-foreground">{value}</div>
      {hint ? <p className="mt-1 text-xs text-secondary-text">{hint}</p> : null}
    </Card>
  );
}

function DetailMetric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-border/50 bg-elevated/35 px-3 py-2">
      <p className="text-[11px] font-medium text-muted-text">{label}</p>
      <div className="mt-1 text-sm font-semibold text-foreground">{value}</div>
    </div>
  );
}

function EvidenceBlock({ title, value }: { title: string; value: string | null | undefined }) {
  return (
    <section className="rounded-lg border border-border/55 bg-elevated/30 px-4 py-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      <p className="mt-2 text-sm leading-6 text-secondary-text">{formatText(value)}</p>
    </section>
  );
}

function PersonalStrategyStockDetail({
  stock,
}: {
  stock: PersonalStrategyMatrixItem;
}) {
  const matchedStrategies = getMatchedStrategies(stock);
  const signalKeys = Array.from(new Set([...(stock.signalKeys || []), ...(stock.triggeredStrategies || [])]));

  return (
    <div className="space-y-5">
      <section className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xl font-semibold text-foreground">{stock.code}</span>
          {stock.name ? <span className="text-lg font-semibold text-foreground">{stock.name}</span> : null}
          {stock.stockReviewLaneLabel ? <Badge variant="info">{stock.stockReviewLaneLabel}</Badge> : null}
          {stock.tier ? <Badge variant="default">{stock.tier}</Badge> : null}
        </div>
        <p className="text-sm leading-6 text-secondary-text">
          {stock.displayReasonSummary || stock.reasonSummary || stock.stockContextSummary || '暂无复盘摘要'}
        </p>
        <MatchedStrategyChips stock={stock} />
      </section>

      <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <DetailMetric label="统一质量分" value={formatNumber(stock.qualityScore)} />
        <DetailMetric label="统一质量判断" value={stock.qualityLabel} />
        <DetailMetric label="策略视图" value={stock.viewLaneLabel} />
        <DetailMetric label="优先级分" value={formatNumber(stock.priorityScore)} />
        <DetailMetric label="当日涨幅" value={formatNumber(stock.todayChangePct, '%')} />
        <DetailMetric label="PE" value={formatNumber(stock.peRatio)} />
        <DetailMetric label="报告期" value={formatText(stock.reportPeriodLabel)} />
        <DetailMetric label="营收同比" value={formatNumber(stock.revenueYoy, '%')} />
        <DetailMetric label="净利同比" value={formatNumber(stock.netProfitYoy, '%')} />
        <DetailMetric label="ROE" value={formatNumber(stock.roe, '%')} />
        <DetailMetric label="业绩策略分" value={formatNumber(stock.earningsStrategyScore)} />
        <DetailMetric label="业绩闸门" value={formatText(stock.earningsStrategyGateStatus)} />
        <DetailMetric label="业绩质量分" value={formatNumber(stock.earningsQualityScore)} />
        <DetailMetric label="资金画像分" value={formatNumber(stock.capitalProfileScore)} />
        <DetailMetric label="相对强度分" value={formatNumber(stock.relativeStrengthScore)} />
        <DetailMetric label="突破质量分" value={formatNumber(stock.breakoutQualityScore)} />
        <DetailMetric label="机构预期数" value={formatInteger(stock.marketExpectationInstitutionCount)} />
        <DetailMetric label="归母净利" value={formatAmount(stock.netProfitAmount)} />
      </section>

      <section className="grid gap-3 lg:grid-cols-2">
        <DetailMetric label="行业/板块" value={formatText(stock.preferredIndustryLabel || stock.primaryBoardName)} />
        <DetailMetric label="题材标签" value={formatText(stock.themeLabel)} />
        <DetailMetric label="主线判断" value={formatText(stock.mainlineJudgement)} />
        <DetailMetric label="驱动标签" value={formatText(stock.driverLabel)} />
        <DetailMetric label="复盘阶段" value={formatText(stock.reviewStageLabel)} />
        <DetailMetric label="最新交易日" value={formatText(stock.latestTradeDate)} />
      </section>

      <section className="grid gap-3">
        <EvidenceBlock title="策略视图说明" value={stock.viewLaneSummary} />
        <EvidenceBlock title="统一质检摘要" value={stock.qualitySummary} />
        <EvidenceBlock title="图形证据" value={stock.chartEvidenceSummary} />
        <EvidenceBlock title="业绩证据" value={stock.earningsEvidenceSummary} />
        <EvidenceBlock title="单票上下文" value={stock.stockContextSummary} />
        <EvidenceBlock title="原因标签" value={stock.causeTagsZh} />
      </section>

      {stock.qualityFlags.length > 0 ? (
        <section className="space-y-2">
          <h3 className="text-sm font-semibold text-foreground">降级原因</h3>
          <div className="flex flex-wrap gap-1.5">
            {stock.qualityFlags.map((flag) => (
              <span key={flag} className="rounded-full border border-warning/20 bg-warning/10 px-2 py-1 text-xs font-medium text-warning">
                {flag}
              </span>
            ))}
          </div>
        </section>
      ) : null}

      <section className="space-y-3">
        <h3 className="text-sm font-semibold text-foreground">命中策略说明</h3>
        <div className="grid gap-3">
          {matchedStrategies.length > 0 ? matchedStrategies.map((strategy) => (
            <div key={strategy.id} className="rounded-lg border border-border/55 bg-elevated/30 px-4 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-semibold text-foreground">{strategy.name}</span>
                <Badge variant={strategy.mode === '默认日复盘' ? 'success' : 'default'}>{strategy.mode}</Badge>
              </div>
              <p className="mt-2 text-sm text-secondary-text">{strategy.role}</p>
              <p className="mt-2 text-sm text-muted-text">{strategy.logic}</p>
            </div>
          )) : (
            <p className="rounded-lg border border-border/55 bg-elevated/30 px-4 py-3 text-sm text-secondary-text">暂无命中策略</p>
          )}
        </div>
      </section>

      <section className="space-y-2">
        <h3 className="text-sm font-semibold text-foreground">原始信号键</h3>
        <div className="flex flex-wrap gap-1.5">
          {signalKeys.length > 0 ? signalKeys.map((signal) => (
            <span key={signal} className="rounded-full border border-border/55 bg-card/70 px-2 py-1 font-mono text-xs text-secondary-text">
              {signal}
            </span>
          )) : (
            <span className="text-sm text-muted-text">暂无</span>
          )}
        </div>
      </section>
    </div>
  );
}

const PersonalStrategiesPage: React.FC = () => {
  const [snapshotDate, setSnapshotDate] = useState('');
  const [codeInput, setCodeInput] = useState('');
  const [query, setQuery] = useState(() => ({ snapshotDate: LATEST_SNAPSHOT_DATE, code: '' }));
  const [data, setData] = useState<PersonalStrategyMatrixResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedStrategyIds, setSelectedStrategyIds] = useState<string[]>([]);
  const [expandedStockCode, setExpandedStockCode] = useState<string | null>(null);
  const [activeViewLane, setActiveViewLane] = useState<ViewLaneFilter>('short_term');

  useEffect(() => {
    const controller = new AbortController();

    signalsApi.getPersonalStrategyMatrix(
      {
        snapshotDate: query.snapshotDate,
        code: query.code || undefined,
      },
      { signal: controller.signal },
    ).then((response) => {
      setData(response);
      if (query.snapshotDate === LATEST_SNAPSHOT_DATE) {
        setSnapshotDate(response.snapshotDate);
      }
    }).catch((loadError: unknown) => {
      if (!controller.signal.aborted) {
        setData(null);
        setError(getErrorMessage(loadError));
      }
    }).finally(() => {
      if (!controller.signal.aborted) {
        setLoading(false);
      }
    });

    return () => controller.abort();
  }, [query]);

  const handleSubmit = (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setLoading(true);
    setError(null);
    setQuery({
      snapshotDate: snapshotDate.trim() || LATEST_SNAPSHOT_DATE,
      code: codeInput.trim(),
    });
    setExpandedStockCode(null);
  };

  const handleRefresh = () => {
    setLoading(true);
    setError(null);
    setQuery({
      snapshotDate: snapshotDate.trim() || LATEST_SNAPSHOT_DATE,
      code: codeInput.trim(),
    });
    setExpandedStockCode(null);
  };

  const toggleStrategyFilter = (strategyId: string) => {
    setSelectedStrategyIds((current) => (
      current.includes(strategyId)
        ? current.filter((id) => id !== strategyId)
        : [...current, strategyId]
    ));
  };

  const stocks = data?.items ?? [];
  const filteredStocks = selectedStrategyIds.length === 0
    ? stocks
    : stocks.filter((stock) => selectedStrategyIds.some((strategyId) => stockMatchesStrategy(stock, strategyId)));
  const visibleStocks = activeViewLane === 'all'
    ? filteredStocks
    : filteredStocks.filter((stock) => stock.viewLane === activeViewLane);
  const viewLaneCounts = buildViewLaneCounts(filteredStocks);
  const strategyGroups = buildStrategyGroups(data?.strategies ?? []);

  return (
    <AppPage className="space-y-6">
      <PageHeader
        eyebrow="Personal Strategy Matrix"
        title="个人策略股票列表"
        description="先选策略，再看股票。页面只读取复盘产物里的策略命中关系，不在前端重新计算阈值。"
        actions={(
          <span
            className="inline-flex items-center gap-2 rounded-xl border border-border/70 bg-card/70 px-3 py-2 text-sm text-secondary-text"
            title="docs/个人策略文档/个人策略界面策略清单.md"
          >
            <FileText className="h-4 w-4" />
            docs/个人策略文档/个人策略界面策略清单.md
          </span>
        )}
      />

      <Card>
        <form className="grid gap-3 md:grid-cols-[minmax(0,180px)_minmax(0,260px)_auto_auto]" onSubmit={handleSubmit}>
          <label className="block">
            <span className="mb-1 flex items-center gap-1.5 text-xs font-medium text-secondary-text">
              <CalendarDays className="h-3.5 w-3.5" />
              复盘日期
            </span>
            <input
              type="date"
              required
              value={snapshotDate}
              onChange={(event) => setSnapshotDate(event.target.value)}
              className="h-10 w-full rounded-xl border border-border/70 bg-card px-3 text-sm text-foreground outline-none transition-colors focus:border-cyan"
            />
          </label>
          <label className="block">
            <span className="mb-1 flex items-center gap-1.5 text-xs font-medium text-secondary-text">
              <Search className="h-3.5 w-3.5" />
              股票代码，可选
            </span>
            <input
              type="search"
              value={codeInput}
              onChange={(event) => setCodeInput(event.target.value)}
              placeholder="例如 300475"
              className="h-10 w-full rounded-xl border border-border/70 bg-card px-3 text-sm text-foreground outline-none transition-colors placeholder:text-muted-text focus:border-cyan"
            />
          </label>
          <button
            type="submit"
            className="mt-5 inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary-gradient px-4 text-sm font-semibold text-[hsl(var(--primary-foreground))] shadow-soft transition-transform hover:-translate-y-0.5"
          >
            <Search className="h-4 w-4" />
            查询
          </button>
          <button
            type="button"
            onClick={handleRefresh}
            className="mt-5 inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-border/70 bg-card/70 px-4 text-sm font-semibold text-secondary-text transition-colors hover:bg-hover hover:text-foreground"
          >
            <RefreshCw className="h-4 w-4" />
            刷新
          </button>
        </form>
      </Card>

      {error ? (
        <InlineAlert
          variant="warning"
          title="没有读到策略矩阵"
          message={`${error}。如果是今天还没有产物，先跑今日复盘生成 fast_review_stock_overview.csv。`}
        />
      ) : null}

      {data ? (
        <div className="grid gap-3 md:grid-cols-3">
          <SummaryCard
            label="股票数量"
            value={visibleStocks.length}
            hint={`总数 ${data.total} / ${data.snapshotDate}`}
          />
          <SummaryCard
            label="短线主升"
            value={viewLaneCounts.short_term}
            hint={`长线发现 ${viewLaneCounts.long_term} / 其他观察 ${viewLaneCounts.watch}`}
          />
          <SummaryCard
            label="信号类型"
            value={Object.entries(data.signalSummary).length}
            hint={Object.entries(data.signalSummary).slice(0, 4).map(([signal, count]) => `${signal}:${count}`).join(' / ') || '暂无'}
          />
        </div>
      ) : null}

      {data ? (
        <Card className="space-y-3">
          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div>
              <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
                <Activity className="h-4 w-4" />
                策略视图
              </div>
              <p className="mt-1 text-xs text-secondary-text">
                短线主升看当下强度与确认，长线发现承接日线慢涨、长平台释放、月线慢涨和业绩观察这类形态/线索。
              </p>
            </div>
          </div>
          <div className="flex flex-wrap gap-2">
            {VIEW_LANE_FILTERS.map((view) => {
              const count = view.key === 'all' ? filteredStocks.length : viewLaneCounts[view.key];
              const selected = activeViewLane === view.key;
              return (
                <button
                  key={view.key}
                  type="button"
                  onClick={() => {
                    setActiveViewLane(view.key);
                    setExpandedStockCode(null);
                  }}
                  className={[
                    'inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs font-semibold transition-colors',
                    selected
                      ? 'border-success/30 bg-success/12 text-success'
                      : 'border-border/55 bg-card/70 text-secondary-text hover:bg-hover hover:text-foreground',
                  ].join(' ')}
                >
                  {view.icon}
                  <span>{view.label}</span>
                  <span className="rounded-full border border-current/20 px-1.5 py-0.5 text-[10px] leading-none">{count}</span>
                </button>
              );
            })}
          </div>
        </Card>
      ) : null}

      {data ? (
        <StrategyFilterPanel
          stocks={stocks}
          strategies={data.strategies}
          strategySummary={data.strategySummary}
          selectedStrategyIds={selectedStrategyIds}
          onToggleStrategy={toggleStrategyFilter}
          onClear={() => setSelectedStrategyIds([])}
        />
      ) : null}

      <section className="space-y-3">
        <div className="flex flex-col gap-1 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <h2 className="text-lg font-semibold text-foreground">股票策略命中</h2>
            <p className="text-sm text-secondary-text">
              当前显示 {visibleStocks.length} 只股票；短线和长线分开看，详情直接行内下拉。
            </p>
          </div>
          {data?.sourceCsvPath ? (
            <p className="max-w-full truncate text-xs text-muted-text" title={data.sourceCsvPath}>
              来源：{data.sourceCsvPath}
            </p>
          ) : null}
        </div>

        {loading ? <Loading label="读取个人策略矩阵..." /> : null}

        {!loading && data && data.items.length === 0 ? (
          <InlineAlert variant="info" message="当前查询没有匹配股票。可以清空股票代码后查看全量复盘列表。" />
        ) : null}

        {!loading && data && data.items.length > 0 && filteredStocks.length === 0 ? (
          <InlineAlert variant="info" message="当前策略过滤下没有股票。可以清空过滤或切换其他策略。" />
        ) : null}

        {!loading && filteredStocks.length > 0 && visibleStocks.length === 0 ? (
          <InlineAlert variant="info" message="当前视图下没有股票。可以切到其他策略视图或查看全部。" />
        ) : null}

        {!loading && visibleStocks.length > 0 ? (
          <Card padding="none" className="overflow-hidden">
            {visibleStocks.map((stock) => (
              <CompactStockRow
                key={`${stock.code}-${stock.latestTradeDate || data?.snapshotDate}`}
                stock={stock}
                expanded={expandedStockCode === stock.code}
                onToggleDetails={(code) => setExpandedStockCode((current) => (current === code ? null : code))}
              />
            ))}
          </Card>
        ) : null}
      </section>

      <section className="space-y-3">
        <div>
          <h2 className="text-lg font-semibold text-foreground">当前策略说明</h2>
          <p className="text-sm text-secondary-text">这里只解释页面如何理解策略命中，真实阈值仍以各策略脚本和复盘产物为准。</p>
        </div>
        {strategyGroups.map(({ group, label, strategies }) => (
          <Collapsible
            key={group}
            title={`${label}（${strategies.length}）`}
            icon={<ListChecks className="h-4 w-4" />}
            defaultOpen={group === 'daily'}
          >
            <div className="grid gap-3 lg:grid-cols-2">
              {strategies.map((strategy) => (
                <div key={strategy.id} className="rounded-2xl border border-border/50 bg-elevated/35 px-3 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="font-semibold text-foreground">{strategy.name}</h3>
                    <Badge variant={strategy.mode === '默认日复盘' ? 'success' : 'default'}>{strategy.mode}</Badge>
                  </div>
                  <p className="mt-2 text-sm text-secondary-text">{strategy.role}</p>
                  <p className="mt-2 text-sm text-secondary-text">{strategy.logic}</p>
                  <p className="mt-2 break-words text-xs text-muted-text">
                    信号别名：{strategy.aliases.join(' / ')}
                  </p>
                </div>
              ))}
            </div>
          </Collapsible>
        ))}
      </section>
    </AppPage>
  );
};

export default PersonalStrategiesPage;
