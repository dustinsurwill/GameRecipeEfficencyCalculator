import { get } from 'svelte/store';
import { accessToken, fetchCurrentUser } from './auth';
import { goto } from '$app/navigation';

const BASE = import.meta.env.VITE_API_BASE as string;

let refreshing: Promise<string | null> | null = null;

async function doRefresh(): Promise<string | null> {
	const res = await fetch(`${BASE}/auth/refresh`, {
		method: 'POST',
		credentials: 'include'
	});
	if (!res.ok) return null;
	const data = await res.json();
	accessToken.set(data.access_token);
	await fetchCurrentUser(data.access_token);
	return data.access_token;
}

async function apiFetch(path: string, options: RequestInit = {}): Promise<Response> {
	const token = get(accessToken);
	const headers: Record<string, string> = {
		'Content-Type': 'application/json',
		...(options.headers as Record<string, string>)
	};
	if (token) headers['Authorization'] = `Bearer ${token}`;

	let res = await fetch(`${BASE}${path}`, {
		...options,
		credentials: 'include',
		headers
	});

	if (res.status === 401) {
		// Deduplicate concurrent refresh attempts
		if (!refreshing) refreshing = doRefresh().finally(() => (refreshing = null));
		const newToken = await refreshing;

		if (!newToken) {
			accessToken.set(null);
			goto('/login');
			return res;
		}

		headers['Authorization'] = `Bearer ${newToken}`;
		res = await fetch(`${BASE}${path}`, {
			...options,
			credentials: 'include',
			headers
		});
	}

	return res;
}

async function json<T>(path: string, options: RequestInit = {}): Promise<T> {
	const res = await apiFetch(path, options);
	if (!res.ok) {
		const text = await res.text();
		let detail = text;
		try {
			detail = JSON.parse(text)?.detail ?? text;
		} catch { /* use raw text */ }
		throw new Error(detail);
	}
	if (res.status === 204) return undefined as T;
	return res.json();
}

export const api = {
	get: <T>(path: string) => json<T>(path, { method: 'GET' }),
	post: <T>(path: string, body?: unknown) =>
		json<T>(path, { method: 'POST', body: body !== undefined ? JSON.stringify(body) : undefined }),
	patch: <T>(path: string, body?: unknown) =>
		json<T>(path, { method: 'PATCH', body: body !== undefined ? JSON.stringify(body) : undefined }),
	delete: <T>(path: string) => json<T>(path, { method: 'DELETE' }),
	raw: apiFetch
};
