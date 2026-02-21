// API Configuration: base URL without version (e.g. http://localhost:8186/api)
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8186/api';

/** v1 API base; all current endpoints use v1. */
export const API_V1 = `${API_BASE_URL}/v1`;

// Log API URL in development for debugging
if (import.meta.env.DEV) {
	console.log('API Base URL:', API_BASE_URL, '| v1:', API_V1);
}

