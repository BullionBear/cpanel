<script lang="ts">
	import { page } from '$app/stores';

	const exchanges = [
		{ slug: 'binance', name: 'Binance' },
		{ slug: 'bybit', name: 'Bybit' }
	];

	$: path = $page.url.pathname;
</script>

<div class="accounts-shell">
	<aside class="sidebar">
		<div class="section-title">Accounts</div>
		<a class="side-item" class:active={path === '/accounts'} href="/accounts">General</a>

		<div class="section-title">Exchanges</div>
		{#each exchanges as ex}
			<a
				class="side-item"
				class:active={path.startsWith(`/accounts/${ex.slug}`)}
				href="/accounts/{ex.slug}">{ex.name}</a
			>
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
		background: #ffffff;
		border: 1px solid #e3e5e8;
		border-radius: 0.625rem;
		box-sizing: border-box;
		position: sticky;
		top: 1rem;
	}

	.section-title {
		padding: 0.5rem 0.625rem 0.25rem;
		font-size: 0.6875rem;
		font-weight: 600;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: #8b949e;
	}

	.side-item {
		display: block;
		padding: 0.4375rem 0.625rem;
		border-radius: 0.375rem;
		font-size: 0.875rem;
		color: #57606a;
		text-decoration: none;
	}
	.side-item:hover {
		background: #f6f8fa;
		color: #1f2328;
	}
	.side-item.active {
		background: #eef1f4;
		color: #1f2328;
		font-weight: 600;
	}
	.side-item.disabled {
		color: #a8b1ba;
		cursor: default;
		font-size: 0.8125rem;
	}
	.side-item.disabled:hover {
		background: transparent;
	}

	.panel {
		flex: 1;
		min-width: 0;
	}

	@media (prefers-color-scheme: dark) {
		.sidebar {
			background: #161b22;
			border-color: #30363d;
		}
		.side-item {
			color: #8b949e;
		}
		.side-item:hover {
			background: #21262d;
			color: #e6edf3;
		}
		.side-item.active {
			background: #21262d;
			color: #e6edf3;
		}
		.side-item.disabled {
			color: #565f68;
		}
		.side-item.disabled:hover {
			background: transparent;
		}
	}
</style>
