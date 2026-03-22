<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getAccountsV2,
		getPositions,
		type AccountV2,
		type PositionItem,
		type PositionResponse
	} from '$lib/api';

	let accounts: AccountV2[] = [];
	let positionData: PositionResponse[] = [];
	let loading = true;
	let refreshing = false;
	let error: string | null = null;

	onMount(() => {
		loadPositions();
	});

	async function loadPositions() {
		loading = true;
		error = null;
		try {
			accounts = await getAccountsV2();
			const acctIds = accounts.map((a) => a.id);
			positionData = acctIds.length > 0 ? await getPositions(acctIds) : [];
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load positions';
		} finally {
			loading = false;
		}
	}

	async function refresh() {
		refreshing = true;
		error = null;
		try {
			const acctIds = accounts.map((a) => a.id);
			positionData = acctIds.length > 0 ? await getPositions(acctIds) : [];
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to refresh positions';
		} finally {
			refreshing = false;
		}
	}

	$: allPositions = positionData.flatMap((r) =>
		r.positions.map((p) => ({
			...p,
			acct_id: r.acct_id,
			account_name: r.account_name,
			exchange: r.exchange
		}))
	);

	type FlatPosition = PositionItem & { acct_id: number; account_name: string; exchange: string };

	function pnlClass(pnl: string): string {
		const v = parseFloat(pnl);
		if (v > 0) return 'positive';
		if (v < 0) return 'negative';
		return '';
	}

	function formatPnl(pnl: string): string {
		const v = parseFloat(pnl);
		if (isNaN(v)) return pnl;
		const sign = v >= 0 ? '+' : '';
		return sign + v.toFixed(2);
	}

	function formatPrice(price: string): string {
		const v = parseFloat(price);
		if (isNaN(v)) return price;
		if (v === 0) return '--';
		if (v >= 1000) return v.toFixed(2);
		if (v >= 1) return v.toFixed(4);
		return v.toFixed(6);
	}

	function formatSize(size: string): string {
		const v = parseFloat(size);
		if (isNaN(v)) return size;
		return v.toString();
	}

	function exchangeColorClass(exchange: string): string {
		const e = exchange.toUpperCase();
		if (e === 'BINANCE') return 'exch-binance';
		if (e === 'BYBIT') return 'exch-bybit';
		if (e === 'BITGET') return 'exch-bitget';
		return '';
	}

	function timeSince(ms: number): string {
		if (!ms) return '';
		const seconds = Math.floor((Date.now() - ms) / 1000);
		if (seconds < 60) return `${seconds}s ago`;
		const minutes = Math.floor(seconds / 60);
		if (minutes < 60) return `${minutes}m ago`;
		const hours = Math.floor(minutes / 60);
		if (hours < 24) return `${hours}h ago`;
		const days = Math.floor(hours / 24);
		return `${days}d ago`;
	}
</script>

