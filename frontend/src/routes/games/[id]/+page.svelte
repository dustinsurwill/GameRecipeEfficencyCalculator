<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import * as Card from '$lib/components/ui/card';
	import * as Table from '$lib/components/ui/table';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import type { Game, GameVersion, Mod, ModPackIdentity, MachineIdentity, ItemIdentityRead } from '$lib/types';

	const BASE = import.meta.env.VITE_API_BASE as string;
	const ITEM_LIMIT = 50;

	let game = $state<Game | null>(null);
	let versions = $state<GameVersion[]>([]);
	let modpacks = $state<ModPackIdentity[]>([]);
	let mods = $state<Mod[]>([]);
	let machines = $state<MachineIdentity[]>([]);
	let items = $state<ItemIdentityRead[]>([]);
	let itemsOffset = $state(0);
	let hasMoreItems = $state(true);
	let itemsLoading = $state(false);
	let loading = $state(true);
	let error = $state('');

	let activeTab = $state<'modpacks' | 'mods' | 'machines' | 'items'>('modpacks');
	let modpackSearch = $state('');
	let modSearch = $state('');
	let machineSearch = $state('');
	let itemSearch = $state('');
	let itemSearchDebounced = $state('');
	let debounceTimer: ReturnType<typeof setTimeout>;

	let filteredModpacks = $derived(
		modpackSearch
			? modpacks.filter(
					(mp) =>
						mp.name.toLowerCase().includes(modpackSearch.toLowerCase()) ||
						mp.slug.toLowerCase().includes(modpackSearch.toLowerCase()) ||
						(mp.description ?? '').toLowerCase().includes(modpackSearch.toLowerCase())
			  )
			: modpacks
	);

	let filteredMods = $derived(
		modSearch
			? mods.filter(
					(m) =>
						m.name.toLowerCase().includes(modSearch.toLowerCase()) ||
						m.namespace.toLowerCase().includes(modSearch.toLowerCase())
			  )
			: mods
	);

	let filteredMachines = $derived(
		machineSearch
			? machines.filter((m) => m.name.toLowerCase().includes(machineSearch.toLowerCase()))
			: machines
	);

	function onItemSearchInput(val: string) {
		itemSearch = val;
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(() => {
			itemSearchDebounced = val;
		}, 350);
	}

	async function loadItems(offset: number, searchTerm: string) {
		const id = $page.params.id;
		itemsLoading = true;
		try {
			const params = new URLSearchParams({
				limit: String(ITEM_LIMIT),
				offset: String(offset),
			});
			if (searchTerm) params.set('search', searchTerm);
			const r = await fetch(`${BASE}/games/${id}/items?${params}`);
			if (r.ok) {
				const batch: ItemIdentityRead[] = await r.json();
				items = offset === 0 ? batch : [...items, ...batch];
				hasMoreItems = batch.length === ITEM_LIMIT;
				itemsOffset = offset + batch.length;
			}
		} finally {
			itemsLoading = false;
		}
	}

	$effect(() => {
		const term = itemSearchDebounced;
		if (activeTab === 'items') {
			loadItems(0, term);
		}
	});

	onMount(async () => {
		const id = $page.params.id;
		try {
			const [gRes, vRes, mpRes, mRes, machRes] = await Promise.all([
				fetch(`${BASE}/games/${id}`),
				fetch(`${BASE}/games/${id}/versions`),
				fetch(`${BASE}/modpacks?game_id=${id}`),
				fetch(`${BASE}/mods?game_id=${id}`),
				fetch(`${BASE}/games/${id}/machines`),
			]);
			if (!gRes.ok) { error = 'Game not found.'; return; }
			game = await gRes.json();
			if (vRes.ok) versions = await vRes.json();
			if (mpRes.ok) modpacks = await mpRes.json();
			if (mRes.ok) mods = await mRes.json();
			if (machRes.ok) machines = await machRes.json();
		} catch {
			error = 'Failed to load game.';
		} finally {
			loading = false;
		}
	});
</script>

