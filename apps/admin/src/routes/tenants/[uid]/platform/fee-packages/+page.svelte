<script lang="ts">
	import { PageHeader } from '@graphene/ui';
	import FeesSection from '$lib/catalogue/fees-section.svelte';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<PageHeader
	eyebrow={data.tenant.name}
	title="Fee packages"
	description="Review fixed, percentage and tiered charges, including effective dates, limits and instrument scope."
/>

<FeesSection
	fees={data.catalogue.fee_packages}
	products={data.catalogue.products}
	models={data.catalogue.models}
	canManage={data.canManage}
	{form}
/>
