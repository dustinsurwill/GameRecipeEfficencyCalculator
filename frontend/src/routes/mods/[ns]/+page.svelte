<script lang="ts">
	import { page } from '$app/state';
	import ItemGrid from '$lib/components/ItemGrid.svelte';
	import RecipeList from '$lib/components/RecipeList.svelte';
	import { discover, fmtNum, type DMod, type DMachine, type DItem } from '$lib/discover';

	let mod = $state<(DMod & { machines: DMachine[] }) | null>(null);
	let error = $state('');
	let tab = $state<'items' | 'machines' | 'recipes'>('items');
	let items = $state<DItem[]>([]);
	let itemTotal = $state(0);
	let q = $state('');
	let timer: ReturnType<typeof setTimeout>;
	const PAGE = 48;
	const ns = $derived(page.params.ns!);

	$effect(() => {
		const cur = ns;
		mod = null;
		error = '';
		discover.mod(cur).then((m) => (mod = m)).catch(() => (error = 'We couldn’t find that mod.'));
		loadItems(true);
	});

	async function loadItems(reset: boolean) {
		const r = await discover.items({ mod: ns, q: q || undefined, sort: 'used_in', limit: PAGE, offset: reset ? 0 : items.length });
		items = reset ? r.items : [...items, ...r.items];
		itemTotal = r.total;
	}
	function onQ() {
		clearTimeout(timer);
		timer = setTimeout(() => loadItems(true), 220);
	}
</script>

<svelte:head><title>{mod ? `${mod.name} — RecipeBook` : 'Mod — RecipeBook'}</title></svelte:head>

<main class="mx-auto max-w-6xl space-y-6 px-4 py-8">
	{#if error}
		<p class="py-20 text-center text-lg">{error} <a href="/mods" class="text-primary underline">Browse mods</a></p>
	{:else if !mod}
		<div class="bg-muted h-24 animate-pulse rounded-xl"></div>
	{:else}
		<header class="space-y-1">
			<h1 class="text-3xl font-bold tracking-tight">{mod.name}</h1>
			<p class="text-muted-foreground font-mono text-sm">{mod.namespace}</p>
			{#if mod.description}<p class="max-w-2xl pt-1 text-sm">{mod.description}</p>{/if}
		</header>

		<div role="tablist" class="flex gap-1 border-b">
			{#each [['items', 'Items', mod.item_count], ['machines', 'Machines', mod.machines.length], ['recipes', 'Recipes', mod.recipe_count]] as [key, label, n]}
				<button role="tab" aria-selected={tab === key} onclick={() => (tab = key as typeof tab)}
					class="-mb-px border-b-2 px-4 py-2.5 text-sm font-medium {tab === key ? 'border-primary' : 'text-muted-foreground hover:text-foreground border-transparent'}">
					{label} <span class="text-muted-foreground ml-1 tabular-nums">{fmtNum(n as number)}</span>
				</button>
			{/each}
		</div>

		{#if tab === 'items'}
			<input bind:value={q} oninput={onQ} placeholder="Filter this mod’s items…" aria-label="Filter items" class="bg-card focus:border-primary w-full max-w-sm rounded-lg border px-3 py-2 text-sm outline-none" />
			<ItemGrid {items} showMod={false} />
			{#if items.length < itemTotal}
				<button onclick={() => loadItems(false)} class="bg-secondary hover:bg-accent mx-auto block rounded-md border px-4 py-2 text-sm font-medium">Show more ({fmtNum(itemTotal - items.length)} left)</button>
			{/if}
		{:else if tab === 'machines'}
			{#if mod.machines.length === 0}
				<p class="text-muted-foreground py-8 text-center text-sm">This mod doesn’t add any crafting stations to the pack.</p>
			{:else}
				<ul class="grid gap-3 sm:grid-cols-2">
					{#each mod.machines as m}
						<li><a href="/machines/{m.id}" class="bg-card hover:border-primary/60 flex items-center justify-between rounded-lg border p-3"><span class="font-medium">{m.name}</span><span class="text-muted-foreground text-sm tabular-nums">{fmtNum(m.recipe_count)} recipes</span></a></li>
					{/each}
				</ul>
			{/if}
		{:else}
			{#key ns}<RecipeList params={{ mod: ns }} />{/key}
		{/if}
	{/if}
</main>
