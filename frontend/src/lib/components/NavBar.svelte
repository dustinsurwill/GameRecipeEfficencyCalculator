<script lang="ts">
	import { isAuthenticated, currentUser, isSuperuser, logout } from '$lib/auth';
	import { goto } from '$app/navigation';
	import * as DropdownMenu from '$lib/components/ui/dropdown-menu';
	import * as Avatar from '$lib/components/ui/avatar';
	import { Button } from '$lib/components/ui/button';

	async function handleLogout() {
		await logout();
		goto('/login');
	}
</script>

<nav class="border-border bg-background border-b px-4 py-3">
	<div class="mx-auto flex max-w-7xl items-center justify-between">
		<a href="/" class="text-foreground text-lg font-semibold">RecipeBook</a>

		<div class="flex items-center gap-3">
			{#if $isAuthenticated}
				{#if $isSuperuser || $currentUser}
					<a href="/admin" class="text-muted-foreground hover:text-foreground text-sm">Admin</a>
				{/if}
				<DropdownMenu.Root>
					<DropdownMenu.Trigger>
						<Avatar.Root class="h-8 w-8 cursor-pointer">
							<Avatar.Image src={$currentUser?.avatar_url ?? ''} alt={$currentUser?.display_name} />
							<Avatar.Fallback>
								{$currentUser?.display_name?.charAt(0)?.toUpperCase() ?? '?'}
							</Avatar.Fallback>
						</Avatar.Root>
					</DropdownMenu.Trigger>
					<DropdownMenu.Content align="end">
						<DropdownMenu.Label>{$currentUser?.display_name ?? 'Account'}</DropdownMenu.Label>
						<DropdownMenu.Separator />
						<DropdownMenu.Item onclick={() => goto('/profile')}>Profile</DropdownMenu.Item>
						<DropdownMenu.Separator />
						<DropdownMenu.Item onclick={handleLogout}>Log out</DropdownMenu.Item>
					</DropdownMenu.Content>
				</DropdownMenu.Root>
			{:else}
				<Button variant="ghost" size="sm" href="/login">Log in</Button>
				<Button size="sm" href="/register">Sign up</Button>
			{/if}
		</div>
	</div>
</nav>
