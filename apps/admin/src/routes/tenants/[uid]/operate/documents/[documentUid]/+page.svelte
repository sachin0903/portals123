<script lang="ts">
	import { deserialize } from '$app/forms';
	import { goto, invalidateAll } from '$app/navigation';
	import { toast } from '@graphene/ui';
	import { DocumentDetail, type DocumentDetailActions } from '@graphene/ui/book/documents';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/operate/documents`);
	async function action(name: string, fields: Record<string, string> = {}): Promise<boolean> {
		const body = new FormData();
		for (const [key, value] of Object.entries(fields)) body.append(key, value);
		const response = await fetch(`?/${name}`, { method: 'POST', body });
		const result = deserialize(await response.text());
		if (result.type === 'success') return true;
		toast.error(
			result.type === 'failure' ? String(result.data?.error ?? 'Action failed.') : 'Action failed.'
		);
		return false;
	}
	const actions: DocumentDetailActions | null = $derived(
		data.writing
			? {
					rename: async ({ name }) => {
						const ok = await action('rename', { name });
						if (ok) await invalidateAll();
						return ok;
					},
					archive: async () => {
						if (await action('archive')) {
							toast.success('Document deleted.');
							await goto(base);
						}
					},
					restore: async () => {
						if (await action('restore')) {
							toast.success('Document restored.');
							await invalidateAll();
						}
					}
				}
			: null
	);
	async function retry() {
		if (await action('retry')) {
			toast.success('Render queued.');
			await invalidateAll();
		}
	}
</script>

<svelte:head><title>{data.doc.name} · {data.tenant.name}</title></svelte:head>

<DocumentDetail
	doc={data.doc}
	events={data.events}
	previewUrl={data.previewUrl}
	previewContentType={data.previewContentType}
	loadError={data.loadError}
	links={{
		documents: base,
		template: () => base,
		client: (uid) => `/tenants/${data.tenant.uid}/book/clients/${uid}`
	}}
	{actions}
	onretry={retry}
	onrefresh={() => invalidateAll()}
	onnavigate={(href) => goto(href)}
/>
