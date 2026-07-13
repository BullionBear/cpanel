<script lang="ts">
	export let sidebar: boolean = true;
	export let sidebarWidth: string = '300px';
</script>

<div class="dashboard-layout">
	{#if sidebar}
		<aside class="dashboard-sidebar" style="--sidebar-width: {sidebarWidth};">
			<slot name="sidebar" />
		</aside>
	{/if}
	<main class="dashboard-main">
		<slot />
	</main>
</div>

<style>
	.dashboard-layout {
		display: flex;
		gap: 24px;
		width: 100%;
		min-height: 400px;
	}

	.dashboard-sidebar {
		width: var(--sidebar-width);
		min-width: var(--sidebar-width);
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		padding: 20px;
		flex-shrink: 0;
	}

	.dashboard-main {
		flex: 1;
		min-width: 0;
		display: flex;
		flex-direction: column;
		gap: 24px;
	}

	@media (max-width: 1024px) {
		.dashboard-layout {
			flex-direction: column;
		}

		.dashboard-sidebar {
			width: 100%;
			min-width: 100%;
		}
	}
</style>
