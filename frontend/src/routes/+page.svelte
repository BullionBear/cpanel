<script lang="ts">
	import { onMount } from 'svelte';
	import { API_V2 } from '$lib/config';

	let accessDenied = false;
	const isDev = import.meta.env.DEV;

	onMount(async () => {
		try {
			// redirect: 'manual' lets us distinguish an Authelia 302 (session
			// expired → reload to re-authenticate) from a backend 4xx (user is
			// authenticated but has no cpanel account → show error, don't loop).
			const res = await fetch(`${API_V2}/auth/session`, {
				credentials: 'include',
				redirect: 'manual'
			});

			if (res.ok) {
				const data = await res.json();
				localStorage.setItem('access_token', data.access_token);
				window.location.href = '/home';
			} else if (res.type === 'opaqueredirect' || res.status === 0) {
				// Authelia redirected to login portal — full reload to follow it
				window.location.reload();
			} else {
				// Authenticated by Authelia but denied by the backend.
				// Show an error rather than reloading into an infinite loop.
				accessDenied = true;
			}
		} catch {
			window.location.reload();
		}
	});
</script>

<svelte:head>
	<title>CPanel</title>
</svelte:head>

<div class="splash">
	{#if accessDenied}
		<div class="card" role="alert" aria-live="polite">
			<div class="icon" aria-hidden="true">
				<svg viewBox="0 0 24 24" width="28" height="28" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
					<circle cx="12" cy="12" r="10" />
					<line x1="12" y1="8" x2="12" y2="12" />
					<line x1="12" y1="16" x2="12.01" y2="16" />
				</svg>
			</div>

			<h1 class="title">Access denied</h1>
			<p class="subtitle">Your account does not have access to CPanel.</p>

			{#if isDev}
				<div class="hint">
					<p class="hint-label">Dev mode checklist</p>
					<ul>
						<li>Backend env has <code>DEV_MODE=true</code></li>
						<li>Backend env has <code>DEV_USER</code> set</li>
						<li>That user exists in the local database</li>
					</ul>
				</div>
			{:else}
				<a href="https://auth.lynxlinkage.com/logout" class="action">Sign out</a>
			{/if}
		</div>
	{:else}
		<div class="loader" role="status" aria-live="polite">
			<span class="spinner" aria-hidden="true"></span>
			<p class="msg">Authenticating…</p>
		</div>
	{/if}
</div>

<style>
	.splash {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 100vh;
		padding: 24px;
		background:
			radial-gradient(1200px 600px at 10% -10%, rgba(99, 102, 241, 0.12), transparent 60%),
			radial-gradient(900px 500px at 110% 110%, rgba(14, 165, 233, 0.10), transparent 60%),
			linear-gradient(180deg, #fafbff 0%, #f3f4f6 100%);
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
		color: #111827;
	}

	/* Loading state */
	.loader {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 14px;
	}
	.spinner {
		width: 28px;
		height: 28px;
		border-radius: 50%;
		border: 2.5px solid rgba(17, 24, 39, 0.12);
		border-top-color: #4f46e5;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to { transform: rotate(360deg); }
	}
	.msg {
		color: #6b7280;
		font-size: 14px;
		margin: 0;
	}

	/* Access-denied card */
	.card {
		width: 100%;
		max-width: 420px;
		background: #ffffff;
		border: 1px solid rgba(17, 24, 39, 0.06);
		border-radius: 16px;
		padding: 32px 28px;
		box-shadow:
			0 1px 2px rgba(17, 24, 39, 0.04),
			0 12px 32px -8px rgba(17, 24, 39, 0.12);
		text-align: center;
	}
	.icon {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 52px;
		height: 52px;
		border-radius: 50%;
		background: #fef2f2;
		color: #dc2626;
		margin-bottom: 16px;
	}
	.title {
		font-size: 20px;
		font-weight: 600;
		margin: 0 0 6px;
		color: #111827;
		letter-spacing: -0.01em;
	}
	.subtitle {
		font-size: 14px;
		color: #6b7280;
		margin: 0 0 20px;
		line-height: 1.5;
	}

	/* Dev hint */
	.hint {
		text-align: left;
		background: #f9fafb;
		border: 1px solid #e5e7eb;
		border-radius: 10px;
		padding: 14px 16px;
	}
	.hint-label {
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: #6366f1;
		margin: 0 0 8px;
	}
	.hint ul {
		margin: 0;
		padding-left: 18px;
		color: #374151;
		font-size: 13px;
		line-height: 1.7;
	}
	.hint code {
		font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Liberation Mono', monospace;
		font-size: 12px;
		background: #eef2ff;
		color: #4338ca;
		padding: 1px 6px;
		border-radius: 4px;
	}

	/* Sign-out action (production) */
	.action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		height: 40px;
		padding: 0 18px;
		background: #111827;
		color: #ffffff;
		font-size: 14px;
		font-weight: 500;
		text-decoration: none;
		border-radius: 8px;
		transition: background-color 0.15s ease, transform 0.05s ease;
	}
	.action:hover {
		background: #1f2937;
	}
	.action:active {
		transform: translateY(1px);
	}

	@media (prefers-color-scheme: dark) {
		.splash {
			background:
				radial-gradient(1200px 600px at 10% -10%, rgba(99, 102, 241, 0.18), transparent 60%),
				radial-gradient(900px 500px at 110% 110%, rgba(14, 165, 233, 0.12), transparent 60%),
				linear-gradient(180deg, #0b1020 0%, #0a0f1c 100%);
			color: #e5e7eb;
		}
		.card {
			background: #111827;
			border-color: rgba(255, 255, 255, 0.06);
			box-shadow:
				0 1px 2px rgba(0, 0, 0, 0.3),
				0 12px 32px -8px rgba(0, 0, 0, 0.5);
		}
		.title { color: #f9fafb; }
		.subtitle, .msg { color: #9ca3af; }
		.icon { background: rgba(220, 38, 38, 0.15); color: #fca5a5; }
		.hint {
			background: #0f172a;
			border-color: rgba(255, 255, 255, 0.06);
		}
		.hint ul { color: #d1d5db; }
		.hint-label { color: #a5b4fc; }
		.hint code {
			background: rgba(99, 102, 241, 0.15);
			color: #c7d2fe;
		}
		.action {
			background: #6366f1;
		}
		.action:hover {
			background: #4f46e5;
		}
		.spinner {
			border-color: rgba(255, 255, 255, 0.12);
			border-top-color: #a5b4fc;
		}
	}
</style>
