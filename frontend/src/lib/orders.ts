import { apiGet, apiPost } from './api';

export interface Order {
	id: number;
	account_id: number;
	exchange: string;
	universal_symbol: string;
	client_order_id: string | null;
	native_order_id: string | null;
	side: string;
	type: string;
	status: string;
	qty: string;
	price: string | null;
	filled_qty: string;
	external: boolean;
	reject_reason: string | null;
	created_at: string;
	submitted_at: string | null;
	updated_at: string | null;
}

export interface OrderLeg {
	universal_symbol: string;
	side: string;
	type: string;
	qty: string;
	price?: string | null;
}

export interface PlaceOrderBody extends OrderLeg {
	account_id: number;
}

export interface BulkOutcome {
	ok: boolean;
	order_id?: number;
	status?: string;
	error?: string;
}

/** Trading filters for one instrument (subset of the symbols endpoint). */
export interface SymbolInfo {
	universal_symbol: string;
	exchange: string;
	market_type: string;
	base_asset: string;
	quote_asset: string;
	price_tick: string;
	qty_step: string;
	min_qty: string;
	max_qty: string | null;
	min_notional: string | null;
	status: string;
	last_price: string | null;
}

export const OPEN_STATES = ['SUBMITTING', 'OPEN', 'PARTIALLY_FILLED'];

export const fetchOrders = (params: Record<string, string> = {}) =>
	apiGet<Order[]>('/orders', params);

export const placeOrder = (body: PlaceOrderBody) => apiPost<Order>('/orders', body);

export const placeOrders = (account_id: number, orders: OrderLeg[]) =>
	apiPost<BulkOutcome[]>('/orders/bulk', { account_id, orders });

export const cancelOrder = (id: number) => apiPost<Order>(`/orders/${id}/cancel`);

export const cancelOrders = (order_ids: number[]) =>
	apiPost<BulkOutcome[]>('/orders/cancel', { order_ids });

export const syncAccountOrders = (account_id: number) =>
	apiPost<{ changed: number }>(`/orders/sync/${account_id}`);

/** Search instruments (for the ticket's symbol picker). */
export const searchSymbols = (exchange: string, search: string) =>
	apiGet<{ total: number; items: SymbolInfo[] }>('/symbols', {
		exchange,
		search,
		status: 'active',
		limit: '20'
	});

export const getSymbol = (universal_symbol: string) =>
	apiGet<SymbolInfo>(`/symbols/${encodeURIComponent(universal_symbol)}`);
