<script lang="ts">
	import { onMount } from 'svelte';
	import { bootAuth, authReady } from '$lib/auth';
	import NavBar from '$lib/components/NavBar.svelte';
	import '../app.css';

	let { children } = $props();

	onMount(() => bootAuth());
</script>

{#if $authReady}
	<NavBar />
	{@render children()}
{:else}
	<div class="flex h-screen items-center justify-center">
		<div class="border-primary h-8 w-8 animate-spin rounded-full border-4 border-t-transparent"></div>
	</div>
{/if}
