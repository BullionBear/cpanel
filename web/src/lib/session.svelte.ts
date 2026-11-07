// Signed-in user. The cookie itself stays HttpOnly; this only remembers /me.
import { api, failureMessage, setUnauthorizedHandler, type Me } from './api.ts';

class Session {
	user = $state<Me | null>(null);
	auth = $state<'unknown' | 'in' | 'out'>('unknown');
	meError = $state<string | null>(null);

	markSignedOut(): void {
		this.user = null;
		this.auth = 'out';
		this.meError = null;
	}

	async loadMe(): Promise<void> {
		this.meError = null;
		try {
			const result = await api.GET('/me');
			if (result.response.status === 401) {
				this.markSignedOut();
				return;
			}
			const message = failureMessage(result.error, result.response);
			if (message || !result.data) {
				this.meError = message ?? '沒有內容';
				return;
			}
			this.user = result.data;
			this.auth = 'in';
		} catch {
			this.meError = '連不到 API';
		}
	}

	async logout(): Promise<void> {
		try {
			const result = await api.POST('/auth/logout');
			if (result.response.status === 204 || result.response.status === 401) {
				this.markSignedOut();
				return;
			}
			this.meError = failureMessage(result.error, result.response) ?? `HTTP ${result.response.status}`;
		} catch {
			this.meError = '連不到 API';
		}
	}
}

export const session = new Session();

setUnauthorizedHandler(() => {
	session.markSignedOut();
});
