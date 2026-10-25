<script lang="ts">
	import ItemTile from './ItemTile.svelte';
	import { itemHref, tagHref, tagLabel, fmtQty, type DSlot } from '$lib/discover';

	let { slot, highlight = null }: { slot: DSlot; highlight?: string | null } = $props();

	const href = $derived(
		slot.item_id ? itemHref(slot.item_id) : slot.tag ? tagHref(slot.tag) : null
	);
	const ns = $derived((slot.item_id ?? slot.tag ?? slot.fluid_label ?? '').split(':')[0]);
	const isHit = $derived(!!highlight && (slot.item_id === highlight || slot.via_tag));
	const label = $derived(slot.item_id || slot.is_fluid ? slot.name : tagLabel(slot.tag!));
</script>

<svelte:element
	this={href ? 'a' : 'span'}
	href={href ?? undefined}
	class="chip group flex min-w-0 items-center gap-2 rounded-md px-1.5 py-1 {href ? 'hover:bg-accent' : ''} {isHit ? 'ring-primary/60 bg-primary/10 ring-1' : ''}"
	title={slot.item_id ?? (slot.tag ? `Tag: #${slot.tag}` : slot.fluid_label ?? '')}
>
	<ItemTile name={slot.item_id || slot.is_fluid ? slot.name : label} namespace={ns} size={32} tag={!slot.item_id && !!slot.tag} fluid={slot.is_fluid} />
	<span class="min-w-0 leading-tight">
		<span class="block truncate text-sm font-medium">
			{#if slot.quantity !== 1}<span class="text-primary mr-1 tabular-nums">{fmtQty(slot.quantity)}×</span>{/if}
			{#if !slot.item_id && slot.tag}<span class="text-muted-foreground mr-1">Any</span>{/if}{label}
		</span>
		{#if slot.via_tag && slot.tag}
			<span class="text-muted-foreground block truncate text-[11px]">via #{slot.tag}</span>
		{:else if slot.is_fluid && slot.quantity > 0}
			<span class="text-muted-foreground block text-[11px]">{slot.item_id ? '' : 'fluid / chemical'}</span>
		{/if}
	</span>
</svelte:element>
