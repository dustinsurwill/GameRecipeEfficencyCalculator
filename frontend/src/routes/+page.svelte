<script lang="ts">
	import { onMount } from 'svelte';
	import { isAuthenticated } from '$lib/auth';
	import { goto } from '$app/navigation';
	import { get } from 'svelte/store';

	onMount(() => {
		goto(get(isAuthenticated) ? '/admin' : '/login', { replaceState: true });
	});
</script>
