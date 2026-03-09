<script lang="ts">
	import { onMount } from 'svelte';
	import { getOpenOrders, getSymbols, getApiKeysV2, cancelOrder, type OpenOrder, type SymbolInfo, type APIKeyV2 } from '$lib/api';

	let orders: OpenOrder[] = [];
	let symbols: SymbolInfo[] = [];
	let accounts: APIKeyV2[] = [];
	let loading = true;
	let error: string | null = null;
	let cancelingOrderId: string | null = null;
	let expandedOrderId: string | null = null;

	function toggleDetail(orderId: string) {
		expandedOrderId = expandedOrderId === orderId ? null : orderId;
	}

	async function loadData() {
		try {
			loading = true;
			error = null;

			const [ordersData, symbolsData, accountsData] = await Promise.all([
				getOpenOrders(),
				getSymbols(200),
				getApiKeysV2()
			]);

			orders = ordersData;
			symbols = symbolsData;
			accounts = accountsData;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load orders';
		} finally {
			loading = false;
		}
	}

	function getSymbolId(symbolName: string, accountId: number): number | null {
		// Find the account to get exchange
		const account = accounts.find(a => a.id === accountId);
		if (!account) return null;

		// Find symbol matching name and exchange
		const symbol = symbols.find(s => 
			s.name === symbolName && 
			s.exchange.slug.toUpperCase() === account.exchange.toUpperCase()
		);
		return symbol?.id ?? null;
	}

	function getExchange(accountId: number): string {
		const account = accounts.find(a => a.id === accountId);
		return account?.exchange ?? '';
	}

	function getExchangeColor(exchange: string): string {
		const colors: Record<string, string> = {
			'BINANCE': '#f0b90b',
			'BYBIT': '#f7a600',
			'OKX': '#000000',
			'BITGET': '#00c8b4',
		};
		return colors[exchange.toUpperCase()] || '#667eea';
	}

	function formatDateTime(timestamp: number): string {
		if (!timestamp) return '-';
		const date = new Date(timestamp * 1000);
		return date.toLocaleString('en-US', {
			month: 'short',
			day: 'numeric',
			hour: '2-digit',
			minute: '2-digit',
			second: '2-digit',
			hour12: false
		});
	}

	async function handleCancel(order: OpenOrder) {
		const symbolId = getSymbolId(order.symbol, order.account_id);
		if (!symbolId) {
			error = `Cannot find symbol ID for ${order.symbol}`;
			return;
		}

		if (!confirm(`Cancel order ${order.order_id}?`)) {
			return;
		}

		try {
			cancelingOrderId = order.order_id;
			error = null;

			await cancelOrder({
				account_id: order.account_id,
				symbol_id: symbolId,
				order_id: order.order_id
			});

			// Refresh orders after cancel
			await loadData();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to cancel order';
		} finally {
			cancelingOrderId = null;
		}
	}

	onMount(() => {
		loadData();
	});
</script>

