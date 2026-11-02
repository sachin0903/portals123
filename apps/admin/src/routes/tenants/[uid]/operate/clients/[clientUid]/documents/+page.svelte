<script lang="ts">
	import { goto, invalidateAll } from '$app/navigation';
	import { DocumentsLibrary } from '@graphene/ui/book/documents';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const base = $derived(
		`/tenants/${data.tenant.uid}/operate/clients/${data.detail.contact.uid}/documents`
	);
</script>

<svelte:head><title>Documents · {data.clientName}</title></svelte:head>
<DocumentsLibrary
	documents={data.documents}
	loadError={data.loadError}
	canReadActivity={false}
	links={{
		document: (uid) => `${base}/${uid}`,
		documentContent: (uid) => `/tenants/${data.tenant.uid}/operate/documents/${uid}/content`,
		fileDownload: (uid) => `/tenants/${data.tenant.uid}/operate/documents/files/${uid}/download`,
		requests: `/tenants/${data.tenant.uid}/operate/documents/requests`,
		templates: `/tenants/${data.tenant.uid}/operate/documents`
	}}
	actions={null}
	onretry={() => invalidateAll()}
	onnavigate={(href) => goto(href)}
/>
