<script lang="ts">
	import '../app.css';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { session } from '#lib/session.svelte.ts';
	import { onMount } from 'svelte';
	import type { LayoutProps } from './$types';

	let { children }: LayoutProps = $props();

	const nav = [
		{ href: '/accounts', label: 'Accounts' },
		{ href: '/history', label: 'History' },
		{ href: '/snapshots', label: 'Snapshots' },
		{ href: '/ops', label: 'Ops' },
		{ href: '/settings', label: 'Settings' }
	];

	function current(href: string, path: string): boolean {
		return path === href || path.startsWith(`${href}/`);
	}

	onMount(() => {
		void session.loadMe();
	});

	$effect(() => {
		if (session.auth === 'out' && page.url.pathname !== '/login') {
			void goto('/login', { replaceState: true });
		}
		if (session.auth === 'in' && page.url.pathname === '/login') {
			void goto('/', { replaceState: true });
		}
	});
</script>

<svelte:head>
	<title>cpanel</title>
</svelte:head>

{#if page.url.pathname === '/login'}
	{@render children()}
{:else if session.auth === 'in'}
	<header class="top">
		<a class="brand" href="/">cpanel</a>
		<nav aria-label="主要">
			{#each nav as item (item.href)}
				<a href={item.href} aria-current={current(item.href, page.url.pathname) ? 'page' : undefined}>
					{item.label}
				</a>
			{/each}
		</nav>
		<div class="user">
			<span>{session.user?.display_name}</span>
			<button type="button" onclick={() => session.logout()}>登出</button>
		</div>
	</header>
	{#if session.meError}
		<p class="error" role="alert">{session.meError}</p>
	{/if}
	<main>
		{@render children()}
	</main>
{:else if session.meError}
	<main>
		<p class="error" role="alert">{session.meError}</p>
		<button type="button" onclick={() => session.loadMe()}>再試一次</button>
	</main>
{:else}
	<main>
		<p class="muted">載入中…</p>
	</main>
{/if}
