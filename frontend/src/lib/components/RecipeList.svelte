<script lang="ts">
	import RecipeCard from './RecipeCard.svelte';
	import { discover, fmtNum, type DRecipe } from '$lib/discover';

	let {
		params,
		highlight = null,
		emptyText = 'No recipes found.'
	}: {
		params: Record<string, string | number | undefined>;
		highlight?: string | null;
		emptyText?: string;
	} = $props();

	const PAGE = 12;
	let recipes = $state<DRecipe[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let error = $state('');
	let seq = 0;

	async function load(reset: boolean) {
		const mine = ++seq;
		loading = true;
		error = '';
		try {
			const res = await discover.recipes({ ...params, limit: PAGE, offset: reset ? 0 : recipes.length });
			if (mine !== seq) return;
			recipes = reset ? res.items : [...recipes, ...res.items];
			total = res.total;
		} catch (e) {
			if (mine === seq) error = e instanceof Error ? e.message : 'Could not load recipes.';
		} finally {
			if (mine === seq) loading = false;
		}
	}

	$effect(() => {
		JSON.stringify(params); // re-run when filters change
		load(true);
	});
</script>

<div class="space-y-3">
	{#each recipes as r (r.id)}
		<RecipeCard recipe={r} {highlight} />
	{/each}

	{#if loading && recipes.length === 0}
		{#each Array(3) as _}<div class="bg-muted h-24 animate-pulse rounded-lg"></div>{/each}
	{:else if error}
		<p class="text-destructive text-sm">{error}</p>
	{:else if recipes.length === 0}
		<p class="text-muted-foreground py-8 text-center text-sm">{emptyText}</p>
	{/if}

	{#if recipes.length > 0 && recipes.length < total}
		<div class="flex items-center justify-center gap-3 pt-2">
			<button
				onclick={() => load(false)}
				disabled={loading}
				class="bg-secondary hover:bg-accent rounded-md border px-4 py-2 text-sm font-medium disabled:opacity-50"
			>
				{loading ? 'Loading…' : `Show ${Math.min(PAGE, total - recipes.length)} more`}
			</button>
			<span class="text-muted-foreground text-xs">{fmtNum(recipes.length)} of {fmtNum(total)}</span>
		</div>
	{/if}
</div>
