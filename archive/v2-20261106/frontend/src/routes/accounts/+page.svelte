<script lang="ts">
	import { onMount } from 'svelte';
	import { ApiError } from '$lib/api';
	import { fetchTree, type AccountNode, type AccountInfo } from '$lib/accounts';

	let tree: AccountNode[] = [];
	let loading = true;
	let error = '';

	$: byExchange = tree.reduce<Record<string, AccountNode[]>>((acc, node) => {
		(acc[node.exchange] ??= []).push(node);
		return acc;
	}, {});

	function credSummary(account: AccountInfo): string {
		const active = account.credentials.filter((c) => c.status === 'active');
		if (active.length === 0) return 'no keys';
		const trading = active.filter((c) => c.read_only === false).length;
		return `${active.length} key${active.length > 1 ? 's' : ''}${trading ? ` · ${trading} trading` : ''}`;
	}

	function accountTags(account: AccountInfo): string[] {
		const tags = account.credentials.flatMap((c) => c.tags ?? []);
		return [...new Set(tags)];
	}

	onMount(async () => {
		try {
			tree = await fetchTree();
		} catch (e) {
			error =
				e instanceof ApiError && e.status === 401
					? 'Please log in to view accounts.'
					: 'Failed to load accounts.';
		} finally {
			loading = false;
		}
	});
</script>

<svelte:head>
	<title>Accounts · CPanel</title>
</svelte:head>

<div class="page-head">
	<h1>Accounts</h1>
</div>

{#if loading}
	<p class="muted">Loading…</p>
{:else if error}
	<p class="error">{error}</p>
{:else if tree.length === 0}
	<div class="empty">
		<p>No accounts registered yet.</p>
		<p class="muted">Pick an exchange in the sidebar to register a main account and its API key.</p>
	</div>
{:else}
	{#each Object.entries(byExchange) as [exchange, accounts]}
		<div class="exchange-block">
			<h2>{exchange}</h2>
			{#each accounts as main}
				<div class="account-card">
					<div class="account-row main">
						<div>
							<span class="label">{main.label}</span>
							{#if main.native_uid}<span class="uid">{main.native_uid}</span>{/if}
							{#each accountTags(main) as t}<span class="chip">{t}</span>{/each}
						</div>
						<span class="creds">{credSummary(main)}</span>
					</div>
					{#each main.children as sub}
						<div class="account-row sub">
							<div>
								<span class="tree-line">└</span>
								<span class="label">{sub.label}</span>
								{#if sub.native_uid}<span class="uid">{sub.native_uid}</span>{/if}
								{#if sub.status === 'frozen'}<span class="chip warn">frozen</span>{/if}
								{#each accountTags(sub) as t}<span class="chip">{t}</span>{/each}
							</div>
							<span class="creds">{credSummary(sub)}</span>
						</div>
					{/each}
				</div>
			{/each}
		</div>
	{/each}
{/if}

<style>
	.page-head {
		margin-bottom: 1.25rem;
	}
	h1 {
		margin: 0;
		font-size: 1.5rem;
	}
	h2 {
		margin: 0 0 0.625rem;
		font-size: 0.75rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.06em;
		color: var(--text-dim);
	}

	.empty {
		padding: 2.5rem 1rem;
		text-align: center;
		background: var(--surface);
		border: 1px dashed var(--border);
		border-radius: var(--radius);
	}

	.exchange-block {
		margin-bottom: 1.75rem;
	}

	.account-card {
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
		margin-bottom: 0.75rem;
		overflow: hidden;
	}

	.account-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.85rem 1.1rem;
		font-size: 0.875rem;
	}
	.account-row.main {
		font-weight: 700;
	}
	.account-row.sub {
		border-top: 1px solid var(--border-light);
		padding-left: 1.75rem;
	}

	.tree-line {
		color: var(--text-dim);
		margin-right: 0.375rem;
	}
	.uid {
		margin-left: 0.625rem;
		font-size: 0.75rem;
		color: var(--text-dim);
		font-weight: 400;
	}
	.chip {
		margin-left: 0.375rem;
	}
	.creds {
		font-size: 0.8125rem;
		color: var(--text-muted);
		font-weight: 500;
	}
</style>
