<script lang="ts">
	import { onMount } from 'svelte';
	import { isAuthenticated, currentUser, isSuperuser, logout } from '$lib/auth';
	import { goto } from '$app/navigation';
	import * as DropdownMenu from '$lib/components/ui/dropdown-menu';
	import * as Avatar from '$lib/components/ui/avatar';
	import { Button } from '$lib/components/ui/button';
	import { page } from '$app/state';
	import SearchBox from './SearchBox.svelte';

	let dark = $state(false);

	onMount(() => {
		dark = document.documentElement.classList.contains('dark');
	});

	function toggleDark() {
		dark = !dark;
		document.documentElement.classList.toggle('dark', dark);
		localStorage.setItem('theme', dark ? 'dark' : 'light');
	}

	async function handleLogout() {
		await logout();
		goto('/login');
	}
</script>

<nav class="border-border bg-background/90 sticky top-0 z-40 border-b px-4 py-2.5 backdrop-blur">
	<div class="mx-auto flex max-w-7xl items-center justify-between gap-4">
		<div class="flex items-center gap-5">
			<a href="/" class="text-foreground flex items-center gap-2 text-lg font-bold tracking-tight">
				<span class="bg-primary text-primary-foreground grid h-6 w-6 place-items-center rounded text-xs font-black">R</span>
				<span class="hidden sm:inline">RecipeBook</span>
			</a>
			<div class="flex items-center gap-3 text-sm sm:gap-4">
				{#each [['/items', 'Items'], ['/mods', 'Mods'], ['/machines', 'Machines'], ['/routes', 'Routes']] as [href, label]}
					<a {href} class="transition-colors {page.url.pathname.startsWith(href) ? 'text-foreground font-medium' : 'text-muted-foreground hover:text-foreground'}">{label}</a>
				{/each}
			</div>
		</div>

		{#if page.url.pathname !== '/'}
			<div class="hidden max-w-md flex-1 md:block"><SearchBox /></div>
		{/if}

		<div class="flex items-center gap-3">
			<button
				onclick={toggleDark}
				aria-label="Toggle dark mode"
				class="text-muted-foreground hover:text-foreground rounded-md p-1.5 transition-colors"
			>
				{#if dark}
					<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg>
				{:else}
					<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
				{/if}
			</button>
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
