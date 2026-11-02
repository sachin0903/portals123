<script lang="ts">
	// The document detail — the platform portal's /documents/[id] page, extracted so the admin
	// portal can render the same record (the portal waterfall). Data arrives as typed props from
	// the app's loader, every href is built by the injected `links`, and the writes (rename,
	// delete/restore) only exist when the app passes `actions` — each callback owns
	// its own fetch/toast/invalidate, this component only owns the edit/pending state around them.
	// Actions absent ⇒ Download alone, which is the admin wave's read-only posture.
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import { DocumentViewer } from '../../ui/document-viewer/index.js';
	import { default as EmptyState } from '../../ui/empty-state/empty-state.svelte';
	import { Input } from '../../ui/input/index.js';
	import { LoadErrorBanner } from '../../ui/load-error-banner/index.js';
	import { Separator } from '../../ui/separator/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { CopyButton } from '../../ui/copy-button/index.js';
	import * as Tabs from '../../ui/tabs/index.js';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import ArchiveRestoreIcon from '@lucide/svelte/icons/archive-restore';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import FileXIcon from '@lucide/svelte/icons/file-x';
	import FileClockIcon from '@lucide/svelte/icons/file-clock';
	import { STATUS_META, formatTime, sourceLabel } from './display.js';
	import { formatBytes } from '../../../bytes.js';
	import { formatDate } from '../../../dates.js';
	import {
		contentTypeLabel,
		documentCategoryLabel,
		documentInFlight,
		downloadName,
		type LibraryRow
	} from './documents.js';
	import EventFeed from '../../audit/event-feed.svelte';
	import type { ActionEntity, ActionEvent } from '../../audit/describe.js';
	import type { DocumentDetailLinks } from './types.js';
	import { cn } from '../../../utils.js';
	import RefreshWhile from '../../ui/refresh-while/refresh-while.svelte';
	import { REFRESH_WHILE } from '../../notifications/poll-controller.js';
	import { LINK_RECORD, WRAP_NAME } from '../../../text-shell.js';

	/** The writes this record can host. Absent ⇒ read-only (Download alone). Each callback owns
	 *  its own fetch/toast/invalidate — the app's form actions and dialogs stay app-side.
	 *
	 *  Every member is OPTIONAL: drafting (rename/delete/restore) is `documents:write`, and the app
	 *  passes exactly the callbacks the caller's permissions earn. There is deliberately no `notify`
	 *  — telling a client about a document has been removed in favour of a document REQUEST, which
	 *  asks them to act on it and records whether they did. */
	export type DocumentDetailActions = {
		/** Save a rename/reclassify. Resolve `true` on success (the edit form closes); the app
		 *  surfaces its own failure toast and resolves `false`. */
		/** Rename only — a document's type is fixed at upload, so no type is sent. */
		rename?: (fields: { name: string }) => Promise<boolean>;
		/** Delete the document — the API's soft `archive` (already confirmed here); the app navigates
		 *  away after. */
		archive?: () => Promise<void>;
		/** Put a deleted document back in the library. */
		restore?: () => Promise<void>;
	};

	let {
		doc,
		events = [],
		previewUrl = null,
		previewContentType = null,
		loadError = false,
		links,
		actions = null,
		/** Re-queues a failed render. The waiting screen does not use this to re-read. */
		onretry,
		/** Re-reads the document while a render is still queued or generating, and behind Reload. */
		onrefresh,
		/** SPA navigation (the app passes `goto`); falls back to a full-page navigation. */
		onnavigate
	}: {
		doc: LibraryRow | null;
		/** This document's events (the action stream, `?document=<uid>` scoped) — its own trail,
		 *  humanised by the shared EventFeed (one vocabulary with the firm-wide Events page). */
		events?: ActionEvent[];
		previewUrl?: string | null;
		previewContentType?: string | null;
		loadError?: boolean;
		links: DocumentDetailLinks;
		actions?: DocumentDetailActions | null;
		onretry?: () => void;
		onrefresh?: () => void;
		onnavigate?: (href: string) => void;
	} = $props();

	function navigate(href: string): void {
		if (onnavigate) onnavigate(href);
		else if (typeof location !== 'undefined') location.assign(href);
	}

	// Inline preview via the app's same-origin content proxy: the viewer renders PDFs/images
	// directly; other types fall back to its "download to view" placeholder. Null while a
	// generation is still queued — the page says so rather than showing an empty viewer.
	const preview = $derived({
		src: previewUrl ?? undefined,
		contentType: previewContentType ?? undefined
	});
	const rendering = $derived(documentInFlight(doc?.status));

	// How long it has been waiting. A render is seconds of work, so anything past a couple of
	// minutes is not "nearly there" — it is stuck, and the reader deserves to be told that plainly
	// rather than left refreshing. The page re-reads the document while it is queued or generating,
	// so this updates when that read lands. The clock itself is not a live ticker.
	const queuedMinutes = $derived.by(() => {
		if (!rendering || !doc?.createdAt) return null;
		const started = new Date(doc.createdAt).getTime();
		if (!Number.isFinite(started)) return null;
		return Math.max(0, Math.floor((Date.now() - started) / 60_000));
	});
	/** Past this, a queued render is overdue rather than slow — see `queuedMinutes`. */
	const OVERDUE_MINUTES = 2;
	const overdue = $derived(queuedMinutes !== null && queuedMinutes >= OVERDUE_MINUTES);
	const renderingTitle = $derived(
		overdue ? 'This is taking longer than it should' : 'Building your document'
	);
	const renderingDetail = $derived.by(() => {
		const from = doc?.templateName ? ` from ${doc.templateName}` : '';
		const asked = doc?.createdAt ? ` Asked for at ${formatTime(doc.createdAt)}.` : '';
		if (overdue) {
			// Names the wait, because "still working on it" after ten minutes is not credible, and
			// says who can act — the reader cannot.
			return (
				`Still queued after ${queuedMinutes} minutes${from}.${asked}` +
				' Documents normally take a few seconds, so ask your administrator to check the' +
				' document renderer.'
			);
		}
		return (
			`We're rendering the PDF${from}.${asked}` +
			' It usually takes a few seconds and appears here on its own.'
		);
	});
	// The file's real format, from the content type the app fetched with the preview. Null when the
	// document has no bytes yet, which the Details row states as "Pending" rather than guessing.
	const format = $derived(contentTypeLabel(previewContentType));
	// Back = plain browser back, so it returns to wherever the document was opened from (the list,
	// the audit trail, a client's tab) without each origin having to pass its path. Falls back to
	// the documents list when there's no in-app history to step back to (a directly-opened link).
	function goBack(): void {
		if (typeof history !== 'undefined' && history.length > 1) history.back();
		else navigate(links.documents);
	}

	// The Events tab's way OUT. NOT to this document — the reader is already on it, and a button
	// back to the page you are on is a dead end dressed as an action. What the tab CAN reach is the
	// context around the document: the client it is filed for and the template it came from, both of
	// which the app injects as routes here (`links.client` is absent on a read-only posture).
	const feedEntityHref = (_e: ActionEvent, ent: ActionEntity) => {
		if (!ent.uid) return null;
		if (ent.kind === 'contact') return links.client?.(ent.uid) ?? null;
		if (ent.kind === 'template') return links.template(ent.uid);
		return null;
	};

	// Inline RENAME, and only a rename. There used to be a category Select beside it; a document's
	// type is set when it is uploaded and is not changed afterwards, so the editor no longer offers
	// one and no longer posts one.
	let editing = $state(false);
	let editName = $state('');
	let editError = $state('');
	let saving = $state(false);

	function startEdit(): void {
		if (!doc) return;
		editName = doc.name;
		editError = '';
		editing = true;
	}

	async function saveEdit(): Promise<void> {
		const rename = actions?.rename;
		if (!rename) return;
		if (!editName.trim()) {
			editError = 'A document needs a name.';
			return;
		}
		editError = '';
		saving = true;
		// The callback owns the POST + failure toast + reload; success closes the editor.
		const ok = await rename({ name: editName });
		saving = false;
		if (ok) editing = false;
	}

	// Full-page reading: the viewer's own toggle asks for it and this hides the details sidebar, so a
	// long report gets the whole width and the reader can then zoom into it. HIDDEN rather than
	// removed from the tree — an in-progress rename, or which sidebar tab was open, must survive a
	// look at the document full width, and `{#if}` would throw both away. Reversible from the same
	// control, or with Escape (the viewer owns that key: it knows whether its find bar is open).
	let expanded = $state(false);

	let archiveOpen = $state(false);

	// Deleting is recoverable (POST /documents/{uid}/unarchive restores the status the document held
	// before), so a deleted document offers Restore rather than a dead end — and this record is the
	// ONLY place a firm user can reach it now that the library has no Archived view. Not
	// destructive, so no confirm — the callback's toast plus the refreshed status badge is the
	// feedback.
	let restoring = $state(false);
	async function restoreDoc(): Promise<void> {
		const restore = actions?.restore;
		if (!restore || restoring) return;
		restoring = true;
		try {
			await restore();
		} finally {
			restoring = false;
		}
	}
