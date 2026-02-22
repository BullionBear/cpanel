<script lang="ts">
	import { onMount } from 'svelte';
	import { getAPIToken, createAPIToken, deleteAPIToken } from '$lib/api';

	// API Token state
	let apiToken: string | null = null;
	let loadingToken = false;
	let tokenError: string | null = null;
	let creatingToken = false;
	let deletingToken = false;

	onMount(() => {
		loadAPIToken();
	});

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
		}
	}
</script>

<div class="settings-page">
	<div class="page-header">
		<h1>Settings</h1>
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
</div>

<style>
	.settings-page {
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

	@media (max-width: 768px) {
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
	}
</style>
