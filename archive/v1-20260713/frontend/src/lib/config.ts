// API Configuration: base URL without version (e.g. http://localhost:8186/api)
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8186/api';

/** v2 API base (all endpoints). */
export const API_V2 = `${API_BASE_URL}/v2`;

// Log API URL in development for debugging
if (import.meta.env.DEV) {
	console.log('API Base URL:', API_BASE_URL, '| v2:', API_V2);
}

