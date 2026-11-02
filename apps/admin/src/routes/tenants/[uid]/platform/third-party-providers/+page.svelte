<script lang="ts">
	import ProvidersSection from '$lib/catalogue/providers-section.svelte';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<ProvidersSection
	kind="third_party"
	providers={data.catalogue.third_party_providers}
	products={data.catalogue.products}
	canManage={data.canManage}
	{form}
/>
