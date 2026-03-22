<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getSymbols,
		getExchanges,
		getProducts,
		createSymbol,
		deleteSymbol,
		type SymbolInfo,
		type ExchangeOption,
		type ProductOption
	} from '$lib/api';

	let symbols: SymbolInfo[] = [];
	let exchanges: ExchangeOption[] = [];
	let products: ProductOption[] = [];

	let loading = true;
	let error: string | null = null;

	// Filters
	let filterExchange = 0;
	let filterProduct = 0;
	let filterSearch = '';

	// Add symbol modal
	let showAddModal = false;
	let addExchangeId = 0;
	let addProductId = 0;
	let addSymbolName = '';
	let adding = false;
	let addError: string | null = null;

	// Delete state
	let deletingId: number | null = null;

	$: filteredSymbols = symbols.filter((s) => {
		if (filterExchange && s.exchange.id !== filterExchange) return false;
		if (filterProduct && s.product.id !== filterProduct) return false;
		if (filterSearch) {
			const q = filterSearch.toLowerCase();
			return (
				s.name.toLowerCase().includes(q) ||
				s.universal_ticker.toLowerCase().includes(q) ||
				s.base_token.name.toLowerCase().includes(q) ||
				s.quote_token.name.toLowerCase().includes(q)
			);
		}
		return true;
	});

	onMount(async () => {
		await loadData();
	});

	async function loadData() {
		loading = true;
		error = null;
		try {
			const [syms, exchs, prods] = await Promise.all([
				getSymbols(1000),
				getExchanges(),
				getProducts()
			]);
			symbols = syms;
			exchanges = exchs;
			products = prods;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load data';
		} finally {
			loading = false;
		}
	}

	function openAddModal() {
		addExchangeId = exchanges.length > 0 ? exchanges[0].id : 0;
		addProductId = products.length > 0 ? products[0].id : 0;
		addSymbolName = '';
		addError = null;
		showAddModal = true;
	}

	function closeAddModal() {
		showAddModal = false;
		addError = null;
	}

	async function handleAddSymbol() {
		if (!addExchangeId || !addProductId || !addSymbolName.trim()) {
			addError = 'All fields are required';
			return;
		}
		adding = true;
		addError = null;
		try {
			await createSymbol({
				exchange_id: addExchangeId,
				product_id: addProductId,
				symbol: addSymbolName.trim().toUpperCase()
			});
			closeAddModal();
			await loadData();
		} catch (e) {
			addError = e instanceof Error ? e.message : 'Failed to create symbol';
		} finally {
			adding = false;
		}
	}

	async function handleDelete(sym: SymbolInfo) {
		if (!confirm(`Delete symbol "${sym.universal_ticker}"? This cannot be undone.`)) return;
		deletingId = sym.id;
		try {
			await deleteSymbol(sym.id);
			symbols = symbols.filter((s) => s.id !== sym.id);
		} catch (e) {
			alert(e instanceof Error ? e.message : 'Failed to delete symbol');
		} finally {
			deletingId = null;
		}
	}

	function exchangeBadgeClass(slug: string): string {
		return `badge-${slug.toLowerCase()}`;
	}

	function productBadgeClass(slug: string): string {
		if (slug === 'SPOT') return 'badge-spot';
		if (slug === 'UMARGIN') return 'badge-umargin';
		return 'badge-default';
	}
</script>

