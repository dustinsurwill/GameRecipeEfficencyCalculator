<script lang="ts">
	import { onMount } from 'svelte';
	import { discover, fmtNum, type DMod } from '$lib/discover';

	let mods = $state<DMod[]>([]);
	let q = $state('');
	let loading = $state(true);
	let timer: ReturnType<typeof setTimeout>;

	async function load() {
		loading = true;
		try {
			mods = (await discover.mods({ q: q.trim() || undefined, limit: 300 })).items;
		} finally {
			loading = false;
		}
	}
	onMount(load);
	function onQ() {
		clearTimeout(timer);
		timer = setTimeout(load, 200);
	}
</script>

<svelte:head><title>Mods — RecipeBook</title></svelte:head>

<main class="mx-auto max-w-7xl space-y-5 px-4 py-8">
	<div class="flex flex-wrap items-end justify-between gap-3">
		<h1 class="text-3xl font-bold tracking-tight">Mods</h1>
		<input bind:value={q} oninput={onQ} placeholder="Filter mods…" aria-label="Filter mods" class="bg-card focus:border-primary w-64 rounded-lg border px-3 py-2 text-sm outline-none" />
	</div>
	<ul class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
		{#each mods as m (m.namespace)}
			<li>
				<a href="/mods/{m.namespace}" class="bg-card hover:border-primary/60 block h-full rounded-lg border p-4 transition-colors">
					<div class="font-semibold">{m.name}</div>
					<div class="text-muted-foreground mt-0.5 font-mono text-xs">{m.namespace}</div>
					{#if m.description}<p class="text-muted-foreground mt-2 line-clamp-2 text-sm">{m.description}</p>{/if}
					<div class="mt-3 flex gap-4 text-xs tabular-nums">
						<span>{fmtNum(m.item_count)} items</span><span>{fmtNum(m.recipe_count)} recipes</span>
					</div>
				</a>
			</li>
		{/each}
	</ul>
	{#if !loading && mods.length === 0}<p class="text-muted-foreground py-12 text-center">No mods match “{q}”.</p>{/if}
</main>
