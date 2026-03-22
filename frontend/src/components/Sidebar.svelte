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
			href="/position" 
			class="nav-item" 
			class:active={currentPath === '/position'}
			on:click|preventDefault={() => navigate('/position')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M18 20V10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M12 20V4" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M6 20V14" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			{#if !collapsed}
				<span>Position</span>
			{/if}
		</a>
		<a 
			href="/account" 
			class="nav-item" 
			class:active={currentPath === '/account'}
			on:click|preventDefault={() => navigate('/account')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 3c1.66 0 3 1.34 3 3s-1.34 3-3 3-3-1.34-3-3 1.34-3 3-3zm0 14.2c-2.5 0-4.71-1.28-6-3.22.03-1.99 4-3.08 6-3.08 1.99 0 5.97 1.09 6 3.08-1.29 1.94-3.5 3.22-6 3.22z" fill="currentColor"/>
			</svg>
			{#if !collapsed}
				<span>Account</span>
			{/if}
		</a>
		<a 
			href="/symbol" 
			class="nav-item" 
			class:active={currentPath === '/symbol'}
			on:click|preventDefault={() => navigate('/symbol')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 2L2 7L12 12L22 7L12 2Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M2 17L12 22L22 17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M2 12L12 17L22 12" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			{#if !collapsed}
				<span>Symbol</span>
			{/if}
		</a>
		<a 
			href="/history" 
			class="nav-item" 
			class:active={currentPath === '/history'}
			on:click|preventDefault={() => navigate('/history')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 8V12L15 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>
			</svg>
			{#if !collapsed}
				<span>History</span>
			{/if}
		</a>
		<a 
			href="/tool" 
			class="nav-item" 
			class:active={currentPath === '/tool'}
			on:click|preventDefault={() => navigate('/tool')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			{#if !collapsed}
				<span>Tool</span>
			{/if}
		</a>
		<a 
			href="/settings" 
			class="nav-item" 
			class:active={currentPath === '/settings'}
			on:click|preventDefault={() => navigate('/settings')}
		>
			<svg width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 15C13.6569 15 15 13.6569 15 12C15 10.3431 13.6569 9 12 9C10.3431 9 9 10.3431 9 12C9 13.6569 10.3431 15 12 15Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M19.4 15C19.27 15.3 19.23 15.64 19.29 15.96C19.35 16.28 19.5 16.58 19.73 16.82L19.79 16.88C19.98 17.07 20.12 17.29 20.22 17.53C20.32 17.77 20.38 18.03 20.38 18.3C20.38 18.56 20.32 18.82 20.22 19.06C20.12 19.3 19.98 19.52 19.79 19.71C19.6 19.9 19.38 20.04 19.14 20.14C18.9 20.24 18.64 20.3 18.38 20.3C18.11 20.3 17.85 20.24 17.61 20.14C17.37 20.04 17.15 19.9 16.96 19.71L16.9 19.65C16.66 19.42 16.37 19.26 16.04 19.21C15.72 19.15 15.38 19.19 15.08 19.32C14.78 19.45 14.53 19.66 14.35 19.93C14.18 20.19 14.08 20.51 14.08 20.83V21C14.08 21.53 13.87 22.04 13.49 22.41C13.12 22.79 12.61 23 12.08 23C11.55 23 11.04 22.79 10.67 22.41C10.29 22.04 10.08 21.53 10.08 21V20.91C10.07 20.58 9.97 20.25 9.77 19.99C9.58 19.72 9.31 19.51 9 19.41C8.7 19.28 8.36 19.24 8.04 19.3C7.72 19.35 7.42 19.51 7.18 19.74L7.12 19.8C6.93 19.99 6.71 20.13 6.47 20.23C6.23 20.33 5.97 20.39 5.71 20.39C5.44 20.39 5.18 20.33 4.94 20.23C4.7 20.13 4.48 19.99 4.29 19.8C4.1 19.61 3.96 19.39 3.86 19.15C3.76 18.91 3.7 18.65 3.7 18.39C3.7 18.12 3.76 17.86 3.86 17.62C3.96 17.38 4.1 17.16 4.29 16.97L4.35 16.91C4.58 16.67 4.74 16.38 4.79 16.05C4.85 15.73 4.81 15.39 4.68 15.09C4.55 14.79 4.34 14.55 4.07 14.36C3.81 14.19 3.49 14.09 3.17 14.09H3C2.47 14.09 1.96 13.88 1.59 13.5C1.21 13.13 1 12.62 1 12.09C1 11.56 1.21 11.05 1.59 10.68C1.96 10.3 2.47 10.09 3 10.09H3.09C3.42 10.08 3.75 9.97 4.01 9.77C4.28 9.58 4.49 9.31 4.59 9C4.72 8.7 4.76 8.36 4.7 8.04C4.65 7.72 4.49 7.42 4.26 7.18L4.2 7.12C4.01 6.93 3.87 6.71 3.77 6.47C3.67 6.23 3.61 5.97 3.61 5.71C3.61 5.44 3.67 5.18 3.77 4.94C3.87 4.7 4.01 4.48 4.2 4.29C4.39 4.1 4.61 3.96 4.85 3.86C5.09 3.76 5.35 3.7 5.62 3.7C5.88 3.7 6.14 3.76 6.38 3.86C6.62 3.96 6.84 4.1 7.03 4.29L7.09 4.35C7.33 4.58 7.63 4.74 7.95 4.79C8.27 4.85 8.61 4.81 8.91 4.68H9C9.3 4.55 9.55 4.34 9.73 4.07C9.9 3.81 10 3.49 10 3.17V3C10 2.47 10.21 1.96 10.59 1.59C10.96 1.21 11.47 1 12 1C12.53 1 13.04 1.21 13.41 1.59C13.79 1.96 14 2.47 14 3V3.09C14 3.41 14.1 3.73 14.27 3.99C14.45 4.26 14.7 4.47 15 4.6C15.3 4.73 15.64 4.77 15.96 4.71C16.28 4.65 16.58 4.5 16.82 4.27L16.88 4.21C17.07 4.02 17.29 3.88 17.53 3.78C17.77 3.68 18.03 3.62 18.3 3.62C18.56 3.62 18.82 3.68 19.06 3.78C19.3 3.88 19.52 4.02 19.71 4.21C19.9 4.4 20.04 4.62 20.14 4.86C20.24 5.1 20.3 5.36 20.3 5.63C20.3 5.89 20.24 6.15 20.14 6.39C20.04 6.63 19.9 6.85 19.71 7.04L19.65 7.1C19.42 7.34 19.26 7.64 19.21 7.96C19.15 8.28 19.19 8.62 19.32 8.92V9C19.45 9.3 19.66 9.55 19.93 9.73C20.19 9.9 20.51 10 20.83 10H21C21.53 10 22.04 10.21 22.41 10.59C22.79 10.96 23 11.47 23 12C23 12.53 22.79 13.04 22.41 13.41C22.04 13.79 21.53 14 21 14H20.91C20.59 14 20.27 14.1 20.01 14.27C19.74 14.45 19.53 14.7 19.4 15Z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
			{#if !collapsed}
				<span>Settings</span>
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

