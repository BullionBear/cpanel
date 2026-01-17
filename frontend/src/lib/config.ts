// API Configuration
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8186/api/v1';

// Log API URL in development for debugging
if (import.meta.env.DEV) {
	console.log('API Base URL:', API_BASE_URL);
}

