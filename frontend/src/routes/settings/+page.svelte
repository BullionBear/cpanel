<script lang="ts">
	import { onMount } from 'svelte';
	import { user, login } from '$lib/auth';
	import { ApiError } from '$lib/api';
	import {
		authMethodLabel,
		createApiToken,
		deleteApiToken,
		deviceLabel,
		fetchAuditHistory,
		getApiToken,
		operationLabel,
		type AuditEntry
	} from '$lib/settings';

	let apiToken: string | null = null;
	let loadingToken = false;
	let creatingToken = false;
	let deletingToken = false;
	let tokenError = '';
	let tokenNotice = '';
	let copied = false;

	let entries: AuditEntry[] = [];
	let loadingAudit = false;
	let auditError = '';

	async function loadToken() {
		loadingToken = true;
		tokenError = '';
		try {
			apiToken = await getApiToken();
		} catch (e) {
			apiToken = null;
			tokenError =
				e instanceof ApiError && e.status === 401
					? 'Please log in with Discord to manage API keys.'
					: e instanceof Error
						? e.message
						: 'Failed to load API key.';
		} finally {
			loadingToken = false;
		}
	}

	async function loadAudit() {
		loadingAudit = true;
		auditError = '';
		try {
			entries = await fetchAuditHistory(100);
		} catch (e) {
			entries = [];
			auditError =
				e instanceof ApiError && e.status === 401
					? 'Please log in to view audit history.'
					: 'Failed to load audit history.';
		} finally {
			loadingAudit = false;
		}
	}

	async function handleCreate() {
		creatingToken = true;
		tokenError = '';
		tokenNotice = '';
		try {
			apiToken = await createApiToken();
			tokenNotice = apiToken
				? 'API key issued. Copy it now — you can also retrieve it later until you revoke it.'
				: '';
			await loadAudit();
		} catch (e) {
			tokenError = e instanceof Error ? e.message : 'Failed to create API key.';
		} finally {
			creatingToken = false;
		}
	}

	async function handleDelete() {
		if (!confirm('Revoke this API key? Scripts using it will stop working immediately.')) return;
		deletingToken = true;
		tokenError = '';
		tokenNotice = '';
		try {
			await deleteApiToken();
			apiToken = null;
			tokenNotice = 'API key revoked.';
			await loadAudit();
		} catch (e) {
			tokenError = e instanceof Error ? e.message : 'Failed to revoke API key.';
		} finally {
			deletingToken = false;
		}
	}

	async function copyToken() {
		if (!apiToken) return;
		try {
			await navigator.clipboard.writeText(apiToken);
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} catch {
			tokenError = 'Could not copy to clipboard.';
		}
	}

	function ts(value: string | null): string {
		return value ? new Date(value).toLocaleString() : '—';
	}

	onMount(async () => {
		await Promise.all([loadToken(), loadAudit()]);
	});
</script>

<svelte:head>
	<title>Settings · CPanel</title>
</svelte:head>

