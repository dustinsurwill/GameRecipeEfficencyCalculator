<script lang="ts">
	import { onMount } from 'svelte';
	import { discover, fmtNum, type DMachine } from '$lib/discover';

	let machines = $state<DMachine[]>([]);
	let q = $state('');
	let loading = $state(true);
	onMount(async () => {
		machines = (await discover.machines()).items;
		loading = false;
	});
	const shown = $derived(machines.filter((m) => !q || m.name.toLowerCase().includes(q.toLowerCase()) || m.mod_name.toLowerCase().includes(q.toLowerCase())));
</script>

<svelte:head><title>Machines — RecipeBook</title></svelte:head>

<main class="mx-auto max-w-5xl space-y-5 px-4 py-8">
	<div class="flex flex-wrap items-end justify-between gap-3">
		<div>
			<h1 class="text-3xl font-bold tracking-tight">Machines</h1>
			<p class="text-muted-foreground text-sm">Every crafting station and processing machine, with the recipes it runs.</p>
		</div>
		<input bind:value={q} placeholder="Filter machines or mods…" aria-label="Filter machines" class="bg-card focus:border-primary w-64 rounded-lg border px-3 py-2 text-sm outline-none" />
	</div>
	<ul class="divide-y rounded-lg border">
		{#each shown as m (m.id)}
			<li>
				<a href="/machines/{m.id}" class="hover:bg-accent flex items-center justify-between gap-4 px-4 py-2.5">
					<span><span class="font-medium">{m.name}</span> <span class="text-muted-foreground text-sm">· {m.mod_name}</span></span>
					<span class="text-muted-foreground text-sm tabular-nums">{fmtNum(m.recipe_count)} recipes</span>
				</a>
			</li>
		{/each}
	</ul>
	{#if !loading && shown.length === 0}<p class="text-muted-foreground py-12 text-center">No machines match “{q}”.</p>{/if}
</main>
