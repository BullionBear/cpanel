<svelte:head>
	<title>登入 · cpanel</title>
</svelte:head>

<main class="card">
	<h1>登入</h1>
	<p>使用 Discord 登入。必須是指定伺服器的成員，而且有指定的角色。</p>
	<p><a class="primary" href="/auth/discord/login">以 Discord 登入</a></p>
</main>

<style>
	a.primary {
		display: inline-block;
		margin-top: 0.5rem;
		padding: 0.4rem 0.8rem;
		background: var(--accent);
		color: var(--accent-fg);
		text-decoration: none;
		border-radius: 4px;
	}
</style>
