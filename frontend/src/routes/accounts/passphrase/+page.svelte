<script lang="ts">
	// Everything on this page runs client-side. No secret is ever sent to the
	// server: the random string uses crypto.getRandomValues, and the Ed25519
	// key pair is generated and exported entirely in the browser.

	// -- Random string generator -------------------------------------------
	const CHAR_CLASSES = {
		upper: 'ABCDEFGHIJKLMNOPQRSTUVWXYZ',
		lower: 'abcdefghijklmnopqrstuvwxyz',
		digits: '0123456789'
	};

	let useUpper = true;
	let useLower = true;
	let useDigits = true;
	let useSpecial = false;
	let specialChars = '!@#$%^&*()-_=+';
	let length = 24;
	let randomOut = '';
	let randomError = '';

	function currentCharset(): string {
		let chars = '';
		if (useUpper) chars += CHAR_CLASSES.upper;
		if (useLower) chars += CHAR_CLASSES.lower;
		if (useDigits) chars += CHAR_CLASSES.digits;
		if (useSpecial) chars += specialChars;
		// De-duplicate while preserving order.
		return Array.from(new Set([...chars])).join('');
	}

	function generateRandom() {
		randomError = '';
		randomOut = '';
		const charset = currentCharset();
		const n = Math.floor(length);
		if (!charset) {
			randomError = 'Select at least one character set.';
			return;
		}
		if (!Number.isFinite(n) || n < 1 || n > 4096) {
			randomError = 'Length must be between 1 and 4096.';
			return;
		}
		// Rejection sampling to avoid modulo bias across the chosen charset.
		const size = charset.length;
		const limit = 256 - (256 % size);
		const out: string[] = [];
		const buf = new Uint8Array(64);
		while (out.length < n) {
			crypto.getRandomValues(buf);
			for (let i = 0; i < buf.length && out.length < n; i++) {
				if (buf[i] < limit) out.push(charset[buf[i] % size]);
			}
		}
		randomOut = out.join('');
	}

	// -- Ed25519 key pair generator ----------------------------------------
	let privateKeyPem = '';
	let publicKeyPem = '';
	let keyError = '';
	let generating = false;

	function toPem(buffer: ArrayBuffer, label: string): string {
		const bytes = new Uint8Array(buffer);
		let binary = '';
		for (const b of bytes) binary += String.fromCharCode(b);
		const b64 = btoa(binary);
		const wrapped = b64.match(/.{1,64}/g)?.join('\n') ?? b64;
		return `-----BEGIN ${label}-----\n${wrapped}\n-----END ${label}-----\n`;
	}

	async function generateKeyPair() {
		keyError = '';
		privateKeyPem = '';
		publicKeyPem = '';
		generating = true;
		try {
			// "Ed25519" is part of the Web Crypto Secure Curves spec (Chrome 137+,
			// Firefox, Safari 17+). Cast around the still-narrow DOM typings.
			const subtle = crypto.subtle as SubtleCrypto;
			const pair = (await subtle.generateKey({ name: 'Ed25519' } as unknown as AlgorithmIdentifier, true, [
				'sign',
				'verify'
			])) as CryptoKeyPair;
			const pkcs8 = await subtle.exportKey('pkcs8', pair.privateKey);
			const spki = await subtle.exportKey('spki', pair.publicKey);
			privateKeyPem = toPem(pkcs8, 'PRIVATE KEY');
			publicKeyPem = toPem(spki, 'PUBLIC KEY');
		} catch (e) {
			keyError =
				'Your browser does not support in-browser Ed25519 generation. Use a recent ' +
				'Chrome, Firefox, or Safari.';
		} finally {
			generating = false;
		}
	}

	// -- Copy-to-clipboard helper ------------------------------------------
	let copied = '';
	let copyTimer: ReturnType<typeof setTimeout>;
	async function copy(text: string, which: string) {
		if (!text) return;
		try {
			await navigator.clipboard.writeText(text);
			copied = which;
			clearTimeout(copyTimer);
			copyTimer = setTimeout(() => (copied = ''), 1500);
		} catch {
			/* clipboard unavailable — ignore */
		}
	}
</script>

<svelte:head>
	<title>Passphrase · CPanel</title>
</svelte:head>

<div class="page-head">
	<h1>Passphrase</h1>
	<span class="cap-note">Generated locally in your browser — nothing is sent to the server.</span>
</div>

