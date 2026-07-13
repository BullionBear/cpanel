import { API_BASE_URL } from './config';

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}
}

/** Authenticated GET returning parsed JSON. */
export async function apiGet<T>(path: string, params?: Record<string, string>): Promise<T> {
	const token = localStorage.getItem('access_token');
	const query = params ? `?${new URLSearchParams(params)}` : '';
	const res = await fetch(`${API_BASE_URL}${path}${query}`, {
		headers: token ? { Authorization: `Bearer ${token}` } : {}
	});
	if (!res.ok) {
		const body = await res.json().catch(() => null);
		throw new ApiError(res.status, body?.detail ?? `Request failed (${res.status})`);
	}
	return res.json();
}
