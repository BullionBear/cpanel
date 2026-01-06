# CPanel Frontend

Svelte frontend application for CPanel.

## Setup

1. Install dependencies:
```bash
npm install
```

2. Configure API URL:
   - Create a `.env` file with:
     ```
     VITE_API_BASE_URL=http://localhost:8186/api/v1
     ```
   - Or it will default to `http://localhost:8186/api/v1`

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

