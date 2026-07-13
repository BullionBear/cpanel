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
	<h1>Welcome to CPanel</h1>
	<p>
		CPanel is an auxiliary web app for handling universal trading operations. It provides a
		single control panel to manage accounts, positions, and trading history across exchanges.
	</p>
	<p class="muted">
		The platform is being rebuilt from the ground up — more panels will appear in the top
		navigation as they come back online.
	</p>
</section>

<style>
	.intro {
		max-width: 640px;
	}
	h1 {
		font-size: 22px;
		font-weight: 600;
		margin: 0 0 12px;
	}
	p {
		font-size: 15px;
		line-height: 1.65;
		margin: 0 0 12px;
	}
	.muted {
		color: #57606a;
		font-size: 14px;
	}

	.error {
		max-width: 640px;
		font-size: 13px;
		line-height: 1.5;
		color: #cf222e;
		background: #ffebe9;
		border: 1px solid #ff818266;
		border-radius: 8px;
		padding: 10px 12px;
		margin: 0 0 20px;
	}

	@media (prefers-color-scheme: dark) {
		.muted { color: #8b949e; }
		.error {
			color: #ff7b72;
			background: #490202;
			border-color: #f8514966;
		}
	}
</style>
