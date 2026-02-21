<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getWallets,
		getAPIKeys,
		getWalletTypes,
		deleteWallet,
		type Wallet,
		type APIKey,
		type WalletType
	} from '$lib/api';
	import CreateWalletModal from '../../../components/CreateWalletModal.svelte';

	let wallets: Wallet[] = [];
	let apiKeys: APIKey[] = [];
	let walletTypes: WalletType[] = [];
	let loading = true;
	let error: string | null = null;
	let deletingId: number | null = null;
	let showMenuId: number | null = null;
	let showCreateModal = false;

	// Map wallet_type_id to slug for display
	$: walletTypeMap = walletTypes.reduce((acc, wt) => {
		acc[wt.id] = wt.slug;
		return acc;
	}, {} as Record<number, string>);
	$: apiKeyMap = apiKeys.reduce((acc, api) => {
		acc[api.id] = `${api.exchange} – ${api.name}`;
		return acc;
	}, {} as Record<number, string>);

	onMount(() => {
		loadAll();

		if (typeof window !== 'undefined' && typeof document !== 'undefined') {
			const handleClickOutside = (event: MouseEvent) => {
				const target = event.target as HTMLElement;
				if (target && !target.closest('.wallet-actions')) {
					showMenuId = null;
				}
			};
			document.addEventListener('click', handleClickOutside);
			return () => document.removeEventListener('click', handleClickOutside);
		}
		return undefined;
	});

	async function loadAll() {
		loading = true;
		error = null;
		try {
			[wallets, apiKeys, walletTypes] = await Promise.all([
				getWallets(),
				getAPIKeys(),
				getWalletTypes()
			]);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load';
		} finally {
			loading = false;
		}
	}

	async function handleDelete(w: Wallet) {
		if (!confirm(`Remove wallet "${w.name}"?`)) return;
		deletingId = w.id;
		showMenuId = null;
		try {
			await deleteWallet(w.id);
			await loadAll();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to delete wallet';
		} finally {
			deletingId = null;
		}
	}

	function toggleMenu(id: number, event: MouseEvent) {
		event.stopPropagation();
		showMenuId = showMenuId === id ? null : id;
	}

	function handleCreate() {
		showCreateModal = true;
	}

	function handleModalClose() {
		showCreateModal = false;
	}

	function handleModalSuccess() {
		loadAll();
	}
</script>

