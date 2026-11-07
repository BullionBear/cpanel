// Problem+json helpers. Kept free of the generated client so node:test can import them.

export type Problem = {
	type: string;
	title: string;
	status: number;
	detail?: string;
	code: string;
};

export function isProblem(value: unknown): value is Problem {
	if (!value || typeof value !== 'object') return false;
	const record = value as Record<string, unknown>;
	return typeof record.status === 'number' && typeof record.code === 'string' && typeof record.title === 'string';
}

/** Message for a failed response. ok responses return undefined. */
export function failureMessage(error: unknown, response: { ok: boolean; status: number }): string | undefined {
	if (response.ok) return undefined;
	if (isProblem(error)) return error.detail || error.title;
	return `HTTP ${response.status}`;
}
