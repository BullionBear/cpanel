<script>
	import { API_V2 } from '../lib/config';
	
	let username = '';
	let email = '';
	let password = '';
	let passwordConfirm = '';
	let loading = false;
	let error = '';
	let success = '';

	function validateForm() {
		if (password !== passwordConfirm) {
			error = 'Passwords do not match';
			return false;
		}
		if (password.length < 6) {
			error = 'Password must be at least 6 characters';
			return false;
		}
		return true;
	}

	async function handleSubmit() {
		error = '';
		success = '';

		if (!validateForm()) {
			return;
		}

		loading = true;

		try {
			const response = await fetch(`${API_V2}/auth/register`, {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json'
				},
				body: JSON.stringify({
					username,
					email,
					password
				})
			});

			const data = await response.json();

			if (response.ok) {
				success = 'Registration successful! Please check your email for activation link.';
				// Clear form
				username = '';
				email = '';
				password = '';
				passwordConfirm = '';
			} else {
				error = data.detail || 'Registration failed. Please try again.';
			}
		} catch (err) {
			error = 'Network error. Please try again.';
			console.error('Registration error:', err);
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
			placeholder="Choose a username"
		/>
	</div>

	<div class="form-group">
		<label for="email">Email</label>
		<input
			id="email"
			type="email"
			bind:value={email}
			required
			disabled={loading}
			placeholder="Enter your email"
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
			placeholder="Create a password"
			minlength="6"
		/>
	</div>

	<div class="form-group">
		<label for="passwordConfirm">Confirm Password</label>
		<input
			id="passwordConfirm"
			type="password"
			bind:value={passwordConfirm}
			required
			disabled={loading}
			placeholder="Confirm your password"
			minlength="6"
		/>
	</div>

	{#if error}
		<div class="alert alert-error">{error}</div>
	{/if}

	{#if success}
		<div class="alert alert-success">{success}</div>
	{/if}

	<button type="submit" disabled={loading} class="btn btn-primary">
		{loading ? 'Registering...' : 'Register'}
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

