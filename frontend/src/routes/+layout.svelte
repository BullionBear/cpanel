<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { user, loadUser, login, logout } from '$lib/auth';

	onMount(loadUser);
</script>

<div class="shell">
	<header class="header">
		<div class="header-left">
			<a class="brand" href="/">
				<span class="logo">C</span>
				<span class="brand-name">CPanel</span>
			</a>

			<nav class="nav">
				<a class="nav-item" class:active={$page.url.pathname === '/'} href="/">Home</a>
				<a
					class="nav-item"
					class:active={$page.url.pathname.startsWith('/symbols')}
					href="/symbols">Symbols</a
				>
				<a
					class="nav-item"
					class:active={$page.url.pathname.startsWith('/accounts')}
					href="/accounts">Accounts</a
				>
			</nav>
		</div>

		<div class="header-right">
			<span class="greeting">Hi, {$user?.username ?? 'guest'}</span>
			{#if $user}
				<button class="action secondary" type="button" on:click={logout}>Logout</button>
			{:else}
				<button class="action" type="button" on:click={login}>Login</button>
			{/if}
		</div>
	</header>

	<main class="content">
		<slot />
	</main>
</div>

<style>
	:global(body) {
		margin: 0;
		padding: 0;
		min-height: 100vh;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu,
			Cantarell, sans-serif;
		background: #f6f8fa;
		color: #1f2328;
	}

	.shell {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
	}

	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 3.5rem;
		padding: 0 2%;
		background: #ffffff;
		border-bottom: 1px solid #e3e5e8;
		box-sizing: border-box;
	}

	.header-left {
		display: flex;
		align-items: center;
		gap: 1.5rem;
	}

	.brand {
		display: flex;
		align-items: center;
		gap: 0.625rem;
		text-decoration: none;
		color: inherit;
	}
	.logo {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 2rem;
		height: 2rem;
		border-radius: 0.5rem;
		background: #5865f2;
		color: #ffffff;
		font-size: 1rem;
		font-weight: 700;
	}
	.brand-name {
		font-size: 1rem;
		font-weight: 600;
	}

	.nav {
		display: flex;
		align-items: center;
		gap: 0.25rem;
	}
	.nav-item {
		display: inline-flex;
		align-items: center;
		height: 2.125rem;
		padding: 0 0.75rem;
		border-radius: 0.375rem;
		font-size: 0.875rem;
		color: #57606a;
		text-decoration: none;
	}
	.nav-item:hover {
		background: #f6f8fa;
		color: #1f2328;
	}
	.nav-item.active {
		background: #eef1f4;
		color: #1f2328;
		font-weight: 600;
	}

	.header-right {
		display: flex;
		align-items: center;
		gap: 0.875rem;
	}
	.greeting {
		font-size: 0.875rem;
		color: #57606a;
	}

	.action {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		height: 2.125rem;
		padding: 0 1rem;
		background: #5865f2;
		color: #ffffff;
		font-size: 0.875rem;
		font-weight: 500;
		border: none;
		border-radius: 0.375rem;
		cursor: pointer;
		transition: background-color 0.15s ease;
	}
	.action:hover { background: #4752c4; }
	.action.secondary { background: #1f2328; }
	.action.secondary:hover { background: #2f363d; }

	.content {
		flex: 1;
		width: 100%;
		padding: 2.5% 3%;
		box-sizing: border-box;
	}

	@media (prefers-color-scheme: dark) {
		:global(body) {
			background: #0d1117;
			color: #e6edf3;
		}
		.header {
			background: #161b22;
			border-color: #30363d;
		}
		.greeting { color: #8b949e; }
		.action.secondary {
			background: #e6edf3;
			color: #0d1117;
		}
		.action.secondary:hover { background: #ffffff; }
		.nav-item { color: #8b949e; }
		.nav-item:hover {
			background: #21262d;
			color: #e6edf3;
		}
		.nav-item.active {
			background: #21262d;
			color: #e6edf3;
		}
	}
</style>
