import tailwindcss from '@tailwindcss/vite';
import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
	plugins: [tailwindcss(), sveltekit()],
	// Svelte component libraries must not be pre-bundled by esbuild
	optimizeDeps: { exclude: ['@lucide/svelte', 'lucide-svelte', 'bits-ui'] }
});
