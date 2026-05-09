<script lang="ts">
	import { goto } from '$app/navigation';
	import { accessToken, fetchCurrentUser } from '$lib/auth';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import * as Card from '$lib/components/ui/card';

	const BASE = import.meta.env.VITE_API_BASE as string;

	let email = $state('');
	let password = $state('');
	let error = $state('');
	let loading = $state(false);

	async function handleSubmit(e: Event) {
		e.preventDefault();
		error = '';
		loading = true;
		try {
			const res = await fetch(`${BASE}/auth/login`, {
				method: 'POST',
				credentials: 'include',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ email, password })
			});
			if (!res.ok) {
				const data = await res.json();
				error = data.detail ?? 'Login failed';
				return;
			}
			const data = await res.json();
			accessToken.set(data.access_token);
			await fetchCurrentUser(data.access_token);
			goto('/admin');
		} catch {
			error = 'Network error';
		} finally {
			loading = false;
		}
	}
</script>

<div class="flex min-h-[calc(100vh-57px)] items-center justify-center p-4">
	<Card.Root class="w-full max-w-sm">
		<Card.Header>
			<Card.Title>Log in</Card.Title>
			<Card.Description>Enter your email and password</Card.Description>
		</Card.Header>
		<Card.Content>
			<form onsubmit={handleSubmit} class="space-y-4">
				<div class="space-y-1">
					<Label for="email">Email</Label>
					<Input id="email" type="email" bind:value={email} required autocomplete="email" />
				</div>
				<div class="space-y-1">
					<Label for="password">Password</Label>
					<Input id="password" type="password" bind:value={password} required autocomplete="current-password" />
				</div>
				{#if error}
					<p class="text-destructive text-sm">{error}</p>
				{/if}
				<Button type="submit" class="w-full" disabled={loading}>
					{loading ? 'Logging in…' : 'Log in'}
				</Button>
			</form>
		</Card.Content>
		<Card.Footer class="justify-center">
			<p class="text-muted-foreground text-sm">
				No account? <a href="/register" class="text-foreground underline">Sign up</a>
			</p>
		</Card.Footer>
	</Card.Root>
</div>
