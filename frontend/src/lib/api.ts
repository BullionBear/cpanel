import { API_V2 } from './config';

// Key generation types
export type KeyType = 'RSA' | 'ED25519';

export interface KeyGenerateResponse {
	key_type: string;
	private_key: string;
	public_key: string;
}

export async function generateKeyPair(keyType: KeyType, keySize?: number): Promise<KeyGenerateResponse> {
	try {
		const response = await fetch(`${API_V2}/tool/keygen`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({
				key_type: keyType,
				key_size: keySize
			})
		});

		if (response.ok) {
			return await response.json();
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to generate key pair');
	} catch (error) {
		console.error('Failed to generate key pair:', error);
		throw error;
	}
}

export interface User {
	id: number;
	username: string;
	email: string;
	is_active: boolean;
}

export async function getCurrentUser(): Promise<User | null> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		return null;
	}

	try {
		const response = await fetch(`${API_V2}/auth/me`, {
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			// Token invalid, remove it
			localStorage.removeItem('access_token');
			return null;
		}
		return null;
	} catch (error) {
		console.error('Failed to get current user:', error);
		return null;
	}
}

export function logout() {
	localStorage.removeItem('access_token');
	window.location.href = '/';
}


export type APIType = 'HMAC' | 'ED25519' | 'RSA';

export interface APIKey {
	id: number;
	user_id: number;
	exchange: string;
	uid: string;  // Exchange account UID
	name: string;
	api_type: APIType;
	master_uid: string | null;  // Master account UID
	created_at: string;
}

export interface APIKeyCreate {
	exchange: string;
	name: string;
	api_type: APIType;
	api_key: string;
	api_secret: string;
	passphrase?: string;
	master_uid?: string;  // Master account UID for sub-accounts
}

export async function getAPIKeys(): Promise<APIKey[]> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/apikey`, {
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to fetch API keys');
	} catch (error) {
		console.error('Failed to get API keys:', error);
		throw error;
	}
}

export async function registerAPIKey(data: APIKeyCreate): Promise<APIKey> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/apikey/register`, {
			method: 'POST',
			headers: {
				'Authorization': `Bearer ${token}`,
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(data)
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to register API key');
	} catch (error) {
		console.error('Failed to register API key:', error);
		throw error;
	}
}

export async function deleteAPIKey(apiId: number): Promise<void> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/apikey/${apiId}`, {
			method: 'DELETE',
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});

		if (response.ok || response.status === 204) {
			return;
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to delete API key');
	} catch (error) {
		console.error('Failed to delete API key:', error);
		throw error;
	}
}

export interface PreferenceResponse {
	preference: {
		banner?: number[];
		[key: string]: any;
	};
}

export async function getPreference(layout: string = 'execution'): Promise<PreferenceResponse> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/preference?layout=${layout}`, {
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to fetch preference');
	} catch (error) {
		console.error('Failed to get preference:', error);
		throw error;
	}
}

export interface BBOData {
	symbol_id: number;
	symbol: string;
	exchange: string;
	bid: [string, string]; // [price, quantity]
	ask: [string, string]; // [price, quantity]
}

