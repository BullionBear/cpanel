<script lang="ts">
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api';
	import {
		fetchTrades,
		fetchOrderHistory,
		fetchStreams,
		syncAccountHistory,
		type TradeRow,
		type OrderHistoryRow,
		type StreamHealth
	} from '$lib/history';
	import { fetchOverview, type OverviewItem } from '$lib/portfolio';

	type Tab = 'trades' | 'orders' | 'streams';

	const PAGE_SIZE = 50;

	let tab: Tab = 'trades';

	// Filters (shared by trades and orders)
	let exchange = '';
	let accountId = '';
	let symbol = '';
	let startTime = '';
	let endTime = '';
	let offset = 0;

	let accounts: OverviewItem[] = [];
	let trades: TradeRow[] = [];
	let orders: OrderHistoryRow[] = [];
	let streams: StreamHealth[] = [];
	let total = 0;
	let loading = false;
	let error = '';
	let syncing = false;
	let syncMessage = '';
	let symbolTimer: ReturnType<typeof setTimeout> | undefined;

	function filterParams(): Record<string, string> {
		const params: Record<string, string> = {
			limit: String(PAGE_SIZE),
			offset: String(offset)
		};
		if (exchange) params.exchange = exchange;
		if (accountId) params.account_id = accountId;
		if (symbol.trim()) params.universal_symbol = symbol.trim();
		if (startTime) params.start = new Date(startTime).toISOString();
		if (endTime) params.end = new Date(endTime).toISOString();
		return params;
	}

	async function load() {
		loading = true;
		error = '';
		try {
			if (tab === 'trades') {
				const page = await fetchTrades(filterParams());
				trades = page.items;
				total = page.total;
			} else if (tab === 'orders') {
				const page = await fetchOrderHistory(filterParams());
				orders = page.items;
				total = page.total;
			} else {
				const params: Record<string, string> = {};
				if (exchange) params.exchange = exchange;
				if (accountId) params.account_id = accountId;
				streams = await fetchStreams(params);
				total = streams.length;
			}
		} catch (e) {
			trades = [];
			orders = [];
			streams = [];
			total = 0;
			error =
				e instanceof ApiError && e.status === 401
					? 'Please log in to browse history.'
					: 'Failed to load history.';
		} finally {
			loading = false;
		}
	}

	function applyFilters() {
		offset = 0;
		load();
	}

	function onSymbolInput() {
		clearTimeout(symbolTimer);
		symbolTimer = setTimeout(applyFilters, 300);
	}

	function switchTab(next: Tab) {
		if (tab === next) return;
		tab = next;
		offset = 0;
		load();
	}

	function prevPage() {
		offset = Math.max(0, offset - PAGE_SIZE);
		load();
	}

	function nextPage() {
		if (offset + PAGE_SIZE < total) {
			offset += PAGE_SIZE;
			load();
		}
	}

	async function syncNow() {
		if (!accountId || syncing) return;
		syncing = true;
		syncMessage = '';
		try {
			const result = await syncAccountHistory(Number(accountId));
			syncMessage = `Synced ${result.ingested} rows`;
			await load();
		} catch (e) {
			syncMessage = e instanceof ApiError ? e.message : 'Sync failed';
		} finally {
			syncing = false;
		}
	}

	function num(value: string | null): string {
		if (value === null) return '—';
		return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
	}

	function ts(value: string | null): string {
		return value ? new Date(value).toLocaleString() : '—';
	}

	function accountLabel(id: number): string {
		const account = accounts.find((a) => a.account_id === id);
		return account ? `${account.exchange} · ${account.label}` : `#${id}`;
	}

	onMount(async () => {
		try {
			accounts = await fetchOverview();
		} catch {
			accounts = [];
		}
		await load();
	});
</script>

<svelte:head>
	<title>History · CPanel</title>
</svelte:head>

