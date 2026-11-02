<script lang="ts">
	import {
		Badge,
		Button,
		buttonVariants,
		DataTable,
		EmptyState,
		LoadErrorBanner,
		Segmented,
		StatusBadge,
		TruncatedText,
		DropdownMenu,
		CreateTemplateDialog,
		type DataTableColumn,
		type DataTableSort,
		type NewTemplateInput
	} from '@graphene/ui';
	import { page } from '$app/state';
	import {
		ListingOverflow,
		ListingToolbar,
		matches,
		nextUrlFromStart,
		type FilterDef
	} from '@graphene/ui/book/listing';
	import { goto, invalidateAll } from '$app/navigation';
	import { deserialize } from '$app/forms';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import LayoutTemplateIcon from '@lucide/svelte/icons/layout-template';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import FileOutputIcon from '@lucide/svelte/icons/file-output';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import ArchiveIcon from '@lucide/svelte/icons/archive';
	import ArchiveRestoreIcon from '@lucide/svelte/icons/archive-restore';
	import CopyIcon from '@lucide/svelte/icons/copy';
	import {
		TEMPLATE_STATUS_META,
		DOC_TYPE_META,
		type Template,
		type DocumentType
	} from '@graphene/ui';
	import { formatDate } from '@graphene/ui';
	import { EmailTemplatesListing } from '@graphene/ui/email-editor';
	import { enhance } from '$app/forms';
	import ListingFrame from '$lib/components/listing-frame.svelte';
	import TemplateKindTabs from '$lib/components/templates/template-kind-tabs.svelte';
	import type { PageData } from './$types';

	let { data }: { data: PageData } = $props();

	/**
	 * Which kind is on screen. Read from the query so the view is linkable and survives a reload.
	 * Switching kind re-runs the loader (`?kind=` is part of the URL it reads), so Documents never
	 * waits on the email catalogue, and flipping to Emails fills that tab.
	 *
	 * An unreadable kind falls back to the other rather than rendering an empty surface: a caller
	 * holding only `email_templates:read` who lands on `/templates` gets Emails.
	 */
	/** The emails listing's URLs. Its editor and the token-safe proxies live under
	 *  `/templates/emails/{uid}/…` — a real route, because opening the editor IS a page load; only
	 *  the kind switch has to be instant. */
	const emailLinks = {
		edit: (uid: string) => `/templates/emails/${uid}/edit`,
		// The token-safe versions proxy (+server.ts beside the editor), not the API.
		versions: (uid: string) => `/templates/emails/${uid}/versions`,
		// So version history can SHOW a version, not only restore it.
		preview: (uid: string) => `/templates/emails/${uid}/preview`
	};

	const kind = $derived.by(() => {
		const asked = page.url.searchParams.get('kind');
		if (asked === 'emails' && data.canEmails) return 'emails';
		// An unreadable or unknown kind falls back to the other rather than rendering an empty
		// surface. Datasets are not a fallback any more: they are their own route, so a caller who can
		// read only those is sent there by the button rather than landing on a kind they cannot see.
		if (data.canDocuments) return 'documents';
		if (data.canEmails) return 'emails';
		return 'documents';
	});
	const templates = $derived(data.templates);
	// Real datasets feed the create wizard's dataset step (forms feed its input-form step); the chosen
	// dataset codes / input-form code persist on the template.
	const datasets = $derived(data.datasets);

	// Live/Archived is a top-level view (like documents), not a status filter — the two slices load
	// together so switching is instant. Rows below source from whichever view is active.
	type ViewMode = 'live' | 'archived';
	let view = $state<ViewMode>('live');
	const rows = $derived(view === 'archived' ? data.archivedTemplates : data.templates);

	// Archive/restore go through the form actions; a fetch (not use:enhance) lets us keep the kebab
	// lightweight, then invalidate so both slices refresh. `busyUid` disables the row mid-flight.
	let busyUid = $state<string | null>(null);
	let actionError = $state('');
	async function runAction(action: 'archive' | 'unarchive', uid: string) {
		busyUid = uid;
		actionError = '';
		try {
			const body = new FormData();
			body.append('uid', uid);
			const res = await fetch(`?/${action}`, { method: 'POST', body });
			const result = deserialize(await res.text());
			if (result.type === 'success') {
				await invalidateAll();
			} else {
				actionError =
					result.type === 'failure'
						? ((result.data?.error as string) ?? 'Could not update the template.')
						: 'Could not update the template.';
			}
		} catch {
			actionError = 'Could not update the template. Please try again.';
		} finally {
			busyUid = null;
		}
	}

	// Duplicate → server clones the template into a fresh draft and returns its uid; open the copy in
	// the editor (like create), so the user lands ready to edit.
	async function duplicateTemplate(uid: string) {
		busyUid = uid;
		actionError = '';
		try {
			const body = new FormData();
			body.append('uid', uid);
			const res = await fetch('?/duplicate', { method: 'POST', body });
			const result = deserialize(await res.text());
			if (result.type === 'success' && result.data?.uid) {
				await invalidateAll();
				await goto(`/templates/${result.data.uid as string}/edit`);
			} else {
				actionError =
					result.type === 'failure'
						? ((result.data?.error as string) ?? 'Could not duplicate the template.')
						: 'Could not duplicate the template.';
			}
		} catch {
			actionError = 'Could not update the template. Please try again.';
		} finally {
			busyUid = null;
		}
	}

	// Returns {ok,error} so the dialog can keep the wizard open + show the error inline on failure
	// (rather than closing + wiping every step). On success we invalidate + navigate to the editor.
	async function handleCreate(input: NewTemplateInput): Promise<{ ok: boolean; error?: string }> {
		try {
			const body = new FormData();
			body.append('input', JSON.stringify(input));
			const res = await fetch('?/create', { method: 'POST', body });
			const result = deserialize(await res.text());
			if (result.type === 'success' && result.data?.uid) {
				// Refresh the cached templates list so the new template shows on return (the fetch above
				// bypasses use:enhance, which would otherwise have invalidated for us).
				await invalidateAll();
				await goto(`/templates/${result.data.uid as string}/edit`);
				return { ok: true };
			}
			return {
				ok: false,
				error:
					result.type === 'failure'
						? ((result.data?.error as string) ?? 'Could not create the template.')
						: 'Could not create the template.'
			};
		} catch {
			// Network failure / unparseable response: keep the wizard open with a retry message rather
			// than throwing (which would freeze it on "Creating…").
			return { ok: false, error: 'Could not update the template. Please try again.' };
		}
	}

	// No status filter. The page header already states the split ("N published · M in draft") and
	// every row carries its own Status cell, so a dropdown over two values narrowed a list that
	// already showed the answer.
	let typeFilter = $state<string[]>([]);
	let sort = $state<DataTableSort | null>(null);
	// Seeded once from the URL, then owned by the toolbar — the search navigates with
	// `replaceState`, so no history entry carries a different `q` to fall out of step with.
	// svelte-ignore state_referenced_locally
	let searchQuery = $state(data.query ?? '');
	/** The search is the URL's, so the API answers it across every template rather than across the
	 *  rows already in hand. The other filters on this page stay local: they run over a complete
	 *  set, and narrowing one page of a list while calling it the whole is the failure this avoids.
	 *
	 *  `ListingToolbar`'s SERVER mode does the debouncing, which is why there is no timer here. */
	function onSearch(term: string): void {
		searchQuery = term;
		goto(nextUrlFromStart(page.url, { q: term }), {
			keepFocus: true,
			noScroll: true,
			replaceState: true
		});
	}
	let createOpen = $state(false);

	// Derive the type pills from the document types actually present in the active view so a filter
	// can never return an empty list (previously "Valuation"/"Statement" were shown with no templates).
	// DOC_TYPE_META key order gives a stable, sensible ordering; labels come from the same map.
	const typeFilters = $derived(
		(Object.keys(DOC_TYPE_META) as DocumentType[])
			.filter((dt) => rows.some((t) => t.documentType === dt))
			.map((dt) => ({ value: dt as string, label: DOC_TYPE_META[dt].label }))
	);
	// One type offers no choice, so no filter is offered.
	const filterDefs = $derived<FilterDef[]>(
		typeFilters.length > 1 ? [{ key: 'type', label: 'Type', options: typeFilters }] : []
	);
	const barValue = $derived<Record<string, string[]>>(
		typeFilter.length ? { type: typeFilter } : {}
	);

	const filtered = $derived(
		rows.filter((t) => {
			if (!matches(typeFilter, t.documentType)) return false;
			if (searchQuery.trim()) {
				const q = searchQuery.toLowerCase();
				if (!t.name.toLowerCase().includes(q) && !t.code.toLowerCase().includes(q)) return false;
			}
			return true;
		})
	);

	const hasFilters = $derived(searchQuery !== '' || typeFilter.length > 0);
	function clearFilters() {
		searchQuery = '';
		typeFilter = [];
	}

	const columns: DataTableColumn<Template>[] = [
		{
			key: 'name',
			header: 'Name',
			sortable: true,
			sortDefault: true,
			headerClass: 'w-[34%]',
			sortValue: (t) => t.name
		},
		{
			key: 'type',
			header: 'Type',
			sortable: true,
			headerClass: 'w-[14%]',
			sortValue: (t) => DOC_TYPE_META[t.documentType].label
		},
		{
			key: 'version',
			header: 'Version',
			sortable: true,
			align: 'center',
			defaultDir: 'desc',
			hideBelowWidth: 'xl',
			headerClass: 'w-[10%]',
			sortValue: (t) => t.currentVersionNumber ?? 0
		},
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-[12%]',
			sortValue: (t) => t.currentVersionStatus ?? 'draft'
		},
		{
			key: 'createdBy',
			header: 'Created by',
			sortable: true,
			hideBelowWidth: '2xl',
			headerClass: 'w-[16%]',
			cellClass: 'truncate',
			sortValue: (t) => t.createdBy
		},
		{
			key: 'createdAt',
			header: 'Created',
			sortable: true,
			defaultDir: 'desc',
			hideBelowWidth: 'lg',
			headerClass: 'w-[10%]',
			// The ISO string sorts correctly; the cell renders the human form.
			sortValue: (t) => t.createdAt
		},
		{ key: 'actions', header: '', align: 'right', headerClass: 'w-[4%]' }
	];

	function openGenerate(tpl: Template, e: Event) {
		e.stopPropagation();
		goto(`/documents/create?template=${tpl.uid}`);
	}
