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

export interface DepositHistoryRow {
	id: number;
	account_id: number;
	exchange: string;
	native_deposit_id: string;
	asset: string;
	amount: string;
	status: string;
	network: string | null;
	tx_id: string | null;
	credited_at: string | null;
	created_at: string | null;
	ingested_at: string;
}

export interface WithdrawalHistoryRow {
	id: number;
	account_id: number;
	exchange: string;
	native_withdrawal_id: string;
	asset: string;
	amount: string;
	status: string;
	address: string | null;
	network: string | null;
	tx_id: string | null;
	fee: string | null;
	fee_asset: string | null;
	created_at: string | null;
	ingested_at: string;
}

export interface FundingFeeHistoryRow {
	id: number;
	account_id: number;
	exchange: string;
	universal_symbol: string;
	native_funding_id: string;
	asset: string;
	amount: string;
	funding_rate: string | null;
	settled_at: string | null;
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

export const fetchDepositHistory = (params: Record<string, string> = {}) =>
	apiGet<Page<DepositHistoryRow>>('/history/deposits', params);

export const fetchWithdrawalHistory = (params: Record<string, string> = {}) =>
	apiGet<Page<WithdrawalHistoryRow>>('/history/withdrawals', params);

export const fetchFundingFeeHistory = (params: Record<string, string> = {}) =>
	apiGet<Page<FundingFeeHistoryRow>>('/history/funding', params);

export const fetchStreams = (params: Record<string, string> = {}) =>
	apiGet<StreamHealth[]>('/history/streams', params);

/** Run one account's history sync now (new accounts backfill the past 3 days). */
export const syncAccountHistory = (account_id: number) =>
	apiPost<{ ingested: number }>(`/history/sync/${account_id}`);