<div class="position-page">
	<div class="dashboard-header">
		<div class="header-left">
			<h1>Positions</h1>
			<span class="position-count">{allPositions.length}</span>
		</div>
		<button
			class="refresh-btn"
			on:click={refresh}
			disabled={refreshing}
		>
			<svg class:spinning={refreshing} width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M1 4V10H7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M23 20V14H17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10M23 14L18.36 18.36A9 9 0 0 1 3.51 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			{refreshing ? 'Refreshing...' : 'Refresh'}
		</button>
	</div>

	{#if loading}
		<div class="loading">
			<div class="spinner"></div>
			<p>Loading positions...</p>
		</div>
	{:else if error}
		<div class="error">
			<p>{error}</p>
			<button class="retry-btn" on:click={loadPositions}>Retry</button>
		</div>
	{:else if allPositions.length === 0}
		<div class="empty-state">
			<svg width="48" height="48" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="#9ca3af" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M2 17L12 22L22 17" stroke="#9ca3af" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M2 12L12 17L22 12" stroke="#9ca3af" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			<p>No open positions</p>
			<span class="empty-hint">Futures positions from connected accounts will appear here.</span>
		</div>
	{:else}
		<div class="positions-grid">
			{#each allPositions as pos (pos.symbol + pos.acct_id + pos.side)}
				<div class="position-card">
					<div class="card-top">
						<div class="symbol-row">
							<span class="symbol">{pos.symbol}</span>
							<span class="side-badge" class:long={pos.side === 'LONG'} class:short={pos.side === 'SHORT'}>
								{pos.side}
							</span>
						</div>
						<div class="leverage-row">
							<span class="leverage-badge">{pos.leverage}x</span>
							<span class="margin-type">{pos.margin_type}</span>
						</div>
					</div>

					<div class="pnl-section {pnlClass(pos.unrealized_pnl)}">
						<span class="pnl-label">Unrealized PnL</span>
						<span class="pnl-value">{formatPnl(pos.unrealized_pnl)} USDT</span>
					</div>

					<div class="details-grid">
						<div class="detail">
							<span class="detail-label">Size</span>
							<span class="detail-value">{formatSize(pos.size)}</span>
						</div>
						<div class="detail">
							<span class="detail-label">Notional</span>
							<span class="detail-value">{formatPrice(pos.notional)}</span>
						</div>
						<div class="detail">
							<span class="detail-label">Entry Price</span>
							<span class="detail-value">{formatPrice(pos.entry_price)}</span>
						</div>
						<div class="detail">
							<span class="detail-label">Mark Price</span>
							<span class="detail-value">{formatPrice(pos.mark_price)}</span>
						</div>
						<div class="detail">
							<span class="detail-label">Liq. Price</span>
							<span class="detail-value liq">{formatPrice(pos.liq_price)}</span>
						</div>
						<div class="detail">
							<span class="detail-label">Updated</span>
							<span class="detail-value">{timeSince(pos.update_time_ms)}</span>
						</div>
					</div>

					<div class="card-footer">
						<span class="account-name">{pos.account_name}</span>
						<span class="exchange-badge {exchangeColorClass(pos.exchange)}">{pos.exchange}</span>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.position-page {
		padding: 24px;
		max-width: 1400px;
		margin: 0 auto;
	}

	.dashboard-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 24px;
	}

	.header-left {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.dashboard-header h1 {
		font-size: 24px;
		font-weight: 700;
		color: #111827;
		margin: 0;
	}

	.position-count {
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
		font-size: 13px;
		font-weight: 600;
		padding: 2px 10px;
		border-radius: 12px;
	}

	.refresh-btn {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 8px 16px;
		background: white;
		color: #374151;
		border: 1px solid #d1d5db;
		border-radius: 8px;
		font-size: 14px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
	}

	.refresh-btn:hover:not(:disabled) {
		background: #f9fafb;
		border-color: #9ca3af;
	}

	.refresh-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.refresh-btn svg {
		flex-shrink: 0;
	}

	@keyframes spin {
		from { transform: rotate(0deg); }
		to { transform: rotate(360deg); }
	}

	.spinning {
		animation: spin 1s linear infinite;
	}

	/* Loading */
	.loading {
		display: flex;
		flex-direction: column;
		align-items: center;
		padding: 60px 20px;
		color: #6b7280;
	}

	.spinner {
		width: 32px;
		height: 32px;
		border: 3px solid #e5e7eb;
		border-top-color: #667eea;
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
		margin-bottom: 12px;
	}

	/* Error */
	.error {
		text-align: center;
		padding: 40px;
		color: #ef4444;
		background: #fef2f2;
		border: 1px solid #fecaca;
		border-radius: 12px;
	}

	.retry-btn {
		margin-top: 12px;
		padding: 8px 20px;
		background: white;
		color: #ef4444;
		border: 1px solid #fecaca;
		border-radius: 6px;
		cursor: pointer;
		font-weight: 500;
		transition: all 0.2s;
	}

	.retry-btn:hover {
		background: #fef2f2;
	}

	/* Empty */
	.empty-state {
		text-align: center;
		padding: 60px 20px;
		background: white;
		border-radius: 12px;
		border: 2px dashed #e5e7eb;
	}

	.empty-state p {
		margin: 16px 0 4px;
		color: #374151;
		font-size: 16px;
		font-weight: 600;
	}

	.empty-hint {
		color: #9ca3af;
		font-size: 14px;
	}

	/* Grid */
	.positions-grid {
		display: grid;
		grid-template-columns: repeat(3, 1fr);
		gap: 16px;
	}

	@media (max-width: 1200px) {
		.positions-grid {
			grid-template-columns: repeat(2, 1fr);
		}
	}

	@media (max-width: 768px) {
		.positions-grid {
			grid-template-columns: 1fr;
		}

		.position-page {
			padding: 16px;
		}
	}

	/* Card */
	.position-card {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		overflow: hidden;
		transition: box-shadow 0.2s, transform 0.2s;
	}

	.position-card:hover {
		box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
		transform: translateY(-2px);
	}

	.card-top {
		padding: 16px 16px 12px;
		display: flex;
		justify-content: space-between;
		align-items: flex-start;
	}

	.symbol-row {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.symbol {
		font-size: 17px;
		font-weight: 700;
		color: #111827;
		letter-spacing: -0.01em;
	}

	.side-badge {
		font-size: 11px;
		font-weight: 700;
		padding: 2px 8px;
		border-radius: 4px;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.side-badge.long {
		background: #dcfce7;
		color: #166534;
	}

	.side-badge.short {
		background: #fef2f2;
		color: #991b1b;
	}

	.leverage-row {
		display: flex;
		align-items: center;
		gap: 6px;
	}

	.leverage-badge {
		font-size: 11px;
		font-weight: 700;
		padding: 2px 6px;
		border-radius: 4px;
		background: #fef3c7;
		color: #92400e;
	}

	.margin-type {
		font-size: 11px;
		color: #6b7280;
		text-transform: capitalize;
	}

	/* PnL */
	.pnl-section {
		padding: 12px 16px;
		margin: 0 16px;
		border-radius: 8px;
		background: #f9fafb;
		display: flex;
		justify-content: space-between;
		align-items: center;
	}

	.pnl-section.positive {
		background: #f0fdf4;
	}

	.pnl-section.negative {
		background: #fef2f2;
	}

	.pnl-label {
		font-size: 12px;
		color: #6b7280;
		font-weight: 500;
	}

	.pnl-value {
		font-size: 15px;
		font-weight: 700;
		color: #6b7280;
	}

	.pnl-section.positive .pnl-value {
		color: #166534;
	}

	.pnl-section.negative .pnl-value {
		color: #991b1b;
	}

	/* Details grid */
	.details-grid {
		display: grid;
		grid-template-columns: 1fr 1fr;
		gap: 0;
		padding: 12px 16px 8px;
	}

	.detail {
		padding: 6px 0;
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.detail-label {
		font-size: 11px;
		color: #9ca3af;
		font-weight: 500;
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}

	.detail-value {
		font-size: 14px;
		font-weight: 600;
		color: #1f2937;
		font-variant-numeric: tabular-nums;
	}

	.detail-value.liq {
		color: #dc2626;
	}

	/* Footer */
	.card-footer {
		padding: 10px 16px;
		border-top: 1px solid #f3f4f6;
		display: flex;
		justify-content: space-between;
		align-items: center;
	}

	.account-name {
		font-size: 12px;
		color: #6b7280;
		font-weight: 500;
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
		max-width: 60%;
	}

	.exchange-badge {
		font-size: 11px;
		font-weight: 600;
		padding: 2px 8px;
		border-radius: 4px;
		background: #f3f4f6;
		color: #374151;
	}

	.exchange-badge.exch-binance {
		background: #fef9ee;
		color: #a16207;
	}

	.exchange-badge.exch-bybit {
		background: #fff7ed;
		color: #c2410c;
	}

	.exchange-badge.exch-bitget {
		background: #ecfdf5;
		color: #047857;
	}
</style>
