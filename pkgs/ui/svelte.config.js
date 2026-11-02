import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';

/** Plain Svelte library (no SvelteKit) — preprocess only, so svelte-check/eslint parse `lang="ts"`. */
export default {
	preprocess: vitePreprocess(),
	compilerOptions: {
		// These are normal passthrough components (`...rest` props), never compiled to custom
		// elements, so the custom-element prop-inference warning is a false positive here.
		// All other warnings (a11y, etc.) stay active.
		warningFilter: (w) => w.code !== 'custom_element_props_identifier'
	}
};
