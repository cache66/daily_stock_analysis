import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
import { AppPage, Badge, Card, InlineAlert, Loading, PageHeader } from '../components/common';
import {
  workbenchApi,
  type WorkbenchCardSection,
  type WorkbenchKline,
  type WorkbenchProfile,
} from '../api/workbench';

const STATUS_TONE: Record<string, 'default' | 'success' | 'warning' | 'danger'> = {
  ok: 'success',
  limited: 'warning',
  pending: 'warning',
  missing: 'danger',
};

const STATUS_LABEL: Record<string, string> = {
  ok: '可用',
  limited: '受限',
  pending: '待补',
  missing: '缺失',
};

const SECTION_TITLES: Record<string, string> = {
  basic: '基本信息',
  valuation: '规模与估值',
  earnings: '业绩',
  holders: '股东户数',
  institution: '机构持股',
  sector: '板块强度',
  price: '价格',
};

function renderValue(value: unknown): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') return Number.isInteger(value) ? String(value) : value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
  if (typeof value === 'boolean') return value ? '是' : '否';
  return String(value);
}

function SectionBlock({ name, section }: { name: string; section: WorkbenchCardSection }) {
  const entries = Object.entries(section.data ?? {});
  return (
    <div className="rounded-lg border border-border p-3">
      <div className="flex items-center gap-2">
        <span className="font-medium">{SECTION_TITLES[name] ?? name}</span>
        <Badge variant={STATUS_TONE[section.status] ?? 'default'}>{STATUS_LABEL[section.status] ?? section.status}</Badge>
        {section.report_period ? <span className="text-xs text-muted-foreground">报告期 {section.report_period}</span> : null}
        {section.holder_asof ? <span className="text-xs text-muted-foreground">截至 {section.holder_asof}</span> : null}
      </div>
      {entries.length > 0 ? (
        <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm md:grid-cols-3">
          {entries.map(([key, value]) => (
            <div key={key} className="flex justify-between gap-2 md:block">
              <dt className="text-xs text-muted-foreground">{key}</dt>
              <dd>{renderValue(value)}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {section.reason ? <p className="mt-2 text-xs text-amber-600">{section.reason}</p> : null}
      {section.available_rule && section.available_rule !== 'ann_date' ? (
        <p className="mt-1 text-xs text-muted-foreground">可获取判定：{section.available_rule}</p>
      ) : null}
    </div>
  );
}

/** 收盘线（明确标注非完整 K 线）：完整 OHLC 在下方表格与接口里都能拿到。 */
function CloseLine({ bars }: { bars: WorkbenchKline['bars'] }) {
  const closes = bars.map((bar) => bar.close).filter((value): value is number => typeof value === 'number');
  if (closes.length < 2) return null;
  const min = Math.min(...closes);
  const max = Math.max(...closes);
  const span = max - min || 1;
  const points = closes
    .map((value, index) => `${(index / (closes.length - 1)) * 100},${40 - ((value - min) / span) * 38}`)
    .join(' ');
  return (
    <div>
      <svg viewBox="0 0 100 40" preserveAspectRatio="none" className="h-24 w-full">
        <polyline points={points} fill="none" stroke="currentColor" strokeWidth="0.8" className="text-primary" />
      </svg>
      <p className="text-xs text-muted-foreground">收盘价折线（示意，不是 K 线；完整 OHLC 见下表）</p>
    </div>
  );
}

export const StockProfilePage: React.FC = () => {
  const { code = '' } = useParams();
  const [searchParams] = useSearchParams();
  const asOf = searchParams.get('as_of') ?? undefined;
  const [profile, setProfile] = useState<WorkbenchProfile | null>(null);
  const [kline, setKline] = useState<WorkbenchKline | null>(null);
  const [period, setPeriod] = useState<'daily' | 'weekly' | 'monthly'>('daily');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [card, bars] = await Promise.all([
        workbenchApi.getProfile(code, asOf),
        workbenchApi.getKline(code, period, 120, asOf),
      ]);
      setProfile(card);
      setKline(bars);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [code, period, asOf]);

  useEffect(() => {
    void load();
  }, [load]);

  const basic = profile?.sections.basic?.data as { name?: string; industry?: string } | undefined;

  return (
    <AppPage>
      <PageHeader
        title={`${code} ${basic?.name ?? ''}`}
        description={`${basic?.industry ? `行业：${basic.industry}｜` : ''}${asOf ? `历史视角：截至 ${asOf}｜` : ''}固定信息卡 + 价格 + 研究过程`}
        actions={
          <div className="flex items-center gap-2">
            <Link
              to="/workbench"
              className="inline-flex items-center gap-1 rounded-md border border-input px-2 py-1 text-sm hover:bg-accent"
            >
              ← 返回工作台
            </Link>
            <button
              type="button"
              onClick={() => void load()}
              className="inline-flex items-center gap-1 rounded-md border border-input px-2 py-1 text-sm hover:bg-accent"
            >
              <RefreshCw className="h-3.5 w-3.5" /> 刷新
            </button>
          </div>
        }
      />

      {loading ? <Loading /> : null}
      {error ? <InlineAlert variant="danger" title="加载失败" message={error} /> : null}

      {profile ? (
        <div className="space-y-4">
          <Card title={`固定信息卡（截至 ${profile.as_of}）`}>
            <div className="grid gap-3 md:grid-cols-2">
              {Object.entries(profile.sections).map(([name, section]) => (
                <SectionBlock key={name} name={name} section={section} />
              ))}
            </div>
            {profile.limitations.length > 0 ? (
              <ul className="mt-3 list-disc pl-5 text-xs text-muted-foreground">
                {profile.limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            ) : null}
          </Card>

          <Card title="价格">
            <div className="mb-2 flex gap-2">
              {(['daily', 'weekly', 'monthly'] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => setPeriod(item)}
                  className={`rounded-full border px-3 py-0.5 text-xs ${period === item ? 'border-primary text-primary' : 'border-border text-muted-foreground'}`}
                >
                  {item === 'daily' ? '日线' : item === 'weekly' ? '周线' : '月线'}
                </button>
              ))}
              <span className="text-xs text-muted-foreground">复权：{kline?.adjusted ?? '—'}</span>
            </div>
            {kline ? <CloseLine bars={kline.bars} /> : null}
            {kline ? <p className="mt-1 text-xs text-muted-foreground">{kline.limitation}</p> : null}
            {kline ? (
              <div className="mt-2 max-h-64 overflow-auto">
                <table className="w-full text-left text-xs">
                  <thead className="sticky top-0 bg-background text-muted-foreground">
                    <tr>
                      <th className="py-1">日期</th>
                      <th>开</th>
                      <th>高</th>
                      <th>低</th>
                      <th>收</th>
                      <th>成交量</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...kline.bars].reverse().slice(0, 60).map((bar) => (
                      <tr key={bar.date} className="border-t border-border/50">
                        <td className="py-0.5">{bar.date}</td>
                        <td>{renderValue(bar.open)}</td>
                        <td>{renderValue(bar.high)}</td>
                        <td>{renderValue(bar.low)}</td>
                        <td>{renderValue(bar.close)}</td>
                        <td>{renderValue(bar.volume)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
          </Card>

          <Card title={`研究过程（${profile.tracks.length} 条假设）`}>
            {profile.tracks.length === 0 ? (
              <p className="text-sm text-muted-foreground">该票还没有跟踪记录；方法发现后会自动建跟踪。</p>
            ) : (
              <ul className="space-y-3">
                {profile.tracks.map((track) => (
                  <li key={track.track_id} className="rounded-lg border border-border p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant={track.status === 'tracking' ? 'success' : 'default'}>{track.status}</Badge>
                      <span className="text-sm font-medium">{track.hypothesis_key}</span>
                      <span className="text-xs text-muted-foreground">{track.horizon}｜{track.business_layer}</span>
                      {track.next_review_at ? <span className="text-xs text-muted-foreground">复核 {track.next_review_at.slice(0, 10)}</span> : null}
                    </div>
                    {track.hypothesis ? <p className="mt-1 text-sm">{track.hypothesis}</p> : null}
                    {track.invalidation ? <p className="mt-1 text-xs text-rose-600">失效条件：{track.invalidation}</p> : null}
                    <ol className="mt-2 space-y-0.5 text-xs text-muted-foreground">
                      {track.events.map((event, index) => (
                        <li key={`${track.track_id}-${index}`}>
                          {event.observed_at?.slice(0, 19).replace('T', ' ') ?? '—'} {event.event_type}
                          {event.summary ? `｜${event.summary}` : ''}
                        </li>
                      ))}
                    </ol>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      ) : null}
    </AppPage>
  );
};

export default StockProfilePage;
