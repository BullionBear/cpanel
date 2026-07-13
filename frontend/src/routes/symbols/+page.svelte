<script lang="ts">
	import { onMount } from 'svelte';
	import { apiGet, ApiError } from '$lib/api';

	interface SymbolInfo {
		universal_symbol: string;
		exchange: string;
		market_type: string;
		native_symbol: string;
		base_asset: string;
		quote_asset: string;
		price_tick: string;
		qty_step: string;
		min_qty: string;
		max_qty: string | null;
		min_notional: string | null;
		contract_size: string | null;
		settle_asset: string | null;
		status: string;
		last_price: string | null;
	}

	interface SymbolPage {
		total: number;
		items: SymbolInfo[];
	}

	const PAGE_SIZE = 50;

	let exchange = '';
	let marketType = '';
	let status = 'active';
	let search = '';
	let offset = 0;

	let items: SymbolInfo[] = [];
	let total = 0;
	let loading = false;
	let error = '';
	let searchTimer: ReturnType<typeof setTimeout> | undefined;

	async function load() {
		loading = true;
		error = '';
		try {
			const params: Record<string, string> = {
				status,
				limit: String(PAGE_SIZE),
				offset: String(offset)
			};
			if (exchange) params.exchange = exchange;
			if (marketType) params.market_type = marketType;
			if (search.trim()) params.search = search.trim();
			const page = await apiGet<SymbolPage>('/symbols', params);
			items = page.items;
			total = page.total;
		} catch (e) {
			items = [];
			total = 0;
			error =
				e instanceof ApiError && e.status === 401
					? 'Please log in to browse symbols.'
					: 'Failed to load symbols.';
		} finally {
			loading = false;
		}
	}

	function applyFilters() {
		offset = 0;
		load();
	}

	function onSearchInput() {
		clearTimeout(searchTimer);
		searchTimer = setTimeout(applyFilters, 300);
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

	/** Trim trailing zeros from decimal strings for display. */
	function num(value: string | null): string {
		if (value === null) return '—';
		return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
	}

	onMount(load);
</script>

<svelte:head>
	<title>Symbols · CPanel</title>
</svelte:head>

<div class="page">
	<div class="page-head">
		<h1>Symbols</h1>
		<span class="count">{total} instruments</span>
	</div>

	<div class="toolbar">
		<input
			class="search"
			type="search"
			placeholder="Search symbol or asset…"
			bind:value={search}
			on:input={onSearchInput}
		/>
		<select bind:value={exchange} on:change={applyFilters}>
			<option value="">All exchanges</option>
			<option value="BINANCE">Binance</option>
			<option value="BYBIT">Bybit</option>
		</select>
		<select bind:value={marketType} on:change={applyFilters}>
			<option value="">All markets</option>
			<option value="SPOT">Spot</option>
			<option value="PERP">Perp</option>
			<option value="CMARGIN">Coin-margined</option>
		</select>
		<select bind:value={status} on:change={applyFilters}>
			<option value="active">Active</option>
			<option value="halted">Halted</option>
			<option value="delisted">Delisted</option>
			<option value="all">All statuses</option>
		</select>
	</div>

	{#if error}
		<div class="notice">{error}</div>
	{:else if loading && items.length === 0}
		<div class="notice">Loading…</div>
	{:else if items.length === 0}
		<div class="notice">No symbols match. The refresh job may not have run yet.</div>
	{:else}
		<div class="table-wrap" class:dim={loading}>
			<table>
				<thead>
					<tr>
						<th>Universal symbol</th>
						<th>Native</th>
						<th>Market</th>
						<th class="right">Last price</th>
						<th class="right">Tick</th>
						<th class="right">Qty step</th>
						<th class="right">Min qty</th>
						<th class="right">Min notional</th>
						<th>Status</th>
					</tr>
				</thead>
				<tbody>
					{#each items as s (s.universal_symbol)}
						<tr>
							<td class="mono strong">{s.universal_symbol}</td>
							<td class="mono">{s.native_symbol}</td>
							<td>{s.market_type}</td>
							<td class="mono right">{num(s.last_price)}</td>
							<td class="mono right">{num(s.price_tick)}</td>
							<td class="mono right">{num(s.qty_step)}</td>
							<td class="mono right">{num(s.min_qty)}</td>
							<td class="mono right">{num(s.min_notional)}</td>
							<td><span class="badge {s.status}">{s.status}</span></td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>

		<div class="pager">
			<button on:click={prevPage} disabled={offset === 0}>Previous</button>
			<span>{offset + 1}–{Math.min(offset + PAGE_SIZE, total)} of {total}</span>
			<button on:click={nextPage} disabled={offset + PAGE_SIZE >= total}>Next</button>
		</div>
	{/if}
</div>

<style>
	.page {
		max-width: 75rem;
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
		font-size: 1.375rem;
	}
	.count {
		font-size: 0.875rem;
		color: #57606a;
	}

	.toolbar {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-bottom: 1rem;
	}
	.search {
		flex: 1;
		min-width: 12rem;
	}
	.search,
	select {
		height: 2.25rem;
		padding: 0 0.625rem;
		border: 1px solid #d0d7de;
		border-radius: 0.375rem;
		background: #ffffff;
		color: inherit;
		font-size: 0.875rem;
		box-sizing: border-box;
	}

	.notice {
		padding: 2rem;
		text-align: center;
		color: #57606a;
		background: #ffffff;
		border: 1px solid #e3e5e8;
		border-radius: 0.5rem;
	}

	.table-wrap {
		overflow-x: auto;
		background: #ffffff;
		border: 1px solid #e3e5e8;
		border-radius: 0.5rem;
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
		padding: 0.5rem 0.75rem;
		text-align: left;
		white-space: nowrap;
	}
	th {
		font-weight: 600;
		color: #57606a;
		border-bottom: 1px solid #e3e5e8;
	}
	tbody tr {
		border-bottom: 1px solid #f0f2f4;
	}
	tbody tr:last-child {
		border-bottom: none;
	}
	.right {
		text-align: right;
	}
	.mono {
		font-family: ui-monospace, 'SF Mono', Menlo, monospace;
	}
	.strong {
		font-weight: 600;
	}

	.badge {
		display: inline-block;
		padding: 0.125rem 0.5rem;
		border-radius: 1rem;
		font-size: 0.75rem;
		font-weight: 500;
		text-transform: capitalize;
	}
	.badge.active {
		background: #dafbe1;
		color: #116329;
	}
	.badge.halted {
		background: #fff8c5;
		color: #7d4e00;
	}
	.badge.delisted {
		background: #ffebe9;
		color: #a40e26;
	}

	.pager {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 1rem;
		margin-top: 1rem;
		font-size: 0.875rem;
		color: #57606a;
	}
	.pager button {
		height: 2rem;
		padding: 0 0.875rem;
		border: 1px solid #d0d7de;
		border-radius: 0.375rem;
		background: #ffffff;
		color: inherit;
		font-size: 0.8125rem;
		cursor: pointer;
	}
	.pager button:disabled {
		opacity: 0.5;
		cursor: default;
	}

	@media (prefers-color-scheme: dark) {
		.count,
		.pager {
			color: #8b949e;
		}
		.search,
		select,
		.pager button {
			background: #161b22;
			border-color: #30363d;
		}
		.notice,
		.table-wrap {
			background: #161b22;
			border-color: #30363d;
		}
		th {
			color: #8b949e;
			border-color: #30363d;
		}
		tbody tr {
			border-color: #21262d;
		}
		.badge.active {
			background: #12341f;
			color: #3fb950;
		}
		.badge.halted {
			background: #3a2d0c;
			color: #d29922;
		}
		.badge.delisted {
			background: #3c1418;
			color: #f85149;
		}
	}
</style>