<div class="symbol-page">
	<div class="page-header">
		<h1>Symbols</h1>
		<button class="add-btn" on:click={openAddModal}>
			<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			Add Symbol
		</button>
	</div>

	<!-- Filters -->
	<div class="filters">
		<div class="filter-group">
			<label for="filterExchange">Exchange</label>
			<select id="filterExchange" bind:value={filterExchange}>
				<option value={0}>All Exchanges</option>
				{#each exchanges as ex}
					<option value={ex.id}>{ex.name}</option>
				{/each}
			</select>
		</div>
		<div class="filter-group">
			<label for="filterProduct">Product</label>
			<select id="filterProduct" bind:value={filterProduct}>
				<option value={0}>All Products</option>
				{#each products as p}
					<option value={p.id}>{p.name}</option>
				{/each}
			</select>
		</div>
		<div class="filter-group search-group">
			<label for="filterSearch">Search</label>
			<input
				id="filterSearch"
				type="text"
				placeholder="Search by name, ticker..."
				bind:value={filterSearch}
			/>
		</div>
	</div>

	{#if error}
		<div class="error-banner">{error}</div>
	{/if}

	{#if loading}
		<div class="loading-state">
			<div class="spinner-icon"></div>
			<span>Loading symbols...</span>
		</div>
	{:else if filteredSymbols.length === 0}
		<div class="empty-state">
			<svg width="48" height="48" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M2 17L12 22L22 17" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M2 12L12 17L22 12" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			<p>No symbols found</p>
			<span class="empty-hint">Add a symbol to get started, or adjust your filters.</span>
		</div>
	{:else}
		<div class="table-wrap">
			<table class="symbol-table">
				<thead>
					<tr>
						<th>Symbol</th>
						<th>Exchange</th>
						<th>Product</th>
						<th>Base</th>
						<th>Quote</th>
						<th class="num-col">Price Precision</th>
						<th class="num-col">Size Precision</th>
						<th>Universal Ticker</th>
						<th class="action-col"></th>
					</tr>
				</thead>
				<tbody>
					{#each filteredSymbols as sym (sym.id)}
						<tr>
							<td class="symbol-name">{sym.name}</td>
							<td>
								<span class="badge {exchangeBadgeClass(sym.exchange.slug)}">{sym.exchange.name}</span>
							</td>
							<td>
								<span class="badge {productBadgeClass(sym.product.slug)}">{sym.product.slug}</span>
							</td>
							<td>{sym.base_token.name}</td>
							<td>{sym.quote_token.name}</td>
							<td class="num-col">{sym.price_precision}</td>
							<td class="num-col">{sym.size_precision}</td>
							<td class="ticker-cell">
								<code>{sym.universal_ticker}</code>
							</td>
							<td class="action-col">
								<button
									class="delete-btn"
									on:click={() => handleDelete(sym)}
									disabled={deletingId === sym.id}
									title="Delete symbol"
								>
									<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
										<path d="M3 6H5H21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
										<path d="M8 6V4C8 3.44772 8.44772 3 9 3H15C15.5523 3 16 3.44772 16 4V6M19 6V20C19 20.5523 18.5523 21 18 21H6C5.44772 21 5 20.5523 5 20V6H19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
									</svg>
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
		<div class="table-footer">
			Showing {filteredSymbols.length} of {symbols.length} symbol{symbols.length !== 1 ? 's' : ''}
		</div>
	{/if}
</div>

<!-- Add Symbol Modal -->
{#if showAddModal}
	<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-noninteractive-element-interactions -->
	<div class="modal-overlay" on:click|self={closeAddModal} role="dialog" tabindex="-1">
		<div class="modal">
			<div class="modal-header">
				<h2>Add Symbol</h2>
				<button class="modal-close" on:click={closeAddModal}>
					<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
						<path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
				</button>
			</div>
			<div class="modal-body">
				{#if addError}
					<div class="modal-error">{addError}</div>
				{/if}
				<div class="form-field">
					<label for="addExchange">Exchange</label>
					<select id="addExchange" bind:value={addExchangeId}>
						{#each exchanges as ex}
							<option value={ex.id}>{ex.name}</option>
						{/each}
					</select>
				</div>
				<div class="form-field">
					<label for="addProduct">Product</label>
					<select id="addProduct" bind:value={addProductId}>
						{#each products as p}
							<option value={p.id}>{p.name} ({p.slug})</option>
						{/each}
					</select>
				</div>
				<div class="form-field">
					<label for="addSymbol">Symbol Name</label>
					<input
						id="addSymbol"
						type="text"
						placeholder="e.g. BTCUSDT"
						bind:value={addSymbolName}
						on:keydown={(e) => e.key === 'Enter' && handleAddSymbol()}
					/>
					<span class="field-hint">The exact trading pair name on the exchange (e.g. BTCUSDT, ETHUSDT)</span>
				</div>
			</div>
			<div class="modal-actions">
				<button class="btn-cancel" on:click={closeAddModal}>Cancel</button>
				<button class="btn-confirm" on:click={handleAddSymbol} disabled={adding}>
					{adding ? 'Adding...' : 'Add Symbol'}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.symbol-page {
		max-width: 1400px;
		margin: 0 auto;
	}

	.page-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 24px;
	}

	.page-header h1 {
		margin: 0;
		font-size: 28px;
		font-weight: 600;
		color: #1f2937;
	}

	.add-btn {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 10px 20px;
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
		border: none;
		border-radius: 8px;
		font-size: 14px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
	}

	.add-btn:hover {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.add-btn svg {
		stroke: currentColor;
	}

	/* Filters */
	.filters {
		display: flex;
		gap: 16px;
		margin-bottom: 20px;
		flex-wrap: wrap;
	}

	.filter-group {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.filter-group label {
		font-size: 12px;
		font-weight: 500;
		color: #6b7280;
		text-transform: uppercase;
		letter-spacing: 0.5px;
	}

	.filter-group select,
	.filter-group input {
		padding: 8px 12px;
		border: 1px solid #d1d5db;
		border-radius: 8px;
		font-size: 14px;
		color: #1f2937;
		background: white;
		transition: border-color 0.2s;
	}

	.filter-group select:focus,
	.filter-group input:focus {
		outline: none;
		border-color: #667eea;
		box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
	}

	.search-group {
		flex: 1;
		min-width: 200px;
	}

	.search-group input {
		width: 100%;
	}

	/* Error */
	.error-banner {
		background: #fef2f2;
		border: 1px solid #fecaca;
		color: #ef4444;
		padding: 12px 16px;
		border-radius: 8px;
		margin-bottom: 20px;
		font-size: 14px;
	}

	/* Loading */
	.loading-state {
		display: flex;
		align-items: center;
		justify-content: center;
		gap: 12px;
		padding: 60px 24px;
		color: #6b7280;
		font-size: 15px;
	}

	.spinner-icon {
		width: 20px;
		height: 20px;
		border: 2px solid #e5e7eb;
		border-top-color: #667eea;
		border-radius: 50%;
		animation: spin 0.8s linear infinite;
	}

	@keyframes spin {
		to { transform: rotate(360deg); }
	}

	/* Empty */
	.empty-state {
		display: flex;
		flex-direction: column;
		align-items: center;
		gap: 12px;
		padding: 60px 24px;
		text-align: center;
	}

	.empty-state p {
		margin: 0;
		font-size: 16px;
		font-weight: 500;
		color: #6b7280;
	}

	.empty-hint {
		font-size: 13px;
		color: #9ca3af;
	}

	/* Table */
	.table-wrap {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		overflow: hidden;
		overflow-x: auto;
	}

	.symbol-table {
		width: 100%;
		border-collapse: collapse;
		min-width: 800px;
	}

	.symbol-table thead {
		background: #f9fafb;
	}

	.symbol-table th {
		padding: 12px 16px;
		text-align: left;
		font-size: 13px;
		font-weight: 600;
		color: #374151;
		border-bottom: 2px solid #e5e7eb;
		white-space: nowrap;
		text-transform: uppercase;
		letter-spacing: 0.3px;
	}

	.symbol-table td {
		padding: 12px 16px;
		font-size: 14px;
		color: #1f2937;
		border-bottom: 1px solid #f3f4f6;
	}

	.symbol-table tbody tr {
		transition: background 0.15s;
	}

	.symbol-table tbody tr:hover {
		background: #f9fafb;
	}

	.symbol-table tbody tr:last-child td {
		border-bottom: none;
	}

	.symbol-name {
		font-weight: 600;
	}

	.num-col {
		text-align: center;
	}

	.action-col {
		width: 48px;
		text-align: center;
	}

	.ticker-cell code {
		font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
		font-size: 12px;
		background: #f3f4f6;
		padding: 4px 8px;
		border-radius: 4px;
		color: #4b5563;
	}

	/* Badges */
	.badge {
		display: inline-block;
		padding: 3px 10px;
		border-radius: 12px;
		font-size: 12px;
		font-weight: 600;
		white-space: nowrap;
	}

	.badge-binance {
		background: #fef3c7;
		color: #92400e;
	}

	.badge-bybit {
		background: #fff7ed;
		color: #c2410c;
	}

	.badge-bitget {
		background: #ecfdf5;
		color: #065f46;
	}

	.badge-spot {
		background: #eff6ff;
		color: #1e40af;
	}

	.badge-umargin {
		background: #faf5ff;
		color: #7c3aed;
	}

	.badge-default {
		background: #f3f4f6;
		color: #374151;
	}

	/* Delete button */
	.delete-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 32px;
		border: none;
		background: transparent;
		border-radius: 6px;
		cursor: pointer;
		color: #9ca3af;
		transition: all 0.2s;
	}

	.delete-btn:hover:not(:disabled) {
		background: #fef2f2;
		color: #ef4444;
	}

	.delete-btn:disabled {
		opacity: 0.4;
		cursor: not-allowed;
	}

	.delete-btn svg {
		stroke: currentColor;
	}

	/* Table footer */
	.table-footer {
		padding: 12px 16px;
		font-size: 13px;
		color: #9ca3af;
		text-align: right;
	}

	/* Modal */
	.modal-overlay {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 1000;
		padding: 16px;
	}

	.modal {
		background: white;
		border-radius: 16px;
		width: 100%;
		max-width: 480px;
		box-shadow: 0 20px 60px rgba(0, 0, 0, 0.15);
		overflow: hidden;
	}

	.modal-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 20px 24px;
		border-bottom: 1px solid #e5e7eb;
	}

	.modal-header h2 {
		margin: 0;
		font-size: 18px;
		font-weight: 600;
		color: #1f2937;
	}

	.modal-close {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 32px;
		border: none;
		background: #f3f4f6;
		border-radius: 8px;
		cursor: pointer;
		color: #6b7280;
		transition: all 0.2s;
	}

	.modal-close:hover {
		background: #e5e7eb;
		color: #374151;
	}

	.modal-close svg {
		stroke: currentColor;
	}

	.modal-body {
		padding: 24px;
		display: flex;
		flex-direction: column;
		gap: 18px;
	}

	.modal-error {
		background: #fef2f2;
		border: 1px solid #fecaca;
		color: #ef4444;
		padding: 10px 14px;
		border-radius: 8px;
		font-size: 13px;
	}

	.form-field {
		display: flex;
		flex-direction: column;
		gap: 6px;
	}

	.form-field label {
		font-size: 14px;
		font-weight: 500;
		color: #374151;
	}

	.form-field select,
	.form-field input {
		padding: 10px 12px;
		border: 1px solid #d1d5db;
		border-radius: 8px;
		font-size: 14px;
		color: #1f2937;
		background: white;
		transition: border-color 0.2s;
	}

	.form-field select:focus,
	.form-field input:focus {
		outline: none;
		border-color: #667eea;
		box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
	}

	.form-field input::placeholder {
		color: #9ca3af;
	}

	.field-hint {
		font-size: 12px;
		color: #9ca3af;
	}

	.modal-actions {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		padding: 16px 24px;
		border-top: 1px solid #e5e7eb;
		background: #f9fafb;
	}

	.btn-cancel {
		padding: 10px 20px;
		border: 1px solid #d1d5db;
		background: white;
		border-radius: 8px;
		font-size: 14px;
		font-weight: 500;
		color: #374151;
		cursor: pointer;
		transition: all 0.2s;
	}

	.btn-cancel:hover {
		background: #f3f4f6;
	}

	.btn-confirm {
		padding: 10px 20px;
		border: none;
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		border-radius: 8px;
		font-size: 14px;
		font-weight: 500;
		color: white;
		cursor: pointer;
		transition: all 0.2s;
	}

	.btn-confirm:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.btn-confirm:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	/* Responsive */
	@media (max-width: 768px) {
		.page-header {
			flex-direction: column;
			align-items: flex-start;
			gap: 16px;
		}

		.filters {
			flex-direction: column;
		}

		.filter-group {
			width: 100%;
		}

		.search-group {
			min-width: 0;
		}

		.symbol-table th,
		.symbol-table td {
			padding: 10px 12px;
			font-size: 13px;
		}

		.modal {
			max-width: 100%;
		}
	}
</style>
