<script lang="ts">
	import { onMount } from 'svelte';
	import SearchBox from '$lib/components/SearchBox.svelte';
	import ItemGrid from '$lib/components/ItemGrid.svelte';
	import { discover, fmtNum, type Overview } from '$lib/discover';

	let data = $state<Overview | null>(null);
	let error = $state('');

	onMount(async () => {
		try {
			data = await discover.overview();
		} catch {
			error = 'Could not reach the API. Is the backend running?';
		}
	});

	const examples = [
		['minecraft:piston', 'Piston'],
		['alltheores:osmium_ingot', 'Osmium Ingot'],
		['minecraft:netherite_ingot', 'Netherite Ingot'],
		['ae2:controller', 'ME Controller']
	];
</script>

<svelte:head><title>RecipeBook — find any item and how to make it</title></svelte:head>

<main class="mx-auto max-w-5xl space-y-14 px-4 pb-20 pt-14 sm:pt-20">
	<section class="space-y-6">
		<p class="text-primary text-sm font-semibold uppercase tracking-widest">All The Mods 10 · Minecraft 1.21.1</p>
		<h1 class="max-w-2xl text-4xl font-bold leading-tight tracking-tight sm:text-5xl">
			Look up any item.<br /><span class="text-muted-foreground">See how it’s made and what it’s for.</span>
		</h1>
		<div class="max-w-2xl"><SearchBox size="lg" autofocus placeholder="Try “piston”, “osmium”, “ME controller”…" /></div>
		<p class="text-muted-foreground flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
			<span>For example:</span>
			{#each examples as [id, label]}
				<a href="/items/{encodeURIComponent(id)}" class="text-foreground underline decoration-dotted underline-offset-4 hover:text-primary">{label}</a>
			{/each}
			<span class="ml-auto hidden sm:inline">Press <kbd class="bg-muted rounded border px-1.5 text-xs">/</kbd> to search anywhere</span>
		</p>
	</section>

	{#if error}
		<p class="text-destructive">{error}</p>
	{:else if data}
		<section aria-label="Pack contents" class="grid grid-cols-2 gap-px overflow-hidden rounded-xl border sm:grid-cols-4">
			{#each [['Items', data.counts.items, '/items'], ['Recipes', data.counts.recipes, '/machines'], ['Mods', data.counts.mods, '/mods'], ['Machines', data.counts.machines, '/machines']] as [label, n, href]}
				<a href={href as string} class="bg-card hover:bg-accent p-4 transition-colors">
					<div class="text-2xl font-bold tabular-nums">{fmtNum(n as number)}</div>
					<div class="text-muted-foreground text-sm">{label}</div>
				</a>
			{/each}
		</section>

		<section class="space-y-4">
			<div class="flex items-baseline justify-between">
				<h2 class="text-xl font-semibold">Well-connected items</h2>
				<a href="/items?sort=used_in" class="text-muted-foreground hover:text-foreground text-sm">Browse all items →</a>
			</div>
			<ItemGrid items={data.popular_items} />
		</section>

		<div class="grid gap-10 md:grid-cols-2">
			<section class="space-y-4">
				<div class="flex items-baseline justify-between">
					<h2 class="text-xl font-semibold">Mods with the most recipes</h2>
					<a href="/mods" class="text-muted-foreground hover:text-foreground text-sm">All mods →</a>
				</div>
				<ul class="divide-y rounded-lg border">
					{#each data.top_mods as m}
						<li>
							<a href="/mods/{m.namespace}" class="hover:bg-accent flex items-center justify-between px-3 py-2 text-sm">
								<span class="font-medium">{m.name}</span>
								<span class="text-muted-foreground tabular-nums">{fmtNum(m.recipe_count)} recipes</span>
							</a>
						</li>
					{/each}
				</ul>
			</section>
			<section class="space-y-4">
				<div class="flex items-baseline justify-between">
					<h2 class="text-xl font-semibold">Busiest machines</h2>
					<a href="/machines" class="text-muted-foreground hover:text-foreground text-sm">All machines →</a>
				</div>
				<ul class="divide-y rounded-lg border">
					{#each data.top_machines as m}
						<li>
							<a href="/machines/{m.id}" class="hover:bg-accent flex items-center justify-between px-3 py-2 text-sm">
								<span><span class="font-medium">{m.name}</span> <span class="text-muted-foreground">· {m.mod_name}</span></span>
								<span class="text-muted-foreground tabular-nums">{fmtNum(m.recipe_count)}</span>
							</a>
						</li>
					{/each}
				</ul>
			</section>
		</div>

		<section class="bg-card flex flex-wrap items-center justify-between gap-4 rounded-xl border p-5">
			<div>
				<h2 class="font-semibold">Planning a production line?</h2>
				<p class="text-muted-foreground text-sm">Find every multi-step route from one item to another, ranked by yield.</p>
			</div>
			<a href="/routes" class="bg-primary text-primary-foreground rounded-md px-4 py-2 text-sm font-semibold hover:opacity-90">Open route finder</a>
		</section>
	{:else}
		<div class="bg-muted h-40 animate-pulse rounded-xl"></div>
	{/if}
</main>
