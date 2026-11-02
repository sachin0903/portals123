<script lang="ts">
	// The gap control: the "…" between two page numbers, opening a small "go to page" field.
	//
	// WHY THE ELLIPSIS IS A CONTROL AT ALL. It looks like one, so people click it — and until now
	// nothing happened, because it was a decorative `<span aria-hidden>`. That is the worst of the
	// two options: a target that invites a click and answers with silence. The alternative fix was
	// prev/next arrows, and those were REMOVED on purpose (see pagination.ssr.test.ts) — every page
	// is directly reachable, so arrows only duplicated a neighbouring number. What they left behind
	// is the case this control exists for: with 24 pages, page 12 is behind a gap and would
	// otherwise take ten clicks to walk to.
	//
	// WHY IT IS NOT A "SKIP 5 PAGES" JUMP. That is the usual shortcut and it keeps the original
	// problem: an unlabelled control whose destination the reader has to learn by trying it. This
	// one NAMES the range it hides ("Go to a page between 3 and 22") and then asks where to go, so
	// the outcome is stated before the click and chosen by the reader after it.
	//
	// A NOTE ON WHERE IT MAY BE MOUNTED. `Popover.Content` portals to the body, and the standing
	// pkgs/ui gotcha is that a portalled panel lands outside a Sheet's focus trap and never
	// receives a click (which is why `RecordPicker` is an inline listbox instead). Every pager in
	// the product is card furniture at the foot of a page-level listing, so this is safe today — but
	// if a paginated table is ever put INSIDE a drawer, this control is the thing that will break,
	// and the fix is the inline arrangement, not a bigger z-index.
	import * as Popover from '../popover/index.js';
	import { Input } from '../input/index.js';
	import { Label } from '../label/index.js';
	import { Button } from '../button/index.js';
	import { cn } from '../../../utils.js';
	import { gapLabel, parsePageInput, type Gap } from './page-jump.js';

	let {
		gap,
		pageCount,
		href,
		onselect,
		class: className
	}: {
		/** The run of pages this control stands in for. */
		gap: Gap;
		/** Used to clamp what is typed, and to say what the upper bound is. */
		pageCount: number;
		/** Link mode: the submit is a real anchor to `href(n)`, matching the numbered pages. */
		href?: (page: number) => string;
		/** Button mode: called with the chosen page. Ignored when `href` is given. */
		onselect?: (page: number) => void;
		class?: string;
	} = $props();

	let open = $state(false);
	let raw = $state('');
	let field = $state<HTMLInputElement | null>(null);
	/** The anchor in link mode, so Enter can trigger the same navigation the click does. */
	let submit = $state<HTMLAnchorElement | null>(null);

	const target = $derived(parsePageInput(raw, pageCount));
	const label = $derived(gapLabel(gap));
	const fieldId = $props.id();

	function onOpenChange(next: boolean): void {
		open = next;
		// Cleared on every open, never seeded. A pre-filled number has to be deleted before it can be
		// replaced, and it reads as a value someone chose — so the reader pauses to work out whether
		// it means anything. The bounds belong in the placeholder, which costs no keystrokes.
		if (next) raw = '';
	}

	function commit(): void {
		if (target === null) return;
		if (href) {
			// Clicking our own anchor rather than assigning `location`: this package is
			// framework-agnostic (no `$app/navigation` here), and a real click is what lets
			// SvelteKit's client router intercept it. Assigning `location` would work and would
			// full-page-reload every jump.
			submit?.click();
		} else {
			onselect?.(target);
		}
		open = false;
	}

	function onkeydown(event: KeyboardEvent): void {
		if (event.key !== 'Enter') return;
		// The field is not in a <form> — a pager sits inside other people's forms often enough that
		// nesting one would be invalid markup — so Enter is wired by hand.
		event.preventDefault();
		commit();
	}

	// Matches the numbered controls exactly, so the gap keeps its place in the rhythm of the row.
	const control =
		'inline-flex size-8 shrink-0 items-center justify-center rounded-md text-sm leading-none transition-colors pointer-coarse:size-10 text-muted-foreground hover:bg-accent hover:text-accent-foreground aria-expanded:bg-accent aria-expanded:text-accent-foreground';
</script>

<Popover.Root bind:open {onOpenChange}>
	<Popover.Trigger>
		{#snippet child({ props })}
			<!-- No `aria-hidden` any more, and a name that states the destination. A control that can
			     be reached by pointer but not by a screen reader is the same bug in a different form. -->
			<button
				{...props}
				type="button"
				class={cn(control, className)}
				aria-label={label}
				title={label}>…</button
			>
		{/snippet}
	</Popover.Trigger>
	<!-- Sized to its contents, not to the popover's default 72. The panel holds a label, a short
	     number field and one button; a fixed width just adds empty space beside them. -->
	<Popover.Content class="w-auto gap-1.5 p-2" align="center">
		<Label for={fieldId} class="text-xs font-medium">Go to page</Label>
		<div class="flex items-center gap-1.5">
			<!-- The placeholder carries the bounds. It replaces a line of prose that said the same
			     thing, and it sits where the answer goes rather than underneath it. -->
			<Input
				bind:ref={field}
				bind:value={raw}
				id={fieldId}
				type="text"
				inputmode="numeric"
				autocomplete="off"
				placeholder={`1–${pageCount}`}
				class="h-8 w-24 tabular-nums"
				{onkeydown}
			/>
			{#if href}
				<!-- An anchor, not a button, for the same reason the numbered pages are anchors: the
				     listing keeps its query in the URL, so a page is a real link — shareable, openable
				     in a new tab, and walkable with the back button. `target` can be null while the
				     field is empty or nonsense, and an anchor cannot be "disabled": it is rendered as
				     an inert span in that state instead, matching how the pager already handles a
				     dead end. -->
				{#if target === null}
					<span
						role="link"
						aria-disabled="true"
						class="text-muted-foreground pointer-events-none inline-flex h-8 items-center rounded-lg px-2.5 text-sm font-medium opacity-40"
						>Go</span
					>
				{:else}
					<a
						bind:this={submit}
						href={href(target)}
						class="bg-primary text-primary-foreground hover:bg-primary/80 inline-flex h-8 items-center rounded-lg px-2.5 text-sm font-medium"
						onclick={() => (open = false)}>Go</a
					>
				{/if}
			{:else}
				<Button size="default" disabled={target === null} onclick={commit}>Go</Button>
			{/if}
		</div>
	</Popover.Content>
</Popover.Root>
