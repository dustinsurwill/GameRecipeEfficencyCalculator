<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import { isSuperuser } from '$lib/auth';
	import type { ModPack, Recipe } from '$lib/types';
	import * as Card from '$lib/components/ui/card';
	import { Badge } from '$lib/components/ui/badge';
	import { Button } from '$lib/components/ui/button';

	let modpacks = $state<ModPack[]>([]);
	let pendingCount = $state(0);
	let loadError = $state('');

	onMount(async () => {
		try {
			const [mp, pending] = await Promise.all([
				api.get<ModPack[]>('/modpacks'),
				api.get<{ items: Recipe[]; total: number }>('/recipes/pending?limit=1')
			]);
			modpacks = mp;
			pendingCount = pending.total;
		} catch (err) {
			loadError = err instanceof Error ? err.message : 'Load failed';
		}
	});
</script>

<div class="mx-auto max-w-5xl p-6 space-y-6">
	<div class="flex items-center justify-between">
		<h1 class="text-2xl font-bold">Admin</h1>
		{#if pendingCount > 0}
			<a href="/admin/recipes">
				<Badge variant="destructive">{pendingCount} pending review{pendingCount === 1 ? '' : 's'}</Badge>
			</a>
		{/if}
	</div>

	{#if loadError}
		<p class="text-destructive text-sm">{loadError}</p>
	{/if}

	<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
		<Card.Root>
			<Card.Header><Card.Title>Recipe Queue</Card.Title></Card.Header>
			<Card.Content>
				<p class="text-muted-foreground text-sm">Review community recipe submissions</p>
			</Card.Content>
			<Card.Footer>
				<Button variant="outline" href="/admin/recipes" class="w-full">
					Open queue {#if pendingCount > 0}({pendingCount}){/if}
				</Button>
			</Card.Footer>
		</Card.Root>

		{#each modpacks as mp (mp.id)}
			<Card.Root>
				<Card.Header>
					<div class="flex items-center justify-between">
						<Card.Title class="text-base">{mp.name}</Card.Title>
						<Badge variant={mp.status === 'published' ? 'default' : 'secondary'}>{mp.status}</Badge>
					</div>
					<Card.Description>v{mp.version}</Card.Description>
				</Card.Header>
				<Card.Footer>
					<Button variant="outline" href="/admin/modpacks/{mp.id}" class="w-full">Manage</Button>
				</Card.Footer>
			</Card.Root>
		{/each}
	</div>

	{#if $isSuperuser}
		<Card.Root>
			<Card.Header><Card.Title>Superuser</Card.Title></Card.Header>
			<Card.Content class="flex gap-3">
				<Button variant="outline" href="/admin/mods">All mods</Button>
				<Button variant="outline" href="/admin/modpacks">All modpacks</Button>
			</Card.Content>
		</Card.Root>
	{/if}
</div>
