<script lang="ts">
	import { page } from '$app/stores';

	// Per-exchange sub-navigation. Binance is registration-only, so it leads
	// with a Readme explaining the model; Bybit is full self-service.
	const exchanges = [
		{
			slug: 'binance',
			name: 'Binance',
			tabs: [
				{ id: 'readme', name: 'Readme' },
				{ id: 'account', name: 'Account' }
			]
		},
		{
			slug: 'bybit',
			name: 'Bybit',
			tabs: [
				{ id: 'readme', name: 'Readme' },
				{ id: 'account', name: 'Account' }
			]
		},
		{
			slug: 'gateio',
			name: 'Gate.io',
			tabs: [
				{ id: 'readme', name: 'Readme' },
				{ id: 'account', name: 'Account' }
			]
		},
		{
			slug: 'bitget',
			name: 'Bitget',
			tabs: [
				{ id: 'readme', name: 'Readme' },
				{ id: 'account', name: 'Account' }
			]
		},
		{
			slug: 'okx',
			name: 'OKX',
			tabs: [
				{ id: 'readme', name: 'Readme' },
				{ id: 'account', name: 'Account' }
			]
		}
	];

	$: path = $page.url.pathname;
	$: activeTab = $page.url.searchParams.get('tab');

	function isActive(slug: string, tabId: string, defaultTab: string): boolean {
		if (path !== `/accounts/${slug}`) return false;
		return (activeTab ?? defaultTab) === tabId;
	}
</script>

<div class="accounts-shell">
	<aside class="sidebar">
		<div class="section-title">Accounts</div>
		<a class="side-item" class:active={path === '/accounts'} href="/accounts">General</a>
		<a class="side-item" class:active={path === '/accounts/passphrase'} href="/accounts/passphrase">Passphrase</a>

		<div class="section-title">Exchanges</div>
		{#each exchanges as ex}
			<div class="group-label">{ex.name}</div>
			{#each ex.tabs as tab}
				<a
					class="side-item sub"
					class:active={isActive(ex.slug, tab.id, ex.tabs[0].id)}
					href="/accounts/{ex.slug}?tab={tab.id}">{tab.name}</a
				>
			{/each}
		{/each}
		<span class="side-item disabled">More exchanges soon</span>
	</aside>

	<section class="panel">
		<slot />
	</section>
</div>

<style>
	.accounts-shell {
		display: flex;
		gap: 1.5rem;
		align-items: flex-start;
	}

	.sidebar {
		flex: 0 0 13rem;
		display: flex;
		flex-direction: column;
		gap: 0.125rem;
		padding: 0.75rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
		box-sizing: border-box;
		position: sticky;
		top: 5rem;
	}

	.section-title {
		padding: 0.5rem 0.625rem 0.25rem;
		font-size: 0.6875rem;
		font-weight: 600;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: var(--text-dim);
	}

	.group-label {
		padding: 0.5rem 0.625rem 0.125rem;
		font-size: 0.8125rem;
		font-weight: 700;
		color: var(--text);
	}
	.side-item.sub {
		padding-left: 1.25rem;
	}

	.side-item {
		display: block;
		padding: 0.4375rem 0.625rem;
		border-radius: var(--radius-xs);
		font-size: 0.875rem;
		font-weight: 500;
		color: var(--text-muted);
		text-decoration: none;
	}
	.side-item:hover {
		background: var(--white);
		color: var(--text);
	}
	.side-item.active {
		background: var(--accent);
		color: var(--text);
		font-weight: 600;
	}
	.side-item.disabled {
		color: var(--text-dim);
		cursor: default;
		font-size: 0.8125rem;
	}
	.side-item.disabled:hover {
		background: transparent;
		color: var(--text-dim);
	}

	.panel {
		flex: 1;
		min-width: 0;
	}
</style>
