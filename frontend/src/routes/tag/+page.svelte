<script lang="ts">
	import { page } from '$app/state';
	import ItemGrid from '$lib/components/ItemGrid.svelte';
	import RecipeList from '$lib/components/RecipeList.svelte';
	import { discover, fmtNum, type DTag, type DItem } from '$lib/discover';

	let tag = $state<(DTag & { members: DItem[] }) | null>(null);
	let error = $state('');
	let tab = $state<'members' | 'recipes'>('members');
	const id = $derived(page.url.searchParams.get('id') ?? '');

	$effect(() => {
		const cur = id;
		tag = null;
		error = '';
		discover.tag(cur).then((t) => (tag = t)).catch(() => (error = 'We couldn’t find that tag.'));
	});
</script>

<svelte:head><title>#{id} — RecipeBook</title></svelte:head>

<main class="mx-auto max-w-6xl space-y-6 px-4 py-8">
	{#if error}
		<p class="py-20 text-center text-lg">{error}</p>
	{:else if !tag}
		<div class="bg-muted h-20 animate-pulse rounded-xl"></div>
	{:else}
		<header class="space-y-1">
			<p class="text-primary text-sm font-semibold uppercase tracking-widest">Item tag</p>
			<h1 class="text-3xl font-bold tracking-tight">#{tag.id}</h1>
			<p class="text-muted-foreground max-w-2xl text-sm">
				Recipes that ask for this tag accept any of its {fmtNum(tag.members.length)} items interchangeably.
			</p>
		</header>
		<div role="tablist" class="flex gap-1 border-b">
			{#each [['members', 'Items in this tag', tag.members.length], ['recipes', 'Recipes that use it', tag.recipe_count]] as [key, label, n]}
				<button role="tab" aria-selected={tab === key} onclick={() => (tab = key as typeof tab)}
					class="-mb-px border-b-2 px-4 py-2.5 text-sm font-medium {tab === key ? 'border-primary' : 'text-muted-foreground hover:text-foreground border-transparent'}">
					{label} <span class="text-muted-foreground ml-1 tabular-nums">{fmtNum(n as number)}</span>
				</button>
			{/each}
		</div>
		{#if tab === 'members'}
			{#if tag.members.length}<ItemGrid items={tag.members} />{:else}<p class="text-muted-foreground py-8 text-center text-sm">No items with this tag appear in recipes.</p>{/if}
		{:else}
			{#key id}<RecipeList params={{ item: '#' + tag.id, role: 'input' }} />{/key}
		{/if}
	{/if}
</main>
