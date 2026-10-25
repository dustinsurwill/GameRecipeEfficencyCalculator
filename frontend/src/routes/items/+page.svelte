<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import ItemGrid from '$lib/components/ItemGrid.svelte';
	import { discover, fmtNum, type DItem } from '$lib/discover';

	const PAGE = 48;
	let items = $state<DItem[]>([]);
	let total = $state(0);
	let loading = $state(true);
	let qInput = $state(page.url.searchParams.get('q') ?? '');
	let timer: ReturnType<typeof setTimeout>;
	let seq = 0;

	const sp = $derived(page.url.searchParams);
	const q = $derived(sp.get('q') ?? '');
	const mod = $derived(sp.get('mod') ?? '');
	const tag = $derived(sp.get('tag') ?? '');
	const sort = $derived(sp.get('sort') ?? 'name');
	const craftable = $derived(sp.get('craftable') === '1');
	const pg = $derived(Number(sp.get('page') ?? '1'));

	function update(changes: Record<string, string | null>, resetPage = true) {
		const next = new URLSearchParams(page.url.searchParams);
		for (const [k, v] of Object.entries(changes)) v ? next.set(k, v) : next.delete(k);
		if (resetPage) next.delete('page');
		goto(`?${next}`, { keepFocus: true, noScroll: true, replaceState: true });
	}

	function onQ() {
		clearTimeout(timer);
		timer = setTimeout(() => update({ q: qInput.trim() || null }), 250);
	}

	$effect(() => {
		const mine = ++seq;
		loading = true;
		discover
			.items({ q, mod, tag, sort, craftable: craftable || undefined, limit: PAGE, offset: (pg - 1) * PAGE })
			.then((r) => {
				if (mine !== seq) return;
				items = r.items;
				total = r.total;
			})
			.finally(() => mine === seq && (loading = false));
	});

	const pages = $derived(Math.max(1, Math.ceil(total / PAGE)));
</script>

<svelte:head><title>Items — RecipeBook</title></svelte:head>

<main class="mx-auto max-w-7xl space-y-5 px-4 py-8">
	<div class="flex flex-wrap items-end justify-between gap-3">
		<h1 class="text-3xl font-bold tracking-tight">Items</h1>
		<p class="text-muted-foreground text-sm tabular-nums">{loading ? 'Loading…' : `${fmtNum(total)} items`}</p>
	</div>

	<div class="flex flex-wrap items-center gap-3">
		<input
			bind:value={qInput}
			oninput={onQ}
			placeholder="Filter by name or id…"
			aria-label="Filter items"
			class="bg-card focus:border-primary min-w-[16rem] flex-1 rounded-lg border px-3 py-2 text-sm outline-none"
		/>
		<label class="flex items-center gap-2 text-sm">
			Sort
			<select value={sort} onchange={(e) => update({ sort: e.currentTarget.value === 'name' ? null : e.currentTarget.value })} class="bg-card rounded-lg border px-2 py-2 text-sm">
				<option value="name">Name</option>
				<option value="used_in">Most used in recipes</option>
				<option value="made_by">Most ways to make</option>
			</select>
		</label>
		<label class="flex cursor-pointer items-center gap-2 text-sm">
			<input type="checkbox" checked={craftable} onchange={(e) => update({ craftable: e.currentTarget.checked ? '1' : null })} />
			Has a recipe
		</label>
	</div>

	{#if mod || tag}
		<div class="flex flex-wrap gap-2 text-sm">
			{#if mod}<button onclick={() => update({ mod: null })} class="bg-secondary rounded-full border px-3 py-1">Mod: {mod} ✕</button>{/if}
			{#if tag}<button onclick={() => update({ tag: null })} class="bg-secondary rounded-full border px-3 py-1">Tag: #{tag} ✕</button>{/if}
		</div>
	{/if}

	{#if !loading && items.length === 0}
		<p class="text-muted-foreground py-16 text-center">No items match. Try a shorter search or clear the filters.</p>
	{:else}
		<div class={loading ? 'opacity-60 transition-opacity' : ''}><ItemGrid {items} /></div>
	{/if}

	{#if pages > 1}
		<nav class="flex items-center justify-center gap-3 pt-4 text-sm" aria-label="Pagination">
			<button disabled={pg <= 1} onclick={() => update({ page: String(pg - 1) }, false)} class="bg-card rounded-md border px-3 py-1.5 disabled:opacity-40">← Previous</button>
			<span class="text-muted-foreground tabular-nums">Page {pg} of {pages}</span>
			<button disabled={pg >= pages} onclick={() => update({ page: String(pg + 1) }, false)} class="bg-card rounded-md border px-3 py-1.5 disabled:opacity-40">Next →</button>
		</nav>
	{/if}
</main>
