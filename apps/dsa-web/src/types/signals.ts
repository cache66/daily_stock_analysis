export interface SignalSnapshotListItem {
  code: string;
  name?: string | null;
  signalDate?: string | null;
  industry?: string | null;
  reasonSummary?: string | null;
  industryLogic?: string | null;
  newsLogic?: string | null;
  technicalLogic?: string | null;
  themeLabel?: string | null;
  latestPreviousHitDate?: string | null;
  previousHitCount: number;
  daysSincePreviousHit?: number | null;
  isConsecutiveSignal: boolean;
  close?: number | null;
  latestHigh?: number | null;
  windowHigh?: number | null;
}

export interface SignalSnapshotCompareItem {
  addedItems: Array<{ code: string; name?: string | null }>;
  droppedItems: Array<{ code: string; name?: string | null }>;
  signalDate: string;
  totalCount: number;
  continuousCount: number;
  topCodes: string[];
  addedCount: number;
  droppedCount: number;
  addedCodes: string[];
  droppedCodes: string[];
}

export interface SignalSnapshotStreakItem {
  code: string;
  name?: string | null;
  industry?: string | null;
  currentStreakCount: number;
  longestStreakCount: number;
  currentStreakStartDate?: string | null;
  currentStreakEndDate?: string | null;
  latestSignalDate?: string | null;
  latestHigh?: number | null;
  close?: number | null;
  themeLabel?: string | null;
}

export interface SignalSnapshotListResponse {
  signalType: string;
  signalDate?: string | null;
  signalDateFrom?: string | null;
  signalDateTo?: string | null;
  total: number;
  page: number;
  pageSize: number;
  compareSummary: SignalSnapshotCompareItem[];
  streakLeaderboard: SignalSnapshotStreakItem[];
  items: SignalSnapshotListItem[];
}

export interface SignalSnapshotHistoryItem extends SignalSnapshotListItem {
  newHighWindow?: number | null;
  historySource?: string | null;
  totalMarketCap?: number | null;
}

export interface SignalContinuitySummary {
  isCurrentStreak: boolean;
  currentStreakCount: number;
  currentStreakStartDate?: string | null;
  currentStreakEndDate?: string | null;
  longestStreakCount: number;
  longestStreakStartDate?: string | null;
  longestStreakEndDate?: string | null;
}

export interface SignalDrawdownSummary {
  anchorClose?: number | null;
  maxSignalHigh?: number | null;
  maxSignalHighDate?: string | null;
  distanceFromMaxSignalHighPct?: number | null;
  latestSignalHigh?: number | null;
  latestSignalDate?: string | null;
  distanceFromLatestSignalHighPct?: number | null;
}

export interface SignalSnapshotHistoryResponse {
  signalType: string;
  code: string;
  days: number;
  total: number;
  continuity: SignalContinuitySummary;
  drawdown: SignalDrawdownSummary;
  items: SignalSnapshotHistoryItem[];
}