<div class="page">
	<div class="page-head">
		<h1>History</h1>
		<span class="count">{total} {tab === 'streams' ? 'streams' : 'rows'}</span>
	</div>

	<div class="tabs">
		<button class="tab" class:active={tab === 'trades'} on:click={() => switchTab('trades')}>
			Trades
		</button>
		<button class="tab" class:active={tab === 'orders'} on:click={() => switchTab('orders')}>
			Orders
		</button>
		<button class="tab" class:active={tab === 'streams'} on:click={() => switchTab('streams')}>
			Sync health
		</button>
	</div>

	<div class="toolbar">
		<select bind:value={exchange} on:change={applyFilters}>
			<option value="">All exchanges</option>
			<option value="BINANCE">Binance</option>
			<option value="BYBIT">Bybit</option>
			<option value="GATEIO">Gate.io</option>
		</select>
		<select bind:value={accountId} on:change={applyFilters}>
			<option value="">All accounts</option>
			{#each accounts as account (account.account_id)}
				<option value={String(account.account_id)}>
					{account.exchange} · {account.label}
				</option>
			{/each}
		</select>
		{#if tab !== 'streams'}
			<input
				class="symbol"
				type="search"
				placeholder="Universal symbol…"
				bind:value={symbol}
				on:input={onSymbolInput}
			/>
			<input type="datetime-local" bind:value={startTime} on:change={applyFilters} />
			<span class="muted">to</span>
			<input type="datetime-local" bind:value={endTime} on:change={applyFilters} />
		{/if}
		{#if accountId}
			<button class="btn small" on:click={syncNow} disabled={syncing}>
				{syncing ? 'Syncing…' : 'Sync now'}
			</button>
		{/if}
		{#if syncMessage}
			<span class="muted">{syncMessage}</span>
		{/if}
	</div>

	{#if error}
		<div class="notice">{error}</div>
	{:else if loading && total === 0}
		<div class="notice">Loading…</div>
	{:else if tab === 'trades'}
		{#if trades.length === 0}
			<div class="notice">
				No trades ingested yet. New accounts backfill the past 3 days on their first sync.
			</div>
		{:else}
			<div class="table-wrap" class:dim={loading}>
				<table>
					<thead>
						<tr>
							<th>Time</th>
							<th>Account</th>
							<th>Symbol</th>
							<th>Side</th>
							<th class="right">Price</th>
							<th class="right">Qty</th>
							<th class="right">Fee</th>
							<th>Trade ID</th>
							<th>Order ID</th>
						</tr>
					</thead>
					<tbody>
						{#each trades as t (t.id)}
							<tr>
								<td class="mono">{ts(t.traded_at)}</td>
								<td>{accountLabel(t.account_id)}</td>
								<td class="mono strong">{t.universal_symbol}</td>
								<td><span class="side {t.side.toLowerCase()}">{t.side}</span></td>
								<td class="mono right">{num(t.price)}</td>
								<td class="mono right">{num(t.qty)}</td>
								<td class="mono right">{num(t.fee)} {t.fee_asset}</td>
								<td class="mono">{t.native_trade_id}</td>
								<td class="mono">{t.native_order_id}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{:else if tab === 'orders'}
		{#if orders.length === 0}
			<div class="notice">
				No order history ingested yet. New accounts backfill the past 3 days on their first sync.
			</div>
		{:else}
			<div class="table-wrap" class:dim={loading}>
				<table>
					<thead>
						<tr>
							<th>Placed</th>
							<th>Account</th>
							<th>Symbol</th>
							<th>Side</th>
							<th>Type</th>
							<th>Status</th>
							<th class="right">Price</th>
							<th class="right">Qty</th>
							<th class="right">Filled</th>
							<th>Order ID</th>
						</tr>
					</thead>
					<tbody>
						{#each orders as o (o.id)}
							<tr>
								<td class="mono">{ts(o.placed_at)}</td>
								<td>{accountLabel(o.account_id)}</td>
								<td class="mono strong">{o.universal_symbol}</td>
								<td><span class="side {o.side.toLowerCase()}">{o.side}</span></td>
								<td>{o.type}</td>
								<td><span class="badge">{o.status}</span></td>
								<td class="mono right">{num(o.price)}</td>
								<td class="mono right">{num(o.qty)}</td>
								<td class="mono right">{num(o.filled_qty)}</td>
								<td class="mono">{o.native_order_id}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{:else if streams.length === 0}
		<div class="notice">
			No sync streams yet — they appear after the first history sync of an account.
		</div>
	{:else}
		<div class="table-wrap" class:dim={loading}>
			<table>
				<thead>
					<tr>
						<th>Exchange</th>
						<th>Account</th>
						<th>Kind</th>
						<th>Stream</th>
						<th>Last synced</th>
						<th>Status</th>
					</tr>
				</thead>
				<tbody>
					{#each streams as s (`${s.account_id}:${s.data_kind}:${s.stream_key}`)}
						<tr>
							<td>{s.exchange}</td>
							<td>{s.account_label}</td>
							<td>{s.data_kind}</td>
							<td class="mono">{s.stream_key}</td>
							<td class="mono">{ts(s.last_synced_at)}</td>
							<td>
								{#if s.last_error}
									<span class="badge halted" title={s.last_error}>error</span>
								{:else if s.last_synced_at}
									<span class="badge active">ok</span>
								{:else}
									<span class="badge">pending</span>
								{/if}
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}

	{#if tab !== 'streams' && total > PAGE_SIZE}
		<div class="pager">
			<button on:click={prevPage} disabled={offset === 0}>Previous</button>
			<span>{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
			<button on:click={nextPage} disabled={offset + PAGE_SIZE >= total}>Next</button>
		</div>
	{/if}
</div>

<style>
	.page {
		max-width: 82rem;
		margin: 0 auto;
	}
	.page-head {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin-bottom: 1rem;
	}
	h1 {
		margin: 0;
		font-size: 1.5rem;
	}
	.count {
		font-size: 0.875rem;
		color: var(--text-muted);
	}

	.tabs {
		display: flex;
		gap: 0.25rem;
		margin-bottom: 1rem;
	}
	.tab {
		height: 2.125rem;
		padding: 0 0.85rem;
		border: none;
		border-radius: var(--radius-xs);
		background: transparent;
		color: var(--text-muted);
		font-family: inherit;
		font-size: 0.875rem;
		font-weight: 500;
		cursor: pointer;
	}
	.tab:hover {
		color: var(--text);
	}
	.tab.active {
		background: var(--accent);
		color: var(--text);
		font-weight: 600;
	}

	.toolbar {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}
	.symbol {
		min-width: 14rem;
	}

	.notice {
		padding: 2rem;
		text-align: center;
		color: var(--text-muted);
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
	}

	.table-wrap {
		overflow-x: auto;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
	}
	.table-wrap.dim {
		opacity: 0.6;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8125rem;
	}
	th,
	td {
		padding: 0.6rem 0.85rem;
		text-align: left;
		white-space: nowrap;
	}
	th {
		font-weight: 700;
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
		border-bottom: 1px solid var(--border);
	}
	tbody tr {
		border-bottom: 1px solid var(--border-light);
	}
	tbody tr:last-child {
		border-bottom: none;
	}
	.right {
		text-align: right;
	}
	.mono {
		font-family: var(--mono);
		font-variant-numeric: tabular-nums;
	}
	.strong {
		font-weight: 600;
	}

	.side {
		font-weight: 700;
		font-size: 0.75rem;
	}
	.side.buy {
		color: var(--success);
	}
	.side.sell {
		color: var(--danger);
	}

	.pager {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 1rem;
		margin-top: 1rem;
		font-size: 0.875rem;
		color: var(--text-muted);
	}
	.pager button {
		height: 2rem;
		padding: 0 0.875rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-xs);
		background: var(--white);
		color: var(--text);
		font-family: inherit;
		font-size: 0.8125rem;
		font-weight: 600;
		cursor: pointer;
		transition: border-color 0.2s ease;
	}
	.pager button:hover:not(:disabled) {
		border-color: var(--text);
	}
	.pager button:disabled {
		opacity: 0.5;
		cursor: default;
	}
</style>
