<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '$lib/api';
	import type { Recipe } from '$lib/types';
	import * as Table from '$lib/components/ui/table';
	import { Button } from '$lib/components/ui/button';
	import { Badge } from '$lib/components/ui/badge';
	import { Textarea } from '$lib/components/ui/textarea';
	import * as Dialog from '$lib/components/ui/dialog';
	import * as Card from '$lib/components/ui/card';

	let recipes = $state<Recipe[]>([]);
	let total = $state(0);
	let offset = $state(0);
	const limit = 20;
	let error = $state('');

	let reviewTarget = $state<Recipe | null>(null);
	let rejectReason = $state('');
	let reviewDialogOpen = $state(false);
	let reviewing = $state(false);

	async function load() {
		try {
			const data = await api.get<{ items: Recipe[]; total: number }>(
				`/recipes/pending?limit=${limit}&offset=${offset}`
			);
			recipes = data.items;
			total = data.total;
		} catch (err) {
			error = err instanceof Error ? err.message : 'Load failed';
		}
	}

	onMount(load);

	function openReview(recipe: Recipe) {
		reviewTarget = recipe;
		rejectReason = '';
		reviewDialogOpen = true;
	}

	async function submitReview(decision: 'approved' | 'rejected') {
		if (!reviewTarget) return;
		reviewing = true;
		try {
			await api.post(`/recipes/${reviewTarget.id}/review`, {
				decision,
				reason: decision === 'rejected' ? rejectReason : undefined
			});
			reviewDialogOpen = false;
			await load();
		} catch (err) {
			error = err instanceof Error ? err.message : 'Review failed';
		} finally {
			reviewing = false;
		}
	}
</script>

<div class="mx-auto max-w-5xl p-6 space-y-4">
	<div>
		<a href="/admin" class="text-muted-foreground hover:text-foreground text-sm">← Admin</a>
		<h1 class="text-2xl font-bold mt-1">Recipe Queue</h1>
		<p class="text-muted-foreground text-sm">{total} pending review</p>
	</div>

	{#if error}<p class="text-destructive text-sm">{error}</p>{/if}

	<Card.Root>
		<Table.Root>
			<Table.Header>
				<Table.Row>
					<Table.Head>Type</Table.Head>
					<Table.Head>Import key</Table.Head>
					<Table.Head>Priority</Table.Head>
					<Table.Head>Created</Table.Head>
					<Table.Head></Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{#each recipes as recipe (recipe.id)}
					<Table.Row>
						<Table.Cell><Badge variant="secondary">{recipe.recipe_type}</Badge></Table.Cell>
						<Table.Cell class="font-mono text-xs max-w-xs truncate">{recipe.import_key}</Table.Cell>
						<Table.Cell>{recipe.source_priority}</Table.Cell>
						<Table.Cell class="text-muted-foreground text-sm">{new Date(recipe.created_at).toLocaleDateString()}</Table.Cell>
						<Table.Cell class="text-right">
							<Button size="sm" onclick={() => openReview(recipe)}>Review</Button>
						</Table.Cell>
					</Table.Row>
				{:else}
					<Table.Row>
						<Table.Cell colspan={5} class="text-center text-muted-foreground py-8">No pending recipes</Table.Cell>
					</Table.Row>
				{/each}
			</Table.Body>
		</Table.Root>
	</Card.Root>

	{#if total > limit}
		<div class="flex justify-center gap-3">
			<Button variant="outline" disabled={offset === 0} onclick={() => { offset = Math.max(0, offset - limit); load(); }}>
				Previous
			</Button>
			<span class="text-muted-foreground self-center text-sm">{offset + 1}–{Math.min(offset + limit, total)} of {total}</span>
			<Button variant="outline" disabled={offset + limit >= total} onclick={() => { offset += limit; load(); }}>
				Next
			</Button>
		</div>
	{/if}
</div>

<Dialog.Root bind:open={reviewDialogOpen}>
	<Dialog.Content class="max-w-md">
		<Dialog.Header>
			<Dialog.Title>Review Recipe</Dialog.Title>
			<Dialog.Description class="font-mono text-xs break-all">{reviewTarget?.import_key}</Dialog.Description>
		</Dialog.Header>

		<div class="space-y-3 py-2">
			<div class="flex gap-2 text-sm">
				<Badge variant="secondary">{reviewTarget?.recipe_type}</Badge>
				<span class="text-muted-foreground">Priority {reviewTarget?.source_priority}</span>
			</div>

			<div class="space-y-1">
				<label class="text-sm font-medium" for="reject-reason">Rejection reason (required if rejecting)</label>
				<Textarea id="reject-reason" bind:value={rejectReason} rows={3} placeholder="Explain why this recipe is being rejected…" />
			</div>
		</div>

		<Dialog.Footer class="flex gap-2">
			<Button variant="destructive" disabled={reviewing || !rejectReason.trim()} onclick={() => submitReview('rejected')}>
				Reject
			</Button>
			<Button disabled={reviewing} onclick={() => submitReview('approved')}>
				Approve
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
