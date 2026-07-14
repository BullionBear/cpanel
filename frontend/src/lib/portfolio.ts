import { apiGet, apiPost } from './api';

export interface Balance {
	wallet: string;
	asset: string;
	free: string;
	locked: string;
	total: string;
	value_quote: string | null;
	quote_asset: string | null;
}

export interface Position {
	universal_symbol: string;
	side: string;
	size: string;
	entry_price: string;
	mark_price: string | null;
	unrealized_pnl: string | null;
	margin: string | null;
	value_quote: string | null;
	quote_asset: string | null;
}

export interface Holdings {
	account_id: number;
	exchange: string;
	label: string;
	taken_at: string | null;
	quote_asset: string;
	total_value: string;
	balances: Balance[];
	positions: Position[];
}

export interface OverviewItem {
	account_id: number;
	exchange: string;
	label: string;
	account_type: string;
	parent_id: number | null;
	taken_at: string | null;
	quote_asset: string;
	total_value: string;
}

export interface HistoryPoint {
	taken_at: string;
	total_value: string;
}

export const fetchOverview = () => apiGet<OverviewItem[]>('/portfolio');

export const fetchHoldings = (accountId: number) =>
	apiGet<Holdings>(`/portfolio/${accountId}`);

export const fetchHistory = (accountId: number) =>
	apiGet<HistoryPoint[]>(`/portfolio/${accountId}/history`);

/** Capture a fresh snapshot for one account on demand. */
export const triggerSnapshot = (accountId: number) =>
	apiPost<Holdings>(`/portfolio/${accountId}/snapshot`);
