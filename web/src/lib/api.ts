// Client for cpanel-api. Types come from contract/openapi/cpanel.v1.yaml
// via `make openapi` (web/src/lib/gen/api/v1.ts).
import createClient, { type Middleware } from 'openapi-fetch';
import type { components, paths } from './gen/api/v1.ts';
import { failureMessage, isProblem, type Problem } from './problem.ts';

export type { Problem };
export { failureMessage, isProblem };
export type Me = components['schemas']['Me'];
export type Token = components['schemas']['Token'];
export type TokenCreated = components['schemas']['TokenCreated'];
export type Stream = components['schemas']['Stream'];
export type Job = components['schemas']['Job'];
export type JobRun = components['schemas']['JobRun'];
export type ErrorClass = components['schemas']['ErrorClass'];
export type StreamStatus = components['schemas']['StreamStatus'];
export type JobStatus = components['schemas']['JobStatus'];
export type JobRunStatus = components['schemas']['JobRunStatus'];
export type MirrorSource = components['schemas']['MirrorSource'];
export type UnresolvedUid = components['schemas']['UnresolvedUid'];

// Same-origin cookies. Dev reaches the API through the Vite proxy.
export const api = createClient<paths>({
	baseUrl: '',
	credentials: 'same-origin'
});

let onUnauthorized: (() => void) | null = null;

/** Called for any API 401. */
export function setUnauthorizedHandler(fn: () => void): void {
	onUnauthorized = fn;
}

const redirectOnUnauthorized: Middleware = {
	onResponse({ response }) {
		if (response.status === 401) onUnauthorized?.();
		return response;
	}
};

api.use(redirectOnUnauthorized);
