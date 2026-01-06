<script>
	import RegisterForm from '../../components/RegisterForm.svelte';
</script>

<svelte:head>
	<title>CPanel - Register</title>
</svelte:head>

<div class="container">
	<div class="card">
		<h1>Create Account</h1>
		<p class="subtitle">Sign up to get started with CPanel</p>
		
		<RegisterForm />
		
		<div class="footer">
			<p>Already have an account? <a href="/">Login here</a></p>
		</div>
	</div>
</div>

<style>
	.container {
		width: 100%;
		max-width: 450px;
	}

	.card {
		background: white;
		border-radius: 12px;
		padding: 40px;
		box-shadow: 0 10px 40px rgba(0, 0, 0, 0.1);
	}

	h1 {
		margin: 0 0 8px 0;
		font-size: 32px;
		font-weight: 700;
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		-webkit-background-clip: text;
		-webkit-text-fill-color: transparent;
		background-clip: text;
		text-align: center;
	}

	.subtitle {
		text-align: center;
		color: #666;
		margin: 0 0 30px 0;
		font-size: 14px;
	}

	.footer {
		margin-top: 24px;
		text-align: center;
		font-size: 14px;
		color: #666;
	}

	.footer a {
		color: #667eea;
		text-decoration: none;
		font-weight: 500;
	}

	.footer a:hover {
		text-decoration: underline;
	}
</style>

