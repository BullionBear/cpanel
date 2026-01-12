<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { getCurrentUser } from '$lib/api';
	import Marquee from '../../../components/Marquee.svelte';

	onMount(async () => {
		// Check authentication
		const user = await getCurrentUser();
		if (!user) {
			goto('/');
		}
	});
</script>

<svelte:head>
	<title>CPanel - Execution</title>
</svelte:head>

<div class="execution-page">
	<Marquee />
	<div class="execution-content">
		<h1>Execution</h1>
		<p class="placeholder">This page will be populated with the layout you provide.</p>
	</div>
</div>

<style>
	.execution-page {
		padding: 0;
		width: 100%;
		max-width: 100%;
		box-sizing: border-box;
		display: flex;
		flex-direction: column;
	}

	.execution-content {
		padding: 24px;
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
		.execution-content {
			padding: 20px;
		}

		h1 {
			font-size: 28px;
		}
	}

	@media (max-width: 768px) {
		.execution-content {
			padding: 16px;
		}

		h1 {
			font-size: 24px;
		}

		.placeholder {
			font-size: 14px;
		}
	}
</style>
