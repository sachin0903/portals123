<script lang="ts">
	// A COMPACT, read-only feed of action events — the same humanising vocabulary as the master-detail
	// EventStream (`headline` / `eventCategory` over `describeActionEvent`), but a single line per
	// event, sized for a sidebar card, an embedded rail or a record tab: no toolbar, no pagination.
	// It is what the retired documents.audit_log activity feeds became — the DATA is now the
	// action-event stream (`audit.actions`), so there is one humanising vocabulary, not a parallel
	// ACTIVITY_META table. Repeated events collapse to a count (see event-feed.ts) so a burst of views
	// does not flood the glance; a collapsed group EXPANDS in place to its members, and clicking any
	// event opens the full detail (the shared EventDetail pane, in a dialog). The caller owns the
	// Card/heading; this renders the list.
	import BanknoteIcon from '@lucide/svelte/icons/banknote';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import SettingsIcon from '@lucide/svelte/icons/settings';
	import ShieldAlertIcon from '@lucide/svelte/icons/shield-alert';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import UserIcon from '@lucide/svelte/icons/user';
	import WorkflowIcon from '@lucide/svelte/icons/workflow';
	// Per-action glyphs (the informative icons the old documents feed had).
	import EyeIcon from '@lucide/svelte/icons/eye';
	import FileOutputIcon from '@lucide/svelte/icons/file-output';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import UploadCloudIcon from '@lucide/svelte/icons/upload-cloud';
	import SendIcon from '@lucide/svelte/icons/send';
	import ArchiveIcon from '@lucide/svelte/icons/archive';
	import ArchiveRestoreIcon from '@lucide/svelte/icons/archive-restore';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import FilePlusIcon from '@lucide/svelte/icons/file-plus';
	import ClipboardCheckIcon from '@lucide/svelte/icons/clipboard-check';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import Share2Icon from '@lucide/svelte/icons/share-2';
	import LinkIcon from '@lucide/svelte/icons/link';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import FilterSelect from '../book/listing/filter-select.svelte';
	import { ALL } from '../book/listing/filters.js';
	import { eventCategory, timestampParts } from './describe.js';
	import type { ActionCategory, ActionEntity, ActionEvent } from './describe.js';
	import PersonAvatar from '../data/person-avatar.svelte';
	import { headline, initialsOf } from './event-detail.js';
	import EventDetail from './event-detail.svelte';
	import { groupEvents } from './event-feed.js';
	import {
		actorSuggestions,
		eventTypeSuggestions,
		matchesActor,
		matchesEventType
	} from './event-stream-filters.js';

	let {
		events = [],
		attribution = 'firm',
		emptyText = 'No activity yet.',
		filterable = false,
		links,
		entityHref
	}: {
		events?: ActionEvent[];
		/** Who is reading — changes how a PROXIED row is attributed (mirrors EventRow/EventStream).
		 *  `firm` names the Graphene operator who came in; `operations` names the target firm. */
		attribution?: 'operations' | 'firm';
		emptyText?: string;
		/** Opt-in: render a compact Event-type + Actor filter bar above the glance (the app-standard
		 *  `FilterSelect`, same control as every listing toolbar) and narrow the LOADED set client-side.
		 *  For the firm-wide OVERVIEW feed, where many actors and types share one glance; the narrowly
		 *  scoped feeds (one client, one document) leave it off — there is nothing to sift there. */
		filterable?: boolean;
		/** Passed through to the detail pane: turns a target id chip (a path param) into a host-app
		 *  route. Absent ⇒ the chip is plain text. */
		links?: (key: string, value: unknown) => string | null;
		/** Passed through to the detail pane: turns a RESOLVED entity into a host-app route. */
		entityHref?: (event: ActionEvent, entity: ActionEntity) => string | null;
	} = $props();

	// The category glyph vocabulary (the fallback), matching EventRow. `data-category` mirrors it for
	// any per-category theming.
	const CATEGORY_ICONS: Record<ActionCategory, typeof UserIcon> = {
		people: UserIcon,
		roles: ShieldIcon,
		documents: FileTextIcon,
		money: BanknoteIcon,
		tasks: ListChecksIcon,
		flows: WorkflowIcon,
		screening: ShieldAlertIcon,
		security: KeyRoundIcon,
		settings: SettingsIcon
	};

	// Per-ACTION glyphs, keyed on the event's operation verb — the informative icons the old documents
	// feed had (viewed = eye, sent = send, uploaded = upload…). A document-only feed drew the SAME
	// document glyph on every row, which told you nothing; the verb icon makes each action read at a
	// glance. Anything unmapped falls back to the category glyph, so a non-document feed still reads.
	const VERB_ICON: Record<string, typeof UserIcon> = {
		view: EyeIcon,
		render: FileOutputIcon,
		generate: SparklesIcon,
		upload: UploadCloudIcon,
		send: SendIcon,
		archive: ArchiveIcon,
		unarchive: ArchiveRestoreIcon,
		update: PencilIcon,
		reassign: FilePlusIcon,
		agree: ClipboardCheckIcon,
		acknowledge: ClipboardCheckIcon,
		complete: ClipboardCheckIcon,
		fulfil: ClipboardCheckIcon,
		create: ClipboardListIcon
	};
	function iconFor(eventType: string): typeof UserIcon {
		const opId = eventType.startsWith('action.') ? eventType.slice('action.'.length) : eventType;
		const [resource, verb] = opId.split('-');
		// Share-link lifecycle reads as sharing/link, not by its 'create'/'revoke' verb.
		if (resource === 'shares') return verb === 'access' ? LinkIcon : Share2Icon;
		return VERB_ICON[verb ?? ''] ?? CATEGORY_ICONS[eventCategory(eventType)];
	}

	// One clock per render keeps the relative hints on a page mutually consistent (only the absolute
	// stamp is shown here, but timestampParts wants a reference).
	const now = new Date();

	// The compact filter (opt-in): two app-standard FilterSelects over the LOADED events. Options come
	// from the FULL loaded set (so picking a type never removes actors that appear only on other
	// types), and each control offers a value only when there is more than one to choose between — a
	// one-actor or one-type glance needs no filter. `ALL` is the "no filter" sentinel.
	let eventType = $state<string>(ALL);
	let actor = $state<string>(ALL);
	const eventTypeOptions = $derived(filterable ? eventTypeSuggestions(events) : []);
	const actorOptions = $derived(filterable ? actorSuggestions(events) : []);
	const showFilters = $derived(eventTypeOptions.length > 1 || actorOptions.length > 1);
	const filtersActive = $derived(eventType !== ALL || actor !== ALL);
	const filtered = $derived(
		filterable
			? events.filter(
					(e) =>
						matchesEventType(e, eventType === ALL ? '' : eventType) &&
						// matchesActor now takes a list; this control still picks one.
						matchesActor(e, actor === ALL ? [] : [actor])
				)
			: events
	);
	const groups = $derived(groupEvents(filtered));

	// A collapsed group ("Viewed … (5)") expands in place to its individual events on click, keyed by
	// the representative's id.
	let expanded = $state<Set<number>>(new Set());
	function toggle(id: number, e: MouseEvent): void {
		e.stopPropagation();
		const next = new Set(expanded);
		if (next.has(id)) next.delete(id);
		else next.add(id);
		expanded = next;
	}

	// Clicking any event opens the full detail (the shared EventDetail pane) in a dialog.
	let selected = $state<ActionEvent | null>(null);
	let detailOpen = $state(false);
	function openDetail(e: ActionEvent): void {
		selected = e;
		detailOpen = true;
	}
	function onKey(e: KeyboardEvent, ev: ActionEvent): void {
		if (e.key !== 'Enter' && e.key !== ' ') return;
		e.preventDefault();
		openDetail(ev);
	}