<div class="page">
	<div class="page-head">
		<h1>Settings</h1>
		<span class="count">Account & access</span>
	</div>

	{#if !$user}
		<section class="panel">
			<h2>Sign in required</h2>
			<p class="muted">
				Log in with Discord to issue a panel API key and review your activity history.
			</p>
			<button class="btn" type="button" on:click={login}>Login with Discord</button>
		</section>
	{:else}
		<section class="panel">
			<h2>API key</h2>
			<p class="muted">
				Issue a long-lived key after Discord sign-in. Use it as
				<code>Authorization: Bearer api_…</code> for CAPI requests. Revoking deletes the key
				immediately.
			</p>

			{#if tokenError}
				<div class="banner error" role="alert">{tokenError}</div>
			{/if}
			{#if tokenNotice}
				<div class="banner notice">{tokenNotice}</div>
			{/if}

			<div class="token-row">
				<input
					class="token-input"
					type="text"
					readonly
					value={apiToken || ''}
					placeholder={loadingToken
						? 'Loading…'
						: 'No API key yet. Click Issue to create one.'}
					disabled={loadingToken}
				/>
				{#if apiToken}
					<button class="btn secondary small" type="button" on:click={copyToken}>
						{copied ? 'Copied' : 'Copy'}
					</button>
				{/if}
			</div>

			<div class="actions">
				{#if apiToken}
					<button
						class="btn danger"
						type="button"
						on:click={handleDelete}
						disabled={deletingToken || loadingToken || creatingToken}
					>
						{deletingToken ? 'Revoking…' : 'Revoke'}
					</button>
					<button
						class="btn"
						type="button"
						on:click={handleCreate}
						disabled={creatingToken || loadingToken || deletingToken}
					>
						{creatingToken ? 'Reissuing…' : 'Reissue'}
					</button>
				{:else}
					<button
						class="btn"
						type="button"
						on:click={handleCreate}
						disabled={creatingToken || loadingToken}
					>
						{creatingToken ? 'Issuing…' : 'Issue API key'}
					</button>
				{/if}
			</div>
		</section>

		<section class="panel">
			<div class="section-head">
				<div>
					<h2>Audit history</h2>
					<p class="muted">
						Your recent activity — IP, device, auth method, operation, and return code.
					</p>
				</div>
				<button
					class="btn secondary small"
					type="button"
					on:click={loadAudit}
					disabled={loadingAudit}
				>
					{loadingAudit ? 'Refreshing…' : 'Refresh'}
				</button>
			</div>

			{#if auditError}
				<div class="banner error" role="alert">{auditError}</div>
			{:else if loadingAudit && entries.length === 0}
				<div class="banner muted">Loading…</div>
			{:else if entries.length === 0}
				<div class="banner muted">No activity recorded yet.</div>
			{:else}
				<div class="table-wrap" class:dim={loadingAudit}>
					<table>
						<thead>
							<tr>
								<th>Time</th>
								<th>Operation</th>
								<th>Auth</th>
								<th>IP</th>
								<th>Device</th>
								<th class="right">Code</th>
								<th>Status</th>
							</tr>
						</thead>
						<tbody>
							{#each entries as entry (entry.id)}
								<tr>
									<td class="mono">{ts(entry.started_at)}</td>
									<td>
										<div class="op">{operationLabel(entry)}</div>
										{#if entry.error}
											<div class="err-detail">{entry.error}</div>
										{/if}
									</td>
									<td>
										<span class="badge" class:api={entry.auth_method === 'api_key'}>
											{authMethodLabel(entry.auth_method)}
										</span>
									</td>
									<td class="mono">{entry.ip_address || '—'}</td>
									<td title={entry.user_agent || ''}>{deviceLabel(entry.user_agent)}</td>
									<td class="right mono">{entry.return_code ?? '—'}</td>
									<td>
										<span
											class="tag"
											class:ok={entry.status === 'succeeded'}
											class:warn={entry.status === 'started'}
											class:fail={entry.status === 'failed'}
										>
											{entry.status}
										</span>
									</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
			{/if}
		</section>
	{/if}
</div>

<style>
	.page {
		width: 100%;
		max-width: 72rem;
		margin: 0 auto;
	}
	.page-head {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin-bottom: 1.5rem;
	}
	.page-head h1 {
		margin: 0;
		font-size: 1.75rem;
	}
	.count {
		color: var(--text-muted);
		font-size: 0.875rem;
	}

	.panel {
		background: var(--white);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
		padding: 1.35rem 1.5rem;
		margin-bottom: 1.25rem;
	}
	.panel h2 {
		margin: 0 0 0.35rem;
		font-size: 1.125rem;
	}
	.panel .muted {
		margin: 0 0 1rem;
		max-width: 40rem;
	}
	.panel code {
		font-family: var(--mono);
		font-size: 0.8125rem;
		background: var(--surface);
		padding: 0.1rem 0.35rem;
		border-radius: 4px;
	}

	.section-head {
		display: flex;
		align-items: flex-start;
		justify-content: space-between;
		gap: 1rem;
		margin-bottom: 0.25rem;
	}

	.banner {
		padding: 0.7rem 0.9rem;
		border-radius: var(--radius-sm);
		font-size: 0.8125rem;
		margin-bottom: 1rem;
	}
	.banner.error {
		color: var(--danger-text);
		background: var(--danger-bg);
	}
	.banner.notice {
		color: var(--success-text);
		background: var(--success-bg);
	}
	.banner.muted {
		color: var(--text-muted);
		background: var(--surface);
	}

	.token-row {
		display: flex;
		gap: 0.5rem;
		align-items: center;
		margin-bottom: 1rem;
	}
	.token-input {
		flex: 1;
		min-width: 0;
		font-family: var(--mono);
		font-size: 0.75rem;
		background: var(--surface);
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.5rem;
		justify-content: flex-end;
	}

	.table-wrap {
		overflow-x: auto;
		border: 1px solid var(--border);
		border-radius: var(--radius-md);
	}
	.table-wrap.dim {
		opacity: 0.65;
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8125rem;
	}
	th,
	td {
		padding: 0.65rem 0.75rem;
		text-align: left;
		border-bottom: 1px solid var(--border-light);
		vertical-align: top;
	}
	th {
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.04em;
		color: var(--text-muted);
		background: var(--surface);
	}
	tr:last-child td {
		border-bottom: none;
	}
	.right {
		text-align: right;
	}
	.mono {
		font-family: var(--mono);
		font-size: 0.75rem;
	}
	.op {
		font-weight: 500;
		word-break: break-word;
	}
	.err-detail {
		margin-top: 0.2rem;
		color: var(--danger-text);
		font-size: 0.75rem;
	}
	.badge.api {
		background: var(--info-bg);
		border-color: transparent;
		color: var(--info-text);
	}
	.tag.fail {
		background: var(--danger-bg);
		border-color: transparent;
		color: var(--danger-text);
	}

	@media (max-width: 720px) {
		.token-row {
			flex-direction: column;
			align-items: stretch;
		}
		.actions {
			justify-content: stretch;
		}
		.actions .btn {
			flex: 1;
		}
		.section-head {
			flex-direction: column;
		}
	}
</style>
