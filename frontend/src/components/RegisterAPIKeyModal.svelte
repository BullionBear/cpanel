<script lang="ts">
	import { registerAPIKey, type APIKeyCreate } from '$lib/api';
	import { createEventDispatcher } from 'svelte';

	export let isOpen = false;

	const dispatch = createEventDispatcher();

	let exchange: 'BINANCE' | 'BYBIT' = 'BINANCE';
	let name = '';
	let apiKey = '';
	let apiSecret = '';
	let masterIs = '';
	let passphrase = '';
	let loading = false;
	let error: string | null = null;

	function close() {
		if (loading) return;
		resetForm();
		isOpen = false;
		dispatch('close');
	}

	function resetForm() {
		exchange = 'BINANCE';
		name = '';
		apiKey = '';
		apiSecret = '';
		masterIs = '';
		passphrase = '';
		error = null;
		loading = false;
	}

	async function handleSubmit() {
		error = null;
		
		// Validation
		if (!name.trim()) {
			error = 'Name is required';
			return;
		}
		if (!apiKey.trim()) {
			error = 'API key is required';
			return;
		}
		if (!apiSecret.trim()) {
			error = 'API secret is required';
			return;
		}

		loading = true;
		error = null; // Clear previous errors

		try {
			const data: APIKeyCreate = {
				exchange: exchange,
				name: name.trim(),
				api_key: apiKey.trim(),
				api_secret: apiSecret.trim(),
				passphrase: passphrase.trim() || undefined,
				master_is: masterIs.trim() || undefined
			};

			await registerAPIKey(data);
			// Success - reset loading, dispatch event and close modal
			loading = false;
			dispatch('success');
			close();
		} catch (e) {
			// Error - stay open and show error message
			error = e instanceof Error ? e.message : 'Failed to register API key';
			loading = false;
		}
	}

	function handleBackdropClick(event: MouseEvent) {
		const target = event.target as HTMLElement;
		if (target.classList.contains('modal-backdrop')) {
			close();
		}
	}

	function handleBackdropKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			close();
		}
	}

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape' && isOpen && !loading) {
			close();
		}
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
		<div class="modal" on:click|stopPropagation role="dialog" aria-modal="true" aria-labelledby="modal-title">
			<div class="modal-header">
				<h2 id="modal-title">Register API Key</h2>
				<button class="close-btn" on:click={close} disabled={loading} aria-label="Close">
					<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
						<path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
				</button>
			</div>

			<form on:submit|preventDefault={handleSubmit} class="modal-body">
				<div class="form-group">
					<label for="exchange">Exchange *</label>
					<select id="exchange" bind:value={exchange} disabled={loading} required>
						<option value="BINANCE">BINANCE</option>
						<option value="BYBIT">BYBIT</option>
					</select>
				</div>

				<div class="form-group">
					<label for="name">Name *</label>
					<input
						id="name"
						type="text"
						bind:value={name}
						placeholder="e.g., Main Account"
						required
						disabled={loading}
					/>
				</div>

				<div class="form-group">
					<label for="apiKey">API Key *</label>
					<input
						id="apiKey"
						type="text"
						bind:value={apiKey}
						placeholder="Enter your API key"
						required
						disabled={loading}
					/>
				</div>

				<div class="form-group">
					<label for="apiSecret">API Secret *</label>
					<input
						id="apiSecret"
						type="password"
						bind:value={apiSecret}
						placeholder="Enter your API secret"
						required
						disabled={loading}
					/>
				</div>

				<div class="form-group">
					<label for="passphrase">Passphrase (Optional)</label>
					<input
						id="passphrase"
						type="password"
						bind:value={passphrase}
						placeholder="Enter passphrase if required"
						disabled={loading}
					/>
					<small class="help-text">Some exchanges require a passphrase</small>
				</div>

				<div class="form-group">
					<label for="masterIs">Master API ID (Optional)</label>
					<input
						id="masterIs"
						type="text"
						bind:value={masterIs}
						placeholder="Enter master API ID for sub-accounts"
						disabled={loading}
					/>
					<small class="help-text">Leave empty for main account, or enter master API ID for sub-accounts</small>
				</div>

				{#if error}
					<div class="error-message">{error}</div>
				{/if}

				<div class="modal-footer">
					<button type="button" class="btn-cancel" on:click={close} disabled={loading}>
						Cancel
					</button>
					<button type="submit" class="btn-submit" disabled={loading}>
						{loading ? 'Registering...' : 'Register'}
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
			transform: translateY(-20px);
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
		padding: 24px;
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
		width: 32px;
		height: 32px;
		border: none;
		background: transparent;
		border-radius: 6px;
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

	.close-btn svg {
		stroke: currentColor;
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
		border-radius: 6px;
		font-size: 14px;
		color: #1f2937;
		transition: all 0.2s;
		box-sizing: border-box;
	}

	.form-group input:focus,
	.form-group select:focus {
		outline: none;
		border-color: #667eea;
		box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
	}

	.form-group input:disabled,
	.form-group select:disabled {
		background: #f3f4f6;
		cursor: not-allowed;
		opacity: 0.6;
	}

	.help-text {
		display: block;
		margin-top: 4px;
		font-size: 12px;
		color: #6b7280;
	}

	.error-message {
		padding: 12px;
		background: #fef2f2;
		border: 1px solid #fecaca;
		border-radius: 6px;
		color: #dc2626;
		font-size: 14px;
		margin-bottom: 20px;
	}

	.modal-footer {
		display: flex;
		justify-content: flex-end;
		gap: 12px;
		margin-top: 24px;
		padding-top: 24px;
		border-top: 1px solid #e5e7eb;
	}

	.btn-cancel,
	.btn-submit {
		padding: 10px 20px;
		border-radius: 6px;
		font-size: 14px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
		border: none;
	}

	.btn-cancel {
		background: #f3f4f6;
		color: #374151;
	}

	.btn-cancel:hover:not(:disabled) {
		background: #e5e7eb;
	}

	.btn-submit {
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
	}

	.btn-submit:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.btn-cancel:disabled,
	.btn-submit:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	@media (max-width: 768px) {
		.modal-backdrop {
			padding: 10px;
		}

		.modal {
			max-width: 100%;
			max-height: 95vh;
		}

		.modal-header,
		.modal-body {
			padding: 20px;
		}

		.modal-footer {
			flex-direction: column-reverse;
		}

		.btn-cancel,
		.btn-submit {
			width: 100%;
		}
	}
</style>
