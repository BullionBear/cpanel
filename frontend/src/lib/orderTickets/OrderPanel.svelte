<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { ApiError } from '$lib/api';
	import { placeOrder, validateLeg, type SymbolInfo } from '$lib/orders';
	import type { PanelSpec, PosMode } from './registry';

	export let exchange: string;
	export let label: string;
	export let spec: PanelSpec;
	export let symbol: SymbolInfo;
	export let accountId: number;

	const dispatch = createEventDispatcher<{ placed: void }>();

	let side: 'BUY' | 'SELL' = 'BUY';
	let otype: 'LIMIT' | 'MARKET' = spec.orderTypes[0];
	let qtyInput = '';
	let priceInput = '';
	let tif: string = spec.tif[0] ?? 'GTC';
	let reduceOnly = false;
	let positionSide: PosMode = 'BOTH';
	let placing = false;
	let msg = '';

	const POS_LABEL: Record<PosMode, string> = { BOTH: 'One-way', LONG: 'Long', SHORT: 'Short' };

	$: validation = validateLeg(symbol, otype, qtyInput, priceInput);
	$: qtyUnit = spec.contracts ? 'cont' : symbol.base_asset;

	function money(v: number | null): string {
		if (v === null) return '—';
		return v.toLocaleString(undefined, { maximumFractionDigits: 8 });
	}

	async function submit() {
		if (!validation.ok || placing) return;
		placing = true;
		msg = '';
		try {
			const order = await placeOrder({
				account_id: accountId,
				universal_symbol: symbol.universal_symbol,
				side,
				type: otype,
				qty: qtyInput,
				price: otype === 'LIMIT' ? priceInput : null,
				time_in_force: spec.tif.length && otype === 'LIMIT' ? tif : null,
				reduce_only: spec.reduceOnly ? reduceOnly : false,
				position_side: spec.positionModes.length ? positionSide : null
			});
			msg = `Placed ${order.side} ${order.qty} ${order.universal_symbol} — ${order.status}`;
			qtyInput = '';
			priceInput = '';
			dispatch('placed');
		} catch (e) {
			msg = e instanceof ApiError ? e.message : 'Order failed.';
		} finally {
			placing = false;
		}
	}
</script>

<div class="panel">
	<div class="badge">{exchange} · {label}</div>

	<div class="row">
		<div class="seg">
			<button class:active={side === 'BUY'} class="buy" on:click={() => (side = 'BUY')}>Buy</button>
			<button class:active={side === 'SELL'} class="sell" on:click={() => (side = 'SELL')}>Sell</button>
		</div>
		<div class="seg">
			{#each spec.orderTypes as t}
				<button class:active={otype === t} on:click={() => (otype = t)}>
					{t === 'LIMIT' ? 'Limit' : 'Market'}
				</button>
			{/each}
		</div>
	</div>

	<div class="row">
		<label class="field">
			<span>Quantity ({qtyUnit})</span>
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

	{#if spec.tif.length && otype === 'LIMIT'}
		<label class="field">
			<span>Time in force</span>
			<select bind:value={tif}>
				{#each spec.tif as t}<option value={t}>{t === 'POST_ONLY' ? 'Post-only' : t}</option>{/each}
			</select>
		</label>
	{/if}

	{#if spec.positionModes.length}
		<label class="field">
			<span>Position</span>
			<select bind:value={positionSide}>
				{#each spec.positionModes as p}<option value={p}>{POS_LABEL[p]}</option>{/each}
			</select>
		</label>
	{/if}

	{#if spec.reduceOnly}
		<label class="check">
			<input type="checkbox" bind:checked={reduceOnly} />
			Reduce-only
		</label>
	{/if}

	{#if validation.notional !== null}
		<div class="notional">≈ {money(validation.notional)} notional</div>
	{/if}
	{#if !validation.ok}
		<ul class="errors">
			{#each validation.errors as e}<li>{e}</li>{/each}
		</ul>
	{/if}

	{#if spec.hint}<p class="hint">{spec.hint}</p>{/if}

	<button
		class="btn place {side === 'SELL' ? 'sell' : 'buy'}"
		on:click={submit}
		disabled={placing || !validation.ok}
	>
		{placing ? 'Placing…' : `${side === 'BUY' ? 'Buy' : 'Sell'} ${spec.contracts ? '' : symbol.base_asset}`}
	</button>
	{#if msg}<div class="place-msg">{msg}</div>{/if}
</div>

<style>
	.badge {
		display: inline-block;
		font-size: 0.6875rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
		border: 1px solid var(--border);
		border-radius: 999px;
		padding: 0.15rem 0.55rem;
		margin-bottom: 0.85rem;
	}
	.field {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		margin-bottom: 0.75rem;
		flex: 1;
		min-width: 0;
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
		width: 100%;
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
	.check {
		display: flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.8125rem;
		color: var(--text-muted);
		margin-bottom: 0.75rem;
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
	.hint {
		margin: 0 0 0.75rem;
		font-size: 0.6875rem;
		color: var(--text-dim);
		line-height: 1.4;
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
	.btn:disabled {
		opacity: 0.55;
		cursor: default;
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
</style>
