<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getAccountsV2,
		getApiKeysV2,
		getSymbols,
		createOrder,
		type AccountV2,
		type APIKeyV2,
		type SymbolInfo
	} from '$lib/api';

	let accounts: AccountV2[] = [];
	let apiKeys: APIKeyV2[] = [];
	let symbols: SymbolInfo[] = [];
	let filteredSymbols: SymbolInfo[] = [];
	let loading = true;
	let submitting = false;
	let error: string | null = null;
	let success: string | null = null;

	// Form fields: selectedAccountId is account id (from GET v2/account/acct)
	let selectedAccountId: number | null = null;
	let selectedSymbolId: number | null = null;
	let orderType: 'LIMIT' | 'MARKET' = 'LIMIT';
	let price: string = '';
	let qty: string = '';

	async function loadData() {
		try {
			loading = true;
			error = null;

			const [accountsData, apiKeysData, symbolsData] = await Promise.all([
				getAccountsV2(),
				getApiKeysV2(),
				getSymbols(200)
			]);

			accounts = accountsData;
			apiKeys = apiKeysData;
			symbols = symbolsData;

			// Set default account if available
			if (accounts.length > 0 && !selectedAccountId) {
				selectedAccountId = accounts[0].id;
				updateFilteredSymbols();
			}
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load data';
		} finally {
			loading = false;
		}
	}

	// Get API keys for an account (match by exchange + uid)
	function getApisForAccount(acct: AccountV2): APIKeyV2[] {
		return apiKeys.filter(
			(api) =>
				api.exchange === acct.exchange && Number(api.uid) === Number(acct.uid)
		);
	}

	function updateFilteredSymbols() {
		if (!selectedAccountId) {
			filteredSymbols = [];
			return;
		}

		const selectedAccount = accounts.find((a) => a.id === selectedAccountId);
		if (!selectedAccount) {
			filteredSymbols = [];
			return;
		}

		// Filter symbols by exchange
		filteredSymbols = symbols.filter(
			(s) => s.exchange.slug.toUpperCase() === selectedAccount.exchange.toUpperCase()
		);

		// Reset symbol selection if not in filtered list
		if (selectedSymbolId && !filteredSymbols.find((s) => s.id === selectedSymbolId)) {
			selectedSymbolId = filteredSymbols.length > 0 ? filteredSymbols[0].id : null;
		} else if (!selectedSymbolId && filteredSymbols.length > 0) {
			selectedSymbolId = filteredSymbols[0].id;
		}
	}

	function handleAccountChange() {
		updateFilteredSymbols();
	}

	function handlePriceInput(event: Event) {
		const input = event.target as HTMLInputElement;
		// Allow only numbers and decimal point
		const value = input.value.replace(/[^0-9.]/g, '');
		// Prevent multiple decimal points
		const parts = value.split('.');
		if (parts.length > 2) {
			price = parts[0] + '.' + parts.slice(1).join('');
		} else {
			price = value;
		}
	}

	function handleQtyInput(event: Event) {
		const input = event.target as HTMLInputElement;
		// Allow only numbers and decimal point
		const value = input.value.replace(/[^0-9.]/g, '');
		// Prevent multiple decimal points
		const parts = value.split('.');
		if (parts.length > 2) {
			qty = parts[0] + '.' + parts.slice(1).join('');
		} else {
			qty = value;
		}
	}

	async function submitOrder(side: 'BUY' | 'SELL') {
		if (!selectedAccountId || !selectedSymbolId || !qty) {
			error = 'Please fill all required fields';
			return;
		}

		if (orderType === 'LIMIT' && !price) {
			error = 'Price is required for LIMIT orders';
			return;
		}

		const selectedAccount = accounts.find((a) => a.id === selectedAccountId);
		if (!selectedAccount) {
			error = 'Account not found';
			return;
		}
		const acctApis = getApisForAccount(selectedAccount);
		if (acctApis.length === 0) {
			error = 'No API key for this account. Add an API key in Account page.';
			return;
		}
		// v2/order expects account_id = API key ID
		const apiKeyId = acctApis[0].id;

		try {
			submitting = true;
			error = null;
			success = null;

			const orderData = {
				symbol_id: selectedSymbolId,
				account_id: apiKeyId,
				type: orderType,
				qty: qty,
				side: side,
				...(orderType === 'LIMIT' && price ? { price } : {})
			};

			const result = await createOrder(orderData);
			success = `Order ${result.order_id} created (${side} ${result.quantity} @ ${result.price === '-1' ? 'MKT' : result.price})`;

			// Clear form after success
			price = '';
			qty = '';
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to create order';
		} finally {
			submitting = false;
		}
	}

	onMount(() => {
		loadData();
	});

	$: if (selectedAccountId) {
		updateFilteredSymbols();
	}
</script>

