# CPanel Frontend

SvelteKit application, rebuilt from scratch on 2026-07-13 (previous version in
`../archive/frontend/`). Built with the static adapter and served by nginx in
production. The root route is the public landing page.

## Development

```bash
npm install
npm run dev
```

## Build

```bash
npm run build   # outputs static site to build/
```

`VITE_API_BASE_URL` selects the backend API base URL at build time (see
`.env.example` and `docker/Dockerfile.frontend`).
