export interface SignalSnapshotListItem {
  code: string;
  name?: string | null;
  signalDate?: string | null;
  eventDate?: string | null;
  boardName?: string | null;
  boardRank?: number | null;
  boardCandidateCount?: number | null;
  sourceSignalType?: string | null;
  sourceSignalDate?: string | null;
  subthemeKey?: string | null;
  chainRole?: string | null;
  passThroughDirection?: string | null;
  earningsValidationStatus?: string | null;
  earningsReleaseProbability?: string | null;
  directness?: string | null;
  matchedExampleBucket?: string | null;
  matchedExampleName?: string | null;
  recognizabilityScore?: number | null;
  sustainedGrowthScore?: number | null;
  liquidityScore?: number | null;
  valuationScore?: number | null;
  dividendScore?: number | null;
  logicConsensusScore?: number | null;
  capitalConsensusScore?: number | null;
  leaderProbability?: string | null;
  leaderType?: string | null;
  sectorLeadershipScore?: number | null;
  relativeStrengthScore?: number | null;
  catalystScore?: number | null;
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
  totalMarketCap?: number | null;
  totalMarketCapYi?: number | null;
  yearStartDate?: string | null;
  yearStartClose?: number | null;
  ytdReturnPct?: number | null;
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
  avgYtdReturnPct?: number | null;
  medianYtdReturnPct?: number | null;
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

export interface SignalSnapshotCountItem {
  signalType: string;
  total: number;
  displayLabel?: string | null;
  group?: string | null;
}

export interface SignalSnapshotCountsResponse {
  signalDate?: string | null;
  signalDateFrom?: string | null;
  signalDateTo?: string | null;
  items: SignalSnapshotCountItem[];
}
