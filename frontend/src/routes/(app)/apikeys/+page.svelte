<script lang="ts">
	import { onMount } from 'svelte';
	import { getAPIKeys, deleteAPIKey, getAPIToken, createAPIToken, deleteAPIToken, type APIKey } from '$lib/api';
	import RegisterAPIKeyModal from '../../../components/RegisterAPIKeyModal.svelte';

	let apiKeys: APIKey[] = [];
	let loading = true;
	let error: string | null = null;
	let deletingId: number | null = null;
	let showMenuId: number | null = null;
	let showRegisterModal = false;

	// API Token state
	let apiToken: string | null = null;
	let loadingToken = false;
	let tokenError: string | null = null;
	let creatingToken = false;
	let deletingToken = false;

	onMount(() => {
		loadAPIKeys();
		loadAPIToken();
		
		// Close menu when clicking outside (only on client)
		if (typeof window !== 'undefined' && typeof document !== 'undefined') {
			const handleClickOutside = (event: MouseEvent) => {
				const target = event.target as HTMLElement;
				if (target && !target.closest('.api-actions')) {
					showMenuId = null;
				}
			};
			
			document.addEventListener('click', handleClickOutside);
			return () => {
				document.removeEventListener('click', handleClickOutside);
			};
		}
		return undefined;
	});

	async function loadAPIKeys() {
		loading = true;
		error = null;
		try {
			apiKeys = await getAPIKeys();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to load API keys';
		} finally {
			loading = false;
		}
	}

	async function handleDelete(id: number) {
		if (!confirm('Are you sure you want to delete this API key?')) {
			return;
		}

		deletingId = id;
		showMenuId = null;
		try {
			await deleteAPIKey(id);
			await loadAPIKeys();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to delete API key';
		} finally {
			deletingId = null;
		}
	}

	function toggleMenu(id: number, event: MouseEvent) {
		event.stopPropagation();
		showMenuId = showMenuId === id ? null : id;
	}

	function handleRegister() {
		showRegisterModal = true;
	}

	function handleModalClose() {
		showRegisterModal = false;
	}

	function handleModalSuccess() {
		// Reload API keys after successful registration
		loadAPIKeys();
	}

	// Group APIs by exchange
	$: groupedAPIs = apiKeys.reduce((acc, api) => {
		const exchange = api.exchange.toUpperCase();
		if (!acc[exchange]) {
			acc[exchange] = [];
		}
		acc[exchange].push(api);
		return acc;
	}, {} as Record<string, APIKey[]>);

	$: exchanges = Object.keys(groupedAPIs).sort();

	async function loadAPIToken() {
		loadingToken = true;
		tokenError = null;
		try {
			const tokenData = await getAPIToken();
			apiToken = tokenData?.access_token || null;
		} catch (e) {
			tokenError = e instanceof Error ? e.message : 'Failed to load API token';
			apiToken = null;
		} finally {
			loadingToken = false;
		}
	}

	async function handleCreateToken() {
		creatingToken = true;
		tokenError = null;
		try {
			const tokenData = await createAPIToken();
			apiToken = tokenData.access_token;
		} catch (e) {
			tokenError = e instanceof Error ? e.message : 'Failed to create API token';
		} finally {
			creatingToken = false;
		}
	}

	async function handleDeleteToken() {
		if (!confirm('Are you sure you want to delete your API token? You will need to create a new one to use API access.')) {
			return;
		}

		deletingToken = true;
		tokenError = null;
		try {
			await deleteAPIToken();
			apiToken = null;
		} catch (e) {
			tokenError = e instanceof Error ? e.message : 'Failed to delete API token';
		} finally {
			deletingToken = false;
		}
	}

	function copyToken() {
		if (apiToken) {
			navigator.clipboard.writeText(apiToken);
			// You could add a toast notification here
		}
	}
</script>

