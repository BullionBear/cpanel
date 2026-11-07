<script lang="ts">
	let { title, note }: { title: string; note: string } = $props();
</script>

<svelte:head>
	<title>{title} · cpanel</title>
</svelte:head>

<h1>{title}</h1>
<p class="muted">{note}</p>
