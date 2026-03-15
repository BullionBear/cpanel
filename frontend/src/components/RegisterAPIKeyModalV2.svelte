<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import {
		getExchanges,
		registerApiKeyV2,
		generateKeyPair,
		type ExchangeOption,
		type APIKeyCreateV2
	} from '$lib/api';

	export let isOpen = false;

	const dispatch = createEventDispatcher();

	let exchanges: ExchangeOption[] = [];
	let exchId: number | null = null;
	let apiType: string = 'HMAC';
	let apiKey = '';
	let apiSecret = '';
	let passphrase = '';
	let parentUid = '';
	let loading = false;
	let error: string | null = null;
	let generatingKeys = false;
	let generatedPublicKey = '';

	$: isAsymmetric = apiType === 'ED25519' || apiType === 'RSA';
	$: secretLabel = isAsymmetric ? 'Private Key *' : 'API Secret *';
	$: secretPlaceholder = isAsymmetric ? 'Enter or generate your private key' : 'Enter your API secret';

	let wasOpen = false;
	$: if (isOpen && !wasOpen) {
		wasOpen = true;
		loadExchanges();
	}
	$: if (!isOpen) wasOpen = false;

	async function loadExchanges() {
		try {
			exchanges = await getExchanges();
			if (exchanges.length > 0 && exchId == null) exchId = exchanges[0].id;
	} catch (e) {
		exchanges = [{ id: 1, name: 'Binance' }, { id: 2, name: 'Bybit' }, { id: 3, name: 'Bitget' }];
		exchId = exchanges[0]?.id ?? 1;
	}
	}

	function close() {
		if (loading) return;
		resetForm();
		isOpen = false;
		dispatch('close');
	}

	function resetForm() {
		exchId = exchanges.length > 0 ? exchanges[0].id : null;
		apiType = 'HMAC';
		apiKey = '';
		apiSecret = '';
		passphrase = '';
		parentUid = '';
		error = null;
		loading = false;
		generatingKeys = false;
		generatedPublicKey = '';
	}

	async function handleGenerateKeys() {
		if (apiType === 'HMAC') return;
		generatingKeys = true;
		error = null;
		try {
			const keyPair = await generateKeyPair(apiType as 'RSA' | 'ED25519');
			apiSecret = keyPair.private_key;
			generatedPublicKey = keyPair.public_key;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to generate key pair';
		} finally {
			generatingKeys = false;
		}
	}

	function copyPublicKey() {
		if (generatedPublicKey) navigator.clipboard.writeText(generatedPublicKey);
	}

	async function handleSubmit() {
		error = null;
		if (exchId == null) {
			error = 'Please select an exchange';
			return;
		}
		if (!apiKey.trim()) {
			error = 'API key is required';
			return;
		}
		if (!apiSecret.trim()) {
			error = isAsymmetric ? 'Private key is required' : 'API secret is required';
			return;
		}

		loading = true;
		try {
			const data: APIKeyCreateV2 = {
				exch_id: exchId,
				api_type: apiType,
				api_key: apiKey.trim(),
				api_secret: apiSecret.trim(),
				passphrase: passphrase.trim() || undefined,
				parent_uid: parentUid.trim() ? parseInt(parentUid.trim(), 10) : undefined
			};
			if (data.parent_uid != null && isNaN(data.parent_uid)) {
				error = 'Master Account UID must be a number';
				loading = false;
				return;
			}
			await registerApiKeyV2(data);
			loading = false;
			dispatch('success');
			close();
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to register API key';
			loading = false;
		}
	}

	function handleBackdropClick(event: MouseEvent) {
		if (event.target === event.currentTarget) close();
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
		<div class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title-v2">
			<div class="modal-header">
				<h2 id="modal-title-v2">Register API Key</h2>
				<button type="button" class="close-btn" on:click={close} disabled={loading} aria-label="Close">
					<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
						<path d="M18 6L6 18M6 6L18 18" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					</svg>
				</button>
			</div>

			<form on:submit|preventDefault={handleSubmit} class="modal-body">
				<div class="form-group">
					<label for="exchange-v2">Exchange *</label>
					<select id="exchange-v2" bind:value={exchId} disabled={loading} required>
						{#each exchanges as ex}
							<option value={ex.id}>{ex.name}</option>
						{/each}
					</select>
				</div>

				<div class="form-group">
					<label for="apiType-v2">API Type *</label>
					<select id="apiType-v2" bind:value={apiType} disabled={loading} required>
						<option value="HMAC">HMAC (Standard)</option>
						<option value="ED25519">ED25519 (Asymmetric)</option>
						<option value="RSA">RSA (Asymmetric)</option>
					</select>
					<small class="help-text">
						{#if apiType === 'HMAC'}
							Standard HMAC-based authentication using API secret
						{:else if apiType === 'ED25519'}
							ED25519 signature authentication using private/public key pair
						{:else}
							RSA signature authentication using private/public key pair
						{/if}
					</small>
				</div>

				<div class="form-group">
					<label for="apiKey-v2">API Key *</label>
					<input
						id="apiKey-v2"
						type="text"
						bind:value={apiKey}
						placeholder="Enter your API key"
						required
						disabled={loading}
					/>
				</div>

				<div class="form-group">
					<label for="apiSecret-v2">{secretLabel}</label>
					<div class="input-with-button">
						<textarea
							id="apiSecret-v2"
							bind:value={apiSecret}
							placeholder={secretPlaceholder}
							required
							disabled={loading || generatingKeys}
							rows={isAsymmetric ? 4 : 1}
							class:textarea-expanded={isAsymmetric}
						></textarea>
						{#if isAsymmetric}
							<button
								type="button"
								class="generate-btn"
								on:click={handleGenerateKeys}
								disabled={loading || generatingKeys}
							>
								{generatingKeys ? 'Generating...' : 'Generate'}
							</button>
						{/if}
					</div>
				</div>

				{#if isAsymmetric && generatedPublicKey}
					<div class="form-group">
						<label for="publicKey-v2">Public Key (Copy this to your exchange)</label>
						<div class="public-key-container">
							<textarea
								id="publicKey-v2"
								value={generatedPublicKey}
								readonly
								rows="4"
								class="public-key-textarea"
							></textarea>
							<button type="button" class="copy-btn" on:click={copyPublicKey} title="Copy to clipboard">
								<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
									<path d="M16 1H4C2.9 1 2 1.9 2 3V17H4V3H16V1ZM19 5H8C6.9 5 6 5.9 6 7V21C6 22.1 6.9 23 8 23H19C20.1 23 21 22.1 21 21V7C21 5.9 20.1 5 19 5ZM19 21H8V7H19V21Z" fill="currentColor"/>
								</svg>
							</button>
						</div>
					</div>
				{/if}

				<div class="form-group">
					<label for="passphrase-v2">Passphrase (Optional)</label>
					<input
						id="passphrase-v2"
						type="password"
						bind:value={passphrase}
						placeholder="Enter passphrase if required"
						disabled={loading}
					/>
					<small class="help-text">Some exchanges require a passphrase</small>
				</div>

				<div class="form-group">
					<label for="parentUid-v2">Master Account UID (Optional)</label>
					<input
						id="parentUid-v2"
						type="text"
						bind:value={parentUid}
						placeholder="Enter master account UID for sub-accounts"
						disabled={loading}
					/>
					<small class="help-text">Leave empty for main account</small>
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
		from { opacity: 0; transform: translateY(-20px); }
		to { opacity: 1; transform: translateY(0); }
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
	.form-group select,
	.form-group textarea {
		width: 100%;
		padding: 10px 12px;
		border: 1px solid #d1d5db;
		border-radius: 6px;
		font-size: 14px;
		color: #1f2937;
		box-sizing: border-box;
		font-family: inherit;
	}

	.form-group textarea {
		resize: vertical;
		min-height: 42px;
	}

	.form-group textarea.textarea-expanded {
		font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
		font-size: 12px;
	}

	.form-group input:focus,
	.form-group select:focus,
	.form-group textarea:focus {
		outline: none;
		border-color: #667eea;
		box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
	}

	.input-with-button {
		display: flex;
		gap: 8px;
		align-items: flex-start;
	}

	.input-with-button textarea {
		flex: 1;
	}

	.generate-btn {
		padding: 10px 16px;
		background: linear-gradient(135deg, #10b981 0%, #059669 100%);
		color: white;
		border: none;
		border-radius: 6px;
		font-size: 14px;
		font-weight: 500;
		cursor: pointer;
		white-space: nowrap;
	}

	.generate-btn:hover:not(:disabled) {
		opacity: 0.95;
	}

	.public-key-container {
		position: relative;
	}

	.public-key-textarea {
		font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
		font-size: 12px;
		background: #f0fdf4;
		border-color: #86efac;
		padding-right: 40px;
	}

	.public-key-container .copy-btn {
		position: absolute;
		top: 8px;
		right: 8px;
		width: 32px;
		height: 32px;
		border: none;
		background: white;
		border-radius: 6px;
		cursor: pointer;
		color: #6b7280;
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
		border: none;
	}

	.btn-cancel {
		background: #f3f4f6;
		color: #374151;
	}

	.btn-submit {
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
	}

	.btn-cancel:disabled,
	.btn-submit:disabled {
		opacity: 0.5;
		cursor: not-allowed;
	}

	@media (max-width: 768px) {
		.modal-footer {
			flex-direction: column-reverse;
		}
		.btn-cancel,
		.btn-submit {
			width: 100%;
		}
	}
</style>