<div class="api-page">
	<div class="page-header">
		<h1>API Keys</h1>
		<button class="register-btn" on:click={handleRegister}>
			<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			Register
		</button>
	</div>

	<!-- API Token Section -->
	<div class="api-token-section">
		<h2 class="section-title">API Token</h2>
		<p class="section-description">Use this token to authenticate API requests. This token never expires unless you delete it.</p>
		
		{#if tokenError}
			<div class="token-error">{tokenError}</div>
		{/if}

		<div class="token-container">
			<div class="token-input-wrapper">
				<input
					type="text"
					class="token-input"
					value={apiToken || ''}
					readonly
					placeholder={loadingToken ? 'Loading...' : 'No API token. Click "Create" to generate one.'}
					disabled={loadingToken}
				/>
				{#if apiToken}
					<button class="copy-btn" on:click={copyToken} title="Copy to clipboard">
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
							<path d="M16 1H4C2.9 1 2 1.9 2 3V17H4V3H16V1ZM19 5H8C6.9 5 6 5.9 6 7V21C6 22.1 6.9 23 8 23H19C20.1 23 21 22.1 21 21V7C21 5.9 20.1 5 19 5ZM19 21H8V7H19V21Z" fill="currentColor"/>
						</svg>
					</button>
				{/if}
			</div>
			<div class="token-actions">
				{#if apiToken}
					<button 
						class="token-btn delete-token-btn" 
						on:click={handleDeleteToken}
						disabled={deletingToken || loadingToken}
					>
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
							<path d="M3 6H5H21M8 6V4C8 3.46957 8.21071 2.96086 8.58579 2.58579C8.96086 2.21071 9.46957 2 10 2H14C14.5304 2 15.0391 2.21071 15.4142 2.58579C15.7893 2.96086 16 3.46957 16 4V6M19 6V20C19 20.5304 18.7893 21.0391 18.4142 21.4142C18.0391 21.7893 17.5304 22 17 22H7C6.46957 22 5.96086 21.7893 5.58579 21.4142C5.21071 21.0391 5 20.5304 5 20V6H19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
						</svg>
						{deletingToken ? 'Deleting...' : 'Delete'}
					</button>
					<button 
						class="token-btn create-token-btn" 
						on:click={handleCreateToken}
						disabled={creatingToken || loadingToken}
					>
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
							<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
						</svg>
						{creatingToken ? 'Recreating...' : 'Recreate'}
					</button>
				{:else}
					<button 
						class="token-btn create-token-btn" 
						on:click={handleCreateToken}
						disabled={creatingToken || loadingToken}
					>
						<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
							<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
						</svg>
						{creatingToken ? 'Creating...' : 'Create'}
					</button>
				{/if}
			</div>
		</div>
	</div>

	{#if loading}
		<div class="loading">Loading API keys...</div>
	{:else if error}
		<div class="error">{error}</div>
	{:else if apiKeys.length === 0}
		<div class="empty-state">
			<p>No API keys registered yet.</p>
			<button class="register-btn" on:click={handleRegister}>
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
					<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				Register Your First API Key
			</button>
		</div>
	{:else}
		<div class="api-list">
			{#each exchanges as exchange}
				<div class="exchange-group">
					<h2 class="exchange-title">{exchange}</h2>
					<div class="api-items">
						{#each groupedAPIs[exchange] as api (api.id)}
							<div class="api-item">
								<div class="api-info">
									<h3 class="api-name">
										{api.name}
										<span class="api-type-badge" class:hmac={api.api_type === 'HMAC'} class:asymmetric={api.api_type === 'ED25519' || api.api_type === 'RSA'}>
											{api.api_type || 'HMAC'}
										</span>
									</h3>
									<p class="api-meta">
										<span class="uid-display">UID: {api.uid}</span>
										Created: {new Date(api.created_at).toLocaleDateString()}
										{#if api.master_uid && api.master_uid !== api.uid}
											<span class="sub-account">Sub-account</span>
										{/if}
									</p>
								</div>
								<div class="api-actions">
									<button 
										class="menu-btn" 
										on:click={(e) => toggleMenu(api.id, e)}
										disabled={deletingId === api.id}
									>
										<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
											<circle cx="12" cy="5" r="2" fill="currentColor"/>
											<circle cx="12" cy="12" r="2" fill="currentColor"/>
											<circle cx="12" cy="19" r="2" fill="currentColor"/>
										</svg>
									</button>
									{#if showMenuId === api.id}
										<div class="menu-dropdown">
											<button 
												class="menu-item delete" 
												on:click={() => handleDelete(api.id)}
												disabled={deletingId === api.id}
											>
												<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
													<path d="M3 6H5H21M8 6V4C8 3.46957 8.21071 2.96086 8.58579 2.58579C8.96086 2.21071 9.46957 2 10 2H14C14.5304 2 15.0391 2.21071 15.4142 2.58579C15.7893 2.96086 16 3.46957 16 4V6M19 6V20C19 20.5304 18.7893 21.0391 18.4142 21.4142C18.0391 21.7893 17.5304 22 17 22H7C6.46957 22 5.96086 21.7893 5.58579 21.4142C5.21071 21.0391 5 20.5304 5 20V6H19Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
												</svg>
												{deletingId === api.id ? 'Deleting...' : 'Remove'}
											</button>
										</div>
									{/if}
								</div>
							</div>
						{/each}
					</div>
				</div>
			{/each}
		</div>
	{/if}

	<RegisterAPIKeyModal 
		bind:isOpen={showRegisterModal} 
		on:close={handleModalClose}
		on:success={handleModalSuccess}
	/>
</div>

<style>
	.api-page {
		max-width: 1200px;
		margin: 0 auto;
	}

	.page-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-bottom: 32px;
	}

	.api-token-section {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		padding: 24px;
		margin-bottom: 32px;
	}

	.section-title {
		margin: 0 0 8px 0;
		font-size: 20px;
		font-weight: 600;
		color: #1f2937;
	}

	.section-description {
		margin: 0 0 20px 0;
		font-size: 14px;
		color: #6b7280;
	}

	.token-error {
		background: #fef2f2;
		border: 1px solid #fecaca;
		color: #ef4444;
		padding: 12px;
		border-radius: 8px;
		margin-bottom: 16px;
		font-size: 14px;
	}

	.token-container {
		display: flex;
		flex-direction: column;
		gap: 16px;
	}

	.token-input-wrapper {
		position: relative;
		display: flex;
		align-items: center;
	}

	.token-input {
		width: 100%;
		padding: 12px 40px 12px 16px;
		border: 1px solid #d1d5db;
		border-radius: 8px;
		font-size: 14px;
		font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
		background: #f9fafb;
		color: #1f2937;
	}

	.token-input:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.token-input::placeholder {
		color: #9ca3af;
	}

	.copy-btn {
		position: absolute;
		right: 8px;
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

	.copy-btn:hover {
		background: #f3f4f6;
		color: #374151;
	}

	.token-actions {
		display: flex;
		gap: 12px;
		justify-content: flex-end;
	}

	.token-btn {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 10px 20px;
		border: none;
		border-radius: 8px;
		font-size: 14px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
	}

	.token-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.create-token-btn {
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
	}

	.create-token-btn:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.delete-token-btn {
		background: #ef4444;
		color: white;
	}

	.delete-token-btn:hover:not(:disabled) {
		background: #dc2626;
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(239, 68, 68, 0.4);
	}

	.token-btn svg {
		stroke: currentColor;
		fill: none;
	}

	.page-header h1 {
		margin: 0;
		font-size: 28px;
		font-weight: 600;
		color: #1f2937;
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

	.api-list {
		display: flex;
		flex-direction: column;
		gap: 32px;
	}

	.exchange-group {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		overflow: visible;
		position: relative;
	}

	.exchange-title {
		margin: 0;
		padding: 20px 24px;
		font-size: 20px;
		font-weight: 600;
		color: #1f2937;
		background: #f9fafb;
		border-bottom: 1px solid #e5e7eb;
	}

	.api-items {
		display: flex;
		flex-direction: column;
		overflow: visible;
	}

	.api-item {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 20px 24px;
		border-bottom: 1px solid #e5e7eb;
		transition: background 0.2s;
	}

	.api-item:last-child {
		border-bottom: none;
	}

	.api-item:hover {
		background: #f9fafb;
	}

	.api-info {
		flex: 1;
	}

	.api-name {
		margin: 0 0 8px 0;
		font-size: 16px;
		font-weight: 600;
		color: #1f2937;
		display: flex;
		align-items: center;
		gap: 10px;
	}

	.api-type-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 4px;
		font-size: 11px;
		font-weight: 500;
		text-transform: uppercase;
	}

	.api-type-badge.hmac {
		background: #e0e7ff;
		color: #4338ca;
	}

	.api-type-badge.asymmetric {
		background: #d1fae5;
		color: #065f46;
	}

	.api-meta {
		margin: 0;
		font-size: 14px;
		color: #6b7280;
		display: flex;
		align-items: center;
		gap: 12px;
		flex-wrap: wrap;
	}

	.uid-display {
		font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
		font-size: 12px;
		background: #f3f4f6;
		padding: 2px 6px;
		border-radius: 4px;
		color: #4b5563;
	}

	.sub-account {
		display: inline-block;
		padding: 2px 8px;
		background: #dbeafe;
		color: #1e40af;
		border-radius: 4px;
		font-size: 12px;
		font-weight: 500;
	}

	.api-actions {
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

	.menu-item:disabled {
		opacity: 0.5;
		cursor: not-allowed;
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

		.register-btn {
			width: 100%;
			justify-content: center;
		}

		.api-token-section {
			padding: 20px;
		}

		.token-actions {
			flex-direction: column;
		}

		.token-btn {
			width: 100%;
			justify-content: center;
		}

		.api-item {
			flex-direction: column;
			align-items: flex-start;
			gap: 16px;
		}

		.api-actions {
			align-self: flex-end;
		}
	}
</style>