<div class="order-panel">
	<h3 class="panel-title">Create Order</h3>

	{#if loading}
		<div class="loading">Loading...</div>
	{:else}
		<div class="panel-body">
			{#if error}
				<div class="message error">{error}</div>
			{/if}
			{#if success}
				<div class="message success">{success}</div>
			{/if}

			<div class="form-grid">
				<div class="form-row">
					<div class="form-group">
						<label for="account">Account</label>
						<select 
							id="account" 
							bind:value={selectedAccountId}
							on:change={handleAccountChange}
							disabled={submitting}
						>
							{#if accounts.length === 0}
								<option value={null}>No accounts</option>
							{:else}
								{#each accounts as account}
									<option value={account.id}>{account.name} ({account.exchange})</option>
								{/each}
							{/if}
						</select>
					</div>

					<div class="form-group">
						<label for="symbol">Symbol</label>
						<select 
							id="symbol" 
							bind:value={selectedSymbolId}
							disabled={submitting || filteredSymbols.length === 0}
						>
							{#if filteredSymbols.length === 0}
								<option value={null}>No symbols</option>
							{:else}
							{#each filteredSymbols as symbol}
								<option value={symbol.id}>{symbol.universal_ticker}</option>
							{/each}
							{/if}
						</select>
					</div>
				</div>

				<div class="form-group">
					<label for="type">Type</label>
					<select id="type" bind:value={orderType} disabled={submitting}>
						<option value="LIMIT">LIMIT</option>
						<option value="MARKET">MARKET</option>
					</select>
				</div>

				<div class="form-row">
					<div class="form-group">
						<label for="price">Price</label>
						<input 
							id="price"
							type="text"
							inputmode="decimal"
							placeholder={orderType === 'MARKET' ? 'N/A' : '0.00'}
							bind:value={price}
							on:input={handlePriceInput}
							disabled={submitting || orderType === 'MARKET'}
						/>
					</div>

					<div class="form-group">
						<label for="qty">Quantity</label>
						<input 
							id="qty"
							type="text"
							inputmode="decimal"
							placeholder="0.00"
							bind:value={qty}
							on:input={handleQtyInput}
							disabled={submitting}
						/>
					</div>
				</div>
			</div>
		</div>

		<div class="button-group">
			<button 
				class="btn btn-buy" 
				on:click={() => submitOrder('BUY')}
				disabled={submitting || !selectedAccountId || !selectedSymbolId || !qty}
			>
				{submitting ? '...' : 'BUY'}
			</button>
			<button 
				class="btn btn-sell" 
				on:click={() => submitOrder('SELL')}
				disabled={submitting || !selectedAccountId || !selectedSymbolId || !qty}
			>
				{submitting ? '...' : 'SELL'}
			</button>
		</div>
	{/if}
</div>

<style>
	.order-panel {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		padding: 20px;
		width: 100%;
		box-sizing: border-box;
		height: 420px;
		display: flex;
		flex-direction: column;
		overflow: hidden;
	}

	.panel-title {
		margin: 0 0 16px 0;
		font-size: 18px;
		font-weight: 600;
		color: #1f2937;
		flex-shrink: 0;
	}

	.panel-body {
		flex: 1;
		overflow-y: auto;
		min-height: 0;
	}

	.loading {
		text-align: center;
		padding: 20px;
		color: #6b7280;
	}

	.message {
		padding: 8px 12px;
		border-radius: 6px;
		font-size: 12px;
		margin-bottom: 12px;
	}

	.message.error {
		background: #fef2f2;
		color: #dc2626;
		border: 1px solid #fecaca;
	}

	.message.success {
		background: #f0fdf4;
		color: #16a34a;
		border: 1px solid #bbf7d0;
	}

	.form-grid {
		display: flex;
		flex-direction: column;
		gap: 12px;
	}

	.form-row {
		display: flex;
		gap: 12px;
	}

	.form-row .form-group {
		flex: 1;
	}

	.form-group {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.form-group label {
		font-size: 12px;
		font-weight: 500;
		color: #374151;
	}

	.form-group select,
	.form-group input {
		padding: 8px 10px;
		border: 1px solid #d1d5db;
		border-radius: 6px;
		font-size: 13px;
		color: #1f2937;
		background: white;
		transition: border-color 0.2s, box-shadow 0.2s;
	}

	.form-group select:focus,
	.form-group input:focus {
		outline: none;
		border-color: #667eea;
		box-shadow: 0 0 0 2px rgba(102, 126, 234, 0.1);
	}

	.form-group select:disabled,
	.form-group input:disabled {
		background: #f9fafb;
		color: #9ca3af;
		cursor: not-allowed;
	}

	.form-group input::placeholder {
		color: #9ca3af;
	}

	.button-group {
		display: flex;
		gap: 12px;
		padding-top: 16px;
		flex-shrink: 0;
		border-top: 1px solid #f3f4f6;
		margin-top: 16px;
	}

	.btn {
		flex: 1;
		padding: 12px 16px;
		border: none;
		border-radius: 8px;
		font-size: 15px;
		font-weight: 700;
		cursor: pointer;
		transition: all 0.2s;
	}

	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.btn-buy {
		background: #0ecb81;
		color: white;
	}

	.btn-buy:hover:not(:disabled) {
		background: #0db374;
	}

	.btn-sell {
		background: #f6465d;
		color: white;
	}

	.btn-sell:hover:not(:disabled) {
		background: #e63950;
	}

	@media (max-width: 480px) {
		.order-panel {
			padding: 16px;
			height: 380px;
		}

		.panel-title {
			font-size: 16px;
		}

		.form-row {
			flex-direction: column;
		}

		.form-group select,
		.form-group input {
			padding: 8px 10px;
			font-size: 13px;
		}

		.btn {
			padding: 10px 14px;
			font-size: 14px;
		}
	}
</style>
