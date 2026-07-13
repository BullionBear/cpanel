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
		width: 100%;
		max-width: 60%;
		margin: 0 auto;
	}
	h1 {
		font-size: 1.375rem;
		font-weight: 600;
		margin: 0 0 0.75rem;
	}
	p {
		font-size: 0.9375rem;
		line-height: 1.65;
		margin: 0 0 0.75rem;
	}
	.muted {
		color: #57606a;
		font-size: 0.875rem;
	}

	.error {
		width: 100%;
		max-width: 60%;
		box-sizing: border-box;
		font-size: 0.8125rem;
		line-height: 1.5;
		color: #cf222e;
		background: #ffebe9;
		border: 1px solid #ff818266;
		border-radius: 0.5rem;
		padding: 0.625rem 0.75rem;
		margin: 0 auto 1.25rem;
	}

	@media (max-width: 48rem) {
		.intro,
		.error {
			max-width: 100%;
		}
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
