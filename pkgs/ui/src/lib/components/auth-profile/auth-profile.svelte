<script lang="ts">
	import ShieldCheckIcon from '@lucide/svelte/icons/shield-check';
	import UsersIcon from '@lucide/svelte/icons/users';
	import { eventCategoryLabel } from '../audit/event-stream-filters.js';
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import * as Card from '../ui/card/index.js';
	import { DataTable, type DataTableColumn, type DataTableSort } from '../ui/data-table/index.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import SectionHeading from '../ui/section-heading/section-heading.svelte';
	import { TruncatedText } from '../ui/truncated-text/index.js';
	import StatusBadge from '../ui/status-badge/status-badge.svelte';
	import { describeActionEvent } from '../audit/describe.js';
	import ActivityHeatmap from './activity-heatmap.svelte';
	import type { ActivityDay } from './activity-heatmap.svelte';
	import LiveSessionList from './live-sessions.svelte';
	import type { LiveSessionRow } from './live-sessions.svelte';
	import Pagination from '../ui/pagination/pagination.svelte';
	import { ListingToolbar, search } from '../book/listing/index.js';
	import { humanise } from '../../sentence-case.js';
	import { clientTeamRoleLabel } from '../book/clients/team.js';
	import { groupPermissions } from './permissions.js';

	export type AuthMembership = {
		portal: string;
		status: string;
		created_at: string;
		role_slugs: string[];
		permissions: string[];
	};

	export type AuthSubjectAccess = {
		uid: string;
		name: string | null;
		email: string | null;
		capacity?: string | null;
		role?: string | null;
		account_count: number;
	};

	export type AuthProfileData = {
		user: {
			id: string;
			uid: string;
			email: string;
			name: string | null;
			mobile: string | null;
			avatar: string | null;
			enabled: boolean;
			types: string[];
			/** Whether they may hold a client book. Staff only; absent on a client profile. */
			is_adviser?: boolean;
			job_title?: string | null;
			fca_reference?: string | null;
			/** Seccl node this adviser opens clients under. Staff only. */
			seccl_node?: string | null;
			/** SEI employee this adviser is. Staff only. */
			sei_dsr?: string | null;
			first_name?: string | null;
			last_name?: string | null;
			created_at: string;
			updated_at: string;
			invitation_sent_at: string | null;
			invitation_expires_at: string | null;
			invitation_pending: boolean;
			invitation_accepted_at: string | null;
			last_sign_in: string | null;
		};
		memberships: AuthMembership[];
		effective_permissions: string[];
		client_access: AuthSubjectAccess[];
		staff_access: AuthSubjectAccess[];
		activity: {
			recorded_actions: number;
			last_recorded_action: string | null;
			successful_sign_ins: number;
			failed_sign_ins: number;
		};
		recent_activity: {
			id: number;
			event_type: string;
			created_at: string;
			method: string;
			route: string;
			status: number;
			path_params: Record<string, unknown>;
			metadata: Record<string, unknown>;
		}[];
		/** Latest navigations, merged into the event list. Absent on an older payload. */
		recent_page_views?: {
			id: number;
			portal: string;
			path: string;
			created_at: string;
		}[];
		/** Daily counts for the last 53 weeks. Absent on a payload from before page views. */
		activity_days?: ActivityDay[];
		security: {
			available: boolean;
			email_verified?: boolean;
			mfa_enrolled?: boolean;
			factor_count?: number;
			active_sessions?: number;
			sessions?: LiveSessionRow[];
		};
	};

	let {
		profile,
		clientHref = (uid: string) => `/clients/${uid}`,
		canRevokeSessions = false
	}: {
		profile: AuthProfileData;
		clientHref?: (uid: string) => string;
		/** A manager may end one of this person's sessions. Their own browser is not offered. */
		canRevokeSessions?: boolean;
	} = $props();

	const dateTime = (value: string | null | undefined) =>
		value
			? new Intl.DateTimeFormat('en-GB', {
					dateStyle: 'medium',
					timeStyle: 'short'
				}).format(new Date(value))
			: 'Never';
	const portalLabel = (portal: string) =>
		({ client: 'Client Portal', platform: 'Platform', operations: 'Internal Operations' })[
			portal
		] ?? portal;
	const accessMode = $derived(profile.user.types.includes('client') ? 'client' : 'staff');
	const access = $derived(accessMode === 'client' ? profile.client_access : profile.staff_access);

	// A search box earns its place once scanning the column by eye gets slower than typing a
	// name — somewhere past a handful of rows. A client login's `client_access` is normally one
	// row (or a small household); an adviser's `staff_access` is their assigned book and can run
	// to hundreds. Below the threshold a box is empty furniture over a list read at a glance (a
	// four-row table is the case the house rule warns about), so it stays hidden until the table
	// is actually long enough to be worth searching.
	const SEARCHABLE_ACCESS_ROWS = 8;
	let accessQuery = $state('');
	// CLIENT-SIDE: `profile` arrives as one whole payload with no server paging, so there is no
	// page boundary a local filter could lie about (contrast the book listings' server mode).
	const filteredAccess = $derived(
		access.filter((item) => search(accessQuery, item.name, item.email))
	);

	// Client and Access are DERIVED — the cell falls back through name/email and capacity/role —
	// so each states its own `sortValue`; Accounts is the row's own field.
	const accessColumns: DataTableColumn<AuthSubjectAccess>[] = [
		{
			key: 'client',
			header: 'Client',
			sortable: true,
			sortDefault: true,
			sortValue: (item) => item.name ?? item.email ?? ''
		},
		{
			key: 'access',
			header: 'Access',
			sortable: true,
			sortValue: (item) => item.capacity ?? item.role ?? ''
		},
		{
			key: 'account_count',
			header: 'Accounts',
			sortable: true,
			align: 'center',
			defaultDir: 'desc'
		}
	];
	// `$state`, not an inline literal: DataTable's `sort` is `$bindable`, and a literal recreated on
	// every update would stamp the reader's chosen column back to unsorted.
	let accessSort = $state<DataTableSort | null>(null);

	const permissionGroups = $derived(groupPermissions(profile.effective_permissions));

	// Actions and page views arrive as two windows of thirty. Merged here, a burst of
	// navigations cannot drop a sign-in off the list.
	const ACTIVITY_PAGE = 20;
	const PAGE_VIEWS = 'Page views';
	type RecordedRow = {
		key: string;
		created_at: string;
		category: string;
		sentence: string;
		title: string;
	};
	let activityFilter = $state<string | null>(null);
	let activityPage = $state(1);
	const recordedActivity = $derived.by(() => {
		const actions: RecordedRow[] = profile.recent_activity.map((event) => ({
			key: `action-${event.id}`,
			created_at: event.created_at,
			category: eventCategoryLabel(event.event_type),
			sentence: describeActionEvent(event.event_type, event.path_params, null, event.metadata),
			title: `${event.method} ${event.route}`
		}));
		const views: RecordedRow[] = (profile.recent_page_views ?? []).map((view) => ({
			key: `view-${view.id}`,
			created_at: view.created_at,
			category: PAGE_VIEWS,
			sentence: `Viewed ${view.path}`,
			title: `${portalLabel(view.portal)} · ${view.path}`
		}));
		return [...actions, ...views].sort((a, b) => {
			const at = Date.parse(a.created_at);
			const bt = Date.parse(b.created_at);
			if (at !== bt) return bt - at;
			return a.key < b.key ? 1 : a.key > b.key ? -1 : 0;
		});
	});
	const activityTags = $derived.by(() => {
		const seen = new Set<string>();
		for (const row of recordedActivity) seen.add(row.category);
		return [...seen].sort((a, b) => a.localeCompare(b));
	});
	const filteredActivity = $derived(
		activityFilter
			? recordedActivity.filter((row) => row.category === activityFilter)
			: recordedActivity
	);
	const activityPageCount = $derived(
		Math.max(1, Math.ceil(filteredActivity.length / ACTIVITY_PAGE))
	);
	const activityRows = $derived.by(() => {
		const page = Math.min(activityPage, activityPageCount);
		const start = (page - 1) * ACTIVITY_PAGE;
		return filteredActivity.slice(start, start + ACTIVITY_PAGE);
	});

	function showActivity(tag: string | null) {
		activityFilter = tag;
		activityPage = 1;
	}
