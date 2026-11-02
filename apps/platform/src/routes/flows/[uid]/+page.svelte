<script lang="ts">
	import { enhance } from '$app/forms';
	import { beforeNavigate, goto, invalidateAll } from '$app/navigation';
	import { FlowEditor } from '@graphene/ui/flow-editor';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();
</script>

<svelte:head><title>{data.flow ? data.flow.name : 'Flow'} · Platform</title></svelte:head>

<FlowEditor
	{data}
	{form}
	{enhance}
	{beforeNavigate}
	navigate={goto}
	invalidate={invalidateAll}
	links={{
		index: '/flows',
		insights: (uid) => `/flows/${uid}/insights`,
		export: (uid) => `/flows/${uid}/export`
	}}
	endpoints={{
		validate: '/flows/validate',
		triggers: '/flows/triggers',
		formFields: '/flows/form-fields'
	}}
/>
