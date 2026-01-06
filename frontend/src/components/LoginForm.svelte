<script>
	import { API_BASE_URL } from '../lib/config';
	
	let username = '';
	let password = '';
	let loading = false;
	let error = '';
	let success = '';

	async function handleSubmit() {
		error = '';
		success = '';
		loading = true;

		try {
			const formData = new FormData();
			formData.append('username', username);
			formData.append('password', password);

			const response = await fetch(`${API_BASE_URL}/auth/login`, {
				method: 'POST',
				body: formData
			});

			const data = await response.json();

			if (response.ok) {
				if (data.access_token) {
					// Active user - save token and redirect
					localStorage.setItem('access_token', data.access_token);
					success = 'Login successful! Redirecting...';
					setTimeout(() => {
						window.location.href = '/home';
					}, 1000);
				} else {
					// Inactive user - show message
					success = data.message || 'Please check your email for activation link.';
				}
			} else {
				error = data.detail || 'Login failed. Please try again.';
			}
		} catch (err) {
			error = 'Network error. Please try again.';
			console.error('Login error:', err);
		} finally {
			loading = false;
		}
	}
</script>

<form on:submit|preventDefault={handleSubmit}>
	<div class="form-group">
		<label for="username">Username</label>
		<input
			id="username"
			type="text"
			bind:value={username}
			required
			disabled={loading}
			placeholder="Enter your username"
		/>
	</div>

	<div class="form-group">
		<label for="password">Password</label>
		<input
			id="password"
			type="password"
			bind:value={password}
			required
			disabled={loading}
			placeholder="Enter your password"
		/>
	</div>

	{#if error}
		<div class="alert alert-error">{error}</div>
	{/if}

	{#if success}
		<div class="alert alert-success">{success}</div>
	{/if}

	<button type="submit" disabled={loading} class="btn btn-primary">
		{loading ? 'Logging in...' : 'Login'}
	</button>
</form>

<style>
	form {
		display: flex;
		flex-direction: column;
		gap: 20px;
	}

	.form-group {
		display: flex;
		flex-direction: column;
		gap: 8px;
	}

	label {
		font-size: 14px;
		font-weight: 500;
		color: #333;
	}

	input {
		padding: 12px 16px;
		border: 1px solid #ddd;
		border-radius: 8px;
		font-size: 14px;
		transition: border-color 0.2s;
	}

	input:focus {
		outline: none;
		border-color: #667eea;
	}

	input:disabled {
		background-color: #f5f5f5;
		cursor: not-allowed;
	}

	.btn {
		padding: 12px 24px;
		border: none;
		border-radius: 8px;
		font-size: 16px;
		font-weight: 600;
		cursor: pointer;
		transition: all 0.2s;
	}

	.btn-primary {
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
	}

	.btn-primary:hover:not(:disabled) {
		transform: translateY(-2px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.btn-primary:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.alert {
		padding: 12px 16px;
		border-radius: 8px;
		font-size: 14px;
	}

	.alert-error {
		background-color: #fee;
		color: #c33;
		border: 1px solid #fcc;
	}

	.alert-success {
		background-color: #efe;
		color: #3c3;
		border: 1px solid #cfc;
	}
</style>

