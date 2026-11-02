<script lang="ts">
	import { enhance } from '$app/forms';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { TasksListing } from '@graphene/ui/book/tasks';
	import type { ClientOption } from '@graphene/ui/book/documents';
	import type { PageData } from './$types';

	let { data, form }: { data: PageData; form: { error?: string } | null } = $props();
	const base = $derived(`/tenants/${data.tenant.uid}/operate`);
	type Member = { user_id: string; email: string; name: string | null; enabled: boolean };
	type Role = { slug: string; name: string; portal: string | null };
	let members = $state<Member[]>([]);
	let roles = $state<Role[]>([]);
	let clients = $state<ClientOption[]>([]);
	let pickersLoaded = $state(false);

	async function loadPickers(): Promise<void> {
		if (pickersLoaded || !data.writing) return;
		pickersLoaded = true;
		const res = await fetch(`${base}/tasks/pickers`);
		if (!res.ok) return;
		const body = (await res.json()) as {
			members: Member[];
			roles: Role[];
			clients: ClientOption[];
		};
		members = body.members ?? [];
		roles = body.roles ?? [];
		clients = body.clients ?? [];
	}
	function filters(next: { category: string[]; due: string[]; q: string; priority: string[] }) {
		const url = new URL(page.url);
		/** Repeated params, so a multi-valued filter round-trips through a shared link. */
		const setAll = (key: string, values: string[]) => {
			url.searchParams.delete(key);
			for (const value of values) if (value) url.searchParams.append(key, value);
		};
		setAll('category', next.category);
		setAll('due', next.due);
		// The flag `due` replaced. Dropped whenever the filters change, so an old bookmark is read
		// once by the loader and then stops shadowing the windows the reader picked.
		url.searchParams.delete('overdue');
		if (next.q.trim()) url.searchParams.set('q', next.q.trim());
		else url.searchParams.delete('q');
		setAll('priority', next.priority);
		// Back to the newest window: a keyset cursor names a position in the PREVIOUS result set,
		// so carrying it into a re-narrowed one starts the list part-way through a list that no
		// longer exists.
		url.searchParams.delete('before');
		void goto(`${url.pathname}${url.search}`, { keepFocus: true });
	}
	function older(before: number) {
		const url = new URL(page.url);
		url.searchParams.set('before', String(before));
		void goto(`${url.pathname}${url.search}`);
	}
</script>

<svelte:head><title>Tasks · {data.tenant.name}</title></svelte:head>

<TasksListing
	tasks={data.tasks}
	mineUids={data.mineUids}
	filters={data.filters}
	nextBefore={data.nextBefore}
	{members}
	{roles}
	{clients}
	links={{
		task: (uid) => `${base}/tasks/${uid}`,
		journey: (uid) => `${base}/journeys/${uid}`,
		// The operate portal's own client record, the shape every other operate mount uses.
		client: (uid) => `${base}/clients/${uid}`
	}}
	actions={data.writing
		? { enhance, complete: '?/complete', create: '?/create', cancel: '?/cancel' }
		: null}
	formError={form?.error ?? null}
	onfilters={filters}
	onshowmore={older}
	onCreateOpen={loadPickers}
/>
