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
			<h1 class="title">Access denied</h1>
			<p class="body">Your account does not have access to CPanel.</p>

			{#if isDev}
				<div class="note">
					<p class="note-title">Local dev checklist</p>
					<ul>
						<li>Backend env has <code>DEV_MODE=true</code></li>
						<li>Backend env has <code>DEV_USER</code> set</li>
						<li>That user exists in the local database</li>
					</ul>
				</div>
			{/if}

			<a
				class="action"
				href={isDev ? '/' : 'https://auth.lynxlinkage.com/logout'}
			>
				Sign out
			</a>
		</div>
	{:else}
		<div class="loader" role="status" aria-live="polite">
			<span class="spinner" aria-hidden="true"></span>
			<p class="body muted">Authenticating…</p>
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
		background: #f4f5f7;
		color: #1f2328;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
	}

	.card {
		width: 100%;
		max-width: 400px;
		background: #ffffff;
		border: 1px solid #e3e5e8;
		border-radius: 12px;
		padding: 28px;
		box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
	}
	.title {
		font-size: 18px;
		font-weight: 600;
		margin: 0 0 8px;
		color: #1f2328;
	}
	.body {
		font-size: 14px;
		line-height: 1.55;
		color: #57606a;
		margin: 0;
	}
	.body.muted { color: #6e7781; }

	.note {
		margin: 20px 0 0;
		padding: 14px 16px;
		background: #f6f8fa;
		border: 1px solid #e3e5e8;
		border-radius: 8px;
	}
	.note-title {
		font-size: 12px;
		font-weight: 600;
		color: #1f2328;
		margin: 0 0 8px;
	}
	.note ul {
		margin: 0;
		padding-left: 18px;
		color: #57606a;
		font-size: 13px;
		line-height: 1.7;
	}
	.note code {
		font-family: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, monospace;
		font-size: 12px;
		background: #eaecef;
		color: #1f2328;
		padding: 1px 6px;
		border-radius: 4px;
	}

	.action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		height: 36px;
		padding: 0 16px;
		margin-top: 20px;
		background: #1f2328;
		color: #ffffff;
		font-size: 14px;
		font-weight: 500;
		text-decoration: none;
		border-radius: 6px;
		transition: background-color 0.15s ease;
	}
	.action:hover { background: #2f363d; }
	.action:active { background: #14171a; }

	.loader {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 12px;
	}
	.spinner {
		width: 24px;
		height: 24px;
		border-radius: 50%;
		border: 2.5px solid #e3e5e8;
		border-top-color: #57606a;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin { to { transform: rotate(360deg); } }

	@media (prefers-color-scheme: dark) {
		.splash { background: #0d1117; color: #e6edf3; }
		.card {
			background: #161b22;
			border-color: #30363d;
			box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
		}
		.title { color: #e6edf3; }
		.body { color: #8b949e; }
		.body.muted { color: #6e7681; }
		.note {
			background: #0d1117;
			border-color: #30363d;
		}
		.note-title { color: #e6edf3; }
		.note ul { color: #8b949e; }
		.note code {
			background: #21262d;
			color: #e6edf3;
		}
		.action {
			background: #e6edf3;
			color: #0d1117;
		}
		.action:hover { background: #ffffff; }
		.action:active { background: #c9d1d9; }
		.spinner {
			border-color: #30363d;
			border-top-color: #8b949e;
		}
	}
</style>
