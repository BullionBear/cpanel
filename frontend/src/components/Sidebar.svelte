<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/stores';

	export let collapsed = false;
	export let mobileOpen = false;

	const dispatch = createEventDispatcher();

	$: currentPath = $page.url.pathname;

	function toggleCollapse() {
		collapsed = !collapsed;
		dispatch('toggle', collapsed);
	}

	function navigate(route: string): void {
		goto(route);
		// Close mobile sidebar after navigation
		if (typeof window !== 'undefined' && window.innerWidth < 768) {
			dispatch('closeMobile');
		}
	}
</script>

<aside class="sidebar" class:collapsed class:mobile-open={mobileOpen}>
	<div class="sidebar-header">
		{#if !collapsed}
			<h2 class="sidebar-title">Menu</h2>
		{/if}
		<button class="collapse-btn" on:click={toggleCollapse} title={collapsed ? 'Expand' : 'Collapse'}>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				{#if collapsed}
					<path d="M9 18L15 12L9 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				{:else}
					<path d="M15 18L9 12L15 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				{/if}
			</svg>
		</button>
	</div>

	<nav class="sidebar-nav">
		<a 
			href="/home" 
			class="nav-item" 
			class:active={currentPath === '/home' || currentPath === '/'}
			on:click|preventDefault={() => navigate('/home')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M3 9L12 2L21 9V20C21 20.5304 20.7893 21.0391 20.4142 21.4142C20.0391 21.7893 19.5304 22 19 22H5C4.46957 22 3.96086 21.7893 3.58579 21.4142C3.21071 21.0391 3 20.5304 3 20V9Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M9 22V12H15V22" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			{#if !collapsed}
				<span>Home</span>
			{/if}
		</a>
		<a 
			href="/execution" 
			class="nav-item" 
			class:active={currentPath === '/execution'}
			on:click|preventDefault={() => navigate('/execution')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M9 11L12 14L22 4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M21 12V19C21 19.5304 20.7893 20.0391 20.4142 20.4142C20.0391 20.7893 19.5304 21 19 21H5C4.46957 21 3.96086 20.7893 3.58579 20.4142C3.21071 20.0391 3 19.5304 3 19V5C3 4.46957 3.21071 3.96086 3.58579 3.58579C3.96086 3.21071 4.46957 3 5 3H16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			{#if !collapsed}
				<span>Execution</span>
			{/if}
		</a>
		<a 
			href="/apikeys" 
			class="nav-item" 
			class:active={currentPath === '/apikeys'}
			on:click|preventDefault={() => navigate('/apikeys')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M2 17L12 22L22 17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M2 12L12 17L22 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			{#if !collapsed}
				<span>API</span>
			{/if}
		</a>
	</nav>
</aside>

<style>
	.sidebar {
		width: 250px;
		min-width: 250px;
		max-width: 250px;
		background: white;
		border-right: 1px solid #e5e7eb;
		display: flex;
		flex-direction: column;
		transition: width 0.3s ease, min-width 0.3s ease, max-width 0.3s ease;
		height: calc(100vh - 64px - 60px); /* Full height minus header and footer */
		overflow-y: auto;
		overflow-x: hidden;
		flex-shrink: 0;
	}

	.sidebar.collapsed {
		width: 64px;
		min-width: 64px;
		max-width: 64px;
	}

	.sidebar-header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 16px;
		border-bottom: 1px solid #e5e7eb;
	}

	.sidebar-title {
		margin: 0;
		font-size: 18px;
		font-weight: 600;
		color: #1f2937;
	}

	.collapse-btn {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 32px;
		height: 32px;
		border: none;
		background: #f3f4f6;
		border-radius: 6px;
		cursor: pointer;
		color: #6b7280;
		transition: all 0.2s;
		flex-shrink: 0;
	}

	.collapse-btn:hover {
		background: #e5e7eb;
		color: #374151;
	}

	.sidebar.collapsed .sidebar-header {
		justify-content: center;
	}

	.sidebar.collapsed .sidebar-title {
		display: none;
	}

	.sidebar-nav {
		flex: 1;
		padding: 8px;
	}

	.nav-item {
		display: flex;
		align-items: center;
		gap: 12px;
		padding: 12px 16px;
		border-radius: 8px;
		color: #6b7280;
		text-decoration: none;
		transition: all 0.2s;
		margin-bottom: 4px;
	}

	.nav-item:hover {
		background: #f3f4f6;
		color: #374151;
	}

	.nav-item.active {
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
	}

	.nav-item svg {
		flex-shrink: 0;
		stroke: currentColor;
	}

	.nav-item span {
		font-weight: 500;
		white-space: nowrap;
	}

	.sidebar.collapsed .nav-item {
		justify-content: center;
		padding: 12px;
	}

	.sidebar.collapsed .nav-item span {
		display: none;
	}

	@media (max-width: 768px) {
		.sidebar {
			position: fixed;
			top: 64px;
			left: 0;
			bottom: 60px;
			z-index: 999;
			transform: translateX(-100%);
			transition: transform 0.3s ease;
			height: calc(100vh - 64px - 60px);
		}

		.sidebar.mobile-open {
			transform: translateX(0);
		}

		.sidebar.collapsed {
			width: 250px;
		}

		.sidebar.collapsed.mobile-open {
			width: 250px;
		}
	}

	@media (max-width: 480px) {
		.sidebar {
			width: 100%;
			max-width: 280px;
		}

		.sidebar.collapsed {
			width: 100%;
			max-width: 280px;
		}
	}
</style>