<div class="wallet-page">
	<div class="page-header">
		<h1>Wallets</h1>
		<button class="create-btn" on:click={handleCreate} disabled={apiKeys.length === 0}>
			<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			Create Wallet
		</button>
	</div>

	{#if !loading && !error && apiKeys.length === 0}
		<div class="empty-hint">
			<p>Register an API key first to create wallets.</p>
			<a href="/apikeys" class="link">Go to API Keys</a>
		</div>
	{/if}

	{#if loading}
		<div class="loading">Loading wallets...</div>
	{:else if error}
		<div class="error">{error}</div>
		<p class="error-hint">Check that the API is running and <code>VITE_API_BASE_URL</code> points to it (e.g. <code>http://localhost:8186/api</code>).</p>
	{:else if wallets.length === 0}
		<div class="empty-state">
			<p>No wallets yet. Create one and associate it to an existing API key.</p>
			<button class="create-btn" on:click={handleCreate} disabled={apiKeys.length === 0}>
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
					<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				Create Wallet
			</button>
		</div>
	{:else}
		<div class="wallet-list">
			<div class="list-card">
				{#each wallets as w (w.id)}
					<div class="wallet-item">
						<div class="wallet-info">
							<h3 class="wallet-name">{w.name}</h3>
							<p class="wallet-meta">
								{#if w.api_id != null && apiKeyMap[w.api_id]}
									<span class="api-label">{apiKeyMap[w.api_id]}</span>
								{/if}
								{#if walletTypeMap[w.wallet_type_id]}
									<span class="type-badge">{walletTypeMap[w.wallet_type_id]}</span>
								{:else}
									<span class="type-badge">Type #{w.wallet_type_id}</span>
								{/if}
								{#if !w.is_active}
									<span class="inactive">Inactive</span>
								{/if}
							</p>
						</div>
						<div class="wallet-actions">
							<button
								class="menu-btn"
								on:click={(e) => toggleMenu(w.id, e)}
								disabled={deletingId === w.id}
								aria-label="Actions"
							>
								<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
									<circle cx="12" cy="5" r="2" fill="currentColor"/>
									<circle cx="12" cy="12" r="2" fill="currentColor"/>
									<circle cx="12" cy="19" r="2" fill="currentColor"/>
								</svg>
							</button>
							{#if showMenuId === w.id}
								<div class="menu-dropdown">
									<button
										class="menu-item delete"
										on:click={() => handleDelete(w)}
										disabled={deletingId === w.id}
									>
										<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
											<path d="M3 6H5H21M8 6V4C8 3.46957 8.21071 2.96086 8.58579 2.58579C8.96086 2.21071 9.46957 2 10 2H14C14.5304 2 15.0391 2.21071 15.4142 2.58579C15.7893 2.96086 16 3.46957 16 4V6M19 6V20C19 20.5304 18.7893 21.0391 18.4142 21.4142C18.0391 21.7893 17.5304 22 17 22H7C6.46957 22 5.96086 21.7893 5.58579 21.4142C5.21071 21.0391 5 20.5304 5 20V6H19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
										</svg>
										{deletingId === w.id ? 'Removing...' : 'Remove'}
									</button>
								</div>
							{/if}
						</div>
					</div>
				{/each}
			</div>
		</div>
	{/if}

	<CreateWalletModal
		bind:isOpen={showCreateModal}
		apiKeys={apiKeys}
		walletTypes={walletTypes}
		on:close={handleModalClose}
		on:success={handleModalSuccess}
	/>
</div>

<style>
	.wallet-page {
		max-width: 1200px;
		margin: 0 auto;
	}

	.page-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 32px;
	}

	.page-header h1 {
		margin: 0;
		font-size: 28px;
		font-weight: 600;
		color: #1f2937;
	}

	.create-btn {
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

	.create-btn:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.create-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.create-btn svg {
		stroke: currentColor;
	}

	.empty-hint {
		background: #fef3c7;
		border: 1px solid #fcd34d;
		color: #92400e;
		padding: 16px 20px;
		border-radius: 8px;
		margin-bottom: 24px;
	}

	.empty-hint p {
		margin: 0 0 8px 0;
		font-size: 14px;
	}

	.link {
		color: #b45309;
		font-weight: 500;
		text-decoration: underline;
	}

	.loading,
	.error {
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

	.error-hint {
		margin: 12px 0 0 0;
		font-size: 13px;
		color: #6b7280;
	}

	.error-hint code {
		background: #f3f4f6;
		padding: 2px 6px;
		border-radius: 4px;
		font-size: 12px;
	}

	.empty-state {
		text-align: center;
		padding: 60px 20px;
		background: white;
		border-radius: 12px;
		border: 2px dashed #e5e7eb;
	}

	.empty-state p {
		margin: 0 0 24px 0;
		color: #6b7280;
		font-size: 16px;
	}

	.wallet-list {
		display: flex;
		flex-direction: column;
		gap: 24px;
		overflow: visible;
	}

	.list-card {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		overflow: visible;
	}

	.wallet-item {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 20px 24px;
		border-bottom: 1px solid #e5e7eb;
		transition: background 0.2s;
		overflow: visible;
	}

	.wallet-item:last-child {
		border-bottom: none;
	}

	.wallet-item:hover {
		background: #f9fafb;
	}

	.wallet-info {
		flex: 1;
	}

	.wallet-name {
		margin: 0 0 8px 0;
		font-size: 16px;
		font-weight: 600;
		color: #1f2937;
	}

	.wallet-meta {
		margin: 0;
		font-size: 14px;
		color: #6b7280;
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}

	.api-label {
		font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
		font-size: 12px;
		background: #f3f4f6;
		padding: 2px 8px;
		border-radius: 4px;
		color: #4b5563;
	}

	.type-badge {
		display: inline-block;
		padding: 2px 8px;
		background: #e0e7ff;
		color: #4338ca;
		border-radius: 4px;
		font-size: 12px;
		font-weight: 500;
	}

	.inactive {
		color: #9ca3af;
		font-size: 12px;
	}

	.wallet-actions {
		position: relative;
		z-index: 1;
	}

	.menu-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 36px;
		height: 36px;
		border: none;
		background: transparent;
		border-radius: 6px;
		cursor: pointer;
		color: #6b7280;
		transition: all 0.2s;
	}

	.menu-btn:hover {
		background: #f3f4f6;
		color: #374151;
	}

	.menu-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.menu-dropdown {
		position: absolute;
		top: 100%;
		right: 0;
		margin-top: 8px;
		background: white;
		border: 1px solid #e5e7eb;
		border-radius: 8px;
		box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
		min-width: 160px;
		z-index: 1000;
	}

	.menu-item {
		display: flex;
		align-items: center;
		gap: 8px;
		width: 100%;
		padding: 12px 16px;
		border: none;
		background: transparent;
		color: #374151;
		font-size: 14px;
		text-align: left;
		cursor: pointer;
		transition: background 0.2s;
	}

	.menu-item:hover:not(:disabled) {
		background: #f3f4f6;
	}

	.menu-item.delete {
		color: #ef4444;
	}

	.menu-item.delete:hover:not(:disabled) {
		background: #fef2f2;
	}

	.menu-item svg {
		stroke: currentColor;
	}

	@media (max-width: 768px) {
		.page-header {
			flex-direction: column;
			align-items: flex-start;
			gap: 16px;
		}

		.wallet-item {
			flex-direction: column;
			align-items: flex-start;
			gap: 16px;
		}

		.wallet-actions {
			align-self: flex-end;
		}
	}
</style>
