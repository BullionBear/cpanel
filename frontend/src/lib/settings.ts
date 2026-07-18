import { apiDelete, apiGet, apiPost, ApiError } from './api';

export interface TokenResponse {
	access_token: string;
	token_type: string;
}

export interface AuditEntry {
	id: number;
	actor_type: string;
	actor_id: string;
	actor_name: string | null;
	action: string;
	target: string | null;
	params: Record<string, unknown> | null;
	status: string;
	error: string | null;
	ip_address: string | null;
	user_agent: string | null;
	auth_method: string | null;
	return_code: number | null;
	started_at: string;
	finished_at: string | null;
}

export async function getApiToken(): Promise<string | null> {
	try {
		const data = await apiGet<TokenResponse>('/auth/api-token');
		return data.access_token;
	} catch (e) {
		if (e instanceof ApiError && e.status === 404) return null;
		throw e;
	}
}

export async function createApiToken(): Promise<string> {
	const data = await apiPost<TokenResponse>('/auth/api-token');
	return data.access_token;
}

export async function deleteApiToken(): Promise<void> {
	await apiDelete('/auth/api-token');
}

export async function fetchAuditHistory(limit = 100): Promise<AuditEntry[]> {
	return apiGet<AuditEntry[]>('/audit', { limit: String(limit) });
}

export function authMethodLabel(method: string | null): string {
	if (method === 'api_key') return 'API key';
	if (method === 'discord_oauth') return 'Discord OAuth';
	return method || '—';
}

export function deviceLabel(userAgent: string | null): string {
	if (!userAgent) return '—';
	const ua = userAgent;
	if (/Mobile|Android|iPhone|iPad/i.test(ua)) {
		if (/iPhone/i.test(ua)) return 'iPhone';
		if (/iPad/i.test(ua)) return 'iPad';
		if (/Android/i.test(ua)) return 'Android';
		return 'Mobile';
	}
	if (/Edg\//i.test(ua)) return 'Edge';
	if (/Chrome\//i.test(ua)) return 'Chrome';
	if (/Firefox\//i.test(ua)) return 'Firefox';
	if (/Safari\//i.test(ua) && !/Chrome/i.test(ua)) return 'Safari';
	if (/curl\//i.test(ua)) return 'curl';
	if (/python-httpx|python-requests/i.test(ua)) return 'Script';
	return ua.length > 48 ? `${ua.slice(0, 45)}…` : ua;
}

export function operationLabel(entry: AuditEntry): string {
	if (entry.action === 'http.request' && entry.target) return entry.target;
	if (entry.target) return `${entry.action} · ${entry.target}`;
	return entry.action;
}
