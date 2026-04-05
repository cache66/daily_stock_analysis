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
    lines.push(`- 行业: ${item.industry || '--'}`);
    lines.push(`- 主题: ${item.themeLabel || '--'}`);
    lines.push(`- 连续新高: ${item.isConsecutiveSignal ? '是' : '否'}`);
    lines.push(`- 最新 high / close: ${item.latestHigh ?? '--'} / ${item.close ?? '--'}`);
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
