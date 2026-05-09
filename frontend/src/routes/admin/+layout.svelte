<script lang="ts">
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { isAuthenticated, authReady } from '$lib/auth';
	import { get } from 'svelte/store';

	let { children } = $props();

	onMount(() => {
		if (get(authReady) && !get(isAuthenticated)) {
			goto('/login');
		}
	});

	$effect(() => {
		if ($authReady && !$isAuthenticated) {
			goto('/login');
		}
	});
</script>

{@render children()}
