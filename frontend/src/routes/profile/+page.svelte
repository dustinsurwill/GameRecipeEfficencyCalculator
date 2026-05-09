<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isAuthenticated, currentUser } from '$lib/auth';
	import { api } from '$lib/api';
	import { get } from 'svelte/store';
	import type { UserWithAccounts } from '$lib/types';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import { Textarea } from '$lib/components/ui/textarea';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Separator } from '$lib/components/ui/separator';

	let profile = $state<UserWithAccounts | null>(null);
	let displayName = $state('');
	let email = $state('');
	let bio = $state('');
	let avatarUrl = $state('');
	let saveError = $state('');
	let saveSuccess = $state(false);
	let saving = $state(false);

	onMount(async () => {
		if (!get(isAuthenticated)) { goto('/login'); return; }
		try {
			profile = await api.get<UserWithAccounts>('/users/me');
			displayName = profile.display_name;
			email = profile.email ?? '';
			bio = profile.bio ?? '';
			avatarUrl = profile.avatar_url ?? '';
		} catch {
			goto('/login');
		}
	});

	async function handleSave(e: Event) {
		e.preventDefault();
		saveError = '';
		saveSuccess = false;
		saving = true;
		try {
			const updated = await api.patch<UserWithAccounts>('/users/me', {
				display_name: displayName || undefined,
				email: email || undefined,
				bio: bio || undefined,
				avatar_url: avatarUrl || undefined
			});
			profile = updated;
			currentUser.set(updated);
			saveSuccess = true;
		} catch (err) {
			saveError = err instanceof Error ? err.message : 'Save failed';
		} finally {
			saving = false;
		}
	}

	async function unlinkOAuth(accountId: string) {
		try {
			await api.delete(`/users/me/oauth/${accountId}`);
			if (profile) {
				profile = { ...profile, oauth_accounts: profile.oauth_accounts.filter((a) => a.id !== accountId) };
			}
		} catch (err) {
			saveError = err instanceof Error ? err.message : 'Unlink failed';
		}
	}
</script>

<div class="mx-auto max-w-2xl p-6 space-y-6">
	<h1 class="text-2xl font-bold">Profile</h1>

	<Card.Root>
		<Card.Header><Card.Title>Personal info</Card.Title></Card.Header>
		<Card.Content>
			<form onsubmit={handleSave} class="space-y-4">
				<div class="space-y-1">
					<Label for="display-name">Display name</Label>
					<Input id="display-name" bind:value={displayName} />
				</div>
				<div class="space-y-1">
					<Label for="email">Email</Label>
					<Input id="email" type="email" bind:value={email} />
				</div>
				<div class="space-y-1">
					<Label for="bio">Bio</Label>
					<Textarea id="bio" bind:value={bio} rows={3} />
				</div>
				<div class="space-y-1">
					<Label for="avatar-url">Avatar URL</Label>
					<Input id="avatar-url" bind:value={avatarUrl} />
				</div>
				{#if saveError}
					<p class="text-destructive text-sm">{saveError}</p>
				{/if}
				{#if saveSuccess}
					<p class="text-sm text-green-600">Saved!</p>
				{/if}
				<Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save changes'}</Button>
			</form>
		</Card.Content>
	</Card.Root>

	{#if profile && profile.oauth_accounts.length > 0}
		<Card.Root>
			<Card.Header><Card.Title>Linked accounts</Card.Title></Card.Header>
			<Card.Content class="space-y-3">
				{#each profile.oauth_accounts as account (account.id)}
					<div class="flex items-center justify-between">
						<div class="flex items-center gap-2">
							<Badge variant="secondary">{account.provider}</Badge>
							<span class="text-muted-foreground text-sm">{account.provider_email ?? 'no email'}</span>
						</div>
						<Button variant="destructive" size="sm" onclick={() => unlinkOAuth(account.id)}>
							Unlink
						</Button>
					</div>
					<Separator />
				{/each}
			</Card.Content>
		</Card.Root>
	{/if}
</div>
