<script lang="ts">
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import type { Game } from '$lib/types';

	const BASE = import.meta.env.VITE_API_BASE as string;

	let games = $state<Game[]>([]);
	let search = $state('');
	let loading = $state(true);

	let filtered = $derived(
		search
			? games.filter(
					(g) =>
						g.name.toLowerCase().includes(search.toLowerCase()) ||
						g.slug.toLowerCase().includes(search.toLowerCase())
			  )
			: games
	);

	onMount(async () => {
		const r = await fetch(`${BASE}/games`);
		if (r.ok) games = await r.json();
		loading = false;
	});
</script>

<main class="mx-auto max-w-4xl px-4 py-10 space-y-6">
	<div class="flex items-center justify-between gap-4">
		<h1 class="text-2xl font-bold">Games</h1>
		<Input placeholder="Search…" bind:value={search} class="max-w-xs" />
	</div>

	{#if loading}
		<p class="text-muted-foreground">Loading…</p>
	{:else if filtered.length === 0}
		<p class="text-muted-foreground">{search ? `No results for "${search}"` : 'No games yet.'}</p>
	{:else}
		<div class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
			{#each filtered as game}
				<a
					href="/games/{game.id}"
					class="flex items-center gap-3 p-4 border rounded-lg hover:border-foreground/30 hover:bg-muted/30 transition-colors"
				>
					{#if game.icon_url}
						<img src={game.icon_url} alt={game.name} class="w-12 h-12 rounded flex-shrink-0" />
					{:else}
						<div class="w-12 h-12 rounded bg-muted flex items-center justify-center text-xl font-bold text-muted-foreground flex-shrink-0">
							{game.name[0].toUpperCase()}
						</div>
					{/if}
					<div>
						<p class="font-semibold">{game.name}</p>
						<p class="text-xs text-muted-foreground font-mono">{game.slug}</p>
					</div>
				</a>
			{/each}
		</div>
	{/if}
</main>
