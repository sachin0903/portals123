<script lang="ts">
	/**
	 * One message, in full: the envelope, the delivery timeline, and the body as it was sent.
	 *
	 * A page rather than a side pane. The body is the substance — "did they get it" is nearly always
	 * followed by "what did it say" — and it wants the width.
	 *
	 * The timeline runs HORIZONTALLY, one node per thing the provider told us, because it is a
	 * sequence of a handful of steps rather than a list: read left to right it shows the shape of
	 * the delivery (sent → delayed → delivered) at a glance, which a stacked list does not.
	 *
	 * The body renders through `EmailPreviewFrame` — the product's ONE way of putting an email on
	 * screen, a `sandbox=""` iframe for the reason its own header spells out: the HTML is
	 * firm-authored template output that is deliberately not sanitised server-side, so rendering it
	 * into the portal's document would run injected script under the adviser's session. Do not swap
	 * it for a raw HTML injection.
	 */
	import type { Component } from 'svelte';
	import MailIcon from '@lucide/svelte/icons/mail';
	import SendIcon from '@lucide/svelte/icons/send';
	import RefreshCwIcon from '@lucide/svelte/icons/refresh-cw';
	import MailCheckIcon from '@lucide/svelte/icons/mail-check';
	import Undo2Icon from '@lucide/svelte/icons/undo-2';
	import ShieldAlertIcon from '@lucide/svelte/icons/shield-alert';
	import CircleXIcon from '@lucide/svelte/icons/circle-x';
	import MailXIcon from '@lucide/svelte/icons/mail-x';
	import { Card, CardContent } from '../ui/card/index.js';
	import { StatusBadge } from '../ui/status-badge/index.js';
	import { CopyButton } from '../ui/copy-button/index.js';
	import * as Tabs from '../ui/tabs/index.js';
	import { EmailPreviewFrame } from '../email/index.js';
	import { formatHtml } from '../email-editor/code-view/format.js';
	import { highlightHtml } from '../email-editor/code-view/highlight.js';
	import { highlightJson } from '../audit/highlight-json.js';
	import { htmlToPlainText } from './html-to-text.js';
	import { cn } from '../../utils.js';
	import type { CommsMessage } from './describe.js';
	import { recipientLabel, statusMeta, templateLabel } from './describe.js';
	import { LINK_RECORD } from '../../text-shell.js';
	import {
		timelineFor,
		formatRawRequest,
		requestLine as requestLineOf,
		type TimelineEntry
	} from './message-detail.js';

	let {
		message,
		clientHref,
		class: className
	}: {
		message: CommsMessage;
		/** Injected by the app — the shared package never builds a route. Absent ⇒ no link. */
		clientHref?: (contactUid: string) => string;
		class?: string;
	} = $props();

	const timeline = $derived(timelineFor(message));
	const meta = $derived(statusMeta(message.status));
	const href = $derived(
		message.recipient_contact_uid ? clientHref?.(message.recipient_contact_uid) : undefined
	);

	let tab = $state('preview');
	/** Said once. A blank pane reads as a rendering bug; this reads as what actually happened. */
	const NOT_KEPT = 'The body was not kept for this message — it was larger than the ledger stores.';
	const formatted = $derived(message.html ? formatHtml(message.html) : NOT_KEPT);
	/**
	 * Highlighted lines for the two code panes, or null when there is no body to show.
	 *
	 * Both tokenisers already exist and are already shared — `highlightHtml` is the email editor's
	 * own code view, `highlightJson` is the Events and audit detail panes' — so this is the third
	 * READER of each rather than a third implementation. They return per-line `{text, cls}`
	 * segments the markup renders as ESCAPED text: never `@html`, which this package forbids and
	 * which would let a stored email body inject into the portal.
	 *
	 * Null rather than an empty array when there is no body, so the pane says so instead of running
	 * the fallback sentence through an HTML tokeniser and colouring it like markup.
	 */
	const htmlLines = $derived(message.html ? highlightHtml(formatted) : null);
	const plain = $derived(message.html ? htmlToPlainText(message.html) : NOT_KEPT);
	const raw = $derived(formatRawRequest(message));
	/** `POST /emails`, as the ledger recorded it — see `requestLine`. */
	const requestLine = $derived(requestLineOf(message));
	const rawLines = $derived(raw ? highlightJson(raw) : null);

	const ICONS: Record<string, Component> = {
		sent: SendIcon,
		delayed: RefreshCwIcon,
		delivered: MailCheckIcon,
		bounced: Undo2Icon,
		complained: ShieldAlertIcon,
		failed: CircleXIcon,
		suppressed: MailXIcon,
		unconfigured: MailXIcon
	};

	function iconFor(entry: TimelineEntry): Component {
		return ICONS[entry.key] ?? MailIcon;
	}

	function when(iso: string | null, style: 'full' | 'short' = 'full'): string {
		if (!iso) return '—';
		const at = new Date(iso);
		if (Number.isNaN(at.getTime())) return '—';
		return style === 'full'
			? at.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
			: at.toLocaleString(undefined, {
					month: 'short',
					day: '2-digit',
					hour: 'numeric',
					minute: '2-digit'
				});
	}
