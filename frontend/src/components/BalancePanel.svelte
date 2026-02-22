<script lang="ts">
	import { onMount } from 'svelte';
	import { getWalletsV2, getBalancesV2, type BalanceResponse } from '$lib/api';

	let balances: BalanceResponse[] = [];
	let loading = true;
	let error: string | null = null;
	let expandedAccounts: Set<number> = new Set();

	async function loadBalances() {
		try {
			loading = true;
			error = null;

			// Get all wallets (v2)
			const wallets = await getWalletsV2();
			if (wallets.length === 0) {
				balances = [];
				loading = false;
				return;
			}

			// Get balances for all wallet IDs (v2)
			const walletIds = wallets.map((w) => w.id);
			balances = await getBalancesV2(walletIds);

			// Expand all accounts by default
			expandedAccounts = new Set(balances.map((b) => b.api_id));
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load balances';
		} finally {
			loading = false;
		}
	}

	function toggleAccount(apiId: number) {
		if (expandedAccounts.has(apiId)) {
			expandedAccounts.delete(apiId);
		} else {
			expandedAccounts.add(apiId);
		}
		expandedAccounts = expandedAccounts; // trigger reactivity
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

	function formatBalance(value: string): string {
		const num = parseFloat(value);
		if (isNaN(num)) return value;
		if (num === 0) return '0';
		if (num >= 1000) {
			return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
		} else if (num >= 1) {
			return num.toFixed(4);
		} else if (num >= 0.0001) {
			return num.toFixed(6);
		} else {
			return num.toFixed(8);
		}
	}

	onMount(() => {
		loadBalances();
	});
</script>

<div class="balance-panel">
	<div class="panel-header">
		<h3 class="panel-title">Balances</h3>
		<button class="refresh-btn" on:click={loadBalances} disabled={loading} title="Refresh">
			<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" class:spinning={loading}>
				<path d="M23 4V10H17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M1 20V14H7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M3.51 9.00001C4.01717 7.56679 4.87913 6.2854 6.01547 5.27543C7.1518 4.26545 8.52547 3.55977 10.0083 3.22427C11.4911 2.88877 13.0348 2.93436 14.4952 3.35679C15.9556 3.77921 17.2853 4.56472 18.36 5.64001L23 10M1 14L5.64 18.36C6.71475 19.4353 8.04437 20.2208 9.50481 20.6432C10.9652 21.0657 12.5089 21.1112 13.9917 20.7757C15.4745 20.4402 16.8482 19.7346 17.9845 18.7246C19.1209 17.7146 19.9828 16.4332 20.49 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
		</button>
	</div>

	<div class="panel-body">
		{#if loading}
			<div class="loading">Loading balances...</div>
		{:else if error}
			<div class="error">{error}</div>
		{:else if balances.length === 0}
			<div class="empty">No wallets configured. Add wallets in Account page.</div>
		{:else}
			<div class="balances-list">
				{#each balances as account (account.api_id)}
					<div class="account-section">
						<button 
							class="account-header" 
							on:click={() => toggleAccount(account.api_id)}
							aria-expanded={expandedAccounts.has(account.api_id)}
						>
							<div class="account-info">
								<svg 
									class="chevron" 
									class:expanded={expandedAccounts.has(account.api_id)}
									width="16" 
									height="16" 
									viewBox="0 0 24 24" 
									fill="none"
								>
									<path d="M9 18L15 12L9 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
								</svg>
								<span class="account-name">{account.name}</span>
							</div>
							<span class="exchange-badge" style="background-color: {getExchangeColor(account.exchange)};">
								{account.exchange}
							</span>
						</button>

						{#if expandedAccounts.has(account.api_id)}
							<div class="account-content">
								{#each account.wallets as w}
									<div class="wallet-section">
										<div class="wallet-name">{w.wallet}</div>
										<div class="token-list">
											{#each w.balance as token}
												<div class="token-row">
													<span class="token-name">{token.token}</span>
													<div class="token-amounts">
														<span class="amount available" title="Available">{formatBalance(token.available)}</span>
														{#if parseFloat(token.frozen) > 0}
															<span class="amount frozen" title="Frozen">({formatBalance(token.frozen)})</span>
														{/if}
													</div>
												</div>
											{/each}
										</div>
									</div>
								{/each}
							</div>
						{/if}
					</div>
				{/each}
			</div>
		{/if}
	</div>
</div>

<style>
	.balance-panel {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		padding: 20px;
		width: 100%;
		box-sizing: border-box;
		height: 420px;
		display: flex;
		flex-direction: column;
	}

	.panel-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 16px;
		flex-shrink: 0;
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

	.panel-body {
		flex: 1;
		overflow-y: auto;
		min-height: 0;
	}

	.loading, .error, .empty {
		text-align: center;
		padding: 24px;
		color: #6b7280;
		font-size: 14px;
	}

	.error {
		color: #dc2626;
		background: #fef2f2;
		border: 1px solid #fecaca;
		border-radius: 8px;
	}

	.balances-list {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.account-section {
		border: 1px solid #e5e7eb;
		border-radius: 8px;
		overflow: hidden;
	}

	.account-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 12px 14px;
		background: #f9fafb;
		border: none;
		width: 100%;
		cursor: pointer;
		transition: background 0.2s;
	}

	.account-header:hover {
		background: #f3f4f6;
	}

	.account-info {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.chevron {
		color: #6b7280;
		transition: transform 0.2s;
		flex-shrink: 0;
	}

	.chevron.expanded {
		transform: rotate(90deg);
	}

	.account-name {
		font-size: 14px;
		font-weight: 600;
		color: #1f2937;
	}

	.exchange-badge {
		color: #000;
		padding: 2px 8px;
		border-radius: 10px;
		font-size: 10px;
		font-weight: 600;
		text-transform: uppercase;
	}

	.account-content {
		border-top: 1px solid #e5e7eb;
	}

	.wallet-section {
		padding: 10px 14px;
	}

	.wallet-section:not(:last-child) {
		border-bottom: 1px solid #f3f4f6;
	}

	.wallet-name {
		font-size: 11px;
		font-weight: 600;
		color: #9ca3af;
		text-transform: uppercase;
		margin-bottom: 8px;
	}

	.token-list {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.token-row {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 4px 0;
	}

	.token-name {
		font-size: 13px;
		font-weight: 500;
		color: #374151;
	}

	.token-amounts {
		display: flex;
		align-items: center;
		gap: 6px;
	}

	.amount {
		font-size: 13px;
		font-family: 'SF Mono', 'Monaco', 'Inconsolata', 'Fira Code', monospace;
	}

	.amount.available {
		color: #1f2937;
		font-weight: 600;
	}

	.amount.frozen {
		color: #9ca3af;
		font-size: 11px;
	}

	@media (max-width: 480px) {
		.balance-panel {
			padding: 16px;
			height: 380px;
		}

		.panel-title {
			font-size: 16px;
		}

		.account-header {
			padding: 10px 12px;
		}

		.account-name {
			font-size: 13px;
		}

		.wallet-section {
			padding: 8px 12px;
		}

		.token-name, .amount {
			font-size: 12px;
		}
	}
</style>
