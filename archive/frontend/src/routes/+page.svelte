<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { API_V2 } from '$lib/config';
	import { logout } from '$lib/api';

	const isDev = import.meta.env.DEV;
	let errorMessage = '';

	$: errorMessage = $page.url.searchParams.get('error') ?? '';

	onMount(() => {
		if (localStorage.getItem('access_token')) {
			window.location.href = '/home';
		}
	});

	async function devSignIn() {
		try {
			const res = await fetch(`${API_V2}/auth/session`, { credentials: 'include' });
			if (res.ok) {
				const data = await res.json();
				localStorage.setItem('access_token', data.access_token);
				window.location.href = '/home';
			} else {
				errorMessage = 'Dev sign-in failed. Ensure DEV_MODE=true on the backend.';
			}
		} catch {
			errorMessage = 'Dev sign-in failed. Is the backend running?';
		}
	}
</script>

<svelte:head>
	<title>CPanel</title>
</svelte:head>

<div class="splash">
	<div class="card">
		<h1 class="title">CPanel</h1>
		<p class="body">Sign in with your Discord account to continue.</p>

		{#if errorMessage}
			<p class="error" role="alert">{errorMessage}</p>
		{/if}

		<a class="action discord" href="{API_V2}/auth/discord/login">
			Continue with Discord
		</a>

		{#if isDev}
			<button class="action secondary" type="button" on:click={devSignIn}>
				Dev sign-in
			</button>
		{/if}

		<button class="link" type="button" on:click={() => logout('/')}>
			Clear session
		</button>
	</div>
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
		margin: 0 0 20px;
	}
	.error {
		font-size: 13px;
		line-height: 1.5;
		color: #cf222e;
		background: #ffebe9;
		border: 1px solid #ff818266;
		border-radius: 8px;
		padding: 10px 12px;
		margin: 0 0 16px;
	}

	.action {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 100%;
		height: 40px;
		padding: 0 16px;
		background: #5865f2;
		color: #ffffff;
		font-size: 14px;
		font-weight: 500;
		text-decoration: none;
		border: none;
		border-radius: 6px;
		cursor: pointer;
		transition: background-color 0.15s ease;
		box-sizing: border-box;
	}
	.action:hover { background: #4752c4; }
	.action:active { background: #3c45a5; }
	.action.secondary {
		margin-top: 10px;
		background: #1f2328;
	}
	.action.secondary:hover { background: #2f363d; }

	.link {
		display: block;
		width: 100%;
		margin-top: 16px;
		padding: 0;
		background: none;
		border: none;
		color: #57606a;
		font-size: 13px;
		text-decoration: underline;
		cursor: pointer;
	}
	.link:hover { color: #1f2328; }

	@media (prefers-color-scheme: dark) {
		:global(body) { background: #0d1117; color: #e6edf3; }
		.card {
			background: #161b22;
			border-color: #30363d;
			box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
		}
		.title { color: #e6edf3; }
		.body { color: #8b949e; }
		.error {
			color: #ff7b72;
			background: #490202;
			border-color: #f8514966;
		}
		.action.secondary {
			background: #e6edf3;
			color: #0d1117;
		}
		.action.secondary:hover { background: #ffffff; }
		.link { color: #8b949e; }
		.link:hover { color: #e6edf3; }
	}
</style>
