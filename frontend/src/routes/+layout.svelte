<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { user, loadUser, login, logout } from '$lib/auth';
	import '../app.css';

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
				<a
					class="nav-item"
					class:active={$page.url.pathname.startsWith('/portfolio')}
					href="/portfolio">Portfolio</a
				>
				<a
					class="nav-item"
					class:active={$page.url.pathname.startsWith('/orders')}
					href="/orders">Orders</a
				>
				<a
					class="nav-item"
					class:active={$page.url.pathname.startsWith('/history')}
					href="/history">History</a
				>
			</nav>
		</div>

		<div class="header-right">
		<span class="greeting">Hi, {$user?.username ?? 'guest'}</span>
		{#if $user}
			<button class="btn secondary" type="button" on:click={logout}>Logout</button>
		{:else}
			<button class="btn" type="button" on:click={login}>Login</button>
		{/if}
		</div>
	</header>

	<main class="content">
		<slot />
	</main>
</div>

<style>
	.shell {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
	}

	.header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		height: 4rem;
		padding: 0 3%;
		background: rgba(250, 250, 250, 0.8);
		backdrop-filter: blur(20px);
		-webkit-backdrop-filter: blur(20px);
		border-bottom: 1px solid var(--border);
		box-sizing: border-box;
		position: sticky;
		top: 0;
		z-index: 100;
	}

	.header-left {
		display: flex;
		align-items: center;
		gap: 2rem;
	}

	.brand {
		display: flex;
		align-items: center;
		gap: 0.625rem;
	}
	.logo {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 1.9rem;
		height: 1.9rem;
		border-radius: var(--radius-xs);
		background: var(--accent);
		color: var(--text);
		font-size: 1rem;
		font-weight: 800;
	}
	.brand-name {
		font-size: 1.0625rem;
		font-weight: 800;
		letter-spacing: -0.04em;
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
		padding: 0 0.85rem;
		border-radius: var(--radius-xs);
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--text-muted);
	}
	.nav-item:hover {
		color: var(--text);
	}
	.nav-item.active {
		background: var(--accent);
		color: var(--text);
		font-weight: 600;
	}

	.header-right {
		display: flex;
		align-items: center;
		gap: 1rem;
	}
	.greeting {
		font-size: 0.875rem;
		color: var(--text-muted);
	}

	.content {
		flex: 1;
		width: 100%;
		padding: 2.5% 3%;
		box-sizing: border-box;
	}
</style>