export async function getBBOs(symbolIds: number[]): Promise<BBOData[]> {
	try {
		const symbolIdParam = symbolIds.join(',');
		const response = await fetch(`${API_V2}/market/bbos?symbol_id=${symbolIdParam}`);

		if (response.ok) {
			return await response.json();
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to fetch BBO data');
	} catch (error) {
		console.error('Failed to get BBOs:', error);
		throw error;
	}
}

export async function updatePreference(preference: Record<string, any>, layout: string = 'execution'): Promise<PreferenceResponse> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/preference?layout=${layout}`, {
			method: 'PUT',
			headers: {
				'Authorization': `Bearer ${token}`,
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({ preference })
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to update preference');
	} catch (error) {
		console.error('Failed to update preference:', error);
		throw error;
	}
}

export interface SymbolInfo {
	id: number;
	name: string;
	exchange: {
		id: number;
		name: string;
		slug: string;
	};
	product: {
		id: number;
		name: string;
		slug: string;
	};
	base_token: {
		id: number;
		name: string;
		slug: string;
	};
	quote_token: {
		id: number;
		name: string;
		slug: string;
	};
	price_precision: number;
	size_precision: number;
	universal_ticker: string;
}

export async function getSymbols(limit: number = 100): Promise<SymbolInfo[]> {
	try {
		const response = await fetch(`${API_V2}/symbol?limit=${limit}`);

		if (response.ok) {
			return await response.json();
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to fetch symbols');
	} catch (error) {
		console.error('Failed to get symbols:', error);
		throw error;
	}
}

export interface OrderCreate {
	symbol_id: number;
	account_id: number;
	type: 'LIMIT' | 'MARKET';
	price?: string;
	qty: string;
	side: 'BUY' | 'SELL';
}

export interface OrderResponse {
	order_id: string;
	symbol: string;
	side: string;
	type: string;
	quantity: string;
	price: string;
	status: string;
	client_order_id: string;
	account_id: number;
	created_at: number;
	updated_at: number;
}

export async function createOrder(data: OrderCreate): Promise<OrderResponse> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/order`, {
			method: 'POST',
			headers: {
				'Authorization': `Bearer ${token}`,
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(data)
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to create order');
	} catch (error) {
		console.error('Failed to create order:', error);
		throw error;
	}
}

export interface APIToken {
	access_token: string;
	token_type: string;
}

export interface TokenBalance {
	token: string;
	total: string;
	frozen: string;
	available: string;
}

export interface WalletBalance {
	wallet: string;
	balance: TokenBalance[];
}

export interface BalanceResponse {
	api_id: number;
	name: string;
	exchange: string;
	wallets: WalletBalance[];
}

export async function getBalances(apiIds: number[]): Promise<BalanceResponse[]> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const idParam = apiIds.join(',');
		const response = await fetch(`${API_V2}/balance?id=${idParam}`, {
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		} else if (response.status === 404) {
			// No balances found, return empty array
			return [];
		}
		let detail = 'Failed to fetch balances';
		try {
			const error = await response.json();
			if (error.detail) detail = error.detail;
		} catch { /* non-JSON body */ }
		throw new Error(detail);
	} catch (error) {
		console.error('Failed to get balances:', error);
		throw error;
	}
}

/** v2: get balances by wallet IDs (resolves wallet -> API, returns same BalanceResponse[] shape). */
export async function getBalancesByWalletIds(walletIds: number[]): Promise<BalanceResponse[]> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}
	if (walletIds.length === 0) {
		return [];
	}
	try {
		const idParam = walletIds.join(',');
		const response = await fetch(`${API_V2}/balance?id=${idParam}`, {
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});
		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		} else if (response.status === 404) {
			return [];
		}
		let detail = 'Failed to fetch balances';
		try {
			const err = await response.json();
			if (err.detail) detail = err.detail;
		} catch { /* non-JSON body */ }
		throw new Error(detail);
	} catch (error) {
		console.error('Failed to get balances by wallets:', error);
		throw error;
	}
}

/** v2: get balance for a single wallet (for per-wallet display with wallet name as header). */
export async function getBalanceByWalletId(walletId: number): Promise<BalanceResponse> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}
	const response = await fetch(`${API_V2}/balance/${walletId}`, {
		headers: { 'Authorization': `Bearer ${token}` }
	});
	if (response.ok) return await response.json();
	if (response.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	if (response.status === 404) {
		throw new Error('Wallet not found');
	}
	let detail = `Failed to fetch balance (HTTP ${response.status})`;
	try {
		const err = await response.json();
		if (err.detail) detail = err.detail;
	} catch {
		// non-JSON error body
	}
	throw new Error(detail);
}

export interface OpenOrder {
	order_id: string;
	symbol: string;
	side: string;
	type: string;
	quantity: string;
	price: string;
	status: string;
	client_order_id: string;
	account_id: number;
	account_name: string;
	created_at: number;
	updated_at: number;
}

