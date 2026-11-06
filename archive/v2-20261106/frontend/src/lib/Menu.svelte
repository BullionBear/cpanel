<script lang="ts">
	import { onMount } from 'svelte';

	// Accessible label for the trigger button.
	export let label = 'Actions';
	// Small variant to sit inline with `.small` buttons/rows.
	export let small = false;

	let open = false;
	let root: HTMLElement;

	const toggle = () => (open = !open);
	const close = () => (open = false);

	onMount(() => {
		const onDocClick = (e: MouseEvent) => {
			if (root && !root.contains(e.target as Node)) close();
		};
		const onKey = (e: KeyboardEvent) => {
			if (e.key === 'Escape') close();
		};
		document.addEventListener('click', onDocClick);
		document.addEventListener('keydown', onKey);
		return () => {
			document.removeEventListener('click', onDocClick);
			document.removeEventListener('keydown', onKey);
		};
	});
</script>

<div class="menu" class:small bind:this={root}>
	<button
		type="button"
		class="menu-btn"
		class:open
		aria-haspopup="true"
		aria-expanded={open}
		aria-label={label}
		title={label}
		on:click|stopPropagation={toggle}
	>
		<svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true">
			<circle cx="5" cy="12" r="1.9" fill="currentColor" />
			<circle cx="12" cy="12" r="1.9" fill="currentColor" />
			<circle cx="19" cy="12" r="1.9" fill="currentColor" />
		</svg>
	</button>
	{#if open}
		<!-- Any activation of an action inside closes the menu. -->
		<!-- svelte-ignore a11y_interactive_supports_focus a11y_click_events_have_key_events -->
		<div class="menu-dropdown" role="menu" on:click={close} on:keydown={(e) => e.key === 'Enter' && close()}>
			<slot />
		</div>
	{/if}
</div>

<style>
	.menu {
		position: relative;
		display: inline-flex;
		margin-left: auto;
	}

	.menu-btn {
		display: inline-flex;
		align-items: center;
		justify-content: center;
		width: 2.25rem;
		height: 2.25rem;
		padding: 0;
		background: var(--white);
		color: var(--text-muted);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		cursor: pointer;
		transition:
			border-color 0.2s ease,
			color 0.2s ease,
			background 0.2s ease;
	}
	.menu.small .menu-btn {
		width: 1.85rem;
		height: 1.85rem;
		border-radius: var(--radius-xs);
	}
	.menu-btn:hover,
	.menu-btn.open {
		border-color: var(--text);
		color: var(--text);
	}

	.menu-dropdown {
		position: absolute;
		top: calc(100% + 0.375rem);
		right: 0;
		min-width: 11rem;
		padding: 0.25rem;
		background: var(--white);
		border: 1px solid var(--border);
		border-radius: var(--radius-sm);
		box-shadow: 0 12px 30px -8px rgba(0, 0, 0, 0.22);
		z-index: 60;
	}
</style>
