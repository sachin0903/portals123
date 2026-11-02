<script lang="ts">
	import { deserialize } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { toast } from '@graphene/ui';
	import { DocumentDetail, type DocumentDetailActions } from '@graphene/ui/book/documents';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
	const clientBase = $derived(
		`/tenants/${data.tenant.uid}/operate/clients/${data.detail.contact.uid}`
	);
	async function post(name: string, fields: Record<string, string> = {}) {
		const body = new FormData();
		for (const [key, value] of Object.entries(fields)) body.append(key, value);
		const response = await fetch(`?/${name}`, { method: 'POST', body });
		const result = deserialize(await response.text());
		return result.type === 'success';
	}
	const actions: DocumentDetailActions | null = $derived(
		data.writing
			? {
					rename: async ({ name }) => {
						const ok = await post('rename', { name });
						if (ok) await invalidateAll();
						return ok;
					},
					archive: async () => {
						if (await post('archive')) await goto(`${clientBase}/documents`);
					},
					restore: async () => {
						if (await post('restore')) await invalidateAll();
					}
				}
			: null
	);
</script>

<svelte:head><title>{data.doc.name} · {data.clientName}</title></svelte:head>
<DocumentDetail
	doc={data.doc}
	events={data.events}
	previewUrl={data.previewUrl}
	previewContentType={data.previewContentType}
	loadError={data.loadError}
	links={{
		documents: `${clientBase}/documents`,
		template: () => `/tenants/${data.tenant.uid}/operate/documents`,
		client: (uid) => `/tenants/${data.tenant.uid}/operate/clients/${uid}`
	}}
	{actions}
	onrefresh={() => invalidateAll()}
	onretry={async () => {
		if (await post('retry')) {
			toast.success('Render queued.');
			await invalidateAll();
		}
	}}
	onnavigate={(href) => goto(href)}
/>
