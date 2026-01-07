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
