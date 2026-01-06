<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { goto } from '$app/navigation';
	import Header from '../../components/Header.svelte';
	import Sidebar from '../../components/Sidebar.svelte';
	import Footer from '../../components/Footer.svelte';
	import { getCurrentUser } from '$lib/api';

	let sidebarCollapsed = false;
	let sidebarMobileOpen = false;

	onMount(async () => {
		// Check if user is authenticated
		const user = await getCurrentUser();
		if (!user) {
			goto('/');
		}
		// Auto-collapse sidebar on mobile
		if (typeof window !== 'undefined') {
			const checkMobile = () => {
				if (window.innerWidth < 768) {
					sidebarCollapsed = true;
					sidebarMobileOpen = false;
				}
			};
			checkMobile();
			window.addEventListener('resize', checkMobile);
			return () => window.removeEventListener('resize', checkMobile);
		}
	});

	function handleSidebarToggle(event: CustomEvent<boolean>) {
		sidebarCollapsed = event.detail;
	}

	function handleSidebarClose() {
		sidebarMobileOpen = false;
	}

	function handleMobileToggle() {
		sidebarMobileOpen = !sidebarMobileOpen;
	}

	function closeMobileSidebar() {
		sidebarMobileOpen = false;
	}
</script>

<div class="app-layout">
	<Header on:mobileMenuToggle={handleMobileToggle} bind:sidebarMobileOpen />
	<div class="app-body">
		{#if sidebarMobileOpen}
			<div class="sidebar-overlay" on:click={closeMobileSidebar} role="button" tabindex="-1"></div>
		{/if}
		<Sidebar 
			collapsed={sidebarCollapsed} 
			mobileOpen={sidebarMobileOpen}
			on:toggle={handleSidebarToggle}
			on:closeMobile={handleSidebarClose}
		/>
		<main class="content">
			<slot />
		</main>
	</div>
	<Footer />
</div>

<style>
	:global(html, body) {
		margin: 0;
		padding: 0;
		width: 100%;
		height: 100%;
		overflow-x: hidden;
	}

	:global(body) {
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
		background: #f9fafb;
	}

	.app-layout {
		display: flex;
		flex-direction: column;
		min-height: 100vh;
		width: 100%;
		max-width: 100vw;
		overflow-x: hidden;
	}

	.app-body {
		display: flex;
		flex: 1;
		overflow: hidden;
		position: relative;
		width: 100%;
		min-width: 0;
	}

	.sidebar-overlay {
		position: fixed;
		top: 64px;
		left: 0;
		right: 0;
		bottom: 60px;
		background: rgba(0, 0, 0, 0.5);
		z-index: 998;
		opacity: 1;
		transition: opacity 0.3s ease;
	}

	@media (min-width: 769px) {
		.sidebar-overlay {
			display: none;
		}
	}

	.content {
		flex: 1;
		padding: 24px;
		overflow-y: auto;
		overflow-x: hidden;
		background: #f9fafb;
		min-width: 0; /* Prevents content overflow */
		width: 100%;
		max-width: 100%;
		box-sizing: border-box;
	}

	@media (max-width: 1024px) {
		.content {
			padding: 20px;
		}
	}

	@media (max-width: 768px) {
		.content {
			padding: 16px;
		}
	}

	@media (max-width: 480px) {
		.content {
			padding: 12px;
		}
	}
</style>

