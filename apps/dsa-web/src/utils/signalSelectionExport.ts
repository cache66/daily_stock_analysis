import type { SignalSnapshotListItem } from '../types/signals';

export type SignalSelectionExportMeta = {
  signalType: string;
  dateLabel: string;
  selectionLabel: string;
  groupedBy?: string;
  generatedAt?: Date;
};

function formatGeneratedAt(value: Date): string {
  return value.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function buildFileStamp(value: Date): string {
  const date = value.toISOString().slice(0, 10).replace(/-/g, '');
  const hh = `${value.getHours()}`.padStart(2, '0');
  const mm = `${value.getMinutes()}`.padStart(2, '0');
  return `${date}_${hh}${mm}`;
}

export function formatSignalSelectionAsMarkdown(
  items: SignalSnapshotListItem[],
  meta: SignalSelectionExportMeta,
): string {
  const generatedAt = meta.generatedAt ?? new Date();
  const uniqueCodes = new Set(items.map((item) => item.code).filter(Boolean));
  const lines: string[] = [
    '# 信号快照导出',
    '',
    `生成时间: ${formatGeneratedAt(generatedAt)}`,
    `信号类型: ${meta.signalType}`,
    `查询范围: ${meta.dateLabel}`,
    `选择范围: ${meta.selectionLabel}`,
  ];

  if (meta.groupedBy) {
    lines.push(`分组视角: ${meta.groupedBy}`);
  }

  lines.push(
    '',
    '## 统计',
    '',
    `- 快照条数: ${items.length}`,
    `- 股票数量: ${uniqueCodes.size}`,
    '',
    '## 明细',
    '',
  );

  for (const item of items) {
    lines.push(`### ${item.name || item.code} (${item.code})`);
    lines.push(`- 信号日期: ${item.signalDate || '--'}`);
    if (item.eventDate) lines.push(`- 事件日期: ${item.eventDate}`);
    if (item.subthemeKey) lines.push(`- Subtheme: ${item.subthemeKey}`);
    if (item.chainRole) lines.push(`- Chain Role: ${item.chainRole}`);
    if (item.passThroughDirection) lines.push(`- Pass Through: ${item.passThroughDirection}`);
    if (item.earningsValidationStatus) lines.push(`- Earnings Validation: ${item.earningsValidationStatus}`);
    if (item.earningsReleaseProbability) lines.push(`- Earnings Release Probability: ${item.earningsReleaseProbability}`);
    if (item.directness) lines.push(`- Directness: ${item.directness}`);
    if (item.matchedExampleBucket) lines.push(`- Matched Example Bucket: ${item.matchedExampleBucket}`);
    if (item.matchedExampleName) lines.push(`- Matched Example Name: ${item.matchedExampleName}`);
    if (item.leaderType) lines.push(`- Leader Type: ${item.leaderType}`);
    if (item.leaderProbability) lines.push(`- Leader Probability: ${item.leaderProbability}`);
    if (item.recognizabilityScore != null) lines.push(`- Recognizability Score: ${item.recognizabilityScore}`);
    if (item.sustainedGrowthScore != null) lines.push(`- Sustained Growth Score: ${item.sustainedGrowthScore}`);
    if (item.liquidityScore != null) lines.push(`- Liquidity Score: ${item.liquidityScore}`);
    if (item.sectorLeadershipScore != null) lines.push(`- Sector Leadership Score: ${item.sectorLeadershipScore}`);
    if (item.relativeStrengthScore != null) lines.push(`- Relative Strength Score: ${item.relativeStrengthScore}`);
    if (item.catalystScore != null) lines.push(`- Catalyst Score: ${item.catalystScore}`);
    if (item.valuationScore != null) lines.push(`- Valuation Score: ${item.valuationScore}`);
    if (item.dividendScore != null) lines.push(`- Dividend Score: ${item.dividendScore}`);
    if (item.logicConsensusScore != null) lines.push(`- Logic Consensus Score: ${item.logicConsensusScore}`);
    if (item.capitalConsensusScore != null) lines.push(`- Capital Consensus Score: ${item.capitalConsensusScore}`);
    lines.push(`- 行业: ${item.industry || '--'}`);
    lines.push(`- 主题: ${item.themeLabel || '--'}`);
    lines.push(`- 连续信号: ${item.isConsecutiveSignal ? '是' : '否'}`);
    lines.push(`- 最新 high / close: ${item.latestHigh ?? '--'} / ${item.close ?? '--'}`);
    lines.push(`- 年内涨幅: ${item.ytdReturnPct != null ? `${item.ytdReturnPct.toFixed(2)}%` : '--'}`);
    lines.push(`- 历史命中次数: ${item.previousHitCount ?? 0}`);
    lines.push(`- 上次命中日期: ${item.latestPreviousHitDate || '--'}`);
    lines.push(`- 摘要: ${item.reasonSummary || '--'}`);
    lines.push(`- 行业逻辑: ${item.industryLogic || '--'}`);
    lines.push(`- 消息逻辑: ${item.newsLogic || '--'}`);
    lines.push(`- 技术逻辑: ${item.technicalLogic || '--'}`);
    lines.push('');
  }

  return lines.join('\n');
}

export function downloadSignalSelectionMarkdown(
  items: SignalSnapshotListItem[],
  meta: SignalSelectionExportMeta,
): void {
  const generatedAt = meta.generatedAt ?? new Date();
  const content = formatSignalSelectionAsMarkdown(items, { ...meta, generatedAt });
  const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' });
  const filename = `signal_selection_${buildFileStamp(generatedAt)}.md`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}

export function downloadSignalSelectionJson(
  items: SignalSnapshotListItem[],
  meta: SignalSelectionExportMeta,
): void {
  const generatedAt = meta.generatedAt ?? new Date();
  const payload = JSON.stringify(
    {
      generatedAt: generatedAt.toISOString(),
      signalType: meta.signalType,
      dateLabel: meta.dateLabel,
      selectionLabel: meta.selectionLabel,
      groupedBy: meta.groupedBy ?? null,
      itemCount: items.length,
      items,
    },
    null,
    2,
  );
  const blob = new Blob([payload], { type: 'application/json;charset=utf-8' });
  const filename = `signal_selection_${buildFileStamp(generatedAt)}.json`;
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  URL.revokeObjectURL(url);
}
