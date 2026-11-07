<script lang="ts">
	import { onMount } from 'svelte';
	import { api, failureMessage, type Token, type TokenCreated } from '#lib/api.ts';

	let tokens = $state<Token[]>([]);
	let name = $state('');
	let error = $state<string | null>(null);
	let created = $state<TokenCreated | null>(null);
	let busy = $state(false);

	function when(value: string | undefined): string {
		if (!value) return '';
		const date = new Date(value);
		if (Number.isNaN(date.getTime())) return value;
		return date.toLocaleString('zh-Hant');
	}

	async function load(): Promise<void> {
		error = null;
		try {
			const result = await api.GET('/tokens');
			const message = failureMessage(result.error, result.response);
			if (message || !result.data) {
				error = message ?? '沒有內容';
				return;
			}
			tokens = result.data;
		} catch {
			error = '連不到 API';
		}
	}

	async function create(event: SubmitEvent): Promise<void> {
		event.preventDefault();
		error = null;
		created = null;
		busy = true;
		try {
			const result = await api.POST('/tokens', { body: { name: name.trim() } });
			const message = failureMessage(result.error, result.response);
			if (message || !result.data) {
				error = message ?? '沒有內容';
				return;
			}
			created = result.data;
			name = '';
			await load();
		} catch {
			error = '連不到 API';
		} finally {
			busy = false;
		}
	}

	async function revoke(id: string): Promise<void> {
		error = null;
		busy = true;
		try {
			const result = await api.DELETE('/tokens/{id}', { params: { path: { id } } });
			if (result.response.status !== 204) {
				error = failureMessage(result.error, result.response) ?? `HTTP ${result.response.status}`;
				return;
			}
			if (created?.id === id) created = null;
			await load();
		} catch {
			error = '連不到 API';
		} finally {
			busy = false;
		}
	}

	onMount(() => {
		void load();
	});
</script>

<svelte:head>
	<title>Settings · cpanel</title>
</svelte:head>

<h1>Settings</h1>
<p class="muted">建立 bearer token。明文只在建立當下出現一次，資料庫只存 SHA-256。</p>

<form class="row" onsubmit={create}>
	<label>
		名稱
		<input name="name" maxlength="80" bind:value={name} autocomplete="off" />
	</label>
	<button class="primary" type="submit" disabled={busy || name.trim() === ''}>建立</button>
</form>

{#if error}
	<p class="error" role="alert">{error}</p>
{/if}

{#if created}
	<p role="status">這是唯一一次看得到這組 token。離開頁面之後就不會再顯示。</p>
	<input class="secret" readonly value={created.token} aria-label="token 明文" />
{/if}

{#if tokens.length === 0}
	<p class="muted">還沒有 token。</p>
{:else}
	<table>
		<thead>
			<tr>
				<th>名稱</th>
				<th>建立時間</th>
				<th>上次使用</th>
				<th></th>
			</tr>
		</thead>
		<tbody>
			{#each tokens as token (token.id)}
				<tr>
					<td>{token.name}</td>
					<td>{when(token.created_at)}</td>
					<td>{token.last_used_at ? when(token.last_used_at) : '尚未使用'}</td>
					<td><button class="danger" type="button" disabled={busy} onclick={() => revoke(token.id)}>撤銷</button></td>
				</tr>
			{/each}
		</tbody>
	</table>
{/if}
