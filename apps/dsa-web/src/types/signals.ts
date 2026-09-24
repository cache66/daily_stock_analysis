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
  earningsQualitySignal?: boolean | null;
  earningsStrategyScore?: number | null;
  earningsStrategyLabel?: string | null;
  earningsStrategyGateStatus?: string | null;
  earningsGrowthContinuityScore?: number | null;
  earningsProfitQualityScore?: number | null;
  earningsProfitabilityScore?: number | null;
  earningsDisclosureSignalScore?: number | null;
  earningsCycleScore?: number | null;
  earningsEventFreshnessScore?: number | null;
  earningsRiskPenalty?: number | null;
  earningsQualityVerdict?: string | null;
  earningsQualityScore?: number | null;
  earningsQualityCyclePhase?: string | null;
  earningsQualityQuarterlyTrend?: string | null;
  earningsQualityDualPositiveStreak?: number | null;
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
  cacheSource?: string | null;
  bundleRefreshedAt?: string | null;
  capitalProfileRefreshedAt?: string | null;
  capitalProfileCacheHit?: boolean | null;
  leaderProbability?: string | null;
  leaderType?: string | null;
  selectionMode?: string | null;
  isBreakoutCandidate?: boolean | null;
  isPullbackCandidate?: boolean | null;
  nearNewHigh?: boolean | null;
  signalTags?: string[] | null;
  primaryProfile?: string | null;
  breakoutScore?: number | null;
  pullbackScore?: number | null;
  hybridScore?: number | null;
  overallScore?: number | null;
  trendLabel?: string | null;
  riskFlags?: string[] | null;
  strategySummary?: string | null;
  profileName?: string | null;
  profileLabel?: string | null;
  sectorLeadershipScore?: number | null;
  relativeStrengthScore?: number | null;
  catalystScore?: number | null;
  monthlyPositiveRatio?: number | null;
  monthlyHigherLowRatio?: number | null;
  monthlyTotalReturnPct?: number | null;
  monthlyMaxSingleGainPct?: number | null;
  monthlyWorstDrawdownPct?: number | null;
  monthlyMaShort?: number | null;
  monthlyMaLong?: number | null;
  monthlyLatestMonth?: string | null;
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

export interface FastReviewFocusItem {
  code: string;
  name?: string | null;
  tier?: string | null;
  abBucket?: string | null;
  priorityScore?: number | null;
  signalKeys: string[];
  signalTypes: string[];
  trendHundredRelation?: string | null;
  focusReason?: string | null;
  reasonSummary?: string | null;
  displayReasonSummary?: string | null;
  industryLogic?: string | null;
  newsLogic?: string | null;
  technicalLogic?: string | null;
  businessLabels: string[];
  businessSummary?: string | null;
  chainRoleLabel?: string | null;
  themeLabel?: string | null;
  themeSource?: string | null;
  mainlineJudgement?: string | null;
  mainlineEvidenceSources?: string[];
  authorityJudgement?: string | null;
  authorityLevel?: string | null;
  authorityReasonSummary?: string | null;
  displayAuthorityJudgement?: string | null;
  displayAuthoritySummary?: string | null;
  authorityEvidenceDigest?: string | null;
  announcementEvidenceSummary?: string | null;
  earningsEvidenceSummary?: string | null;
  researchEvidenceSummary?: string | null;
  authorityTimeWindowDays?: number | null;
  preferredIndustryLabel?: string | null;
  peerGroupLabel?: string | null;
  displayPeerSummary?: string | null;
  peerResonanceSummary?: string | null;
  leaderPositionSummary?: string | null;
  turningPointPeerSummary?: string | null;
  earningsAnchor?: string | null;
  supplyDemandBias?: string | null;
  trendLabel?: string | null;
  selectionMode?: string | null;
  riskFlags: string[];
  reviewStageType?: string | null;
  reviewStageLabel?: string | null;
  reviewStageReason?: string | null;
  driverType?: string | null;
  driverLabel?: string | null;
  driverReason?: string | null;
  eventDate?: string | null;
  todayChangePct?: number | null;
  peRatio?: number | null;
  reportDate?: string | null;
  reportPeriodLabel?: string | null;
  revenueAmount?: number | null;
  netProfitAmount?: number | null;
}

export interface FastReviewFocusResponse {
  snapshotDate: string;
  total: number;
  sourceRunDir: string;
  sourceCsvPath: string;
  abSummary: Record<string, number>;
  stageSummary: Record<string, number>;
  driverSummary: Record<string, number>;
  items: FastReviewFocusItem[];
}

export interface FastReviewStockOverviewItem {
  code: string;
  name?: string | null;
  stockReviewLane?: string | null;
  stockReviewLaneLabel?: string | null;
  tier?: string | null;
  abBucket?: string | null;
  reviewStageLabel?: string | null;
  driverLabel?: string | null;
  priorityScore?: number | null;
  strategyCount: number;
  signalKeys: string[];
  signalTypes: string[];
  triggeredStrategies: string[];
  chartEvidenceSummary?: string | null;
  earningsEvidenceSummary?: string | null;
  stockContextSummary?: string | null;
  todayChangePct?: number | null;
  peRatio?: number | null;
  reportPeriodLabel?: string | null;
  revenueYoy?: number | null;
  netProfitYoy?: number | null;
  roe?: number | null;
  earningsStrategyScore?: number | null;
  earningsStrategyGateStatus?: string | null;
  earningsQualityScore?: number | null;
  earningsQualityCyclePhase?: string | null;
  capitalProfileScore?: number | null;
  relativeStrengthScore?: number | null;
  breakoutQualityScore?: number | null;
  marketExpectationInstitutionCount?: number | null;
  netProfitAmount?: number | null;
  primaryBoardName?: string | null;
  preferredIndustryLabel?: string | null;
  themeLabel?: string | null;
  mainlineJudgement?: string | null;
  reasonSummary?: string | null;
  displayReasonSummary?: string | null;
  causeTagsZh?: string | null;
  latestTradeDate?: string | null;
  pureChartQualityPassed?: boolean | null;
}

export interface FastReviewStockOverviewResponse {
  snapshotDate: string;
  total: number;
  sourceRunDir: string;
  sourceCsvPath: string;
  laneSummary: Record<string, number>;
  signalSummary: Record<string, number>;
  items: FastReviewStockOverviewItem[];
}

export interface PersonalStrategyDefinition {
  id: string;
  name: string;
  shortName: string;
  group: string;
  groupLabel: string;
  mode: string;
  aliases: string[];
  role: string;
  logic: string;
}

export interface PersonalStrategyMatch {
  id: string;
  name: string;
  shortName: string;
  group: string;
  groupLabel: string;
  mode: string;
  role: string;
  logic: string;
}

export interface PersonalStrategyMatrixItem extends FastReviewStockOverviewItem {
  matchedStrategies: PersonalStrategyMatch[];
  matchedStrategyIds: string[];
  matchedStrategyCount: number;
  qualityScore: number;
  qualityBand: 'recommended' | 'watch' | 'weak';
  qualityLabel: string;
  qualitySummary: string;
  qualityFlags: string[];
  viewLane: 'short_term' | 'long_term' | 'watch';
  viewLaneLabel: string;
  viewLaneSummary: string;
}

export interface PersonalStrategyMatrixResponse {
  snapshotDate: string;
  total: number;
  sourceRunDir: string;
  sourceCsvPath: string;
  laneSummary: Record<string, number>;
  signalSummary: Record<string, number>;
  strategySummary: Record<string, number>;
  strategies: PersonalStrategyDefinition[];
  items: PersonalStrategyMatrixItem[];
}
