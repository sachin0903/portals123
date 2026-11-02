<script lang="ts">
	/**
	 * Newest-first version list; Restore copies one into a new draft (server-side, never
	 * overwrites/auto-publishes). `versionsUrl`/`onRestored` mirror `PreviewDialog`'s pattern, keeping pkgs/ui free of `$app/navigation`.
	 *
	 * AND EACH ONE CAN BE LOOKED AT. Restore was once the only control, so an author chose off a
	 * subject line and a date and found out what they had picked afterwards — on a template that goes
	 * to a firm's clients. View reads the version through `?vid=` and renders it the way Preview does,
	 * merge fields filled, in the same dialog rather than a second one on top of it.
	 */
	import EmailPreviewFrame from '../email/email-preview-frame.svelte';
	import { Alert, AlertDescription } from '../ui/alert/index.js';
	import { Button } from '../ui/button/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import { StatusBadge, type StatusTone } from '../ui/status-badge/index.js';

	let {
		open = $bindable(false),
		versionsUrl,
		previewUrl = null,
		templateName = '',
		canManage = false,
		onRestored
	}: {
		open?: boolean;
		/** Lists (GET), reads one (`?vid=`) and restores (POST `{ vid }`); `null` keeps the dialog inert. */
		versionsUrl: string | null;
		/** Renders content the way the editor's own Preview does. Absent ⇒ no View (a mount with no
		 *  preview proxy); the list still lists and Restore still restores. */
		previewUrl?: string | null;
		templateName?: string;
		canManage?: boolean;
		/** After a successful restore: the app opens the editor on the new draft (`goto`). */
		onRestored?: () => void | Promise<void>;
	} = $props();

	type Version = {
		uid: string;
		version_number: number;
		status: string;
		subject: string;
		created_at: string;
		created_by: string | null;
		published_at: string | null;
		published_by: string | null;
		/** `'firm'` or `'operations'` (Graphene); `null`/absent predates 0051, authorship unknown. */
		authored_by_scope?: string | null;
	};

	let loading = $state(false);
	let errorMsg = $state<string | null>(null);
	let versions = $state<Version[]>([]);
	let restoring = $state<string | null>(null);
	/** The version being looked at, and the merged html to show for it. */
	let viewing = $state<{ v: Version; html: string } | null>(null);
	let viewLoading = $state<string | null>(null);

	async function view(v: Version): Promise<void> {
		if (!versionsUrl || !previewUrl || viewLoading) return;
		viewLoading = v.uid;
		errorMsg = null;
		try {
			const sep = versionsUrl.includes('?') ? '&' : '?';
			const detail = await fetch(`${versionsUrl}${sep}vid=${encodeURIComponent(v.uid)}`);
			if (!detail.ok) {
				errorMsg = await errText(detail, 'Could not read that version');
				return;
			}
			const full = (await detail.json()) as {
				subject?: string;
				preheader?: string;
				schema?: { html?: string; css?: string };
			};
			// THROUGH PREVIEW, not raw: a stored body is full of unresolved tokens, and a version shown
			// with `{{ first_name }}` in it does not answer the question the author opened it to ask.
			const rendered = await fetch(previewUrl, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					subject: full.subject ?? '',
					preheader: full.preheader ?? '',
					html: full.schema?.html ?? '',
					css: full.schema?.css ?? ''
				})
			});
			if (!rendered.ok) {
				errorMsg = await errText(rendered, 'Could not render that version');
				return;
			}
			const body = (await rendered.json()) as { html?: string };
			viewing = { v, html: body.html ?? '' };
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Could not read that version';
		} finally {
			viewLoading = null;
		}
	}

	async function errText(res: Response, fallback: string): Promise<string> {
		const msg = await res
			.clone()
			.json()
			.then((b) => b?.message)
			.catch(() => null);
		return msg || `${fallback} (${res.status})`;
	}

	async function load(): Promise<void> {
		if (!versionsUrl) return;
		viewing = null;
		loading = true;
		errorMsg = null;
		versions = [];
		try {
			const res = await fetch(versionsUrl);
			if (!res.ok) {
				errorMsg = await errText(res, 'Could not load history');
				return;
			}
			versions = (await res.json()) as Version[];
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Could not load history';
		} finally {
			loading = false;
		}
	}

	// Load each time the dialog opens so history reflects the latest saves/publishes.
	$effect(() => {
		if (open) load();
	});

	async function restore(v: Version): Promise<void> {
		if (!versionsUrl || restoring) return;
		restoring = v.uid;
		errorMsg = null;
		try {
			const res = await fetch(versionsUrl, {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({ vid: v.uid })
			});
			if (!res.ok) {
				errorMsg = await errText(res, 'Restore failed');
				return;
			}
			// Restore made a fresh draft — the app opens the editor on it to review + publish.
			open = false;
			await onRestored?.();
		} catch (e) {
			errorMsg = e instanceof Error ? e.message : 'Restore failed';
		} finally {
			restoring = null;
		}
	}

	function fmtDate(s: string | null): string {
		if (!s) return '';
		const d = new Date(s);
		return Number.isNaN(d.getTime())
			? ''
			: d.toLocaleString('en-GB', { dateStyle: 'medium', timeStyle: 'short' });
	}

	/* `StatusBadge` + tone, never `Badge`. `published` (what send resolves) → success; draft/archived → neutral. */
	const STATUS_META: Record<string, { label: string; tone: StatusTone }> = {
		published: { label: 'Published', tone: 'success' },
		draft: { label: 'Draft', tone: 'neutral' },
		archived: { label: 'Archived', tone: 'neutral' }
	};

	function statusMeta(s: string): { label: string; tone: StatusTone } {
		return STATUS_META[s] ?? { label: s || 'Unknown', tone: 'neutral' };
	}
