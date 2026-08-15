<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { ApiError } from '$lib/api';
	import { subscribeBalanceUpdates } from '$lib/events';
	import PieChart from '$lib/PieChart.svelte';
	import LineChart from '$lib/LineChart.svelte';
	import type { Slice, Point } from '$lib/charts';
	import {
		fetchOverview,
		fetchHoldings,
		fetchHistory,
		fetchPnlHistory,
		requestRefresh,
		type OverviewItem,
		type Holdings,
		type HistoryPoint,
		type PnlPoint
	} from '$lib/portfolio';

	let overview: OverviewItem[] = [];
	let loading = true;
	let error = '';

	let selected: number | null = null;
	let holdings: Holdings | null = null;
	let history: HistoryPoint[] = [];
	let pnlHistory: PnlPoint[] = [];
	let detailLoading = false;
	let refreshing = false;
	let refreshTimer: ReturnType<typeof setTimeout> | undefined;
	let unsubEvents: (() => void) | undefined;

	$: totalValue = overview.reduce((sum, item) => sum + Number(item.total_value || 0), 0);
	$: byExchange = overview.reduce<Record<string, OverviewItem[]>>((acc, item) => {
		(acc[item.exchange] ??= []).push(item);
		return acc;
	}, {});

	// Spot allocation pie: coin holdings aggregated by asset across *all* wallets
	// (spot, margin, derivatives collateral, funding) valued in the quote asset.
	$: pieSlices = holdings ? buildSlices(holdings) : [];
	$: historyPoints = buildPoints(history);

	// Position views: notional-exposure allocation, PnL trend, and summary stats.
	$: notionalSlices = holdings ? buildNotionalSlices(holdings) : [];
	$: pnlPoints = buildPnlPoints(pnlHistory);
	$: posStats = holdings ? buildPosStats(holdings) : null;

	function buildSlices(h: Holdings): Slice[] {
		const byAsset: Record<string, number> = {};
		for (const b of h.balances) {
			const v = Number(b.value_quote);
			if (b.value_quote !== null && Number.isFinite(v) && v > 0)
				byAsset[b.asset] = (byAsset[b.asset] ?? 0) + v;
		}
		const items: Slice[] = Object.entries(byAsset).map(([asset, v]) => ({ label: asset, value: v }));
		return topSlices(items);
	}

	function buildNotionalSlices(h: Holdings): Slice[] {
		const items: Slice[] = [];
		for (const p of h.positions) {
			const v = Math.abs(Number(p.value_quote));
			if (p.value_quote !== null && Number.isFinite(v) && v > 0)
				items.push({ label: p.universal_symbol, value: v });
		}
		return topSlices(items);
	}

	// Slices sorted by value descending. The smallest slices are merged into a
	// single "Other" class while their combined value stays under 5% of the
	// total (merging one slice alone would just rename it, so skip that).
	function topSlices(items: Slice[]): Slice[] {
		items.sort((a, b) => b.value - a.value);
		const total = items.reduce((s, i) => s + i.value, 0);
		if (total <= 0) return items;
		let rest = 0;
		let cut = items.length;
		for (let i = items.length - 1; i >= 0; i--) {
			if (rest + items[i].value >= total * 0.05) break;
			rest += items[i].value;
			cut = i;
		}
		if (items.length - cut < 2) return items;
		return [...items.slice(0, cut), { label: 'Other', value: rest }];
	}

	function buildPoints(hist: HistoryPoint[]): Point[] {
		return hist
			.map((p) => ({ t: new Date(p.taken_at).getTime(), value: Number(p.total_value) }))
			.filter((p) => Number.isFinite(p.value));
	}

	function buildPnlPoints(hist: PnlPoint[]): Point[] {
		return hist
			.map((p) => ({ t: new Date(p.taken_at).getTime(), value: Number(p.pnl) }))
			.filter((p) => Number.isFinite(p.value));
	}

	interface PosStats {
		notional: number;
		pnl: number;
		margin: number | null;
		leverage: number | null;
	}

	// Notional = gross exposure (Σ|size·mark|). Leverage = notional / equity.
	// Posted margin is isolated margin only (cross positions report zero, so
	// treat "no margin data" as unknown rather than zero).
	function buildPosStats(h: Holdings): PosStats {
		let notional = 0;
		let pnl = 0;
		let margin = 0;
		let hasMargin = false;
		for (const p of h.positions) {
			const n = Number(p.value_quote);
			if (p.value_quote !== null && Number.isFinite(n)) notional += Math.abs(n);
			const u = Number(p.unrealized_pnl);
			if (p.unrealized_pnl !== null && Number.isFinite(u)) pnl += u;
			const m = Number(p.margin);
			if (p.margin !== null && Number.isFinite(m)) {
				margin += m;
				if (m > 0) hasMargin = true;
			}
		}
		const equity = Number(h.total_value);
		const usableEquity = Number.isFinite(equity) && equity > 0 ? equity : null;
		return {
			notional,
			pnl,
			margin: hasMargin ? margin : null,
			leverage: usableEquity ? notional / usableEquity : null
		};
	}

	function lev(value: number | null): string {
		if (value === null || !Number.isFinite(value)) return '—';
		return `${value.toLocaleString(undefined, { maximumFractionDigits: 2 })}×`;
	}

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

	async function loadOverview(quiet = false) {
		if (!quiet) {
			loading = true;
			error = '';
		}
		try {
			overview = await fetchOverview();
		} catch (e) {
			if (!quiet) {
				overview = [];
				error =
					e instanceof ApiError && e.status === 401
						? 'Please log in to view the portfolio.'
						: 'Failed to load portfolio.';
			}
		} finally {
			if (!quiet) loading = false;
		}
	}

	async function select(item: OverviewItem) {
		selected = item.account_id;
		detailLoading = true;
		holdings = null;
		history = [];
		pnlHistory = [];
		try {
			[holdings, history, pnlHistory] = await Promise.all([
				fetchHoldings(item.exchange, item.label),
				fetchHistory(item.exchange, item.label),
				fetchPnlHistory(item.exchange, item.label)
			]);
		} catch {
			error = 'Failed to load account holdings.';
		} finally {
			detailLoading = false;
		}
	}

	function clearRefreshing() {
		refreshing = false;
		clearTimeout(refreshTimer);
		refreshTimer = undefined;
	}

	async function refreshSelected() {
		if (selected === null) return;
		const item = overview.find((o) => o.account_id === selected);
		if (!item) return;
		refreshing = true;
		error = '';
		clearTimeout(refreshTimer);
		refreshTimer = setTimeout(() => {
			void onBalanceUpdated(item.account_id);
		}, 15_000);
		try {
			await requestRefresh(item.exchange, item.label);
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Refresh failed.';
			clearRefreshing();
		}
	}

	async function onBalanceUpdated(accountId: number) {
		await loadOverview(true);
		if (selected !== accountId) return;
		const item = overview.find((o) => o.account_id === accountId);
		if (!item) {
			clearRefreshing();
			return;
		}
		try {
			[holdings, history, pnlHistory] = await Promise.all([
				fetchHoldings(item.exchange, item.label),
				fetchHistory(item.exchange, item.label),
				fetchPnlHistory(item.exchange, item.label)
			]);
		} catch {
			/* keep prior holdings */
		} finally {
			clearRefreshing();
		}
	}

	onMount(() => {
		loadOverview();
		unsubEvents = subscribeBalanceUpdates((ev) => {
			if (ev.type === 'balance.refresh_failed') {
				if (selected === ev.account_id || refreshing) {
					error = `Refresh failed (${ev.exchange}:${ev.label}): ${ev.error}`;
					clearRefreshing();
				}
				return;
			}
			void onBalanceUpdated(ev.account_id);
		});
	});

	onDestroy(() => {
		unsubEvents?.();
		clearTimeout(refreshTimer);
	});
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
		<div class="notice error-banner" role="alert">{error}</div>
	{/if}
	{#if loading}
		<div class="notice">Loading…</div>
	{:else if overview.length === 0 && !error}
		<div class="notice">No accounts registered yet. Register one to start snapshotting.</div>
	{:else if overview.length > 0}
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
								<span class="acct-meta">
									<span class="acct-val">{money(item.total_value)}</span>
									<span class="acct-age">{ago(item.taken_at)}</span>
								</span>
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
							<button class="btn" on:click={refreshSelected} disabled={refreshing}>
								{refreshing ? 'Refreshing…' : 'Refresh'}
							</button>
						</div>
					</div>

					{#if holdings.balances.length === 0 && holdings.positions.length === 0}
						<div class="notice">
							No snapshot data yet. Use “Refresh” to capture the current state from the exchange.
						</div>
					{:else}
						<div class="charts">
							<div class="chart-card">
							<h3>Spot allocation</h3>
							<PieChart slices={pieSlices} unit={holdings.quote_asset} />
							</div>
							<div class="chart-card">
								<h3>Asset value history</h3>
								<LineChart points={historyPoints} unit={holdings.quote_asset} />
								{#if historyPoints.length < 2}
									<p class="chart-hint">Take more snapshots to build a trend.</p>
								{/if}
							</div>
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

					{#if holdings.positions.length > 0 && posStats}
						<h3>Position statistics</h3>
						<div class="stats">
							<div class="stat">
								<span class="stat-label">Notional</span>
								<span class="stat-val">{money(posStats.notional)} {holdings.quote_asset}</span>
							</div>
							<div class="stat">
								<span class="stat-label">Unrealized PnL</span>
								<span
									class="stat-val"
									class:pos={posStats.pnl > 0}
									class:neg={posStats.pnl < 0}
								>{money(posStats.pnl)} {holdings.quote_asset}</span>
							</div>
							<div class="stat">
								<span class="stat-label">Leverage</span>
								<span class="stat-val">{lev(posStats.leverage)}</span>
							</div>
							<div class="stat">
								<span class="stat-label">Posted margin</span>
								<span class="stat-val">
									{posStats.margin === null ? '—' : `${money(posStats.margin)} ${holdings.quote_asset}`}
								</span>
							</div>
						</div>
						<div class="charts">
							<div class="chart-card">
								<h3>Notional allocation</h3>
								<PieChart slices={notionalSlices} unit={holdings.quote_asset} />
							</div>
							<div class="chart-card">
								<h3>Position PnL history</h3>
								<LineChart points={pnlPoints} unit={holdings.quote_asset} />
								{#if pnlPoints.length < 2}
									<p class="chart-hint">Take more snapshots to build a trend.</p>
								{/if}
							</div>
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
	.acct-meta {
		display: flex;
		flex-direction: column;
		align-items: flex-end;
		gap: 0.1rem;
	}
	.acct-val {
		font-family: var(--mono);
		font-variant-numeric: tabular-nums;
		color: var(--text-muted);
	}
	.acct-age {
		font-size: 0.7rem;
		color: var(--text-dim, var(--text-muted));
		opacity: 0.85;
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

	.charts {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 1rem;
		margin-bottom: 0.5rem;
	}
	.chart-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
		padding: 1rem 1.25rem 1.25rem;
		min-width: 0;
	}
	.chart-card h3 {
		margin-top: 0.25rem;
	}
	.chart-hint {
		margin: 0.6rem 0 0;
		font-size: 0.75rem;
		color: var(--text-dim);
	}

	.stats {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
		gap: 0.75rem;
		margin-bottom: 0.5rem;
	}
	.stat {
		display: flex;
		flex-direction: column;
		gap: 0.2rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
		padding: 0.7rem 0.85rem;
	}
	.stat-label {
		font-size: 0.6875rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
	}
	.stat-val {
		font-family: var(--mono);
		font-variant-numeric: tabular-nums;
		font-size: 1rem;
		font-weight: 700;
	}
	.stat-val.pos {
		color: #157347;
	}
	.stat-val.neg {
		color: #b02a37;
	}
	@media (max-width: 60rem) {
		.charts {
			grid-template-columns: 1fr;
		}
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

	.notice.error-banner {
		padding: 0.75rem 1rem;
		margin-bottom: 0.75rem;
		text-align: left;
		color: var(--danger, #b42318);
		border-color: color-mix(in srgb, var(--danger, #b42318) 35%, var(--border));
	}

	.table-wrap {
		overflow-x: auto;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
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
