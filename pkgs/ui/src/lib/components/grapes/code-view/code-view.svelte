<script lang="ts" module>
	/**
	 * Which document the pane is showing.
	 *
	 * `source` is what the visual editor holds, with the editor's own `data-*` bookkeeping on it, and
	 * is the only shape a hand edit can be written back to. `output` is what that compiles to — the
	 * email a recipient receives, or the Jinja template a document renderer is handed — and is
	 * DERIVED, so it has nowhere to write an edit.
	 *
	 * The two names are the shared concept; what each is CALLED and what the difference means are the
	 * caller's, because "Sent" is meaningless for a document and "Template" is meaningless for an
	 * email. See `shapeLabels` / `shapeNotes`.
	 */
	export type CodeShape = 'source' | 'output';
</script>

<script lang="ts">
	// Resend `</>`-style code view, read-only by default: the right panel edits conditions/repeats/fields
	// through this markup's own `data-*` attributes via the one-way `conditions/compile.ts`, so a hand edit can silently cost that unflagged. Preview is live — same document as the canvas.
	import { Button } from '../../ui/button/index.js';
	import { CopyButton } from '../../ui/copy-button/index.js';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import { Textarea } from '../../ui/textarea/index.js';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import CodeXmlIcon from '@lucide/svelte/icons/code-xml';
	import LockIcon from '@lucide/svelte/icons/lock';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import XIcon from '@lucide/svelte/icons/x';
	import { formatHtml } from './format.js';
	import { highlightHtml } from './highlight.js';
	import { foldRanges, hiddenLines, rangeAt } from './folds.js';

	let {
		html,
		shape,
		onshapechange,
		shapeLabels,
		shapeNotes,
		markerAttrs = [],
		onapply,
		onclose
	}: {
		/** Unformatted GrapesJS HTML (one long line); `format.ts` holds the one rule that keeps re-parsing it safe. */
		html: string;
		shape: CodeShape;
		onshapechange: (shape: CodeShape) => void;
		/** What each shape is called on its tab. */
		shapeLabels: Record<CodeShape, string>;
		/** One sentence per shape, saying what it IS and why editability differs — owed on the tab
		 *  itself rather than left to guess. */
		shapeNotes: Record<CodeShape, string>;
		/** The mounting editor's own bookkeeping attributes, coloured so a hand-editing author can see
		 *  what not to drop. Empty means none are marked, which is honest for an editor that has not
		 *  said (see `highlight.ts::highlightHtml`). */
		markerAttrs?: readonly string[];
		/** Replaces the canvas with hand-edited HTML; absent means no unlock — read-only, or the derived `sent` shape, which has nowhere to write an edit back to. */
		onapply?: (html: string) => void;
		onclose: () => void;
	} = $props();

	/** Hold duration to confirm — long enough to be a decision, short enough not to be a puzzle (Resend's is similar). */
	const HOLD_MS = 800;

	const formatted = $derived(formatHtml(html));

	/** Unlocked = the author held the confirm and is now editing. */
	let unlocked = $state(false);
	/** The text being edited. */
	let draft = $state('');
	/** Draft's starting text — apply must skip when unchanged: GrapesJS re-parses/re-serialises (mints ids, adds `<tbody>`, reorders attrs), so a no-op apply would still read as unsaved; `emitNow` guards this too. */
	let draftBase = $state('');
	let confirmOpen = $state(false);

	const shown = $derived(unlocked ? draft : formatted);
	const lines = $derived(shown.split('\n'));
	const segs = $derived(highlightHtml(shown, markerAttrs));
	/** No folding while editing — the textarea holds every line, and a fold is a reading aid, not an edit. */
	const ranges = $derived(unlocked ? [] : foldRanges(lines));
	let collapsedList = $state<number[]>([]);
	const collapsed = $derived(new Set(collapsedList));
	const hidden = $derived(hiddenLines(ranges, collapsed));

	// A fold is a line-number pair on a live pane; edits below renumber it, so folds key on line COUNT (not text) to survive in-line typing.
	let foldedAt = $state(-1);
	$effect(() => {
		const n = lines.length;
		if (n === foldedAt) return;
		foldedAt = n;
		if (collapsedList.length) collapsedList = [];
	});

	function toggleFold(line: number): void {
		collapsedList = collapsed.has(line)
			? collapsedList.filter((n) => n !== line)
			: [...collapsedList, line];
	}

	// Inline panel, not a Popover: `Popover.Content` portals to `document.body` and misses clicks from inside a focus-trapped surface (the standing `pkgs/ui` gotcha — see `AddressLookup`/`RecordPicker`).
	let holdTick = $state(0);
	let holdTimer: ReturnType<typeof setInterval> | null = null;

	function startHold(): void {
		if (holdTimer) return;
		const from = Date.now();
		holdTick = 1;
		holdTimer = setInterval(() => {
			holdTick = Date.now() - from;
			if (holdTick >= HOLD_MS) {
				cancelHold();
				unlock();
			}
		}, 30);
	}

	function cancelHold(): void {
		if (holdTimer) clearInterval(holdTimer);
		holdTimer = null;
		holdTick = 0;
	}

	/** Keyboard-operable too: `event.repeat` stops a held key from restarting the timer. */
	function onHoldKey(event: KeyboardEvent): void {
		if (event.key !== ' ' && event.key !== 'Enter') return;
		event.preventDefault();
		if (!event.repeat) startHold();
	}

	const holdPct = $derived(Math.min(100, Math.round((holdTick / HOLD_MS) * 100)));

	function unlock(): void {
		confirmOpen = false;
		draft = formatted;
		draftBase = formatted;
		collapsedList = [];
		unlocked = true;
	}

	function discard(): void {
		unlocked = false;
		draft = '';
		draftBase = '';
	}

	function apply(): void {
		// draftBase: an apply that changes nothing must not touch the document, or it reads as unsaved.
		if (draft !== draftBase) onapply?.(draft);
		unlocked = false;
		draft = '';
		draftBase = '';
	}

	$effect(() => () => cancelHold());

	const shapeNote = $derived(shapeNotes[shape]);
	/** Unlock offered only for `source` (the editable shape) and only when `onapply` is given; `output` is derived. */
	const canEdit = $derived(!!onapply && shape === 'source');
