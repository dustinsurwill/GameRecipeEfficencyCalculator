<script lang="ts">
	import ItemTile from './ItemTile.svelte';
	import { itemHref, fmtNum, type DItem } from '$lib/discover';

	let { items, showMod = true }: { items: DItem[]; showMod?: boolean } = $props();
</script>

<ul class="grid gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
	{#each items as it (it.item_id)}
		<li>
			<a
				href={itemHref(it.item_id)}
				class="bg-card hover:border-primary/60 hover:bg-accent flex items-center gap-3 rounded-lg border p-2.5 transition-colors"
			>
				<ItemTile name={it.name} namespace={it.namespace} size={40} />
				<span class="min-w-0 flex-1">
					<span class="block truncate text-sm font-semibold">{it.name}</span>
					{#if showMod}<span class="text-muted-foreground block truncate text-xs">{it.mod_name}</span>{/if}
					<span class="text-muted-foreground block truncate text-xs tabular-nums">
						{it.made_by ? `${fmtNum(it.made_by)} recipe${it.made_by === 1 ? '' : 's'}` : 'no recipe'} · {fmtNum(it.used_in)} use{it.used_in === 1 ? '' : 's'}
					</span>
				</span>
			</a>
		</li>
	{/each}
</ul>
