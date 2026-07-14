import { API_BASE_URL } from './config';

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
	}
}

async function request<T>(method: string, path: string, body?: unknown, params?: Record<string, string>): Promise<T> {
	const token = localStorage.getItem('access_token');
	const query = params ? `?${new URLSearchParams(params)}` : '';
	const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
	if (body !== undefined) headers['Content-Type'] = 'application/json';
	const res = await fetch(`${API_BASE_URL}${path}${query}`, {
		method,
		headers,
		body: body !== undefined ? JSON.stringify(body) : undefined
	});
	if (!res.ok) {
		const errBody = await res.json().catch(() => null);
		throw new ApiError(res.status, errBody?.detail ?? `Request failed (${res.status})`);
	}
	if (res.status === 204) return undefined as T;
	return res.json();
}

/** Authenticated GET returning parsed JSON. */
export async function apiGet<T>(path: string, params?: Record<string, string>): Promise<T> {
	return request<T>('GET', path, undefined, params);
}

/** Authenticated POST with JSON body. */
export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
	return request<T>('POST', path, body);
}

/** Authenticated PUT with JSON body. */
export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
	return request<T>('PUT', path, body);
}
