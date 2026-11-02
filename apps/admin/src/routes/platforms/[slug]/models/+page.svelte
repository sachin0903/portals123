<script lang="ts">
	import ModelsTab from '$lib/platforms/components/ModelsTab.svelte';
	import { usePlatformStore } from '$lib/platforms/context';

	const holder = usePlatformStore();
	const store = $derived(holder.store);
</script>

<ModelsTab {store} />
