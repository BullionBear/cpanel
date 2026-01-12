<script lang="ts">
	export let headers: string[] = [];
</script>

<div class="table-layout">
	<table class="table">
		{#if headers.length > 0}
			<thead>
				<tr>
					{#each headers as header}
						<th>{header}</th>
					{/each}
				</tr>
			</thead>
		{/if}
		<tbody>
			<slot />
		</tbody>
	</table>
</div>

<style>
	.table-layout {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		overflow: hidden;
		overflow-x: auto;
	}

	.table {
		width: 100%;
		border-collapse: collapse;
		min-width: 600px;
	}

	thead {
		background: #f9fafb;
	}

	th {
		padding: 12px 16px;
		text-align: left;
		font-size: 14px;
		font-weight: 600;
		color: #374151;
		border-bottom: 2px solid #e5e7eb;
		white-space: nowrap;
	}

	tbody tr {
		border-bottom: 1px solid #e5e7eb;
		transition: background 0.2s;
	}

	tbody tr:hover {
		background: #f9fafb;
	}

	tbody tr:last-child {
		border-bottom: none;
	}

	tbody td {
		padding: 12px 16px;
		font-size: 14px;
		color: #1f2937;
	}

	@media (max-width: 768px) {
		th, td {
			padding: 10px 12px;
			font-size: 13px;
		}
	}
</style>
