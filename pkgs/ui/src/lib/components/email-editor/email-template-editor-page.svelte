<script lang="ts">
	// The shared email-template EDIT page — the whole editor surface (top bar · compose strip ·
	// save/publish forms · GrapesJS canvas · preview dialog) as ONE component, mounted by BOTH the
	// platform firm surface (`/templates/emails/[uid]/edit`) and the admin surfaces
	// (`/tenants/[uid]/email-templates/[templateId]/edit`) over their own loaders + `?/save`/`?/publish`
	// actions. It exists once so a firm's copy and an operator's copy can never drift.
	//
	// This package cannot import SvelteKit's virtuals (`$app/forms`, `$app/navigation`) — they only
	// exist inside an app — so the app INJECTS `enhance` + `beforeNavigate` + an `onPublished` callback,
	// exactly as the book surfaces do. Everything app-specific (hrefs, labels, the no-permission copy,
	// the preview/send-test URLs, the sender) comes in as props; all the editor STATE lives here.
	import type { Snippet } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import { Badge } from '../ui/badge/index.js';
	import { Button } from '../ui/button/index.js';
	import { ConfirmDialog } from '../ui/confirm-dialog/index.js';
	import { Input } from '../ui/input/index.js';
	import { Label } from '../ui/label/index.js';
	import EmailEditor from './email-editor.svelte';
	import FieldsReferenceDialog from './fields-reference-dialog.svelte';
	import PreviewDialog from './preview-dialog.svelte';
	import { defaultAudienceFor, type PreviewPayload } from './preview.js';
	import { createTestSender, testSendFeedback } from './send-test.js';
	import { defaultComposeFields, type EmailDefaultContent } from './default-content.js';
	import type { Enhance } from '../book/clients/form-enhance.js';
	import * as DropdownMenu from '../ui/dropdown-menu/index.js';
	import { insertVocabulary } from './merge-fields.js';
	import { readSubjects, type SubjectKind } from './scopes.js';
	import { insertAtCaret } from './token-insert.js';
	import EllipsisIcon from '@lucide/svelte/icons/ellipsis';
	import BracesIcon from '@lucide/svelte/icons/braces';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import SendIcon from '@lucide/svelte/icons/send';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';

	type Version = {
		uid: string;
		status: string;
		subject: string;
		preheader: string;
		/** The editor's own jsonb. */
		schema?: {
			html?: string;
			css?: string;
			source?: string;
			subjects?: unknown;
		} | null;
	} | null;

	/** The slice of the app's `ActionData` this page reads — structural, so either app's union fits. */
	type EditorForm =
		| {
				action?: string;
				ok?: boolean;
				error?: string;
				vid?: string;
				status?: string;
				/** Merge tokens the published version references that nothing supplies — they render
				 *  BLANK on every send. A WARNING: the publish succeeded. */
				unsourced?: string[];
		  }
		| null
		| undefined;

	let {
		template,
		version,
		defaultContent = null,
		canManage,
		documentTitle,
		backHref,
		backLabel,
		previewUrl,
		sendTestUrl,
		senderName = 'Your firm',
		brandLogoUrl = null,
		brandName = null,
		brandColours = [],
		noPermission,
		notice,
		form,
		enhance,
		beforeNavigate,
		goto,
		onPublished
	}: {
		template: { uid: string; code: string; name: string };
		version: Version;
		/** The shipped default for this template's code (null for a custom code, which has none) —
		 *  its body seeds a blank canvas and its subject + preheader fill the compose strip. */
		defaultContent?: EmailDefaultContent | null;
		canManage: boolean;
		/** `<title>` for this surface — each app frames its own scope (firm vs operations). */
		documentTitle: string;
		backHref: string;
		backLabel: string;
		previewUrl: string;
		/** The endpoint that mails the live canvas to the SIGNED-IN AUTHOR'S OWN address (the app's own
		 * token-safe proxy). Absent ⇒ no "Send test" button at all, the same way an absent mutation
		 * callback makes a book surface read-only. It also never renders without `canManage`: the API
		 * gates the send on the write permission, so offering it to a viewer would only produce a 403.
		 * There is deliberately no recipient anywhere in this flow — see `send-test.ts`. */
		sendTestUrl?: string;
		/** Sender name in the preview's inbox chrome (platform passes the firm brand). */
		senderName?: string;
		/** The firm's logo URL, for the canvas's masthead preview. Whose logo differs by surface: the
		 *  firm portal passes its own, the operations one passes the TARGET tenant's. */
		brandLogoUrl?: string | null;
		/** The firm's square mark, for the Brand icon block's canvas paint. */
		/** Painted as the masthead when the firm has no brand image — see `EmailEditor`. */
		brandName?: string | null;
		/** The colours of the firm WHOSE TEMPLATE THIS IS, offered as swatches in the colour pickers
		 * (`emailBrandColours` derives them from that firm's branding). A prop, not something this
		 * package reads: the admin surface authors a different tenant's templates than the one the
		 * admin portal is branded as, so its own `page.data.branding` would be the wrong answer.
		 * Empty ⇒ no swatches. */
		brandColours?: readonly string[];
		/** The read-only alert body — copy differs per app (which permission grants editing). */
		noPermission?: Snippet;
		/** One short line of posture, shown in the top bar beside the status. The ops mount says whose
		 *  behalf the operator is acting on — the LISTING said it and the editor did not, and the editor
		 *  is where a firm's client-facing copy is actually changed and published. In the bar rather
		 *  than a banner: an Alert here costs the canvas its height on every open, for a sentence that
		 *  does not change. */
		notice?: Snippet;
		/** The app's latest `ActionData` (save/publish outcome), for the status badge + error banner. */
		form?: EditorForm;
		/** SvelteKit's `enhance`, injected because this package cannot import `$app/forms`. */
		enhance: Enhance;
		/** SvelteKit's `beforeNavigate`, injected — registers the unsaved-changes guard. `type` and `to`
		 *  are read as well as `cancel`: a full page unload cannot be answered with our own dialog. */
		beforeNavigate: (
			fn: (nav: { cancel: () => void; type: string; to?: { url: URL } | null }) => void
		) => void;
		/** SvelteKit's `goto`, injected — resumes the navigation the guard cancelled. */
		goto: (url: string) => Promise<unknown>;
		/** Called after a successful publish so the app can `invalidateAll()`. */
		onPublished?: () => void | Promise<void>;
	} = $props();

	// `use:` needs a plain identifier for the injected action; `$derived` tracks the (stable) prop.
	const enhanceForm: Enhance = $derived(enhance);

	let previewOpen = $state(false);
	// The editor instance, for reading live canvas content at submit/preview time. current() returns
	// null while GrapesJS is still loading — callers fall back to the seeded html/css.
	let editorRef:
		{ current: () => { html: string; css: string; source: string } | null } | undefined = $state();
	// Set on any subject/preheader/canvas edit, cleared on save and on (re)seed. Drives the "Unsaved"
	// hint and the navigate-away guard.
	let dirty = $state(false);

	// Editor state: subject/preheader bind straight to the inputs; html/css come from the GrapesJS
	// canvas (via onChange) and are submitted through the save form's hidden inputs.
	let subject = $state('');
	let preheader = $state('');
	let html = $state('');
	let css = $state('');
	/** The editor's own copy — what the canvas is seeded from and what `?/save` stores alongside the
	 *  compiled `html`. See the seeding effect for why the canvas must never be fed `html`. */
	let source = $state('');
	/** WHAT THE EMAIL IS ABOUT. A setting, not body content — but it lives in the same `schema` jsonb,
	 *  so it is re-posted with every save (the save REPLACES the schema, and a claim made at creation
	 *  and not carried here is lost the first time anyone edits the body). */
	let subjects = $state<SubjectKind[]>([]);
	let contextOpen = $state(false);

	// Seed from the head version (blank for a template with no versions yet). Keyed on the version
	// uid so a reload (e.g. after publish) doesn't clobber unsaved edits to the same draft — and
	// reading `version` inside an effect avoids the one-time `state_referenced_locally` capture.
	let seededUid = $state<string | null>(null);
	$effect(() => {
		const v = version;
		const id = v?.uid ?? null;
		if (id === seededUid) return;
		seededUid = id;
		subject = v?.subject ?? '';
		preheader = v?.preheader ?? '';
		// SOURCE FIRST. `html` is the render-ready form — an authored condition is already compiled into
		// Jinja there and its `data-if` stripped — so seeding the canvas from it loses every condition
		// the moment a template is reopened, and the next save writes the compiled Jinja back as if an
		// author had typed it. `source` is the editor's own copy. The fallback is not defensive: a
		// version saved before `source` existed has none, and `html` holds no conditions to lose.
		source = v?.schema?.source ?? v?.schema?.html ?? '';
		html = v?.schema?.html ?? '';
		css = v?.schema?.css ?? '';
		subjects = readSubjects(v?.schema);
		dirty = false; // freshly loaded saved content is clean
		// …and the editor has not told us its own shape of it yet, so the next settled emission is the
		// baseline rather than an edit. See `baseline`.
		baseline = null;
	});

	// Warn before leaving with unsaved edits, by the recipe docs/design-language.md names: cancel the
	// navigation, open a `ConfirmDialog`, re-`goto` on confirm (as `flows/[id]` and `forms/[id]/edit`
	// do). Never a raw `window.confirm` — the design language forbids native popups outright.
	//
	// A FULL UNLOAD IS THE ONE EXCEPTION, and it is not a gap. A reload or a closed tab cannot be held
	// open long enough to answer a dialog of ours; cancelling is what makes the BROWSER show its native
	// prompt, which is the only thing available there. So `leave` cancels and stops.
	//
	// The injected `beforeNavigate` registers once at init — capturing its initial value is intended.
	let guardOpen = $state(false);
	// Plain, not `$state`: it is only ever READ inside `leaveWithoutSaving`, and `guardOpen` is what the
	// dialog renders from.
	let pendingNav: string | null = null;
	// svelte-ignore state_referenced_locally
	beforeNavigate((nav) => {
		if (!dirty) return;
		nav.cancel();
		if (nav.type === 'leave') return;
		pendingNav = nav.to?.url?.href ?? backHref;
		guardOpen = true;
	});

	/** Leave anyway. `dirty` is cleared FIRST, or the guard fires again on the re-issued navigation and
	 *  the author is asked the same question for ever. */
	async function leaveWithoutSaving(): Promise<void> {
		const to = pendingNav;
		pendingNav = null;
		dirty = false;
		if (to) await goto(to);
	}

	let saving = $state(false);
	let publishing = $state(false);
	let publishOpen = $state(false);

	/**
	 * Submit the publish form, from the dialog's confirm. `requestSubmit`, not `submit`: it runs the
	 * form's own submit handling, which is what `use:enhance` hangs off — a bare `submit()` bypasses it
	 * and does a full page POST, losing the progressive enhancement and the `onPublished` callback.
	 */
	function doPublish(): void {
		const form = document.getElementById('et-publish') as HTMLFormElement | null;
		form?.requestSubmit();
	}

	/** Save the draft from code, for the context panel's own Save. `requestSubmit` for the reason
	 *  `doPublish` gives — a bare `submit()` skips `use:enhance` and does a full page POST. */
	function doSave(): void {
		const form = document.getElementById('et-save') as HTMLFormElement | null;
		form?.requestSubmit();
	}

	/**
	 * The canvas content as the EDITOR last settled on it, which is what "unsaved" has to be measured
	 * against — NEVER the stored string. The editor's output can never equal what was stored:
	 * `ensurePageCell` wraps the content in a page table the stored form does not have, and GrapesJS
	 * normalises whitespace and attributes on top (measured on the password-reset template, 3055 chars in
	 * the database against 3310 from the canvas), so comparing against it marks every freshly-loaded
	 * template dirty. The first settled emission after a load becomes the baseline instead — the editor's
	 * own idea of the loaded document — and only a later emission that differs from it is an edit.
	 */
	let baseline = $state<{ html: string; css: string; source: string } | null>(null);

	// Error banner: `error` only exists on the failed-action branches of the union.
	const formError = $derived(form && 'error' in form ? form.error : undefined);
	// Only from a SUCCESSFUL publish: a failed one has an error to show instead, and a save says
	// nothing about sourcing because the send reads the published version.
	const publishWarnings = $derived(
		form?.action === 'publish' && form.ok ? (form.unsourced ?? []) : []
	);

	// The version we can publish: a just-saved draft wins; otherwise the head draft (if any).
	// A published head with no fresh save leaves this empty, which disables Publish.
	const publishVid = $derived.by(() => {
		if (form?.action === 'save' && form.ok) return form.vid ?? '';
		if (version && version.status === 'draft') return version.uid;
		return '';
	});

	/**
	 * Whose BRAND the preview and the test send are rendered in — never who receives the real email
	 * (the send site decides that). Derived from the template's CODE and deliberately not switchable:
	 * the code already answers it correctly (`task_assigned`/`task_reminder` really do go out
	 * staff-branded to an assignee, everything else client-branded), and for most firms both portals
	 * resolve to the same branding, so an override would re-render an identical preview, and a control
	 * that changes nothing visible teaches an author that the setting does not matter. The value still
	 * travels in both requests, so the API contract carries an audience either way. `defaultAudienceFor`
	 * is pure and tested.
	 */
	const audience = $derived(defaultAudienceFor(template.code));

	// The content BOTH the preview and the test send are rendered from: the live canvas, read at click
	// time, because the seeded `html`/`css` state lags by the editor's ~250ms sync debounce — a tweak
	// made just before clicking would otherwise be missing from the very thing the author clicked to
	// inspect. subject/preheader are always live (bound inputs).
	//
	// Typed as `PreviewPayload` on purpose: it is the ONE payload builder both actions use, so the
	// compiler — not a reviewer — is what stops a preview and a test send disagreeing about audience.
	function liveContent(): PreviewPayload {
		const c = editorRef?.current();
		return {
			subject,
			preheader,
			html: c?.html ?? html,
			css: c?.css ?? css,
			audience
		};
	}

	// THE SUBJECT AND PREVIEW LINE NEED THE FIELD MENU TOO. They are the only places an author would
	// otherwise type Jinja by hand, and a mis-typed token renders BLANK — a subject reading "Your
	// valuation is ready" with the firm's name silently missing. The caret is remembered on every
	// interaction because opening the menu takes focus off the input, at which point
	// `selectionStart` is gone; unknown ⇒ `insertAtCaret` appends.
	let headCaret = $state<{ field: 'subject' | 'preheader'; start: number; end: number } | null>(
		null
	);

	function rememberCaret(field: 'subject' | 'preheader', el: HTMLInputElement): void {
		headCaret = {
			field,
			start: el.selectionStart ?? el.value.length,
			end: el.selectionEnd ?? el.value.length
		};
	}

	function insertIntoHead(field: 'subject' | 'preheader', token: string): void {
		const at = headCaret?.field === field ? headCaret : null;
		const current = field === 'subject' ? subject : preheader;
		const { value, caret } = insertAtCaret(current, token, at?.start, at?.end);
		if (field === 'subject') subject = value;
		else preheader = value;
		dirty = true;
		headCaret = { field, start: caret, end: caret };
		// Focus back where the author was, caret past the token, so they can keep typing.
		const el = document.getElementById(`et-${field}`) as HTMLInputElement | null;
		el?.focus();
		el?.setSelectionRange?.(caret, caret);
	}

	// One in-flight flag rather than the preview's stale-response guard: a superseded preview can be
	// dropped because nothing happened, but every send that resolves has put mail on the wire and has
	// to be reported — so a second send is prevented instead of discarded.
	let sendingTest = $state(false);
	const sendTest = $derived(sendTestUrl ? createTestSender(sendTestUrl) : null);

	async function onSendTest(): Promise<void> {
		const send = sendTest;
		if (!send || sendingTest) return;
		sendingTest = true;
		try {
			const { tone, message } = testSendFeedback(await send(liveContent()));
			// Three outcomes, three tones — a suppressed send is a warning naming the setting, never a
			// success it hasn't earned and never a failure to go hunting.
			if (tone === 'success') toast.success(message);
			else if (tone === 'warning') toast.warning(message);
			else toast.error(message);
		} finally {
			sendingTest = false;
		}
	}

	// Status shown in the header badge — follow the latest successful action, else the loaded head.
	const currentStatus = $derived.by(() => {
		if (form?.action === 'publish' && form.ok) return form.status ?? 'published';
		if (form?.action === 'save' && form.ok) return form.status ?? 'draft';
		return version?.status ?? 'draft';
	});
