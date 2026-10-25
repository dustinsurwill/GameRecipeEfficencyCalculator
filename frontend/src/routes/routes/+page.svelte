<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/state';
	import SearchBox from '$lib/components/SearchBox.svelte';
	import ItemTile from '$lib/components/ItemTile.svelte';
	import { api } from '$lib/api';
	import { discover, itemHref, tagLabel, fmtQty, type DItem } from '$lib/discover';
	import type { ChainResponse, ChainSlot, ModPackIdentity } from '$lib/types';

	let target = $state<{ id: string; name: string; ns: string } | null>(null);
	let start = $state<{ id: string; name: string; ns: string } | null>(null);
	let modpacks = $state<ModPackIdentity[]>([]);
	let modpackId = $state('');
	let skipCrafting = $state(true);
	let maxDepth = $state(6);
	let result = $state<ChainResponse | null>(null);
	let loading = $state(false);
	let error = $state('');

	const CRAFTING = ['minecraft:crafting_shaped', 'minecraft:crafting_shapeless', 'crafting_shaped', 'crafting_shapeless', 'kubejs:shaped', 'kubejs:shapeless'];

	const toSel = (i: DItem) => ({ id: i.item_id, name: i.name, ns: i.namespace });

	onMount(async () => {
		modpacks = await api.get<ModPackIdentity[]>('/modpacks').catch(() => []);
		modpackId = modpacks[0]?.id ?? '';
		const t = page.url.searchParams.get('target');
		const s = page.url.searchParams.get('start');
		if (t) discover.item(t).then((d) => (target = { id: d.item_id, name: d.name, ns: d.namespace })).catch(() => {});
		if (s) discover.item(s).then((d) => (start = { id: d.item_id, name: d.name, ns: d.namespace })).catch(() => {});
	});

	async function run() {
		if (!target || !modpackId) return;
		loading = true;
		error = '';
		result = null;
		try {
			const p = new URLSearchParams({ modpack: modpackId, target: target.id, max_depth: String(maxDepth), limit: '25' });
			if (start) p.set('start', start.id);
			if (skipCrafting) for (const c of CRAFTING) p.append('exclude', c);
			result = await api.get<ChainResponse>(`/chains?${p}`);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Route search failed.';
		} finally {
			loading = false;
		}
	}

	const nm = (s: ChainSlot) => s.canonical_name ?? (s.item_id ? s.item_id.split(':')[1].replace(/_/g, ' ') : s.tag ? `Any ${tagLabel(s.tag)}` : 'Fluid / chemical');
</script>

