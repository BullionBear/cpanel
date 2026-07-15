import { apiGet, apiPost } from './api';

export interface TradeRow {
	id: number;
	account_id: number;
	exchange: string;
	universal_symbol: string;
	native_trade_id: string;
	native_order_id: string;
	side: string;
	price: string;
	qty: string;
	fee: string;
	fee_asset: string;
	traded_at: string | null;
	ingested_at: string;
}

export interface OrderHistoryRow {
	id: number;
	account_id: number;
	exchange: string;
	universal_symbol: string;
	native_order_id: string;
	client_order_id: string | null;
	side: string;
	type: string;
	status: string;
	qty: string;
	price: string | null;
	filled_qty: string;
	placed_at: string | null;
	updated_at: string | null;
	ingested_at: string;
}

export interface StreamHealth {
	account_id: number;
	account_label: string;
	exchange: string;
	data_kind: string;
	stream_key: string;
	cursor: string | null;
	last_synced_at: string | null;
	last_error: string | null;
}

export interface Page<T> {
	total: number;
	items: T[];
}

export const fetchTrades = (params: Record<string, string> = {}) =>
	apiGet<Page<TradeRow>>('/history/trades', params);

export const fetchOrderHistory = (params: Record<string, string> = {}) =>
	apiGet<Page<OrderHistoryRow>>('/history/orders', params);

export const fetchStreams = (params: Record<string, string> = {}) =>
	apiGet<StreamHealth[]>('/history/streams', params);

/** Run one account's history sync now (new accounts backfill the past 3 days). */
export const syncAccountHistory = (account_id: number) =>
	apiPost<{ ingested: number }>(`/history/sync/${account_id}`);
