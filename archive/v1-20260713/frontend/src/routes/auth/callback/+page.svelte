<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { API_V2 } from '$lib/config';

	let errorMessage = '';
	let loading = true;

	onMount(async () => {
		const code = $page.url.searchParams.get('code');
		if (!code) {
			errorMessage = 'Missing sign-in code.';
			loading = false;
			return;
		}

		try {
			const res = await fetch(`${API_V2}/auth/discord/exchange`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ code })
			});

			if (res.ok) {
				const data = await res.json();
				localStorage.setItem('access_token', data.access_token);
				window.location.href = '/home';
				return;
			}

			const err = await res.json().catch(() => ({}));
			errorMessage = err.detail || 'Sign-in failed. Please try again.';
		} catch {
			errorMessage = 'Sign-in failed. Please try again.';
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head>
	<title>Signing in…</title>
</svelte:head>

<div class="splash">
	{#if loading}
		<div class="loader" role="status" aria-live="polite">
			<span class="spinner" aria-hidden="true"></span>
			<p class="body muted">Signing in…</p>
		</div>
	{:else}
		<div class="card" role="alert">
			<h1 class="title">Sign-in failed</h1>
			<p class="body">{errorMessage}</p>
			<a class="action" href="/">Back to sign in</a>
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
		box-sizing: border-box;
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
	}
	.action:hover { background: #2f363d; }

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
		:global(body) { background: #0d1117; color: #e6edf3; }
		.card {
			background: #161b22;
			border-color: #30363d;
		}
		.title { color: #e6edf3; }
		.body { color: #8b949e; }
		.body.muted { color: #6e7681; }
		.action {
			background: #e6edf3;
			color: #0d1117;
		}
		.action:hover { background: #ffffff; }
		.spinner {
			border-color: #30363d;
			border-top-color: #8b949e;
		}
	}
</style>
