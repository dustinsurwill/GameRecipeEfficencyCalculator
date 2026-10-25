<script lang="ts">
	import SlotChip from './SlotChip.svelte';
	import { fmtNum, type DRecipe } from '$lib/discover';

	let { recipe, highlight = null }: { recipe: DRecipe; highlight?: string | null } = $props();

	const inputs = $derived([...recipe.inputs, ...recipe.aux_inputs]);
	const meta = $derived(
		[
			recipe.time_ticks ? `${(recipe.time_ticks / 20).toFixed(recipe.time_ticks % 20 ? 1 : 0)}s` : null,
			recipe.energy_rf ? `${fmtNum(recipe.energy_rf)} RF` : null
		].filter(Boolean)
	);
</script>

<article class="bg-card rounded-lg border">
	<header class="text-muted-foreground flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-3 py-1.5 text-xs">
		<a href="/machines/{recipe.machine.id}" class="text-foreground font-semibold hover:underline">
			{recipe.machine.name}
		</a>
		<a href="/mods/{recipe.namespace}" class="hover:text-foreground font-mono">{recipe.namespace}</a>
		{#each meta as m}<span>{m}</span>{/each}
		{#if recipe.modpack_override}
			<span class="bg-primary/15 text-primary ml-auto rounded px-1.5 py-0.5 font-medium">Modpack recipe</span>
		{/if}
	</header>
	<div class="grid items-center gap-3 p-3 md:grid-cols-[1fr_auto_1fr]">
		<div class="grid gap-0.5 sm:grid-cols-2 md:grid-cols-1 lg:grid-cols-2">
			{#each inputs as s}<SlotChip slot={s} {highlight} />{/each}
		</div>
		<div class="text-muted-foreground hidden text-xl md:block" aria-hidden="true">→</div>
		<div class="text-muted-foreground text-xs md:hidden" aria-hidden="true">produces ↓</div>
		<div class="grid gap-0.5 sm:grid-cols-2 md:grid-cols-1 lg:grid-cols-2">
			{#each recipe.outputs as s}<SlotChip slot={s} {highlight} />{/each}
		</div>
	</div>
</article>
