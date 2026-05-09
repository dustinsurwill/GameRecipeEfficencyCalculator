import { writable, derived, get } from 'svelte/store';
import type { User, TokenClaims } from './types';

export const accessToken = writable<string | null>(null);
export const currentUser = writable<User | null>(null);
export const authReady = writable(false);

export const isAuthenticated = derived(accessToken, ($t) => $t !== null);
export const isCurator = derived(accessToken, ($t) => {
	if (!$t) return false;
	return decodeClaims($t)?.curator ?? false;
});
export const isSuperuser = derived(accessToken, ($t) => {
	if (!$t) return false;
	return decodeClaims($t)?.superuser ?? false;
});

export function decodeClaims(token: string): TokenClaims | null {
	try {
		return JSON.parse(atob(token.split('.')[1]));
	} catch {
		return null;
	}
}

const BASE = import.meta.env.VITE_API_BASE as string;

export async function bootAuth(): Promise<void> {
	try {
		const res = await fetch(`${BASE}/auth/refresh`, {
			method: 'POST',
			credentials: 'include'
		});
		if (res.ok) {
			const data = await res.json();
			accessToken.set(data.access_token);
			await fetchCurrentUser(data.access_token);
		}
	} catch {
		// no refresh token or network error — stay logged out
	} finally {
		authReady.set(true);
	}
}

export async function fetchCurrentUser(token: string): Promise<void> {
	const res = await fetch(`${BASE}/users/me`, {
		headers: { Authorization: `Bearer ${token}` },
		credentials: 'include'
	});
	if (res.ok) {
		currentUser.set(await res.json());
	}
}

export async function logout(): Promise<void> {
	const token = get(accessToken);
	await fetch(`${BASE}/auth/logout`, {
		method: 'POST',
		credentials: 'include',
		headers: token ? { Authorization: `Bearer ${token}` } : {}
	});
	accessToken.set(null);
	currentUser.set(null);
}
