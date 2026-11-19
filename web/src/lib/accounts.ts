// Kind choices for the account form. The in-scope list matches the pms.account_kind
// seed (C4-D015). BinanceCM stays closed until C8-01. The API rejects anything else;
// this module also keeps those kinds out of the request.

export const exchanges = ['binance', 'bybit', 'okx'] as const;
export type ExchangeName = (typeof exchanges)[number];

const inScopeKinds: Record<ExchangeName, readonly string[]> = {
	binance: ['Binance', 'BinanceUM'],
	bybit: ['Bybit'],
	okx: ['Okx']
};

const binanceClosed = ['BinanceCM', 'BinanceEO', 'BinancePM'] as const;

export const exchangeLabel: Record<ExchangeName, string> = {
	binance: 'Binance',
	bybit: 'Bybit',
	okx: 'OKX'
};

export type KindChoice = {
	kind: string;
	checked: boolean;
	disabled: boolean;
};

export type UnmatchedRow = {
	exchange: string | null;
	exch_account_uid: string | null;
	api_ids: readonly { kind: string }[];
	missing_kinds: readonly string[];
};

export type BroughtForm = {
	exchange: ExchangeName;
	uid: string;
	choices: KindChoice[];
};

export function isExchange(value: string): value is ExchangeName {
	return (exchanges as readonly string[]).includes(value);
}

export function autoKind(exchange: ExchangeName): string {
	const kinds = inScopeKinds[exchange];
	return kinds.length === 1 ? kinds[0] : '';
}

export function blankChoices(exchange: ExchangeName): KindChoice[] {
	if (exchange !== 'binance') return [];
	return inScopeKinds.binance.map((kind) => ({ kind, checked: false, disabled: false }));
}

// Kinds on this account that are in scope and not already present, including a disabled row.
export function kindsToOffer(exchange: ExchangeName, present: readonly string[]): string[] {
	return inScopeKinds[exchange].filter((kind) => !present.includes(kind));
}

// null means omit `kinds` so Bybit and OKX auto-enable their single kind.
// A Binance array never contains a closed kind, even if that choice was checked.
export function kindsToSubmit(exchange: ExchangeName, choices: readonly KindChoice[]): string[] | null {
	if (exchange !== 'binance') return null;
	return choices
		.filter((choice) => choice.checked && !choice.disabled && inScopeKinds.binance.includes(choice.kind))
		.map((choice) => choice.kind);
}

// Rows that share an exchange and uid are one mftiko identity split by the unmatched view.
// In-scope kinds are pre-checked. Closed kinds stay visible and cannot be submitted.
// kind_not_enabled pre-checks only missing_kinds. A row without an identity cannot be brought in.
export function formFromUnmatched(row: UnmatchedRow, rows: readonly UnmatchedRow[]): BroughtForm | null {
	if (!row.exchange || !isExchange(row.exchange) || !row.exch_account_uid) return null;
	const exchange = row.exchange;
	const present = new Set<string>();
	const toCheck = new Set<string>();
	for (const item of [row, ...rows]) {
		if (item.exchange !== exchange || item.exch_account_uid !== row.exch_account_uid) continue;
		for (const api of item.api_ids) present.add(api.kind);
		if (item.missing_kinds.length > 0) {
			for (const kind of item.missing_kinds) {
				present.add(kind);
				if (inScopeKinds[exchange].includes(kind)) toCheck.add(kind);
			}
			continue;
		}
		for (const api of item.api_ids) {
			if (inScopeKinds[exchange].includes(api.kind)) toCheck.add(api.kind);
		}
	}
	if (exchange !== 'binance') return { exchange, uid: row.exch_account_uid, choices: [] };
	const choices: KindChoice[] = inScopeKinds.binance.map((kind) => ({
		kind,
		checked: toCheck.has(kind),
		disabled: false
	}));
	for (const kind of binanceClosed) {
		if (present.has(kind)) choices.push({ kind, checked: false, disabled: true });
	}
	return { exchange, uid: row.exch_account_uid, choices };
}
