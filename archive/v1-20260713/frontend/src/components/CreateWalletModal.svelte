<script lang="ts">
	import { createWallet, type APIKey, type WalletType } from '$lib/api';
	import { createEventDispatcher } from 'svelte';

	export let isOpen = false;
	export let apiKeys: APIKey[] = [];
	export let walletTypes: WalletType[] = [];

	const dispatch = createEventDispatcher();

	let name = '';
	let apiKeyId: number | '' = '';
	let walletTypeId: number | '' = '';
	let loading = false;
	let error: string | null = null;

	function close() {
		if (loading) return;
		resetForm();
		isOpen = false;
		dispatch('close');
	}

	function resetForm() {
		name = '';
		apiKeyId = '';
		walletTypeId = '';
		error = null;
		loading = false;
	}

	async function handleSubmit() {
		error = null;
		if (!name.trim()) {
			error = 'Name is required';
			return;
		}
		if (apiKeyId === '' || apiKeyId === null) {
			error = 'Please select an API key';
			return;
		}
		if (walletTypeId === '' || walletTypeId === null) {
			error = 'Please select a wallet type';
			return;
		}

		loading = true;
		try {
			await createWallet({
				name: name.trim(),
				api_key_id: Number(apiKeyId),
				wallet_type_id: Number(walletTypeId)
			});
			loading = false;
			dispatch('success');
			close();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to create wallet';
			loading = false;
		}
	}

	function handleBackdropClick(event: MouseEvent) {
		const target = event.target as HTMLElement;
		if (target.classList.contains('modal-backdrop')) close();
	}

	function handleBackdropKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			close();
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && isOpen && !loading) close();
	}
</script>

<svelte:window on:keydown={handleKeydown} />

{#if isOpen}
	<div
		class="modal-backdrop"
		on:click={handleBackdropClick}
		on:keydown={handleBackdropKeydown}
		role="button"
		tabindex="0"
		aria-label="Close modal"
	>
		<!-- svelte-ignore a11y-no-noninteractive-element-interactions -->
		<div class="modal" on:click|stopPropagation on:keydown|stopPropagation role="dialog" aria-modal="true" aria-labelledby="modal-title">
			<div class="modal-header">
				<h2 id="modal-title">Create Wallet</h2>
				<button class="close-btn" on:click={close} disabled={loading} aria-label="Close">
					<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
						<path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
				</button>
			</div>

			<form on:submit|preventDefault={handleSubmit} class="modal-body">
				<div class="form-group">
					<label for="wallet-name">Name *</label>
					<input
						id="wallet-name"
						type="text"
						bind:value={name}
						placeholder="e.g., bn-spot-main"
						required
						disabled={loading}
					/>
				</div>

				<div class="form-group">
					<label for="api-key">API Key *</label>
					<select id="api-key" bind:value={apiKeyId} disabled={loading} required>
						<option value="">Select an API key</option>
						{#each apiKeys as api}
							<option value={api.id}>{api.exchange} – {api.name}</option>
						{/each}
					</select>
					<small class="help-text">Wallet will use this API key to fetch balance and trade.</small>
				</div>

				<div class="form-group">
					<label for="wallet-type">Wallet Type *</label>
					<select id="wallet-type" bind:value={walletTypeId} disabled={loading} required>
						<option value="">Select wallet type</option>
						{#each walletTypes as wt}
							<option value={wt.id}>{wt.name} ({wt.slug})</option>
						{/each}
					</select>
					<small class="help-text">e.g. Spot, Unified. Allowed types depend on the selected exchange.</small>
				</div>

				{#if error}
					<div class="error-message">{error}</div>
				{/if}

				<div class="modal-footer">
					<button type="button" class="btn-cancel" on:click={close} disabled={loading}>
						Cancel
					</button>
					<button type="submit" class="btn-submit" disabled={loading}>
						{loading ? 'Creating...' : 'Create'}
					</button>
				</div>
			</form>
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
		backdrop-filter: blur(4px);
	}

	.modal {
		background: white;
		border-radius: 12px;
		width: 100%;
		max-width: 500px;
		max-height: 90vh;
		overflow-y: auto;
		box-shadow: 0 20px 25px -5px rgba(0, 0, 0, 0.1), 0 10px 10px -5px rgba(0, 0, 0, 0.04);
		animation: slideIn 0.2s ease-out;
	}

	@keyframes slideIn {
		from {
			opacity: 0;
			transform: translateY(-10px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
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
		background: transparent;
		border-radius: 8px;
		cursor: pointer;
		color: #6b7280;
		transition: all 0.2s;
	}

	.close-btn:hover:not(:disabled) {
		background: #f3f4f6;
		color: #374151;
	}

	.close-btn:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	.modal-body {
		padding: 24px;
	}

	.form-group {
		margin-bottom: 20px;
	}

	.form-group label {
		display: block;
		margin-bottom: 8px;
		font-size: 14px;
		font-weight: 500;
		color: #374151;
	}

	.form-group input,
	.form-group select {
		width: 100%;
		padding: 10px 12px;
		border: 1px solid #d1d5db;
		border-radius: 8px;
		font-size: 14px;
		color: #1f2937;
		background: white;
		box-sizing: border-box;
	}

	.form-group input:disabled,
	.form-group select:disabled {
		background: #f9fafb;
		cursor: not-allowed;
		opacity: 0.8;
	}

	.help-text {
		display: block;
		margin-top: 6px;
		font-size: 12px;
		color: #6b7280;
	}

	.error-message {
		background: #fef2f2;
		border: 1px solid #fecaca;
		color: #dc2626;
		padding: 12px;
		border-radius: 8px;
		font-size: 14px;
		margin-bottom: 16px;
	}

	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		margin-top: 24px;
		padding-top: 20px;
		border-top: 1px solid #e5e7eb;
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

	.btn-cancel:hover:not(:disabled) {
		background: #f9fafb;
	}

	.btn-submit {
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

	.btn-submit:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.btn-cancel:disabled,
	.btn-submit:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}
</style>