<main class="mx-auto max-w-5xl px-4 py-10 space-y-8">
	{#if loading}
		<p class="text-muted-foreground">Loading…</p>
	{:else if error}
		<p class="text-destructive">{error}</p>
	{:else if game}
		<!-- Header -->
		<div class="flex items-start gap-4">
			{#if game.icon_url}
				<img src={game.icon_url} alt={game.name} class="w-16 h-16 rounded object-cover flex-shrink-0" />
			{:else}
				<div class="w-16 h-16 rounded bg-muted flex items-center justify-center text-2xl font-bold text-muted-foreground flex-shrink-0">
					{game.name[0].toUpperCase()}
				</div>
			{/if}
			<div class="flex-1 min-w-0">
				<h1 class="text-2xl font-bold">{game.name}</h1>
				<p class="text-muted-foreground text-sm font-mono">{game.slug}</p>
				{#if versions.length > 0}
					<p class="text-xs text-muted-foreground mt-1">
						Versions: {versions.map((v) => v.version).join(', ')}
					</p>
				{/if}
			</div>
			<a href="/?game={game.id}">
				<Button>Search chains</Button>
			</a>
		</div>

		<!-- Tab nav -->
		<div class="flex gap-2 border-b pb-0">
			{#each [['modpacks', `Modpacks (${modpacks.length})`], ['mods', `Mods (${mods.length})`], ['machines', `Machines (${machines.length})`], ['items', 'Items']] as [tab, label]}
				<button
					onclick={() => { activeTab = tab as typeof activeTab; if (tab === 'items' && items.length === 0) loadItems(0, itemSearchDebounced); }}
					class="px-3 py-2 text-sm font-medium border-b-2 transition-colors {activeTab === tab ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}"
				>{label}</button>
			{/each}
		</div>

		<!-- Modpacks tab -->
		{#if activeTab === 'modpacks'}
			<div class="space-y-3">
				<Input placeholder="Search modpacks…" value={modpackSearch} oninput={(e) => (modpackSearch = (e.target as HTMLInputElement).value)} class="max-w-sm" />
				{#if filteredModpacks.length === 0}
					<p class="text-muted-foreground text-sm">No modpacks found.</p>
				{:else}
					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
						{#each filteredModpacks as mp}
							<a href="/modpacks/{mp.id}" class="flex items-center gap-3 p-3 border rounded-lg hover:border-foreground/30 transition-colors">
								{#if mp.icon_url}
									<img src={mp.icon_url} alt={mp.name} class="w-10 h-10 rounded flex-shrink-0" />
								{:else}
									<div class="w-10 h-10 rounded bg-muted flex items-center justify-center text-sm font-bold text-muted-foreground flex-shrink-0">
										{mp.name[0].toUpperCase()}
									</div>
								{/if}
								<div class="min-w-0">
									<p class="font-medium text-sm truncate">{mp.name}</p>
									<p class="text-xs text-muted-foreground font-mono">{mp.slug}</p>
								</div>
							</a>
						{/each}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Mods tab -->
		{#if activeTab === 'mods'}
			<div class="space-y-3">
				<Input placeholder="Search mods…" value={modSearch} oninput={(e) => (modSearch = (e.target as HTMLInputElement).value)} class="max-w-sm" />
				{#if filteredMods.length === 0}
					<p class="text-muted-foreground text-sm">No mods found.</p>
				{:else}
					<div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
						{#each filteredMods as mod}
							<a href="/mods/{mod.id}" class="flex items-center gap-2 p-2 border rounded-md hover:border-foreground/30 transition-colors">
								{#if mod.icon_url}
									<img src={mod.icon_url} alt={mod.name} class="w-8 h-8 rounded flex-shrink-0" />
								{:else}
									<div class="w-8 h-8 rounded bg-muted flex items-center justify-center text-xs font-bold text-muted-foreground flex-shrink-0">
										{mod.name[0].toUpperCase()}
									</div>
								{/if}
								<div class="min-w-0">
									<p class="text-sm font-medium truncate">{mod.name}</p>
									<p class="text-xs text-muted-foreground font-mono truncate">{mod.namespace}</p>
								</div>
							</a>
						{/each}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Machines tab -->
		{#if activeTab === 'machines'}
			<div class="space-y-3">
				<Input placeholder="Search machines…" value={machineSearch} oninput={(e) => (machineSearch = (e.target as HTMLInputElement).value)} class="max-w-sm" />
				{#if filteredMachines.length === 0}
					<p class="text-muted-foreground text-sm">No machines found.</p>
				{:else}
					<div class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
						{#each filteredMachines as m}
							<div class="flex items-center gap-2 p-2 border rounded-md">
								{#if m.icon_url}
									<img src={m.icon_url} alt={m.name} class="w-8 h-8 rounded flex-shrink-0" />
								{:else}
									<div class="w-8 h-8 rounded bg-muted flex items-center justify-center text-xs font-bold text-muted-foreground flex-shrink-0">
										{m.name[0].toUpperCase()}
									</div>
								{/if}
								<span class="text-sm truncate">{m.name}</span>
							</div>
						{/each}
					</div>
				{/if}
			</div>
		{/if}

		<!-- Items tab -->
		{#if activeTab === 'items'}
			<div class="space-y-3">
				<Input
					placeholder="Search items…"
					value={itemSearch}
					oninput={(e) => onItemSearchInput((e.target as HTMLInputElement).value)}
					class="max-w-sm"
				/>
				{#if itemsLoading && items.length === 0}
					<p class="text-muted-foreground text-sm">Loading…</p>
				{:else if items.length === 0}
					<p class="text-muted-foreground text-sm">No items found.</p>
				{:else}
					<Table.Root>
						<Table.Header>
							<Table.Row>
								<Table.Head>Icon</Table.Head>
								<Table.Head>Canonical Name</Table.Head>
								<Table.Head>Namespace</Table.Head>
							</Table.Row>
						</Table.Header>
						<Table.Body>
							{#each items as item}
								<Table.Row>
									<Table.Cell>
										{#if item.icon_url}
											<img src={item.icon_url} alt={item.canonical_name} class="w-6 h-6 rounded" />
										{:else}
											<div class="w-6 h-6 rounded bg-muted flex items-center justify-center text-[10px] text-muted-foreground">
												{item.canonical_name[0]?.toUpperCase() ?? '?'}
											</div>
										{/if}
									</Table.Cell>
									<Table.Cell class="font-medium text-sm">{item.canonical_name}</Table.Cell>
									<Table.Cell class="text-sm text-muted-foreground font-mono">{item.canonical_namespace ?? '—'}</Table.Cell>
								</Table.Row>
							{/each}
						</Table.Body>
					</Table.Root>
					{#if hasMoreItems}
						<Button variant="outline" onclick={() => loadItems(itemsOffset, itemSearchDebounced)} disabled={itemsLoading}>
							{itemsLoading ? 'Loading…' : 'Load more'}
						</Button>
					{/if}
				{/if}
			</div>
		{/if}
	{/if}
</main>
