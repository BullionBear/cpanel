<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getAccountsV2,
		getApiKeysV2,
		getWalletsV2,
		getWalletTypes,
		renameAccountV2,
		createWalletV2,
		deleteApiKeyV2,
		getSymbols,
		getSymbolsByExchange,
		getCursorsV2,
		createCursorV2,
		deleteCursorV2,
		type AccountV2,
		type APIKeyV2,
		type WalletV2,
		type WalletType,
		type SymbolInfo,
		type CursorV2
	} from '$lib/api';
	import RegisterAPIKeyModalV2 from '../../../components/RegisterAPIKeyModalV2.svelte';

	let accounts: AccountV2[] = [];
	let apiKeys: APIKeyV2[] = [];
	let wallets: WalletV2[] = [];
	let walletTypes: WalletType[] = [];
	let loading = true;
	let error: string | null = null;
	let showRegisterModal = false;

	// Collapsible state: exchange name -> open, account id -> open
	let openExchanges: Record<string, boolean> = {};
	let openAccounts: Record<number, boolean> = {};

	// Account widget: menu and dialogs
	let openMenuAcctId: number | null = null;
	let renameDialogAcct: AccountV2 | null = null;
	let renameName = '';
	let renameLoading = false;
	let renameError: string | null = null;
	let walletDialogAcct: AccountV2 | null = null;
	let walletCategoryId: number | null = null;
	let walletName = '';
	let walletLoading = false;
	let walletError: string | null = null;
	let deleteDialogAcct: AccountV2 | null = null;
	let deleteSelectedApiId: number | null = null;
	let deleteLoading = false;
	let deleteError: string | null = null;

	let tickerDialogAcct: AccountV2 | null = null;
	let tickerSymbols: SymbolInfo[] = [];
	let tickerFilter = '';
	let tickerLoading = false;
	let tickerError: string | null = null;

	let cursors: CursorV2[] = [];
	let symbolMap: Record<number, SymbolInfo> = {};
	let allSymbols: SymbolInfo[] = [];

	onMount(() => {
		loadAll();
		if (typeof document !== 'undefined') {
			const closeMenu = (e: MouseEvent) => {
				const t = e.target as HTMLElement;
				if (t && !t.closest('.account-widget')) openMenuAcctId = null;
			};
			document.addEventListener('click', closeMenu);
			return () => document.removeEventListener('click', closeMenu);
		}
		return undefined;
	});

	async function loadAll() {
		loading = true;
		error = null;
		try {
			const [accts, keys, wals, wtypes, syms, curs] = await Promise.all([
				getAccountsV2(),
				getApiKeysV2(),
				getWalletsV2(),
				getWalletTypes(),
				getSymbols(500).catch(() => [] as SymbolInfo[]),
				getCursorsV2().catch(() => [] as CursorV2[])
			]);
			accounts = accts;
			apiKeys = keys;
			wallets = wals;
			walletTypes = wtypes;
			allSymbols = syms;
			cursors = curs;
			symbolMap = {};
			for (const s of allSymbols) symbolMap[s.id] = s;
			// Default open first exchange and first account per exchange
			const exchanges = [...new Set(accounts.map((a) => a.exchange))];
			exchanges.forEach((ex, i) => {
				openExchanges[ex] = i === 0;
			});
			accounts.forEach((acct, i) => {
				const isFirstInExchange =
					accounts.findIndex((a) => a.exchange === acct.exchange) === i;
				openAccounts[acct.id] = isFirstInExchange;
			});
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load account data';
		} finally {
			loading = false;
		}
	}

	function toggleExchange(exchange: string) {
		openExchanges[exchange] = !openExchanges[exchange];
		openExchanges = { ...openExchanges };
	}

	function toggleAccount(acctId: number) {
		openAccounts[acctId] = !openAccounts[acctId];
		openAccounts = { ...openAccounts };
	}

	// Map API key id -> account (exchange + uid match)
	$: apiKeyToAccount = (() => {
		const m: Record<number, AccountV2> = {};
		for (const api of apiKeys) {
			const acct = accounts.find(
				(a) => a.exchange === api.exchange && Number(a.uid) === Number(api.uid)
			);
			if (acct) m[api.id] = acct;
		}
		return m;
	})();

	// Category id -> slug (from v1 wallet types)
	$: categorySlug = walletTypes.reduce<Record<number, string>>(
		(acc, wt) => {
			acc[wt.id] = wt.slug || wt.name || '';
			return acc;
		},
		{}
	);

	// Group accounts by exchange
	$: byExchange = accounts.reduce<Record<string, AccountV2[]>>(
		(acc, acct) => {
			const ex = acct.exchange || 'Unknown';
			if (!acc[ex]) acc[ex] = [];
			acc[ex].push(acct);
			return acc;
		},
		{}
	);
	$: exchangeNames = Object.keys(byExchange).sort();

	// For each account: APIs (same exchange + uid), wallets (same acct_id)
	function getApisForAccount(acct: AccountV2): APIKeyV2[] {
		return apiKeys.filter(
			(api) =>
				api.exchange === acct.exchange && Number(api.uid) === Number(acct.uid)
		);
	}

	function getWalletsForAccount(acct: AccountV2): WalletV2[] {
		return wallets.filter((w) => w.acct_id === acct.id);
	}

	function maskKey(key: string): string {
		if (!key || key.length < 8) return '•••••••••••••••';
		return '•••••••••••••••' + key.slice(-4);
	}

	// Wallet card style class from category slug (SPOT -> spot, FUTURES/UNIFIED/PERP -> perp, MARGIN -> margin)
	function walletCardClass(slug: string): string {
		const s = (slug || '').toUpperCase();
		if (s === 'SPOT') return 'spot';
		if (s === 'MARGIN') return 'margin';
		if (['FUTURES', 'UNIFIED', 'PERP', 'FUNDING'].includes(s)) return 'perp';
		return 'default';
	}

	function handleRegisterSuccess() {
		loadAll();
	}

	function openMenu(acctId: number, e: MouseEvent) {
		e.stopPropagation();
		openMenuAcctId = openMenuAcctId === acctId ? null : acctId;
	}

	function openRenameDialog(acct: AccountV2, e: MouseEvent) {
		e.stopPropagation();
		openMenuAcctId = null;
		renameDialogAcct = acct;
		renameName = acct.name;
		renameError = null;
	}

	function closeRenameDialog() {
		if (!renameLoading) {
			renameDialogAcct = null;
			renameName = '';
			renameError = null;
		}
	}

	async function submitRename() {
		if (!renameDialogAcct || !renameName.trim()) return;
		renameLoading = true;
		renameError = null;
		try {
			await renameAccountV2(renameDialogAcct.id, renameName.trim());
			await loadAll();
			closeRenameDialog();
		} catch (e) {
			renameError = e instanceof Error ? e.message : 'Failed to rename';
		} finally {
			renameLoading = false;
		}
	}

	function openWalletDialog(acct: AccountV2, e: MouseEvent) {
		e.stopPropagation();
		openMenuAcctId = null;
		walletDialogAcct = acct;
		walletCategoryId = walletTypes.length > 0 ? walletTypes[0].id : null;
		walletName = '';
		walletError = null;
	}

	function closeWalletDialog() {
		if (!walletLoading) {
			walletDialogAcct = null;
			walletCategoryId = null;
			walletName = '';
			walletError = null;
		}
	}

	async function submitWallet() {
		if (!walletDialogAcct || walletCategoryId == null) return;
		walletLoading = true;
		walletError = null;
		try {
			await createWalletV2(walletDialogAcct.id, walletCategoryId, walletName.trim() || undefined);
			await loadAll();
			closeWalletDialog();
		} catch (e) {
			walletError = e instanceof Error ? e.message : 'Failed to create wallet';
		} finally {
			walletLoading = false;
		}
	}

	function openDeleteDialog(acct: AccountV2, e: MouseEvent) {
		e.stopPropagation();
		openMenuAcctId = null;
		deleteDialogAcct = acct;
		const apis = getApisForAccount(acct);
		deleteSelectedApiId = apis.length > 0 ? apis[0].id : null;
		deleteError = null;
	}

	function closeDeleteDialog() {
		if (!deleteLoading) {
			deleteDialogAcct = null;
			deleteSelectedApiId = null;
			deleteError = null;
		}
	}

	async function submitDelete() {
		if (!deleteDialogAcct || deleteSelectedApiId == null) return;
		deleteLoading = true;
		deleteError = null;
		try {
			await deleteApiKeyV2(deleteSelectedApiId);
			await loadAll();
			closeDeleteDialog();
		} catch (e) {
			deleteError = e instanceof Error ? e.message : 'Failed to delete API key';
		} finally {
			deleteLoading = false;
		}
	}

	function getCursorsForAccount(acctId: number): CursorV2[] {
		return cursors.filter((c) => c.acct_id === acctId);
	}

	function getTickerName(symbolId: number): string {
		return symbolMap[symbolId]?.universal_ticker ?? `symbol#${symbolId}`;
	}

	async function openTickerDialog(acct: AccountV2, e: MouseEvent) {
		e.stopPropagation();
		openMenuAcctId = null;
		tickerDialogAcct = acct;
		tickerFilter = '';
		tickerError = null;
		tickerLoading = true;
		try {
			tickerSymbols = await getSymbolsByExchange(acct.exch_id, 500);
		} catch (err) {
			tickerSymbols = allSymbols.filter((s) => s.exchange.id === acct.exch_id);
		} finally {
			tickerLoading = false;
		}
	}

	function closeTickerDialog() {
		if (!tickerLoading) {
			tickerDialogAcct = null;
			tickerSymbols = [];
			tickerFilter = '';
			tickerError = null;
		}
	}

	$: filteredTickers = tickerSymbols.filter((s) =>
		s.universal_ticker.toLowerCase().includes(tickerFilter.toLowerCase())
	);

	$: acctCursorSymbolIds = (() => {
		if (!tickerDialogAcct) return new Set<number>();
		return new Set(
			cursors.filter((c) => c.acct_id === tickerDialogAcct!.id).map((c) => c.symbol_id)
		);
	})();

	async function toggleTicker(sym: SymbolInfo) {
		if (!tickerDialogAcct) return;
		tickerLoading = true;
		tickerError = null;
		const isAssociated = acctCursorSymbolIds.has(sym.id);
		try {
			if (isAssociated) {
				await deleteCursorV2(tickerDialogAcct.id, sym.id);
			} else {
				await createCursorV2(tickerDialogAcct.id, sym.id);
			}
			cursors = await getCursorsV2().catch(() => cursors);
		} catch (err) {
			tickerError = err instanceof Error ? err.message : (isAssociated ? 'Failed to remove ticker' : 'Failed to associate ticker');
			tickerLoading = false;
			return;
		}
		tickerLoading = false;
	}
