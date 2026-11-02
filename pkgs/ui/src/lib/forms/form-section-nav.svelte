<script lang="ts">
	// A sticky left-rail nav for a long form: lists the sections, highlights the one in view
	// (IntersectionObserver scroll-spy) and jumps to one on click. Keyed off the section anchor ids
	// DynamicForm stamps on each section (`sectionAnchorId`). Client-only behaviour — on the server it
	// renders with the first section active. Set `--form-scroll-offset` on an ancestor to clear a
	// sticky app header (both the stick point here and the section scroll-margin honour it).
	//
	// Below @4xl the rail would cost the form its width, so the same list becomes one sticky dropdown
	// above the sections, with a visible "Jump to section" label: its trigger shows only the current
	// section's name, which does not say what the control is for. It is not portalled: this nav
	// lives inside dialogs, and a portalled panel sits outside the dialog's focus trap.

	import ListIcon from '@lucide/svelte/icons/list';
	import { SelectMenu } from '../components/ui/select-menu/index.js';

	let {
		sections
	}: {
		/** `errors` is how many of that section's fields are failing right now — 0 until the form has
		    been submitted once, since nothing is validated before then. */
		sections: { id: string; label: string; errors?: number }[];
	} = $props();

	// The scroll-spy sets this; empty until the observer fires. `active` falls back to the first
	// section so one item is highlighted from the start, and stays reactive if the sections change.
	let activeId = $state('');
	const active = $derived(activeId || sections[0]?.id || '');

	function goTo(id: string) {
		activeId = id;
		document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
	}

	const jumpItems = $derived(
		sections.map((s) => ({
			value: s.id,
			label: s.errors ? `${s.label} · ${s.errors}` : s.label
		}))
	);

	$effect(() => {
		const els = sections
			.map((s) => document.getElementById(s.id))
			.filter((el): el is HTMLElement => el !== null);
		if (els.length === 0) return;
		const observer = new IntersectionObserver(
			(entries) => {
				const onscreen = entries.filter((e) => e.isIntersecting);
				if (onscreen.length === 0) return;
				const topmost = onscreen.reduce((a, b) =>
					a.boundingClientRect.top <= b.boundingClientRect.top ? a : b
				);
				activeId = topmost.target.id;
			},
			{ rootMargin: '-15% 0px -75% 0px' }
		);
		els.forEach((el) => observer.observe(el));
		return () => observer.disconnect();
	});

	// Whether the bar is stuck over the form. A stuck bar casts a shadow, so it reads as floating
	// above the card scrolling under it instead of blending into it. Stuck means the bar's top edge
	// has reached its stick line: observed against its own scroller (a dialog's, or the window),
	// with the root's top pulled down to one pixel below that line.
	let bar = $state<HTMLElement | null>(null);
	let stuck = $state(false);

	$effect(() => {
		if (!bar) return;
		let root = bar.parentElement;
		while (root && !/(auto|scroll)/.test(getComputedStyle(root).overflowY)) {
			root = root.parentElement;
		}
		const line = parseFloat(getComputedStyle(bar).top) || 0;
		const observer = new IntersectionObserver(
			([entry]) => {
				if (!entry) return;
				const edge = entry.rootBounds?.top ?? line + 1;
				stuck = entry.intersectionRatio < 1 && entry.boundingClientRect.top <= edge;
			},
			{ root, rootMargin: `-${line + 1}px 0px 0px 0px`, threshold: [1] }
		);
		observer.observe(bar);
		return () => observer.disconnect();
	});
</script>

<!-- As wide as the sections below it, padded like them, so on a page it reads as one more card. A
     host whose scroller has side padding (the form dialog, the illustration drawer) sets
     `--form-nav-bleed` to that padding, and the bar then reaches the scroller's edges as a band.
     It sticks under the shell's topbar (`--topbar-h`). A dialog is portalled outside the shell, so
     the variable is unset there and the bar sticks to the top of the dialog's own scroller. -->
<div
	bind:this={bar}
	data-stuck={stuck || undefined}
	class="bg-popover sticky top-[var(--topbar-h,0px)] z-10 -mx-[var(--form-nav-bleed,0px)] mb-2 flex items-center gap-3 rounded-xl px-[var(--form-nav-bleed,1rem)] py-2 motion-safe:transition-shadow @4xl:hidden data-stuck:shadow-md"
>
	<!-- Hidden from screen readers: the trigger's own aria-label already says this. -->
	<span
		aria-hidden="true"
		class="text-muted-foreground flex shrink-0 items-center gap-1.5 text-xs font-medium"
	>
		<ListIcon class="size-3.5" />
		Jump to section
	</span>
	<SelectMenu
		label="Jump to section"
		items={jumpItems}
		value={active}
		portal={false}
		class="min-w-0 flex-1"
		onValueChange={goTo}
	/>
</div>
<nav aria-label="Form sections" class="hidden @4xl:block">
	<!-- Capped to the visible panel. A fact find has more sections than a short window can show, and a
	     sticky list that is taller than the dialog can never be scrolled to its last entries. The
	     dialog sets --form-rail-max; a full page falls back to the viewport. -->
	<ol
		class="sticky top-[var(--form-scroll-offset,1rem)] max-h-[var(--form-rail-max,calc(100svh-var(--form-scroll-offset,1rem)-2rem))] space-y-0.5 overflow-y-auto text-sm"
	>
		{#each sections as s (s.id)}
			<li>
				<button
					type="button"
					onclick={() => goTo(s.id)}
					aria-current={active === s.id ? 'true' : undefined}
					class={[
						'flex w-full items-center gap-2 rounded-md px-2.5 py-1.5 text-left transition-colors',
						active === s.id
							? 'bg-muted text-foreground font-medium'
							: 'text-muted-foreground hover:bg-muted/60 hover:text-foreground'
					]}
				>
					<!-- The dot carries the failure, because it is the one element already on every row:
					     a section with something missing turns destructive rather than growing a second
					     marker, so the rail reads as one column of states instead of a column of dots
					     with badges appearing between them. -->
					<span
						class={[
							'size-1.5 shrink-0 rounded-full',
							s.errors
								? 'bg-destructive'
								: active === s.id
									? 'bg-primary'
									: 'bg-muted-foreground/40'
						]}
					></span>
					<span class="min-w-0 flex-1 text-pretty">{s.label}</span>
					{#if s.errors}
						<!-- The count, not just a mark: "3" is the difference between one field left and a
						     section you have not started. `sr-only` text because a bare numeral beside a
						     heading is ambiguous to a screen reader. -->
						<span class="text-destructive shrink-0 text-xs tabular-nums">
							{s.errors}<span class="sr-only"> fields need attention</span>
						</span>
					{/if}
				</button>
			</li>
		{/each}
	</ol>
</nav>