</script>

<RefreshWhile
	active={rendering && !!onrefresh}
	interval={REFRESH_WHILE.documentRender}
	onpoll={() => onrefresh?.()}
/>

{#snippet reloadDocument()}
	<Button variant="outline" onclick={() => onrefresh?.()}>Reload</Button>
{/snippet}

{#if !doc}
	<!-- `tone="warning"`: this is "not found OR not permitted", which is a statement about US, and
	     the neutral dashed treatment reads as "you have no documents" at a glance. -->
	<EmptyState
		icon={FileXIcon}
		tone="warning"
		title="Document not found"
		description="It may have been deleted, or you may not have access to it."
		class="mx-auto mt-16 max-w-md"
	>
		{#snippet action()}
			<Button href={links.documents} variant="ghost">Back to documents</Button>
		{/snippet}
	</EmptyState>
{:else}
	<!-- Full-bleed document + sidebar, breaking out of the shell padding like the shared doc -->
	<div class="-m-4 flex h-[calc(100svh-var(--topbar-h,4rem))] flex-col md:-m-6 lg:flex-row">
		<!-- Viewer — the page floats on the app's gradient backdrop (transparent surround) -->
		<div class="relative min-w-0 flex-1 overflow-hidden">
			{#if rendering}
				<!-- No bytes exist yet, so the viewer's generic placeholder would read as a broken
				     document. This says which template it is coming from, when it was asked for, and
				     that it arrives on its own — and once it is overdue it says THAT instead, because
				     "nearly there" stops being true and the reader is the one person who cannot fix
				     it. A clock rather than the error icon: nothing has gone wrong yet. -->
				<div class="flex h-full w-full items-center justify-center p-6">
					<!-- The Reload is gated on the PROP rather than inside the snippet. `onrefresh` is
					     optional and this rendered regardless — a control that does nothing on a mount
					     that passes none, which is worse than no control: the reader presses it,
					     nothing happens, and cannot tell whether the document or the button is broken.
					     Putting the `{#if}` inside the snippet does not fix that either, because a
					     snippet is always truthy, so EmptyState still paints its empty wrapper.
					     It re-reads. Re-queueing is `onretry`, and that is not what Reload means. -->
					<EmptyState
						icon={FileClockIcon}
						title={renderingTitle}
						description={renderingDetail}
						class="max-w-md"
						action={onrefresh ? reloadDocument : undefined}
					/>
				</div>
			{:else if doc.status === 'failed'}
				<div class="flex h-full w-full items-center justify-center p-6">
					<EmptyState
						icon={FileXIcon}
						tone="warning"
						title="This document could not be produced"
						description={doc.error ??
							'The render failed and no reason was recorded. Check the template, then try again from the library.'}
						class="max-w-md"
					>
						{#snippet action()}
							<Button variant="outline" href={links.documents}>Back to documents</Button>
						{/snippet}
					</EmptyState>
				</div>
			{:else}
				<DocumentViewer
					src={preview.src}
					contentType={preview.contentType}
					name={doc.name}
					{expanded}
					onexpand={(next) => (expanded = next)}
					class="h-full w-full bg-transparent"
				/>
			{/if}

			<!-- Floating back (top-left of the viewer) — plain browser back to wherever you came from. -->
			<Button
				onclick={goBack}
				aria-label="Back"
				variant="outline"
				size="icon-lg"
				class="bg-card/85 hover:bg-card absolute left-3 top-3 z-20 rounded-full shadow-sm backdrop-blur"
			>
				<ChevronLeftIcon class="size-5" />
			</Button>
		</div>

		<!-- Sidebar — scrolls alongside the document, and steps aside entirely when the reader expands
		     the viewer (hidden, not unmounted: see `expanded` above). -->
		<aside
			class={cn(
				'bg-card w-full shrink-0 overflow-y-auto border-t lg:w-[360px] lg:border-l lg:border-t-0',
				expanded && 'hidden'
			)}
		>
			<div class="space-y-4 p-5">
				<div>
					{#if editing}
						<div class="space-y-2">
							<Input
								bind:value={editName}
								class="font-semibold"
								aria-invalid={editError ? 'true' : undefined}
								aria-label="Document name"
							/>
							{#if editError}<p class="text-destructive text-xs">{editError}</p>{/if}
							<div class="flex gap-2">
								<Button size="sm" onclick={saveEdit} disabled={saving}>
									{saving ? 'Saving…' : 'Save'}
								</Button>
								<Button size="sm" variant="ghost" onclick={() => (editing = false)}>Cancel</Button>
							</div>
						</div>
					{:else}
						<!-- `min-w-0` on the heading and `WRAP_NAME` in it. A document name is a FILENAME, and
						     `Annual_Suitability_Report_Smith-Jones_2024_FINAL_v3.pdf` has no break opportunity at
						     all (`_` is not one), so in a hard `lg:w-[360px]` aside it ran out of the panel. As a
						     flex item with `min-width: auto` it also refused to shrink, pushing the `shrink-0`
						     pencil beside it off the edge — so the fix is both: the item may shrink, and the text
						     may break anywhere. Wrapped rather than truncated, because this is the document's
						     own title. -->
						<div class="flex items-start justify-between gap-2">
							<h1 class="min-w-0 text-xl leading-snug font-semibold {WRAP_NAME}">{doc.name}</h1>
							{#if actions?.rename}
								<Button
									variant="ghost"
									size="icon"
									onclick={startEdit}
									aria-label="Rename"
									class="text-muted-foreground hover:text-foreground shrink-0"
								>
									<PencilIcon class="size-4" />
								</Button>
							{/if}
						</div>
						<!-- The client this is filed for, linked on the same terms as the Details tab's
						     "Filed for" row below: only when the app injected a builder AND we hold the
						     uid, because matching a person by display name reaches the wrong Margaret.
						     Linked HERE too because this is where a reader's eye lands — the name under
						     the title is the one they reach for, and having it inert while the same name
						     two tabs down is a link reads as the two being different people. -->
						{#if doc.client}<p class="text-muted-foreground mt-0.5 text-sm {WRAP_NAME}">
								{#if doc.clientContactUid != null && links.client}
									<a href={links.client(doc.clientContactUid)} class={LINK_RECORD}>{doc.client}</a>
								{:else}
									{doc.client}
								{/if}
							</p>{/if}
						<div class="mt-2">
							<StatusBadge
								tone={STATUS_META[doc.status].tone}
								pulse={STATUS_META[doc.status].pulse}
							>
								{STATUS_META[doc.status].label}
							</StatusBadge>
						</div>
					{/if}
				</div>
				{#if loadError}
					<LoadErrorBanner
						message="Some of this document's history couldn't be loaded."
						onRetry={onrefresh ?? onretry}
					/>
				{/if}

				<div class="grid grid-cols-2 gap-2">
					<!-- The API serves PDFs `inline`, so without the `download` attribute this button opened a
					     tab rather than saving. Same-origin proxy ⇒ the attribute is honoured.
					     `downloadName`, not `doc.name`: a generated document's name is a TITLE with no
					     extension ("Suitability Report — Q1 2026"), and the attribute is where the browser
					     takes the filename from — so this saved a file Windows typed as "File" and could not
					     open. `disabled={!preview.src}` already covers the other half, which the row kebab
					     had to learn separately: with no bytes to serve, a download saves the error page. -->
					<Button
						variant="secondary"
						size="sm"
						class="col-span-2"
						href={preview.src}
						download={downloadName(doc)}
						disabled={!preview.src}
					>
						<DownloadIcon class="size-4" /> Download
					</Button>
				</div>

				<Card>
					<CardContent class="p-0">
						<Tabs.Root value="details">
							<Tabs.List class="m-2 w-auto">
								<Tabs.Trigger value="details">Details</Tabs.Trigger>
								<Tabs.Trigger value="events">Events</Tabs.Trigger>
							</Tabs.List>

							<!-- The record's facts, GROUPED. It was seven flat rows and read as a list of
							     whatever happened to be handy; a document has three separable things to say — what
							     the file IS, where it CAME FROM, and who it is FOR — and a reader scanning for one
							     of them should not have to read the other two. The headings are what make it
							     scannable, not decoration: they encode that separation. -->
							<Tabs.Content value="details" class="p-4 text-xs">
								<!-- A plain wrapper, not decoration: a snippet declared as the direct child of a
								     COMPONENT is handed to it as a prop (svelte-check: "'band' does not exist in
								     type ..."), so the two below need an element to be block-scoped by. -->
								<div>
									{#snippet band(title: string)}
										<p
											class="text-muted-foreground/70 mt-4 mb-1.5 text-[0.68rem] font-semibold tracking-[0.09em] uppercase first:mt-0"
										>
											{title}
										</p>
									{/snippet}
									<!-- The value takes `WRAP_NAME`, not just `min-w-0`. `min-w-0` lets the box shrink but
									     gives the TEXT no way to break, so an adviser's email or a long template name
									     rendered into this ~300px column overflowed it regardless. These rows carry
									     `doc.createdBy` — a name or an email address — which is exactly the content with
									     no break opportunity. -->
									{#snippet fact(label: string, value: string | null | undefined)}
										<div class="flex justify-between gap-3 py-0.5">
											<span class="text-muted-foreground shrink-0">{label}</span>
											<span class="min-w-0 text-right {WRAP_NAME}">{value || '—'}</span>
										</div>
									{/snippet}

									{@render band('File')}
									{@render fact('Category', documentCategoryLabel(doc.category))}
									<!-- The REAL content type, not `fileFormat`'s icon bucket — see `contentTypeLabel`.
								     Null while a generate is still queued, which the row says as "Pending" rather
								     than naming a format the document does not have yet. -->
									{@render fact('Format', format ?? (rendering ? 'Pending' : null))}
									{@render fact('Size', doc.sizeBytes ? formatBytes(doc.sizeBytes) : null)}

									{@render band('Origin')}
									{@render fact('Source', sourceLabel(doc.source))}
									<!-- With the TIME. A document's date alone cannot separate two versions produced in
								     the same afternoon, which is exactly when someone asks. -->
									{@render fact(
										'Created',
										`${formatDate(doc.createdAt)}, ${formatTime(doc.createdAt)}`
									)}
									{#if doc.source !== 'provider'}
										{@render fact(
											doc.source === 'uploaded' ? 'Uploaded by' : 'Generated by',
											doc.createdBy
										)}
									{/if}
									{#if doc.generatedAt && doc.generatedAt !== doc.createdAt}
										<!-- When the RENDER finished, as against when it was queued (`createdAt`). Shown
									     only when the two differ, so an instant render does not print the same
									     timestamp twice. -->
										{@render fact(
											'Rendered',
											`${formatDate(doc.generatedAt)}, ${formatTime(doc.generatedAt)}`
										)}
									{/if}
									{#if doc.version}
										<!-- Which template (and revision) produced this document — the audit answer to
									     "did these two generations come from the same template version?" -->
										<div class="flex justify-between gap-3 py-0.5">
											<span class="text-muted-foreground shrink-0">From template</span>
											{#if doc.templateUid}
												<a
													href={links.template(doc.templateUid)}
													class="min-w-0 truncate text-right hover:underline"
												>
													{doc.templateName ?? 'Template'} · {doc.version}
												</a>
											{:else}
												<span class="min-w-0 truncate text-right"
													>{doc.templateName ?? 'Template'} · {doc.version}</span
												>
											{/if}
										</div>
									{/if}

									<!-- The document's own id, in full and copyable. It is what every OTHER system calls
								     this document — the object-storage key holds it, `audit.actions.path_params.uid`
								     records it against every event, a request line item targets it — so it is the
								     value someone reads out on a support call or pastes into a query.
								     INLINE, as a row like the ones above. It used to be a stacked band because a
								     two-column row truncated it to `doc_69063cf7ca1b44688b8b59…`; a public id is
								     twelve characters now (docs/data-architecture.md Decision 9), so the reason is
								     gone and a lone heading over one value was just noise.
								     It still assumes NOTHING about the length — a book holds more than one
								     generation of id — so `break-all` WRAPS a longer legacy id onto a second line
								     rather than clipping it, and the row is `items-start` so the label stays put
								     when it does. Truncating here would reintroduce exactly the defect above.
								     The VALUE is the click target, not just the icon: an id is a small thing to aim
								     at and it is the obvious thing to press — the ghost button's hover background is
								     what says so, rather than muting the value, which would make the one field on this
								     page people actually reach for the faintest thing on it. The negative margins undo
								     the button's own padding so the row keeps its neighbours' height and right edge.
								     `select-all` keeps one click useful where the clipboard is unavailable (it needs a
								     secure context); it beats the button's inherited `select-none` because
								     `user-select` is set on the value itself. -->
									<div class="flex items-start justify-between gap-3 py-0.5">
										<span class="text-muted-foreground shrink-0">Reference</span>
										<CopyButton
											value={doc.uid}
											label="Copy document id"
											class="-my-0.5 -mr-1.5 min-w-0 shrink"
										>
											<!-- THREE things have to be true for this to adapt to any id length, and each was
											     separately false. `shrink` on the button, because `buttonVariants`' base sets
											     `shrink-0` — so the button kept its full intrinsic width and no amount of
											     `min-w-0` or wrapping inside it could give the row its space back (the same
											     trap `multi-select` documents for `Badge`'s own `shrink-0`). `whitespace-normal`
											     on this element, because the base also sets `whitespace-nowrap` and
											     `white-space` INHERITS, which forbids every line break — so the `break-all`
											     beside it could never fire. And `break-all` rather than truncation, because an
											     id has no word to keep whole and half of one is not a reference anyone can act
											     on; this row is the only place it is shown now the listing's inline copy is
											     hidden. Together they mean a 12-character nanoid sits on the label's line and a
											     pre-nanoid 32-hex id — which dev still holds — wraps whole beneath it. Nothing
											     here assumes a length. -->
											<code class="min-w-0 font-mono text-xs break-all whitespace-normal select-all"
												>{doc.uid}</code
											>
										</CopyButton>
									</div>

									{#if doc.client}
										<!-- The client this document is ABOUT. It was only ever a subtitle under the
										     title, where it states a name but goes nowhere; from here it is one click to
										     their record, which is where a reader who has just identified a document
										     wants to be. A heading with the value under it, rather than a label/value row
										     like the ones above, because "Filed for" and a "Client" row label are two
										     names for one fact.
										     A link only when the app injected a builder AND we hold the id: matching a
										     person by display name is how a reader reaches the wrong Margaret. The whole
										     band is absent for a document filed for nobody, rather than printing a dash
										     under a heading. -->
										{@render band('Filed for')}
										{#if doc.clientContactUid != null && links.client}
											<a href={links.client(doc.clientContactUid)} class={LINK_RECORD}>
												{doc.client}
											</a>
										{:else}
											<span>{doc.client}</span>
										{/if}
									{/if}
								</div>
							</Tabs.Content>

							<!-- This document's own events, off the action stream (`?document=<uid>`), humanised
							     by the shared EventFeed — one vocabulary with the firm-wide Events page. -->
							<Tabs.Content value="events" class="p-2 text-xs">
								<EventFeed
									{events}
									attribution="firm"
									entityHref={feedEntityHref}
									emptyText="No events yet."
								/>
							</Tabs.Content>
						</Tabs.Root>
					</CardContent>
				</Card>

				{#if doc.status === 'archived'}
					<Alert>
						<AlertDescription>
							This document is deleted: it is off the library and out of every client portal, and
							retained on the record.
							{#if actions?.restore}
								Restore it to put it back at the status it held before.
							{/if}
						</AlertDescription>
					</Alert>
					{#if actions?.restore}
						<Button variant="secondary" size="sm" disabled={restoring} onclick={restoreDoc}>
							<ArchiveRestoreIcon class="size-4" />
							{restoring ? 'Restoring…' : 'Restore to the library'}
						</Button>
					{/if}
				{:else if actions?.archive && doc.deletable === false}
					<!-- Says WHY, where the library can only withhold the menu item. A record is where a
					     reader comes to find out what they may do with a document, so "the button is
					     missing" is not an acceptable answer here.
					     Gated on `actions.archive` as well, so this is only ever shown to a reader who
					     WOULD otherwise have the button. Without that, a read-only reader (and the admin
					     portal's read-only mount, where `actions` is null) was told a provider-synced
					     document could not be deleted — naming the wrong reason, since their own
					     permissions are what withhold the control, and only on the subset of documents
					     where a second unrelated rule happened to apply. -->
					<Separator />
					<p class="text-muted-foreground text-sm">
						This document can’t be deleted — it is synced from a provider, so it belongs to that
						provider’s record rather than to your library.
					</p>
				{:else if actions?.archive}
					<Separator />
					<Button
						variant="ghost"
						size="sm"
						class="text-destructive"
						onclick={() => (archiveOpen = true)}
					>
						<Trash2Icon class="size-4" /> Delete document
					</Button>
				{/if}
			</div>
		</aside>
	</div>

	<!-- A soft delete, so the copy states what actually happens rather than promising permanence: the
	     document leaves the library and every client portal, and is retained on the record. It does
	     NOT offer a way back, because this record navigates to the list on confirm and the list has
	     no deleted view — Restore lives on this page, which by then you have left. -->
	<ConfirmDialog
		bind:open={archiveOpen}
		title="Delete this document?"
		description="It leaves your library and any client portal it appears in, and is retained on the record."
		confirmLabel="Delete"
		destructive
		onconfirm={() => actions?.archive?.()}
	/>
{/if}
