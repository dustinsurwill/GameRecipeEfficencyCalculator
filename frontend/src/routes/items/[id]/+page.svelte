<script lang="ts">
	import { page } from '$app/state';
	import ItemTile from '$lib/components/ItemTile.svelte';
	import RecipeList from '$lib/components/RecipeList.svelte';
	import { discover, tagHref, fmtNum, type ItemDetail } from '$lib/discover';

	let item = $state<ItemDetail | null>(null);
	let error = $state('');
	let tab = $state<'output' | 'input'>('output');
	let machine = $state('');
	let showAllTags = $state(false);

	const id = $derived(page.params.id!);

	$effect(() => {
		const current = id;
		item = null;
		error = '';
		machine = '';
		discover
			.item(current)
			.then((d) => {
				if (current !== id) return;
				item = d;
				tab = d.made_by > 0 || d.used_in === 0 ? 'output' : 'input';
			})
			.catch(() => (error = 'We couldn’t find that item in this pack.'));
	});

	const facets = $derived(item ? (tab === 'output' ? item.machines.made_in : item.machines.used_in) : []);
	const tags = $derived(item ? (showAllTags ? item.tags : item.tags.slice(0, 8)) : []);

	function switchTab(t: 'output' | 'input') {
		tab = t;
		machine = '';
	}
</script>

<svelte:head><title>{item ? `${item.name} — RecipeBook` : 'Item — RecipeBook'}</title></svelte:head>

<main class="mx-auto max-w-5xl space-y-8 px-4 py-8">
	{#if error}
		<div class="py-20 text-center">
			<p class="text-lg">{error}</p>
			<a href="/items" class="text-primary mt-3 inline-block underline">Browse all items</a>
		</div>
	{:else if !item}
		<div class="bg-muted h-28 animate-pulse rounded-xl"></div>
	{:else}
		<header class="flex flex-wrap items-start gap-5">
			<ItemTile name={item.name} namespace={item.namespace} size={72} />
			<div class="min-w-0 flex-1 space-y-2">
				<h1 class="text-3xl font-bold tracking-tight">{item.name}</h1>
				<p class="text-muted-foreground flex flex-wrap items-center gap-x-3 text-sm">
					<a href="/mods/{item.namespace}" class="hover:text-foreground font-medium underline-offset-4 hover:underline">{item.mod_name}</a>
					<code class="bg-muted rounded px-1.5 py-0.5 text-xs">{item.item_id}</code>
				</p>
				{#if item.tags.length}
					<div class="flex flex-wrap items-center gap-1.5 pt-1">
						{#each tags as t}
							<a href={tagHref(t.id)} class="bg-secondary hover:bg-accent rounded-full border px-2.5 py-0.5 text-xs" title="{t.member_count} items share this tag">#{t.id}</a>
						{/each}
						{#if item.tags.length > 8 && !showAllTags}
							<button onclick={() => (showAllTags = true)} class="text-muted-foreground hover:text-foreground text-xs underline">+{item.tags.length - 8} more</button>
						{/if}
					</div>
				{/if}
			</div>
			<a href="/routes?target={encodeURIComponent(item.item_id)}" class="bg-secondary hover:bg-accent rounded-md border px-3.5 py-2 text-sm font-medium">
				Find routes to this item →
			</a>
		</header>

		<section class="space-y-4">
			<div role="tablist" class="flex gap-1 border-b">
				{#each [['output', 'How to make it', item.made_by], ['input', 'What it’s used for', item.used_in]] as [key, label, n]}
					<button
						role="tab"
						aria-selected={tab === key}
						onclick={() => switchTab(key as 'output' | 'input')}
						class="-mb-px border-b-2 px-4 py-2.5 text-sm font-medium transition-colors {tab === key ? 'border-primary text-foreground' : 'text-muted-foreground hover:text-foreground border-transparent'}"
					>
						{label} <span class="text-muted-foreground ml-1 tabular-nums">{fmtNum(n as number)}</span>
					</button>
				{/each}
			</div>

			{#if facets.length > 1}
				<div class="flex flex-wrap gap-1.5" aria-label="Filter by machine">
					<button onclick={() => (machine = '')} class="rounded-full border px-3 py-1 text-xs {machine === '' ? 'bg-primary text-primary-foreground border-primary' : 'bg-card hover:bg-accent'}">All</button>
					{#each facets as f}
						<button onclick={() => (machine = f.id)} class="rounded-full border px-3 py-1 text-xs {machine === f.id ? 'bg-primary text-primary-foreground border-primary' : 'bg-card hover:bg-accent'}">
							{f.name} <span class="opacity-70">{fmtNum(f.count)}</span>
						</button>
					{/each}
				</div>
			{/if}

			{#key tab + id}
				<RecipeList
					params={{ item: item.item_id, role: tab, machine: machine || undefined }}
					highlight={item.item_id}
					emptyText={tab === 'output' ? 'Nothing in this pack crafts this item — it may be found in the world or dropped by mobs.' : 'This item isn’t an ingredient in any recipe.'}
				/>
			{/key}
		</section>
	{/if}
</main>
