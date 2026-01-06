<script lang="ts">
	import { onMount, createEventDispatcher } from 'svelte';
	import { getCurrentUser, logout, type User } from '$lib/api';

	const dispatch = createEventDispatcher();

	let user: User | null = null;
	let loading = true;

	onMount(async () => {
		user = await getCurrentUser();
		loading = false;
	});

	function handleLogout() {
		logout();
	}

	function handleMobileMenuToggle() {
		dispatch('mobileMenuToggle');
	}
</script>

<header class="header">
	<div class="header-left">
		<button class="mobile-menu-btn" on:click={handleMobileMenuToggle} aria-label="Toggle menu">
			<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M3 12H21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M3 6H21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<path d="M3 18H21" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
			</svg>
		</button>
		<div class="user-info">
			<div class="user-icon">
				<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
					<path d="M12 12C14.7614 12 17 9.76142 17 7C17 4.23858 14.7614 2 12 2C9.23858 2 7 4.23858 7 7C7 9.76142 9.23858 12 12 12Z" fill="currentColor"/>
					<path d="M12.0002 14.5C6.99016 14.5 2.91016 17.86 2.91016 22C2.91016 22.28 3.13016 22.5 3.41016 22.5H20.5902C20.8702 22.5 21.0902 22.28 21.0902 22C21.0902 17.86 17.0102 14.5 12.0002 14.5Z" fill="currentColor"/>
				</svg>
			</div>
			{#if loading}
				<span class="username">Loading...</span>
			{:else if user}
				<span class="username">{user.username}</span>
			{:else}
				<span class="username">Guest</span>
			{/if}
		</div>
	</div>
	<div class="header-right">
		{#if user}
			<button class="logout-btn" on:click={handleLogout}>
				<svg width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
					<path d="M9 21H5C4.46957 21 3.96086 20.7893 3.58579 20.4142C3.21071 20.0391 3 19.5304 3 19V5C3 4.46957 3.21071 3.96086 3.58579 3.58579C3.96086 3.21071 4.46957 3 5 3H9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					<path d="M16 17L21 12L16 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					<path d="M21 12H9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				<span class="logout-text">Logout</span>
			</button>
		{/if}
	</div>
</header>

<style>
	.header {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding: 16px 24px;
		background: white;
		border-bottom: 1px solid #e5e7eb;
		box-shadow: 0 1px 3px rgba(0, 0, 0, 0.1);
		height: 64px;
		position: sticky;
		top: 0;
		z-index: 999;
		width: 100%;
		max-width: 100%;
		box-sizing: border-box;
	}

	.header-left {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.mobile-menu-btn {
		display: none;
		align-items: center;
		justify-content: center;
		width: 40px;
		height: 40px;
		border: none;
		background: transparent;
		border-radius: 6px;
		cursor: pointer;
		color: #6b7280;
		transition: all 0.2s;
	}

	.mobile-menu-btn:hover {
		background: #f3f4f6;
		color: #374151;
	}

	.mobile-menu-btn svg {
		stroke: currentColor;
	}

	.user-info {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.user-icon {
		width: 40px;
		height: 40px;
		border-radius: 50%;
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		display: flex;
		align-items: center;
		justify-content: center;
		color: white;
	}

	.username {
		font-weight: 500;
		color: #1f2937;
		font-size: 16px;
	}

	.header-right {
		display: flex;
		align-items: center;
	}

	.logout-btn {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 8px 16px;
		background: #f3f4f6;
		border: 1px solid #e5e7eb;
		border-radius: 6px;
		color: #374151;
		font-size: 14px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
	}

	.logout-btn:hover {
		background: #e5e7eb;
		color: #1f2937;
	}

	.logout-btn svg {
		stroke: currentColor;
	}

	.logout-text {
		display: inline;
	}

	@media (max-width: 768px) {
		.header {
			padding: 12px 16px;
		}

		.mobile-menu-btn {
			display: flex;
		}

		.username {
			font-size: 14px;
		}

		.user-icon {
			width: 36px;
			height: 36px;
		}

		.logout-text {
			display: none;
		}

		.logout-btn {
			padding: 8px;
			min-width: 40px;
		}
	}

	@media (max-width: 480px) {
		.header {
			padding: 10px 12px;
		}

		.user-info {
			gap: 8px;
		}

		.username {
			font-size: 13px;
		}
	}
</style>

