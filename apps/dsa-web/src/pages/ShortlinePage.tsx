import type React from 'react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { CalendarDays, RefreshCw } from 'lucide-react';
import { AppPage, Badge, Card, InlineAlert, Loading, PageHeader } from '../components/common';
import {
  workbenchApi,
  type WorkbenchIntersection,
  type WorkbenchMethodDetail,
  type WorkbenchMethodSummary,
} from '../api/workbench';

const pct = (value?: number | null, digits = 1) => (value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`);

export const ShortlinePage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const methodId = params.get('method') ?? '';
  const [date, setDate] = useState('');
  const [methods, setMethods] = useState<WorkbenchMethodSummary[]>([]);
  const [intersections, setIntersections] = useState<WorkbenchIntersection[]>([]);
  const [detail, setDetail] = useState<WorkbenchMethodDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const activeId = useMemo(() => {
    if (methodId) return methodId;
    return methods[0]?.id ?? '';
  }, [methodId, methods]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const list = await workbenchApi.getMethods(date || undefined);
      setMethods(list.methods);
      setIntersections(list.intersections);
      const target = methodId || list.methods[0]?.id;
      if (target) {
        setDetail(await workbenchApi.getMethod(target, date || undefined));
      } else {
        setDetail(null);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
      setDetail(null);
    } finally {
      setLoading(false);
    }
  }, [date, methodId]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <AppPage>
      <PageHeader
        title="短线研究"
        description="每种方法只回答一个问题：找什么、现在到哪一步、什么条件下算确认或失效"
        actions={
          <div className="flex items-center gap-2">
            <Link
              to="/workbench"
              className="inline-flex items-center gap-1 rounded-md border border-input px-2 py-1 text-sm hover:bg-accent"
            >
              ← 返回工作台
            </Link>
            {methodId ? (
              <button
                type="button"
                onClick={() => setParams({})}
                className="inline-flex items-center gap-1 rounded-md border border-input px-2 py-1 text-sm hover:bg-accent"
              >
                ← 全部方法
              </button>
            ) : null}
            <CalendarDays className="h-4 w-4 text-muted-foreground" />
            <input
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
              className="rounded-md border border-input bg-background px-2 py-1 text-sm"
              aria-label="切换交易日"
            />
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

      <div className="mt-3 flex flex-wrap gap-2">
        {methods.map((method) => {
          const active = method.id === activeId;
          return (
            <button
              key={method.id}
              type="button"
              onClick={() => setParams({ method: method.id })}
              className={`rounded-full border px-3 py-1 text-sm ${active ? 'border-primary bg-primary/10 text-primary' : 'border-border hover:bg-accent'}`}
            >
              {method.name}
              <span className="ml-2 text-xs text-muted-foreground">{method.count}</span>
            </button>
          );
        })}
      </div>

      {detail ? (
        <div className="mt-4 space-y-4">
          <Card title={detail.method.name}>
            <dl className="grid gap-2 text-sm md:grid-cols-2">
              <div>
                <dt className="text-muted-foreground">研究问题</dt>
                <dd>{detail.method.question}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">持有期</dt>
                <dd>{detail.method.horizon}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">确认条件</dt>
                <dd>{detail.method.confirm_rule}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">失效条件</dt>
                <dd>{detail.method.invalid_rule}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">数据来源</dt>
                <dd>{detail.method.producer ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">行业排除</dt>
                <dd>{detail.excluded_industries.length ? detail.excluded_industries.join('、') : '该方法不做行业过滤'}</dd>
              </div>
            </dl>
            <div className="mt-3 flex flex-wrap gap-4 text-sm">
              <span>今日发现 <b>{detail.count}</b></span>
              <span>新建跟踪 {detail.new_today}</span>
              <span>跟踪中 {detail.tracking}</span>
              <span className="text-emerald-600">相对确认 {detail.confirming.length}</span>
              <span className="text-rose-600">已失效 {detail.invalid.length}</span>
            </div>
          </Card>

          <Card title={`今日候选（${detail.candidates.length}）`}>
            {detail.candidates.length === 0 ? (
              <p className="text-sm text-muted-foreground">该方法今日没有符合条件的新发现（零项也是有效结果）。</p>
            ) : (
              <div className="space-y-3">
                {detail.candidates.map((item) => (
                  <div key={`${item.code}-${item.source_signal ?? ''}`} className="rounded-lg border border-border p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link className="font-medium text-primary hover:underline" to={`/workbench/stock/${item.code}?as_of=${detail.as_of}`}>
                        {item.code}
                      </Link>
                      <span>{item.name}</span>
                      {item.industry ? <Badge>{item.industry}</Badge> : null}
                      {item.source_signal ? <Badge variant="warning">{item.source_signal}</Badge> : null}
                      {item.close ? <span className="text-sm">现价 {item.close}</span> : null}
                      {item.chg_today != null ? (
                        <span className={`text-sm ${item.chg_today >= 0 ? 'text-rose-600' : 'text-emerald-600'}`}>
                          当日 {pct(item.chg_today, 2)}
                        </span>
                      ) : null}
                    </div>
                    <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-muted-foreground">
                      {(item.why ?? []).map((line) => (
                        <li key={line}>{line}</li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </div>
      ) : null}

      {intersections.length > 0 ? (
        <Card title="方法交集" className="mt-4">
          <ul className="space-y-1 text-sm">
            {intersections.map((item) => (
              <li key={item.id}>
                <b>{item.name}</b>
                <Badge variant={item.enabled ? 'success' : 'warning'}>{item.enabled ? '已启用' : '未启用'}</Badge>
                <span className="ml-2 text-muted-foreground">{item.rule}</span>
              </li>
            ))}
          </ul>
        </Card>
      ) : null}
    </AppPage>
  );
};

export default ShortlinePage;
