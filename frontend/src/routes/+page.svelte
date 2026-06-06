<script lang="ts">
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import * as Card from '$lib/components/ui/card';
	import * as Table from '$lib/components/ui/table';
	import type { Game, Mod, ModVersion, ModPackIdentity, ChainResponse, Chain } from '$lib/types';

	const BASE = import.meta.env.VITE_API_BASE as string;

	// ── Catalog state ─────────────────────────────────────────────────────────
	let games = $state<Game[]>([]);
	let modpacks = $state<ModPackIdentity[]>([]);
	let catalogSearch = $state('');
	let catalogLoading = $state(true);

	// ── Search form state ─────────────────────────────────────────────────────
	let scopeType = $state<'modpack' | 'mods' | 'game'>('modpack');

	// Modpack scope — identity UUID; backend picks latest version
	let selectedModpackId = $state('');

	// Game/mods scope
	let selectedGameId = $state('');
	let gameMods = $state<Mod[]>([]);
	let modVersionMap = $state<Record<string, ModVersion[]>>({});
	// values are version strings (e.g. "10.7.18.84"), not UUIDs
	let selectedModVersions = $state<Record<string, string>>({});
	let modsLoading = $state(false);

	// Start / target
	let startInput = $state('');
	let targetInput = $state('');
	let useTag = $state(true);

	// Results
	let results = $state<ChainResponse | null>(null);
	let searchError = $state('');
	let loading = $state(false);

	// ── Derived ───────────────────────────────────────────────────────────────
	let filteredModpacks = $derived(
		catalogSearch
			? modpacks.filter(
					(mp) =>
						mp.name.toLowerCase().includes(catalogSearch.toLowerCase()) ||
						mp.slug.toLowerCase().includes(catalogSearch.toLowerCase())
			  )
			: modpacks
	);

	let filteredGames = $derived(
		catalogSearch
			? games.filter(
					(g) =>
						g.name.toLowerCase().includes(catalogSearch.toLowerCase()) ||
						g.slug.toLowerCase().includes(catalogSearch.toLowerCase())
			  )
			: games
	);

	onMount(async () => {
		try {
			const [gRes, mpRes] = await Promise.all([
				fetch(`${BASE}/games`),
				fetch(`${BASE}/modpacks`),
			]);
			if (gRes.ok) games = await gRes.json();
			if (mpRes.ok) modpacks = await mpRes.json();
		} finally {
			catalogLoading = false;
		}
	});

	// Load mods + their versions whenever game selection changes
	$effect(() => {
		const gameId = selectedGameId;
		if (!gameId) {
			gameMods = [];
			modVersionMap = {};
			selectedModVersions = {};
			return;
		}
		modsLoading = true;
		fetch(`${BASE}/mods?game_id=${gameId}`)
			.then((r) => (r.ok ? r.json() : []))
			.then(async (mods: Mod[]) => {
				if (selectedGameId !== gameId) return;
				gameMods = mods;
				const fetched = await Promise.all(
					mods.map(async (m) => {
						const r = await fetch(`${BASE}/mods/${m.id}/versions`);
						const versions: ModVersion[] = r.ok ? await r.json() : [];
						const latest = versions.find((v) => v.status === 'published') ?? versions[0];
						return { modId: m.id, versions, defaultVer: latest?.version ?? '' };
					})
				);
				if (selectedGameId !== gameId) return;
				const vmap: Record<string, ModVersion[]> = {};
				const smap: Record<string, string> = {};
				for (const e of fetched) {
					vmap[e.modId] = e.versions;
					smap[e.modId] = e.defaultVer;
				}
				modVersionMap = vmap;
				selectedModVersions = smap;
			})
			.finally(() => {
				if (selectedGameId === gameId) modsLoading = false;
			});
	});

	function prefillModpack(mp: ModPackIdentity) {
		scopeType = 'modpack';
		selectedModpackId = mp.id;
		document.getElementById('chain-search')?.scrollIntoView({ behavior: 'smooth' });
	}

	function prefillGame(g: Game) {
		scopeType = 'game';
		selectedGameId = g.id;
		document.getElementById('chain-search')?.scrollIntoView({ behavior: 'smooth' });
	}

	function toggleMod(mod: Mod, checked: boolean) {
		const copy = { ...selectedModVersions };
		if (!checked) {
			delete copy[mod.id];
		} else {
			const versions = modVersionMap[mod.id] ?? [];
			const latest = versions.find((v) => v.status === 'published') ?? versions[0];
			copy[mod.id] = latest?.version ?? '';
		}
		selectedModVersions = copy;
	}

	async function search(e: Event) {
		e.preventDefault();
		searchError = '';
		results = null;
		loading = true;
		try {
			const params = new URLSearchParams();

			if (scopeType === 'modpack' && selectedModpackId) {
				params.set('modpack', selectedModpackId);
			} else if (scopeType === 'mods') {
				const entries = Object.entries(selectedModVersions).filter(([, ver]) => ver);
				if (entries.length === 0) {
					searchError = 'Select at least one mod.';
					return;
				}
				for (const [modId, ver] of entries) {
					params.append('mod', ver ? `${modId}@${ver}` : modId);
				}
				if (selectedGameId) params.set('game', selectedGameId);
			} else if (scopeType === 'game' && selectedGameId) {
				params.set('game', selectedGameId);
			} else {
				searchError = 'Select a scope (modpack, mods, or game).';
				return;
			}

			const target = useTag ? `#${targetInput}` : targetInput;
			params.set('target', target);
			if (startInput) {
				const start = useTag ? `#${startInput}` : startInput;
				params.set('start', start);
			}

			const res = await fetch(`${BASE}/chains?${params}`);
			if (res.status === 429) {
				searchError = 'Too many requests — please wait a moment and try again.';
				return;
			}
			if (!res.ok) {
				const data = await res.json().catch(() => ({}));
				searchError = data.detail ?? `Error ${res.status}`;
				return;
			}
			const data: ChainResponse = await res.json();
			results = data;
			if (data.total_found === 0) searchError = data.note ?? 'No chains found.';
		} catch {
			searchError = 'Network error — could not reach the server.';
		} finally {
			loading = false;
		}
	}

	function scopeLabel(chain: ChainResponse): string {
		const s = chain.scope;
		if (s.type === 'modpack') return `${s.modpack_name} v${s.modpack_version}`;
		if (s.type === 'mods') return `${s.mods.length} mod${s.mods.length === 1 ? '' : 's'}`;
		return 'All game recipes';
	}
