<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { accessToken, fetchCurrentUser } from '$lib/auth';

	let error = $state('');

	onMount(async () => {
		const hash = window.location.hash.slice(1);
		if (!hash) {
			error = 'No token received from OAuth provider';
			return;
		}
		// Clear the fragment from the URL immediately
		history.replaceState(null, '', window.location.pathname);

		accessToken.set(hash);
		try {
			await fetchCurrentUser(hash);
			goto('/admin');
		} catch {
			error = 'Failed to fetch user info';
		}
	});
</script>

{#if error}
	<div class="flex min-h-[calc(100vh-57px)] items-center justify-center p-4">
		<div class="text-center space-y-3">
			<p class="text-destructive">{error}</p>
			<a href="/login" class="text-sm underline">Back to login</a>
		</div>
	</div>
{:else}
	<div class="flex min-h-[calc(100vh-57px)] items-center justify-center">
		<div class="border-primary h-8 w-8 animate-spin rounded-full border-4 border-t-transparent"></div>
	</div>
{/if}
