<script lang="ts">
	import TerminologySection from '$lib/catalogue/terminology-section.svelte';
	import type { PageData, ActionData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<TerminologySection terms={data.terms} canManage={data.canManage} {form} />