</script>

<main class="mx-auto max-w-5xl px-4 py-10 space-y-12">

	<!-- Hero -->
	<div class="space-y-1">
		<h1 class="text-3xl font-bold tracking-tight">RecipeBook</h1>
		<p class="text-muted-foreground">
			Find every multi-step processing chain between two items, ranked by yield per input.
		</p>
	</div>

	<!-- Browse games & modpacks -->
	<section class="space-y-4">
		<div class="flex items-center justify-between gap-4">
			<h2 class="text-xl font-semibold shrink-0">Browse</h2>
			<Input placeholder="Search games & modpacks…" bind:value={catalogSearch} class="max-w-xs" />
		</div>

		{#if catalogLoading}
			<p class="text-muted-foreground text-sm">Loading catalog…</p>
		{:else if filteredGames.length === 0 && filteredModpacks.length === 0}
			<p class="text-muted-foreground text-sm">{catalogSearch ? `No results for "${catalogSearch}"` : 'No published content yet.'}</p>
		{:else}
			{#each filteredGames as game}
				<div class="space-y-3">
					<div class="flex items-center gap-3">
						{#if game.icon_url}
							<img src={game.icon_url} alt={game.name} class="w-7 h-7 rounded" />
						{:else}
							<div class="w-7 h-7 rounded bg-muted flex items-center justify-center text-xs font-bold text-muted-foreground">
								{game.name[0]}
							</div>
						{/if}
						<a href="/games/{game.id}" class="font-semibold hover:underline">{game.name}</a>
						<button
							onclick={() => prefillGame(game)}
							class="text-muted-foreground hover:text-foreground text-xs underline"
						>Search all</button>
					</div>

					{#if filteredModpacks.length > 0}
						<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
							{#each filteredModpacks as mp}
								<div class="border rounded-lg hover:border-foreground/30 transition-colors overflow-hidden">
									<a href="/modpacks/{mp.id}" class="block p-3">
										<div class="flex items-center gap-2">
											{#if mp.icon_url}
												<img src={mp.icon_url} alt={mp.name} class="w-8 h-8 rounded" />
											{:else}
												<div class="w-8 h-8 rounded bg-muted flex items-center justify-center text-sm font-bold text-muted-foreground">
													{mp.name[0]}
												</div>
											{/if}
											<div>
												<p class="text-sm font-medium leading-tight">{mp.name}</p>
												<p class="text-xs text-muted-foreground font-mono">{mp.slug}</p>
											</div>
										</div>
										{#if mp.description}
											<p class="text-muted-foreground text-xs line-clamp-2 mt-2">{mp.description}</p>
										{/if}
									</a>
									<div class="px-3 pb-2">
										<button
											onclick={() => prefillModpack(mp)}
											class="text-xs text-primary hover:underline"
										>Search chains →</button>
									</div>
								</div>
							{/each}
						</div>
					{:else if !catalogSearch}
						<p class="text-muted-foreground text-sm pl-10">No published modpacks yet.</p>
					{/if}
				</div>
			{/each}
		{/if}
	</section>

	<!-- Chain search -->
	<section id="chain-search" class="space-y-4 scroll-mt-6">
		<h2 class="text-xl font-semibold">Chain Finder</h2>

		<Card.Root>
			<Card.Content class="pt-6 space-y-4">
				<form onsubmit={search} class="space-y-4">

					<!-- Scope selector -->
					<div class="space-y-3">
						<Label>Scope</Label>
						<div class="flex gap-2">
							{#each [['modpack', 'Modpack'], ['mods', 'Pick Mods'], ['game', 'All Game']] as [val, label]}
								<button
									type="button"
									onclick={() => (scopeType = val as typeof scopeType)}
									class="px-3 py-1.5 text-sm rounded-md border transition-colors {scopeType === val ? 'bg-primary text-primary-foreground' : 'bg-background hover:bg-muted'}"
								>{label}</button>
							{/each}
						</div>

						{#if scopeType === 'modpack'}
							<div class="space-y-1">
								<Label for="modpack-select">Modpack</Label>
								<select
									id="modpack-select"
									bind:value={selectedModpackId}
									class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
								>
									<option value="">Select a modpack…</option>
									{#each modpacks as mp}
										<option value={mp.id}>{mp.name}</option>
									{/each}
								</select>
								<p class="text-xs text-muted-foreground">Latest published version will be used automatically.</p>
							</div>
						{:else}
							<div class="space-y-1">
								<Label for="game-select">Game</Label>
								<select
									id="game-select"
									bind:value={selectedGameId}
									class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
								>
									<option value="">Select a game…</option>
									{#each games as g}
										<option value={g.id}>{g.name}</option>
									{/each}
								</select>
							</div>

							{#if scopeType === 'mods' && selectedGameId}
								{#if modsLoading}
									<p class="text-muted-foreground text-sm">Loading mods…</p>
								{:else if gameMods.length === 0}
									<p class="text-muted-foreground text-sm">No mods found for this game.</p>
								{:else}
									<div class="space-y-1">
										<Label>Mods</Label>
										<div class="grid gap-1 sm:grid-cols-2 max-h-64 overflow-y-auto border rounded-md p-2">
											{#each gameMods as mod}
												{@const versions = modVersionMap[mod.id] ?? []}
												{@const isChecked = mod.id in selectedModVersions}
												<div class="flex items-center gap-2 py-0.5">
													<input
														type="checkbox"
														id="mod-{mod.id}"
														checked={isChecked}
														onchange={(e) => toggleMod(mod, (e.target as HTMLInputElement).checked)}
														class="rounded flex-shrink-0"
													/>
													<label for="mod-{mod.id}" class="text-sm flex-1 truncate min-w-0 cursor-pointer">
														{mod.name}
													</label>
													{#if isChecked && versions.length > 1}
														<select
															bind:value={selectedModVersions[mod.id]}
															class="text-xs border rounded px-1 py-0.5 max-w-[90px] flex-shrink-0"
														>
															{#each versions as v}
																<option value={v.version}>{v.version}</option>
															{/each}
														</select>
													{:else if isChecked && versions.length === 1}
														<span class="text-xs text-muted-foreground flex-shrink-0">{versions[0]?.version}</span>
													{/if}
												</div>
											{/each}
										</div>
										<p class="text-xs text-muted-foreground">
											{Object.keys(selectedModVersions).length} of {gameMods.length} selected
										</p>
									</div>
								{/if}
							{/if}
						{/if}
					</div>

					<!-- Input type toggle -->
					<div class="flex items-center gap-3 text-sm">
						<span class="text-muted-foreground">Search by</span>
						{#each [[true, 'Tag'], [false, 'Item ID']] as [val, label]}
							<button
								type="button"
								onclick={() => (useTag = val as boolean)}
								class="px-2.5 py-1 rounded-md border transition-colors {useTag === val ? 'bg-primary text-primary-foreground' : 'bg-background hover:bg-muted'}"
							>{label}</button>
						{/each}
					</div>

					<!-- Start / target + submit -->
					<div class="grid gap-4 sm:grid-cols-[1fr_1fr_auto]">
						<div class="space-y-1">
							<Label for="start">Start {useTag ? 'tag' : 'item'} <span class="text-muted-foreground text-xs">(optional — omit for backward search)</span></Label>
							<Input
								id="start"
								placeholder={useTag ? 'e.g. c:ores/gold' : 'e.g. minecraft:raw_gold'}
								bind:value={startInput}
							/>
						</div>
						<div class="space-y-1">
							<Label for="target">Target {useTag ? 'tag' : 'item'}</Label>
							<Input
								id="target"
								placeholder={useTag ? 'e.g. c:ingots/gold' : 'e.g. mekanism:ingot_gold'}
								bind:value={targetInput}
								required
							/>
						</div>
						<div class="flex items-end">
							<Button type="submit" disabled={loading} class="w-full sm:w-auto">
								{loading ? 'Searching…' : 'Find chains'}
							</Button>
						</div>
					</div>
				</form>
			</Card.Content>
		</Card.Root>

		{#if searchError}
			<p class="text-destructive text-sm">{searchError}</p>
		{/if}

		{#if results && results.total_found > 0}
			<p class="text-muted-foreground text-sm">
				{results.total_found} chain{results.total_found === 1 ? '' : 's'} ·
				{results.direction === 'forward' ? `${results.start} → ${results.target}` : `← ${results.target}`}
				· {scopeLabel(results)}
				{#if results.cycles_excluded > 0}
					· {results.cycles_excluded} cyclic chain{results.cycles_excluded === 1 ? '' : 's'} excluded
				{/if}
			</p>

			{#each results.chains as chain}
				<Card.Root>
					<Card.Header>
						<Card.Title class="text-base">
							Chain {chain.rank}
							{#if chain.yield_per_start_item != null}
								— {chain.yield_per_start_item.toFixed(3)}× yield per input
							{:else if chain.raw_materials_per_target != null}
								— {chain.raw_materials_per_target.toFixed(3)} raw per target
							{/if}
							{#if chain.has_cycle}
								<span class="ml-2 text-xs text-yellow-600 dark:text-yellow-400">(cyclic)</span>
							{/if}
						</Card.Title>
						<Card.Description>
							{chain.steps.length} step{chain.steps.length === 1 ? '' : 's'} ·
							{chain.machines_required.map((m) => m.name).join(' → ')}
							{#if chain.chemicals_required.length > 0}
								· chemicals: {chain.chemicals_required.join(', ')}
							{/if}
						</Card.Description>
					</Card.Header>
					<Card.Content>
						<!-- Raw inputs / final outputs summary -->
						{#if chain.raw_inputs.length > 0 || chain.final_outputs.length > 0}
							<div class="flex gap-6 mb-3 text-sm">
								{#if chain.raw_inputs.length > 0}
									<div>
										<p class="text-xs text-muted-foreground mb-1">Raw inputs</p>
										{#each chain.raw_inputs as inp}
											<p>{inp.quantity.toFixed(3)}× {inp.item_id ?? inp.tag ?? '?'}</p>
										{/each}
									</div>
								{/if}
								{#if chain.final_outputs.length > 0}
									<div>
										<p class="text-xs text-muted-foreground mb-1">Final outputs</p>
										{#each chain.final_outputs as out}
											<p>{out.quantity.toFixed(3)}× {out.canonical_name ?? out.item_id}</p>
										{/each}
									</div>
								{/if}
							</div>
						{/if}

						<Table.Root>
							<Table.Header>
								<Table.Row>
									<Table.Head class="w-8">#</Table.Head>
									<Table.Head>Machine</Table.Head>
									<Table.Head>Inputs</Table.Head>
									<Table.Head>Outputs</Table.Head>
								</Table.Row>
							</Table.Header>
							<Table.Body>
								{#each chain.steps as step}
									<Table.Row>
										<Table.Cell class="text-muted-foreground">{step.step}</Table.Cell>
										<Table.Cell class="font-medium">
											<div>{step.machine.name}</div>
											<div class="text-xs text-muted-foreground font-mono">{step.machine.recipe_type}</div>
										</Table.Cell>
										<Table.Cell class="text-sm">
											{#each step.inputs as inp}
												<div>{inp.quantity.toFixed(2)}× {inp.item_id ?? inp.tag ?? '?'}</div>
											{/each}
											{#each step.aux_inputs as aux}
												<div class="text-muted-foreground">{aux.quantity.toFixed(2)}× {aux.item_id ?? '?'} (fluid)</div>
											{/each}
										</Table.Cell>
										<Table.Cell class="text-sm">
											{#each step.outputs as out}
												<div>{out.quantity.toFixed(2)}× {out.canonical_name ?? out.item_id ?? '?'}</div>
											{/each}
										</Table.Cell>
									</Table.Row>
								{/each}
							</Table.Body>
						</Table.Root>
					</Card.Content>
				</Card.Root>
			{/each}
		{/if}
	</section>

</main>
