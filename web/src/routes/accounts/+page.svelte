<script lang="ts">
	import { onMount } from 'svelte';
	import {
		api,
		failureMessage,
		type Credential,
		type Exchange,
		type ExchangeAccount,
		type UnmatchedAccount,
		type UnmatchedReason
	} from '#lib/api.ts';
	import {
		autoKind,
		blankChoices,
		exchangeLabel,
		exchanges,
		formFromUnmatched,
		isExchange,
		kindsToOffer,
		kindsToSubmit,
		type KindChoice
	} from '#lib/accounts.ts';

	type Draft = { apiKey: string; apiSecret: string; passphrase: string };

	const reasonLabels: Record<UnmatchedReason, string> = {
		no_exchange_account: '沒有 vault 帳號',
		missing_identity: '缺身分',
		kind_not_enabled: 'kind 未啟用',
		kind_out_of_scope: '一期不做'
	};

	let accounts = $state<ExchangeAccount[]>([]);
	let unmatched = $state<UnmatchedAccount[]>([]);
	let label = $state('');
	let exchange = $state<Exchange>('binance');
	let uid = $state('');
	let choices = $state<KindChoice[]>(blankChoices('binance'));
	let brought = $state(false);
	let drafts = $state<Record<number, Draft>>({});
	let error = $state<string | null>(null);
	let note = $state<string | null>(null);
	let busy = $state(false);

	const watching = $derived(
		accounts.some((account) => account.credentials?.some((item) => item.status === 'unverified'))
	);

	function when(value: string | null | undefined): string {
		if (!value) return '';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return date.toLocaleString('zh-Hant');
	}

	function emptyDraft(): Draft {
		return { apiKey: '', apiSecret: '', passphrase: '' };
	}

	function rememberDrafts(list: ExchangeAccount[]): void {
		for (const account of list) {
			if (!drafts[account.id]) drafts[account.id] = emptyDraft();
		}
	}

	async function load(quiet = false): Promise<void> {
		try {
			const [accountsResult, unmatchedResult] = await Promise.all([
				api.GET('/exchange-accounts'),
				api.GET('/exchange-accounts/unmatched')
			]);
			const accountMessage = failureMessage(accountsResult.error, accountsResult.response);
			const unmatchedMessage = failureMessage(unmatchedResult.error, unmatchedResult.response);
			if (accountMessage || !accountsResult.data || unmatchedMessage || !unmatchedResult.data) {
				if (!quiet) error = accountMessage ?? unmatchedMessage ?? '沒有內容';
				return;
			}
			rememberDrafts(accountsResult.data);
			accounts = accountsResult.data;
			unmatched = unmatchedResult.data;
			if (!quiet) error = null;
		} catch {
			if (!quiet) error = '連不到 API';
		}
	}

	function exchangeChanged(event: Event): void {
		const next = (event.currentTarget as HTMLSelectElement).value;
		if (!isExchange(next)) return;
		exchange = next;
		brought = false;
		choices = blankChoices(next);
	}

	function bringIn(row: UnmatchedAccount): void {
		const form = formFromUnmatched(row, unmatched);
		if (!form) return;
		exchange = form.exchange;
		uid = form.uid;
		choices = form.choices;
		brought = true;
		note = null;
	}

	function clearBrought(): void {
		brought = false;
		uid = '';
		choices = blankChoices(exchange);
	}

	function toggle(kind: string): void {
		choices = choices.map((choice) =>
			choice.kind === kind && !choice.disabled ? { ...choice, checked: !choice.checked } : choice
		);
	}

	function failed(result: { error?: unknown; response: Response }, fallback: string): string | null {
		if (result.response.ok) return null;
		return failureMessage(result.error, result.response) ?? fallback;
	}

	async function create(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		error = null;
		note = null;
		busy = true;
		try {
			const kinds = kindsToSubmit(exchange, choices);
			const result = await api.POST('/exchange-accounts', {
				body: {
					exchange,
					exch_account_uid: uid.trim(),
					label: label.trim(),
					...(kinds === null ? {} : { kinds })
				}
			});
			const message = failed(result, '沒有內容');
			if (message || !result.data) {
				error = message ?? '沒有內容';
				return;
			}
			label = '';
			uid = '';
			brought = false;
			choices = blankChoices(exchange);
			await load();
		} catch {
			error = '連不到 API';
		} finally {
			busy = false;
		}
	}

	async function register(event: SubmitEvent, account: ExchangeAccount): Promise<void> {
		event.preventDefault();
		const draft = drafts[account.id];
		if (!draft) return;
		error = null;
		note = null;
		busy = true;
		try {
			const result = await api.POST('/exchange-accounts/{id}/credentials', {
				params: { path: { id: account.id } },
				body:
					account.exchange === 'okx'
						? { api_key: draft.apiKey, api_secret: draft.apiSecret, passphrase: draft.passphrase }
						: { api_key: draft.apiKey, api_secret: draft.apiSecret }
			});
			const message = failed(result, '沒有內容');
			if (message || !result.data) {
				error = message ?? '沒有內容';
				return;
			}
			drafts[account.id] = emptyDraft();
			note = `${account.label} 已登記。狀態 unverified。secret 已清掉，不會再顯示。`;
			await load();
		} catch {
			error = '連不到 API';
		} finally {
			busy = false;
		}
	}

	async function run(task: () => Promise<string | null>): Promise<void> {
		error = null;
		note = null;
		busy = true;
		try {
			const message = await task();
			if (message) {
				error = message;
				return;
			}
			await load();
		} catch {
			error = '連不到 API';
		} finally {
			busy = false;
		}
	}

	function disableCredential(accountId: number, credential: Credential): void {
		void run(async () => {
			const result = await api.POST('/exchange-accounts/{id}/credentials/{cid}/disable', {
				params: { path: { id: accountId, cid: credential.id } }
			});
			return failed(result, '沒有內容');
		});
	}

	function deleteCredential(accountId: number, credential: Credential): void {
		void run(async () => {
			const result = await api.DELETE('/exchange-accounts/{id}/credentials/{cid}', {
				params: { path: { id: accountId, cid: credential.id } }
			});
			return failed(result, '沒有內容');
		});
	}

	function verifyCredential(accountId: number, credential: Credential): void {
		void run(async () => {
			const result = await api.POST('/exchange-accounts/{id}/credentials/{cid}/verify', {
				params: { path: { id: accountId, cid: credential.id } }
			});
			return failed(result, '沒有內容');
		});
	}

	function enableKind(accountId: number, kind: string): void {
		void run(async () => {
			const result = await api.POST('/exchange-accounts/{id}/kinds', {
				params: { path: { id: accountId } },
				body: { kind }
			});
			return failed(result, '沒有內容');
		});
	}

	function disableKind(accountId: number, kind: string): void {
		void run(async () => {
			const result = await api.POST('/exchange-accounts/{id}/kinds/{kind}/disable', {
				params: { path: { id: accountId, kind } }
			});
			return failed(result, '沒有內容');
		});
	}

	$effect(() => {
		if (!watching) return;
		const timer = setInterval(() => {
			void load(true);
		}, 3000);
		return () => clearInterval(timer);
	});

	onMount(() => {
		void load();
	});
