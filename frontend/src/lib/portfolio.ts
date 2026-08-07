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

export interface PnlPoint {
	taken_at: string;
	pnl: string;
}

// Accounts are addressed by their natural key (exchange + nickname).
const enc = encodeURIComponent;
const portfolioPath = (exchange: string, label: string) =>
	`/portfolio/${enc(exchange)}/${enc(label)}`;

export const fetchOverview = () => apiGet<OverviewItem[]>('/portfolio');

export const fetchHoldings = (exchange: string, label: string) =>
	apiGet<Holdings>(portfolioPath(exchange, label));

export const fetchHistory = (exchange: string, label: string) =>
	apiGet<HistoryPoint[]>(`${portfolioPath(exchange, label)}/history`);

export const fetchPnlHistory = (exchange: string, label: string) =>
	apiGet<PnlPoint[]>(`${portfolioPath(exchange, label)}/pnl-history`);

export interface RefreshResult {
	account_id: number;
	exchange: string;
	label: string;
	scheduled: boolean;
}

/** Enqueue a debounced balance refresh; re-GET holdings after SSE ``balance.updated``. */
export const requestRefresh = (exchange: string, label: string) =>
	apiPost<RefreshResult>(`${portfolioPath(exchange, label)}/refresh`);
