<script lang="ts">
	/**
	 * Text that clips to its box and reveals itself in full on hover or keyboard focus.
	 *
	 * This replaces the `title` attribute the book surfaces had been reaching for. Two reasons it is
	 * a component and not an attribute: `docs/design-language.md` rules out browser-rendered popups
	 * for everything else in the product, and a native `title` is unreachable by keyboard — so the
	 * one affordance for reading a clipped client name excluded anyone not using a mouse.
	 *
	 * THE TOOLTIP IS GATED ON ACTUAL OVERFLOW. Measured on pointer-enter / focus, when layout has
	 * settled, and the tooltip stays disabled unless the box is really clipping. A tooltip that
	 * repeats text you can already read is noise, and these listings would otherwise carry one on
	 * every cell of every row.
	 *
	 * `full` exists for content that is SUMMARISED rather than merely long: an adviser column showing
	 * "Zoe Lead +2" wants the three names on hover, not the summary again. Where it is omitted the
	 * displayed text is what the tooltip shows.
	 *
	 * Renders an `<a>` when given an `href`, because half these values are links (a client name in
	 * the accounts book) and wrapping an anchor in a span would lose the row's middle-click.
	 */
	import * as Tooltip from '../tooltip/index.js';
	import { cn } from '../../../utils.js';
	import { LINK_RECORD } from '../../../text-shell.js';
	import { overflows } from './overflow.js';

	let {
		text,
		full,
		href,
		delayMs = 300,
		class: className
	}: {
		/** What is displayed, and — unless `full` says otherwise — what the tooltip shows. */
		text: string;
		/** The complete value, when `text` is a summary of it. */
		full?: string;
		/** Given, this renders an anchor rather than a span — wearing `LINK_RECORD`, so every name
		 *  that leads to its own record invites the click the same way. Every caller of this is such a
		 *  name (a client, an account, a template, a journey), which is why the look lives here rather
		 *  than being passed in nine times and drifting in five of them. */
		href?: string;
		/** Hover dwell before the tooltip appears. The shell's provider sets 0, which is right for a
		 *  sidebar icon and too eager for a table you are scanning across. */
		delayMs?: number;
		class?: string;
	} = $props();

	let el = $state<HTMLElement | null>(null);
	// Disabled until measurement says otherwise, so the first hover cannot flash a tooltip on text
	// that fits. SSR renders the text and nothing else.
	let clipped = $state(false);

	function measure(): void {
		if (el) clipped = overflows(el.scrollWidth, el.clientWidth);
	}

	const shown = $derived(full ?? text);
	/**
	 * The tooltip is offered when the box is CLIPPING, or when `full` genuinely says more than the cell
	 * displays. The second case is the one that needed adding: a SUMMARISED label ("Craig Currie +1")
	 * usually fits its column perfectly, so gating on overflow alone meant the summary was the only
	 * thing a reader could ever see and the names behind it were unreachable. A summary that cannot be
	 * expanded is just missing data.
	 */
	const summarised = $derived(full != null && full !== text);
</script>

<!-- Its OWN provider, deliberately. `Tooltip.Root` throws `Context "Tooltip.Provider" not found`
     without an ancestor one, and the only shell that mounts one is the staff sidebar — so a
     provider-dependent version of this would render fine in the book and hard-crash the client
     portal, a dialog, or anything mounted outside that shell. The cost is nothing: `Provider`
     emits no DOM, it only sets context, so one per cell is a context object rather than an
     element. Nesting inside the shell's provider is fine — the inner one wins, which is what
     gives us a per-instance dwell instead of the sidebar's `delayDuration={0}`. -->
<Tooltip.Provider delayDuration={delayMs}>
	<Tooltip.Root disabled={!clipped && !summarised}>
		<Tooltip.Trigger>
			{#snippet child({ props })}
				{#if href}
					<a
						{...props}
						bind:this={el}
						{href}
						class={cn('block truncate', LINK_RECORD, className)}
						onpointerenter={measure}
						onfocusin={measure}>{text}</a
					>
				{:else}
					<span
						{...props}
						bind:this={el}
						class={cn('block truncate', className)}
						onpointerenter={measure}
						onfocusin={measure}>{text}</span
					>
				{/if}
			{/snippet}
		</Tooltip.Trigger>
		<!-- `whitespace-normal` because the tooltip is where the value is read WHOLE: the content's own
		     `max-w-xs` would otherwise hold one unbroken line, and a long adviser list or fund name is
		     exactly what got us here. `break-words` for the tokens that have no space to break at. -->
		<Tooltip.Content class="max-w-xs break-words whitespace-normal">{shown}</Tooltip.Content>
	</Tooltip.Root>
</Tooltip.Provider>
