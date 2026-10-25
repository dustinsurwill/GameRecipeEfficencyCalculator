<script lang="ts">
	import { page } from '$app/state';
	import RecipeList from '$lib/components/RecipeList.svelte';
	import { discover, fmtNum, type DMachine } from '$lib/discover';

	let machine = $state<DMachine | null>(null);
	let error = $state('');
	const id = $derived(page.params.id!);

	$effect(() => {
		const cur = id;
		machine = null;
		discover.machine(cur).then((m) => (machine = m)).catch(() => (error = 'We couldn’t find that machine.'));
	});
</script>

<svelte:head><title>{machine ? `${machine.name} — RecipeBook` : 'Machine — RecipeBook'}</title></svelte:head>

<main class="mx-auto max-w-5xl space-y-6 px-4 py-8">
	{#if error}
		<p class="py-20 text-center text-lg">{error} <a href="/machines" class="text-primary underline">Browse machines</a></p>
	{:else if !machine}
		<div class="bg-muted h-20 animate-pulse rounded-xl"></div>
	{:else}
		<header class="space-y-1">
			<h1 class="text-3xl font-bold tracking-tight">{machine.name}</h1>
			<p class="text-muted-foreground text-sm">
				<a href="/mods/{machine.namespace}" class="hover:text-foreground underline-offset-4 hover:underline">{machine.mod_name}</a>
				· {fmtNum(machine.recipe_count)} recipes
			</p>
			<p class="flex flex-wrap gap-1.5 pt-1">
				{#each machine.recipe_types as t}<code class="bg-muted rounded px-1.5 py-0.5 text-xs">{t}</code>{/each}
			</p>
		</header>
		{#key id}<RecipeList params={{ machine: id }} />{/key}
	{/if}
</main>
