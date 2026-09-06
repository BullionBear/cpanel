import type { Category } from '../orders';

/** Position modes a derivatives panel can offer. BOTH = one-way; LONG/SHORT =
 *  hedge-mode sides. Spot panels leave this empty. */
export type PosMode = 'BOTH' | 'LONG' | 'SHORT';

/** Describes the controls a submit panel exposes for one (exchange, category).
 *  This is where each venue's "best fit" differences live — we don't force a
 *  lowest-common-denominator ticket. */
export interface PanelSpec {
	orderTypes: ('LIMIT' | 'MARKET')[];
	/** Time-in-force choices; empty hides the control. */
	tif: string[];
	/** Whether a reduce-only toggle applies (derivatives only). */
	reduceOnly: boolean;
	/** Position modes; empty hides the control (one-way only / spot). */
	positionModes: PosMode[];
	/** Quantity is denominated in contracts rather than the base asset. */
	contracts: boolean;
	/** Market-buy qty is quote-denominated (venues with no base-qty market buy). */
	marketBuyQuote?: boolean;
	/** Short venue-specific note surfaced in the panel. */
	hint?: string;
}

export type PanelResolution =
	| { supported: true; label: string; spec: PanelSpec }
	| { supported: false; label: string; note: string };

const FULL_TIF = ['GTC', 'IOC', 'FOK', 'POST_ONLY'];
const HEDGE: PosMode[] = ['BOTH', 'LONG', 'SHORT'];

const SPEC = {
	binanceSpot: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: false,
		positionModes: [],
		contracts: false,
		hint: 'Post-only routes as a LIMIT_MAKER order.'
	},
	binancePerp: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: HEDGE,
		contracts: false,
		hint: 'One-way: use BOTH. Hedge mode: pick LONG/SHORT. Post-only uses GTX.'
	},
	binanceCmargin: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: HEDGE,
		contracts: true,
		hint: 'Coin-margined: quantity is in contracts.'
	},
	bybitSpot: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: false,
		positionModes: [],
		contracts: false
	},
	bybitPerp: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: HEDGE,
		contracts: false,
		hint: 'positionIdx: BOTH=one-way, LONG/SHORT=hedge.'
	},
	bybitInverse: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: HEDGE,
		contracts: true,
		hint: 'Inverse (coin-margined): quantity is in contracts.'
	},
	gateioSpot: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: false,
		positionModes: [],
		contracts: false,
		marketBuyQuote: true,
		hint: 'Post-only routes as poc. Market buys are sized in the quote asset.'
	},
	gateioPerp: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: [],
		contracts: true,
		hint: 'Quantity is in contracts; one-way position mode.'
	},
	gateioInverse: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: [],
		contracts: true,
		hint: 'Inverse (BTC-settled): quantity is in contracts; one-way position mode.'
	},
	bitgetSpot: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: false,
		positionModes: [],
		contracts: false,
		hint: 'Post-only routes as force=post_only. Market orders use IOC.'
	},
	bitgetPerp: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: [],
		contracts: true,
		hint: 'USDT-M perpetual: quantity is in contracts; one-way position mode.'
	},
	bitgetInverse: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: [],
		contracts: true,
		hint: 'Coin-margined: quantity is in contracts; one-way position mode.'
	},
	okxSpot: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: false,
		positionModes: [],
		contracts: false,
		hint: 'Post-only routes as ordType=post_only. Market orders are sized in the base asset.'
	},
	okxPerp: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: HEDGE,
		contracts: true,
		hint: 'USDT/USDC-M perpetual: quantity is in contracts. One-way: BOTH; hedge: LONG/SHORT.'
	},
	okxInverse: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: HEDGE,
		contracts: true,
		hint: 'Coin-margined (inverse): quantity is in contracts.'
	},
	deribitSpot: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: false,
		positionModes: [],
		contracts: false,
		hint: 'Post-only is reject-or-rest. Quantity is in the base asset.'
	},
	deribitPerp: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: [],
		contracts: false,
		hint: 'USDC/USDT linear perpetual: quantity is in the base asset. One-way only.'
	},
	deribitInverse: {
		orderTypes: ['LIMIT', 'MARKET'],
		tif: FULL_TIF,
		reduceOnly: true,
		positionModes: [],
		contracts: true,
		hint: 'Inverse (coin-margined): quantity is in USD. BTC-PERPETUAL contract size is 10 USD.'
	}
} satisfies Record<string, PanelSpec>;

const CATEGORY_LABEL: Record<Category, string> = {
	SPOT: 'Spot',
	PERP: 'Perpetual',
	CMARGIN: 'Coin-margined',
	DELIVERY: 'Delivery futures',
	OPTION: 'Options',
	RFQ: 'RFQ / block'
};

// Per-exchange support map. Categories omitted here fall through to a
// "not yet supported" panel with an explanatory note.
const SUPPORT: Record<string, Partial<Record<Category, PanelSpec>>> = {
	BINANCE: { SPOT: SPEC.binanceSpot, PERP: SPEC.binancePerp, CMARGIN: SPEC.binanceCmargin },
	BYBIT: { SPOT: SPEC.bybitSpot, PERP: SPEC.bybitPerp, CMARGIN: SPEC.bybitInverse },
	GATEIO: { SPOT: SPEC.gateioSpot, PERP: SPEC.gateioPerp, CMARGIN: SPEC.gateioInverse },
	BITGET: { SPOT: SPEC.bitgetSpot, PERP: SPEC.bitgetPerp, CMARGIN: SPEC.bitgetInverse },
	OKX: { SPOT: SPEC.okxSpot, PERP: SPEC.okxPerp, CMARGIN: SPEC.okxInverse },
	DERIBIT: {
		SPOT: SPEC.deribitSpot,
		PERP: SPEC.deribitPerp,
		CMARGIN: SPEC.deribitInverse
	}
};

export function resolvePanel(exchange: string, category: Category): PanelResolution {
	const label = CATEGORY_LABEL[category] ?? category;
	const spec = SUPPORT[exchange.toUpperCase()]?.[category];
	if (spec) return { supported: true, label, spec };
	return {
		supported: false,
		label,
		note: `Trading ${label.toLowerCase()} on ${exchange} isn't supported yet — this market needs a dedicated flow.`
	};
}
