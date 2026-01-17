import { API_BASE_URL } from './config';

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
		const response = await fetch(`${API_BASE_URL}/auth/me`, {
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


export interface APIKey {
	id: number;
	user_id: number;
	exchange: string;
	name: string;
	master_is: number | null;
	created_at: string;
}

export interface APIKeyCreate {
	exchange: string;
	name: string;
	api_key: string;
	api_secret: string;
	passphrase?: string;
	master_is?: string;
}

export async function getAPIKeys(): Promise<APIKey[]> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_BASE_URL}/apikey`, {
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
		const response = await fetch(`${API_BASE_URL}/apikey/register`, {
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
		const response = await fetch(`${API_BASE_URL}/apikey/${apiId}`, {
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
		const response = await fetch(`${API_BASE_URL}/preference?layout=${layout}`, {
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
		const response = await fetch(`${API_BASE_URL}/market/bbos?symbol_id=${symbolIdParam}`);

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
		const response = await fetch(`${API_BASE_URL}/preference?layout=${layout}`, {
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
		const response = await fetch(`${API_BASE_URL}/symbol?limit=${limit}`);

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
		const response = await fetch(`${API_BASE_URL}/order`, {
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
		const response = await fetch(`${API_BASE_URL}/balance?id=${idParam}`, {
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
		const error = await response.json();
		throw new Error(error.detail || 'Failed to fetch balances');
	} catch (error) {
		console.error('Failed to get balances:', error);
		throw error;
	}
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

export async function getOpenOrders(): Promise<OpenOrder[]> {
	const token = localStorage.getItem('access_token');
	if (!token) {
		throw new Error('Not authenticated');
	}

	try {
		const response = await fetch(`${API_BASE_URL}/order/open`, {
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
		const response = await fetch(`${API_BASE_URL}/order`, {
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
		const response = await fetch(`${API_BASE_URL}/auth/api-token`, {
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
		const response = await fetch(`${API_BASE_URL}/auth/api-token`, {
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
		const response = await fetch(`${API_BASE_URL}/auth/api-token`, {
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
