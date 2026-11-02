<script lang="ts">
	// One listing behind both email-template pages, on the `book/clients` contract: typed props, hrefs from `links`, `actions` absent ⇒ read-only.
	import type { Snippet } from 'svelte';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import MailIcon from '@lucide/svelte/icons/mail';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import SettingsIcon from '@lucide/svelte/icons/settings-2';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { toast } from 'svelte-sonner';
	import { Button, buttonVariants } from '../../ui/button/index.js';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import {
		DataTable,
		type DataTableColumn,
		type DataTableSort
	} from '../../ui/data-table/index.js';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import { RecordId } from '../../ui/record-id/index.js';
	import * as Select from '../../ui/select/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import { Checkbox } from '../../ui/checkbox/index.js';
	import { BY_SUBJECT, SUBJECT_LABELS, type SubjectKind } from '../scopes.js';
	import PageHeader from '../../shell/page-header.svelte';
	import ListingOverflow from '../../book/listing/listing-overflow.svelte';
	import type { Enhance } from '../../book/clients/form-enhance.js';
	import TemplateSettingsDialog from '../template-settings-dialog.svelte';
	import VersionHistoryDialog from '../version-history-dialog.svelte';
	import { categoryFor, codeState, createBlocker } from './create-rules.js';
	import { emailTemplateState } from './template-state.js';
	import type {
		EmailCodeOption,
		EmailTemplateRow,
		EmailTemplatesListingActions,
		EmailTemplatesListingLinks
	} from './types.js';

	let {
		title,
		description,
		eyebrow = undefined,
		back = undefined,
		templates,
		catalog = [],
		loadError = false,
		links,
		actions = null,
		deleteConsequence,
		templateUsage = {},
		loadTemplateUsage = undefined,
		emptyTitle = 'No email templates yet',
		emptyDescription = 'Create one to override a default email with your own branding and copy.',
		showCodes = false,
		notices = undefined,
		tabs = undefined,
		menuItems = undefined,
		onretry = undefined
	}: {
		/** Absent ⇒ listing mode: no PageHeader, page acts in a kebab, the card fills the viewport. */
		title?: string;
		description?: string;
		eyebrow?: string;
		back?: { href: string; label: string };
		templates: EmailTemplateRow[];
		/** The send-path codes: they feed the type picker AND flag rows nothing will ever send. */
		catalog?: EmailCodeOption[];
		loadError?: boolean;
		links: EmailTemplatesListingLinks;
		/** Present ⇒ write mode. Absent ⇒ read-only (see `./types.ts`). */
		actions?: EmailTemplatesListingActions | null;
		/** The delete confirm's one consequence sentence — it differs for a firm and the model preset. */
		deleteConsequence: string;
		/** `code` → notification rules that render it; the delete confirm lists them. Absent ⇒ no warning. */
		templateUsage?: Record<string, string[]>;
		/** Fetch the usage map when the delete confirm opens, so first paint of the listing does not
		 *  wait on notification rules. Result is merged into `templateUsage` for this session. */
		loadTemplateUsage?: () => Promise<Record<string, string[]>>;
		emptyTitle?: string;
		emptyDescription?: string;
		/** Codes are noise to a firm but the subject for an operator curating what a new firm clones. */
		showCodes?: boolean;
		notices?: Snippet;
		/**
		 * Page-level sub-navigation, rendered between the head and the content — the platform's
		 * Documents/Emails switch, which sits on both template listings and so cannot live in either
		 * one's own markup.
		 *
		 * Separate from `notices` on purpose: a notice is a transient statement ABOUT the page, a tab
		 * strip is part of its chrome, and the two must not be able to displace each other. A mount
		 * with one kind of template (internal operations) passes neither.
		 */
		tabs?: Snippet;
		/** Listing mode only: the caller's own `DropdownMenu.Item`s, appended to the kebab. */
		menuItems?: Snippet;
		onretry?: () => void;
	} = $props();

	// `use:` needs a plain identifier; the noop only satisfies types (every form sits inside `{#if writes}`).
	const enhanceForm: Enhance = $derived(actions?.enhance ?? (() => {}));

	// A second name for `actions`: `PageHeader`'s slot is `{#snippet actions()}`, which would shadow the prop.
	const writes = $derived(actions);

	let showCreate = $state(false);

	// The only way back from an unwanted edit (there is no "reset to default"), so it is offered read-only too; the dialog withholds Restore without `canManage`.
	let historyOpen = $state(false);
	let historyTemplate = $state<{ uid: string; name: string } | null>(null);
	function openHistory(t: EmailTemplateRow): void {
		historyTemplate = { uid: t.uid, name: t.name };
		historyOpen = true;
	}

	// Without a metadata edit a mistyped code needs delete-and-recreate, and on the model preset that propagates — a new firm clones the codes verbatim.
	let settingsOpen = $state(false);
	let settingsTemplate = $state<EmailTemplateRow | null>(null);
	function openSettings(t: EmailTemplateRow): void {
		settingsTemplate = t;
		settingsOpen = true;
	}

	// Irreversible, so the confirm is destructive and spells out what goes; the menu item can only name the act.
	let deleteOpen = $state(false);
	let deleteTarget = $state<EmailTemplateRow | null>(null);
	let fetchedUsage = $state<Record<string, string[]> | null>(null);
	const usage = $derived(fetchedUsage ?? templateUsage);
	function askDelete(t: EmailTemplateRow): void {
		deleteTarget = t;
		deleteOpen = true;
		if (!loadTemplateUsage || fetchedUsage) return;
		void loadTemplateUsage()
			.then((map) => {
				fetchedUsage = map;
			})
			.catch(() => {
				fetchedUsage = {};
			});
	}

	let busyUid = $state<string | null>(null);
	/** The API refuses a delete that would break a send (409) and its `detail` NAMES the journey or code to fix, so it is shown as it stands and held on screen long enough to read — a generic "could not delete" would leave the author with nothing to act on. */
	async function removeTemplate(t: EmailTemplateRow): Promise<void> {
		if (!actions) return;
		busyUid = t.uid;
		try {
			const body = new FormData();
			body.append('uid', t.uid);
			const res = await fetch(actions.delete, { method: 'POST', body });
			const result = actions.deserialize(await res.text());
			if (result.type === 'success') {
				await actions.onmutated();
				toast.success(`“${t.name}” deleted.`);
			} else {
				const refusal = result.type === 'failure' ? (result.data?.error as string) : undefined;
				toast.error(refusal ?? 'Could not delete the email template.', {
					duration: refusal ? 10000 : undefined
				});
			}
		} catch {
			toast.error('Could not load email templates. Try again.');
		} finally {
			busyUid = null;
		}
	}

	// The chosen catalogue type drives the code, "Custom" reveals free text for a not-yet-wired trigger; category is derived, not asked (`categoryFor` in ./create-rules.ts).
	let selectedType = $state('');
	let prevType = $state('');
	let name = $state('');
	/** What the new email is about. Ordered as `BY_SUBJECT` declares them, so the row never reshuffles. */
	const SUBJECT_ORDER = Object.keys(BY_SUBJECT) as SubjectKind[];
	let subjects = $state<SubjectKind[]>([]);
	let customCode = $state('');

	const hasCatalog = $derived(catalog.length > 0);
	const chosen = $derived(catalog.find((c) => c.code === selectedType));
	const effectiveCategory = $derived(categoryFor(chosen?.category));
	// Codes the send path resolves — a row whose code isn't one of these is authored but never sent.
	const knownCodes = $derived(new Set(catalog.map((c) => c.code)));

	// A type already held by a template is DROPPED, not listed and disabled: with all six built-ins added that is six dead rows above the one option that works, and a deleted template holds nothing, so there is no such row to keep.
	const catalogState = $derived(
		new Map(catalog.map((c) => [c.code, codeState(c.code, templates)]))
	);
	const offeredTypes = $derived(catalog.filter((c) => !catalogState.get(c.code)?.taken));
	const noTypesLeft = $derived(hasCatalog && offeredTypes.length === 0);

	// No picker on screen ⇒ custom: the code can only come from free text, and `createBlocker`'s `typeChosen` must not refuse a control that was never rendered.
	const isCustom = $derived(selectedType === '__custom__' || !hasCatalog || noTypesLeft);
	const effectiveCode = $derived((isCustom ? customCode : (chosen?.code ?? '')).trim());

	const chosenCodeState = $derived(codeState(effectiveCode, templates));
	const blocker = $derived(
		createBlocker({
			typeChosen: !!selectedType || isCustom,
			isCustom,
			code: effectiveCode,
			name,
			state: chosenCodeState
		})
	);

	// Split on the stored category: `transactional` is platform account/security email, not a notification; everything else is sent by a feature.
	const GROUPS = [
		{
			key: 'system',
			label: 'System',
			description: 'Account access and security — sent by the platform, always on.',
			match: (c: string) => c === 'transactional'
		},
		{
			key: 'automatic',
			label: 'Notifications',
			description: 'Sent automatically when something happens — documents, requests, tasks.',
			match: (c: string) => c !== 'transactional'
		}
	] as const;
	// A row carries only its code, so the category comes from the catalogue; unknown ⇒ the API's documented default (notification ⇒ Automatic).
	const codeCategory = $derived(new Map(catalog.map((c) => [c.code, c.category])));

	// Only templates that exist are listed — no phantom "Default" rows; a code with none still falls back to the shipped default at send time (`email_render.render_or_default`).
	type Listed = EmailTemplateRow;

	// Fixed Status/Actions widths line the group tables up; Email takes the rest.
	let sort = $state<DataTableSort | null>(null);
	const columns: DataTableColumn<Listed>[] = [
		{ key: 'name', header: 'Email', sortable: true, cellClass: 'whitespace-normal align-top' },
		{
			key: 'status',
			header: 'Status',
			sortable: true,
			headerClass: 'w-40',
			cellClass: 'align-top',
			sortValue: (t) => emailTemplateState(t).label
		},
		{ key: 'actions', header: '', align: 'right', headerClass: 'w-32', cellClass: 'align-top' }
	];

	const groupedRows = $derived(
		GROUPS.map((g) => ({
			...g,
			items: templates.filter((t) => g.match(categoryFor(codeCategory.get(t.code))))
		})).filter((g) => g.items.length > 0)
	);

	/**
	 * Which group is on screen. The two were stacked, so a firm scrolled past the platform's
	 * always-on account email to reach the notices it actually authors — and the further apart they
	 * are, the more the second reads as an afterthought. They are different KINDS of email (one the
	 * platform sends and a firm only re-skins, one a firm's own features raise), so choosing between
	 * them is a view.
	 *
	 * Local state, not a URL param: unlike the Documents/Emails switch one level up, nothing has to
	 * link to it — no nav row targets a group, and it is not worth a history entry. This is the
	 * Live/Archived toggle's arrangement.
	 *
	 * `null` until chosen, resolving to the FIRST group that has rows: deterministic, and it keeps
	 * the order the stacked layout read in. A group with no rows is already filtered out, so a firm
	 * holding only notifications never sees an empty System tab — and with one group left the switch
	 * does not render at all, because a one-option toggle offers no choice.
	 */
	let groupKey = $state<string | null>(null);
	const activeGroup = $derived(
		groupedRows.find((g) => g.key === groupKey) ?? groupedRows[0] ?? null
	);

	/** The two facts every surface must state — what goes, and that the code is free again — then the
	 *  mount's own consequence sentence, and that one ONLY for a code the send path knows.
	 *
	 *  The mount's sentence says what gets sent in this template's place, which presumes there is
	 *  something to send: a code the catalogue does not carry has nothing behind it and nothing that
	 *  sends it, so appending that sentence to a firm's `test` template promised a substitute that
	 *  does not exist. */
	const deleteDescription = $derived(
		deleteTarget
			? `“${deleteTarget.name}” and its whole version history are deleted for good, and the code ${deleteTarget.code} becomes free to use again.${knownCodes.has(deleteTarget.code) ? ` ${deleteConsequence}` : ''}`
			: deleteConsequence
	);

	// Not the app's `ActionData`: that survives to the next submit, so "already exists" would stay on screen against the empty code field that fixed it.
	let createError = $state<string | null>(null);
	function clearCreateError(): void {
		createError = null;
	}

	// Keyed on the selection CHANGING, so the auto-filled name doesn't clobber a later name edit.
	$effect(() => {
		if (selectedType !== prevType) {
			prevType = selectedType;
			name = chosen ? chosen.label : ''; // "Custom code…" — drop the stale auto-filled name
			clearCreateError();
		}
	});

	let defaultsFormEl = $state<HTMLFormElement | null>(null);

	function toggleCreate(): void {
		if (showCreate) showCreate = false;
		else openCreate();
	}

	function openCreate(): void {
		selectedType = '';
		prevType = '';
		name = '';
		customCode = '';
		subjects = [];
		createError = null;
		showCreate = true;
	}