</script>

{#snippet createFirstTemplate()}
	<Button size="sm" onclick={() => (createOpen = true)}>
		<PlusIcon class="mr-1.5 size-4" />
		New template
	</Button>
{/snippet}

<!-- In the toolbar row on both views. It renders nothing when only one kind is readable. -->
{#snippet viewTabs()}
	<TemplateKindTabs {kind} canDocuments={data.canDocuments} canEmails={data.canEmails} />
{/snippet}

<!-- Datasets belong to the Documents view; the email view offers this door only to a reader who cannot open it. -->
{#snippet datasetsItem()}
	<DropdownMenu.Item>
		{#snippet child({ props })}
			<a {...props} href="/templates/datasets">Datasets</a>
		{/snippet}
	</DropdownMenu.Item>
{/snippet}

{#snippet listingActions()}
	<ListingOverflow>
		{#if data.canManage}
			<DropdownMenu.Item onclick={() => (createOpen = true)}>New template</DropdownMenu.Item>
		{/if}
		{#if data.canDatasets}{@render datasetsItem()}{/if}
	</ListingOverflow>
{/snippet}

{#if kind === 'emails'}
	<!-- A thin mount of the shared listing (`@graphene/ui/email-editor`), which internal operations
	     mounts too. Everything app-specific stays here: this portal's URL shapes, its own permission
	     family (`email_templates:write` -> `data.canManageEmails`), and SvelteKit's
	     `enhance`/`deserialize`/`goto`/`invalidateAll` — virtuals pkgs/ui cannot import.

	     Its write actions are prefixed `email*` because this route now owns BOTH kinds' actions and
	     both legitimately have a `create`. The listing takes its action names as data, so the prefix
	     is invisible to it. -->
	<EmailTemplatesListing
		templates={data.emailTemplates}
		catalog={data.emailCatalog}
		loadError={data.loadError}
		links={emailLinks}
		showCodes
		menuItems={data.canDatasets && !data.canDocuments ? datasetsItem : undefined}
		onretry={() => invalidateAll()}
		deleteConsequence="This email then goes out with Graphene’s built-in default."
		loadTemplateUsage={async () => {
			const res = await fetch('/templates/emails/usage');
			if (!res.ok) return {};
			return (await res.json()) as Record<string, string[]>;
		}}
		emptyDescription="Create one to override a default email with your firm’s own branding and copy."
		actions={data.canManageEmails
			? {
					enhance,
					deserialize,
					create: '?/emailCreate',
					update: '?/emailUpdate',
					delete: '?/emailDelete',
					// No `loadDefaults`: a firm curates the templates it was given rather than re-seeding
					// itself from the shipped set, so that action exists only in internal operations.
					onmutated: invalidateAll,
					navigate: (href) => goto(href)
				}
			: null}
	>
		{#snippet tabs()}{@render viewTabs()}{/snippet}
	</EmailTemplatesListing>
{:else}
	<ListingFrame>
		{#snippet banner()}
			{#if data.loadError}
				<LoadErrorBanner message="Couldn't load your templates." onRetry={() => invalidateAll()} />
			{/if}

			{#if actionError}
				<p class="text-destructive text-xs">{actionError}</p>
			{/if}
		{/snippet}
		<ListingToolbar
			class="shrink-0"
			stacked
			query={searchQuery}
			onsearch={onSearch}
			placeholder="Search templates…"
			dirty={hasFilters}
			onclear={clearFilters}
			{filterDefs}
			filterValue={barValue}
			onfilters={(next) => (typeFilter = next.type ?? [])}
			actions={data.canManage || data.canDatasets ? listingActions : undefined}
		>
			{#snippet filters()}
				{@render viewTabs()}
				{#if data.archivedTemplates.length > 0 || view === 'archived'}
					<Segmented
						options={[
							{ value: 'live', label: 'Live' },
							{ value: 'archived', label: 'Archived' }
						]}
						value={view}
						onValueChange={(v) => (view = v as ViewMode)}
					/>
				{/if}
			{/snippet}
		</ListingToolbar>

		{#if filtered.length === 0 && !data.loadError}
			{#if hasFilters}
				<EmptyState
					icon={LayoutTemplateIcon}
					title="No templates match your filters"
					description="Try a different search or document type, or clear the filters to see every template."
					compact
					chromeless
				>
					{#snippet action()}
						<Button variant="link" size="sm" onclick={clearFilters}>Clear filters</Button>
					{/snippet}
				</EmptyState>
			{:else if view === 'archived'}
				<EmptyState
					icon={ArchiveIcon}
					title="Nothing archived"
					description="Archived templates move here, where you can view or restore them."
					compact
					chromeless
				/>
			{:else}
				<EmptyState
					icon={LayoutTemplateIcon}
					title="No templates yet"
					description="Create your first template to start generating documents."
					action={data.canManage ? createFirstTemplate : undefined}
					compact
					chromeless
				/>
			{/if}
		{:else if filtered.length > 0}
			<DataTable
				{columns}
				rows={filtered}
				bind:sort
				scroll
				fixed
				getKey={(t) => t.uid}
				label="Document templates"
				empty="No templates match your filters."
				rowClass={(t) => (busyUid === t.uid ? 'pointer-events-none opacity-50' : '')}
			>
				{#snippet cell(template, column)}
					{#if column.key === 'name'}
						<div class="min-w-0">
							<TruncatedText
								text={template.name}
								href={`/templates/${template.uid}`}
								class="font-medium"
							/>
							<p class="mt-0.5 truncate text-xs">
								ID: <span class="text-muted-foreground font-mono">{template.code}</span>
								{#if template.versionCount > 0}
									<span class="text-muted-foreground">
										· {template.versionCount}
										{template.versionCount === 1 ? 'version' : 'versions'}
									</span>
								{/if}
							</p>
						</div>
					{:else if column.key === 'type'}
						{@const dtm = DOC_TYPE_META[template.documentType]}
						<Badge class="{dtm.bgColor} {dtm.textColor}">{dtm.label}</Badge>
					{:else if column.key === 'version'}
						{#if template.currentVersionNumber}
							<span class="tabular-nums">v{template.currentVersionNumber}</span>
						{:else}
							<span class="text-muted-foreground">—</span>
						{/if}
					{:else if column.key === 'status'}
						{@const stm = template.currentVersionStatus
							? TEMPLATE_STATUS_META[template.currentVersionStatus]
							: TEMPLATE_STATUS_META.draft}
						<StatusBadge tone={stm.tone}>{stm.label}</StatusBadge>
					{:else if column.key === 'createdBy'}
						<TruncatedText text={template.createdBy} />
					{:else if column.key === 'createdAt'}
						<span class="text-muted-foreground text-xs">{formatDate(template.createdAt)}</span>
					{:else if column.key === 'actions'}
						{@const isArchived = view === 'archived'}
						<DropdownMenu.Root>
							<DropdownMenu.Trigger
								class={buttonVariants({ variant: 'ghost', size: 'icon' })}
								aria-label={`Actions for ${template.name}`}
							>
								<EllipsisIcon class="size-4" />
							</DropdownMenu.Trigger>
							<DropdownMenu.Content align="end" class="w-48">
								<DropdownMenu.Item onclick={() => goto(`/templates/${template.uid}`)}>
									<EyeIcon class="mr-2 size-4" />
									View
								</DropdownMenu.Item>
								{#if !isArchived}
									<DropdownMenu.Item onclick={() => goto(`/templates/${template.uid}/edit`)}>
										<PencilIcon class="mr-2 size-4" />
										Edit
									</DropdownMenu.Item>
									<DropdownMenu.Item
										onclick={(e: Event) => openGenerate(template, e)}
										disabled={template.currentVersionStatus !== 'published'}
									>
										<FileOutputIcon class="mr-2 size-4" />
										Generate document
									</DropdownMenu.Item>
									{#if data.canManage}
										<DropdownMenu.Separator />
										<DropdownMenu.Item onclick={() => void duplicateTemplate(template.uid)}>
											<CopyIcon class="mr-2 size-4" />
											Duplicate
										</DropdownMenu.Item>
										<DropdownMenu.Item
											class="text-destructive"
											onclick={() => void runAction('archive', template.uid)}
										>
											<ArchiveIcon class="mr-2 size-4" />
											Archive
										</DropdownMenu.Item>
									{/if}
								{:else if data.canManage}
									<DropdownMenu.Item onclick={() => void runAction('unarchive', template.uid)}>
										<ArchiveRestoreIcon class="mr-2 size-4" />
										Restore
									</DropdownMenu.Item>
								{/if}
							</DropdownMenu.Content>
						</DropdownMenu.Root>
					{/if}
				{/snippet}
			</DataTable>
		{/if}
	</ListingFrame>

	<CreateTemplateDialog
		bind:open={createOpen}
		{datasets}
		forms={data.forms}
		existingCodes={templates.map((t) => t.code)}
		oncreate={handleCreate}
	/>
{/if}
