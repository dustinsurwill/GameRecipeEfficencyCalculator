<script lang="ts">
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Label } from '$lib/components/ui/label';
	import * as Card from '$lib/components/ui/card';
	import * as Select from '$lib/components/ui/select';
	import * as Table from '$lib/components/ui/table';

	const BASE = import.meta.env.VITE_API_BASE as string;

	type ChainStep = {
		step: number;
		machine: string;
		input_item: string;
		input_qty: number;
		output_item: string;
		output_qty: number;
		recipe_id: string;
	};

	type ChainResult = {
		target: string;
		start: string;
		total_yield: number;
		steps: ChainStep[];
	};

	let modpackId = $state('');
	let startItem = $state('');
	let targetItem = $state('');
	let results = $state<ChainResult[]>([]);
	let error = $state('');
	let loading = $state(false);

	async function search(e: Event) {
		e.preventDefault();
		error = '';
		results = [];
		loading = true;
		try {
			const params = new URLSearchParams({ start: startItem, target: targetItem });
			if (modpackId) params.set('modpack_id', modpackId);
			const res = await fetch(`${BASE}/chains?${params}`);
			if (res.status === 429) {
				error = 'Too many requests — please wait a moment and try again.';
				return;
			}
			if (!res.ok) {
				const data = await res.json().catch(() => ({}));
				error = data.detail ?? `Error ${res.status}`;
				return;
			}
			results = await res.json();
			if (results.length === 0) error = 'No chains found between those items.';
		} catch {
			error = 'Network error — could not reach the server.';
		} finally {
			loading = false;
		}
	}
</script>

<main class="mx-auto max-w-4xl px-4 py-10 space-y-8">
	<div class="space-y-1">
		<h1 class="text-3xl font-bold tracking-tight">Recipe Chain Finder</h1>
		<p class="text-muted-foreground">
			Find every multi-step processing path between two items, ranked by yield per input.
		</p>
	</div>

	<Card.Root>
		<Card.Content class="pt-6">
			<form onsubmit={search} class="grid gap-4 sm:grid-cols-[1fr_1fr_auto]">
				<div class="space-y-1">
					<Label for="start">Start item / tag</Label>
					<Input
						id="start"
						placeholder="e.g. minecraft:raw_gold"
						bind:value={startItem}
						required
					/>
				</div>
				<div class="space-y-1">
					<Label for="target">Target item / tag</Label>
					<Input
						id="target"
						placeholder="e.g. mekanism:ingot_gold"
						bind:value={targetItem}
						required
					/>
				</div>
				<div class="flex items-end">
					<Button type="submit" disabled={loading} class="w-full sm:w-auto">
						{loading ? 'Searching…' : 'Find chains'}
					</Button>
				</div>
			</form>
		</Card.Content>
	</Card.Root>

	{#if error}
		<p class="text-destructive text-sm">{error}</p>
	{/if}

	{#each results as chain, i}
		<Card.Root>
			<Card.Header>
				<Card.Title>Chain {i + 1} — {chain.total_yield}× yield</Card.Title>
				<Card.Description>{chain.start} → {chain.target}</Card.Description>
			</Card.Header>
			<Card.Content>
				<Table.Root>
					<Table.Header>
						<Table.Row>
							<Table.Head class="w-10">#</Table.Head>
							<Table.Head>Machine</Table.Head>
							<Table.Head>Input</Table.Head>
							<Table.Head>Output</Table.Head>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{#each chain.steps as step}
							<Table.Row>
								<Table.Cell>{step.step}</Table.Cell>
								<Table.Cell>{step.machine}</Table.Cell>
								<Table.Cell>{step.input_qty}× {step.input_item}</Table.Cell>
								<Table.Cell>{step.output_qty}× {step.output_item}</Table.Cell>
							</Table.Row>
						{/each}
					</Table.Body>
				</Table.Root>
			</Card.Content>
		</Card.Root>
	{/each}
</main>