export async function getOpenOrders(accountId?: number, symbolId?: number): Promise<OpenOrder[]> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	const params = new URLSearchParams();
	if (accountId != null) params.set('account_id', String(accountId));
	if (symbolId != null) params.set('symbol_id', String(symbolId));
	const q = params.toString() ? `?${params.toString()}` : '';

	try {
		const response = await fetch(`${API_V2}/order/open${q}`, {
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		} else if (response.status === 404) {
			return [];
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to fetch open orders');
	} catch (error) {
		console.error('Failed to get open orders:', error);
		throw error;
	}
}

export interface OrderCancel {
	account_id: number;
	symbol_id: number;
	order_id?: string;
	client_order_id?: string;
}

export async function cancelOrder(data: OrderCancel): Promise<OrderResponse> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/order`, {
			method: 'DELETE',
			headers: {
				'Authorization': `Bearer ${token}`,
				'Content-Type': 'application/json'
			},
			body: JSON.stringify(data)
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to cancel order');
	} catch (error) {
		console.error('Failed to cancel order:', error);
		throw error;
	}
}

export async function getAPIToken(): Promise<APIToken | null> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/auth/api-token`, {
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 404) {
			// No API token found
			return null;
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to fetch API token');
	} catch (error) {
		console.error('Failed to get API token:', error);
		throw error;
	}
}

export async function createAPIToken(): Promise<APIToken> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/auth/api-token`, {
			method: 'POST',
			headers: {
				'Authorization': `Bearer ${token}`,
				'Content-Type': 'application/json'
			}
		});

		if (response.ok) {
			return await response.json();
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to create API token');
	} catch (error) {
		console.error('Failed to create API token:', error);
		throw error;
	}
}

export async function deleteAPIToken(): Promise<void> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_V2}/auth/api-token`, {
			method: 'DELETE',
			headers: {
				'Authorization': `Bearer ${token}`
			}
		});

		if (response.ok || response.status === 204) {
			return;
		} else if (response.status === 401) {
			localStorage.removeItem('access_token');
			throw new Error('Unauthorized');
		}
		const error = await response.json();
		throw new Error(error.detail || 'Failed to delete API token');
	} catch (error) {
		console.error('Failed to delete API token:', error);
		throw error;
	}
}

// --- Wallets ---

export interface Wallet {
	id: number;
	name: string;
	api_id: number | null;
	wallet_type_id: number;
	is_active: boolean;
}

export interface WalletType {
	id: number;
	name: string;
	slug: string;
}

export interface WalletCreate {
	name: string;
	api_key_id: number;
	wallet_type_id: number;
}

export async function getWallets(): Promise<Wallet[]> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}
	const response = await fetch(`${API_V2}/wallet`, {
		headers: { 'Authorization': `Bearer ${token}` }
	});
	if (response.ok) return await response.json();
	if (response.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await response.json();
	throw new Error(err.detail || 'Failed to fetch wallets');
}

export async function getWalletTypes(): Promise<WalletType[]> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}
	const response = await fetch(`${API_V2}/wallet/types`, {
		headers: { 'Authorization': `Bearer ${token}` }
	});
	if (response.ok) return await response.json();
	if (response.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await response.json();
	throw new Error(err.detail || 'Failed to fetch wallet types');
}

