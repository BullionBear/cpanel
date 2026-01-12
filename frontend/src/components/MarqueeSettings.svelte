<script lang="ts">
	import { createEventDispatcher, onMount } from 'svelte';
	import { getSymbols, getPreference, updatePreference, type SymbolInfo } from '$lib/api';

	export let isOpen = false;

	const dispatch = createEventDispatcher();

	let symbols: SymbolInfo[] = [];
	let selectedIds: number[] = [];
	let loading = true;
	let saving = false;
	let error: string | null = null;
	let searchQuery = '';

	async function loadData() {
		try {
			loading = true;
			error = null;

			// Load symbols and current preference in parallel
			const [symbolsData, preferenceData] = await Promise.all([
				getSymbols(200),
				getPreference('execution')
			]);

			symbols = symbolsData;
			selectedIds = preferenceData.preference.banner || [];
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load data';
		} finally {
			loading = false;
		}
	}

	async function handleSave() {
		try {
			saving = true;
			error = null;

			await updatePreference({ banner: selectedIds });
			dispatch('save');
			close();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to save preference';
		} finally {
			saving = false;
		}
	}

	function toggleSymbol(id: number) {
		if (selectedIds.includes(id)) {
			selectedIds = selectedIds.filter(sid => sid !== id);
		} else {
			selectedIds = [...selectedIds, id];
		}
	}

	function close() {
		isOpen = false;
		dispatch('close');
	}

	function handleBackdropClick(event: MouseEvent) {
		if (event.target === event.currentTarget) {
			close();
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') {
			close();
		}
	}

	$: filteredSymbols = symbols.filter(s => 
		s.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
		s.exchange.name.toLowerCase().includes(searchQuery.toLowerCase())
	);

	$: if (isOpen) {
		loadData();
	}
</script>

<svelte:window on:keydown={handleKeydown} />

{#if isOpen}
	<div class="modal-backdrop" on:click={handleBackdropClick} role="dialog" aria-modal="true">
		<div class="modal">
			<div class="modal-header">
				<h2>Banner Settings</h2>
				<button class="close-btn" on:click={close} aria-label="Close">
					<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
						<path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
				</button>
			</div>

			<div class="modal-body">
				{#if loading}
					<div class="loading">Loading symbols...</div>
				{:else if error}
					<div class="error">{error}</div>
				{:else}
					<p class="description">Select symbols to display in the banner marquee.</p>
					
					<div class="search-box">
						<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
							<circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/>
							<path d="M21 21L16.65 16.65" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
						</svg>
						<input 
							type="text" 
							placeholder="Search symbols..." 
							bind:value={searchQuery}
						/>
					</div>

					<div class="selected-count">
						{selectedIds.length} symbol{selectedIds.length !== 1 ? 's' : ''} selected
					</div>

					<div class="symbols-list">
						{#each filteredSymbols as symbol (symbol.id)}
							<label class="symbol-item" class:selected={selectedIds.includes(symbol.id)}>
								<input 
									type="checkbox" 
									checked={selectedIds.includes(symbol.id)}
									on:change={() => toggleSymbol(symbol.id)}
								/>
								<div class="symbol-info">
									<span class="symbol-name">{symbol.name}</span>
									<span class="symbol-exchange">{symbol.exchange.name}</span>
								</div>
							</label>
						{/each}
						{#if filteredSymbols.length === 0}
							<div class="no-results">No symbols found</div>
						{/if}
					</div>
				{/if}
			</div>

			<div class="modal-footer">
				<button class="btn btn-secondary" on:click={close} disabled={saving}>
					Cancel
				</button>
				<button class="btn btn-primary" on:click={handleSave} disabled={saving || loading}>
					{saving ? 'Saving...' : 'Save'}
				</button>
			</div>
		</div>
	</div>
{/if}

<style>
	.modal-backdrop {
		position: fixed;
		top: 0;
		left: 0;
		right: 0;
		bottom: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 1000;
		padding: 20px;
	}

	.modal {
		background: white;
		border-radius: 16px;
		box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3);
		width: 100%;
		max-width: 500px;
		max-height: 80vh;
		display: flex;
		flex-direction: column;
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
		font-size: 20px;
		font-weight: 600;
		color: #1f2937;
	}

	.close-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 36px;
		height: 36px;
		border: none;
		background: #f3f4f6;
		border-radius: 8px;
		cursor: pointer;
		color: #6b7280;
		transition: all 0.2s;
	}

	.close-btn:hover {
		background: #e5e7eb;
		color: #374151;
	}

	.modal-body {
		flex: 1;
		padding: 24px;
		overflow-y: auto;
	}

	.description {
		margin: 0 0 20px 0;
		color: #6b7280;
		font-size: 14px;
	}

	.search-box {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 12px 16px;
		background: #f9fafb;
		border: 1px solid #e5e7eb;
		border-radius: 8px;
		margin-bottom: 16px;
	}

	.search-box svg {
		color: #9ca3af;
		flex-shrink: 0;
	}

	.search-box input {
		flex: 1;
		border: none;
		background: transparent;
		font-size: 14px;
		outline: none;
		color: #1f2937;
	}

	.search-box input::placeholder {
		color: #9ca3af;
	}

	.selected-count {
		font-size: 13px;
		color: #6b7280;
		margin-bottom: 12px;
		font-weight: 500;
	}

	.symbols-list {
		display: flex;
		flex-direction: column;
		gap: 8px;
		max-height: 300px;
		overflow-y: auto;
	}

	.symbol-item {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 12px 16px;
		background: #f9fafb;
		border: 2px solid transparent;
		border-radius: 8px;
		cursor: pointer;
		transition: all 0.2s;
	}

	.symbol-item:hover {
		background: #f3f4f6;
	}

	.symbol-item.selected {
		background: #ede9fe;
		border-color: #8b5cf6;
	}

	.symbol-item input[type="checkbox"] {
		width: 18px;
		height: 18px;
		cursor: pointer;
		accent-color: #8b5cf6;
	}

	.symbol-info {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.symbol-name {
		font-size: 14px;
		font-weight: 600;
		color: #1f2937;
	}

	.symbol-exchange {
		font-size: 12px;
		color: #6b7280;
	}

	.no-results {
		text-align: center;
		padding: 24px;
		color: #9ca3af;
		font-size: 14px;
	}

	.loading, .error {
		text-align: center;
		padding: 40px;
		color: #6b7280;
	}

	.error {
		color: #ef4444;
		background: #fef2f2;
		border: 1px solid #fecaca;
		border-radius: 8px;
		padding: 16px;
	}

	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		padding: 16px 24px;
		border-top: 1px solid #e5e7eb;
		background: #f9fafb;
	}

	.btn {
		padding: 10px 20px;
		border-radius: 8px;
		font-size: 14px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
		border: none;
	}

	.btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.btn-secondary {
		background: white;
		border: 1px solid #e5e7eb;
		color: #374151;
	}

	.btn-secondary:hover:not(:disabled) {
		background: #f3f4f6;
	}

	.btn-primary {
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
	}

	.btn-primary:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	@media (max-width: 480px) {
		.modal {
			max-height: 90vh;
		}

		.modal-header, .modal-body, .modal-footer {
			padding: 16px;
		}

		.symbols-list {
			max-height: 250px;
		}
	}
</style>