<!-- Random string generator -->
<div class="card">
	<h2>Random string</h2>
	<p class="muted">Pick the character sets, choose a length, and sample a random string.</p>

	<div class="options">
		<label class="check"><input type="checkbox" bind:checked={useUpper} /> Uppercase (A–Z)</label>
		<label class="check"><input type="checkbox" bind:checked={useLower} /> Lowercase (a–z)</label>
		<label class="check"><input type="checkbox" bind:checked={useDigits} /> Digits (0–9)</label>
		<label class="check"><input type="checkbox" bind:checked={useSpecial} /> Special</label>
	</div>

	{#if useSpecial}
		<div class="field">
			<span class="field-label">Special characters</span>
			<input class="wide" bind:value={specialChars} placeholder="e.g. !@#$%^&*" />
		</div>
	{/if}

	<div class="row">
		<div class="field len">
			<span class="field-label">Length</span>
			<input type="number" min="1" max="4096" bind:value={length} />
		</div>
		<button class="btn" on:click={generateRandom}>Generate</button>
	</div>

	{#if randomError}<p class="error">{randomError}</p>{/if}

	{#if randomOut}
		<div class="output">
			<code class="value">{randomOut}</code>
			<button class="btn secondary small" on:click={() => copy(randomOut, 'random')}>
				{copied === 'random' ? 'Copied' : 'Copy'}
			</button>
		</div>
	{/if}
</div>

<!-- Ed25519 key pair generator -->
<div class="card">
	<h2>Ed25519 key pair</h2>
	<p class="muted">
		Generate an Ed25519 key pair. Register the <strong>public key</strong> on the exchange (e.g.
		Binance), then paste the <strong>private key</strong> into CPanel as the secret with scheme
		<code>ED25519</code>.
	</p>

	<button class="btn" disabled={generating} on:click={generateKeyPair}>
		{generating ? 'Generating…' : 'Generate key pair'}
	</button>

	{#if keyError}<p class="error">{keyError}</p>{/if}

	{#if publicKeyPem}
		<div class="key-field">
			<div class="key-head">
				<span class="field-label">Public key <span class="muted">(register on the exchange)</span></span>
				<button class="btn secondary small" on:click={() => copy(publicKeyPem, 'pub')}>
					{copied === 'pub' ? 'Copied' : 'Copy'}
				</button>
			</div>
			<pre class="pem">{publicKeyPem}</pre>
		</div>

		<div class="key-field">
			<div class="key-head">
				<span class="field-label">Private key <span class="warn-text">(keep secret — paste into CPanel)</span></span>
				<button class="btn secondary small" on:click={() => copy(privateKeyPem, 'priv')}>
					{copied === 'priv' ? 'Copied' : 'Copy'}
				</button>
			</div>
			<pre class="pem">{privateKeyPem}</pre>
		</div>
	{/if}
</div>

<style>
	.page-head {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin-bottom: 1.25rem;
		flex-wrap: wrap;
	}
	h1 {
		margin: 0;
		font-size: 1.5rem;
	}
	.cap-note {
		font-size: 0.8125rem;
		color: var(--text-dim);
	}
	h2 {
		margin: 0 0 0.35rem;
		font-size: 1.05rem;
	}
	.card {
		margin-bottom: 1rem;
	}

	.options {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem 1.25rem;
		margin: 0.75rem 0;
	}
	.check {
		display: inline-flex;
		align-items: center;
		gap: 0.4rem;
		font-size: 0.875rem;
		color: var(--text-muted);
	}
	.check input {
		height: auto;
	}

	.field {
		margin-top: 0.75rem;
	}
	.field-label {
		display: block;
		font-size: 0.8125rem;
		font-weight: 600;
		margin-bottom: 0.375rem;
	}
	.wide {
		width: 100%;
	}
	.row {
		display: flex;
		align-items: flex-end;
		gap: 0.75rem;
		margin-top: 0.75rem;
	}
	.field.len {
		margin-top: 0;
	}
	.field.len input {
		width: 8rem;
	}

	.output {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		margin-top: 0.85rem;
		padding: 0.6rem 0.75rem;
		background: var(--white);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
	}
	.output .value {
		flex: 1;
		min-width: 0;
		font-family: var(--mono);
		font-size: 0.875rem;
		word-break: break-all;
	}

	.key-field {
		margin-top: 1rem;
	}
	.key-head {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 0.75rem;
		margin-bottom: 0.375rem;
	}
	.key-head .field-label {
		margin-bottom: 0;
	}
	.warn-text {
		color: var(--warn);
		font-weight: 600;
		font-size: 0.75rem;
	}
	.pem {
		margin: 0;
		padding: 0.6rem 0.75rem;
		background: var(--white);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		font-family: var(--mono);
		font-size: 0.75rem;
		white-space: pre-wrap;
		word-break: break-all;
	}
</style>
