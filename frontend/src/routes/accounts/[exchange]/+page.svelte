<script lang="ts">
	import { browser } from '$app/environment';
	import { page } from '$app/stores';
	import { ApiError } from '$lib/api';
	import {
		fetchTree,
		fetchCapabilities,
		createAccount,
		addCredential,
		revokeCredential,
		syncSubaccounts,
		createSubaccount,
		createSubApiKey,
		type AccountNode,
		type AccountInfo,
		type ExchangeCapabilities
	} from '$lib/accounts';

	$: exchange = ($page.params.exchange ?? '').toUpperCase();

	let tree: AccountNode[] = [];
	let caps: ExchangeCapabilities | null = null;
	let loading = true;
	let error = '';
	let notice = '';

	// One-time display of a freshly created API key secret
	let newKey: { api_key: string; secret: string; account: string } | null = null;

	// Forms
	let newMainLabel = '';
	let keyForms: Record<number, { scheme: string; api_key: string; secret: string }> = {};
	let subLabel: Record<number, string> = {};
	let subKeyTarget: Record<number, number> = {};
	let subKeyTrade: Record<number, boolean> = {};
	let busy = false;

	$: mains = tree.filter((a) => a.exchange === exchange);

	// SvelteKit reuses this component when navigating between exchange pages,
	// so reload (and re-resolve capabilities) whenever the exchange changes.
	let loadedFor = '';
	$: if (browser && exchange && exchange !== loadedFor) {
		loadedFor = exchange;
		caps = null;
		notice = '';
		newKey = null;
		keyForms = {};
		load();
	}

	async function load() {
		loading = true;
		error = '';
		try {
			const [t, c] = await Promise.all([fetchTree(), fetchCapabilities()]);
			tree = t;
			caps = c.find((x) => x.exchange === exchange) ?? null;
		} catch (e) {
			error =
				e instanceof ApiError && e.status === 401
					? 'Please log in to manage accounts.'
					: 'Failed to load accounts.';
		} finally {
			loading = false;
		}
	}

	async function run(fn: () => Promise<void>) {
		busy = true;
		error = '';
		notice = '';
		try {
			await fn();
			await load();
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Operation failed.';
		} finally {
			busy = false;
		}
	}

	const registerMain = () =>
		run(async () => {
			await createAccount({ exchange, label: newMainLabel.trim(), account_type: 'main' });
			notice = `Registered main account "${newMainLabel.trim()}".`;
			newMainLabel = '';
		});

	const submitKey = (account: AccountInfo) =>
		run(async () => {
			const form = keyForms[account.id];
			await addCredential(account.id, {
				scheme: form.scheme,
				api_key: form.api_key.trim(),
				secret: form.secret.trim()
			});
			notice = `API key verified and stored for "${account.label}".`;
			delete keyForms[account.id];
			keyForms = keyForms;
		});

	const revoke = (account: AccountInfo, credentialId: number) =>
		run(async () => {
			await revokeCredential(account.id, credentialId);
			notice = `Key revoked on "${account.label}".`;
		});

	const sync = (main: AccountNode) =>
		run(async () => {
			const subs = await syncSubaccounts(main.id);
			notice = `Synced ${subs.length} subaccount${subs.length === 1 ? '' : 's'} from ${exchange}.`;
		});

	const addSub = (main: AccountNode) =>
		run(async () => {
			await createSubaccount(main.id, subLabel[main.id].trim());
			notice = `Subaccount "${subLabel[main.id].trim()}" created on ${exchange}.`;
			subLabel[main.id] = '';
		});

	const issueKey = (main: AccountNode) =>
		run(async () => {
			const subId = subKeyTarget[main.id];
			const sub = main.children.find((s) => s.id === subId);
			const permissions = subKeyTrade[main.id] ? ['read', 'trade'] : ['read'];
			const result = await createSubApiKey(main.id, {
				sub_account_id: subId,
				permissions
			});
			newKey = {
				api_key: result.credential.api_key,
				secret: result.secret,
				account: sub?.label ?? String(subId)
			};
		});

	function startKeyForm(accountId: number) {
		keyForms[accountId] = { scheme: 'HMAC', api_key: '', secret: '' };
		keyForms = keyForms;
	}

