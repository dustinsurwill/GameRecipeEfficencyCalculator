<script lang="ts">
	import { onMount } from 'svelte';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import * as Table from '$lib/components/ui/table';
	import type { Game, ModPack } from '$lib/types';

	const BASE = import.meta.env.VITE_API_BASE as string;

	// ── Browse state ──────────────────────────────────────────────────────────
	let games = $state<Game[]>([]);
	let modpacks = $state<ModPack[]>([]);
	let catalogSearch = $state('');
	let catalogLoading = $state(true);

	// ── Search form state ─────────────────────────────────────────────────────
	let scopeType = $state<'modpack' | 'game'>('modpack');
	let selectedModpackId = $state('');
	let selectedGameId = $state('');
	let startInput = $state('');
	let targetInput = $state('');
	let useTag = $state(true);

	let results = $state<any>(null);
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

	function prefillSearch(mp: ModPack) {
		scopeType = 'modpack';
		selectedModpackId = mp.id;
		document.getElementById('chain-search')?.scrollIntoView({ behavior: 'smooth' });
	}

	function prefillGame(g: Game) {
		scopeType = 'game';
		selectedGameId = g.id;
		document.getElementById('chain-search')?.scrollIntoView({ behavior: 'smooth' });
	}

	async function search(e: Event) {
		e.preventDefault();
		searchError = '';
		results = null;
		loading = true;
		try {
			const params = new URLSearchParams();
			if (scopeType === 'modpack' && selectedModpackId) {
				params.set('modpack_id', selectedModpackId);
			} else if (scopeType === 'game' && selectedGameId) {
				params.set('game_id', selectedGameId);
			}
			if (useTag) {
				params.set('start_tag', startInput);
				params.set('target_tag', targetInput);
			} else {
				params.set('start_item', startInput);
				params.set('target_item', targetInput);
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
			const data = await res.json();
			results = data;
			if (data.chain_count === 0) searchError = data.note ?? 'No chains found.';
		} catch {
			searchError = 'Network error — could not reach the server.';
		} finally {
			loading = false;
		}
	}

	function modpackName(id: string) {
		return modpacks.find((mp) => mp.id === id)?.name ?? id;
	}
	function gameName(id: string) {
		return games.find((g) => g.id === id)?.name ?? id;
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
		<div class="flex items-center justify-between">
			<h2 class="text-xl font-semibold">Browse</h2>
			<Input
				placeholder="Search games & modpacks…"
				bind:value={catalogSearch}
				class="max-w-xs"
			/>
		</div>

		{#if catalogLoading}
			<p class="text-muted-foreground text-sm">Loading catalog…</p>
		{:else if games.length === 0 && modpacks.length === 0}
			<p class="text-muted-foreground text-sm">No published content yet.</p>
		{:else}
			{#each filteredGames as game}
				<div class="space-y-2">
					<div class="flex items-center gap-2">
						<h3 class="font-medium">{game.name}</h3>
						<button
							onclick={() => prefillGame(game)}
							class="text-muted-foreground hover:text-foreground text-xs underline"
						>
							Search all
						</button>
					</div>
					<div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
						{#each filteredModpacks as mp}
							<Card.Root class="cursor-pointer hover:border-foreground/30 transition-colors" onclick={() => prefillSearch(mp)}>
								<Card.Header class="pb-2">
									<Card.Title class="text-base">{mp.name}</Card.Title>
									<Card.Description>v{mp.version}</Card.Description>
								</Card.Header>
								{#if mp.description}
									<Card.Content class="pt-0">
										<p class="text-muted-foreground text-xs line-clamp-2">{mp.description}</p>
									</Card.Content>
								{/if}
							</Card.Root>
						{/each}
						{#if filteredModpacks.length === 0 && catalogSearch}
							<p class="text-muted-foreground text-sm col-span-3">No modpacks match "{catalogSearch}"</p>
						{/if}
					</div>
				</div>
			{/each}
			{#if filteredGames.length === 0 && catalogSearch}
				<p class="text-muted-foreground text-sm">No results match "{catalogSearch}"</p>
			{/if}
		{/if}
	</section>

	<!-- Chain search -->
	<section id="chain-search" class="space-y-4 scroll-mt-6">
		<h2 class="text-xl font-semibold">Chain Finder</h2>

		<Card.Root>
			<Card.Content class="pt-6 space-y-4">
				<form onsubmit={search} class="space-y-4">
					<!-- Scope selector -->
					<div class="grid gap-4 sm:grid-cols-2">
						<div class="space-y-1">
							<Label>Scope</Label>
							<div class="flex gap-2">
								<button
									type="button"
									onclick={() => (scopeType = 'modpack')}
									class="px-3 py-1.5 text-sm rounded-md border transition-colors {scopeType === 'modpack' ? 'bg-primary text-primary-foreground' : 'bg-background hover:bg-muted'}"
								>
									Modpack
								</button>
								<button
									type="button"
									onclick={() => (scopeType = 'game')}
									class="px-3 py-1.5 text-sm rounded-md border transition-colors {scopeType === 'game' ? 'bg-primary text-primary-foreground' : 'bg-background hover:bg-muted'}"
								>
									All games
								</button>
							</div>
						</div>

						{#if scopeType === 'modpack'}
							<div class="space-y-1">
								<Label for="modpack-select">Modpack</Label>
								<select
									id="modpack-select"
									bind:value={selectedModpackId}
									class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
									required={scopeType === 'modpack'}
								>
									<option value="">Select a modpack…</option>
									{#each modpacks as mp}
										<option value={mp.id}>{mp.name} v{mp.version}</option>
									{/each}
								</select>
							</div>
						{:else}
							<div class="space-y-1">
								<Label for="game-select">Game</Label>
								<select
									id="game-select"
									bind:value={selectedGameId}
									class="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
									required={scopeType === 'game'}
								>
									<option value="">Select a game…</option>
									{#each games as g}
										<option value={g.id}>{g.name}</option>
									{/each}
								</select>
							</div>
						{/if}
					</div>

					<!-- Input type toggle -->
					<div class="flex items-center gap-3 text-sm">
						<span class="text-muted-foreground">Search by</span>
						<button
							type="button"
							onclick={() => (useTag = true)}
							class="px-2.5 py-1 rounded-md border transition-colors {useTag ? 'bg-primary text-primary-foreground' : 'bg-background hover:bg-muted'}"
						>
							Tag
						</button>
						<button
							type="button"
							onclick={() => (useTag = false)}
							class="px-2.5 py-1 rounded-md border transition-colors {!useTag ? 'bg-primary text-primary-foreground' : 'bg-background hover:bg-muted'}"
						>
							Item ID
						</button>
					</div>

					<!-- Start / target -->
					<div class="grid gap-4 sm:grid-cols-[1fr_1fr_auto]">
						<div class="space-y-1">
							<Label for="start">Start {useTag ? 'tag' : 'item'}</Label>
							<Input
								id="start"
								placeholder={useTag ? 'e.g. c:ores/gold' : 'e.g. minecraft:raw_gold'}
								bind:value={startInput}
								required
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

		{#if results && results.chain_count > 0}
			<p class="text-muted-foreground text-sm">
				{results.chain_count} chain{results.chain_count === 1 ? '' : 's'} found ·
				{results.start} → {results.target}
				{#if results.scope?.type === 'modpack'}
					· {results.scope.name} v{results.scope.version}
				{:else if results.scope?.type === 'game'}
					· {results.scope.name}
				{/if}
			</p>

			{#each results.chains as chain, i}
				<Card.Root>
					<Card.Header>
						<Card.Title class="text-base">
							Chain {i + 1} — {chain.yield_per_input.toFixed(2)}× yield
						</Card.Title>
						<Card.Description>
							{chain.step_count} step{chain.step_count === 1 ? '' : 's'} ·
							{chain.machine_sequence.join(' → ')}
							{#if chain.chemicals_required.length > 0}
								· chemicals: {chain.chemicals_required.join(', ')}
							{/if}
						</Card.Description>
					</Card.Header>
					<Card.Content>
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
								{#each chain.steps as step, j}
									<Table.Row>
										<Table.Cell class="text-muted-foreground">{j + 1}</Table.Cell>
										<Table.Cell class="font-medium">{step.machine}</Table.Cell>
										<Table.Cell class="text-sm">
											{#each step.inputs as inp}
												<div>{inp.quantity}× {inp.item_id ?? inp.tag_string}</div>
											{/each}
											{#each step.aux_inputs as aux}
												<div class="text-muted-foreground">{aux.quantity}× {aux.label} (fluid)</div>
											{/each}
										</Table.Cell>
										<Table.Cell class="text-sm">
											{#each step.outputs as out}
												<div>{out.quantity}× {out.item_id}</div>
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