export async function createWallet(data: WalletCreate): Promise<Wallet> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}
	const response = await fetch(`${API_V2}/wallet`, {
		method: 'POST',
		headers: {
			'Authorization': `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(data)
	});
	if (response.ok) return await response.json();
	if (response.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await response.json();
	throw new Error(err.detail || 'Failed to create wallet');
}

export async function deleteWallet(walletId: number): Promise<void> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}
	const response = await fetch(`${API_V2}/wallet/${walletId}`, {
		method: 'DELETE',
		headers: { 'Authorization': `Bearer ${token}` }
	});
	if (response.ok || response.status === 204) return;
	if (response.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await response.json();
	throw new Error(err.detail || 'Failed to delete wallet');
}

// --- Account v2 (dashboard: accounts, API keys, wallets, balance) ---

export interface AccountV2 {
	id: number;
	uid: number;
	exch_id: number;
	exchange: string;
	name: string;
	parent_uid: number;
}

export interface APIKeyV2 {
	id: number;
	user_id: number;
	exchange: string;
	uid: number;
	name: string;
	api_type: string;
	api_key: string;
	api_secret: string;
	passphrase: string;
	parent_uid: number;
	created_at: string;
}

export interface WalletV2 {
	id: number;
	name: string;
	category_id: number;
	acct_id: number;
	user_id: number;
	is_active: boolean;
}

export async function getAccountsV2(exchId?: number): Promise<AccountV2[]> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const q = exchId != null ? `?exch_id=${exchId}` : '';
	const res = await fetch(`${API_V2}/account/acct${q}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to fetch accounts');
}

export async function getApiKeysV2(exchId?: number): Promise<APIKeyV2[]> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const q = exchId != null ? `?exch_id=${exchId}` : '';
	const res = await fetch(`${API_V2}/account/apikey${q}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to fetch API keys');
}

export async function getWalletsV2(acctId?: number): Promise<WalletV2[]> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const q = acctId != null ? `?acct_id=${acctId}` : '';
	const res = await fetch(`${API_V2}/account/wallet${q}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to fetch wallets');
}

export async function getBalancesV2(walletIds: number[]): Promise<BalanceResponse[]> {
	if (walletIds.length === 0) return [];
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const idParam = walletIds.join(',');
	const res = await fetch(`${API_V2}/balance?id=${idParam}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	if (res.status === 404) return [];
	let detail = 'Failed to fetch balances';
	try {
		const err = await res.json();
		if (err.detail) detail = err.detail;
	} catch { /* non-JSON body */ }
	throw new Error(detail);
}

/** Unique exchanges from symbols; fallback to known list if symbols unavailable. */
export interface ExchangeOption {
	id: number;
	name: string;
}

const KNOWN_EXCHANGES: ExchangeOption[] = [
	{ id: 1, name: 'Binance' },
	{ id: 2, name: 'Bybit' },
	{ id: 3, name: 'Bitget' }
];

export async function getExchanges(): Promise<ExchangeOption[]> {
	try {
		const symbols = await getSymbols(500);
		const byId = new Map<number, string>();
		for (const s of symbols) {
			if (s.exchange?.id != null && s.exchange?.name) {
				byId.set(s.exchange.id, s.exchange.name);
			}
		}
		// Merge in known exchanges that may not have symbols yet
		for (const known of KNOWN_EXCHANGES) {
			if (!byId.has(known.id)) {
				byId.set(known.id, known.name);
			}
		}
		return [...byId.entries()].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
	} catch {
		return [...KNOWN_EXCHANGES];
	}
}

export interface APIKeyCreateV2 {
	exch_id: number;
	api_type?: string;
	api_key: string;
	api_secret: string;
	passphrase?: string;
	parent_uid?: number;
}

export async function registerApiKeyV2(data: APIKeyCreateV2): Promise<APIKeyV2> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/account/apikey`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({
			exch_id: data.exch_id,
			api_type: data.api_type ?? 'HMAC',
			api_key: data.api_key,
			api_secret: data.api_secret,
			passphrase: data.passphrase || undefined,
			parent_uid: data.parent_uid ?? undefined
		})
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to register API key');
}

export async function renameAccountV2(id: number, name: string): Promise<AccountV2> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/account/acct/rename`, {
		method: 'PUT',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ id, name })
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to rename account');
}

export async function createWalletV2(acctId: number, categoryId: number, name?: string): Promise<WalletV2> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/account/wallet`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ acct_id: acctId, category_id: categoryId, name: name || undefined })
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to create wallet');
}

export async function deleteApiKeyV2(apiId: number): Promise<void> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/account/apikey/${apiId}`, {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok || res.status === 204) return;
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to delete API key');
}

export interface CursorV2 {
	acct_id: number;
	symbol_id: number;
	cursor_start: number;
	cursor_end: number;
}

export async function getCursorsV2(acctId?: number): Promise<CursorV2[]> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const q = acctId != null ? `?acct_id=${acctId}` : '';
	const res = await fetch(`${API_V2}/mytrades/cursors${q}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	return [];
}

export async function createCursorV2(acctId: number, symbolId: number): Promise<CursorV2> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/mytrades/cursor`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ acct_id: acctId, symbol_id: symbolId })
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to create cursor');
}

export async function deleteCursorV2(acctId: number, symbolId: number): Promise<void> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/mytrades/cursor`, {
		method: 'DELETE',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify({ acct_id: acctId, symbol_id: symbolId })
	});
	if (res.ok || res.status === 204) return;
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to delete cursor');
}

export async function getSymbolsByExchange(exchangeId: number, limit: number = 500): Promise<SymbolInfo[]> {
	const res = await fetch(`${API_V2}/symbol?exchange_id=${exchangeId}&limit=${limit}`);
	if (res.ok) return await res.json();
	return [];
}

export interface SymbolCreate {
	exchange_id: number;
	product_id: number;
	symbol: string;
}