</script>

<div class="account-page">
	<div class="dashboard-header">
		<div class="header-left">
			<span class="header-icon">🏦</span>
			<h1>Account</h1>
		</div>
		<button type="button" class="register-btn" on:click={() => (showRegisterModal = true)}>
			<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			Register
		</button>
	</div>

	<RegisterAPIKeyModalV2
		bind:isOpen={showRegisterModal}
		on:close={() => {}}
		on:success={handleRegisterSuccess}
	/>

	{#if loading}
		<div class="loading">Loading account data...</div>
	{:else if error}
		<div class="error">{error}</div>
	{:else if exchangeNames.length === 0}
		<div class="empty-state">
			<p>No accounts yet. Register an API key to see your exchange accounts here.</p>
		</div>
	{:else}
		{#each exchangeNames as exchange}
			{@const accts = byExchange[exchange]}
			<div class="exchange-card">
				<button
					type="button"
					class="exchange-header"
					on:click={() => toggleExchange(exchange)}
					aria-expanded={openExchanges[exchange] ?? true}
				>
					<div class="exchange-info">
						<span
							class="arrow"
							class:down={openExchanges[exchange] ?? true}
							class:right={!(openExchanges[exchange] ?? true)}
						></span>
						<span class="exchange-icon">🌐</span>
						<span class="exchange-name">{exchange}</span>
						<span class="exchange-badge"
							>{accts.length} {accts.length === 1 ? 'Account' : 'Accounts'}</span
						>
					</div>
				</button>

				{#if openExchanges[exchange] ?? true}
					<div class="exchange-content">
						{#each accts as acct (acct.id)}
							{@const acctApis = getApisForAccount(acct)}
							{@const acctWallets = getWalletsForAccount(acct)}
							<div class="account-card">
								<div class="account-header">
									<button
										type="button"
										class="account-header-toggle"
										on:click={() => toggleAccount(acct.id)}
										aria-expanded={openAccounts[acct.id] ?? true}
									>
										<div class="account-info">
											<span
												class="arrow"
												class:down={openAccounts[acct.id] ?? true}
												class:right={!(openAccounts[acct.id] ?? true)}
											></span>
											<span class="account-name">{acct.name}</span>
										</div>
										<div class="api-badges">
											{#each acctApis as api (api.id)}
												<span
													class="api-badge"
													class:read={api.api_type === 'HMAC'}
													class:trade={api.api_type === 'ED25519'}
													class:other={api.api_type === 'RSA'}
												>
													{api.api_type || 'API'}
												</span>
											{/each}
										</div>
									</button>
									<div class="account-widget">
										<button
											type="button"
											class="widget-btn"
											on:click={(e) => openMenu(acct.id, e)}
											aria-haspopup="true"
											aria-expanded={openMenuAcctId === acct.id}
											title="Account actions"
										>
											<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
												<circle cx="12" cy="5" r="2" fill="currentColor"/>
												<circle cx="12" cy="12" r="2" fill="currentColor"/>
												<circle cx="12" cy="19" r="2" fill="currentColor"/>
											</svg>
										</button>
										{#if openMenuAcctId === acct.id}
											<div class="widget-dropdown" role="menu">
												<button type="button" class="widget-item" role="menuitem" on:click={(e) => openRenameDialog(acct, e)}>
													Rename
												</button>
											<button type="button" class="widget-item" role="menuitem" on:click={(e) => openWalletDialog(acct, e)}>
												Associate Wallet
											</button>
											<button type="button" class="widget-item" role="menuitem" on:click={(e) => openTickerDialog(acct, e)}>
												Associate Ticker
											</button>
											<button type="button" class="widget-item widget-item-danger" role="menuitem" on:click={(e) => openDeleteDialog(acct, e)}>
												Delete
											</button>
											</div>
										{/if}
									</div>
								</div>

								{#if openAccounts[acct.id] ?? true}
									<div class="account-content">
										<!-- API Keys -->
										{#if acctApis.length > 0}
											<div class="api-keys-detail">
												<div class="api-keys-title">API Keys</div>
												<div class="api-keys-grid">
													{#each acctApis as api (api.id)}
														<div class="api-key-item">
															<span>{api.name || api.api_type}:</span>
															<code>{maskKey(api.api_key)}</code>
														</div>
													{/each}
												</div>
											</div>
										{/if}

										<!-- Wallets grid: from GET v2/account/wallet -->
										{#if acctWallets.length > 0}
											<div class="wallets-grid">
												{#each acctWallets as wallet (wallet.id)}
													{@const slug = categorySlug[wallet.category_id] || 'Wallet'}
													<div class="wallet-card {walletCardClass(slug)}">
														<div class="wallet-header">
															<span class="wallet-type-badge">{slug}</span>
															<span class="wallet-title">{wallet.name}</span>
														</div>
													</div>
												{/each}
											</div>
									{:else}
										<p class="no-wallets">No wallets for this account.</p>
									{/if}

									{#if getCursorsForAccount(acct.id).length > 0}
										<div class="cursors-section">
											<div class="cursors-title">Associated Tickers</div>
											<div class="cursors-list">
												{#each getCursorsForAccount(acct.id) as cur}
													<span class="cursor-tag">{getTickerName(cur.symbol_id)}</span>
												{/each}
											</div>
										</div>
									{/if}
								</div>
							{/if}
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/each}
	{/if}

	<!-- Rename dialog -->
	{#if renameDialogAcct}
		<div class="dialog-backdrop" role="presentation" on:click={(e) => e.target === e.currentTarget && closeRenameDialog()}>
			<div class="dialog" role="dialog" aria-labelledby="rename-title">
				<h2 id="rename-title" class="dialog-title">Rename Account</h2>
				<form on:submit|preventDefault={submitRename}>
					<div class="form-group">
						<label for="rename-input">Name</label>
						<input id="rename-input" type="text" bind:value={renameName} disabled={renameLoading} placeholder="Account name" />
					</div>
					{#if renameError}
						<div class="dialog-error">{renameError}</div>
					{/if}
					<div class="dialog-actions">
						<button type="button" class="btn-cancel" on:click={closeRenameDialog} disabled={renameLoading}>Cancel</button>
						<button type="submit" class="btn-submit" disabled={renameLoading || !renameName.trim()}>
							{renameLoading ? 'Saving...' : 'Save'}
						</button>
					</div>
				</form>
			</div>
		</div>
	{/if}

	<!-- Associate Wallet dialog -->
	{#if walletDialogAcct}
		<div class="dialog-backdrop" role="presentation" on:click={(e) => e.target === e.currentTarget && closeWalletDialog()}>
			<div class="dialog" role="dialog" aria-labelledby="wallet-title">
				<h2 id="wallet-title" class="dialog-title">Associate Wallet</h2>
				<p class="dialog-desc">Create a wallet for this account by selecting a wallet type.</p>
				<form on:submit|preventDefault={submitWallet}>
					<div class="form-group">
						<label for="wallet-type">Wallet Type *</label>
						<select id="wallet-type" bind:value={walletCategoryId} disabled={walletLoading} required>
							{#each walletTypes as wt}
								<option value={wt.id}>{wt.name} ({wt.slug})</option>
							{/each}
						</select>
					</div>
					<div class="form-group">
						<label for="wallet-name">Name (optional)</label>
						<input id="wallet-name" type="text" bind:value={walletName} disabled={walletLoading} placeholder="Leave empty for default" />
					</div>
					{#if walletError}
						<div class="dialog-error">{walletError}</div>
					{/if}
					<div class="dialog-actions">
						<button type="button" class="btn-cancel" on:click={closeWalletDialog} disabled={walletLoading}>Cancel</button>
						<button type="submit" class="btn-submit" disabled={walletLoading || walletCategoryId == null}>
							{walletLoading ? 'Creating...' : 'Create'}
						</button>
					</div>
				</form>
			</div>
		</div>
	{/if}

	<!-- Associate Ticker dialog -->
	{#if tickerDialogAcct}
		<div class="dialog-backdrop" role="presentation" on:click={(e) => e.target === e.currentTarget && closeTickerDialog()}>
			<div class="dialog ticker-dialog" role="dialog" aria-labelledby="ticker-title">
				<h2 id="ticker-title" class="dialog-title">Associate Ticker</h2>
				<p class="dialog-desc">Select a ticker to associate with <strong>{tickerDialogAcct.name}</strong>.</p>
				<div class="form-group">
					<input
						type="text"
						bind:value={tickerFilter}
						placeholder="Filter tickers..."
						disabled={tickerLoading}
					/>
				</div>
				{#if tickerError}
					<div class="dialog-error">{tickerError}</div>
				{/if}
				{#if tickerLoading && tickerSymbols.length === 0}
					<div class="ticker-loading">Loading tickers...</div>
				{:else}
					<div class="ticker-list">
						{#each filteredTickers as sym (sym.id)}
							{@const alreadyAssociated = acctCursorSymbolIds.has(sym.id)}
							<button
								type="button"
								class="ticker-item"
								class:associated={alreadyAssociated}
								disabled={tickerLoading}
								on:click={() => toggleTicker(sym)}
							>
								<span class="ticker-name">{sym.universal_ticker}</span>
								{#if alreadyAssociated}
									<span class="ticker-check">&#10003;</span>
								{/if}
							</button>
						{/each}
						{#if filteredTickers.length === 0 && !tickerLoading}
							<p class="ticker-empty">No tickers found.</p>
						{/if}
					</div>
				{/if}
				<div class="dialog-actions">
					<button type="button" class="btn-cancel" on:click={closeTickerDialog} disabled={tickerLoading}>Close</button>
				</div>
			</div>
		</div>
	{/if}

	<!-- Delete API key dialog -->
	{#if deleteDialogAcct}
		{@const deleteApis = getApisForAccount(deleteDialogAcct)}
		<div class="dialog-backdrop" role="presentation" on:click={(e) => e.target === e.currentTarget && closeDeleteDialog()}>
			<div class="dialog" role="dialog" aria-labelledby="delete-title">
				<h2 id="delete-title" class="dialog-title">Delete API Key</h2>
				<p class="dialog-desc">Choose which API key to delete from this account.</p>
				{#if deleteApis.length === 0}
					<p class="dialog-muted">No API keys for this account.</p>
					<div class="dialog-actions">
						<button type="button" class="btn-cancel" on:click={closeDeleteDialog}>Close</button>
					</div>
				{:else}
					<div class="form-group">
						<label for="delete-api">API Key to delete</label>
						<select id="delete-api" bind:value={deleteSelectedApiId} disabled={deleteLoading}>
							{#each deleteApis as api (api.id)}
								<option value={api.id}>{api.name} ({maskKey(api.api_key)})</option>
							{/each}
						</select>
					</div>
					{#if deleteError}
						<div class="dialog-error">{deleteError}</div>
					{/if}
					<div class="dialog-actions">
						<button type="button" class="btn-cancel" on:click={closeDeleteDialog} disabled={deleteLoading}>Cancel</button>
						<button type="button" class="btn-danger" on:click={submitDelete} disabled={deleteLoading || deleteSelectedApiId == null}>
							{deleteLoading ? 'Deleting...' : 'Delete'}
						</button>
					</div>
				{/if}
			</div>
		</div>
	{/if}
</div>

<style>
	.account-page {
		max-width: 1200px;
		margin: 0 auto;
		padding: 0 4px;
	}

	.dashboard-header {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 16px;
		margin-bottom: 24px;
	}

	.header-left {
		display: flex;
		align-items: center;
		gap: 8px;
	}

	.dashboard-header h1 {
		font-size: 24px;
		font-weight: 700;
		color: #111827;
		margin: 0;
	}

	.header-icon {
		font-size: 24px;
	}

	.register-btn {
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

	.register-btn:hover {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.register-btn svg {
		stroke: currentColor;
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
	}

	.empty-state {
		text-align: center;
		padding: 60px 20px;
		background: white;
		border-radius: 12px;
		border: 2px dashed #e5e7eb;
	}

	.empty-state p {
		margin: 0;
		color: #6b7280;
		font-size: 16px;
	}

	.exchange-card {
		background: white;
		border-radius: 8px;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
		margin-bottom: 16px;
		overflow: hidden;
	}

	.exchange-header {
		width: 100%;
		background: linear-gradient(to right, #eff6ff, #eef2ff);
		padding: 16px 20px;
		cursor: pointer;
		display: flex;
		justify-content: space-between;
		align-items: center;
		border: none;
		text-align: left;
		transition: background-color 0.2s;
		font: inherit;
	}

	.exchange-header:hover {
		background: linear-gradient(to right, #dbeafe, #e0e7ff);
	}

	.exchange-info {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.arrow {
		width: 20px;
		height: 20px;
		display: inline-block;
		position: relative;
		transition: transform 0.2s;
		flex-shrink: 0;
	}

	.arrow::before {
		content: '';
		position: absolute;
		top: 50%;
		left: 50%;
		width: 8px;
		height: 8px;
		border-right: 2px solid #4b5563;
		border-bottom: 2px solid #4b5563;
	}

	.arrow.right::before {
		transform: translate(-50%, -50%) rotate(-45deg);
	}

	.arrow.down::before {
		transform: translate(-50%, -50%) rotate(45deg);
	}

	.exchange-icon {
		font-size: 20px;
	}

	.exchange-name {
		font-weight: 600;
		font-size: 18px;
		color: #111827;
	}

	.exchange-badge {
		background: #e5e7eb;
		padding: 4px 8px;
		border-radius: 4px;
		font-size: 14px;
		color: #4b5563;
	}

	.exchange-content {
		padding: 20px;
		border-top: 1px solid #e5e7eb;
	}

	.account-card {
		border: 1px solid #e5e7eb;
		border-radius: 6px;
		margin-bottom: 12px;
		overflow: hidden;
	}

	.account-card:last-child {
		margin-bottom: 0;
	}

	.account-header {
		display: flex;
		align-items: center;
		width: 100%;
		background: #f9fafb;
		padding: 12px 16px;
		transition: background-color 0.2s;
	}

	.account-header-toggle {
		flex: 1;
		display: flex;
		justify-content: space-between;
		align-items: center;
		border: none;
		background: transparent;
		cursor: pointer;
		text-align: left;
		font: inherit;
		padding: 0;
		min-width: 0;
	}

	.account-header-toggle:hover {
		background: transparent;
	}

	.account-header:hover {
		background: #f3f4f6;
	}

	.account-widget {
		position: relative;
		flex-shrink: 0;
		margin-left: 8px;
	}

	.widget-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 32px;
		border: none;
		background: transparent;
		border-radius: 6px;
		cursor: pointer;
		color: #6b7280;
		transition: all 0.2s;
	}

	.widget-btn:hover {
		background: #e5e7eb;
		color: #374151;
	}

	.widget-dropdown {
		position: absolute;
		top: 100%;
		right: 0;
		margin-top: 4px;
		background: white;
		border: 1px solid #e5e7eb;
		border-radius: 8px;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
		min-width: 160px;
		z-index: 50;
		padding: 4px;
	}

	.widget-item {
		display: block;
		width: 100%;
		padding: 8px 12px;
		border: none;
		background: transparent;
		text-align: left;
		font-size: 14px;
		color: #374151;
		cursor: pointer;
		border-radius: 4px;
	}

	.widget-item:hover {
		background: #f3f4f6;
	}

	.widget-item-danger {
		color: #dc2626;
	}

	.widget-item-danger:hover {
		background: #fef2f2;
	}

	.account-info {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.account-name {
		font-weight: 500;
		color: #111827;
	}

	.api-badges {
		display: flex;
		gap: 8px;
		flex-wrap: wrap;
	}

	.api-badge {
		display: inline-flex;
		align-items: center;
		gap: 4px;
		padding: 4px 8px;
		border-radius: 4px;
		font-size: 12px;
		font-weight: 500;
	}

	.api-badge.read {
		background: #dcfce7;
		color: #166534;
	}

	.api-badge.trade {
		background: #fef9c3;
		color: #854d0e;
	}

	.api-badge.other {
		background: #f3f4f6;
		color: #1f2937;
	}

	.account-content {
		padding: 16px;
		border-top: 1px solid #e5e7eb;
		background: white;
	}

	.api-keys-detail {
		background: #f9fafb;
		padding: 12px;
		border-radius: 6px;
		margin-bottom: 16px;
		font-size: 12px;
	}

	.api-keys-title {
		display: flex;
		align-items: center;
		gap: 4px;
		font-weight: 500;
		margin-bottom: 8px;
		color: #374151;
	}

	.api-keys-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 16px;
	}

	.api-key-item {
		color: #4b5563;
	}

	.api-key-item span {
		text-transform: capitalize;
	}

	.api-key-item code {
		font-family: 'Courier New', monospace;
		background: white;
		padding: 2px 4px;
		border-radius: 4px;
		border: 1px solid #e5e7eb;
		margin-left: 4px;
		font-size: 11px;
	}

	.wallets-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
		gap: 16px;
	}

	.wallet-card {
		padding: 16px;
		border-radius: 6px;
		border: 1px solid;
	}

	.wallet-card.spot {
		background: #eff6ff;
		border-color: #bfdbfe;
	}

	.wallet-card.perp {
		background: #faf5ff;
		border-color: #e9d5ff;
	}

	.wallet-card.margin {
		background: #fff7ed;
		border-color: #fed7aa;
	}

	.wallet-card.default {
		background: #f9fafb;
		border-color: #e5e7eb;
	}

	.wallet-header {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
		gap: 6px;
	}

	.wallet-type-badge {
		font-size: 11px;
		font-weight: 600;
		text-transform: uppercase;
		color: #6b7280;
		letter-spacing: 0.02em;
	}

	.wallet-title {
		font-weight: 500;
		color: #111827;
		font-size: 14px;
	}

	.no-wallets {
		margin: 0;
		color: #6b7280;
		font-size: 14px;
	}

	/* Cursors section */
	.cursors-section {
		margin-top: 16px;
		padding-top: 12px;
		border-top: 1px solid #f3f4f6;
	}

	.cursors-title {
		font-size: 12px;
		font-weight: 500;
		color: #374151;
		margin-bottom: 8px;
	}

	.cursors-list {
		display: flex;
		flex-wrap: wrap;
		gap: 6px;
	}

	.cursor-tag {
		display: inline-block;
		padding: 3px 10px;
		background: #eef2ff;
		border: 1px solid #c7d2fe;
		border-radius: 12px;
		font-size: 12px;
		font-weight: 500;
		color: #4338ca;
	}

	/* Ticker dialog */
	.ticker-dialog {
		max-width: 480px;
	}

	.ticker-loading {
		text-align: center;
		padding: 20px;
		color: #6b7280;
		font-size: 14px;
	}

	.ticker-list {
		max-height: 320px;
		overflow-y: auto;
		border: 1px solid #e5e7eb;
		border-radius: 6px;
		margin-bottom: 8px;
	}

	.ticker-item {
		display: flex;
		justify-content: space-between;
		align-items: center;
		width: 100%;
		padding: 10px 12px;
		border: none;
		border-bottom: 1px solid #f3f4f6;
		background: white;
		text-align: left;
		font-size: 13px;
		cursor: pointer;
		transition: background 0.15s;
	}

	.ticker-item:last-child {
		border-bottom: none;
	}

	.ticker-item:hover:not(:disabled) {
		background: #f0f4ff;
	}

	.ticker-item.associated {
		background: #f0fdf4;
		cursor: default;
	}

	.ticker-name {
		font-weight: 500;
		color: #111827;
	}

	.ticker-item.associated .ticker-name {
		color: #15803d;
	}

	.ticker-check {
		color: #16a34a;
		font-size: 14px;
		font-weight: 700;
	}

	.ticker-empty {
		text-align: center;
		padding: 20px;
		color: #9ca3af;
		font-size: 14px;
		margin: 0;
	}

	/* Dialogs */
	.dialog-backdrop {
		position: fixed;
		inset: 0;
		background: rgba(0, 0, 0, 0.5);
		display: flex;
		align-items: center;
		justify-content: center;
		z-index: 1000;
		padding: 20px;
	}

	.dialog {
		background: white;
		border-radius: 12px;
		width: 100%;
		max-width: 420px;
		box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1);
		padding: 24px;
	}

	.dialog-title {
		margin: 0 0 16px 0;
		font-size: 18px;
		font-weight: 600;
		color: #111827;
	}

	.dialog-desc {
		margin: 0 0 16px 0;
		font-size: 14px;
		color: #6b7280;
	}

	.dialog-muted {
		margin: 0 0 16px 0;
		font-size: 14px;
		color: #9ca3af;
	}

	.dialog .form-group {
		margin-bottom: 16px;
	}

	.dialog .form-group label {
		display: block;
		margin-bottom: 6px;
		font-size: 14px;
		font-weight: 500;
		color: #374151;
	}

	.dialog .form-group input,
	.dialog .form-group select {
		width: 100%;
		padding: 8px 12px;
		border: 1px solid #d1d5db;
		border-radius: 6px;
		font-size: 14px;
		box-sizing: border-box;
	}

	.dialog-error {
		padding: 10px 12px;
		background: #fef2f2;
		border: 1px solid #fecaca;
		border-radius: 6px;
		color: #dc2626;
		font-size: 14px;
		margin-bottom: 16px;
	}

	.dialog-actions {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		margin-top: 20px;
		padding-top: 16px;
		border-top: 1px solid #e5e7eb;
	}

	.dialog .btn-cancel {
		padding: 8px 16px;
		border-radius: 6px;
		font-size: 14px;
		font-weight: 500;
		border: none;
		background: #f3f4f6;
		color: #374151;
		cursor: pointer;
	}

	.dialog .btn-cancel:hover:not(:disabled) {
		background: #e5e7eb;
	}

	.dialog .btn-submit {
		padding: 8px 16px;
		border-radius: 6px;
		font-size: 14px;
		font-weight: 500;
		border: none;
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
		cursor: pointer;
	}

	.dialog .btn-submit:hover:not(:disabled) {
		opacity: 0.95;
	}

	.dialog .btn-danger {
		padding: 8px 16px;
		border-radius: 6px;
		font-size: 14px;
		font-weight: 500;
		border: none;
		background: #dc2626;
		color: white;
		cursor: pointer;
	}

	.dialog .btn-danger:hover:not(:disabled) {
		background: #b91c1c;
	}

	.dialog .btn-cancel:disabled,
	.dialog .btn-submit:disabled,
	.dialog .btn-danger:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	@media (max-width: 768px) {
		.wallets-grid {
			grid-template-columns: 1fr;
		}

		.api-keys-grid {
			grid-template-columns: 1fr;
		}

		.exchange-header,
		.account-header {
			flex-wrap: wrap;
			gap: 8px;
		}

		.api-badges {
			width: 100%;
			justify-content: flex-start;
		}
	}
</style>