</script>

<svelte:head>
	<title>{exchange} accounts · CPanel</title>
</svelte:head>

<div class="page-head">
	<h1>{exchange}</h1>
	{#if caps}
		<span class="cap-note">
			{#if caps.api_key_create}Full API management{:else}Keys are created on the exchange and registered here{/if}
		</span>
	{/if}
</div>

{#if newKey}
	<div class="secret-banner">
		<strong>New API key for {newKey.account} — copy the secret now, it will not be shown again.</strong>
		<div class="secret-line"><span>Key</span><code>{newKey.api_key}</code></div>
		<div class="secret-line"><span>Secret</span><code>{newKey.secret}</code></div>
		<button class="btn secondary" on:click={() => (newKey = null)}>I saved it</button>
	</div>
{/if}

{#if notice}<p class="notice">{notice}</p>{/if}
{#if error}<p class="error">{error}</p>{/if}

{#if loading}
	<p class="muted">Loading…</p>
{:else}
	<div class="card">
		<h2>Register main account</h2>
		<p class="muted">
			A main account is your {exchange} account. After registering, attach a verified API key to it.
		</p>
		<form class="inline-form" on:submit|preventDefault={registerMain}>
			<input placeholder="Label (e.g. main-trading)" bind:value={newMainLabel} required />
			<button class="btn" disabled={busy || !newMainLabel.trim()}>Register</button>
		</form>
	</div>

	{#each mains as main (main.id)}
		<div class="card">
			<div class="card-head">
				<h2>{main.label}</h2>
				{#if main.native_uid}<span class="uid">uid {main.native_uid}</span>{/if}
			</div>

			<h3>API keys</h3>
			{#if main.credentials.length === 0}
				<p class="muted">No keys registered.</p>
			{/if}
			{#each main.credentials as cred (cred.id)}
				<div class="cred-row" class:revoked={cred.status !== 'active'}>
					<code>{cred.api_key.slice(0, 10)}…</code>
					<span class="tag">{cred.scheme}</span>
					{#if cred.read_only}<span class="tag">read-only</span>{/if}
					{#if cred.can_withdraw}<span class="tag warn">withdraw</span>{/if}
					<span class="tag" class:ok={cred.status === 'active'}>{cred.status}</span>
					{#if cred.status === 'active'}
						<button class="btn danger small" disabled={busy} on:click={() => revoke(main, cred.id)}>
							Revoke
						</button>
					{/if}
				</div>
			{/each}

			{#if keyForms[main.id]}
				<form class="key-form" on:submit|preventDefault={() => submitKey(main)}>
					<select bind:value={keyForms[main.id].scheme}>
						<option value="HMAC">HMAC</option>
						<option value="ED25519">ED25519</option>
					</select>
					<input placeholder="API key" bind:value={keyForms[main.id].api_key} required />
					<input
						type="password"
						placeholder="Secret / private key"
						bind:value={keyForms[main.id].secret}
						required
					/>
					<button class="btn" disabled={busy}>Verify & save</button>
				</form>
			{:else}
				<button class="btn secondary small" on:click={() => startKeyForm(main.id)}>
					Add API key
				</button>
			{/if}

			{#if caps?.subaccount_list || caps?.subaccount_create}
				<h3>Subaccounts</h3>
				{#if main.children.length === 0}
					<p class="muted">No subaccounts known.</p>
				{/if}
				{#each main.children as sub (sub.id)}
					<div class="sub-block">
						<div class="cred-row">
							<span class="sub-label">{sub.label}</span>
							{#if sub.native_uid}<span class="uid">uid {sub.native_uid}</span>{/if}
						</div>

						{#each sub.credentials as cred (cred.id)}
							<div class="cred-row indent" class:revoked={cred.status !== 'active'}>
								<code>{cred.api_key.slice(0, 10)}…</code>
								<span class="tag">{cred.scheme}</span>
								{#if cred.read_only}<span class="tag">read-only</span>{/if}
								{#if cred.can_withdraw}<span class="tag warn">withdraw</span>{/if}
								<span class="tag" class:ok={cred.status === 'active'}>{cred.status}</span>
								{#if cred.status === 'active'}
									<button
										class="btn danger small"
										disabled={busy}
										on:click={() => revoke(sub, cred.id)}>Revoke</button
									>
								{/if}
							</div>
						{/each}

						{#if keyForms[sub.id]}
							<form class="key-form indent" on:submit|preventDefault={() => submitKey(sub)}>
								<select bind:value={keyForms[sub.id].scheme}>
									<option value="HMAC">HMAC</option>
									<option value="ED25519">ED25519</option>
								</select>
								<input placeholder="API key" bind:value={keyForms[sub.id].api_key} required />
								<input
									type="password"
									placeholder="Secret / private key"
									bind:value={keyForms[sub.id].secret}
									required
								/>
								<button class="btn" disabled={busy}>Verify & save</button>
							</form>
						{:else}
							<div class="indent">
								<button class="btn secondary small" on:click={() => startKeyForm(sub.id)}>
									Register API key
								</button>
							</div>
						{/if}
					</div>
				{/each}

				<div class="sub-actions">
					{#if caps?.subaccount_list}
						<button class="btn secondary small" disabled={busy} on:click={() => sync(main)}>
							Sync from {exchange}
						</button>
					{/if}
					{#if caps?.subaccount_create}
						<form class="inline-form" on:submit|preventDefault={() => addSub(main)}>
							<input placeholder="New subaccount name" bind:value={subLabel[main.id]} />
							<button class="btn small" disabled={busy || !subLabel[main.id]?.trim()}>
								Create subaccount
							</button>
						</form>
					{/if}
				</div>

				{#if caps?.api_key_create && main.children.length > 0}
					<h3>Issue subaccount API key</h3>
					<form class="inline-form" on:submit|preventDefault={() => issueKey(main)}>
						<select bind:value={subKeyTarget[main.id]}>
							<option value={undefined} disabled selected>Choose subaccount…</option>
							{#each main.children as sub (sub.id)}
								<option value={sub.id}>{sub.label}</option>
							{/each}
						</select>
						<label class="check">
							<input type="checkbox" bind:checked={subKeyTrade[main.id]} /> allow trading
						</label>
						<button class="btn" disabled={busy || !subKeyTarget[main.id]}>Create key</button>
					</form>
				{/if}
			{/if}
		</div>
	{/each}
{/if}

<style>
	.page-head {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin-bottom: 1.25rem;
	}
	h1 {
		margin: 0;
		font-size: 1.375rem;
	}
	.cap-note {
		font-size: 0.8125rem;
		color: #8b949e;
	}

	h2 {
		margin: 0 0 0.5rem;
		font-size: 1rem;
	}
	h3 {
		margin: 1.25rem 0 0.5rem;
		font-size: 0.8125rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: #57606a;
	}

	.card {
		background: #ffffff;
		border: 1px solid #e3e5e8;
		border-radius: 0.625rem;
		padding: 1.25rem;
		margin-bottom: 1rem;
	}
	.card-head {
		display: flex;
		align-items: baseline;
		gap: 0.625rem;
	}

	.muted {
		color: #8b949e;
		font-size: 0.875rem;
	}
	.error {
		color: #cf222e;
		font-size: 0.875rem;
	}
	.notice {
		color: #1a7f37;
		font-size: 0.875rem;
	}
	.uid {
		font-size: 0.75rem;
		color: #8b949e;
	}

	.secret-banner {
		background: #fff8c5;
		border: 1px solid #d4a72c;
		border-radius: 0.625rem;
		padding: 1rem 1.25rem;
		margin-bottom: 1rem;
		font-size: 0.875rem;
		color: #1f2328;
	}
	.secret-line {
		display: flex;
		gap: 0.625rem;
		margin: 0.5rem 0;
		align-items: center;
	}
	.secret-line span {
		width: 3.5rem;
		color: #57606a;
	}
	.secret-line code {
		background: #ffffff;
		padding: 0.25rem 0.5rem;
		border-radius: 0.25rem;
		border: 1px solid #e3e5e8;
		word-break: break-all;
	}

	.inline-form,
	.key-form {
		display: flex;
		gap: 0.5rem;
		flex-wrap: wrap;
		align-items: center;
		margin-top: 0.5rem;
	}

	input,
	select {
		height: 2.125rem;
		padding: 0 0.625rem;
		border: 1px solid #d0d7de;
		border-radius: 0.375rem;
		font-size: 0.875rem;
		background: #ffffff;
		color: inherit;
		box-sizing: border-box;
	}
	input:focus,
	select:focus {
		outline: 2px solid #5865f2;
		outline-offset: -1px;
	}

	.check {
		display: inline-flex;
		align-items: center;
		gap: 0.375rem;
		font-size: 0.8125rem;
		color: #57606a;
	}
	.check input {
		height: auto;
	}

	.btn {
		display: inline-flex;
		align-items: center;
		height: 2.125rem;
		padding: 0 0.875rem;
		background: #5865f2;
		color: #ffffff;
		font-size: 0.8125rem;
		font-weight: 500;
		border: none;
		border-radius: 0.375rem;
		cursor: pointer;
	}
	.btn:hover:not(:disabled) {
		background: #4752c4;
	}
	.btn:disabled {
		opacity: 0.5;
		cursor: default;
	}
	.btn.secondary {
		background: #eef1f4;
		color: #1f2328;
	}
	.btn.secondary:hover:not(:disabled) {
		background: #e3e5e8;
	}
	.btn.danger {
		background: #cf222e;
	}
	.btn.danger:hover:not(:disabled) {
		background: #a40e26;
	}
	.btn.small {
		height: 1.75rem;
		padding: 0 0.625rem;
		font-size: 0.75rem;
	}

	.cred-row {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.375rem 0;
		font-size: 0.875rem;
		flex-wrap: wrap;
	}
	.cred-row.revoked {
		opacity: 0.55;
	}
	.sub-block {
		padding: 0.375rem 0 0.625rem;
		border-bottom: 1px solid #f0f2f4;
	}
	.sub-block:last-of-type {
		border-bottom: none;
	}
	.indent {
		margin-left: 1.25rem;
	}
	.cred-row code {
		font-size: 0.8125rem;
	}
	.sub-label {
		font-weight: 500;
	}

	.tag {
		padding: 0.125rem 0.5rem;
		border-radius: 999px;
		background: #eef1f4;
		font-size: 0.6875rem;
		color: #57606a;
	}
	.tag.ok {
		background: #dafbe1;
		color: #1a7f37;
	}
	.tag.warn {
		background: #fff1e5;
		color: #bc4c00;
	}

	.sub-actions {
		display: flex;
		gap: 1rem;
		align-items: center;
		flex-wrap: wrap;
		margin-top: 0.625rem;
	}

	@media (prefers-color-scheme: dark) {
		.card {
			background: #161b22;
			border-color: #30363d;
		}
		h3 {
			color: #8b949e;
		}
		input,
		select {
			background: #0d1117;
			border-color: #30363d;
		}
		.btn.secondary {
			background: #21262d;
			color: #e6edf3;
		}
		.btn.secondary:hover:not(:disabled) {
			background: #30363d;
		}
		.tag {
			background: #21262d;
			color: #8b949e;
		}
		.tag.ok {
			background: #12261e;
			color: #3fb950;
		}
		.tag.warn {
			background: #2b1a12;
			color: #d29922;
		}
		.sub-block {
			border-color: #21262d;
		}
		.secret-banner {
			background: #2b2611;
			border-color: #9e6a03;
			color: #e6edf3;
		}
		.secret-line code {
			background: #0d1117;
			border-color: #30363d;
		}
		.secret-line span {
			color: #8b949e;
		}
	}
</style>
