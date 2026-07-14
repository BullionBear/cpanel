<script lang="ts">
	import { browser } from '$app/environment';
	import { page } from '$app/stores';
	import { ApiError } from '$lib/api';
	import {
		fetchTree,
		fetchCapabilities,
		registerAccount,
		revealSecret,
		setTags,
		revokeCredential,
		modifyCredential,
		syncSubaccounts,
		createSubaccount,
		createSubApiKey,
		freezeSubaccount,
		type AccountNode,
		type AccountInfo,
		type Credential,
		type ExchangeCapabilities
	} from '$lib/accounts';

	$: exchange = ($page.params.exchange ?? '').toUpperCase();

	let tree: AccountNode[] = [];
	let caps: ExchangeCapabilities | null = null;
	let loading = true;
	let error = '';
	let notice = '';
	let busy = false;

	$: registrationOnly = caps?.registration_only ?? exchange === 'BINANCE';
	// Every exchange page leads with its Readme.
	$: tab = $page.url.searchParams.get('tab') ?? 'readme';
	$: schemes = exchange === 'BINANCE' ? ['HMAC', 'ED25519'] : ['HMAC'];
	$: mains = tree.filter((a) => a.exchange === exchange);

	// Registration form (main account)
	let reg = { label: '', scheme: 'HMAC', api_key: '', secret: '', tags: '' };
	// Per-main subaccount registration form (registration-only venues)
	let subReg: Record<number, { label: string; scheme: string; api_key: string; secret: string; tags: string }> = {};
	// Per-main new-subaccount name (self-service venues)
	let subName: Record<number, string> = {};
	// Per-sub create-key form (self-service venues)
	let keyForm: Record<number, { trade: boolean; tags: string }> = {};

	// Revealed secrets, keyed by credential id
	let revealed: Record<number, { api_key: string; secret: string }> = {};
	// Tag editing drafts, keyed by credential id
	let tagDraft: Record<number, string> = {};

	let loadedFor = '';
	$: if (browser && exchange && exchange !== loadedFor) {
		loadedFor = exchange;
		caps = null;
		notice = '';
		revealed = {};
		tagDraft = {};
		reg = { label: '', scheme: schemes[0] ?? 'HMAC', api_key: '', secret: '', tags: '' };
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

	const parseTags = (raw: string): string[] =>
		raw
			.split(',')
			.map((t) => t.trim())
			.filter(Boolean);

	const registerMain = () =>
		run(async () => {
			await registerAccount({
				exchange,
				label: reg.label.trim(),
				account_type: 'main',
				scheme: reg.scheme,
				api_key: reg.api_key.trim(),
				secret: reg.secret.trim(),
				tags: parseTags(reg.tags)
			});
			notice = `Registered "${reg.label.trim()}".`;
			reg = { label: '', scheme: schemes[0] ?? 'HMAC', api_key: '', secret: '', tags: '' };
		});

	const registerSub = (main: AccountNode) =>
		run(async () => {
			const form = subReg[main.id];
			await registerAccount({
				exchange,
				label: form.label.trim(),
				account_type: 'sub',
				parent_id: main.id,
				scheme: form.scheme,
				api_key: form.api_key.trim(),
				secret: form.secret.trim(),
				tags: parseTags(form.tags)
			});
			notice = `Registered subaccount "${form.label.trim()}".`;
			delete subReg[main.id];
			subReg = subReg;
		});

	const startSubReg = (mainId: number) => {
		subReg[mainId] = { label: '', scheme: schemes[0] ?? 'HMAC', api_key: '', secret: '', tags: '' };
		subReg = subReg;
	};

	const addSub = (main: AccountNode) =>
		run(async () => {
			await createSubaccount(main.id, subName[main.id].trim());
			notice = `Subaccount "${subName[main.id].trim()}" created on ${exchange}.`;
			subName[main.id] = '';
		});

	const sync = (main: AccountNode) =>
		run(async () => {
			const subs = await syncSubaccounts(main.id);
			notice = `Synced ${subs.length} subaccount${subs.length === 1 ? '' : 's'} from ${exchange}.`;
		});

	const issueKey = (main: AccountNode, sub: AccountInfo) =>
		run(async () => {
			const form = keyForm[sub.id] ?? { trade: false, tags: '' };
			await createSubApiKey(main.id, {
				sub_account_id: sub.id,
				permissions: form.trade ? ['read', 'trade'] : ['read'],
				tags: parseTags(form.tags)
			});
			notice = `Key created for "${sub.label}".`;
			delete keyForm[sub.id];
			keyForm = keyForm;
		});

	const setReadOnly = (subId: number, cred: Credential, readOnly: boolean) =>
		run(async () => {
			await modifyCredential(subId, cred.id, {
				read_only: readOnly,
				permissions: readOnly ? ['read'] : ['read', 'trade']
			});
			notice = readOnly ? 'Key set to read-only.' : 'Trading enabled on key.';
		});

	const revoke = (account: AccountInfo, credentialId: number) =>
		run(async () => {
			await revokeCredential(account.id, credentialId);
			notice = `Key revoked on "${account.label}".`;
		});

	const toggleFreeze = (main: AccountNode, sub: AccountInfo) =>
		run(async () => {
			const freeze = sub.status !== 'frozen';
			await freezeSubaccount(main.id, sub.id, freeze);
			notice = `${freeze ? 'Froze' : 'Unfroze'} "${sub.label}".`;
		});

	async function toggleReveal(account: AccountInfo, cred: Credential) {
		if (revealed[cred.id]) {
			delete revealed[cred.id];
			revealed = revealed;
			return;
		}
		error = '';
		try {
			revealed[cred.id] = await revealSecret(account.id, cred.id);
			revealed = revealed;
		} catch (e) {
			error = e instanceof ApiError ? e.message : 'Could not reveal secret.';
		}
	}

	const startTagEdit = (cred: Credential) => {
		tagDraft[cred.id] = (cred.tags ?? []).join(', ');
		tagDraft = tagDraft;
	};
	const saveTags = (account: AccountInfo, cred: Credential) =>
		run(async () => {
			await setTags(account.id, cred.id, parseTags(tagDraft[cred.id]));
			delete tagDraft[cred.id];
			tagDraft = tagDraft;
		});
	const cancelTagEdit = (credId: number) => {
		delete tagDraft[credId];
		tagDraft = tagDraft;
	};
</script>

<svelte:head>
	<title>{exchange} accounts · CPanel</title>
</svelte:head>

<div class="page-head">
	<h1>{exchange}</h1>
	{#if caps}
		<span class="cap-note">
			{#if registrationOnly}Registration only — keys are created on {exchange}{:else}Full self-service management{/if}
		</span>
	{/if}
</div>

{#if notice}<p class="notice">{notice}</p>{/if}
{#if error}<p class="error">{error}</p>{/if}

{#if loading}
	<p class="muted">Loading…</p>
{:else if tab === 'readme'}
	<div class="card readme">
		<h2>How {exchange} accounts work here</h2>
		{#if registrationOnly}
			<p>
				{exchange}'s API cannot create, rotate, or revoke API keys and subaccounts for a normal
				master account. So CPanel does <strong>not</strong> manage the {exchange} key or
				subaccount lifecycle — it only <em>uses</em> the keys you give it.
			</p>
			<ol>
				<li>Create your API keys and subaccounts on {exchange} directly.</li>
				<li>
					Register each key here (under <strong>Account</strong>) so CPanel can read balances,
					orders, and history and place trades.
				</li>
				<li>
					Give every account a <strong>nickname</strong> to recognize it, and tag each key
					however you like.
				</li>
			</ol>
			<p class="muted">
				The venue account id (e.g. subaccount email) is detected automatically when you register a
				key — you never enter it.
			</p>
		{:else}
			<p>
				{exchange}'s API supports the full account lifecycle, so you do everything here — CPanel
				creates subaccounts, mints their API keys, changes permissions, revokes keys, and
				freezes subaccounts on your behalf.
			</p>
			<ol>
				<li>
					<strong>Once:</strong> register your master account and its master API key below (the
					only manual step — created on {exchange}). Give it a nickname.
				</li>
				<li>
					From then on, use <strong>Account</strong> to create subaccounts and their keys,
					adjust permissions, freeze/unfreeze, and revoke — no trips back to {exchange}.
				</li>
				<li>
					Keys and secrets are stored encrypted; <strong>reveal any secret on demand</strong>
					(there is no one-time copy prompt). Tag keys however you like.
				</li>
			</ol>
			<p class="muted">
				Key creation targets subaccounts only — {exchange} does not allow minting additional
				master-account keys over the API.
			</p>
		{/if}
		<a class="btn" href="/accounts/{exchange.toLowerCase()}?tab=account">Go to Account</a>
	</div>
{:else}
	<!-- Combined registration -->
	<div class="card">
		<h2>Register {registrationOnly ? 'an' : 'the master'} account</h2>
		<p class="muted">
			{#if registrationOnly}
				Register a {exchange} account together with its API key. Give it a nickname for use in the
				panel.
			{:else}
				Register your {exchange} master account and its master API key. Subaccounts and their keys
				are then managed here.
			{/if}
		</p>
		<form class="key-form" on:submit|preventDefault={registerMain}>
			<input placeholder="Nickname" bind:value={reg.label} required />
			{#if schemes.length > 1}
				<select bind:value={reg.scheme}>
					{#each schemes as s}<option value={s}>{s}</option>{/each}
				</select>
			{/if}
			<input placeholder="API key" bind:value={reg.api_key} required />
			<input type="password" placeholder="Secret / private key" bind:value={reg.secret} required />
			<input placeholder="Tags (comma-separated)" bind:value={reg.tags} />
			<button class="btn" disabled={busy || !reg.label.trim()}>Verify &amp; register</button>
		</form>
	</div>

	{#each mains as main (main.id)}
		<div class="card">
			<div class="card-head">
				<h2>{main.label}</h2>
				{#if main.native_uid}<span class="uid">{main.native_uid}</span>{/if}
			</div>

			<h3>API keys</h3>
			{#if main.credentials.length === 0}<p class="muted">No keys registered.</p>{/if}
			{#each main.credentials as cred (cred.id)}
				<div class="cred-row" class:revoked={cred.status !== 'active'}>
					<code>{cred.api_key.slice(0, 10)}…</code>
					<span class="tag">{cred.scheme}</span>
					{#if cred.read_only}<span class="tag">read-only</span>{/if}
					{#if cred.can_withdraw}<span class="tag warn">withdraw</span>{/if}
					<span class="tag" class:ok={cred.status === 'active'}>{cred.status}</span>
					{#each cred.tags ?? [] as t}<span class="tag chip">{t}</span>{/each}
					<div class="row-actions">
						<button class="btn secondary small" disabled={busy} on:click={() => toggleReveal(main, cred)}>
							{revealed[cred.id] ? 'Hide' : 'Reveal'}
						</button>
						<button class="btn secondary small" on:click={() => startTagEdit(cred)}>Tags</button>
						{#if cred.status === 'active'}
							<button class="btn danger small" disabled={busy} on:click={() => revoke(main, cred.id)}>Revoke</button>
						{/if}
					</div>
				</div>
				{#if revealed[cred.id]}
					<div class="reveal">
						<div><span>Key</span><code>{revealed[cred.id].api_key}</code></div>
						<div><span>Secret</span><code>{revealed[cred.id].secret}</code></div>
					</div>
				{/if}
				{#if tagDraft[cred.id] !== undefined}
					<form class="inline-form indent" on:submit|preventDefault={() => saveTags(main, cred)}>
						<input placeholder="Tags (comma-separated)" bind:value={tagDraft[cred.id]} />
						<button class="btn small" disabled={busy}>Save tags</button>
						<button type="button" class="btn secondary small" on:click={() => cancelTagEdit(cred.id)}>Cancel</button>
					</form>
				{/if}
			{/each}

			<h3>Subaccounts</h3>
			{#if main.children.length === 0}<p class="muted">No subaccounts.</p>{/if}
			{#each main.children as sub (sub.id)}
				<div class="sub-block">
					<div class="cred-row">
						<span class="sub-label">{sub.label}</span>
						{#if sub.native_uid}<span class="uid">{sub.native_uid}</span>{/if}
						{#if sub.status === 'frozen'}<span class="tag warn">frozen</span>{/if}
						{#if caps?.subaccount_freeze}
							<button class="btn secondary small" disabled={busy} on:click={() => toggleFreeze(main, sub)}>
								{sub.status === 'frozen' ? 'Unfreeze' : 'Freeze'}
							</button>
						{/if}
					</div>

					{#each sub.credentials as cred (cred.id)}
						<div class="cred-row indent" class:revoked={cred.status !== 'active'}>
							<code>{cred.api_key.slice(0, 10)}…</code>
							<span class="tag">{cred.scheme}</span>
							{#if cred.read_only}<span class="tag">read-only</span>{/if}
							<span class="tag" class:ok={cred.status === 'active'}>{cred.status}</span>
							{#each cred.tags ?? [] as t}<span class="tag chip">{t}</span>{/each}
							<div class="row-actions">
								<button class="btn secondary small" disabled={busy} on:click={() => toggleReveal(sub, cred)}>
									{revealed[cred.id] ? 'Hide' : 'Reveal'}
								</button>
								<button class="btn secondary small" on:click={() => startTagEdit(cred)}>Tags</button>
								{#if caps?.api_key_modify && cred.status === 'active'}
									{#if cred.read_only}
										<button class="btn secondary small" disabled={busy} on:click={() => setReadOnly(sub.id, cred, false)}>Allow trading</button>
									{:else}
										<button class="btn secondary small" disabled={busy} on:click={() => setReadOnly(sub.id, cred, true)}>Make read-only</button>
									{/if}
								{/if}
								{#if cred.status === 'active'}
									<button class="btn danger small" disabled={busy} on:click={() => revoke(sub, cred.id)}>Revoke</button>
								{/if}
							</div>
						</div>
						{#if revealed[cred.id]}
							<div class="reveal indent">
								<div><span>Key</span><code>{revealed[cred.id].api_key}</code></div>
								<div><span>Secret</span><code>{revealed[cred.id].secret}</code></div>
							</div>
						{/if}
						{#if tagDraft[cred.id] !== undefined}
							<form class="inline-form indent" on:submit|preventDefault={() => saveTags(sub, cred)}>
								<input placeholder="Tags (comma-separated)" bind:value={tagDraft[cred.id]} />
								<button class="btn small" disabled={busy}>Save tags</button>
								<button type="button" class="btn secondary small" on:click={() => cancelTagEdit(cred.id)}>Cancel</button>
							</form>
						{/if}
					{/each}

					{#if caps?.api_key_create}
						<form class="inline-form indent" on:submit|preventDefault={() => issueKey(main, sub)}>
							<label class="check">
								<input type="checkbox" checked={keyForm[sub.id]?.trade ?? false}
									on:change={(e) => { keyForm[sub.id] = { trade: e.currentTarget.checked, tags: keyForm[sub.id]?.tags ?? '' }; keyForm = keyForm; }} />
								allow trading
							</label>
							<input placeholder="Tags" value={keyForm[sub.id]?.tags ?? ''}
								on:input={(e) => { keyForm[sub.id] = { trade: keyForm[sub.id]?.trade ?? false, tags: e.currentTarget.value }; keyForm = keyForm; }} />
							<button class="btn small" disabled={busy}>Create key</button>
						</form>
					{/if}
				</div>
			{/each}

			<!-- Subaccount provisioning / registration -->
			{#if registrationOnly}
				{#if subReg[main.id] !== undefined}
					<form class="key-form" on:submit|preventDefault={() => registerSub(main)}>
						<input placeholder="Subaccount nickname" bind:value={subReg[main.id].label} required />
						{#if schemes.length > 1}
							<select bind:value={subReg[main.id].scheme}>
								{#each schemes as s}<option value={s}>{s}</option>{/each}
							</select>
						{/if}
						<input placeholder="API key" bind:value={subReg[main.id].api_key} required />
						<input type="password" placeholder="Secret" bind:value={subReg[main.id].secret} required />
						<input placeholder="Tags" bind:value={subReg[main.id].tags} />
						<button class="btn small" disabled={busy}>Register subaccount</button>
					</form>
				{:else}
					<button class="btn secondary small" on:click={() => startSubReg(main.id)}>Register a subaccount</button>
				{/if}
			{:else}
				<div class="sub-actions">
					{#if caps?.subaccount_list}
						<button class="btn secondary small" disabled={busy} on:click={() => sync(main)}>Sync from {exchange}</button>
					{/if}
					{#if caps?.subaccount_create}
						<form class="inline-form" on:submit|preventDefault={() => addSub(main)}>
							<input placeholder="New subaccount name" bind:value={subName[main.id]} />
							<button class="btn small" disabled={busy || !subName[main.id]?.trim()}>Create subaccount</button>
						</form>
					{/if}
				</div>
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
	.readme p {
		font-size: 0.9375rem;
		line-height: 1.5;
	}
	.readme ol {
		font-size: 0.9375rem;
		line-height: 1.6;
		padding-left: 1.25rem;
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
		text-decoration: none;
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
	.row-actions {
		display: inline-flex;
		gap: 0.375rem;
		margin-left: auto;
	}
	.sub-block {
		padding: 0.5rem 0 0.625rem;
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

	.reveal {
		background: #f6f8fa;
		border: 1px solid #e3e5e8;
		border-radius: 0.375rem;
		padding: 0.5rem 0.75rem;
		margin: 0.25rem 0 0.5rem;
		font-size: 0.8125rem;
	}
	.reveal div {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		margin: 0.15rem 0;
	}
	.reveal span {
		width: 3rem;
		color: #57606a;
	}
	.reveal code {
		word-break: break-all;
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
	.tag.chip {
		background: #ddf4ff;
		color: #0969da;
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
		.tag.chip {
			background: #121d2f;
			color: #4493f8;
		}
		.sub-block {
			border-color: #21262d;
		}
		.reveal {
			background: #0d1117;
			border-color: #30363d;
		}
	}
</style>
