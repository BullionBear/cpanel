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

