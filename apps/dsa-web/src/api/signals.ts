import apiClient from './index';
import { toCamelCase } from './utils';
import type {
  SignalSnapshotCountsResponse,
  SignalSnapshotHistoryResponse,
  SignalSnapshotListResponse,
} from '../types/signals';

export interface GetSignalSnapshotsParams {
  signalType: string;
  signalDate?: string;
  signalDateFrom?: string;
  signalDateTo?: string;
  code?: string;
  codes?: string[];
  page?: number;
  pageSize?: number;
}

export interface SignalsRequestOptions {
  signal?: AbortSignal;
}

export interface GetSignalSnapshotHistoryParams {
  days?: number;
  limit?: number;
}

export interface GetSignalSnapshotCountsParams {
  signalDate?: string;
  signalDateFrom?: string;
  signalDateTo?: string;
  code?: string;
  codes?: string[];
  signalTypes?: string[];
}

export interface SendSignalSelectionParams {
  content: string;
  title?: string;
}

export interface SendSignalSelectionResponse {
  success: boolean;
  error?: string;
  message?: string;
}

export const signalsApi = {
  getSnapshots: async (
    params: GetSignalSnapshotsParams,
    options?: SignalsRequestOptions,
  ): Promise<SignalSnapshotListResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/signals/kline-snapshots', {
      signal: options?.signal,
      params: {
        signal_type: params.signalType,
        signal_date: params.signalDate,
        signal_date_from: params.signalDateFrom,
        signal_date_to: params.signalDateTo,
        code: params.code || undefined,
        codes: params.codes && params.codes.length > 0 ? params.codes.join(',') : undefined,
        page: params.page ?? 1,
        page_size: params.pageSize ?? 50,
      },
    });
    return toCamelCase<SignalSnapshotListResponse>(response.data);
  },

  getHistory: async (
    signalType: string,
    code: string,
    params: GetSignalSnapshotHistoryParams = {},
    options?: SignalsRequestOptions,
  ): Promise<SignalSnapshotHistoryResponse> => {
    const response = await apiClient.get<Record<string, unknown>>(
      `/api/v1/signals/kline-snapshots/${encodeURIComponent(signalType)}/${encodeURIComponent(code)}`,
      {
        signal: options?.signal,
        params: {
          days: params.days ?? 180,
          limit: params.limit ?? 100,
        },
      },
    );
    return toCamelCase<SignalSnapshotHistoryResponse>(response.data);
  },

  getSnapshotCounts: async (
    params: GetSignalSnapshotCountsParams,
    options?: SignalsRequestOptions,
  ): Promise<SignalSnapshotCountsResponse> => {
    const response = await apiClient.get<Record<string, unknown>>('/api/v1/signals/kline-snapshot-counts', {
      signal: options?.signal,
      params: {
        signal_date: params.signalDate,
        signal_date_from: params.signalDateFrom,
        signal_date_to: params.signalDateTo,
        code: params.code || undefined,
        codes: params.codes && params.codes.length > 0 ? params.codes.join(',') : undefined,
        signal_types: params.signalTypes && params.signalTypes.length > 0 ? params.signalTypes.join(',') : undefined,
      },
    });
    return toCamelCase<SignalSnapshotCountsResponse>(response.data);
  },

  sendSelection: async (
    params: SendSignalSelectionParams,
  ): Promise<SendSignalSelectionResponse> => {
    const response = await apiClient.post<Record<string, unknown>>('/api/v1/agent/chat/send', {
      content: params.content,
      title: params.title,
    });
    return toCamelCase<SendSignalSelectionResponse>(response.data);
  },
};