</script>

<svelte:head><title>{documentTitle}</title></svelte:head>

<!-- Full-height editor surface: ONE flex column filling the viewport below the shell top bar
     (`--topbar-h`), bled out of the shell padding so it's flush to the sidebar + bottom — the same
     pattern the document/flow editors use. Top bar + compose are fixed rows; the editor takes the
     rest (flex-1) and owns the only scrollbar. No `100svh-Nrem` magic number, so it fits every
     shell — including the admin SaaSShell (whose top bar a hardcoded number could never model). -->
<div
	class="bg-card -m-4 flex h-[calc(100svh-var(--topbar-h,4rem))] flex-col overflow-hidden md:-m-6"
>
	<!-- Top bar: back · name · status · actions on ONE row (not PageHeader — minimal vertical cost). -->
	<div class="flex flex-none flex-wrap items-center gap-x-3 gap-y-2 border-b px-4 py-3 md:px-6">
		<a
			href={backHref}
			class="text-muted-foreground hover:text-foreground inline-flex items-center gap-1 text-sm transition-colors"
		>
			<ChevronLeftIcon class="size-4" />
			{backLabel}
		</a>
		<span class="text-border" aria-hidden="true">/</span>
		<h1 class="text-sm font-semibold">{template.name}</h1>
		<Badge variant={currentStatus === 'published' ? 'default' : 'outline'}>
			{currentStatus === 'published' ? 'Published' : 'Draft'}
		</Badge>
		{#if notice}
			<span
				class="bg-warning-subtle text-warning-strong inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs"
				role="note"
			>
				<TriangleAlertIcon class="size-3.5 shrink-0" aria-hidden="true" />
				{@render notice()}
			</span>
		{/if}

		<div class="ml-auto flex flex-wrap items-center gap-2">
			{#if canManage && dirty}
				<span class="text-warning text-xs">Unsaved</span>
			{/if}

			<!-- THE SECONDARY ACTIONS LIVE BEHIND ONE OVERFLOW, and only Save/Publish stay as buttons.
			     Five buttons in a row read as five things of equal weight, and they are not: Save draft and
			     Publish COMMIT, and the rest are ways of looking at the draft or describing it. The
			     reference editor makes the same split with a `…` menu.
			     What the email is ABOUT belongs here rather than in the email's head or the style rail —
			     the head is the letter as a recipient sees it, and the rail is the selected element's
			     styles; the claim is authoring metadata about the letter. -->
			<DropdownMenu.Root>
				<DropdownMenu.Trigger
					class="text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:ring-ring/50 flex size-8 shrink-0 items-center justify-center rounded-md border transition-colors focus-visible:ring-2 focus-visible:outline-none"
					aria-label="More template actions"
					title="More actions"
				>
					<EllipsisIcon class="size-4" />
				</DropdownMenu.Trigger>
				<DropdownMenu.Content align="end" class="w-52">
					<DropdownMenu.Item onSelect={() => (previewOpen = true)}>
						<EyeIcon class="mr-2 size-4" /> Preview
					</DropdownMenu.Item>
					<!-- The context panel. Reachable here as well as from the rail, because the question
					     ("what can this email even say?") comes up before any selection or flyout. -->
					<DropdownMenu.Item onSelect={() => (contextOpen = true)}>
						<BracesIcon class="mr-2 size-4" /> What this email is about
					</DropdownMenu.Item>
					{#if canManage && sendTest}
						<!-- The inbox test. Sends the LIVE canvas to the author's own address — no recipient is
						     asked for or accepted, so there is nothing here to aim at a client. -->
						<DropdownMenu.Item disabled={sendingTest} onSelect={onSendTest}>
							<SendIcon class="mr-2 size-4" />
							{sendingTest ? 'Sending…' : 'Send test'}
						</DropdownMenu.Item>
					{/if}
				</DropdownMenu.Content>
			</DropdownMenu.Root>

			{#if canManage}
				<!-- Submit the body forms via `form=` — the actions live here, the forms in the body. -->
				<Button type="submit" form="et-save" size="sm" disabled={saving}>
					{saving ? 'Saving…' : 'Save draft'}
				</Button>
				<!-- PUBLISH ASKS FIRST, and Save deliberately does not. They are not the same act: a draft is
				     private and reversible, where publishing REPLACES the email every future send of this
				     code goes out as — immediately, for every client, with no undo.
				     `type="button"`, not a submit: the form is submitted by the dialog's confirm instead. -->
				<Button
					type="button"
					size="sm"
					variant="secondary"
					disabled={!publishVid || publishing || dirty}
					title={dirty
						? 'Save your changes first'
						: !publishVid
							? 'Save a draft before publishing'
							: ''}
					onclick={() => (publishOpen = true)}
				>
					{publishing ? 'Publishing…' : 'Publish'}
				</Button>
			{/if}
		</div>
	</div>

	{#if !canManage}
		<Alert class="mx-4 mt-3 md:mx-6">
			<AlertDescription>
				{#if noPermission}
					{@render noPermission()}
				{:else}
					You can view this template but not change it.
				{/if}
			</AlertDescription>
		</Alert>
	{/if}

	{#if formError}
		<Alert variant="destructive" class="mx-4 mt-3 md:mx-6">
			<AlertDescription>{formError}</AlertDescription>
		</Alert>
	{/if}

	<!-- Reported at PUBLISH, because that is the moment an author is looking. The same list has always
	     been reachable through Preview and nowhere else, so a template published without opening
	     Preview shipped its blanks unremarked. Not an error: the publish succeeded, and the wording
	     says what was checked rather than pronouncing the template clean — a token behind an
	     `{% if %}` is never evaluated and a present-but-empty value is not unsourced. Same sentence
	     as the preview dialog, so an author reads one form of words in both places. -->
	{#if publishWarnings.length}
		<Alert class="mx-4 mt-3 md:mx-6">
			<AlertDescription>
				Published. These fields had no sample value and rendered empty:
				<span class="font-medium">{publishWarnings.join(', ')}</span>. Check the token spelling if
				that's unexpected.
			</AlertDescription>
		</Alert>
	{/if}

	<!-- The save form, holding only the hidden content inputs. Subject and Preview live in the email's
	     own head at the top of the canvas and reach this form by `form="et-save"`, the attribute the top
	     bar's Save and Publish buttons already submit through: an input's owning form is an attribute,
	     not a position, so the action contract is the same wherever the inputs sit. -->
	<form
		id="et-save"
		method="POST"
		action="?/save"
		use:enhanceForm={({ formData }) => {
			// Read the editor's live content at submit time so an edit made within the ~250ms sync
			// debounce (drag/delete/style/seed) isn't lost to a stale hidden-input value.
			const c = editorRef?.current();
			if (c) {
				formData.set('html', c.html);
				formData.set('css', c.css);
				// `source` TOO, or a save inside the debounce posts the compiled html live and the source
				// stale — and the stale one is what the canvas is seeded from next time.
				formData.set('source', c.source);
			}
			// THE CLAIM, read from state rather than left to the hidden input. `doSave()` is called FROM
			// CODE the moment the context panel saves, and `requestSubmit` gathers the form synchronously
			// — before Svelte has flushed the new value into the input — so the panel's own Save stored
			// the PREVIOUS claim, one edit behind for ever. An empty string still means "post no key at
			// all", which is what keeps "absent" and "claims none" a single state.
			formData.set('subjects', subjects.length ? JSON.stringify(subjects) : '');
			saving = true;
			return async ({ result, update }) => {
				await update({ reset: false });
				if (result.type === 'success') {
					dirty = false;
					// THE EDITOR'S OWN READING OF WHAT WAS POSTED IS THE CLEAN STATE — `c`, never the state
					// trio, which lags the 250ms sync debounce. A null reading (the canvas had not resolved,
					// so the stored strings were posted) leaves the next settled emission as the baseline,
					// exactly as a load does, rather than pinning a stored-string shape the editor cannot
					// reproduce.
					baseline = c ?? null;
				}
				saving = false;
			};
		}}
	>
		<input type="hidden" name="html" value={html} />
		<input type="hidden" name="css" value={css} />
		<input type="hidden" name="source" value={source} />
		<!-- Blank when the email claims nothing, so the app's action posts no `subjects` key and the
		     schema never acquires an empty one — "absent" and "claims none" stay one state. -->
		<input type="hidden" name="subjects" value={subjects.length ? JSON.stringify(subjects) : ''} />
	</form>

	{#if canManage}
		<!-- Publish is a separate (empty) form; its button lives in the top bar via form="et-publish". -->
		<form
			id="et-publish"
			method="POST"
			action="?/publish"
			use:enhanceForm={() => {
				publishing = true;
				return async ({ update }) => {
					await update({ reset: false });
					await onPublished?.();
					publishing = false;
				};
			}}
		>
			<input type="hidden" name="vid" value={publishVid} />
		</form>
	{/if}

	<!--
		THE EMAIL'S HEAD, rendered inside the canvas column by `EmailEditor` (which owns the position and
		the width, so it lines up with the 600px card below it). On the sheet rather than in a form above
		the workspace, because what an author is looking at is the head of a letter — the subject line,
		and the preview text an inbox shows after it.
	-->
	{#snippet documentHeader()}
		<!-- Rows only. The sheet around them — white, the frame's radius, the frame's shadow, the
		     frame's width — belongs to `.gjs-email-head` in `create.ts`, declared next to the card's own
		     treatment so the two cannot drift into looking like different kinds of object. -->
		<div>
			<div class="divide-border divide-y">
				<!-- Subject and Preview: the two lines an author actually writes. DELIBERATELY NO From /
				     Reply-To ROWS — both are resolved server-side per firm and neither is editable here, so
				     they would occupy a third of the head to tell an author something they cannot act on
				     (`resolve_sender` owns the envelope; the firm's branding page is where it changes).
				     They use `Input`, so the field shell, focus ring and iOS text-size rule all come from one
				     place, with only the border and background overridden — exactly the one-property
				     divergence the design language allows: at rest they read as document lines, and they
				     reveal as fields on hover and focus.

				     THE SUBJECT'S PLACEHOLDER IS JUST THE WORD, never a worked example: placeholder grey
				     carrying a real merge token reads as a subject somebody already wrote, and the one thing
				     an author must be able to tell at a glance is whether the line says anything yet. -->
				<div class="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3.5 py-1.5">
					<Label for="et-subject" class="text-muted-foreground w-16 shrink-0 text-xs font-medium">
						Subject
					</Label>
					<Input
						id="et-subject"
						name="subject"
						form="et-save"
						bind:value={subject}
						oninput={() => (dirty = true)}
						readonly={!canManage}
						placeholder="Subject"
						onselect={(e) => rememberCaret('subject', e.currentTarget as HTMLInputElement)}
						onkeyup={(e) => rememberCaret('subject', e.currentTarget as HTMLInputElement)}
						onclick={(e) => rememberCaret('subject', e.currentTarget as HTMLInputElement)}
						class="hover:border-input h-8 min-w-48 flex-1 border-transparent bg-transparent px-1.5 font-medium dark:bg-transparent"
					/>
				</div>
				<div class="flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3.5 py-1.5">
					<Label for="et-preheader" class="text-muted-foreground w-16 shrink-0 text-xs font-medium">
						Preview
					</Label>
					<Input
						id="et-preheader"
						name="preheader"
						form="et-save"
						bind:value={preheader}
						oninput={() => (dirty = true)}
						readonly={!canManage}
						placeholder="Inbox preview line, shown after the subject"
						onselect={(e) => rememberCaret('preheader', e.currentTarget as HTMLInputElement)}
						onkeyup={(e) => rememberCaret('preheader', e.currentTarget as HTMLInputElement)}
						onclick={(e) => rememberCaret('preheader', e.currentTarget as HTMLInputElement)}
						class="hover:border-input h-8 min-w-48 flex-1 border-transparent bg-transparent px-1.5 dark:bg-transparent"
					/>
				</div>
			</div>
		</div>
	{/snippet}

	<!-- The visual editor fills the remaining height; the canvas owns the only scrollbar. `fields` is
	     the send site's own vocabulary, so both ways in — the rail's Insert-field flyout and typing
	     `{{` in the canvas — offer one list. -->
	<div class="min-h-0 flex-1">
		<EmailEditor
			bind:this={editorRef}
			html={source}
			{css}
			{documentHeader}
			code={template.code}
			fields={insertVocabulary(template.code)}
			onreference={() => (contextOpen = true)}
			interceptInsert={(token) => {
				// ONE affordance for all three places: the rail's field menu inserts into whichever line
				// the author was last in, and only falls through to the canvas when that is none of them.
				if (!headCaret || !canManage) return false;
				insertIntoHead(headCaret.field, token);
				return true;
			}}
			oncanvasfocus={() => (headCaret = null)}
			{brandColours}
			{brandLogoUrl}
			{brandName}
			readOnly={!canManage}
			defaultBody={defaultContent
				? {
						// `source` FIRST, for the reason the seeding effect gives: the canvas needs conditionals as
						// attributes, not Jinja text. The `?? html` covers a deployment older than
						// `EmailDefaultContent.source`, and it is the lossy path.
						html: defaultContent.source ?? defaultContent.html,
						css: defaultContent.css
					}
				: null}
			onLoadDefault={() => {
				// The canvas has taken the default body; the compose strip takes the rest of the same
				// default — BOTH lines, per `defaultComposeFields`. A blank preheader here publishes an
				// email with no inbox preview text.
				const fields = defaultComposeFields(defaultContent);
				if (fields) {
					subject = fields.subject;
					preheader = fields.preheader;
				}
				dirty = true;
			}}
			onChange={(v) => {
				html = v.html;
				css = v.css;
				source = v.source;
				// THE FIRST EMISSION AFTER A LOAD IS THE BASELINE, however long it takes to arrive. It is
				// the editor's own shape of the SAVED document — the page-cell wrap plus GrapesJS's
				// normalisation, neither of which the author did.
				//
				// DO NOT PUT A TIME WINDOW ON THIS. Past any such window the settling emission is read as an
				// edit, so a canvas slow to settle — a long template, a cold cache, a web font still loading
				// — opens already saying "Unsaved" with nothing touched. A window would only guard against a
				// missed FIRST keystroke, which cannot happen: the canvas emits its own baseline when it
				// seeds (`email-editor.svelte`, right after `lastEmitted = read()`), before anyone can type
				// into it. That emission is load-bearing for exactly this branch — without it the first
				// emission IS the first edit and gets swallowed here, which is how "Starter layout" on an
				// empty template used to leave the page clean and Publish enabled against the old body.
				if (!baseline) {
					baseline = { html: v.html, css: v.css, source: v.source };
					return;
				}
				if (v.html === baseline.html && v.css === baseline.css && v.source === baseline.source)
					return; // a sync that changed nothing — a re-read, a reselect, a settle
				dirty = true;
			}}
		/>
	</div>
</div>

<!-- NO token list of its own: the rail's field flyout is the one place a name is read and inserted. -->
<FieldsReferenceDialog
	bind:open={contextOpen}
	{subjects}
	onsubjects={canManage
		? (next) => {
				// SAVED, not merely marked dirty: what an email is about is a setting, and the panel asks
				// for it deliberately. It rides the draft form because it lives in the same schema — one
				// write, so a claim can never be stored against a body it was not authored for.
				subjects = next;
				dirty = true;
				doSave();
			}
		: undefined}
/>

<!-- `audience` is passed as well as travelling inside `content()`: the dialog needs it to STATE which
     brand the author is looking at, which is the whole point of having chosen one. -->
<PreviewDialog bind:open={previewOpen} {previewUrl} {senderName} {audience} content={liveContent} />

<!-- The publish confirmation, deliberately NOT `destructive`. Publishing is irreversible but it is not
     damage — it is the point of the page — and dressing the primary action in the danger colour
     teaches an author to ignore that colour. The description carries the weight instead, by saying
     what actually changes and for whom. -->
<ConfirmDialog
	bind:open={publishOpen}
	title="Publish this email?"
	description={`Every ${template.name} sent from now on will use this version. It replaces the published one immediately, for every client, and cannot be undone.`}
	confirmLabel="Publish"
	cancelLabel="Keep as draft"
	pendingLabel="Publishing…"
	onconfirm={doPublish}
/>

<!-- The unsaved-changes guard. `destructive`, because confirming discards work that cannot be
     recovered — and ONE sentence of consequence, per the copy rules. -->
<ConfirmDialog
	bind:open={guardOpen}
	title="Leave without saving?"
	description="Your unsaved changes to this template will be lost."
	confirmLabel="Leave"
	cancelLabel="Stay"
	destructive
	onconfirm={leaveWithoutSaving}
/>
