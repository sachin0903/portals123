<script lang="ts">
	import { enhance } from '$app/forms';
	import { beforeNavigate, goto, invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import { FlowEditor } from '@graphene/ui/flow-editor';
	import type { ActionData, PageData } from './$types';
	let { data, form }: { data: PageData; form: ActionData } = $props();
	const base = $derived(`/tenants/${page.params.uid}/operate/flows`);
</script>

<svelte:head><title>{data.flow ? data.flow.name : 'Flow'} · {data.tenant.name}</title></svelte:head>

<FlowEditor
	{data}
	{form}
	{enhance}
	{beforeNavigate}
	navigate={goto}
	invalidate={invalidateAll}
	links={{ index: base, insights: (uid) => `${base}/${uid}/insights` }}
	endpoints={{
		validate: `${base}/validate`,
		triggers: `${base}/triggers`,
		formFields: `${base}/form-fields`
	}}
/>
