<script lang="ts">
	import { onMount } from 'svelte';
	import { page } from '$app/stores';
	import { api } from '$lib/api';
	import type { ModPackIdentity, ModPackVersion, AdminGroup } from '$lib/types';
	import * as Table from '$lib/components/ui/table';
	import { Button } from '$lib/components/ui/button';
	import { Input } from '$lib/components/ui/input';
	import { Badge } from '$lib/components/ui/badge';
	import * as Card from '$lib/components/ui/card';

	const identityId = $derived($page.params.id);

	let identity = $state<ModPackIdentity | null>(null);
	let versions = $state<ModPackVersion[]>([]);
	let members = $state<AdminGroup[]>([]);
	let error = $state('');
	let newMemberUserId = $state('');
	let newMemberRole = $state('maintainer');
	let addingMember = $state(false);

	onMount(async () => {
		try {
			[identity, versions, members] = await Promise.all([
				api.get<ModPackIdentity>(`/modpacks/${identityId}`),
				api.get<ModPackVersion[]>(`/modpacks/${identityId}/versions`),
				api.get<AdminGroup[]>(`/admin/modpacks/${identityId}/members`),
			]);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Load failed';
		}
	});

	async function setVersionStatus(versionId: string, action: 'publish' | 'deprecate') {
		try {
			const updated = await api.post<ModPackVersion>(`/admin/modpacks/${versionId}/${action}`);
			versions = versions.map((v) => (v.id === versionId ? updated : v));
		} catch (err) {
			error = err instanceof Error ? err.message : `${action} failed`;
		}
	}

	async function addMember(e: Event) {
		e.preventDefault();
		addingMember = true;
		try {
			const group = await api.post<AdminGroup>(`/admin/modpacks/${identityId}/members`, {
				user_id: newMemberUserId,
				role: newMemberRole,
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
			await api.delete(`/admin/modpacks/${identityId}/members/${userId}`);
			members = members.filter((m) => m.user_id !== userId);
		} catch (err) {
			error = err instanceof Error ? err.message : 'Remove failed';
		}
	}

	function statusVariant(status: string): 'default' | 'secondary' | 'destructive' {
		if (status === 'published') return 'default';
		if (status === 'deprecated') return 'destructive';
		return 'secondary';
	}
</script>

<div class="mx-auto max-w-4xl p-6 space-y-6">
	<div>
		<a href="/admin" class="text-muted-foreground hover:text-foreground text-sm">← Admin</a>
		<h1 class="text-2xl font-bold mt-1">{identity?.name ?? 'Modpack'}</h1>
		{#if identity}
			<p class="text-muted-foreground text-sm font-mono">{identity.slug}</p>
		{/if}
	</div>

	{#if error}<p class="text-destructive text-sm">{error}</p>{/if}

	<!-- Versions -->
	<Card.Root>
		<Card.Header><Card.Title>Versions</Card.Title></Card.Header>
		<Card.Content>
			{#if versions.length === 0}
				<p class="text-muted-foreground text-sm">No versions yet.</p>
			{:else}
				<Table.Root>
					<Table.Header>
						<Table.Row>
							<Table.Head>Version</Table.Head>
							<Table.Head>Status</Table.Head>
							<Table.Head class="text-right">Actions</Table.Head>
						</Table.Row>
					</Table.Header>
					<Table.Body>
						{#each versions as v (v.id)}
							<Table.Row>
								<Table.Cell class="font-medium">{v.version}</Table.Cell>
								<Table.Cell>
									<Badge variant={statusVariant(v.status)}>{v.status}</Badge>
								</Table.Cell>
								<Table.Cell class="text-right space-x-2">
									{#if v.status === 'draft'}
										<Button size="sm" onclick={() => setVersionStatus(v.id, 'publish')}>Publish</Button>
									{:else if v.status === 'published'}
										<Button size="sm" variant="outline" onclick={() => setVersionStatus(v.id, 'deprecate')}>Deprecate</Button>
									{/if}
								</Table.Cell>
							</Table.Row>
						{/each}
					</Table.Body>
				</Table.Root>
			{/if}
		</Card.Content>
	</Card.Root>

	<!-- Members -->
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
