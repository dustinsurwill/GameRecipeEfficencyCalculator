<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import * as Table from '$lib/components/ui/table';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import type { Mod, ModVersion, MachineIdentity, ItemAppearanceWithIdentity, ItemInfoRead, RecipeInfo } from '$lib/types';

	const BASE = import.meta.env.VITE_API_BASE as string;
	const LIMIT = 50;

	let mod = $state<Mod | null>(null);
	let versions = $state<ModVersion[]>([]);
	let machines = $state<MachineIdentity[]>([]);
	let items = $state<ItemAppearanceWithIdentity[]>([]);
	let itemsOffset = $state(0);
	let hasMoreItems = $state(true);
	let loading = $state(true);
	let itemsLoading = $state(false);
	let error = $state('');

	let activeModVersionId = $state('');

	let machineSearch = $state('');
	let itemSearch = $state('');
	let itemSearchDebounced = $state('');
	let debounceTimer: ReturnType<typeof setTimeout>;

	let jeiItem = $state<{ id: string; info: ItemInfoRead | null; loading: boolean } | null>(null);
	let jeiMachine = $state<{ id: string; name: string; recipes: RecipeInfo[] | null; loading: boolean } | null>(null);

	let filteredMachines = $derived(
		machineSearch
			? machines.filter((m) => m.name.toLowerCase().includes(machineSearch.toLowerCase()))
			: machines
	);

	function onItemSearchInput(val: string) {
		itemSearch = val;
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => { itemSearchDebounced = val; }, 350);
	}

	$effect(() => {
		const term = itemSearchDebounced;
		if (mod && activeModVersionId) {
			loadItems(0, term);
		}
	});

	async function loadItems(offset: number, search: string = '') {
		const id = $page.params.id;
		itemsLoading = true;
		try {
			const params = new URLSearchParams({ limit: String(LIMIT), offset: String(offset) });
			if (search) params.set('search', search);
			if (activeModVersionId) params.set('mod_version_id', activeModVersionId);
			const r = await fetch(`${BASE}/mods/${id}/items?${params}`);
			if (r.ok) {
				const batch: ItemAppearanceWithIdentity[] = await r.json();
				items = offset === 0 ? batch : [...items, ...batch];
				hasMoreItems = batch.length === LIMIT;
				itemsOffset = offset + batch.length;
			}
		} finally {
			itemsLoading = false;
		}
	}

	async function openItemJei(itemId: string) {
		if (jeiItem?.id === itemId) { jeiItem = null; return; }
		if (!activeModVersionId) return;
		jeiItem = { id: itemId, info: null, loading: true };
		jeiMachine = null;
		const params = new URLSearchParams({ item_id: itemId });
		params.append('mod_version_id', activeModVersionId);
		const r = await fetch(`${BASE}/item-info?${params}`);
		if (r.ok) {
			jeiItem = { id: itemId, info: await r.json(), loading: false };
		} else {
			jeiItem = { id: itemId, info: null, loading: false };
		}
	}

	async function openMachineJei(machine: MachineIdentity) {
		if (jeiMachine?.id === machine.id) { jeiMachine = null; return; }
		if (!activeModVersionId) return;
		jeiMachine = { id: machine.id, name: machine.name, recipes: null, loading: true };
		jeiItem = null;
		const params = new URLSearchParams();
		params.append('mod_version_id', activeModVersionId);
		const r = await fetch(`${BASE}/machines/${machine.id}/recipes?${params}`);
		if (r.ok) {
			jeiMachine = { id: machine.id, name: machine.name, recipes: await r.json(), loading: false };
		} else {
			jeiMachine = { id: machine.id, name: machine.name, recipes: [], loading: false };
		}
	}

	onMount(async () => {
		const id = $page.params.id;
		try {
			const [modRes, vRes, mRes] = await Promise.all([
				fetch(`${BASE}/mods/${id}`),
				fetch(`${BASE}/mods/${id}/versions`),
				fetch(`${BASE}/mods/${id}/machines`),
			]);
			if (!modRes.ok) { error = 'Mod not found.'; return; }
			mod = await modRes.json();
			if (vRes.ok) versions = await vRes.json();
			if (mRes.ok) machines = await mRes.json();
			const latestPublished = versions.find((v) => v.status === 'published') ?? versions[0];
			if (latestPublished) activeModVersionId = latestPublished.id;
			if (activeModVersionId) await loadItems(0);
		} catch {
			error = 'Failed to load mod.';
		} finally {
			loading = false;
		}
	});
</script>

