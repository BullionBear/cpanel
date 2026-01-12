<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { getCurrentUser } from '$lib/api';
	import Marquee from '../../../components/Marquee.svelte';
	import OrderPanel from '../../../components/OrderPanel.svelte';
	import BalancePanel from '../../../components/BalancePanel.svelte';

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
		<div class="panels-row">
			<OrderPanel />
			<BalancePanel />
		</div>
		<div class="info-section">
			<div class="placeholder-card">
				<h2>Open Orders</h2>
				<p>Open orders will appear here.</p>
			</div>
		</div>
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
		display: flex;
		flex-direction: column;
		gap: 24px;
	}

	.panels-row {
		display: flex;
		gap: 24px;
		align-items: stretch;
	}

	.panels-row :global(.order-panel),
	.panels-row :global(.balance-panel) {
		flex: 1;
		min-width: 0;
	}

	.info-section {
		display: flex;
		flex-direction: column;
		gap: 24px;
	}

	.placeholder-card {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		padding: 20px;
	}

	.placeholder-card h2 {
		margin: 0 0 12px 0;
		font-size: 18px;
		font-weight: 600;
		color: #1f2937;
	}

	.placeholder-card p {
		margin: 0;
		color: #6b7280;
		font-size: 14px;
	}

	@media (max-width: 900px) {
		.panels-row {
			flex-direction: column;
		}

		.panels-row :global(.order-panel),
		.panels-row :global(.balance-panel) {
			width: 100%;
		}
	}

	@media (max-width: 768px) {
		.execution-content {
			padding: 16px;
		}

		.panels-row {
			gap: 16px;
		}
	}
</style>
