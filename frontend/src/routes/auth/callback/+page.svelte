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
			<a class="btn" href="/">Back to Home</a>
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
		box-sizing: border-box;
		text-align: center;
	}
	h1 {
		font-size: 1.25rem;
		margin: 0 0 0.5rem;
	}
	p {
		font-size: 0.9375rem;
		line-height: 1.55;
		color: var(--text-muted);
		margin: 0;
	}

	.card .btn {
		margin-top: 1.25rem;
	}

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
		border: 0.1875rem solid var(--border);
		border-top-color: var(--text);
		animation: spin 0.8s linear infinite;
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
</style>
