<script lang="ts">
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api';
	import { fetchOverview, type OverviewItem } from '$lib/portfolio';
	import {
		fetchOrders,
		placeOrder,
		cancelOrder,
		cancelOrders,
		syncAccountOrders,
		searchSymbols,
		OPEN_STATES,
		type Order,
		type SymbolInfo
	} from '$lib/orders';

	let overview: OverviewItem[] = [];
	let orders: Order[] = [];
	let loading = true;
	let error = '';

	// Ticket state
	let accountId: number | null = null;
	let symbolQuery = '';
	let symbolResults: SymbolInfo[] = [];
	let selectedSymbol: SymbolInfo | null = null;
	let side: 'BUY' | 'SELL' = 'BUY';
	let otype: 'LIMIT' | 'MARKET' = 'LIMIT';
	let qtyInput = '';
	let priceInput = '';
	let placing = false;
	let placeMsg = '';

	// Open-orders list state
	let openOnly = true;
	let selected = new Set<number>();
	let busy = false;

	$: account = overview.find((o) => o.account_id === accountId) ?? null;
	$: accountLabel = (id: number) => {
		const a = overview.find((o) => o.account_id === id);
		return a ? `${a.exchange} · ${a.label}` : `#${id}`;
	};
	$: validation = validate(selectedSymbol, otype, qtyInput, priceInput);

	interface Validation {
		ok: boolean;
		errors: string[];
		notional: number | null;
	}

	function stepDecimals(step: string): number {
		const i = step.indexOf('.');
		return i < 0 ? 0 : step.length - i - 1;
	}

	function validate(
		sym: SymbolInfo | null,
		type: string,
		qtyRaw: string,
		priceRaw: string
	): Validation {
		const errors: string[] = [];
		if (!sym) return { ok: false, errors: ['Select an instrument'], notional: null };
		const qty = Number(qtyRaw);
		if (!qtyRaw || !Number.isFinite(qty) || qty <= 0) errors.push('Enter a positive quantity');
		const minQty = Number(sym.min_qty);
		if (qty > 0 && qty < minQty) errors.push(`Below min quantity ${sym.min_qty}`);
		const maxQty = sym.max_qty !== null ? Number(sym.max_qty) : null;
		if (maxQty !== null && qty > maxQty) errors.push(`Above max quantity ${sym.max_qty}`);

		let price: number | null = null;
		if (type === 'LIMIT') {
			price = Number(priceRaw);
			if (!priceRaw || !Number.isFinite(price) || price <= 0) errors.push('Enter a positive price');
		}
		const ref = price ?? (sym.last_price !== null ? Number(sym.last_price) : null);
		const notional = ref !== null && qty > 0 ? ref * qty : null;
		if (sym.min_notional !== null && notional !== null && notional < Number(sym.min_notional))
			errors.push(`Below min notional ${sym.min_notional}`);
		return { ok: errors.length === 0, errors, notional };
	}

	function money(v: string | number | null): string {
		if (v === null) return '—';
		const n = Number(v);
		if (!Number.isFinite(n)) return '—';
		return n.toLocaleString(undefined, { maximumFractionDigits: 8 });
	}

	function ago(iso: string | null): string {
		if (!iso) return '—';
		const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
		if (mins < 1) return 'just now';
		if (mins < 60) return `${mins}m ago`;
		const hrs = Math.round(mins / 60);
		if (hrs < 24) return `${hrs}h ago`;
		return `${Math.round(hrs / 24)}d ago`;
	}

	const isOpen = (o: Order) => OPEN_STATES.includes(o.status);

	async function loadAll() {
		loading = true;
		error = '';
		try {
			[overview, orders] = await Promise.all([fetchOverview(), loadOrders()]);
			if (accountId === null && overview.length) accountId = overview[0].account_id;
		} catch (e) {
			error =
				e instanceof ApiError && e.status === 401
					? 'Please log in to manage orders.'
					: 'Failed to load orders.';
		} finally {
			loading = false;
		}
	}

	async function loadOrders(): Promise<Order[]> {
		const params: Record<string, string> = {};
		if (openOnly) params.open_only = 'true';
		return fetchOrders(params);
	}

	async function refreshOrders() {
		try {
			orders = await loadOrders();
			selected = new Set([...selected].filter((id) => orders.some((o) => o.id === id)));
		} catch {
			/* keep previous list on transient errors */
		}
	}

	let searchTimer: ReturnType<typeof setTimeout> | undefined;
	function onSymbolInput() {
		selectedSymbol = null;
		clearTimeout(searchTimer);
		const q = symbolQuery.trim();
		if (!account || q.length < 2) {
			symbolResults = [];
			return;
		}
		searchTimer = setTimeout(async () => {
			try {
				const res = await searchSymbols(account!.exchange, q);
				symbolResults = res.items;
			} catch {
				symbolResults = [];
			}
		}, 200);
	}

	function pickSymbol(s: SymbolInfo) {
		selectedSymbol = s;
		symbolQuery = s.universal_symbol;
		symbolResults = [];
	}

	async function submit() {
		if (accountId === null || !selectedSymbol || !validation.ok) return;
		placing = true;
		placeMsg = '';
		try {
			const order = await placeOrder({
				account_id: accountId,
				universal_symbol: selectedSymbol.universal_symbol,
				side,
				type: otype,
				qty: qtyInput,
				price: otype === 'LIMIT' ? priceInput : null
			});
			placeMsg = `Placed ${order.side} ${order.qty} ${order.universal_symbol} — ${order.status}`;
			qtyInput = '';
			priceInput = '';
			await refreshOrders();
		} catch (e) {
			placeMsg = e instanceof ApiError ? e.message : 'Order failed.';
		} finally {
			placing = false;
		}
	}

	async function cancelOne(o: Order) {
		busy = true;
		try {
			await cancelOrder(o.id);
			await refreshOrders();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Cancel failed.';
		} finally {
			busy = false;
		}
	}

	async function cancelSelected() {
		if (selected.size === 0) return;
		busy = true;
		try {
			await cancelOrders([...selected]);
			selected = new Set();
			await refreshOrders();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Bulk cancel failed.';
		} finally {
			busy = false;
		}
	}

	async function syncNow() {
		if (accountId === null) return;
		busy = true;
		try {
			await syncAccountOrders(accountId);
			await refreshOrders();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Sync failed.';
		} finally {
			busy = false;
		}
	}

	function toggle(id: number) {
		const next = new Set(selected);
		next.has(id) ? next.delete(id) : next.add(id);
		selected = next;
	}

	$: cancellable = orders.filter(isOpen);

	onMount(loadAll);
