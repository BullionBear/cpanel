<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { getCurrentUser } from '$lib/api';

	onMount(async () => {
		// Check authentication
		const user = await getCurrentUser();
		if (!user) {
			goto('/');
		}
	});
</script>

<svelte:head>
	<title>CPanel - Home</title>
</svelte:head>

<div class="home-page">
	<h1>Home</h1>
	<p class="placeholder">This page will be populated once the backend is ready.</p>
</div>

<style>
	.home-page {
		padding: 0;
		width: 100%;
		max-width: 100%;
		box-sizing: border-box;
	}

	h1 {
		margin: 0 0 16px 0;
		font-size: 32px;
		font-weight: 700;
		color: #1f2937;
		word-wrap: break-word;
	}

	.placeholder {
		color: #6b7280;
		font-size: 16px;
		margin: 0;
		word-wrap: break-word;
	}

	@media (max-width: 1024px) {
		h1 {
			font-size: 28px;
		}
	}

	@media (max-width: 768px) {
		h1 {
			font-size: 24px;
		}

		.placeholder {
			font-size: 14px;
		}
	}
</style>