{#snippet slotRow(s: ChainSlot)}
	<span class="flex items-center gap-2 text-sm">
		<ItemTile name={nm(s)} namespace={(s.item_id ?? s.tag ?? '').split(':')[0]} size={24} tag={!s.item_id && !!s.tag} />
		<span class="text-primary tabular-nums">{fmtQty(s.quantity)}×</span>
		{#if s.item_id && !s.is_fluid}<a href={itemHref(s.item_id)} class="hover:underline">{nm(s)}</a>{:else}<span>{nm(s)}</span>{/if}
	</span>
{/snippet}

<svelte:head><title>Route finder — RecipeBook</title></svelte:head>

<main class="mx-auto max-w-4xl space-y-6 px-4 py-8">
	<header>
		<h1 class="text-3xl font-bold tracking-tight">Route finder</h1>
		<p class="text-muted-foreground max-w-2xl text-sm">
			Pick the item you want. Add a starting item to see every way to turn one into the other, ranked by how much you get per input.
			Leave it empty to trace the target back to raw materials.
		</p>
	</header>

	<form onsubmit={(e) => { e.preventDefault(); run(); }} class="bg-card space-y-5 rounded-xl border p-5">
		<div class="grid gap-5 sm:grid-cols-2">
			{#each [['Start with', 'optional', start, 'start'], ['I want', 'required', target, 'target']] as [label, hint, sel, which]}
				<div class="space-y-1.5">
					<div class="text-sm font-medium">{label} <span class="text-muted-foreground font-normal">· {hint}</span></div>
					{#if sel}
						{@const s = sel as { id: string; name: string; ns: string }}
						<div class="bg-background flex items-center gap-3 rounded-lg border p-2">
							<ItemTile name={s.name} namespace={s.ns} size={32} />
							<span class="min-w-0 flex-1"><span class="block truncate text-sm font-semibold">{s.name}</span><span class="text-muted-foreground block truncate font-mono text-xs">{s.id}</span></span>
							<button type="button" onclick={() => (which === 'start' ? (start = null) : (target = null))} class="text-muted-foreground hover:text-foreground px-2 text-sm" aria-label="Clear {label}">✕</button>
						</div>
					{:else}
						<SearchBox itemsOnly placeholder="Search for an item…" onpick={(i) => (which === 'start' ? (start = toSel(i)) : (target = toSel(i)))} />
					{/if}
				</div>
			{/each}
		</div>

		<div class="flex flex-wrap items-center gap-x-6 gap-y-3 text-sm">
			<label class="flex cursor-pointer items-center gap-2"><input type="checkbox" bind:checked={skipCrafting} /> Ignore crafting-table recipes</label>
			<label class="flex items-center gap-2">Max steps
				<select bind:value={maxDepth} class="bg-background rounded-md border px-2 py-1">{#each [3, 4, 5, 6, 8, 10] as n}<option value={n}>{n}</option>{/each}</select>
			</label>
			{#if modpacks.length > 1}
				<label class="flex items-center gap-2">Pack
					<select bind:value={modpackId} class="bg-background rounded-md border px-2 py-1">{#each modpacks as m}<option value={m.id}>{m.name}</option>{/each}</select>
				</label>
			{/if}
			<button type="submit" disabled={!target || loading} class="bg-primary text-primary-foreground ml-auto rounded-md px-5 py-2 font-semibold disabled:opacity-40">
				{loading ? 'Searching…' : 'Find routes'}
			</button>
		</div>
	</form>

	{#if error}<p class="text-destructive text-sm">{error}</p>{/if}

	{#if result}
		{#if result.total_found === 0}
			<p class="text-muted-foreground py-10 text-center">{result.note ?? 'No routes found. Try more steps, or allow crafting-table recipes.'}</p>
		{:else}
			<p class="text-muted-foreground text-sm">
				{result.total_found} route{result.total_found === 1 ? '' : 's'} found, showing the best {result.returned}.
				{#if (result as any).truncated}<span class="text-primary">Search stopped early because this item has a very large number of routes — lower the step limit or ignore crafting recipes to narrow it.</span>{/if}
			</p>
			{#each result.chains as chain}
				<article class="bg-card rounded-xl border">
					<header class="flex flex-wrap items-baseline justify-between gap-2 border-b px-4 py-3">
						<h2 class="font-semibold">
							Route {chain.rank}
							<span class="text-muted-foreground font-normal">· {chain.steps.length} step{chain.steps.length === 1 ? '' : 's'}</span>
							{#if chain.has_cycle}<span class="text-primary ml-2 text-xs">loops back</span>{/if}
						</h2>
						<span class="text-sm tabular-nums">
							{#if chain.yield_per_start_item != null}<b>{chain.yield_per_start_item.toFixed(2)}</b> per input
							{:else if chain.raw_materials_per_target != null}<b>{chain.raw_materials_per_target.toFixed(2)}</b> raw per item{/if}
						</span>
					</header>
					<ol class="divide-y">
						{#each chain.steps as step}
							<li class="grid gap-3 px-4 py-3 md:grid-cols-[10rem_1fr_1fr]">
								<div class="text-sm"><span class="text-muted-foreground mr-1 tabular-nums">{step.step}.</span><a href="/machines/{step.machine.id}" class="font-medium hover:underline">{step.machine.name}</a></div>
								<div class="space-y-1">{#each [...step.inputs, ...step.aux_inputs] as s}{@render slotRow(s)}{/each}</div>
								<div class="space-y-1 md:border-l md:pl-3">{#each step.outputs as s}{@render slotRow(s)}{/each}</div>
							</li>
						{/each}
					</ol>
				</article>
			{/each}
		{/if}
	{/if}
</main>
