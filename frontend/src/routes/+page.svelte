<script lang="ts">
	import { onMount } from 'svelte';
	import { API_V2 } from '$lib/config';

	onMount(async () => {
		try {
			const res = await fetch(`${API_V2}/auth/session`, { credentials: 'include' });
			if (res.ok) {
				const data = await res.json();
				localStorage.setItem('access_token', data.access_token);
				window.location.href = '/home';
			} else {
				// Authelia session gone — full reload so Traefik can redirect to login portal
				window.location.reload();
			}
		} catch {
			window.location.reload();
		}
	});
</script>

<svelte:head>
	<title>CPanel</title>
</svelte:head>

<div class="splash">
	<p>Authenticating…</p>
</div>

<style>
	.splash {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 100vh;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
		color: #666;
		font-size: 14px;
	}
</style>