</script>

<!--
  A numbered, syntax-coloured code block. Shared by the HTML and Raw panes so the two cannot drift
  into different type sizes and gutters, and copied in shape from the audit detail pane, which
  renders JSON exactly this way.

  `whitespace-pre` deliberately: one line per element is what makes markup scannable, and wrapping
  turns a long attribute list into a paragraph. It scrolls sideways instead.
-->
{#snippet codeBlock(lines: { text: string; cls: string }[][])}
	<div class="h-full overflow-auto p-3 font-mono text-[11px] leading-relaxed">
		{#each lines as segs, i (i)}
			<div class="flex">
				<span class="text-muted-foreground/50 w-8 shrink-0 pr-3 text-right tabular-nums select-none"
					>{i + 1}</span
				>
				<code class="whitespace-pre"
					>{#each segs as seg (seg)}<span class={seg.cls}>{seg.text}</span>{/each}</code
				>
			</div>
		{/each}
	</div>
{/snippet}

<div class={cn('mx-auto flex w-[80%] flex-col gap-5', className)}>
	<Card glass>
		<CardContent class="space-y-6">
			<header class="flex items-center gap-4">
				<span
					class="bg-muted text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-lg"
					aria-hidden="true"
				>
					<MailIcon class="size-4" />
				</span>
				<div class="min-w-0 space-y-1">
					<p class="text-muted-foreground text-[0.65rem] font-medium tracking-wide uppercase">
						Email
					</p>
					<div class="flex min-w-0 flex-wrap items-center gap-x-2.5 gap-y-1">
						<h1 class="truncate text-lg font-semibold leading-tight">{message.to_address}</h1>
						<span class="text-muted-foreground shrink-0 text-sm">
							{templateLabel(message.template_code)}
						</span>
						<StatusBadge tone={meta.tone}>{meta.label}</StatusBadge>
					</div>
					{#if href && recipientLabel(message) !== message.to_address}
						<a class="text-muted-foreground text-sm {LINK_RECORD}" {href}>
							{recipientLabel(message)}
						</a>
					{/if}
				</div>
			</header>

			<!-- Four equal columns across the same 80% band as the timeline and the preview, so this
		     strip does not sit as a cluster on the left of a wider pane. -->
			<!-- FROM · SUBJECT · TO · ID, then the rest — the arrangement Resend's own message view
			     uses, and worth matching because these two screens get read side by side when somebody
			     is working out what happened to an email. Reading them in a different order costs a beat
			     every time.
			
			     `auto-rows-min` so a wrapped value does not stretch its neighbours' rows, and each cell
			     is `min-w-0` because `truncate` cannot shrink a grid child that refuses to. -->
			<dl class="grid auto-rows-min grid-cols-2 gap-x-8 gap-y-4 sm:grid-cols-4">
				{#snippet field(label: string)}
					<dt class="text-muted-foreground text-[0.65rem] font-medium tracking-wide uppercase">
						{label}
					</dt>
				{/snippet}

				<!-- FROM leads, as it does there. The envelope AS IT WENT, read out of the stored wire
				     request: on this platform the sender is not a constant — `resolve_sender` sends from
				     the VERIFIED domain and puts the firm, or the adviser, in Reply-To when their own
				     address is not on it — so it is the question behind most delivery complaints. -->
				<div class="min-w-0">
					{@render field('From')}
					<dd class="mt-1.5 truncate text-sm" title={message.from_address ?? undefined}>
						{#if message.from_address}
							{message.from_address}
						{:else}
							<!-- Said rather than omitted, now that the field has a fixed place in the row: a
							     gap in the first column reads as a rendering fault, where "Not recorded" reads
							     as a row written before the request was kept. -->
							<span class="text-muted-foreground text-xs">Not recorded</span>
						{/if}
					</dd>
				</div>

				<div class="min-w-0">
					{@render field('Subject')}
					<dd class="mt-1.5 truncate text-sm" title={message.subject}>{message.subject}</dd>
				</div>

				<div class="min-w-0">
					{@render field('To')}
					<dd class="mt-1.5 truncate text-sm">
						{#if href}
							<a class="underline-offset-2 hover:underline" {href}>{message.to_address}</a>
						{:else}
							{message.to_address}
						{/if}
					</dd>
				</div>

				<!-- RESEND'S id, and the only one shown. Ours is not displayed at all: it is already in
				     the address bar (this page is /emails/{uid}), so putting it here spent a quarter of
				     the row on a value the reader can copy from the URL — while making the field that DOES
				     matter, the one you paste into Resend's dashboard, ambiguous enough to need labelling
				     as "theirs". With one id on screen there is nothing to disambiguate. -->
				<div class="min-w-0">
					{@render field('Id')}
					<dd class="mt-1.5">
						{#if message.provider_message_id}
							<span
								class="bg-muted inline-flex max-w-full items-center rounded-md py-0.5 pr-0.5 pl-2 font-mono text-xs"
							>
								<span class="min-w-0 truncate">{message.provider_message_id}</span>
								<CopyButton
									value={message.provider_message_id}
									label="Copy the provider's message id"
								/>
							</span>
						{:else}
							<!-- No id means nothing was POSTED (suppressed, unconfigured), so its absence is
							     itself the answer to "why is this not in their dashboard". Worth saying in
							     place rather than leaving the column blank. -->
							<span class="text-muted-foreground text-xs">Never posted</span>
						{/if}
					</dd>
				</div>

				<!-- ── second row ──────────────────────────────────────────────────────────────────── -->
				<!-- LOG, the request line as the ledger recorded it — `POST /emails`, the same thing their
				     view shows. It is the shortest honest statement of "we did call them", which for a
				     suppressed row is exactly the thing being questioned. -->
				<div class="min-w-0">
					{@render field('Log')}
					<dd class="mt-1.5 truncate font-mono text-sm" title={requestLine ?? undefined}>
						{#if requestLine}
							{requestLine}
						{:else}
							<span class="text-muted-foreground font-sans text-xs">Not recorded</span>
						{/if}
					</dd>
				</div>

				{#if message.reply_to}
					<div class="min-w-0">
						{@render field('Reply to')}
						<dd class="mt-1.5 truncate text-sm" title={message.reply_to}>
							{message.reply_to}
						</dd>
					</div>
				{/if}

				{#if message.event_type || message.source}
					<div class="min-w-0">
						{@render field('Event')}
						<dd class="mt-1.5 truncate font-mono text-sm">
							{message.event_type ?? message.source}
						</dd>
					</div>
				{/if}
			</dl>

			{#if timeline.length}
				<section class="space-y-3">
					<h2 class="text-muted-foreground text-[0.65rem] font-medium tracking-wide uppercase">
						Email events
					</h2>
					<!-- Dotted grid + a line through the icon centres, continuing past the last node: the
			     delivery story is a sequence, and a trail that stops at the last stamp reads as
			     finished rather than as "this is how far it has got". -->
					<div class="email-events-grid relative overflow-x-auto py-4">
						<ol class="flex min-w-full items-start" aria-label="Email events">
							{#each timeline as entry, i (entry.key)}
								{@const Icon = iconFor(entry)}
								<li class="flex w-36 shrink-0 flex-col items-center gap-1.5 text-center">
									<div class="flex h-10 w-full items-center">
										<span
											class={cn('email-events-line flex-1', i === 0 && 'invisible')}
											aria-hidden="true"
										></span>
										<span
											class="bg-background relative z-10 flex size-10 shrink-0 items-center justify-center rounded-lg border shadow-xs"
										>
											<Icon class="size-4" />
										</span>
										<span class="email-events-line flex-1" aria-hidden="true"></span>
									</div>
									<StatusBadge tone={entry.tone}>{entry.label}</StatusBadge>
									<p class="text-muted-foreground text-xs tabular-nums">
										{when(entry.at, 'short')}
									</p>
									{#if entry.times}
										<p class="text-muted-foreground/80 text-xs">{entry.times} times</p>
									{/if}
								</li>
							{/each}
							<li class="min-w-8 flex-1 self-start" aria-hidden="true">
								<div class="flex h-10 items-center">
									<span class="email-events-line w-full"></span>
								</div>
							</li>
						</ol>
					</div>
				</section>
			{/if}
		</CardContent>
	</Card>

	<!-- `bg-card`, not the page ground: this is a card holding the message, and a tinted panel
	     read as a disabled region rather than as the body of the email. -->
	<section class="bg-card flex h-[42rem] w-full flex-col overflow-hidden rounded-lg border">
		{#if !message.html && !raw}
			<p class="text-muted-foreground p-4 text-sm">{NOT_KEPT}</p>
		{:else}
			<Tabs.Root bind:value={tab} class="flex min-h-0 flex-1 flex-col gap-0">
				<div class="flex shrink-0 items-center border-b px-2 py-1">
					<Tabs.List>
						<Tabs.Trigger value="preview">Preview</Tabs.Trigger>
						<Tabs.Trigger value="plain">Plain text</Tabs.Trigger>
						<Tabs.Trigger value="html">HTML</Tabs.Trigger>
						<Tabs.Trigger value="raw">Raw</Tabs.Trigger>
					</Tabs.List>
				</div>
				<Tabs.Content value="preview" class="min-h-0 flex-1 overflow-hidden p-0">
					{#if message.html}
						<EmailPreviewFrame fill html={message.html} title="The email as it was sent" />
					{:else}
						<p class="text-muted-foreground p-4 text-sm">{NOT_KEPT}</p>
					{/if}
				</Tabs.Content>
				<Tabs.Content value="plain" class="min-h-0 flex-1 overflow-hidden p-0">
					<pre
						class="h-full overflow-auto p-4 text-sm leading-relaxed break-words whitespace-pre-wrap">{plain}</pre>
				</Tabs.Content>
				<Tabs.Content value="html" class="min-h-0 flex-1 overflow-hidden p-0">
					<!-- Pretty-printed and NOT wrapped. One line per element is what makes markup
					     scannable; wrapping turns a long attribute list into a paragraph and loses the
					     structure. It scrolls sideways instead — the fix for "everything on one line"
					     is the formatter handling the doctype (see code-view/format.ts), not soft
					     wrapping. -->
					{#if htmlLines}
						{@render codeBlock(htmlLines)}
					{:else}
						<p class="text-muted-foreground p-4 text-sm">{NOT_KEPT}</p>
					{/if}
				</Tabs.Content>
				<Tabs.Content value="raw" class="min-h-0 flex-1 overflow-hidden p-0">
					<!-- The POST as JSON where the ledger kept it; for a send that predates that, a
					     RECONSTRUCTION which names itself as one (`_note`, see formatRawRequest).
					     Coloured by the SAME tokeniser the Events and audit detail panes use, and
					     rendered as escaped text — the markup inside the request is the thing being
					     read, so it must never go through `@html`. -->
					{#if rawLines}
						{@render codeBlock(rawLines)}
					{:else}
						<p class="text-muted-foreground p-4 text-sm">{NOT_KEPT}</p>
					{/if}
				</Tabs.Content>
			</Tabs.Root>
		{/if}
	</section>
</div>

<style>
	.email-events-grid {
		background-image: radial-gradient(
			circle,
			color-mix(in oklch, var(--foreground) 12%, transparent) 1px,
			transparent 1.2px
		);
		background-size: 12px 12px;
	}

	/* `--border` is the same weight as the dots, so a 1px hairline vanished. Two pixels of
	   muted-foreground is the axis the nodes sit on. */
	.email-events-line {
		height: 2px;
		background: color-mix(in oklch, var(--muted-foreground) 55%, transparent);
	}
</style>
