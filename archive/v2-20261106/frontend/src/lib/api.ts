import { API_BASE_URL } from './config';

export class ApiError extends Error {
	constructor(
		public status: number,
		message: string
	) {
		super(message);
		this.name = 'ApiError';
	}
}

/** FastAPI `detail` is a string, a validation-error array, or rarely an object. */
export function formatApiDetail(detail: unknown): string | null {
	if (detail == null) return null;
	if (typeof detail === 'string') return detail;
	if (Array.isArray(detail)) {
		const parts = detail
			.map((item) => {
				if (typeof item === 'string') return item;
				if (item && typeof item === 'object' && 'msg' in item) {
					return String((item as { msg: unknown }).msg);
				}
				return null;
			})
			.filter((part): part is string => Boolean(part));
		return parts.length ? parts.join('; ') : null;
	}
	if (typeof detail === 'object' && 'msg' in detail) {
		return String((detail as { msg: unknown }).msg);
	}
	return null;
}

export function apiErrorMessage(err: unknown, fallback = 'Request failed'): string {
	if (err instanceof ApiError && err.message) return err.message;
	if (err instanceof Error && err.message) return err.message;
	return fallback;
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
		const message =
			formatApiDetail(errBody?.detail) ??
			(typeof errBody?.message === 'string' ? errBody.message : null) ??
			`Request failed (${res.status})`;
		throw new ApiError(res.status, message);
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

/** Authenticated PATCH with JSON body. */
export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
	return request<T>('PATCH', path, body);
}

/** Authenticated DELETE. */
export async function apiDelete<T>(path: string): Promise<T> {
	return request<T>('DELETE', path);
}
