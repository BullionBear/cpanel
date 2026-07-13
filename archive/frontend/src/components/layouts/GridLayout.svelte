<script lang="ts">
	export let columns: number = 3;
	export let gap: string = '24px';
</script>

<div class="grid-layout" style="--columns: {columns}; --gap: {gap};">
	<slot />
</div>

<style>
	.grid-layout {
		display: grid;
		grid-template-columns: repeat(var(--columns), 1fr);
		gap: var(--gap);
		width: 100%;
	}

	@media (max-width: 1024px) {
		.grid-layout {
			grid-template-columns: repeat(2, 1fr);
		}
	}

	@media (max-width: 768px) {
		.grid-layout {
			grid-template-columns: 1fr;
		}
	}
</style>
