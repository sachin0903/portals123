<script lang="ts">
	// Thin mount of the SHARED tasks inbox (@graphene/ui/book/tasks — the portal waterfall: the
	// admin portal mounts the same listing read-only over its own loaders). Data loading and the
	// form actions stay in this app (+page.server.ts); the component gets typed rows, this portal's
	// link builders, and — because staff can act here — the action names + `enhance`. The
	// server-side filters and keyset cursor live in THIS page's URL, so the navigation callbacks
	// are implemented here.
	import { enhance } from '$app/forms';
	import { goto, invalidate } from '$app/navigation';
	import { page } from '$app/state';
	import { ListingSkeleton, Streamed } from '@graphene/ui';
	import { TasksListing, type TaskScope } from '@graphene/ui/book/tasks';
	import type { ClientOption } from '@graphene/ui/book/documents';
	import type { Member } from '$lib/api';
	import type { PageData } from './$types';

	let { data, form }: { data: PageData; form: { error?: string; created?: boolean } | null } =
		$props();

	type RoleOption = { slug: string; name: string; portal: string | null };
	let members = $state<Member[]>([]);
	let roles = $state<RoleOption[]>([]);
	let clients = $state<ClientOption[]>([]);
	let pickersLoaded = $state(false);

	async function loadPickers(): Promise<void> {
		if (pickersLoaded) return;
		pickersLoaded = true;
		const res = await fetch('/tasks/pickers');
		if (!res.ok) return;
		const body = (await res.json()) as {
			members: Member[];
			roles: RoleOption[];
			clients: ClientOption[];
		};
		members = body.members ?? [];
		roles = body.roles ?? [];
		clients = body.clients ?? [];
	}

	/** Push a filter change into the URL so `load` re-queries the API (and drops any page cursor). */
	function applyFilters(next: {
		category: string[];
		due: string[];
		q: string;
		priority: string[];
		assignee: TaskScope;
	}): void {
		const url = new URL(page.url);
		/** Repeated params, so a multi-valued filter round-trips through a shared link. */
		const setAll = (key: string, values: string[]) => {
			url.searchParams.delete(key);
			for (const value of values) if (value) url.searchParams.append(key, value);
		};
		setAll('category', next.category);
		setAll('due', next.due);
		// The flag this replaced. Dropped whenever the filters change, so an old bookmark is read
		// once (see `+page.server.ts`) and then stops shadowing the windows the reader picked.
		url.searchParams.delete('overdue');
		if (next.q.trim()) url.searchParams.set('q', next.q.trim());
		else url.searchParams.delete('q');
		setAll('priority', next.priority);
		// `me` is the default, so it stays OUT of the URL: a shared link then means "your tasks" to
		// whoever opens it, which is what this inbox is for.
		if (next.assignee === 'all') url.searchParams.set('assignee', 'all');
		else url.searchParams.delete('assignee');
		// Back to the newest window: a keyset cursor names a position in the PREVIOUS result set,
		// so carrying it into a re-narrowed one starts the list part-way through a list that no
		// longer exists.
		url.searchParams.delete('before');
		goto(`${url.pathname}${url.search}`, { keepFocus: true });
	}
	/** The next keyset window — the inbox used to stop dead at 200 rows with no hint. */
	function showMore(before: number): void {
		const url = new URL(page.url);
		url.searchParams.set('before', String(before));
		goto(`${url.pathname}${url.search}`);
	}
</script>

<svelte:head><title>Tasks · Platform</title></svelte:head>

<!-- Streamed: the inbox is the two task windows (the queue and the other-scope count). The
     pickers the create dialog needs load when it opens. -->
<Streamed data={data.inbox} onretry={() => invalidate('app:tasks')}>
	{#snippet pending()}
		<ListingSkeleton fill={false} actions pager={false} />
	{/snippet}
	{#snippet children(inbox)}
		<TasksListing
			tasks={inbox.tasks}
			mineUids={inbox.mineUids}
			otherOpenCount={inbox.otherOpenCount}
			filters={data.filters}
			nextBefore={inbox.nextBefore}
			{members}
			{roles}
			{clients}
			links={{
				task: (uid) => `/tasks/${uid}`,
				journey: (id) => `/journeys/${id}`,
				// By CONTACT uid — the row's `assignee_contact_id` is a serial and this route
				// resolves by uid, which is why the API now carries the uid alongside it.
				client: (uid) => `/clients/${uid}`
			}}
			actions={{
				enhance,
				complete: '?/complete',
				...(data.canManage ? { create: '?/create', cancel: '?/cancel' } : {})
			}}
			formError={form?.error ?? null}
			initialCreate={page.url.searchParams.get('new') === '1'}
			onfilters={applyFilters}
			onshowmore={showMore}
			onCreateOpen={loadPickers}
		/>
	{/snippet}
</Streamed>
