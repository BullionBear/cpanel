<script lang="ts">
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api';
	import {
		fetchOverview,
		fetchHoldings,
		fetchHistory,
		triggerSnapshot,
		type OverviewItem,
		type Holdings,
		type HistoryPoint
	} from '$lib/portfolio';

	let overview: OverviewItem[] = [];
	let loading = true;
	let error = '';

	let selected: number | null = null;
	let holdings: Holdings | null = null;
	let history: HistoryPoint[] = [];
	let detailLoading = false;
	let snapshotting = false;

	$: totalValue = overview.reduce((sum, item) => sum + Number(item.total_value || 0), 0);
	$: byExchange = overview.reduce<Record<string, OverviewItem[]>>((acc, item) => {
		(acc[item.exchange] ??= []).push(item);
		return acc;
	}, {});

	function money(value: string | number | null): string {
		if (value === null) return '—';
		const n = Number(value);
		if (!Number.isFinite(n)) return '—';
		return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
	}

	function num(value: string | null): string {
		if (value === null) return '—';
		return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
	}

	function ago(iso: string | null): string {
		if (!iso) return 'never';
		const diff = Date.now() - new Date(iso).getTime();
		const mins = Math.round(diff / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hrs = Math.round(mins / 60);
		if (hrs < 24) return `${hrs}h ago`;
		return `${Math.round(hrs / 24)}d ago`;
	}

	async function loadOverview() {
		loading = true;
		error = '';
		try {
			overview = await fetchOverview();
		} catch (e) {
			overview = [];
			error =
				e instanceof ApiError && e.status === 401
					? 'Please log in to view the portfolio.'
					: 'Failed to load portfolio.';
		} finally {
			loading = false;
		}
	}

	async function select(item: OverviewItem) {
		selected = item.account_id;
		detailLoading = true;
		holdings = null;
		history = [];
		try {
			[holdings, history] = await Promise.all([
				fetchHoldings(item.exchange, item.label),
				fetchHistory(item.exchange, item.label)
			]);
		} catch {
			error = 'Failed to load account holdings.';
		} finally {
			detailLoading = false;
		}
	}

	async function snapshotNow() {
		if (selected === null) return;
		const item = overview.find((o) => o.account_id === selected);
		if (!item) return;
		snapshotting = true;
		try {
			holdings = await triggerSnapshot(item.exchange, item.label);
			history = await fetchHistory(item.exchange, item.label);
			await loadOverview();
		} catch (e) {
			error =
				e instanceof ApiError ? e.message : 'Snapshot failed.';
		} finally {
			snapshotting = false;
		}
	}

	onMount(loadOverview);
</script>

<svelte:head>
	<title>Portfolio · CPanel</title>
</svelte:head>

<div class="page">
	<div class="page-head">
		<h1>Portfolio</h1>
		{#if !loading && overview.length > 0}
			<span class="total">≈ {money(totalValue)} USDT</span>
		{/if}
	</div>

	{#if error}
		<div class="notice">{error}</div>
	{:else if loading}
		<div class="notice">Loading…</div>
	{:else if overview.length === 0}
		<div class="notice">No accounts registered yet. Register one to start snapshotting.</div>
	{:else}
		<div class="layout">
			<aside class="accounts">
				{#each Object.entries(byExchange) as [exchange, accounts]}
					<div class="exch-group">
						<div class="exch-name">{exchange}</div>
						{#each accounts as item}
							<button
								class="acct"
								class:active={selected === item.account_id}
								class:sub={item.account_type === 'sub'}
								on:click={() => select(item)}
							>
								<span class="acct-label">{item.label}</span>
								<span class="acct-val">{money(item.total_value)}</span>
							</button>
						{/each}
					</div>
				{/each}
			</aside>

			<section class="detail">
				{#if selected === null}
					<div class="notice">Select an account to see its holdings.</div>
				{:else if detailLoading}
					<div class="notice">Loading holdings…</div>
				{:else if holdings}
					<div class="detail-head">
						<div>
							<h2>{holdings.label}</h2>
							<span class="sub-text">
								{holdings.exchange} ·
								{holdings.taken_at ? `snapshot ${ago(holdings.taken_at)}` : 'no snapshot yet'}
							</span>
						</div>
						<div class="detail-head-right">
							<span class="detail-total">{money(holdings.total_value)} {holdings.quote_asset}</span>
							<button class="btn" on:click={snapshotNow} disabled={snapshotting}>
								{snapshotting ? 'Snapshotting…' : 'Snapshot now'}
							</button>
						</div>
					</div>

					{#if holdings.balances.length === 0 && holdings.positions.length === 0}
						<div class="notice">
							No snapshot data yet. Use “Snapshot now” to capture the current state.
						</div>
					{/if}

					{#if holdings.balances.length > 0}
						<h3>Balances</h3>
						<div class="table-wrap">
							<table>
								<thead>
									<tr>
										<th>Wallet</th>
										<th>Asset</th>
										<th class="right">Free</th>
										<th class="right">Locked</th>
										<th class="right">Total</th>
										<th class="right">Value ({holdings.quote_asset})</th>
									</tr>
								</thead>
								<tbody>
									{#each holdings.balances as b}
										<tr>
											<td>{b.wallet}</td>
											<td class="mono strong">{b.asset}</td>
											<td class="mono right">{num(b.free)}</td>
											<td class="mono right">{num(b.locked)}</td>
											<td class="mono right">{num(b.total)}</td>
											<td class="mono right">{money(b.value_quote)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}

					{#if holdings.positions.length > 0}
						<h3>Positions</h3>
						<div class="table-wrap">
							<table>
								<thead>
									<tr>
										<th>Symbol</th>
										<th>Side</th>
										<th class="right">Size</th>
										<th class="right">Entry</th>
										<th class="right">Mark</th>
										<th class="right">uPnL</th>
										<th class="right">Notional</th>
									</tr>
								</thead>
								<tbody>
									{#each holdings.positions as p}
										<tr>
											<td class="mono strong">{p.universal_symbol}</td>
											<td><span class="side {p.side}">{p.side}</span></td>
											<td class="mono right">{num(p.size)}</td>
											<td class="mono right">{num(p.entry_price)}</td>
											<td class="mono right">{num(p.mark_price)}</td>
											<td class="mono right">{num(p.unrealized_pnl)}</td>
											<td class="mono right">{money(p.value_quote)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}

					{#if history.length > 1}
						<h3>History</h3>
						<div class="table-wrap history">
							<table>
								<thead>
									<tr>
										<th>Taken at</th>
										<th class="right">Total value ({holdings.quote_asset})</th>
									</tr>
								</thead>
								<tbody>
									{#each history as point}
										<tr>
											<td>{new Date(point.taken_at).toLocaleString()}</td>
											<td class="mono right">{money(point.total_value)}</td>
										</tr>
									{/each}
								</tbody>
							</table>
						</div>
					{/if}
				{/if}
			</section>
		</div>
	{/if}
</div>

<style>
	.page {
		max-width: 78rem;
		margin: 0 auto;
	}
	.page-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.75rem;
		margin-bottom: 1.25rem;
	}
	h1 {
		margin: 0;
		font-size: 1.5rem;
	}
	.total {
		font-family: var(--mono);
		font-size: 1.125rem;
		font-weight: 700;
	}

	.layout {
		display: grid;
		grid-template-columns: 16rem 1fr;
		gap: 1.25rem;
		align-items: start;
	}

	.accounts {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
		padding: 0.5rem;
	}
	.exch-group {
		margin-bottom: 0.75rem;
	}
	.exch-name {
		font-size: 0.6875rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-dim);
		padding: 0.35rem 0.5rem;
	}
	.acct {
		display: flex;
		align-items: center;
		justify-content: space-between;
		width: 100%;
		padding: 0.5rem 0.6rem;
		border: none;
		background: none;
		border-radius: var(--radius-xs);
		font-family: inherit;
		font-size: 0.8125rem;
		color: var(--text);
		cursor: pointer;
		text-align: left;
	}
	.acct:hover {
		background: var(--border-light);
	}
	.acct.active {
		background: var(--accent);
		font-weight: 600;
	}
	.acct.sub .acct-label {
		padding-left: 0.75rem;
		color: var(--text-muted);
	}
	.acct-val {
		font-family: var(--mono);
		font-variant-numeric: tabular-nums;
		color: var(--text-muted);
	}

	.detail {
		min-width: 0;
	}
	.detail-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 1.25rem;
	}
	.detail-head h2 {
		margin: 0;
		font-size: 1.25rem;
	}
	.sub-text {
		font-size: 0.8125rem;
		color: var(--text-muted);
	}
	.detail-head-right {
		display: flex;
		align-items: center;
		gap: 1rem;
	}
	.detail-total {
		font-family: var(--mono);
		font-size: 1.125rem;
		font-weight: 700;
	}
	.btn {
		height: 2.125rem;
		padding: 0 0.9rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-xs);
		background: var(--accent);
		color: var(--text);
		font-family: inherit;
		font-size: 0.8125rem;
		font-weight: 600;
		cursor: pointer;
	}
	.btn:hover:not(:disabled) {
		border-color: var(--text);
	}
	.btn:disabled {
		opacity: 0.6;
		cursor: default;
	}

	h3 {
		margin: 1.5rem 0 0.5rem;
		font-size: 0.75rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
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
	.table-wrap.history {
		max-height: 20rem;
		overflow-y: auto;
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
		text-transform: capitalize;
		font-weight: 600;
	}
	.side.long {
		color: #157347;
	}
	.side.short {
		color: #b02a37;
	}

	@media (max-width: 52rem) {
		.layout {
			grid-template-columns: 1fr;
		}
	}
</style>