</script>

<svelte:head><title>Orders · CPanel</title></svelte:head>

<div class="page">
	<div class="page-head">
		<h1>Orders</h1>
		<button class="btn secondary" on:click={syncNow} disabled={busy || accountId === null}>
			Sync open orders
		</button>
	</div>

	{#if error}
		<div class="notice">{error}</div>
	{:else if loading}
		<div class="notice">Loading…</div>
	{:else}
		<div class="layout">
			<!-- Order ticket -->
			<section class="ticket">
				<h2>New order</h2>

				<label class="field">
					<span>Account</span>
					<select bind:value={accountId}>
						{#each overview as a}
							<option value={a.account_id}>{a.exchange} · {a.label}</option>
						{/each}
					</select>
				</label>

				<label class="field">
					<span>Instrument</span>
					<input
						type="text"
						placeholder="Search e.g. BTCUSDT"
						bind:value={symbolQuery}
						on:input={onSymbolInput}
						autocomplete="off"
					/>
				</label>
				{#if symbolResults.length}
					<ul class="results">
						{#each symbolResults as s}
							<li>
								<button type="button" on:click={() => pickSymbol(s)}>
									<span class="mono">{s.universal_symbol}</span>
									<span class="dim">{s.market_type}</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}

				{#if selectedSymbol}
					<div class="filters">
						<span>tick {selectedSymbol.price_tick}</span>
						<span>step {selectedSymbol.qty_step}</span>
						<span>min {selectedSymbol.min_qty}</span>
						{#if selectedSymbol.min_notional}<span>min notional {selectedSymbol.min_notional}</span>{/if}
						{#if selectedSymbol.last_price}<span>last {selectedSymbol.last_price}</span>{/if}
					</div>
				{/if}

				<div class="row">
					<div class="seg">
						<button class:active={side === 'BUY'} class="buy" on:click={() => (side = 'BUY')}>Buy</button>
						<button class:active={side === 'SELL'} class="sell" on:click={() => (side = 'SELL')}>Sell</button>
					</div>
					<div class="seg">
						<button class:active={otype === 'LIMIT'} on:click={() => (otype = 'LIMIT')}>Limit</button>
						<button class:active={otype === 'MARKET'} on:click={() => (otype = 'MARKET')}>Market</button>
					</div>
				</div>

				<div class="row">
					<label class="field">
						<span>Quantity</span>
						<input type="text" inputmode="decimal" bind:value={qtyInput} placeholder="0.0" />
					</label>
					<label class="field">
						<span>Price</span>
						<input
							type="text"
							inputmode="decimal"
							bind:value={priceInput}
							placeholder={otype === 'MARKET' ? 'market' : '0.0'}
							disabled={otype === 'MARKET'}
						/>
					</label>
				</div>

				{#if validation.notional !== null}
					<div class="notional">≈ {money(validation.notional)} notional</div>
				{/if}
				{#if selectedSymbol && !validation.ok}
					<ul class="errors">
						{#each validation.errors as e}<li>{e}</li>{/each}
					</ul>
				{/if}

				<button
					class="btn place {side === 'SELL' ? 'sell' : 'buy'}"
					on:click={submit}
					disabled={placing || !validation.ok || accountId === null}
				>
					{placing ? 'Placing…' : `${side === 'BUY' ? 'Buy' : 'Sell'} ${selectedSymbol?.base_asset ?? ''}`}
				</button>
				{#if placeMsg}<div class="place-msg">{placeMsg}</div>{/if}
			</section>

			<!-- Open orders -->
			<section class="orders">
				<div class="orders-head">
					<h2>{openOnly ? 'Open orders' : 'Recent orders'}</h2>
					<div class="orders-actions">
						<label class="toggle">
							<input
								type="checkbox"
								bind:checked={openOnly}
								on:change={refreshOrders}
							/>
							Open only
						</label>
						<button
							class="btn secondary danger"
							on:click={cancelSelected}
							disabled={busy || selected.size === 0}
						>
							Cancel selected ({selected.size})
						</button>
					</div>
				</div>

				{#if orders.length === 0}
					<div class="notice">No orders to show.</div>
				{:else}
					<div class="table-wrap">
						<table>
							<thead>
								<tr>
									<th></th>
									<th>Account</th>
									<th>Symbol</th>
									<th>Side</th>
									<th>Type</th>
									<th>Status</th>
									<th class="right">Qty</th>
									<th class="right">Filled</th>
									<th class="right">Price</th>
									<th>Age</th>
									<th></th>
								</tr>
							</thead>
							<tbody>
								{#each orders as o}
									<tr class:closed={!isOpen(o)}>
										<td>
											{#if isOpen(o)}
												<input
													type="checkbox"
													checked={selected.has(o.id)}
													on:change={() => toggle(o.id)}
												/>
											{/if}
										</td>
										<td class="dim">{accountLabel(o.account_id)}</td>
										<td class="mono strong">
											{o.universal_symbol}
											{#if o.external}<span class="tag">ext</span>{/if}
										</td>
										<td><span class="side {o.side.toLowerCase()}">{o.side}</span></td>
										<td>{o.type}</td>
										<td><span class="status s-{o.status.toLowerCase()}">{o.status}</span></td>
										<td class="mono right">{money(o.qty)}</td>
										<td class="mono right">{money(o.filled_qty)}</td>
										<td class="mono right">{money(o.price)}</td>
										<td class="dim">{ago(o.created_at)}</td>
										<td>
											{#if isOpen(o)}
												<button class="link-btn" on:click={() => cancelOne(o)} disabled={busy}>
													Cancel
												</button>
											{/if}
										</td>
									</tr>
								{/each}
							</tbody>
						</table>
					</div>
				{/if}
			</section>
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
		align-items: center;
		justify-content: space-between;
		margin-bottom: 1.25rem;
	}
	h1 {
		margin: 0;
		font-size: 1.5rem;
	}
	h2 {
		margin: 0 0 0.9rem;
		font-size: 1rem;
	}

	.layout {
		display: grid;
		grid-template-columns: 22rem 1fr;
		gap: 1.25rem;
		align-items: start;
	}
	@media (max-width: 60rem) {
		.layout {
			grid-template-columns: 1fr;
		}
	}

	.ticket,
	.orders,
	.table-wrap,
	.notice {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
	}
	.ticket {
		padding: 1.1rem 1.25rem 1.25rem;
	}
	.orders {
		padding: 1.1rem 1.25rem 1.25rem;
		min-width: 0;
	}

	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin-bottom: 0.75rem;
		flex: 1;
	}
	.field span {
		font-size: 0.6875rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
	}
	input[type='text'],
	select {
		height: 2.2rem;
		padding: 0 0.6rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-xs);
		background: var(--white, #fff);
		font-family: inherit;
		font-size: 0.875rem;
		color: var(--text);
		box-sizing: border-box;
	}
	input:disabled {
		opacity: 0.5;
	}

	.results {
		list-style: none;
		margin: -0.5rem 0 0.75rem;
		padding: 0;
		border: 1px solid var(--border);
		border-radius: var(--radius-xs);
		max-height: 12rem;
		overflow-y: auto;
	}
	.results li + li {
		border-top: 1px solid var(--border-light);
	}
	.results button {
		display: flex;
		justify-content: space-between;
		width: 100%;
		padding: 0.4rem 0.6rem;
		background: none;
		border: none;
		cursor: pointer;
		font-family: inherit;
		font-size: 0.8125rem;
		color: var(--text);
		text-align: left;
	}
	.results button:hover {
		background: var(--border-light);
	}

	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		margin-bottom: 0.85rem;
		font-size: 0.6875rem;
		color: var(--text-dim);
		font-family: var(--mono);
	}

	.row {
		display: flex;
		gap: 0.6rem;
	}
	.seg {
		display: flex;
		flex: 1;
		margin-bottom: 0.75rem;
		border: 1px solid var(--border);
		border-radius: var(--radius-xs);
		overflow: hidden;
	}
	.seg button {
		flex: 1;
		height: 2.2rem;
		border: none;
		background: none;
		font-family: inherit;
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--text-muted);
		cursor: pointer;
	}
	.seg button + button {
		border-left: 1px solid var(--border);
	}
	.seg button.active {
		background: var(--accent);
		color: var(--text);
	}
	.seg button.buy.active {
		background: #d5f2e0;
		color: #157347;
	}
	.seg button.sell.active {
		background: #f7d9dd;
		color: #b02a37;
	}

	.notional {
		font-family: var(--mono);
		font-size: 0.8125rem;
		color: var(--text-muted);
		margin-bottom: 0.5rem;
	}
	.errors {
		margin: 0 0 0.75rem;
		padding-left: 1.1rem;
		color: #b02a37;
		font-size: 0.75rem;
	}

	.btn {
		height: 2.3rem;
		padding: 0 1rem;
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
		opacity: 0.55;
		cursor: default;
	}
	.btn.secondary {
		background: var(--surface);
	}
	.btn.danger {
		color: #b02a37;
	}
	.btn.place {
		width: 100%;
		margin-top: 0.25rem;
	}
	.btn.place.buy {
		background: #157347;
		color: #fff;
		border-color: #157347;
	}
	.btn.place.sell {
		background: #b02a37;
		color: #fff;
		border-color: #b02a37;
	}
	.place-msg {
		margin-top: 0.6rem;
		font-size: 0.75rem;
		color: var(--text-muted);
	}

	.orders-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 0.9rem;
	}
	.orders-head h2 {
		margin: 0;
	}
	.orders-actions {
		display: flex;
		align-items: center;
		gap: 0.9rem;
	}
	.toggle {
		display: flex;
		align-items: center;
		gap: 0.35rem;
		font-size: 0.8125rem;
		color: var(--text-muted);
	}

	.table-wrap {
		overflow-x: auto;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8125rem;
	}
	th,
	td {
		padding: 0.55rem 0.7rem;
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
	tr.closed {
		opacity: 0.6;
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
	.dim {
		color: var(--text-muted);
	}
	.side {
		font-weight: 700;
	}
	.side.buy {
		color: #157347;
	}
	.side.sell {
		color: #b02a37;
	}
	.status {
		font-size: 0.6875rem;
		font-weight: 700;
		padding: 0.1rem 0.4rem;
		border-radius: 999px;
		background: var(--border-light);
		color: var(--text-muted);
	}
	.status.s-open,
	.status.s-partially_filled {
		background: #dbeafe;
		color: #1e40af;
	}
	.status.s-filled {
		background: #d5f2e0;
		color: #157347;
	}
	.status.s-rejected {
		background: #f7d9dd;
		color: #b02a37;
	}
	.tag {
		font-size: 0.625rem;
		font-weight: 700;
		color: var(--text-dim);
		border: 1px solid var(--border);
		border-radius: 4px;
		padding: 0 0.25rem;
		margin-left: 0.3rem;
	}
	.link-btn {
		background: none;
		border: none;
		color: #b02a37;
		font-family: inherit;
		font-size: 0.8125rem;
		font-weight: 600;
		cursor: pointer;
		padding: 0;
	}
	.link-btn:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.notice {
		padding: 2rem;
		text-align: center;
		color: var(--text-muted);
	}
</style>
