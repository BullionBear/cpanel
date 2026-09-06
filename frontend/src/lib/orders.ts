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
	time_in_force?: string | null;
	reduce_only?: boolean;
	position_side?: string | null;
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
	native_symbol?: string;
	base_asset: string;
	quote_asset: string;
	price_tick: string;
	qty_step: string;
	min_qty: string;
	max_qty: string | null;
	min_notional: string | null;
	settle_asset?: string | null;
	expiry?: string | null;
	status: string;
	last_price: string | null;
}

export const OPEN_STATES = ['SUBMITTING', 'OPEN', 'PARTIALLY_FILLED'];

// Trading categories. Each maps from an instrument's market_type and selects a
// best-fit submit panel; unlike a unified ticket, categories that need a
// fundamentally different flow (options, delivery, RFQ) get their own panel.
export type Category = 'SPOT' | 'PERP' | 'CMARGIN' | 'DELIVERY' | 'OPTION' | 'RFQ';

export function categoryOf(marketType: string): Category {
	const m = marketType.toUpperCase();
	if (m === 'SPOT' || m === 'PERP' || m === 'CMARGIN' || m === 'DELIVERY' || m === 'OPTION')
		return m as Category;
	return 'SPOT';
}

export const TIME_IN_FORCE = ['GTC', 'IOC', 'FOK', 'POST_ONLY'] as const;

export interface Validation {
	ok: boolean;
	errors: string[];
	notional: number | null;
}

/** Client-side preview of the venue's filter checks (the server is authoritative). */
export function validateLeg(
	sym: SymbolInfo | null,
	type: string,
	qtyRaw: string,
	priceRaw: string,
	opts: { quoteQty?: boolean } = {}
): Validation {
	const errors: string[] = [];
	if (!sym) return { ok: false, errors: ['Select an instrument'], notional: null };
	const qty = Number(qtyRaw);
	if (!qtyRaw || !Number.isFinite(qty) || qty <= 0) errors.push('Enter a positive quantity');

	// Quote-denominated market buys: qty is already the notional spend.
	if (opts.quoteQty) {
		const notional = qty > 0 ? qty : null;
		if (sym.min_notional !== null && notional !== null && notional < Number(sym.min_notional))
			errors.push(`Below min notional ${sym.min_notional}`);
		return { ok: errors.length === 0, errors, notional };
	}

	const minQty = Number(sym.min_qty);
	if (qty > 0 && qty < minQty) errors.push(`Below min quantity ${sym.min_qty}`);
	const maxQty = sym.max_qty !== null ? Number(sym.max_qty) : null;
	if (maxQty !== null && qty > maxQty) errors.push(`Above max quantity ${sym.max_qty}`);

	let price: number | null = null;
	if (type === 'LIMIT') {
		price = Number(priceRaw);
		if (!priceRaw || !Number.isFinite(price) || price <= 0) errors.push('Enter a positive price');
	}
	const ref = price ?? (sym.last_price !== null ? Number(sym.last_price) : null);
	const notional = ref !== null && qty > 0 ? ref * qty : null;
	if (sym.min_notional !== null && notional !== null && notional < Number(sym.min_notional))
		errors.push(`Below min notional ${sym.min_notional}`);
	return { ok: errors.length === 0, errors, notional };
}

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
