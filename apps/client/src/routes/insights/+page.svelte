<script lang="ts">
	import ComposedPage from '$lib/components/composed-page.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
</script>

<ComposedPage composed={data.composed} />
