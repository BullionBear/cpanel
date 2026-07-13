# CPanel Frontend

Svelte frontend application for CPanel.

## Setup

1. Install dependencies:
```bash
npm install
```

2. Configure API URL:
   - Create a `.env` file in the frontend directory with:
     ```
     VITE_API_BASE_URL=http://localhost:8186/api
     ```
   - The default is `http://localhost:8186/api` if no `.env` file exists
   - **Important**: After creating or modifying `.env`, restart the dev server

### API URL Configuration

The API base URL is configured via the `VITE_API_BASE_URL` environment variable (no version segment). Each endpoint determines its version (e.g. `/v1/wallet`, `/v2/balance`). All current requests use v1.

- **Local Development**: `http://localhost:8186/api` (backend running locally on port 8186)
- **Docker/Production**: Update to your backend URL, e.g., `https://capi.yourdomain.com/api`

**Note**: Vite requires environment variables to be prefixed with `VITE_` to be exposed to the client-side code.

## Development

```bash
npm run dev
```

The app will be available at http://localhost:5173

## Build

```bash
npm run build
```

## Project Structure

```
frontend/
├── src/
│   ├── components/        # Reusable components
│   │   ├── LoginForm.svelte
│   │   └── RegisterForm.svelte
│   ├── lib/               # Utilities and config
│   │   └── config.ts      # API configuration
│   ├── routes/            # Pages/routes
│   │   ├── +page.svelte   # Login page (home)
│   │   └── register/      # Register page
│   │       └── +page.svelte
│   ├── app.css            # Global styles
│   └── app.html           # HTML template
├── static/                # Static assets
├── package.json
└── vite.config.js
```

## Features

- **Login Page** (`/`): Login form with username and password
- **Register Page** (`/register`): Registration form with username, email, password, and password confirmation
- Responsive design with modern UI
- Form validation
- Error and success message handling

