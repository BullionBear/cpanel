<script lang="ts">
	export let title: string = '';
	export let subtitle: string = '';
</script>

<div class="card-layout">
	{#if title || subtitle}
		<div class="card-header">
			{#if title}
				<h3 class="card-title">{title}</h3>
			{/if}
			{#if subtitle}
				<p class="card-subtitle">{subtitle}</p>
			{/if}
		</div>
	{/if}
	<div class="card-content">
		<slot />
	</div>
</div>

<style>
	.card-layout {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		overflow: hidden;
		transition: box-shadow 0.2s;
	}

	.card-layout:hover {
		box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
	}

	.card-header {
		padding: 20px 24px;
		background: #f9fafb;
		border-bottom: 1px solid #e5e7eb;
	}

	.card-title {
		margin: 0 0 4px 0;
		font-size: 18px;
		font-weight: 600;
		color: #1f2937;
	}

	.card-subtitle {
		margin: 0;
		font-size: 14px;
		color: #6b7280;
	}

	.card-content {
		padding: 24px;
	}

	@media (max-width: 768px) {
		.card-header {
			padding: 16px 20px;
		}

		.card-content {
			padding: 20px;
		}
	}
</style>
