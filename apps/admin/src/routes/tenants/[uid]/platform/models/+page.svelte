<script lang="ts">
	import { PageHeader } from '@graphene/ui';
	import ModelsSection from '$lib/catalogue/models-section.svelte';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<PageHeader
	eyebrow={data.tenant.name}
	title="Models"
	description="Open Allocations on a model to choose each instrument and its percentage. Cash counts towards the total, and the total is 100%."
/>

<ModelsSection
	models={data.catalogue.models}
	detail={data.model}
	canManage={data.canManage}
	seiEnabled={data.seiEnabled}
	seiDsrOptions={data.seiDsrOptions}
	modelDsrs={data.modelDsrs}
	{form}
/>
