<script lang="ts">
	import { onMount } from 'svelte';
	import { API_V2 } from '$lib/config';

	let accessDenied = false;
	const isDev = import.meta.env.DEV;

	onMount(async () => {
		try {
			// redirect: 'manual' lets us distinguish an Authelia 302 (session
			// expired → reload to re-authenticate) from a backend 4xx (user is
			// authenticated but has no cpanel account → show error, don't loop).
			const res = await fetch(`${API_V2}/auth/session`, {
				credentials: 'include',
				redirect: 'manual'
			});

			if (res.ok) {
				const data = await res.json();
				localStorage.setItem('access_token', data.access_token);
				window.location.href = '/home';
			} else if (res.type === 'opaqueredirect' || res.status === 0) {
				// Authelia redirected to login portal — full reload to follow it
				window.location.reload();
			} else {
				// Authenticated by Authelia but denied by the backend.
				// Show an error rather than reloading into an infinite loop.
				accessDenied = true;
			}
		} catch {
			window.location.reload();
		}
	});
</script>

<svelte:head>
	<title>CPanel</title>
</svelte:head>

{#if accessDenied}
<div class="splash">
	<p class="msg error">Your account does not have access to CPanel.</p>
	{#if isDev}
		<p class="msg">Dev mode: check that the backend has <code>DEV_MODE=true</code> and <code>DEV_USER</code> set, and that the user exists in the local database.</p>
	{:else}
		<a href="https://auth.lynxlinkage.com/logout" class="logout">Sign out</a>
	{/if}
</div>
{:else}
<div class="splash">
	<p class="msg">Authenticating…</p>
</div>
{/if}

<style>
	.splash {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		gap: 1rem;
		min-height: 100vh;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
	}
	.msg {
		color: #666;
		font-size: 14px;
		margin: 0;
	}
	.msg.error {
		color: #b42318;
	}
	.logout {
		font-size: 13px;
		color: #666;
		text-decoration: underline;
	}
</style>
