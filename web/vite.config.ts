import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [
		sveltekit({
			compilerOptions: {
				// Force runes mode for the project, except for libraries. Can be removed in svelte 6.
				runes: ({ filename }) =>
					filename.split(/[/\\]/).includes('node_modules') ? undefined : true
			},
			// Single-page app: build/ is embedded by cpanel-api. Unknown paths fall back to index.html.
			adapter: adapter({ fallback: 'index.html' }),
			// A timestamp would make every build rewrite index.html. The binary and the SPA ship together.
			version: { name: 'cpanel' }
		})
	],
	server: {
		// The browser only talks to this origin, so the session cookie stays same-origin.
		// cpanel-api listens on :8080. Set CPANEL_PUBLIC_ORIGIN to this dev origin.
		proxy: {
			'/auth': 'http://127.0.0.1:8080',
			'/me': 'http://127.0.0.1:8080',
			'/tokens': 'http://127.0.0.1:8080',
			'/healthz': 'http://127.0.0.1:8080',
			'/readyz': 'http://127.0.0.1:8080',
			'/metrics': 'http://127.0.0.1:8080',
			'/streams': 'http://127.0.0.1:8080',
			'/jobs': 'http://127.0.0.1:8080',
			'/job-runs': 'http://127.0.0.1:8080',
			'/mirror': 'http://127.0.0.1:8080'
		}
	}
});
