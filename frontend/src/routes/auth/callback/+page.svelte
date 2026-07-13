<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { API_BASE_URL } from '$lib/config';

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
			const res = await fetch(`${API_BASE_URL}/auth/discord/exchange`, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ code })
			});

			if (res.ok) {
				const data = await res.json();
				localStorage.setItem('access_token', data.access_token);
				window.location.href = '/';
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

<div class="wrap">
	{#if loading}
		<div class="loader" role="status" aria-live="polite">
			<span class="spinner" aria-hidden="true"></span>
			<p class="muted">Signing in…</p>
		</div>
	{:else}
		<div class="card" role="alert">
			<h1>Sign-in failed</h1>
			<p>{errorMessage}</p>
			<a class="action" href="/">Back to Home</a>
		</div>
	{/if}
</div>

<style>
	.wrap {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 50vh;
	}

	.card {
		width: 90%;
		max-width: 25rem;
		background: #ffffff;
		border: 1px solid #e3e5e8;
		border-radius: 0.75rem;
		padding: 1.75rem;
		box-sizing: border-box;
		box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04);
	}
	h1 {
		font-size: 1.125rem;
		font-weight: 600;
		margin: 0 0 0.5rem;
	}
	p {
		font-size: 0.875rem;
		line-height: 1.55;
		color: #57606a;
		margin: 0;
	}
	.muted { color: #6e7781; }

	.action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		height: 2.25rem;
		padding: 0 1rem;
		margin-top: 1.25rem;
		background: #1f2328;
		color: #ffffff;
		font-size: 0.875rem;
		font-weight: 500;
		text-decoration: none;
		border-radius: 0.375rem;
	}
	.action:hover { background: #2f363d; }

	.loader {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 0.75rem;
	}
	.spinner {
		width: 1.5rem;
		height: 1.5rem;
		border-radius: 50%;
		border: 0.1875rem solid #e3e5e8;
		border-top-color: #57606a;
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to { transform: rotate(360deg); }
	}

	@media (prefers-color-scheme: dark) {
		.card {
			background: #161b22;
			border-color: #30363d;
		}
		p { color: #8b949e; }
		.muted { color: #6e7681; }
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
