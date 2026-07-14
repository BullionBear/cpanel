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
		font-size: 1.375rem;
	}
	h2 {
		margin: 0 0 0.625rem;
		font-size: 0.9375rem;
		color: #57606a;
	}

	.muted {
		color: #8b949e;
		font-size: 0.875rem;
	}
	.error {
		color: #cf222e;
		font-size: 0.875rem;
	}

	.empty {
		padding: 2.5rem 1rem;
		text-align: center;
		background: #ffffff;
		border: 1px dashed #d0d7de;
		border-radius: 0.625rem;
	}

	.exchange-block {
		margin-bottom: 1.75rem;
	}

	.account-card {
		background: #ffffff;
		border: 1px solid #e3e5e8;
		border-radius: 0.625rem;
		margin-bottom: 0.75rem;
		overflow: hidden;
	}

	.account-row {
		display: flex;
		align-items: center;
		justify-content: space-between;
		padding: 0.75rem 1rem;
		font-size: 0.875rem;
	}
	.account-row.main {
		font-weight: 600;
	}
	.account-row.sub {
		border-top: 1px solid #f0f2f4;
		padding-left: 1.75rem;
	}

	.tree-line {
		color: #a8b1ba;
		margin-right: 0.375rem;
	}
	.uid {
		margin-left: 0.625rem;
		font-size: 0.75rem;
		color: #8b949e;
		font-weight: 400;
	}
	.chip {
		margin-left: 0.375rem;
		padding: 0.05rem 0.4rem;
		border-radius: 999px;
		background: #ddf4ff;
		color: #0969da;
		font-size: 0.6875rem;
		font-weight: 500;
	}
	.chip.warn {
		background: #fff1e5;
		color: #bc4c00;
	}
	.creds {
		font-size: 0.8125rem;
		color: #57606a;
		font-weight: 400;
	}

	@media (prefers-color-scheme: dark) {
		h2 {
			color: #8b949e;
		}
		.empty {
			background: #161b22;
			border-color: #30363d;
		}
		.account-card {
			background: #161b22;
			border-color: #30363d;
		}
		.account-row.sub {
			border-color: #21262d;
		}
		.creds {
			color: #8b949e;
		}
	}
</style>