</script>

<section class="code-pane" aria-label="HTML code">
	<header class="code-head">
		<CodeXmlIcon class="text-muted-foreground size-3.5 shrink-0" />
		<span class="code-title">HTML code</span>
		<CopyButton value={shown} label="Copy the HTML" />
		<Button
			variant="ghost"
			size="icon-sm"
			aria-label="Close the HTML code view"
			title="Close the HTML code view"
			onclick={onclose}
		>
			<XIcon class="size-4" />
		</Button>
	</header>

	<div class="code-controls">
		<!-- Two questions: what will send, and why the panel stopped offering a condition. `Segmented` (docs/design-language.md) is the mode toggle; Sent is listed first since it's the default. -->
		<Segmented
			size="sm"
			label="Which HTML"
			value={shape}
			options={[
				{ value: 'output', label: shapeLabels.output },
				{ value: 'source', label: shapeLabels.source }
			]}
			onValueChange={(v) => onshapechange(v === 'output' ? 'output' : 'source')}
		/>
		{#if unlocked}
			<div class="code-actions">
				<Button variant="ghost" size="sm" onclick={discard}>Discard</Button>
				<Button size="sm" onclick={apply}>Apply</Button>
			</div>
		{:else if canEdit}
			<!-- Read-only is the default, useful state; this should look like unlocking a padlock, not switching a tab. -->
			<Button
				variant="outline"
				size="sm"
				class="code-unlock"
				aria-expanded={confirmOpen}
				onclick={() => (confirmOpen = !confirmOpen)}
			>
				<LockIcon class="mr-1.5 size-3.5" /> Edit HTML
			</Button>
		{/if}
	</div>

	{#if confirmOpen && canEdit}
		<!-- The confirmation, in OUR words: every sentence is true of this editor specifically. -->
		<div class="code-confirm" role="group" aria-label="Edit the HTML by hand?">
			<p class="code-confirm-title">Edit the HTML by hand?</p>
			<p>What you apply replaces the visual document, and it cannot be undone.</p>
			<p>
				This editor edits conditions, repeats and merge fields through its own attributes —
				<code>data-if</code>, <code>data-choice</code>, <code>data-repeat</code>,
				<code>data-fallback</code>, <code>data-field</code>. Drop one and that condition, repeat or
				field stops being editable in the panel, with nothing on the page to say so.
			</p>
			<p>Jinja you write here stays Jinja: nothing can read it back into a condition.</p>
			<div class="code-confirm-actions">
				<Button variant="ghost" size="sm" onclick={() => (confirmOpen = false)}>Cancel</Button>
				<!-- Hold, not click — a click risks a slip-of-hand replace with no undo; works via pointer and keyboard (Space/Enter, key-up cancels). Not `destructive`: irreversible isn't damage (the publish-confirm precedent) — the amber panel carries the warning, this button the gesture. -->
				<Button
					size="sm"
					class="relative overflow-hidden"
					onpointerdown={startHold}
					onpointerup={cancelHold}
					onpointerleave={cancelHold}
					onpointercancel={cancelHold}
					onkeydown={onHoldKey}
					onkeyup={cancelHold}
					onblur={cancelHold}
				>
					<!-- A layer behind the label (not a bar beside it) shows the hold registered and how much is left — a press-and-hold with no feedback looks broken. -->
					<span class="code-hold-fill" style:width="{holdPct}%" aria-hidden="true"></span>
					<span class="code-hold-label">
						{holdTick > 0 ? 'Keep holding…' : 'Hold to confirm'}
					</span>
				</Button>
			</div>
		</div>
	{/if}

	<p class="code-note">{shapeNote}</p>

	{#if unlocked}
		<p class="code-editing" role="note">
			<TriangleAlertIcon class="size-3.5 shrink-0" />
			<span>Editing by hand. <strong>Apply</strong> replaces the visual document.</span>
		</p>
	{/if}

	<div class="code-scroll">
		<div class="code-stack">
			<div class="code-lines" aria-hidden={unlocked ? 'true' : undefined}>
				{#each segs as line, i (i)}
					{#if !hidden.has(i)}
						{@const fold = rangeAt(ranges, i)}
						<div class="code-line">
							<span class="code-gutter">
								{#if fold}
									<button
										type="button"
										class="code-fold"
										aria-expanded={!collapsed.has(i)}
										aria-label={collapsed.has(i)
											? `Unfold lines ${fold.head + 2} to ${fold.end + 1}`
											: `Fold lines ${fold.head + 2} to ${fold.end + 1}`}
										onclick={() => toggleFold(i)}
									>
										{#if collapsed.has(i)}
											<ChevronRightIcon class="size-3" />
										{:else}
											<ChevronDownIcon class="size-3" />
										{/if}
									</button>
								{/if}
								<span class="code-no">{i + 1}</span>
							</span>
							<code class="code-text"
								>{#each line as seg, j (j)}<span class={seg.cls}>{seg.text}</span
									>{/each}{#if collapsed.has(i)}<span class="code-folded">…</span>{/if}</code
							>
						</div>
					{/if}
				{/each}
			</div>
			{#if unlocked}
				<!-- Transparent textarea over the highlighted lines, utility classes only (a scoped rule can't reach a child component's root). Both layers must wrap at the same column or the caret drifts — three measured overrides hold that: `[overflow-wrap:normal]` (1397 vs 1278px), `w-auto` (605 vs 541px), `md:text-[11px]` (21.7 vs 17.05px/line, tailwind-merge misses the unprefixed class). Inset matches `.code-stack`'s padding + gutter width to `.code-text` exactly. -->
				<Textarea
					bind:value={draft}
					aria-label="HTML source"
					spellcheck="false"
					autocapitalize="off"
					class="absolute top-2 right-3 bottom-3 left-[var(--code-gutter-w)] min-h-0 w-auto resize-none overflow-hidden rounded-none border-0 bg-transparent p-0 font-mono text-[11px] leading-[var(--code-lh)] whitespace-pre-wrap text-transparent shadow-none outline-none [caret-color:var(--foreground)] [field-sizing:fixed] [overflow-wrap:normal] focus-visible:border-0 focus-visible:ring-0 md:text-[11px] dark:bg-transparent"
				/>
			{/if}
		</div>
	</div>

	{#if !unlocked}
		<p class="code-legend">
			<span class="code-swatch text-warning">data-if</span> the editor’s own attributes ·
			<span class="code-swatch text-brand-strong">&lbrace;&lbrace; … &rbrace;&rbrace;</span> merged at
			send
		</p>
	{/if}
</section>

<style>
	/* Floating card styled like the style panel, positioned against `.workspace` (its scroller), 64px from the left (12px inset + 44px rail + 8px air); `.canvas-column` reserves `--code-gutter` while open so unlike the style panel this may move the email (`codeOpen`, email-editor.svelte — don't "fix" that away); `toggleCode` closes the style panel when this opens so the two never overlap; no `overflow` (as `.rail-right`) or it kills `.code-scroll`'s auto-scroll, so the radius clips on the scrolling child instead. */
	.code-pane {
		position: absolute;
		top: 12px;
		left: 64px;
		/* `--code-w` (declared on `.workspace`) is the one number shared with the gutter reserving space for this pane, so they can't drift apart; it narrows with the window so the email stays visible. */
		width: var(--code-w, 440px);
		bottom: 12px;
		z-index: 20;
		display: flex;
		flex-direction: column;
		min-width: 0;
		min-height: 0;
		border-radius: 12px;
		/* One step off `--card` (the email's own paint) via `--email-chrome` on `.workspace`, keeping this pane, the style panel and the blocks pill one family; the fallback covers mounting elsewhere. */
		background: var(--email-chrome, var(--card));
		box-shadow: var(--elev-2);
		outline: 1px solid color-mix(in oklab, var(--foreground) 10%, transparent);
		outline-offset: -1px;
		/* Timed to the canvas's 180ms padding transition so both read as one movement; safe to animate here since nothing measures against this absolutely-positioned pane. Entry only — it unmounts on close. */
		animation: code-pane-in 180ms ease;
	}
	@keyframes code-pane-in {
		from {
			opacity: 0;
			transform: translateX(-8px);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.code-pane {
			animation: none;
		}
	}
	/* The same header treatment as the style panel's, so the two cards read as one product. */
	.code-head {
		display: flex;
		flex: none;
		align-items: center;
		gap: 6px;
		padding: 7px 6px 7px 10px;
		border-bottom: 1px solid var(--border);
	}
	.code-title {
		flex: 1;
		min-width: 0;
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--foreground);
	}
	.code-controls {
		display: flex;
		flex: none;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 8px 10px 6px;
	}
	.code-actions {
		display: flex;
		align-items: center;
		gap: 4px;
	}
	.code-note {
		flex: none;
		padding: 0 10px 8px;
		font-size: 0.7rem;
		line-height: 1.35;
		color: var(--muted-foreground);
	}
	/* In flow, not floating, so it pushes the code down rather than covering the thing about to be replaced. */
	.code-confirm {
		flex: none;
		display: flex;
		flex-direction: column;
		gap: 6px;
		margin: 0 10px 8px;
		padding: 10px;
		font-size: 0.7rem;
		line-height: 1.4;
		color: var(--muted-foreground);
		background: color-mix(in oklab, var(--warning) 8%, var(--email-chrome, var(--card)));
		border: 1px solid color-mix(in oklab, var(--warning) 30%, transparent);
		border-radius: 8px;
	}
	.code-confirm-title {
		font-size: 0.75rem;
		font-weight: 600;
		color: var(--foreground);
	}
	.code-confirm code {
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 0.65rem;
		color: var(--foreground);
	}
	.code-confirm-actions {
		display: flex;
		align-items: center;
		justify-content: flex-end;
		gap: 4px;
		margin-top: 2px;
	}
	/* Child of the button, not `::before` on it — a scoped rule can't reach a child component's own root element. */
	.code-hold-fill {
		position: absolute;
		top: 0;
		bottom: 0;
		left: 0;
		background: color-mix(in oklab, var(--primary-foreground) 26%, transparent);
		pointer-events: none;
	}
	.code-hold-label {
		position: relative;
	}
	.code-editing {
		display: flex;
		flex: none;
		align-items: center;
		gap: 6px;
		margin: 0 10px 8px;
		padding: 6px 8px;
		font-size: 0.7rem;
		line-height: 1.35;
		color: var(--foreground);
		background: color-mix(in oklab, var(--warning) 12%, var(--email-chrome, var(--card)));
		border-radius: 6px;
	}
	/* The pane's only scroller, `overflow: auto` both axes — a verbatim long line (see `format.ts`) is normal, and wide content scrolls in its own container rather than the page. */
	.code-scroll {
		flex: 1;
		min-height: 0;
		overflow: auto;
		border-top: 1px solid var(--border);
		/* Radius on the scroller, not the card (which can't be `overflow: hidden`) — clips content as `.rail-right` does; matters most while editing, when this box reaches the card's bottom edge. */
		border-radius: 0 0 12px 12px;
	}
	/* Both layers wrap to this box: `width: 100%`, never `max-content` (which never wraps `pre-wrap`). Font metrics and `--code-lh` live here once for the highlight layer and the editing overlay together — two copies is how the caret drifts from the character it's editing. */
	.code-stack {
		--code-gutter-w: 3.25rem;
		--code-lh: 1.55;
		position: relative;
		width: 100%;
		padding: 8px 12px 12px 0;
		font-family: var(--font-mono, ui-monospace, monospace);
		font-size: 11px;
		line-height: var(--code-lh);
		box-sizing: border-box;
	}
	.code-lines {
		display: block;
	}
	.code-line {
		display: flex;
		align-items: flex-start;
	}
	/* Pinned to the top of the (possibly wrapped) line, one line-height tall, so the number and fold chevron sit on its first visual row and stay LOGICAL — matching the fold ranges' own numbering. */
	.code-gutter {
		display: flex;
		flex: none;
		align-self: flex-start;
		align-items: center;
		justify-content: flex-end;
		gap: 1px;
		width: var(--code-gutter-w);
		height: calc(1em * var(--code-lh));
		padding-right: 8px;
		box-sizing: border-box;
		user-select: none;
		-webkit-user-select: none;
	}
	.code-no {
		color: color-mix(in oklab, var(--muted-foreground) 55%, transparent);
		font-variant-numeric: tabular-nums;
	}
	.code-fold {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 12px;
		height: 12px;
		color: var(--muted-foreground);
		cursor: pointer;
	}
	.code-fold:hover {
		color: var(--foreground);
	}
	/* Soft wrap only (`pre-wrap`, string untouched, byte-identical on apply) — a HARD wrap is not safe, since a newline inside `<p>Hello <b>x</b></p>` changes what a client renders. No `overflow-wrap`/`word-break`: wraps only between attributes, so an unbreakable token (`data-if="%7B%22…"`) overflows and the pane scrolls to it rather than chopping it mid-token. */
	.code-text {
		white-space: pre-wrap;
		/* `min-width: 0` — a flex item's min-content defaults to its longest unbreakable token (`data-if="%7B%22…"`); without this the line outgrows the overlay: measured 1346px vs 1278px, four rows of drift, caret 52px off. */
		min-width: 0;
		font: inherit;
	}
	.code-folded {
		margin-left: 4px;
		padding: 0 4px;
		color: var(--muted-foreground);
		background: var(--muted);
		border-radius: 4px;
	}
	.code-legend {
		display: flex;
		flex: none;
		flex-wrap: wrap;
		gap: 4px;
		padding: 6px 10px 8px;
		font-size: 0.65rem;
		line-height: 1.3;
		color: var(--muted-foreground);
		border-top: 1px solid var(--border);
	}
	.code-swatch {
		font-family: var(--font-mono, ui-monospace, monospace);
		font-weight: 600;
	}
</style>
