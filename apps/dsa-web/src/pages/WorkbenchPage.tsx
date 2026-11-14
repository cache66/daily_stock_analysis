import type React from 'react';
import { useCallback, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, ArrowRight, CalendarDays, RefreshCw } from 'lucide-react';
import { AppPage, Badge, Card, InlineAlert, Loading, PageHeader } from '../components/common';
import { workbenchApi, type WorkbenchHome } from '../api/workbench';

const STATUS_TONE: Record<string, 'default' | 'success' | 'warning' | 'danger'> = {
  ok: 'success',
  limited: 'warning',
  pending: 'warning',
  missing: 'danger',
};

function money(value?: number | null): string {
  if (value === null || value === undefined) return '—';
  const yi = value / 1e8;
  if (Math.abs(yi) >= 1) return `${yi >= 0 ? '+' : ''}${yi.toFixed(2)}亿`;
  return `${(value / 1e4).toFixed(0)}万`;
}

function pct(value?: number | null, digits = 1): string {
  if (value === null || value === undefined) return '—';
  return `${(value * 100).toFixed(digits)}%`;
}

export const WorkbenchPage: React.FC = () => {
  const [date, setDate] = useState('');
  const [data, setData] = useState<WorkbenchHome | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      setData(await workbenchApi.getHome(date || undefined, force));
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    void load();
  }, [load]);

  const review = data?.sections.market_review ?? null;
  const followup = data?.sections.broken_followup ?? null;

  return (
    <AppPage>
      <PageHeader
        title="每日复盘"
        description="先看今天谁封住、谁炸了、板块怎么走，再看各短线方法进展，最后看长线变化与待办"
        actions={
          <div className="flex items-center gap-2">
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
              onClick={() => void load(true)}
              title="强制重算（默认直接读缓存）"
              className="inline-flex items-center gap-1 rounded-md border border-input px-2 py-1 text-sm hover:bg-accent"
            >
              <RefreshCw className="h-3.5 w-3.5" /> 重算
            </button>
          </div>
        }
      />

      {loading ? <Loading /> : null}
      {error ? <InlineAlert variant="danger" title="加载失败" message={error} /> : null}

      {data && review ? (
        <div className="space-y-4">
          <Card>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              <Badge variant={STATUS_TONE[data.market.status] ?? 'default'}>
                市场：{data.market.status === 'ok' ? '已计算' : data.market.status}
              </Badge>
              <span className="text-muted-foreground">
                个股/横截面 {data.as_of} · 复盘 {data.review_as_of ?? data.as_of}
              </span>
              {data.generated_at ? (
                <span className="text-xs text-muted-foreground">
                  生成 {data.generated_at.slice(11, 19)}
                  {data.cached ? '（缓存）' : '（刚重算）'}
                </span>
              ) : null}
              <span>{data.market.evidence ?? data.market.reason ?? '—'}</span>
            </div>
            {data.review_as_of && data.review_as_of !== data.as_of ? (
              <p className="mt-2 text-xs text-amber-600">
                {data.review_as_of} 日线只同步到 {data.data_coverage?.bars ?? 0}/{data.data_coverage?.peak ?? 0} 只，
                涨幅与横截面指标按 {data.as_of} 收盘计算；涨停/炸板复盘用 {data.review_as_of} 收盘数据。
              </p>
            ) : null}
          </Card>

          <Card title={`① ${review.as_of} 市场怎么走`}>
            <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
              <span>
                涨停 <b className="text-rose-600">{review.summary.limit_up ?? 0}</b> 家
              </span>
              <span>
                炸板 <b className="text-amber-600">{review.summary.broken ?? 0}</b> 家
              </span>
              <span>
                跌停 <b className="text-emerald-600">{review.summary.limit_down ?? 0}</b> 家
              </span>
              <span>
                封板率 <b>{pct(review.summary.seal_rate)}</b>
              </span>
              <span>
                最高 <b>{review.summary.highest_boards ?? 0}</b> 连板
              </span>
              {review.summary.seal_amount_total_yi != null ? (
                <span className="text-muted-foreground">封单合计 {review.summary.seal_amount_total_yi} 亿</span>
              ) : null}
              <Badge variant={review.status === 'ok' ? 'success' : 'warning'}>
                {review.source === 'eastmoney_zt_pool' ? '东财涨停池' : review.source === 'local_daily_approx' ? '日线近似' : '缺失'}
              </Badge>
            </div>
            {review.ladder.length > 0 ? (
              <div className="mt-3 flex flex-wrap gap-2">
                {review.ladder.map((tier) => (
                  <span key={tier.boards} className="rounded-md border border-border px-2 py-1 text-xs">
                    <b>{tier.label}</b> {tier.count} 家
                    <span className="ml-1 text-muted-foreground">{tier.names.slice(0, 3).join('、')}</span>
                  </span>
                ))}
              </div>
            ) : null}
            {review.status !== 'ok' ? (
              <p className="mt-2 text-xs text-muted-foreground">{review.limitations[0] ?? review.reason}</p>
            ) : null}
          </Card>

          <Card title="② 板块：谁在带，谁在崩">
            {review.sectors.length === 0 ? (
              <p className="text-sm text-muted-foreground">今日没有可统计的涨停板块。</p>
            ) : (
              <table className="w-full text-left text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1">行业（涨停池口径）</th>
                    <th>板块涨幅</th>
                    <th>涨停</th>
                    <th>炸板</th>
                    <th>涨/跌家数</th>
                    <th>领涨股</th>
                  </tr>
                </thead>
                <tbody>
                  {review.sectors.map((row) => (
                    <tr key={row.industry} className="border-t border-border/50">
                      <td className="py-1">{row.industry}</td>
                      <td className={row.board_change == null ? '' : row.board_change >= 0 ? 'text-rose-600' : 'text-emerald-600'}>
                        {row.board_change == null ? '—' : `${row.board_change.toFixed(2)}%`}
                      </td>
                      <td>{row.limit_up}</td>
                      <td>{row.broken > 0 ? <span className="text-amber-600">{row.broken}</span> : 0}</td>
                      <td className="text-muted-foreground">
                        {row.up == null ? '—' : `${row.up}/${row.down}`}
                      </td>
                      <td className="text-muted-foreground">
                        {row.leader ? `${row.leader} ${row.leader_change?.toFixed(2)}%` : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <p className="mt-2 text-xs text-muted-foreground">
              口径：涨停家数来自东财涨停池「所属行业」（截断名），板块涨幅/涨跌家数/领涨股来自东财行业板块当日行情，按名称最长前缀匹配；两者分类不完全一致。
            </p>
          </Card>

          <Card title={review.broken.length > 0 ? `③ 今天炸板的 ${review.broken.length} 只` : '③ 今天没有炸板'}>
            {review.broken.length === 0 ? (
              <p className="text-sm text-muted-foreground">当日无炸板记录（或取数缺失，见下方口径）。</p>
            ) : (
              <table className="w-full text-left text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1">代码</th>
                    <th>名称</th>
                    <th>炸板次数</th>
                    <th>现价 / 涨停价</th>
                    <th>涨幅</th>
                    <th>行业</th>
                  </tr>
                </thead>
                <tbody>
                  {review.broken.slice(0, 12).map((row) => (
                    <tr key={row.code} className="border-t border-border/50">
                      <td className="py-1">
                        <Link className="text-primary hover:underline" to={`/workbench/stock/${row.code}?as_of=${review.as_of}`}>
                          {row.code}
                        </Link>
                      </td>
                      <td>{row.name}</td>
                      <td className="text-amber-600">{row.break_count ?? 0}</td>
                      <td className="text-muted-foreground">
                        {row.price ?? '—'} / {row.limit_price ?? '—'}
                      </td>
                      <td className={(row.change_pct ?? 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'}>
                        {row.change_pct == null ? '—' : `${row.change_pct.toFixed(2)}%`}
                      </td>
                      <td className="text-muted-foreground">{row.industry ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Card>

          <Card title={`④ 涨停 ${review.limit_up.length} 只（按连板数排序）`}>
            {review.limit_up.length === 0 ? (
              <p className="text-sm text-muted-foreground">当日涨停池为空或取数失败。</p>
            ) : (
              <table className="w-full text-left text-xs">
                <thead className="text-muted-foreground">
                  <tr>
                    <th className="py-1">代码</th>
                    <th>名称</th>
                    <th>连板</th>
                    <th>封单(亿)</th>
                    <th>首封</th>
                    <th>涨停统计</th>
                    <th>行业</th>
                  </tr>
                </thead>
                <tbody>
                  {review.limit_up.slice(0, 15).map((row) => (
                    <tr key={row.code} className="border-t border-border/50">
                      <td className="py-1">
                        <Link className="text-primary hover:underline" to={`/workbench/stock/${row.code}?as_of=${review.as_of}`}>
                          {row.code}
                        </Link>
                      </td>
                      <td>{row.name}</td>
                      <td className="text-rose-600">{row.boards ?? 1}</td>
                      <td>{row.seal_amount == null ? '—' : (row.seal_amount / 1e8).toFixed(2)}</td>
                      <td className="text-muted-foreground">{row.first_seal ?? '—'}</td>
                      <td className="text-muted-foreground">{row.limit_stat ?? '—'}</td>
                      <td className="text-muted-foreground">{row.industry ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {review.limitations.length > 0 ? (
              <ul className="mt-2 list-disc pl-5 text-xs text-muted-foreground">
                {review.limitations.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            ) : null}
          </Card>

          <Card title="⑤ 炸板后跟踪（按炸板日 → 之后走势）">
            {!followup ? (
              <p className="text-sm text-muted-foreground">暂无炸板跟踪数据。</p>
            ) : followup.groups.length === 0 ? (
              <p className="text-sm text-muted-foreground">{followup.reason ?? '近 5 日没有炸板记录。'}</p>
            ) : (
              <div className="space-y-4">
                <div className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
                  <span>
                    炸板事件 <b>{followup.summary.events ?? 0}</b> 个（每日按成交额取前 {followup.per_day ?? '—'} 只）
                  </span>
                  <span className="text-rose-600">大单承接 {followup.summary.supported ?? 0}</span>
                  <span className="text-emerald-600">大单出货 {followup.summary.dumped ?? 0}</span>
                  <span className="text-muted-foreground">已收复 {followup.summary.recovered ?? 0}</span>
                  <span className="text-muted-foreground">待观察 {followup.summary.pending ?? 0}</span>
                  <span className="text-muted-foreground">
                    评分：重点观察 {followup.summary.grades?.['重点观察'] ?? 0} / 观察{' '}
                    {followup.summary.grades?.['观察'] ?? 0} / 不跟 {followup.summary.grades?.['不跟'] ?? 0}
                  </span>
                  <span className="text-muted-foreground">
                    首次触板 {followup.summary.first_touch ?? 0} / 连续试盘{' '}
                    {followup.summary.repeat_touch ?? 0}
                  </span>
                  {(followup.hot_industries ?? [])
                    .filter((item) => item.count >= 4)
                    .slice(0, 4)
                    .map((item) => (
                      <span
                        key={item.industry}
                        className={item.count >= 7 ? 'text-amber-600' : 'text-muted-foreground'}
                        title={item.count >= 7 ? '板块过热：该行业炸板票历史后续表现最差' : '板块有一定扩散'}
                      >
                        {item.industry} {item.count}
                      </span>
                    ))}
                </div>

                {followup.groups.map((group) => (
                  <div key={group.break_date} className="space-y-1">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-medium">炸板日 {group.break_date}</span>
                      <span className="text-muted-foreground">
                        {group.count} 只 · 成交额 ≥5 亿 {group.big_amount} 只
                      </span>
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[1220px] text-left text-xs">
                        <thead className="text-muted-foreground">
                          <tr>
                            <th className="py-1">代码</th>
                            <th>名称</th>
                            <th>试盘10</th>
                            <th>板块涨停</th>
                            <th>分数</th>
                            <th>量比</th>
                            <th>炸板瞬间5m</th>
                            <th>炸板后大单净</th>
                            <th>炸板</th>
                            <th>成交额</th>
                            <th>换手</th>
                            <th>主力</th>
                            <th>大单</th>
                            <th>小单</th>
                            <th>T+1</th>
                            <th>T+2</th>
                            <th>至今</th>
                            <th>状态</th>
                            <th>判定</th>
                          </tr>
                        </thead>
                        <tbody>
                          {group.rows.map((row) => (
                            <tr key={`${row.break_date}-${row.code}`} className="border-t border-border/50">
                              <td className="py-1">
                                <Link
                                  className="text-primary hover:underline"
                                  to={`/workbench/stock/${row.code}?as_of=${followup.as_of}`}
                                >
                                  {row.code}
                                </Link>
                              </td>
                              <td>{row.name ?? '—'}</td>
                              <td
                                className="text-muted-foreground"
                                title={`近 10 个交易日触板 ${row.touch10 ?? 0} 次（封 ${row.seal10 ?? 0} / 炸 ${row.break10 ?? 0}），同日多次开板只算 1 次`}
                              >
                                {(row.touch10 ?? 0) >= 3 ? (
                                  <b className="text-foreground">{row.touch10} 次</b>
                                ) : (
                                  `${row.touch10 ?? 0} 次`
                                )}
                              </td>
                              <td
                                className={(row.industry_limit ?? 0) >= 7 ? 'text-amber-600' : 'text-muted-foreground'}
                                title="炸板日该行业封板家数；≥7 只属板块过热（历史后续表现最差）"
                              >
                                {row.industry_limit ?? 0}
                              </td>
                              <td title={(row.score_reasons ?? []).join('；')}>
                                {row.grade === '重点观察' ? (
                                  <Badge variant="success">{row.score ?? '—'} 重点</Badge>
                                ) : row.grade === '观察' ? (
                                  <Badge variant="warning">{row.score ?? '—'} 观察</Badge>
                                ) : (
                                  <Badge>{row.score ?? '—'} 不跟</Badge>
                                )}
                              </td>
                              <td className="text-muted-foreground">
                                {row.amount_ratio == null ? '—' : `${row.amount_ratio.toFixed(1)}x`}
                              </td>
                              <td className="text-muted-foreground">
                                {row.break_window?.moment_ratio == null ? (
                                  '—'
                                ) : (
                                  <span
                                    title={`炸板瞬间 5 分钟成交占全天 ${(row.break_window.moment_ratio * 100).toFixed(1)}%；开板至收盘累计 ${
                                      row.break_window.open_ratio == null
                                        ? '—'
                                        : `${(row.break_window.open_ratio * 100).toFixed(1)}%`
                                    }（仅参考）· 口径 ${
                                      row.break_window.method === 'min5' ? '5 分钟线' : '3 秒分笔'
                                    }${row.break_window.break_time ? ` · 跌破涨停 ${row.break_window.break_time}` : ''}`}
                                  >
                                    {(row.break_window.moment_ratio * 100).toFixed(1)}%
                                  </span>
                                )}
                              </td>
                              <td className="text-muted-foreground">
                                {row.break_window?.big_net == null ? (
                                  '—'
                                ) : (
                                  <span
                                    className={
                                      (row.break_window.super_net ?? 0) + row.break_window.big_net >= 0
                                        ? 'text-rose-600'
                                        : 'text-emerald-600'
                                    }
                                    title={`炸板后 超大单净 ${money(row.break_window.super_net)} / 大单净 ${money(
                                      row.break_window.big_net,
                                    )}（大单净卖 + 资金净流出 = 散户接盘，直接降级）`}
                                  >
                                    {money((row.break_window.super_net ?? 0) + row.break_window.big_net)}
                                  </span>
                                )}
                              </td>
                              <td>{row.break_count ?? 1}</td>
                              <td className={(row.amount ?? 0) >= 5e8 ? 'font-medium' : 'text-muted-foreground'}>
                                {money(row.amount)}
                              </td>
                              <td className="text-muted-foreground">
                                {row.turnover == null ? '—' : `${row.turnover.toFixed(1)}%`}
                              </td>
                              <td
                                className={(row.funds.main ?? 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'}
                                title={row.funds.source === 'ths' ? '同花顺主动买卖净额（东财资金流不可用时的兜底，口径不同）' : '东财资金流：主力净额（超大单+大单）'}
                              >
                                {money(row.funds.main)}
                                {row.funds.source === 'ths' ? <span className="ml-1 text-[10px] text-muted-foreground">THS</span> : null}
                              </td>
                              <td className="text-muted-foreground">{money(row.funds.big)}</td>
                              <td className="text-muted-foreground">{money(row.funds.small)}</td>
                              <td className="text-muted-foreground">{pct(row.followup.t1_ret)}</td>
                              <td className="text-muted-foreground">{pct(row.followup.t2_ret)}</td>
                              <td className={(row.followup.latest_ret ?? 0) >= 0 ? 'text-rose-600' : 'text-emerald-600'}>
                                {pct(row.followup.latest_ret)}
                              </td>
                              <td>
                                {row.followup.held ? (
                                  <Badge variant="success">已站稳</Badge>
                                ) : row.followup.recovered ? (
                                  <Badge variant="warning">冲高回落</Badge>
                                ) : row.followup.days.length === 0 ? (
                                  <Badge>待观察</Badge>
                                ) : (
                                  <Badge variant="danger">未修复</Badge>
                                )}
                              </td>
                              <td className="text-muted-foreground">{row.label}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ))}

                {(followup.watchlist ?? []).length > 0 ? (
                  <div className="space-y-1">
                    <div className="text-sm font-medium">
                      近 10 日连续试盘榜（触板 ≥2 次，含封板与炸板）
                    </div>
                    <div className="text-xs text-muted-foreground">
                      这些票是「资金反复回来做」的池子：历史上近 10 日试盘 ≥3 次的炸板票，3 日内再涨停率 28%，
                      首次触板只有 7.9%。
                    </div>
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[760px] text-left text-xs">
                        <thead className="text-muted-foreground">
                          <tr>
                            <th className="py-1">代码</th>
                            <th>名称</th>
                            <th>行业</th>
                            <th>试盘</th>
                            <th>封/炸</th>
                            <th>封板率</th>
                            <th>最近一次</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(followup.watchlist ?? []).map((item) => (
                            <tr key={item.code} className="border-t border-border/50">
                              <td className="py-1">
                                <Link
                                  className="text-primary hover:underline"
                                  to={`/workbench/stock/${item.code}?as_of=${followup.as_of}`}
                                >
                                  {item.code}
                                </Link>
                              </td>
                              <td>{item.name ?? '—'}</td>
                              <td className="text-muted-foreground">{item.industry ?? '—'}</td>
                              <td>{item.touch} 次</td>
                              <td className="text-muted-foreground">
                                {item.seal} / {item.break}
                              </td>
                              <td className="text-muted-foreground">
                                {item.seal_rate == null ? '—' : `${(item.seal_rate * 100).toFixed(0)}%`}
                              </td>
                              <td className="text-muted-foreground">
                                {item.last_date ?? '—'}{' '}
                                {item.last_kind === 'seal' ? (
                                  <span className="text-rose-600">封</span>
                                ) : (
                                  <span className="text-emerald-600">炸</span>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : null}

                <p className="text-xs text-muted-foreground">
                  分数 = 用 25 个交易日、709 个炸板事件拟合的逻辑回归概率（AUC 0.63，基准 T+1 胜率 37.7%）：
                  量比（成交额 ÷ 20 日均额）权重最大，其次炸板时段成交占比、换手、炸板次数、散户净额；悬停分数看理由。
                  「炸板瞬间5m」= 首次跌破涨停价后 5 分钟内的成交额 / 当日成交额（悬停可看开板至收盘的累计口径，仅参考）；「炸板后大单净」= 开板后超大单 + 大单的净买卖额，只有当日炸板有逐笔方向，历史日期显示「—」（此时打分改用当日资金流分档）。炸板瞬间占比只展示不计分；大单净卖且资金净流出会被硬规则压到 30 分以下。
                  主力列标 THS 表示东财资金流不可用、改用同花顺主动买卖净额兜底（无分档，不可与东财数字相减）。
                  「试盘10」= 近 10 个交易日里该票封板或炸板的天数（同日多次开板只算 1 次），
                  反映「资金有没有反复回来做」；「板块涨停」= 炸板日该行业封板家数，≥7 只属板块过热（兑现）。
                  它只是排序工具，不是买入信号。
                </p>
                <ul className="list-disc pl-5 text-xs text-muted-foreground">
                  {followup.limitations.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              </div>
            )}
          </Card>

          <Card title="⑥ 我的短线研究（按方法）">
            <div className="grid gap-3 md:grid-cols-2">
              {data.sections.shortline.map((method) => (
                <Link
                  key={method.id}
                  to={`/workbench/shortline?method=${method.id}`}
                  className="rounded-lg border border-border p-3 transition hover:bg-accent"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{method.name}</span>
                    <ArrowRight className="h-4 w-4 text-muted-foreground" />
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{method.question}</p>
                  <div className="mt-2 flex flex-wrap gap-3 text-xs">
                    <span>发现 <b>{method.count}</b></span>
                    <span>跟踪 {method.tracking}</span>
                    <span className="text-emerald-600">确认 {method.confirming}</span>
                    <span className="text-rose-600">失效 {method.invalid}</span>
                    {!method.default_daily ? <Badge variant="warning">非默认每日</Badge> : null}
                  </div>
                </Link>
              ))}
            </div>
          </Card>

          <Card title="⑦ 长线公司有什么新变化">
            {data.sections.longterm_changes.status !== 'ok' ? (
              <p className="text-sm text-muted-foreground">{data.sections.longterm_changes.reason}</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {data.sections.longterm_changes.rows.slice(0, 8).map((row) => (
                  <li key={`${row.code}-${row.hypothesis_key}`}>
                    <Link className="text-primary hover:underline" to={`/workbench/stock/${row.code}?as_of=${data.as_of}`}>
                      {row.code}
                    </Link>
                    <span className="ml-2 text-muted-foreground">{row.hypothesis || row.hypothesis_key}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          <Card title="⑧ 今天待处理什么">
            {data.sections.todos.length === 0 ? (
              <p className="text-sm text-muted-foreground">没有到期的复核事项。</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {data.sections.todos.map((todo) => (
                  <li key={`${todo.track_id ?? todo.code}-${todo.hypothesis_key}`} className="flex items-center gap-2">
                    <AlertTriangle className="h-3.5 w-3.5 text-amber-500" />
                    <Link className="text-primary hover:underline" to={`/workbench/stock/${todo.code}?as_of=${data.as_of}`}>
                      {todo.code}
                    </Link>
                    <span className="text-muted-foreground">{todo.hypothesis_key}</span>
                    <span className="text-xs text-muted-foreground">到期 {todo.due ?? '未设置'}</span>
                  </li>
                ))}
              </ul>
            )}
          </Card>

          {data.limitations.length > 0 ? (
            <Card title="数据限制">
              <ul className="list-disc pl-5 text-xs text-muted-foreground">
                {data.limitations.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </Card>
          ) : null}
        </div>
      ) : null}
    </AppPage>
  );
};

export default WorkbenchPage;