</script>

<svelte:head>
	<title>帳戶 · cpanel</title>
</svelte:head>

<h1>帳戶</h1>
{#if error}
	<p class="error">{error}</p>
{/if}
{#if note}
	<p>{note}</p>
{/if}
{#if watching}
	<p class="muted">有 unverified 的 key，每 3 秒重新讀列表。驗證由 worker 完成，這頁不打交易所。</p>
{/if}

<h2>建立</h2>
<form class="row" onsubmit={create}>
	<label>名稱 <input bind:value={label} autocomplete="off" required /></label>
	<label>
		交易所
		<select value={exchange} onchange={exchangeChanged}>
			{#each exchanges as name (name)}
				<option value={name}>{exchangeLabel[name]}</option>
			{/each}
		</select>
	</label>
	<label>uid <input bind:value={uid} autocomplete="off" required /></label>
	{#if exchange === 'binance'}
		{#each choices as choice (choice.kind)}
			<label>
				<input
					type="checkbox"
					checked={choice.checked}
					disabled={choice.disabled}
					onchange={() => toggle(choice.kind)}
				/>
				{choice.kind}
				{#if choice.disabled}<span class="muted">一期不做</span>{/if}
			</label>
		{/each}
	{:else}
		<p>將啟用 {autoKind(exchange)}</p>
	{/if}
	<button class="primary" type="submit" disabled={busy}>建立</button>
	{#if brought}
		<button type="button" disabled={busy} onclick={clearBrought}>清除帶入</button>
	{/if}
</form>
{#if brought}
	<p class="muted">從從未對上的列帶入。一期不做的 kind 不能勾，也不會送出。</p>
{/if}

<h2>列表</h2>
<button type="button" disabled={busy} onclick={() => load()}>重新整理</button>
{#if accounts.length === 0}
	<p class="muted">還沒有帳號。</p>
{:else}
	{#each accounts as account (account.id)}
		<article>
			<h2>{account.label}</h2>
			<p>
				{exchangeLabel[account.exchange]} · {account.exch_account_uid}
			</p>
			<p>
				{#if account.api_ids && account.api_ids.length > 0}
					api_id
					{account.api_ids
						.map((item) => `${item.api_id} ${item.name} ${item.kind}${item.deleted_at ? ' 已刪' : ''}`)
						.join('、')}
				{:else}
					<span class="muted">還沒有對上的 api_id。</span>
				{/if}
			</p>
			<table>
				<thead>
					<tr>
						<th>kind</th>
						<th>驗證</th>
						<th>錯誤</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each account.kinds as kind (kind.kind)}
						<tr>
							<td>{kind.kind}</td>
							<td>{kind.disabled_at ? '已停用' : when(kind.verified_at)}</td>
							<td class="wrap">{kind.last_error ?? ''}</td>
							<td>
								{#if kind.disabled_at}
									<button type="button" disabled={busy} onclick={() => enableKind(account.id, kind.kind)}>
										重新啟用
									</button>
								{:else}
									<button type="button" disabled={busy} onclick={() => disableKind(account.id, kind.kind)}>
										停用
									</button>
								{/if}
							</td>
						</tr>
					{/each}
					{#each kindsToOffer(account.exchange, account.kinds.map((kind) => kind.kind)) as kind (kind)}
						<tr>
							<td>{kind}</td>
							<td class="muted">尚未啟用</td>
							<td></td>
							<td>
								<button type="button" disabled={busy} onclick={() => enableKind(account.id, kind)}>啟用</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
			{#if account.credentials && account.credentials.length > 0}
				<table>
					<thead>
						<tr>
							<th>末四碼</th>
							<th>狀態</th>
							<th>原因</th>
							<th></th>
						</tr>
					</thead>
					<tbody>
						{#each account.credentials as credential (credential.id)}
							<tr>
								<td class="last4">····{credential.api_key_last4}</td>
								<td>{credential.status}</td>
								<td class="wrap">{credential.failure_reason ?? ''}</td>
								<td class="actions">
									{#if credential.status !== 'disabled'}
										<button type="button" disabled={busy} onclick={() => disableCredential(account.id, credential)}>
											停用
										</button>
									{/if}
									<button type="button" class="danger" disabled={busy} onclick={() => deleteCredential(account.id, credential)}>
										刪除
									</button>
									{#if credential.status === 'rejected' || credential.status === 'auth_failed'}
										<button type="button" disabled={busy} onclick={() => verifyCredential(account.id, credential)}>
											再驗證
										</button>
									{/if}
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			{:else}
				<p class="muted">還沒有 key。</p>
			{/if}
			{#if drafts[account.id]}
				<form class="row" autocomplete="off" onsubmit={(event) => register(event, account)}>
					<label>
						api key
						<input bind:value={drafts[account.id].apiKey} autocomplete="off" required />
					</label>
					<label>
						secret
						<input type="password" bind:value={drafts[account.id].apiSecret} autocomplete="new-password" required />
					</label>
					{#if account.exchange === 'okx'}
						<label>
							passphrase
							<input
								type="password"
								bind:value={drafts[account.id].passphrase}
								autocomplete="new-password"
								required
							/>
						</label>
					{/if}
					<button type="submit" disabled={busy}>登記</button>
				</form>
			{/if}
		</article>
	{/each}
{/if}

<h2>從未對上</h2>
{#if unmatched.length === 0}
	<p class="muted">沒有。</p>
{:else}
	<div class="scroll">
		<table>
			<thead>
				<tr>
					<th>原因</th>
					<th>交易所</th>
					<th>uid</th>
					<th>缺的 kind</th>
					<th>api_id</th>
					<th></th>
				</tr>
			</thead>
			<tbody>
				{#each unmatched as row (`${row.reason}:${row.exchange ?? ''}:${row.exch_account_uid ?? ''}:${row.api_ids.map((item) => item.api_id).join(',')}`)}
					<tr>
						<td>{reasonLabels[row.reason]}</td>
						<td>{row.exchange && isExchange(row.exchange) ? exchangeLabel[row.exchange] : (row.exchange ?? '')}</td>
						<td>{row.exch_account_uid ?? ''}</td>
						<td>{row.missing_kinds.join(', ')}</td>
						<td class="wrap">
							{row.api_ids
								.map((item) => `${item.api_id} ${item.name} ${item.kind}${item.deleted_at ? ' 已刪' : ''}`)
								.join(', ')}
						</td>
						<td>
							{#if formFromUnmatched(row, unmatched)}
								<button type="button" disabled={busy} onclick={() => bringIn(row)}>帶入</button>
							{:else}
								<span class="muted">不能帶入</span>
							{/if}
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	</div>
{/if}

<style>
	h2 {
		font-size: 1.1rem;
		margin: 1.5rem 0 0.5rem;
	}

	article {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: 6px;
		padding: 0.75rem 1rem;
		margin: 0.75rem 0;
	}

	article h2 {
		margin: 0 0 0.25rem;
	}

	.scroll {
		overflow-x: auto;
	}

	td.wrap {
		white-space: normal;
		max-width: 16rem;
	}

	.last4 {
		font-family: var(--mono);
	}

	.actions {
		white-space: nowrap;
	}

	form.row label {
		display: flex;
		gap: 0.35rem;
		align-items: center;
	}
</style>
