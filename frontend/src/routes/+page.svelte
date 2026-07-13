<svelte:head>
	<title>CPanel</title>
	<meta name="description" content="CPanel — trading control panel" />
</svelte:head>

<div class="splash">
	<main class="card">
		<div class="logo">C</div>
		<h1>CPanel</h1>
		<p>The trading control panel is being rebuilt from the ground up.</p>
		<span class="badge">Coming back soon</span>
	</main>
</div>

<style>
	.splash {
		display: flex;
		align-items: center;
		justify-content: center;
		min-height: 100vh;
		padding: 24px;
		box-sizing: border-box;
	}

	.card {
		width: 100%;
		max-width: 420px;
		background: #ffffff;
		border: 1px solid #e3e5e8;
		border-radius: 12px;
		padding: 32px;
		box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
		text-align: center;
	}

	.logo {
		width: 48px;
		height: 48px;
		margin: 0 auto 16px;
		border-radius: 12px;
		background: #5865f2;
		display: flex;
		align-items: center;
		justify-content: center;
		color: #ffffff;
		font-size: 22px;
		font-weight: 700;
	}

	h1 {
		font-size: 20px;
		font-weight: 600;
		margin: 0 0 8px;
	}

	p {
		font-size: 14px;
		line-height: 1.6;
		color: #57606a;
		margin: 0;
	}

	.badge {
		display: inline-block;
		margin-top: 20px;
		padding: 4px 12px;
		border: 1px solid #e3e5e8;
		border-radius: 999px;
		font-size: 12px;
		color: #57606a;
	}

	@media (prefers-color-scheme: dark) {
		.card {
			background: #161b22;
			border-color: #30363d;
			box-shadow: 0 1px 2px rgba(0, 0, 0, 0.3);
		}
		p,
		.badge {
			color: #8b949e;
		}
		.badge {
			border-color: #30363d;
		}
	}
</style>
