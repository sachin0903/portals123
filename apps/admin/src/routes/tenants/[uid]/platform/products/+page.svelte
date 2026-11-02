<script lang="ts">
	import ProductsSection from '$lib/catalogue/products-section.svelte';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<ProductsSection
	products={data.catalogue.products}
	platforms={data.catalogue.platforms}
	currencies={data.currencySettings.currencies.filter((currency) =>
		data.currencySettings.allowed_currencies.includes(currency.code)
	)}
	canManage={data.canManage}
	seiAccountTemplates={data.seiAccountTemplates}
	seiFirmId={data.seiFirmId}
	{form}
/>