</script>

<div class="space-y-6">
	<div class="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
		<Card.Root glass>
			<Card.Content>
				<p class="text-muted-foreground text-xs font-medium uppercase tracking-wide">Account</p>
				<div class="mt-2 flex flex-wrap items-center gap-1.5">
					<StatusBadge tone={profile.user.enabled ? 'success' : 'neutral'}>
						{profile.user.enabled ? 'Active' : 'Disabled'}
					</StatusBadge>
					{#if profile.user.types.includes('staff') && profile.user.is_adviser}
						<StatusBadge tone="brand">Adviser</StatusBadge>
					{/if}
				</div>
				{#if profile.user.job_title}
					<p class="text-muted-foreground mt-2 text-sm">{profile.user.job_title}</p>
				{/if}
			</Card.Content>
		</Card.Root>
		<Card.Root glass>
			<Card.Content>
				<p class="text-muted-foreground text-xs font-medium uppercase tracking-wide">
					Last sign-in
				</p>
				<p class="mt-2 text-sm font-medium">{dateTime(profile.user.last_sign_in)}</p>
			</Card.Content>
		</Card.Root>
		<Card.Root glass>
			<Card.Content>
				<p class="text-muted-foreground text-xs font-medium uppercase tracking-wide">Two-factor</p>
				<p class="mt-2 text-sm font-medium">
					{profile.security.available
						? profile.security.mfa_enrolled
							? `Enrolled (${profile.security.factor_count ?? 1})`
							: 'Not enrolled'
						: 'Unavailable'}
				</p>
			</Card.Content>
		</Card.Root>
		<Card.Root glass>
			<Card.Content>
				<p class="text-muted-foreground text-xs font-medium uppercase tracking-wide">
					Live sessions
				</p>
				<p class="mt-2 text-sm font-medium">
					{profile.security.available
						? (profile.security.active_sessions ?? 0)
						: profile.security.sessions
							? profile.security.sessions.length
							: 'Unavailable'}
				</p>
			</Card.Content>
		</Card.Root>
	</div>

	<section>
		<SectionHeading title="Signed-in sessions" count={profile.security.sessions?.length} />
		<Card.Root glass>
			<Card.Content>
				{#if profile.security.sessions}
					<LiveSessionList
						sessions={profile.security.sessions}
						listed={true}
						canRevoke={canRevokeSessions}
					/>
				{:else if profile.security.available}
					<LiveSessionList sessions={[]} listed={true} canRevoke={canRevokeSessions} />
				{:else}
					<p class="text-muted-foreground text-sm">Sessions could not be loaded.</p>
				{/if}
			</Card.Content>
		</Card.Root>
	</section>

	<section>
		<SectionHeading title="Portal access" count={profile.memberships.length} />
		<Card.Root glass>
			<Card.Content>
				{#if profile.memberships.length === 0}
					<EmptyState compact icon={ShieldCheckIcon} title="No portal access" />
				{:else}
					<div class="divide-y">
						{#each profile.memberships as membership (`${membership.portal}:${membership.status}`)}
							<div
								class="flex flex-wrap items-start justify-between gap-3 py-3 first:pt-0 last:pb-0"
							>
								<div>
									<p class="font-medium">{portalLabel(membership.portal)}</p>
									<p class="text-muted-foreground text-xs">
										Granted {dateTime(membership.created_at)}
									</p>
								</div>
								<div class="flex flex-wrap items-center justify-end gap-1.5">
									{#each membership.role_slugs as role (role)}
										<Badge variant="secondary">{humanise(role)}</Badge>
									{/each}
									<StatusBadge tone={membership.status === 'active' ? 'success' : 'warning'}>
										{humanise(membership.status)}
									</StatusBadge>
								</div>
							</div>
						{/each}
					</div>
				{/if}
			</Card.Content>
		</Card.Root>
	</section>

	<section>
		<SectionHeading title="Effective permissions" count={permissionGroups.length} />
		<Card.Root glass>
			<Card.Content>
				{#if permissionGroups.length}
					<div class="flex flex-wrap gap-1.5">
						{#each permissionGroups as permission (permission)}
							<Badge variant="outline" class="max-w-none" title={permission}>{permission}</Badge>
						{/each}
					</div>
				{:else}
					<p class="text-muted-foreground text-sm">This identity has no application permissions.</p>
				{/if}
			</Card.Content>
		</Card.Root>
	</section>

	<section>
		<SectionHeading
			title={accessMode === 'client' ? 'Client access' : 'Assigned client book'}
			count={access.length}
		/>
		<Card.Root glass>
			<Card.Content>
				{#if access.length === 0}
					<EmptyState
						compact
						icon={UsersIcon}
						title={accessMode === 'client'
							? 'Not linked to a client record'
							: 'No clients assigned'}
						description={accessMode === 'client'
							? 'They may be able to sign in, but their client scope resolves to nothing.'
							: 'Firm-wide visibility may still come from a contacts:read_all permission.'}
					/>
				{:else}
					{#if access.length > SEARCHABLE_ACCESS_ROWS}
						<ListingToolbar
							bind:query={accessQuery}
							placeholder="Search by name or email"
							class="mb-3"
						/>
					{/if}
					{#if filteredAccess.length === 0}
						<!-- Distinct from the EMPTY STATE above: there ARE rows, none of them match. -->
						<p class="text-muted-foreground text-sm">No clients match “{accessQuery}”.</p>
					{:else}
						<DataTable
							columns={accessColumns}
							rows={filteredAccess}
							bind:sort={accessSort}
							getKey={(item) => item.uid}
							label={accessMode === 'client' ? 'Client access' : 'Assigned client book'}
							empty="No clients assigned."
						>
							{#snippet cell(item, column)}
								{#if column.key === 'client'}
									{@const name = item.name ?? item.email ?? 'Unnamed client'}
									<TruncatedText text={name} href={clientHref(item.uid)} class="font-medium" />
									{#if item.email}<div class="text-muted-foreground text-xs">
											{item.email}
										</div>{/if}
								{:else if column.key === 'access'}
									<Badge variant="outline" class="font-normal"
										>{item.capacity
											? humanise(item.capacity)
											: (clientTeamRoleLabel(item.role) ?? 'Assigned')}</Badge
									>
								{:else if column.key === 'account_count'}
									<span class="tabular-nums">{item.account_count}</span>
								{/if}
							{/snippet}
						</DataTable>
					{/if}
				{/if}
			</Card.Content>
		</Card.Root>
	</section>

	<section>
		<SectionHeading title="Recorded activity" count={profile.activity.recorded_actions} />
		<Card.Root glass>
			<Card.Content class="space-y-4">
				<ActivityHeatmap days={profile.activity_days ?? []} />
				{#if recordedActivity.length === 0}
					<p class="text-muted-foreground text-sm">
						No sign-ins, page views, or successful actions recorded.
					</p>
				{:else}
					<div class="flex flex-wrap gap-1.5" role="group" aria-label="Activity">
						<Button
							type="button"
							size="xs"
							variant={activityFilter ? 'outline' : 'secondary'}
							class="rounded-full"
							aria-pressed={!activityFilter}
							onclick={() => showActivity(null)}>All</Button
						>
						{#each activityTags as tag (tag)}
							<Button
								type="button"
								size="xs"
								variant={activityFilter === tag ? 'secondary' : 'outline'}
								class="rounded-full"
								aria-pressed={activityFilter === tag}
								onclick={() => showActivity(tag)}>{tag}</Button
							>
						{/each}
					</div>
					{#if activityRows.length === 0}
						<p class="text-muted-foreground text-sm">No events match this filter.</p>
					{:else}
						<div class="divide-y">
							{#each activityRows as row (row.key)}
								<div class="flex items-baseline justify-between gap-3 py-1.5 first:pt-0 last:pb-0">
									<p class="min-w-0 truncate text-sm" title={row.title}>
										{row.sentence}
									</p>
									<time class="text-muted-foreground shrink-0 text-xs">
										{dateTime(row.created_at)}
									</time>
								</div>
							{/each}
						</div>
					{/if}
					<Pagination bind:page={activityPage} pageCount={activityPageCount} />
				{/if}
			</Card.Content>
		</Card.Root>
	</section>
</div>