<div class="open-orders-panel">
	<div class="panel-header">
		<h3 class="panel-title">Open Orders</h3>
		<button class="refresh-btn" on:click={loadData} disabled={loading} title="Refresh">
			<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" class:spinning={loading}>
				<path d="M23 4V10H17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M1 20V14H7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M3.51 9.00001C4.01717 7.56679 4.87913 6.2854 6.01547 5.27543C7.1518 4.26545 8.52547 3.55977 10.0083 3.22427C11.4911 2.88877 13.0348 2.93436 14.4952 3.35679C15.9556 3.77921 17.2853 4.56472 18.36 5.64001L23 10M1 14L5.64 18.36C6.71475 19.4353 8.04437 20.2208 9.50481 20.6432C10.9652 21.0657 12.5089 21.1112 13.9917 20.7757C15.4745 20.4402 16.8482 19.7346 17.9845 18.7246C19.1209 17.7146 19.9828 16.4332 20.49 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
		</button>
	</div>

	{#if error}
		<div class="error">{error}</div>
	{/if}

	{#if loading}
		<div class="loading">Loading orders...</div>
	{:else if orders.length === 0}
		<div class="empty">No open orders</div>
	{:else}
		<div class="table-container">
			<table class="orders-table">
				<thead>
					<tr>
						<th>Account</th>
						<th>Symbol</th>
						<th>Side</th>
						<th>Price</th>
						<th>Size</th>
						<th>Created</th>
						<th>Detail</th>
						<th>Cancel</th>
					</tr>
				</thead>
				<tbody>
					{#each orders as order}
						<tr>
							<td>
								<div class="account-cell">
									<span class="account-name">{order.account_name}</span>
									<span class="exchange-badge" style="background-color: {getExchangeColor(getExchange(order.account_id))};">
										{getExchange(order.account_id)}
									</span>
								</div>
							</td>
							<td class="symbol">{order.symbol}</td>
							<td>
								<span class="side-badge" class:buy={order.side === 'BUY'} class:sell={order.side === 'SELL'}>
									{order.side}
								</span>
							</td>
							<td class="price">{order.price}</td>
							<td class="size">{order.quantity}</td>
							<td class="time">{formatDateTime(order.created_at)}</td>
						<td>
								<button
									class="detail-btn"
									class:active={expandedOrderId === order.order_id}
									on:click={() => toggleDetail(order.order_id)}
									title="Order details"
								>
									Detail
								</button>
						</td>
						<td>
								<button 
									class="cancel-btn" 
									on:click={() => handleCancel(order)}
									disabled={cancelingOrderId === order.order_id}
									title="Cancel order"
								>
									{cancelingOrderId === order.order_id ? '...' : '✕'}
								</button>
						</td>
					</tr>
					{#if expandedOrderId === order.order_id}
						<tr class="detail-row">
							<td colspan="3">
								<div class="detail-label">Order ID</div>
								<div class="detail-value mono">{order.order_id}</div>
							</td>
							<td colspan="3">
								<div class="detail-label">Client Order ID</div>
								<div class="detail-value mono">{order.client_order_id}</div>
							</td>
							<td colspan="2">
								<div class="detail-label">Type</div>
								<div class="detail-value">{order.type}</div>
							</td>
						</tr>
					{/if}
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<style>
	.open-orders-panel {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		padding: 20px;
		width: 100%;
		box-sizing: border-box;
	}

	.panel-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 16px;
	}

	.panel-title {
		margin: 0;
		font-size: 18px;
		font-weight: 600;
		color: #1f2937;
	}

	.refresh-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 32px;
		border: none;
		background: #f3f4f6;
		border-radius: 6px;
		cursor: pointer;
		color: #6b7280;
		transition: all 0.2s;
	}

	.refresh-btn:hover:not(:disabled) {
		background: #e5e7eb;
		color: #374151;
	}

	.refresh-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.refresh-btn svg.spinning {
		animation: spin 1s linear infinite;
	}

	@keyframes spin {
		from { transform: rotate(0deg); }
		to { transform: rotate(360deg); }
	}

	.loading, .empty {
		text-align: center;
		padding: 40px;
		color: #6b7280;
		font-size: 14px;
	}

	.error {
		padding: 10px 14px;
		border-radius: 8px;
		font-size: 13px;
		margin-bottom: 16px;
		background: #fef2f2;
		color: #dc2626;
		border: 1px solid #fecaca;
	}

	.table-container {
		overflow-x: auto;
	}

	.orders-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}

	.orders-table thead {
		background: #f9fafb;
	}

	.orders-table th {
		padding: 10px 12px;
		text-align: center;
		font-weight: 600;
		color: #6b7280;
		border-bottom: 1px solid #e5e7eb;
		white-space: nowrap;
	}

	.orders-table td {
		padding: 12px;
		text-align: center;
		border-bottom: 1px solid #f3f4f6;
		vertical-align: middle;
	}

	.orders-table tbody tr:hover {
		background: #f9fafb;
	}

	.account-cell {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 8px;
	}

	.account-name {
		font-weight: 500;
		color: #1f2937;
	}

	.exchange-badge {
		color: #000;
		padding: 2px 6px;
		border-radius: 8px;
		font-size: 10px;
		font-weight: 600;
		text-transform: uppercase;
	}

	.symbol {
		font-weight: 600;
		color: #1f2937;
	}

	.side-badge {
		display: inline-block;
		padding: 3px 8px;
		border-radius: 4px;
		font-size: 11px;
		font-weight: 700;
		text-transform: uppercase;
	}

	.side-badge.buy {
		background: #d1fae5;
		color: #065f46;
	}

	.side-badge.sell {
		background: #fee2e2;
		color: #991b1b;
	}

	.price, .size {
		font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', monospace;
		color: #374151;
	}

	.time {
		color: #6b7280;
		font-size: 12px;
		white-space: nowrap;
	}

	.detail-btn {
		padding: 4px 10px;
		border: 1px solid #d1d5db;
		background: #f9fafb;
		border-radius: 6px;
		cursor: pointer;
		color: #374151;
		font-size: 12px;
		font-weight: 500;
		transition: all 0.2s;
		white-space: nowrap;
	}

	.detail-btn:hover {
		background: #e5e7eb;
		border-color: #9ca3af;
	}

	.detail-btn.active {
		background: #eef2ff;
		border-color: #818cf8;
		color: #4338ca;
	}

	.detail-row td {
		padding: 8px 12px !important;
		border-bottom: 1px solid #e5e7eb !important;
		background: #f9fafb;
		text-align: center;
	}

	.detail-label {
		font-size: 11px;
		color: #6b7280;
		font-weight: 600;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		margin-bottom: 2px;
	}

	.detail-value {
		font-size: 13px;
		color: #1f2937;
		font-weight: 500;
		word-break: break-all;
	}

	.detail-value.mono {
		font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', monospace;
		font-size: 12px;
	}

	.cancel-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 28px;
		height: 28px;
		border: none;
		background: #fee2e2;
		border-radius: 6px;
		cursor: pointer;
		color: #dc2626;
		font-size: 14px;
		font-weight: 600;
		transition: all 0.2s;
	}

	.cancel-btn:hover:not(:disabled) {
		background: #fecaca;
	}

	.cancel-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	@media (max-width: 768px) {
		.open-orders-panel {
			padding: 16px;
		}

		.orders-table {
			font-size: 12px;
		}

		.orders-table th,
		.orders-table td {
			padding: 8px;
		}

		.account-cell {
			flex-direction: column;
			align-items: center;
			gap: 4px;
		}
	}
</style>
