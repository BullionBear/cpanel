<script lang="ts">
	import { session } from '#lib/session.svelte.ts';
</script>

<svelte:head>
	<title>cpanel</title>
</svelte:head>

<h1>cpanel</h1>
<p>已登入為 {session.user?.display_name}（{session.user?.username}）。</p>
<p class="muted">這個首頁還沒有內容。</p>
