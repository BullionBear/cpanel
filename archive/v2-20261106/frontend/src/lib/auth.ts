import { writable } from 'svelte/store';
import { API_BASE_URL } from './config';

export interface User {
	id: string;
	username: string;
}

/** null means not signed in (guest). */
export const user = writable<User | null>(null);

export async function loadUser(): Promise<void> {
	const token = localStorage.getItem('access_token');
	if (!token) return;
	try {
		const res = await fetch(`${API_BASE_URL}/auth/me`, {
			headers: { Authorization: `Bearer ${token}` }
		});
		if (res.ok) {
			user.set(await res.json());
		} else {
			localStorage.removeItem('access_token');
			user.set(null);
		}
	} catch {
		// Backend unreachable; stay signed out for this session.
	}
}

export function login(): void {
	window.location.href = `${API_BASE_URL}/auth/discord/login`;
}

export function logout(): void {
	localStorage.removeItem('access_token');
	user.set(null);
}
