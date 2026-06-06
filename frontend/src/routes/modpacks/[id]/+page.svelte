<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import * as Table from '$lib/components/ui/table';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import type { ModPack, ModPackModEntry, MachineIdentity, ItemAppearanceWithIdentity } from '$lib/types';

	const BASE = import.meta.env.VITE_API_BASE as string;
	const ITEM_LIMIT = 50;

	let modpack = $state<ModPack | null>(null);
	let mods = $state<ModPackModEntry[]>([]);
	let machines = $state<MachineIdentity[]>([]);
	let items = $state<ItemAppearanceWithIdentity[]>([]);
	let itemsOffset = $state(0);
	let hasMoreItems = $state(true);
	let itemsLoading = $state(false);
	let loading = $state(true);
	let error = $state('');

	let activeTab = $state<'mods' | 'machines' | 'items'>('mods');
	let modSearch = $state('');
	let machineSearch = $state('');
	let itemSearch = $state('');
	let itemSearchDebounced = $state('');
	let debounceTimer: ReturnType<typeof setTimeout>;

	let filteredMods = $derived(
		modSearch
			? mods.filter(
					(e) =>
						e.mod.name.toLowerCase().includes(modSearch.toLowerCase()) ||
						e.mod.namespace.toLowerCase().includes(modSearch.toLowerCase())
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
			const params = new URLSearchParams({ limit: String(ITEM_LIMIT), offset: String(offset) });
			if (searchTerm) params.set('search', searchTerm);
			const r = await fetch(`${BASE}/modpacks/${id}/items?${params}`);
			if (r.ok) {
				const batch: ItemAppearanceWithIdentity[] = await r.json();
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
		if (activeTab === 'items' && modpack) {
			loadItems(0, term);
		}
	});

	onMount(async () => {
		const id = $page.params.id;
		try {
			const [mpRes, modsRes, machRes] = await Promise.all([
				fetch(`${BASE}/modpacks/${id}`),
				fetch(`${BASE}/modpacks/${id}/mods`),
				fetch(`${BASE}/modpacks/${id}/machines`),
			]);
			if (!mpRes.ok) {
				error = 'ModPack not found.';
				return;
			}
			modpack = await mpRes.json();
			if (modsRes.ok) mods = await modsRes.json();
			if (machRes.ok) machines = await machRes.json();
		} catch {
			error = 'Failed to load modpack.';
		} finally {
			loading = false;
		}
	});
</script>

<main class="mx-auto max-w-4xl px-4 py-10 space-y-8">
	{#if loading}
		<p class="text-muted-foreground">Loading…</p>
	{:else if error}
		<p class="text-destructive">{error}</p>
	{:else if modpack}
		<!-- Header -->
		<div class="flex items-start gap-4">
			{#if modpack.icon_url}
				<img src={modpack.icon_url} alt={modpack.name} class="w-16 h-16 rounded object-cover flex-shrink-0" />
			{:else}
				<div class="w-16 h-16 rounded bg-muted flex items-center justify-center text-2xl font-bold text-muted-foreground flex-shrink-0">
					{modpack.name[0].toUpperCase()}
				</div>
			{/if}
			<div class="flex-1 min-w-0">
				<h1 class="text-2xl font-bold">{modpack.name}</h1>
				<p class="text-muted-foreground">v{modpack.version}</p>
				{#if modpack.description}
					<p class="mt-1 text-sm">{modpack.description}</p>
				{/if}
				{#if modpack.source_url}
					<a
						href={modpack.source_url}
						target="_blank"
						rel="noopener noreferrer"
						class="text-xs text-primary hover:underline mt-1 inline-block"
					>Source ↗</a>
				{/if}
			</div>
			<a href="/?modpack_id={modpack.id}">
				<Button>Search chains</Button>
			</a>
		</div>

		<!-- Tab nav -->
		<div class="flex gap-2 border-b">
			{#each [['mods', `Mods (${mods.length})`], ['machines', `Machines (${machines.length})`], ['items', 'Items']] as [tab, label]}
				<button
					onclick={() => { activeTab = tab as typeof activeTab; if (tab === 'items' && items.length === 0) loadItems(0, itemSearchDebounced); }}
					class="px-3 py-2 text-sm font-medium border-b-2 transition-colors {activeTab === tab ? 'border-primary text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground'}"
				>{label}</button>
			{/each}
		</div>

		<!-- Mods tab -->
		{#if activeTab === 'mods'}
			<div class="space-y-3">
				<Input placeholder="Search mods…" value={modSearch} oninput={(e) => (modSearch = (e.target as HTMLInputElement).value)} class="max-w-sm" />
				{#if filteredMods.length === 0}
					<p class="text-muted-foreground text-sm">No mods found.</p>
				{:else}
					<Table.Root>
						<Table.Header>
							<Table.Row>
								<Table.Head>Mod</Table.Head>
								<Table.Head>Namespace</Table.Head>
								<Table.Head>Version</Table.Head>
								<Table.Head></Table.Head>
							</Table.Row>
						</Table.Header>
						<Table.Body>
							{#each filteredMods as entry}
								<Table.Row>
									<Table.Cell>
										<div class="flex items-center gap-2">
											{#if entry.mod.icon_url}
												<img src={entry.mod.icon_url} alt={entry.mod.name} class="w-6 h-6 rounded flex-shrink-0" />
											{:else}
												<div class="w-6 h-6 rounded bg-muted flex items-center justify-center text-xs font-bold text-muted-foreground flex-shrink-0">
													{entry.mod.name[0].toUpperCase()}
												</div>
											{/if}
											<a href="/mods/{entry.mod.id}" class="hover:underline font-medium">
												{entry.mod.name}
											</a>
										</div>
									</Table.Cell>
									<Table.Cell class="text-muted-foreground text-sm font-mono">{entry.mod.namespace}</Table.Cell>
									<Table.Cell class="text-sm">{entry.mod_version.version}</Table.Cell>
									<Table.Cell>
										{#if entry.mod.source_url}
											<a
												href={entry.mod.source_url}
												target="_blank"
												rel="noopener noreferrer"
												class="text-xs text-primary hover:underline"
											>Source ↗</a>
										{/if}
									</Table.Cell>
								</Table.Row>
							{/each}
						</Table.Body>
					</Table.Root>
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
								<Table.Head>Item ID</Table.Head>
								<Table.Head>Canonical Name</Table.Head>
							</Table.Row>
						</Table.Header>
						<Table.Body>
							{#each items as entry}
								<Table.Row>
									<Table.Cell class="font-mono text-sm">
										<div class="flex items-center gap-2">
											{#if entry.identity.icon_url}
												<img
													src={entry.identity.icon_url}
													alt={entry.identity.canonical_name}
													class="w-5 h-5 rounded flex-shrink-0"
												/>
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