</script>

{#snippet defaultsForm(listing: boolean)}
	{#if writes?.loadDefaults}
		<!-- Admin-only seeding: the firm's surface passes no such action, so the asymmetry IS the contract rather than a flag. -->
		<form
			method="POST"
			action={writes.loadDefaults}
			class={listing ? 'hidden' : undefined}
			bind:this={defaultsFormEl}
			use:enhanceForm={() =>
				async ({ update }) => {
					await update({ reset: false });
					await writes?.onmutated();
				}}
		>
			{#if !listing}
				<Button type="submit" variant="outline">
					<SparklesIcon class="size-4" /> Load defaults
				</Button>
			{/if}
		</form>
	{/if}
{/snippet}

{#snippet createForm(listing: boolean)}
	{#if showCreate && writes}
		<Card class={listing ? 'shrink-0 md:max-h-[70%] md:overflow-y-auto' : undefined}>
			<CardContent>
				<form
					method="POST"
					action={writes.create}
					use:enhanceForm={() =>
						async ({ result }) => {
							// Handled here, not via `update()`, so a failure lands in `createError` above.
							if (result.type === 'success') {
								const uid = (result.data as { uid?: string } | undefined)?.uid;
								createError = null;
								showCreate = false;
								if (uid) await writes?.navigate(links.edit(uid));
								else await writes?.onmutated();
							} else if (result.type === 'redirect') {
								// Both create actions redirect rather than fail on write scope revoked mid-session, so follow it — "could not create" would name the wrong cause.
								const to = (result as { location?: string }).location;
								if (to) await writes?.navigate(to);
							} else {
								createError =
									result.type === 'failure'
										? ((result.data?.error as string) ?? 'Could not create the email template.')
										: 'Could not create the email template.';
							}
						}}
					class="max-w-2xl space-y-5"
				>
					{#if hasCatalog && !noTypesLeft}
						<div class="grid gap-1.5">
							<Label>Email type</Label>
							<Select.Root type="single" bind:value={selectedType}>
								<Select.Trigger class="w-full">
									{#if chosen}{chosen.label}{:else if isCustom}Custom code{:else}
										Choose an email type…
									{/if}
								</Select.Trigger>
								<Select.Content>
									<!-- Only codes no template already holds: the unique key would refuse the insert. Change one via the holder's own Settings, or delete it. -->
									{#each offeredTypes as c (c.code)}
										<Select.Item value={c.code}>
											<span class="truncate">{c.label}</span>
										</Select.Item>
									{/each}
									<Select.Item value="__custom__">Custom code…</Select.Item>
								</Select.Content>
							</Select.Root>
							{#if chosen}
								<p class="text-muted-foreground text-xs">{chosen.description}</p>
							{:else if isCustom}
								<p class="text-muted-foreground text-xs">
									Advanced. This email type is not in use yet.
								</p>
							{:else}
								<p class="text-muted-foreground text-xs">
									The event this email is sent for. Only these codes are resolved by the send path.
								</p>
							{/if}
						</div>
					{/if}

					{#if selectedType || isCustom}
						<!-- Code + Name share a row (same height); Description spans full width so a
						     textarea is not paired with a single-line field. Long Code helpers span both. -->
						<div class="grid gap-4 sm:grid-cols-2">
							{#if isCustom}
								<div class="grid gap-1.5">
									<Label for="et-code">Code</Label>
									<Input
										id="et-code"
										bind:value={customCode}
										oninput={clearCreateError}
										aria-invalid={blocker?.kind === 'conflict' ? 'true' : undefined}
										placeholder="invite"
									/>
									{#if blocker?.kind === 'conflict'}
										<p class="text-destructive text-xs">{blocker.message}</p>
									{:else if !noTypesLeft}
										<p class="text-muted-foreground text-xs">
											The stable key the send path resolves.
										</p>
									{/if}
								</div>
							{/if}
							<div class="grid gap-1.5 {!isCustom ? 'sm:col-span-2' : ''}">
								<Label for="et-name">Name</Label>
								<Input
									id="et-name"
									name="name"
									bind:value={name}
									oninput={clearCreateError}
									placeholder="Client invitation"
								/>
							</div>
							{#if isCustom && noTypesLeft && blocker?.kind !== 'conflict'}
								<!-- With no picker on screen the field must say why the control an author saw last time is gone, or its absence reads as a bug. -->
								<p class="text-muted-foreground text-xs sm:col-span-2">
									Every built-in email type is already added. Enter a unique code for this custom
									email type. It will not be sent until it is put into use.
								</p>
							{/if}
							<div class="grid gap-1.5 sm:col-span-2">
								<Label for="et-desc">Description</Label>
								<Textarea id="et-desc" name="description" placeholder="Optional" rows={2} />
							</div>
						</div>
					{/if}

					<!-- WHAT THE EMAIL IS ABOUT. Asked HERE because it decides which event details the
					     context panel lists: an author who has not said cannot be shown a narrowed list, and
					     every field of every subject is the "way too many" state. None ticked is a real
					     answer — an email that talks only about the firm and the act. -->
					<div class="grid gap-2">
						<div class="grid gap-0.5">
							<span class="text-sm font-medium">What is this email about?</span>
							<p class="text-muted-foreground text-xs">
								Decides which details it can use. Leave all clear if it needs none.
							</p>
						</div>
						<div class="grid gap-2 sm:grid-cols-2">
							{#each SUBJECT_ORDER as kind (kind)}
								<label class="flex items-center gap-2 text-sm">
									<Checkbox
										name="subjects"
										value={kind}
										checked={subjects.includes(kind)}
										onCheckedChange={(on) => {
											subjects = on ? [...subjects, kind] : subjects.filter((k) => k !== kind);
										}}
									/>
									{SUBJECT_LABELS[kind]}
								</label>
							{/each}
						</div>
					</div>

					<!-- The API body still requires a category, but nothing at send time reads it, so it is derived rather than asked for (`categoryFor` in ./create-rules.ts). -->
					<input type="hidden" name="code" value={effectiveCode} />
					<input type="hidden" name="category" value={effectiveCategory} />

					{#if createError}
						<p class="text-destructive text-sm">{createError}</p>
					{/if}
					<div class="flex flex-wrap items-center gap-x-3 gap-y-2 border-t pt-4">
						<Button type="submit" disabled={!!blocker}>Create &amp; edit</Button>
						<Button type="button" variant="ghost" onclick={() => (showCreate = false)}>
							Cancel
						</Button>
						<!-- A disabled submit always states its reason, muted for `missing` (unfinished, not wrong); a `conflict` reaches here only on the catalogue path, which has no Code field to hang it on, from a stale load. -->
						{#if blocker && (blocker.kind === 'missing' || !isCustom)}
							<p
								class={blocker.kind === 'conflict'
									? 'text-destructive text-xs'
									: 'text-muted-foreground text-xs'}
							>
								{blocker.message}
							</p>
						{/if}
					</div>
				</form>
			</CardContent>
		</Card>
	{/if}
{/snippet}

{#snippet groupSwitch()}
	{#if templates.length > 0 && activeGroup}
		{#if groupedRows.length > 1}
			<Segmented
				options={groupedRows.map((g) => ({ value: g.key, label: g.label }))}
				value={activeGroup.key}
				label="Email kind"
				onValueChange={(v) => (groupKey = v)}
			/>
		{/if}
	{/if}
{/snippet}

{#snippet content(listing: boolean)}
	{#if templates.length === 0 && !loadError}
		<EmptyState
			icon={MailIcon}
			title={emptyTitle}
			description={emptyDescription}
			class={listing ? undefined : 'mx-auto mt-12 max-w-md'}
			compact={listing}
			chromeless={listing}
		/>
	{:else if templates.length > 0 && activeGroup}
		{#each [activeGroup] as group (group.key)}
			<section class={listing ? 'flex min-h-0 flex-1 flex-col gap-2' : 'space-y-2'}>
				<div>
					<!-- No heading: the switch above already names the group, and repeating it two rows
					     apart reads as two different things. The DESCRIPTION stays — it says what the
					     group IS, which the label alone cannot ("sent by the platform, always on"). -->
					<p class="text-muted-foreground text-xs">{group.description}</p>
				</div>
				<!-- No Created/Updated column: identical when seeded, and the API has no `updated_at`. -->
				<DataTable
					{columns}
					rows={group.items}
					bind:sort
					scroll={listing}
					fixed
					getKey={(t) => t.uid}
					label={`${group.label} email templates`}
					empty="No email templates in this group."
					rowClass={(t) => (busyUid === t.uid ? 'pointer-events-none opacity-50' : '')}
				>
					{#snippet cell(t: Listed, column: DataTableColumn<Listed>)}
						{#if column.key === 'name'}
							{@const linked = knownCodes.has(t.code)}
							<a class="font-medium hover:underline" href={links.edit(t.uid)}>{t.name}</a>
							{#if showCodes}
								<RecordId value={t.code} />
							{/if}
							{#if t.description}
								<p class="text-muted-foreground text-xs">{t.description}</p>
							{/if}
							{#if !linked}
								<p class="text-warning text-xs">
									Not in use. No notification currently sends <code>{t.code}</code>.
								</p>
							{/if}
						{:else if column.key === 'status'}
							{@const rowState = emailTemplateState(t)}
							<span class="flex items-center gap-2" title={rowState.hint}>
								<StatusBadge tone={rowState.tone}>{rowState.label}</StatusBadge>
								{#if t.current_version_number}
									<span class="text-muted-foreground text-xs tabular-nums">
										v{t.current_version_number}
									</span>
								{/if}
							</span>
						{:else if column.key === 'actions'}
							<div class="flex items-center justify-end gap-1">
								<Button href={links.edit(t.uid)} variant="outline" size="sm">
									{#if writes}
										<PencilIcon class="mr-1.5 size-4" /> Edit
									{:else}
										<EyeIcon class="mr-1.5 size-4" /> View
									{/if}
								</Button>

								<DropdownMenu.Root>
									<DropdownMenu.Trigger
										class={buttonVariants({ variant: 'ghost', size: 'icon' })}
										aria-label={`Actions for ${t.name}`}
									>
										<EllipsisIcon class="size-4" />
									</DropdownMenu.Trigger>
									<DropdownMenu.Content align="end" class="w-52">
										<!-- Read-only readers see history; the dialog hides Restore. -->
										<DropdownMenu.Item onclick={() => openHistory(t)}>
											<HistoryIcon class="mr-2 size-4" /> Version history
										</DropdownMenu.Item>
										{#if writes}
											<DropdownMenu.Item onclick={() => openSettings(t)}>
												<SettingsIcon class="mr-2 size-4" /> Settings
											</DropdownMenu.Item>
											<DropdownMenu.Separator />
											<DropdownMenu.Item variant="destructive" onclick={() => askDelete(t)}>
												<Trash2Icon class="mr-2 size-4" /> Delete permanently
											</DropdownMenu.Item>
										{/if}
									</DropdownMenu.Content>
								</DropdownMenu.Root>
							</div>
						{/if}
					{/snippet}
				</DataTable>
			</section>
		{/each}
	{/if}
{/snippet}

{#if title}
	<PageHeader {title} {description} {eyebrow} {back}>
		{#snippet actions()}
			{#if writes}
				{@render defaultsForm(false)}
				<Button onclick={toggleCreate}>
					<PlusIcon class="size-4" /> New email template
				</Button>
			{/if}
		{/snippet}
	</PageHeader>

	{#if tabs}{@render tabs()}{/if}

	{#if notices}{@render notices()}{/if}

	<div class="space-y-5">
		{#if loadError}
			<!-- A failed fetch lands as an empty array, so without this an outage reads as "no templates". -->
			<LoadErrorBanner message="Couldn't load these email templates." onRetry={onretry} />
		{/if}
		{@render createForm(false)}
		{@render groupSwitch()}
		{@render content(false)}
	</div>
{:else}
	{#if writes}{@render defaultsForm(true)}{/if}
	<div
		class="-m-4 flex min-w-0 flex-col gap-5 p-4 md:-m-6 md:h-[calc(100svh-var(--topbar-h,4rem))] md:overflow-hidden md:p-6"
	>
		{#if notices}{@render notices()}{/if}
		{#if loadError}
			<LoadErrorBanner message="Couldn't load these email templates." onRetry={onretry} />
		{/if}
		{@render createForm(true)}
		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-4 md:min-h-0 md:flex-1 md:overflow-hidden">
				<div class="flex min-h-9 shrink-0 flex-wrap items-center gap-2">
					{#if tabs}{@render tabs()}{/if}
					{@render groupSwitch()}
					{#if writes || menuItems}
						<div class="ml-auto flex items-center">
							<ListingOverflow>
								{#if writes}
									<DropdownMenu.Item onclick={toggleCreate}>New email template</DropdownMenu.Item>
									{#if writes.loadDefaults}
										<DropdownMenu.Item onclick={() => defaultsFormEl?.requestSubmit()}>
											Load defaults
										</DropdownMenu.Item>
									{/if}
								{/if}
								{@render menuItems?.()}
							</ListingOverflow>
						</div>
					{/if}
				</div>
				{@render content(true)}
			</CardContent>
		</Card>
	</div>
{/if}

<ConfirmDialog
	bind:open={deleteOpen}
	title={deleteTarget ? `Delete “${deleteTarget.name}”?` : 'Delete this email template?'}
	description={deleteDescription}
	confirmLabel="Delete permanently"
	pendingLabel="Deleting…"
	destructive
	onconfirm={async () => {
		if (deleteTarget) await removeTemplate(deleteTarget);
	}}
>
	{#if deleteTarget && (usage[deleteTarget.code]?.length ?? 0) > 0}
		{@const used = usage[deleteTarget.code] ?? []}
		<!-- A WARNING, not a refusal: these rules keep sending, on the design Graphene ships. -->
		<div class="border-warning/40 bg-warning/10 rounded-md border px-3 py-2.5 text-sm">
			<p class="font-medium">
				{used.length} notification {used.length === 1 ? 'rule' : 'rules'}
				{used.length === 1 ? 'uses' : 'use'} this template for its email:
			</p>
			<ul class="text-muted-foreground mt-1 list-disc space-y-0.5 pl-5">
				{#each used as label (label)}
					<li>{label}</li>
				{/each}
			</ul>
			<p class="mt-2">
				Those notifications will still send, falling back to Graphene’s built-in design, until you
				point them at another template.
			</p>
		</div>
	{/if}
</ConfirmDialog>

<!-- Mounted with what this package cannot reach: the token-safe /versions proxy URL, the app's `enhance` and its navigation. A restore copies into a NEW draft, publishing nothing, so we land on the editor. -->
<VersionHistoryDialog
	bind:open={historyOpen}
	versionsUrl={historyTemplate ? links.versions(historyTemplate.uid) : null}
	previewUrl={historyTemplate && links.preview ? links.preview(historyTemplate.uid) : null}
	templateName={historyTemplate?.name ?? ''}
	canManage={!!writes}
	onRestored={async () => {
		if (historyTemplate) await writes?.navigate(links.edit(historyTemplate.uid));
	}}
/>

{#if writes}
	<TemplateSettingsDialog
		bind:open={settingsOpen}
		template={settingsTemplate}
		enhance={writes.enhance}
		updateAction={writes.update}
		onSaved={writes.onmutated}
	/>
{/if}
