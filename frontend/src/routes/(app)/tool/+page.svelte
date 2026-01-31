<script lang="ts">
	import { generateKeyPair, type KeyType } from '$lib/api';

	// Tab state
	let activeTab = 'keygen';

	// Key generation state
	let keyType: KeyType = 'RSA';
	let keySize = 4096;
	let generating = false;
	let error: string | null = null;
	let privateKey: string | null = null;
	let publicKey: string | null = null;

	const keySizeOptions = [2048, 4096, 8192];

	async function handleGenerate() {
		generating = true;
		error = null;
		privateKey = null;
		publicKey = null;

		try {
			const result = await generateKeyPair(keyType, keyType === 'RSA' ? keySize : undefined);
			privateKey = result.private_key;
			publicKey = result.public_key;
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to generate key pair';
		} finally {
			generating = false;
		}
	}

	function copyToClipboard(text: string, type: string) {
		navigator.clipboard.writeText(text);
		// Simple feedback - could be enhanced with a toast notification
		const btn = document.querySelector(`[data-copy="${type}"]`) as HTMLButtonElement;
		if (btn) {
			const originalText = btn.textContent;
			btn.textContent = 'Copied!';
			setTimeout(() => {
				btn.textContent = originalText;
			}, 2000);
		}
	}

	function downloadKey(content: string, filename: string) {
		const blob = new Blob([content], { type: 'text/plain' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = filename;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);
	}

	function clearKeys() {
		privateKey = null;
		publicKey = null;
		error = null;
	}
</script>

<div class="tool-page">
	<div class="page-header">
		<h1>Tools</h1>
	</div>

	<!-- Tab Navigation -->
	<div class="tabs">
		<button
			class="tab"
			class:active={activeTab === 'keygen'}
			on:click={() => (activeTab = 'keygen')}
		>
			<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M21 2L19 4M11.39 11.61C12.3187 12.5387 12.8356 13.7952 12.8356 15.105C12.8356 16.4148 12.3187 17.6713 11.39 18.6C10.4613 19.5287 9.20483 20.0456 7.895 20.0456C6.58517 20.0456 5.32865 19.5287 4.4 18.6C3.47126 17.6713 2.9544 16.4148 2.9544 15.105C2.9544 13.7952 3.47126 12.5387 4.4 11.61C5.32865 10.6813 6.58517 10.1644 7.895 10.1644C9.20483 10.1644 10.4613 10.6813 11.39 11.61ZM11.39 11.61L15.5 7.5M15.5 7.5L18.5 10.5L21.5 7.5L18.5 4.5L15.5 7.5Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			Key Generator
		</button>
	</div>

	<!-- Tab Content -->
	<div class="tab-content">
		{#if activeTab === 'keygen'}
			<div class="keygen-section">
				<div class="section-header">
					<h2>Generate Key Pair</h2>
					<p class="section-description">
						Generate RSA or ED25519 cryptographic key pairs. Keys are generated securely on the server and returned in PEM format.
					</p>
				</div>

				{#if error}
					<div class="error-message">{error}</div>
				{/if}

				<div class="keygen-form">
					<div class="form-row">
						<div class="form-group">
							<label for="keyType">Key Type</label>
							<select id="keyType" bind:value={keyType} on:change={clearKeys}>
								<option value="RSA">RSA</option>
								<option value="ED25519">ED25519</option>
							</select>
							<span class="help-text">
								{#if keyType === 'RSA'}
									RSA: Widely compatible, configurable key size
								{:else}
									ED25519: Modern, fast, compact keys (256-bit)
								{/if}
							</span>
						</div>

						{#if keyType === 'RSA'}
							<div class="form-group">
								<label for="keySize">Key Size</label>
								<select id="keySize" bind:value={keySize} on:change={clearKeys}>
									{#each keySizeOptions as size}
										<option value={size}>{size} bits</option>
									{/each}
								</select>
								<span class="help-text">
									Larger keys are more secure but slower
								</span>
							</div>
						{/if}
					</div>

					<button
						class="generate-btn"
						on:click={handleGenerate}
						disabled={generating}
					>
						{#if generating}
							<svg class="spinner" width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
								<path d="M12 2V6M12 18V22M4.93 4.93L7.76 7.76M16.24 16.24L19.07 19.07M2 12H6M18 12H22M4.93 19.07L7.76 16.24M16.24 7.76L19.07 4.93" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
							</svg>
							Generating...
						{:else}
							<svg width="18" height="18" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
								<path d="M12 5V19M5 12H19" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
							</svg>
							Generate Key Pair
						{/if}
					</button>
				</div>

				{#if privateKey && publicKey}
					<div class="keys-output">
						<div class="key-block">
							<div class="key-header">
								<h3>Private Key</h3>
								<div class="key-actions">
									<button
										class="action-btn"
										data-copy="private"
										on:click={() => copyToClipboard(privateKey || '', 'private')}
									>
										<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
											<path d="M16 1H4C2.9 1 2 1.9 2 3V17H4V3H16V1ZM19 5H8C6.9 5 6 5.9 6 7V21C6 22.1 6.9 23 8 23H19C20.1 23 21 22.1 21 21V7C21 5.9 20.1 5 19 5ZM19 21H8V7H19V21Z" fill="currentColor"/>
										</svg>
										Copy
									</button>
									<button
										class="action-btn"
										on:click={() => downloadKey(privateKey || '', `${keyType.toLowerCase()}_private_key.pem`)}
									>
										<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
											<path d="M21 15V19C21 19.5304 20.7893 20.0391 20.4142 20.4142C20.0391 20.7893 19.5304 21 19 21H5C4.46957 21 3.96086 20.7893 3.58579 20.4142C3.21071 20.0391 3 19.5304 3 19V15M7 10L12 15M12 15L17 10M12 15V3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
										</svg>
										Download
									</button>
								</div>
							</div>
							<div class="key-warning">
								Keep this key secure and never share it!
							</div>
							<pre class="key-content">{privateKey}</pre>
						</div>

						<div class="key-block">
							<div class="key-header">
								<h3>Public Key</h3>
								<div class="key-actions">
									<button
										class="action-btn"
										data-copy="public"
										on:click={() => copyToClipboard(publicKey || '', 'public')}
									>
										<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
											<path d="M16 1H4C2.9 1 2 1.9 2 3V17H4V3H16V1ZM19 5H8C6.9 5 6 5.9 6 7V21C6 22.1 6.9 23 8 23H19C20.1 23 21 22.1 21 21V7C21 5.9 20.1 5 19 5ZM19 21H8V7H19V21Z" fill="currentColor"/>
										</svg>
										Copy
									</button>
									<button
										class="action-btn"
										on:click={() => downloadKey(publicKey || '', `${keyType.toLowerCase()}_public_key.pem`)}
									>
										<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
											<path d="M21 15V19C21 19.5304 20.7893 20.0391 20.4142 20.4142C20.0391 20.7893 19.5304 21 19 21H5C4.46957 21 3.96086 20.7893 3.58579 20.4142C3.21071 20.0391 3 19.5304 3 19V15M7 10L12 15M12 15L17 10M12 15V3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
										</svg>
										Download
									</button>
								</div>
							</div>
							<pre class="key-content">{publicKey}</pre>
						</div>
					</div>
				{/if}
			</div>
		{/if}
	</div>
</div>

<style>
	.tool-page {
		max-width: 1200px;
		margin: 0 auto;
	}

	.page-header {
		margin-bottom: 24px;
	}

	.page-header h1 {
		margin: 0;
		font-size: 28px;
		font-weight: 600;
		color: #1f2937;
	}

	/* Tabs */
	.tabs {
		display: flex;
		gap: 8px;
		border-bottom: 1px solid #e5e7eb;
		margin-bottom: 24px;
	}

	.tab {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 20px;
		background: transparent;
		border: none;
		border-bottom: 2px solid transparent;
		font-size: 14px;
		font-weight: 500;
		color: #6b7280;
		cursor: pointer;
		transition: all 0.2s;
	}

	.tab:hover {
		color: #374151;
	}

	.tab.active {
		color: #667eea;
		border-bottom-color: #667eea;
	}

	.tab svg {
		stroke: currentColor;
	}

	/* Tab Content */
	.tab-content {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		padding: 24px;
	}

	/* Section Header */
	.section-header {
		margin-bottom: 24px;
	}

	.section-header h2 {
		margin: 0 0 8px 0;
		font-size: 20px;
		font-weight: 600;
		color: #1f2937;
	}

	.section-description {
		margin: 0;
		font-size: 14px;
		color: #6b7280;
	}

	/* Error Message */
	.error-message {
		background: #fef2f2;
		border: 1px solid #fecaca;
		color: #ef4444;
		padding: 12px 16px;
		border-radius: 8px;
		margin-bottom: 20px;
		font-size: 14px;
	}

	/* Form */
	.keygen-form {
		margin-bottom: 24px;
	}

	.form-row {
		display: flex;
		gap: 24px;
		margin-bottom: 20px;
	}

	.form-group {
		flex: 1;
		max-width: 300px;
	}

	.form-group label {
		display: block;
		margin-bottom: 8px;
		font-size: 14px;
		font-weight: 500;
		color: #374151;
	}

	.form-group select {
		width: 100%;
		padding: 10px 12px;
		border: 1px solid #d1d5db;
		border-radius: 8px;
		font-size: 14px;
		color: #1f2937;
		background: white;
		cursor: pointer;
		transition: border-color 0.2s;
	}

	.form-group select:focus {
		outline: none;
		border-color: #667eea;
		box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
	}

	.help-text {
		display: block;
		margin-top: 6px;
		font-size: 12px;
		color: #9ca3af;
	}

	/* Generate Button */
	.generate-btn {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 24px;
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
		border: none;
		border-radius: 8px;
		font-size: 14px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
	}

	.generate-btn:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.generate-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.generate-btn svg {
		stroke: currentColor;
	}

	.spinner {
		animation: spin 1s linear infinite;
	}

	@keyframes spin {
		from {
			transform: rotate(0deg);
		}
		to {
			transform: rotate(360deg);
		}
	}

	/* Keys Output */
	.keys-output {
		display: flex;
		flex-direction: row;
		gap: 20px;
	}

	.keys-output .key-block {
		flex: 1;
		min-width: 0;
	}

	.key-block {
		background: #f9fafb;
		border: 1px solid #e5e7eb;
		border-radius: 8px;
		overflow: hidden;
	}

	.key-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 12px 16px;
		background: #f3f4f6;
		border-bottom: 1px solid #e5e7eb;
	}

	.key-header h3 {
		margin: 0;
		font-size: 14px;
		font-weight: 600;
		color: #374151;
	}

	.key-actions {
		display: flex;
		gap: 8px;
	}

	.action-btn {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 6px 12px;
		background: white;
		border: 1px solid #d1d5db;
		border-radius: 6px;
		font-size: 12px;
		font-weight: 500;
		color: #374151;
		cursor: pointer;
		transition: all 0.2s;
	}

	.action-btn:hover {
		background: #f3f4f6;
		border-color: #9ca3af;
	}

	.action-btn svg {
		fill: currentColor;
		stroke: currentColor;
	}

	.key-warning {
		padding: 8px 16px;
		background: #fef3c7;
		border-bottom: 1px solid #fcd34d;
		font-size: 12px;
		color: #92400e;
		font-weight: 500;
	}

	.key-content {
		margin: 0;
		padding: 16px;
		font-family: 'Monaco', 'Menlo', 'Ubuntu Mono', monospace;
		font-size: 12px;
		line-height: 1.5;
		color: #1f2937;
		white-space: pre-wrap;
		word-break: break-all;
		overflow-x: auto;
		max-height: 300px;
		overflow-y: auto;
	}

	/* Responsive */
	@media (max-width: 1024px) {
		.keys-output {
			flex-direction: column;
		}
	}

	@media (max-width: 768px) {
		.form-row {
			flex-direction: column;
		}

		.form-group {
			max-width: 100%;
		}

		.key-header {
			flex-direction: column;
			align-items: flex-start;
			gap: 12px;
		}

		.key-actions {
			width: 100%;
		}

		.action-btn {
			flex: 1;
			justify-content: center;
		}
	}
</style>