</script>

<Dialog.Root bind:open>
	<!-- Wide enough for the frame while a version is being READ, narrow for the list. The frame's
	     viewport is 640px and it sets that width inline, so at `max-w-lg` (512px) it spilled out of
	     the dialog and took the footer's background with it. `max-w-3xl` is what `PreviewDialog`
	     gives the same email. -->
	<Dialog.Content class={viewing ? 'sm:max-w-3xl' : 'sm:max-w-lg'}>
		<Dialog.Header>
			<Dialog.Title>
				{viewing ? `Version ${viewing.v.version_number}` : 'Version history'}
			</Dialog.Title>
			<Dialog.Description>
				{#if viewing}
					{templateName} — as it was sent, with sample values.
				{:else}
					{templateName} — look at an earlier version, or restore it as a new draft.
				{/if}
			</Dialog.Description>
		</Dialog.Header>

		{#if errorMsg}
			<Alert variant="destructive"><AlertDescription>{errorMsg}</AlertDescription></Alert>
		{/if}

		{#if viewing}
			<!-- `srcdoc`, not a live document: an email body carries its own styles and must not inherit
			     the app's, which is the same isolation `PreviewDialog` uses. -->
			<p class="text-muted-foreground truncate text-xs">
				Subject: {viewing.v.subject || '(no subject)'}
			</p>
			<!-- THROUGH THE SANDBOXED SINK, never a bare iframe. This body is author-written HTML that
			     the API deliberately does not sanitise (a mail client runs no script, so the markup is
			     policed at the canvas and nowhere else), and a same-origin `srcdoc` frame would run an
			     injected `<script>` or `on*` handler in the app's own origin under the reader's session.
			     The reach is both ways: a firm's stored version renders in a Graphene operator's admin
			     origin, and `clone_published` copies the model tenant's schema verbatim into every new
			     firm. `EmailPreviewFrame` owns `sandbox=""` so it is stated once. -->
			<!-- Sized like `PreviewDialog`, because it is the same email at the same 640px viewport: the
			     frame sets its own width inline, so a `w-full` class here never applied and the box
			     kept the iframe's default 150px — a version was read through a letterbox. The HEIGHT is
			     the caller's to give (the component's own docstring says so) and there was none. -->
			<EmailPreviewFrame
				title={`Version ${viewing.v.version_number}`}
				html={viewing.html}
				class="bg-background h-[52vh] rounded-md border"
			/>
			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => (viewing = null)}>
					Back to versions
				</Button>
				{#if canManage}
					{@const v = viewing.v}
					<Button type="button" disabled={!!restoring} onclick={() => restore(v)}>
						{restoring === v.uid ? 'Restoring…' : 'Restore this version'}
					</Button>
				{/if}
			</Dialog.Footer>
		{:else if loading && !versions.length}
			<p class="text-muted-foreground py-8 text-center text-sm">Loading…</p>
		{:else if !versions.length}
			<p class="text-muted-foreground py-8 text-center text-sm">No versions yet.</p>
		{:else}
			<ul class="max-h-96 divide-y overflow-auto">
				{#each versions as v (v.uid)}
					{@const meta = statusMeta(v.status)}
					<li class="flex items-center gap-3 py-2.5">
						<div class="min-w-0 flex-1">
							<div class="flex flex-wrap items-center gap-2">
								<span class="text-sm font-medium">v{v.version_number}</span>
								<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
								{#if v.authored_by_scope === 'operations'}
									<!-- Top row, not the clipping metadata line. No 'firm' counterpart: NULL scope is genuinely unknown. -->
									<StatusBadge tone="info">Graphene</StatusBadge>
								{/if}
							</div>
							<p class="text-muted-foreground truncate text-xs">
								{v.subject || '(no subject)'} · {fmtDate(
									v.published_at ?? v.created_at
								)}{(v.published_by ?? v.created_by) ? ` · ${v.published_by ?? v.created_by}` : ''}
							</p>
						</div>
						{#if previewUrl}
							<!-- BEFORE Restore, and available to a reader too: looking is not managing. -->
							<Button
								type="button"
								variant="ghost"
								size="sm"
								disabled={!!viewLoading}
								onclick={() => view(v)}
							>
								{viewLoading === v.uid ? 'Opening…' : 'View'}
							</Button>
						{/if}
						{#if canManage}
							<Button
								type="button"
								variant="outline"
								size="sm"
								disabled={!!restoring}
								onclick={() => restore(v)}
							>
								{restoring === v.uid ? 'Restoring…' : 'Restore'}
							</Button>
						{/if}
					</li>
				{/each}
			</ul>
		{/if}

		<!-- The list's own footer, and only the list's: a version being VIEWED has its own (Back to
		     versions / Restore this version), and rendering both stacked two footers on top of each
		     other with the way out in the lower one. -->
		{#if !viewing}
			<Dialog.Footer>
				<Button type="button" variant="outline" onclick={() => (open = false)}>Close</Button>
			</Dialog.Footer>
		{/if}
	</Dialog.Content>
</Dialog.Root>