{#snippet recipePanel(recipe: RecipeInfo)}
	<div class="border rounded-md p-3 space-y-1 text-sm bg-muted/30">
		<p class="font-medium">{recipe.machine_name} <span class="text-xs text-muted-foreground font-mono">({recipe.recipe_type})</span></p>
		<div class="flex gap-6 flex-wrap">
			<div>
				<p class="text-xs text-muted-foreground mb-0.5">Inputs</p>
				{#each recipe.inputs as s}
					<p>{s.quantity}× {s.item_id ?? s.tag_string}</p>
				{/each}
				{#each recipe.aux_inputs as s}
					<p class="text-muted-foreground">{s.quantity}× {s.fluid_label ?? s.item_id} (fluid)</p>
				{/each}
			</div>
			<div>
				<p class="text-xs text-muted-foreground mb-0.5">Outputs</p>
				{#each recipe.outputs as s}
					<p>{s.quantity}× {s.item_id}</p>
				{/each}
			</div>
		</div>
	</div>
{/snippet}

<main class="mx-auto max-w-4xl px-4 py-10 space-y-8">
	{#if loading}
		<p class="text-muted-foreground">Loading…</p>
	{:else if error}
		<p class="text-destructive">{error}</p>
	{:else if mod}
		<!-- Header -->
		<div class="flex items-start gap-4">
			{#if mod.icon_url}
				<img src={mod.icon_url} alt={mod.name} class="w-16 h-16 rounded object-cover flex-shrink-0" />
			{:else}
				<div class="w-16 h-16 rounded bg-muted flex items-center justify-center text-2xl font-bold text-muted-foreground flex-shrink-0">
					{mod.name[0].toUpperCase()}
				</div>
			{/if}
			<div class="flex-1 min-w-0">
				<h1 class="text-2xl font-bold">{mod.name}</h1>
				<p class="text-muted-foreground text-sm font-mono">namespace: {mod.namespace}</p>
				{#if mod.description}
					<p class="mt-1 text-sm">{mod.description}</p>
				{/if}
				{#if mod.source_url}
					<a href={mod.source_url} target="_blank" rel="noopener noreferrer"
						class="text-xs text-primary hover:underline mt-1 inline-block">Source ↗</a>
				{/if}
			</div>
			<a href="/?scope=mods&mod_id={mod.id}">
				<Button>Search chains</Button>
			</a>
		</div>

		<!-- Versions — clickable to switch scope -->
		<section class="space-y-2">
			<h2 class="text-lg font-semibold">Versions</h2>
			<div class="flex flex-wrap gap-2">
				{#each versions as v}
					<button
						onclick={() => { activeModVersionId = v.id; jeiItem = null; jeiMachine = null; loadItems(0, itemSearchDebounced); }}
						class="text-xs px-2 py-1 rounded-full border transition-colors {activeModVersionId === v.id
							? 'bg-primary text-primary-foreground border-primary'
							: v.status === 'published'
								? 'bg-green-100 text-green-800 dark:bg-green-900/20 dark:text-green-400 border-green-200 dark:border-green-800 hover:border-foreground/40'
								: 'bg-muted text-muted-foreground hover:border-foreground/40'}"
					>{v.version}</button>
				{/each}
			</div>
		</section>

		<!-- Machines -->
		{#if machines.length > 0}
			<section class="space-y-3">
				<div class="flex items-center justify-between gap-4">
					<h2 class="text-lg font-semibold">Machines ({machines.length})</h2>
					<Input placeholder="Search machines…" value={machineSearch}
						oninput={(e) => (machineSearch = (e.target as HTMLInputElement).value)}
						class="max-w-xs" />
				</div>
				<p class="text-xs text-muted-foreground">Click a machine to see its recipes.</p>
				<div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
					{#each filteredMachines as m}
						<button
							onclick={() => openMachineJei(m)}
							class="flex items-center gap-2 p-2 border rounded-md text-left hover:border-primary/60 transition-colors {jeiMachine?.id === m.id ? 'border-primary bg-primary/5' : ''}"
						>
							{#if m.icon_url}
								<img src={m.icon_url} alt={m.name} class="w-8 h-8 rounded flex-shrink-0" />
							{:else}
								<div class="w-8 h-8 rounded bg-muted flex items-center justify-center text-xs font-bold text-muted-foreground flex-shrink-0">
									{m.name[0].toUpperCase()}
								</div>
							{/if}
							<span class="text-sm truncate">{m.name}</span>
						</button>
					{/each}
				</div>

				{#if jeiMachine}
					<div class="border rounded-lg p-4 space-y-3 bg-card">
						<div class="flex items-center justify-between">
							<h3 class="font-semibold">{jeiMachine.name} — all recipes</h3>
							<button onclick={() => (jeiMachine = null)} class="text-muted-foreground hover:text-foreground text-xs">✕ close</button>
						</div>
						{#if jeiMachine.loading}
							<p class="text-muted-foreground text-sm">Loading…</p>
						{:else if !jeiMachine.recipes || jeiMachine.recipes.length === 0}
							<p class="text-muted-foreground text-sm">No recipes found for this version.</p>
						{:else}
							<p class="text-xs text-muted-foreground">{jeiMachine.recipes.length} recipe{jeiMachine.recipes.length === 1 ? '' : 's'}</p>
							<div class="space-y-2 max-h-96 overflow-y-auto">
								{#each jeiMachine.recipes as r}
									{@render recipePanel(r)}
								{/each}
							</div>
						{/if}
					</div>
				{/if}
			</section>
		{/if}

		<!-- Items -->
		<section class="space-y-2">
			<div class="flex items-center justify-between gap-4">
				<h2 class="text-lg font-semibold">Items{items.length > 0 ? ` (${itemsOffset}${hasMoreItems ? '+' : ''})` : ''}</h2>
				<Input placeholder="Search items…" value={itemSearch}
					oninput={(e) => onItemSearchInput((e.target as HTMLInputElement).value)}
					class="max-w-xs" />
			</div>
			<p class="text-xs text-muted-foreground">Click an item to see its recipes and usages.</p>
			{#if items.length === 0 && !itemsLoading}
				<p class="text-muted-foreground text-sm">No items in this version.</p>
			{:else}
				<Table.Root>
					<Table.Header>
						<Table.Row>
							<Table.Head>Item ID</Table.Head>
							<Table.Head>Canonical Name</Table.Head>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{#each items as entry}
							<Table.Row
								class="cursor-pointer hover:bg-muted/50 {jeiItem?.id === entry.appearance.item_id ? 'bg-primary/5' : ''}"
								onclick={() => openItemJei(entry.appearance.item_id)}
							>
								<Table.Cell class="font-mono text-sm">
									<div class="flex items-center gap-2">
										{#if entry.identity.icon_url}
											<img src={entry.identity.icon_url} alt={entry.identity.canonical_name}
												class="w-5 h-5 rounded flex-shrink-0" />
										{:else}
											<div class="w-5 h-5 rounded bg-muted flex items-center justify-center text-[10px] text-muted-foreground flex-shrink-0">
												{entry.appearance.namespace[0].toUpperCase()}
											</div>
										{/if}
										{entry.appearance.item_id}
									</div>
								</Table.Cell>
								<Table.Cell class="text-sm text-muted-foreground">
									{entry.identity.canonical_name}
								</Table.Cell>
							</Table.Row>

							{#if jeiItem?.id === entry.appearance.item_id}
								<Table.Row>
									<Table.Cell colspan={2} class="p-0 border-t-0">
										<div class="p-4 bg-card space-y-4">
											<div class="flex items-center justify-between">
												<h3 class="font-semibold text-sm font-mono">{entry.appearance.item_id}</h3>
												<button onclick={(e) => { e.stopPropagation(); jeiItem = null; }}
													class="text-muted-foreground hover:text-foreground text-xs">✕ close</button>
											</div>
											{#if jeiItem.loading}
												<p class="text-muted-foreground text-sm">Loading…</p>
											{:else if jeiItem.info}
												<div class="grid gap-4 sm:grid-cols-2">
													<div class="space-y-2">
														<p class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
															Recipes — produces this ({jeiItem.info.produces.length})
														</p>
														{#if jeiItem.info.produces.length === 0}
															<p class="text-muted-foreground text-xs">Nothing produces this item.</p>
														{:else}
															<div class="space-y-2 max-h-72 overflow-y-auto">
																{#each jeiItem.info.produces as r}
																	{@render recipePanel(r)}
																{/each}
															</div>
														{/if}
													</div>
													<div class="space-y-2">
														<p class="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
															Usages — uses this ({jeiItem.info.uses.length})
														</p>
														{#if jeiItem.info.uses.length === 0}
															<p class="text-muted-foreground text-xs">Nothing uses this item.</p>
														{:else}
															<div class="space-y-2 max-h-72 overflow-y-auto">
																{#each jeiItem.info.uses as r}
																	{@render recipePanel(r)}
																{/each}
															</div>
														{/if}
													</div>
												</div>
											{/if}
										</div>
									</Table.Cell>
								</Table.Row>
							{/if}
						{/each}
					</Table.Body>
				</Table.Root>
				{#if hasMoreItems}
					<Button variant="outline" onclick={() => loadItems(itemsOffset, itemSearchDebounced)} disabled={itemsLoading}>
						{itemsLoading ? 'Loading…' : 'Load more'}
					</Button>
				{/if}
			{/if}
		</section>
	{/if}
</main>
