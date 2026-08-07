import { API_BASE_URL } from './config';

export interface BalanceUpdatedEvent {
	type: 'balance.updated';
	account_id: number;
	exchange: string;
	label: string;
	reason: string;
	taken_at: string | null;
}

export type BalanceUpdateHandler = (event: BalanceUpdatedEvent) => void;

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;

/**
 * Subscribe to authenticated SSE ``balance.updated`` events.
 * Returns an unsubscribe function that aborts the stream and stops reconnects.
 */
export function subscribeBalanceUpdates(onEvent: BalanceUpdateHandler): () => void {
	let stopped = false;
	let controller: AbortController | null = null;
	let backoff = INITIAL_BACKOFF_MS;
	let reconnectTimer: ReturnType<typeof setTimeout> | undefined;

	async function connect() {
		if (stopped) return;
		controller = new AbortController();
		const token = localStorage.getItem('access_token');
		try {
			const res = await fetch(`${API_BASE_URL}/events/stream`, {
				headers: token ? { Authorization: `Bearer ${token}` } : {},
				signal: controller.signal
			});
			if (!res.ok || !res.body) {
				throw new Error(`SSE connect failed (${res.status})`);
			}
			backoff = INITIAL_BACKOFF_MS;
			await readStream(res.body, onEvent, () => stopped);
			if (!stopped) scheduleReconnect();
		} catch (err) {
			if (stopped || (err instanceof DOMException && err.name === 'AbortError')) return;
			scheduleReconnect();
		}
	}

	function scheduleReconnect() {
		if (stopped) return;
		clearTimeout(reconnectTimer);
		reconnectTimer = setTimeout(() => {
			backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
			connect();
		}, backoff);
	}

	connect();

	return () => {
		stopped = true;
		clearTimeout(reconnectTimer);
		controller?.abort();
	};
}

async function readStream(
	body: ReadableStream<Uint8Array>,
	onEvent: BalanceUpdateHandler,
	isStopped: () => boolean
) {
	const reader = body.getReader();
	const decoder = new TextDecoder();
	let buffer = '';
	while (!isStopped()) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		const frames = buffer.split('\n\n');
		buffer = frames.pop() ?? '';
		for (const frame of frames) {
			const event = parseSseFrame(frame);
			if (event) onEvent(event);
		}
	}
}

function parseSseFrame(frame: string): BalanceUpdatedEvent | null {
	let eventType = 'message';
	const dataLines: string[] = [];
	for (const line of frame.split('\n')) {
		if (!line || line.startsWith(':')) continue;
		if (line.startsWith('event:')) {
			eventType = line.slice(6).trim();
		} else if (line.startsWith('data:')) {
			dataLines.push(line.slice(5).trim());
		}
	}
	if (eventType !== 'balance.updated' || dataLines.length === 0) return null;
	try {
		const parsed = JSON.parse(dataLines.join('\n')) as BalanceUpdatedEvent;
		if (parsed?.type !== 'balance.updated' || typeof parsed.account_id !== 'number') {
			return null;
		}
		return parsed;
	} catch {
		return null;
	}
}
