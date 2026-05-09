<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { api } from '$lib/api';
	import type { ModPack, AdminGroup } from '$lib/types';
	import * as Table from '$lib/components/ui/table';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Badge } from '$lib/components/ui/badge';
	import * as Card from '$lib/components/ui/card';

	const modpackId = $derived($page.params.id);

	let modpack = $state<ModPack | null>(null);
	let members = $state<AdminGroup[]>([]);
	let error = $state('');
	let newMemberUserId = $state('');
	let newMemberRole = $state('maintainer');
	let addingMember = $state(false);

	onMount(async () => {
		try {
			[modpack, members] = await Promise.all([
				api.get<ModPack>(`/modpacks/${modpackId}`),
				api.get<AdminGroup[]>(`/admin/modpacks/${modpackId}/members`)
			]);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Load failed';
		}
	});

	async function publish() {
		try {
			modpack = await api.post<ModPack>(`/admin/modpack-versions/${modpackId}/publish`);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Publish failed';
		}
	}

	async function deprecate() {
		try {
			modpack = await api.post<ModPack>(`/admin/modpack-versions/${modpackId}/deprecate`);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Deprecate failed';
		}
	}

	async function addMember(e: Event) {
		e.preventDefault();
		addingMember = true;
		try {
			const group = await api.post<AdminGroup>(`/admin/modpacks/${modpackId}/members`, {
				user_id: newMemberUserId,
				role: newMemberRole
			});
			members = [...members, group];
			newMemberUserId = '';
		} catch (err) {
			error = err instanceof Error ? err.message : 'Add member failed';
		} finally {
			addingMember = false;
		}
	}

	async function removeMember(userId: string) {
		try {
			await api.delete(`/admin/modpacks/${modpackId}/members/${userId}`);
			members = members.filter((m) => m.user_id !== userId);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Remove failed';
		}
	}

	function statusVariant(status: string) {
		return status === 'published' ? 'default' : status === 'deprecated' ? 'destructive' : 'secondary';
	}
</script>

<div class="mx-auto max-w-4xl p-6 space-y-6">
	<div>
		<a href="/admin" class="text-muted-foreground hover:text-foreground text-sm">← Admin</a>
		<div class="flex items-center gap-3 mt-1">
			<h1 class="text-2xl font-bold">{modpack?.name ?? 'Modpack'}</h1>
			{#if modpack}
				<Badge variant={statusVariant(modpack.status)}>{modpack.status}</Badge>
				<span class="text-muted-foreground text-sm">v{modpack.version}</span>
			{/if}
		</div>
	</div>

	{#if error}<p class="text-destructive text-sm">{error}</p>{/if}

	{#if modpack}
		<div class="flex gap-2">
			{#if modpack.status === 'draft'}
				<Button onclick={publish}>Publish</Button>
			{:else if modpack.status === 'published'}
				<Button variant="outline" onclick={deprecate}>Deprecate</Button>
			{/if}
		</div>
	{/if}

	<Card.Root>
		<Card.Header><Card.Title>Members</Card.Title></Card.Header>
		<Card.Content class="space-y-4">
			{#if members.length > 0}
				<Table.Root>
					<Table.Header>
						<Table.Row>
							<Table.Head>User ID</Table.Head>
							<Table.Head>Role</Table.Head>
							<Table.Head></Table.Head>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{#each members as m (m.id)}
							<Table.Row>
								<Table.Cell class="font-mono text-sm">{m.user_id}</Table.Cell>
								<Table.Cell><Badge variant="secondary">{m.role}</Badge></Table.Cell>
								<Table.Cell class="text-right">
									<Button size="sm" variant="destructive" onclick={() => removeMember(m.user_id)}>Revoke</Button>
								</Table.Cell>
							</Table.Row>
						{/each}
					</Table.Body>
				</Table.Root>
			{/if}

			<form onsubmit={addMember} class="flex gap-2 items-end">
				<div class="flex-1 space-y-1">
					<label class="text-sm font-medium" for="user-id">User ID (UUID)</label>
					<Input id="user-id" bind:value={newMemberUserId} placeholder="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" required />
				</div>
				<div class="space-y-1">
					<label class="text-sm font-medium" for="role">Role</label>
					<select id="role" bind:value={newMemberRole} class="border-input bg-background h-9 rounded-md border px-3 text-sm">
						<option value="maintainer">Maintainer</option>
						<option value="admin">Admin</option>
					</select>
				</div>
				<Button type="submit" disabled={addingMember}>Add</Button>
			</form>
		</Card.Content>
	</Card.Root>
</div>