export async function createSymbol(data: SymbolCreate): Promise<SymbolInfo> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/symbol`, {
		method: 'POST',
		headers: {
			Authorization: `Bearer ${token}`,
			'Content-Type': 'application/json'
		},
		body: JSON.stringify(data)
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to create symbol');
}

export async function deleteSymbol(symbolId: number): Promise<void> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/symbol/${symbolId}`, {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok || res.status === 204) return;
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to delete symbol');
}

export interface ProductOption {
	id: number;
	name: string;
	slug: string;
}

const KNOWN_PRODUCTS: ProductOption[] = [
	{ id: 1, name: 'Spot', slug: 'SPOT' },
	{ id: 2, name: 'U Margined Perpetual', slug: 'UMARGIN' }
];

export async function getProducts(): Promise<ProductOption[]> {
	try {
		const symbols = await getSymbols(500);
		const byId = new Map<number, ProductOption>();
		for (const s of symbols) {
			if (s.product?.id != null) {
				byId.set(s.product.id, { id: s.product.id, name: s.product.name, slug: s.product.slug });
			}
		}
		for (const known of KNOWN_PRODUCTS) {
			if (!byId.has(known.id)) {
				byId.set(known.id, known);
			}
		}
		return [...byId.values()].sort((a, b) => a.id - b.id);
	} catch {
		return [...KNOWN_PRODUCTS];
	}
}

export interface TradeItemV2 {
	exch_id: number;
	exch_tid: string;
	acct_id: number;
	order_id: string;
	symbol: string;
	product_id: number;
	side: string;
	exec_px: string;
	exec_qty: string;
	exec_fee: string;
	fee_currency: string;
	is_maker: boolean;
	exec_time: string;
}

export interface MyTradesDbParams {
	acct_id?: string;
	symbol_id?: string;
	side?: string;
	start_ms?: number;
	end_ms?: number;
	limit?: number;
}

export async function getMyTradesDb(params: MyTradesDbParams = {}): Promise<TradeItemV2[]> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const q = new URLSearchParams();
	if (params.acct_id) q.set('acct_id', params.acct_id);
	if (params.symbol_id) q.set('symbol_id', params.symbol_id);
	if (params.side && params.side !== 'all') q.set('side', params.side);
	if (params.start_ms != null) q.set('start_ms', String(params.start_ms));
	if (params.end_ms != null) q.set('end_ms', String(params.end_ms));
	if (params.limit != null) q.set('limit', String(params.limit));
	const qs = q.toString();
	const res = await fetch(`${API_V2}/mytrades/db${qs ? '?' + qs : ''}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to fetch trading history');
}

export interface SyncCursorResult {
	acct_id: number;
	symbol_id: number;
	trades_inserted: number;
	cursor_end: number;
	error?: string;
}

export interface SyncResponse {
	total_cursors: number;
	total_inserted: number;
	results: SyncCursorResult[];
}

export async function syncMyTrades(): Promise<SyncResponse> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const res = await fetch(`${API_V2}/mytrades/sync`, {
		method: 'POST',
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	const err = await res.json();
	throw new Error(err.detail || 'Failed to sync trades');
}

// --- Positions ---

export interface PositionItem {
	symbol: string;
	side: string;
	size: string;
	entry_price: string;
	mark_price: string;
	liq_price: string;
	unrealized_pnl: string;
	leverage: string;
	margin_type: string;
	notional: string;
	update_time_ms: number;
}

export interface PositionResponse {
	acct_id: number;
	account_name: string;
	exchange: string;
	positions: PositionItem[];
}

export async function getPositions(acctIds?: number[]): Promise<PositionResponse[]> {
	const token = localStorage.getItem('access_token');
	if (!token) throw new Error('Not authenticated');
	const q = acctIds && acctIds.length > 0 ? `?acct_id=${acctIds.join(',')}` : '';
	const res = await fetch(`${API_V2}/position${q}`, {
		headers: { Authorization: `Bearer ${token}` }
	});
	if (res.ok) return await res.json();
	if (res.status === 401) {
		localStorage.removeItem('access_token');
		throw new Error('Unauthorized');
	}
	if (res.status === 404) return [];
	let detail = 'Failed to fetch positions';
	try {
		const err = await res.json();
		if (err.detail) detail = err.detail;
	} catch { /* non-JSON body */ }
	throw new Error(detail);
}