</script>

{#if filterable && showFilters}
	<!-- The compact filter bar: the app-standard FilterSelect (same control as the /events toolbar and
	     every listing), sticky so it stays put while the glance scrolls beneath it. Wraps on a narrow
	     sidebar. Only the two the overview needs — Event type and Actor. -->
	<div class="bg-card sticky top-0 z-10 mb-2 flex flex-wrap items-center gap-2 pb-2">
		{#if eventTypeOptions.length > 1}
			<FilterSelect
				bind:value={eventType}
				options={eventTypeOptions}
				allLabel="All events"
				label="Event type"
				class="h-8 min-w-[8rem]"
			/>
		{/if}
		{#if actorOptions.length > 1}
			<FilterSelect
				bind:value={actor}
				options={actorOptions}
				allLabel="All actors"
				label="Actor"
				class="h-8 min-w-[8rem]"
			/>
		{/if}
	</div>
{/if}

{#if groups.length === 0}
	<p class="text-muted-foreground py-8 text-center text-sm">
		{filtersActive ? 'No activity matches these filters.' : emptyText}
	</p>
{:else}
	<div class="divide-border divide-y">
		{#each groups as g (g.event.id)}
			{@const e = g.event}
			{@const led = headline(e)}
			<!-- The action is the humanised sentence minus the actor (WHO); a leading verb-space or the
			     de-slugged " · " is trimmed so the line reads "{actor} {action}". -->
			{@const action = led.rest.replace(/^\s*·?\s*/, '')}
			{@const stamp = timestampParts(e.created_at, now)}
			{@const category = eventCategory(e.event_type)}
			{@const RowIcon = iconFor(e.event_type)}
			{@const isProxied = e.target_tenant_id !== null}
			<!-- The row is clickable → the detail dialog. It is a div (not a button) so the "(N)" expand
			     control can be a real nested button; keyboard support is wired explicitly. -->
			<!-- `ring-inset`: the feed sits in a scroll rail that clips an outside ring. -->
			<div
				role="button"
				tabindex="0"
				onclick={() => openDetail(e)}
				onkeydown={(ev) => onKey(ev, e)}
				class="hover:bg-muted/40 focus-visible:ring-ring/50 flex cursor-pointer items-start gap-2.5 rounded-md px-1 py-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-inset"
			>
				<span
					class="bg-muted text-muted-foreground mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full"
					data-category={category}
					aria-hidden="true"
				>
					<RowIcon class="size-3.5" />
				</span>
				<div class="min-w-0 flex-1">
					<div class="flex flex-wrap items-center gap-x-1.5 gap-y-1 leading-snug">
						<!-- Same identity colour as the rest of the product (see event-stream's own rows). -->
						<PersonAvatar
							name={led.actor}
							initials={initialsOf(led.actor)}
							class="size-5 text-[9px] font-medium"
						/>
						<span class="font-medium">{led.actor}</span>
						<!-- `anywhere` lets a long filename break inside the rail. -->
						<span class="text-muted-foreground min-w-0 [overflow-wrap:anywhere]">{action}</span>
						{#if g.count > 1}
							<button
								type="button"
								onclick={(ev) => toggle(e.id, ev)}
								class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 inline-flex items-center gap-0.5 rounded-sm align-middle outline-none focus-visible:ring-2"
								aria-expanded={expanded.has(e.id)}
								aria-label={`${g.count} occurrences — expand`}
							>
								<span class="tabular-nums">({g.count})</span>
								<ChevronDownIcon
									class="size-3 transition-transform {expanded.has(e.id) ? 'rotate-180' : ''}"
								/>
							</button>
						{/if}
						{#if isProxied && attribution === 'firm'}
							<StatusBadge tone="warning">
								<TriangleAlertIcon class="size-3" />
								Graphene support
							</StatusBadge>
						{/if}
						{#if e.metadata?.break_glass}
							<StatusBadge tone="danger">Break-glass</StatusBadge>
						{/if}
					</div>

					<!-- Expanded: each occurrence as its own row, clickable for its own detail. -->
					{#if g.count > 1 && expanded.has(e.id)}
						<div class="border-border/60 mt-1.5 space-y-0.5 border-l pl-3">
							{#each g.members as m (m.id)}
								{@const ms = timestampParts(m.created_at, now)}
								<button
									type="button"
									onclick={(ev) => {
										ev.stopPropagation();
										openDetail(m);
									}}
									class="text-muted-foreground hover:text-foreground block w-full text-left text-xs tabular-nums"
									title={m.created_at}
								>
									{ms.absolute}
								</button>
							{/each}
						</div>
					{/if}
				</div>
				<!-- The stamp is its OWN COLUMN of the row, not the last item of the wrapping sentence.
				     It used to sit inside that wrap with `ms-auto`, which put it on the actor's line only
				     while the sentence was short enough — a long document name pushed it onto a third
				     line, right-aligned under the description, so the feed's rows were inconsistently
				     two and three lines tall and the times formed no column to scan down.
				     `leading-5` matches the avatar's 1.25rem line box, so `items-start` on the row aligns
				     the stamp with the actor's name exactly rather than by a guessed margin. -->
				<span
					class="text-muted-foreground shrink-0 text-xs leading-5 tabular-nums"
					title={e.created_at}>{stamp.absolute}</span
				>
			</div>
		{/each}
	</div>
{/if}

<!-- The full event, on click — the shared master-detail detail pane, in a dialog so the glance keeps
     its place. `links`/`entityHref` (when injected) make its id/entity chips navigable. -->
<Dialog.Root bind:open={detailOpen}>
	<Dialog.Content class="max-h-[85vh] gap-0 overflow-y-auto p-0 sm:max-w-2xl">
		<Dialog.Header class="sr-only">
			<Dialog.Title>Event details</Dialog.Title>
		</Dialog.Header>
		{#if selected}
			{#key selected.id}
				<EventDetail event={selected} {links} {entityHref} />
			{/key}
		{/if}
	</Dialog.Content>
</Dialog.Root>
