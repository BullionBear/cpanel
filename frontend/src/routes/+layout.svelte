<slot />

<style>
	:global(body) {
		margin: 0;
		padding: 0;
		min-height: 100vh;
		font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu,
			Cantarell, sans-serif;
		background: #f6f8fa;
		color: #1f2328;
	}

	@media (prefers-color-scheme: dark) {
		:global(body) {
			background: #0d1117;
			color: #e6edf3;
		}
	}
</style>
