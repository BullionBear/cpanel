<script lang="ts">
	import { page } from '$app/stores';

	$: errorMessage = $page.url.searchParams.get('error') ?? '';
</script>

<svelte:head>
	<title>CPanel — Home</title>
	<meta name="description" content="CPanel — auxiliary web app for universal trading operations" />
</svelte:head>

{#if errorMessage}
	<p class="error" role="alert">{errorMessage}</p>
{/if}

<section class="intro">
	<span class="tag">Control panel</span>
	<h1>Universal trading operations, in <span class="accent-word">one panel</span>.</h1>
	<p>
		CPanel is an auxiliary web app for handling universal trading operations. It provides a
		single control panel to manage accounts, positions, and trading history across exchanges.
	</p>
	<p class="muted">
		The platform is being rebuilt from the ground up — more panels will appear in the top
		navigation as they come back online.
	</p>
	<div class="actions">
		<a class="btn" href="/accounts">Manage accounts</a>
		<a class="btn secondary" href="/symbols">Browse symbols</a>
	</div>
</section>

<style>
	.intro {
		width: 100%;
		max-width: 46rem;
		margin: 2rem auto;
	}
	.tag {
		margin-bottom: 1.25rem;
	}
	h1 {
		font-size: clamp(2rem, 5vw, 3.25rem);
		line-height: 1.05;
		letter-spacing: -0.045em;
		margin: 0 0 1rem;
	}
	.accent-word {
		background: var(--accent);
		border-radius: var(--radius-xs);
		padding: 0 0.35rem;
	}
	p {
		font-size: 1.0625rem;
		line-height: 1.6;
		color: var(--text-muted);
		margin: 0 0 0.75rem;
		max-width: 34rem;
	}
	.actions {
		display: flex;
		flex-wrap: wrap;
		gap: 0.75rem;
		margin-top: 1.75rem;
	}

	.error {
		width: 100%;
		max-width: 46rem;
		box-sizing: border-box;
		font-size: 0.8125rem;
		line-height: 1.5;
		color: var(--danger-text);
		background: var(--danger-bg);
		border: 1px solid transparent;
		border-radius: var(--radius-sm);
		padding: 0.7rem 0.9rem;
		margin: 0 auto 1.25rem;
	}
</style>
