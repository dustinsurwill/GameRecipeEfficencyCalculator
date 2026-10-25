<script lang="ts">
	import { goto } from '$app/navigation';
	import ItemTile from './ItemTile.svelte';
	import { discover, itemHref, tagHref, fmtNum, type SearchResults, type DItem } from '$lib/discover';

	let {
		size = 'md',
		autofocus = false,
		placeholder = 'Search items, mods, machines, tags…',
		onpick = null,
		itemsOnly = false
	}: {
		size?: 'md' | 'lg';
		autofocus?: boolean;
		placeholder?: string;
		onpick?: ((item: DItem) => void) | null;
		itemsOnly?: boolean;
	} = $props();

	let q = $state('');
	let results = $state<SearchResults | null>(null);
	let open = $state(false);
	let active = $state(0);
	let timer: ReturnType<typeof setTimeout>;
	let seq = 0;
	let input: HTMLInputElement;

	type Row = { key: string; href?: string; item?: DItem; label: string; sub: string; ns: string; kind: string };

	const rows = $derived.by<Row[]>(() => {
		if (!results) return [];
		const out: Row[] = results.items.map((i) => ({
			key: 'i' + i.item_id, href: itemHref(i.item_id), item: i, label: i.name,
			sub: `${i.mod_name} · ${i.made_by} recipes`, ns: i.namespace, kind: 'Item'
		}));
		if (itemsOnly) return out;
		for (const m of results.mods) out.push({ key: 'm' + m.namespace, href: `/mods/${m.namespace}`, label: m.name, sub: `${fmtNum(m.item_count)} items`, ns: m.namespace, kind: 'Mod' });
		for (const m of results.machines) out.push({ key: 'c' + m.id, href: `/machines/${m.id}`, label: m.name, sub: `${fmtNum(m.recipe_count)} recipes · ${m.mod_name}`, ns: m.namespace, kind: 'Machine' });
		for (const t of results.tags) out.push({ key: 't' + t.id, href: tagHref(t.id), label: '#' + t.id, sub: `${fmtNum(t.member_count)} items`, ns: t.id.split(':')[0], kind: 'Tag' });
		return out;
	});

	function onInput() {
		clearTimeout(timer);
		active = 0;
		if (q.trim().length < 2) {
			results = null;
			return;
		}
		timer = setTimeout(async () => {
			const mine = ++seq;
			try {
				const r = await discover.search(q.trim());
				if (mine === seq) {
					results = r;
					open = true;
				}
			} catch {
				results = null;
			}
		}, 120);
	}

	function pick(row: Row | undefined) {
		if (!row) {
			if (!onpick && q.trim()) goto(`/items?q=${encodeURIComponent(q.trim())}`);
			return;
		}
		open = false;
		if (onpick && row.item) {
			onpick(row.item);
			q = '';
			results = null;
		} else if (row.href) goto(row.href);
	}

	function onKey(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') { e.preventDefault(); open = true; active = Math.min(active + 1, rows.length - 1); }
		else if (e.key === 'ArrowUp') { e.preventDefault(); active = Math.max(active - 1, 0); }
		else if (e.key === 'Enter') { e.preventDefault(); pick(open ? rows[active] : undefined); }
		else if (e.key === 'Escape') open = false;
	}

	$effect(() => {
		if (autofocus) input?.focus();
	});
	$effect(() => {
		// "/" focuses search from anywhere
		const h = (e: KeyboardEvent) => {
			if (e.key === '/' && document.activeElement?.tagName !== 'INPUT' && document.activeElement?.tagName !== 'TEXTAREA') {
				e.preventDefault();
				input?.focus();
			}
		};
		window.addEventListener('keydown', h);
		return () => window.removeEventListener('keydown', h);
	});
</script>

<div class="relative w-full">
	<input
		bind:this={input}
		bind:value={q}
		oninput={onInput}
		onkeydown={onKey}
		onfocus={() => (open = true)}
		onblur={() => setTimeout(() => (open = false), 150)}
		role="combobox"
		aria-expanded={open && rows.length > 0}
		aria-controls="search-results"
		aria-label="Search"
		autocomplete="off"
		{placeholder}
		class="bg-card focus:border-primary focus:ring-primary/30 w-full rounded-xl border outline-none focus:ring-4 {size === 'lg' ? 'px-5 py-4 text-lg' : 'px-3.5 py-2 text-sm'}"
	/>
	{#if open && rows.length > 0}
		<ul id="search-results" role="listbox" class="bg-popover absolute left-0 right-0 top-full z-50 mt-2 max-h-[70vh] overflow-auto rounded-xl border p-1.5 shadow-xl">
			{#each rows as row, i (row.key)}
				<li role="option" aria-selected={i === active}>
					<button
						type="button"
						onmousedown={(e) => { e.preventDefault(); pick(row); }}
						onmousemove={() => (active = i)}
						class="flex w-full items-center gap-3 rounded-lg px-2.5 py-1.5 text-left {i === active ? 'bg-accent' : ''}"
					>
						<ItemTile name={row.label} namespace={row.ns} size={30} tag={row.kind === 'Tag'} />
						<span class="min-w-0 flex-1">
							<span class="block truncate text-sm font-medium">{row.label}</span>
							<span class="text-muted-foreground block truncate text-xs">{row.sub}</span>
						</span>
						<span class="text-muted-foreground text-[11px] uppercase tracking-wider">{row.kind}</span>
					</button>
				</li>
			{/each}
		</ul>
	{/if}
</div>
