<script lang="ts">
	import { invalidateAll } from '$app/navigation';
	import {
		Button,
		EventStream,
		LoadErrorBanner,
		PageHeader,
		actionLogBrowserQuery,
		type ActionEntity,
		type ActionEvent,
		type ActionLogFilters
	} from '@graphene/ui';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import { operateEntityHref } from '$lib/audit/entity-href';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();
	const base = $derived(`/tenants/${data.tenantUid}/operate/activity`);
	type EventPage = { events: ActionEvent[]; hasMore: boolean };

	async function fetchPage(filters: ActionLogFilters, before?: number): Promise<EventPage | null> {
		try {
			const response = await fetch(`${base}/more?${actionLogBrowserQuery(filters, { before })}`);
			return response.ok ? ((await response.json()) as EventPage) : null;
		} catch {
			return null;
		}
	}

	const applyFilters = (filters: ActionLogFilters) => fetchPage(filters);
	const fetchNextPage = (before: number, filters: ActionLogFilters) => fetchPage(filters, before);

	async function pollNewer(
		after: number,
		filters: ActionLogFilters
	): Promise<ActionEvent[] | null> {
		try {
			const response = await fetch(`${base}/more?${actionLogBrowserQuery(filters, { after })}`);
			return response.ok ? ((await response.json()) as EventPage).events : null;
		} catch {
			return null;
		}
	}

	// The shared map — every kind operate has a record page for, so the detail pane's "Open …"
	// button reaches documents and tasks, not just clients and accounts.
	const actionEntityHref = (_event: ActionEvent, entity: ActionEntity) =>
		operateEntityHref(data.tenantUid, entity);
</script>

<div
	class="-m-4 flex min-w-0 flex-col gap-3 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
>
	<PageHeader
		eyebrow="Firm activity"
		title="Activity"
		description="Actions by the firm's staff and by Graphene support acting on its behalf."
	>
		{#snippet actions()}
			{#if data.canConfigure}
				<!-- As on the platform: the notification registry has no nav row of its own, because it
				     is configuration FOR this surface and is reached from the surface it configures. -->
				<Button href="/tenants/{data.tenantUid}/notifications" variant="outline" size="sm">
					<SettingsIcon class="size-4" />
					Configure notifications
				</Button>
			{/if}
		{/snippet}
	</PageHeader>
	{#if data.apiError}
		<LoadErrorBanner message={data.apiError} onRetry={() => invalidateAll()} />
	{/if}
	<div class="min-h-0 flex-1">
		<EventStream
			events={data.events}
			facets={data.facets ?? undefined}
			hasMore={data.hasMore}
			loadError={!!data.apiError}
			showTenantFilter={false}
			entityHref={actionEntityHref}
			onFilterApply={applyFilters}
			onLoadMore={fetchNextPage}
			onRefresh={() => invalidateAll()}
			onPollNewer={pollNewer}
		/>
	</div>
</div>
