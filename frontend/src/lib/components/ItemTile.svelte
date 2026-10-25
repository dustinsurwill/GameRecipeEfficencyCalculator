<script lang="ts">
	// Stand-in for an item icon: a slot-style tile tinted by the owning mod, with initials.
	let {
		name,
		namespace = '',
		size = 36,
		tag = false,
		fluid = false
	}: { name: string; namespace?: string; size?: number; tag?: boolean; fluid?: boolean } = $props();

	const hue = $derived.by(() => {
		let h = 0;
		for (const c of namespace) h = (h * 31 + c.charCodeAt(0)) % 360;
		return h;
	});
	const initials = $derived.by(() => {
		if (tag) return '#';
		const words = name.split(/[\s/]+/).filter((w) => w && !/^(of|the|and)$/i.test(w));
		return ((words[0]?.[0] ?? '?') + (words[1]?.[0] ?? words[0]?.[1] ?? '')).toUpperCase();
	});
</script>

<span
	class="tile"
	class:tag
	class:fluid
	style="--s:{size}px; --h:{hue}"
	aria-hidden="true"
>{fluid ? '≈' : initials}</span>

<style>
	.tile {
		width: var(--s);
		height: var(--s);
		flex: none;
		display: inline-grid;
		place-items: center;
		font-weight: 700;
		font-size: calc(var(--s) * 0.36);
		letter-spacing: 0.02em;
		border-radius: 4px;
		color: hsl(var(--h) 45% 90%);
		background: hsl(var(--h) 34% 30%);
		box-shadow:
			inset 2px 2px 0 hsl(var(--h) 34% 42% / 0.9),
			inset -2px -2px 0 hsl(var(--h) 40% 17% / 0.9);
		user-select: none;
	}
	.tag {
		background: transparent;
		color: hsl(var(--h) 45% 70%);
		box-shadow: inset 0 0 0 1.5px hsl(var(--h) 40% 50% / 0.7);
		border-radius: 4px;
		border: 1.5px dashed transparent;
	}
	.fluid {
		background: hsl(205 45% 28%);
		color: hsl(205 70% 82%);
		box-shadow:
			inset 2px 2px 0 hsl(205 45% 40%),
			inset -2px -2px 0 hsl(205 50% 16%);
	}
</style>
