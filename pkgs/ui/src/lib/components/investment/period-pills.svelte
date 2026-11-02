<script lang="ts">
	// The period switcher shared by the portfolio header looks. One component so the touch-target
	// fix lives once: the visible pill is 32px tall (text-xs — the design floor — inside h-8) and
	// an invisible ::before extends the hit area to ≥44px each way, so a thumb lands the tap the
	// old 21px pills fumbled, without inflating the visual weight of the control.
	import { tick } from 'svelte';
	import { cn } from '../../utils.js';
	import { periodKeys, type PeriodKey } from './periods.js';

	let {
		selected,
		onselect,
		interactive = true,
		tone = 'hero',
		collapsible = false
	}: {
		selected: PeriodKey;
		onselect: (key: PeriodKey) => void;
		/** False renders the control inert (previews): disabled buttons, current state shown. */
		interactive?: boolean;
		/** 'hero' rides the hero-toggle tokens (ink surfaces); 'page' is the ledger's quiet,
		 * typographic take on the same control — page-ground tokens, an underline for state. */
		tone?: 'hero' | 'page';
		/** On a narrow card, show only the selected period until tapped, then glide open to the
		 * full set — the same easing as the shell's bottom bar. Wide cards stay open. */
		collapsible?: boolean;
	} = $props();

	// Tailwind's `@md` container breakpoint (28rem). Below it the control collapses.
	const COLLAPSE_BELOW = 448;
	// Same curve the bottom-nav pill glides on.
	const EASE = 'cubic-bezier(0.32, 0.72, 0, 1)';

	let shell: HTMLDivElement | undefined = $state();
	let row: HTMLDivElement | undefined = $state();
	let buttons: HTMLButtonElement[] = $state([]);
	let narrow = $state(false);
	let open = $state(false);
	/** Clip width of the pill row; null until measured, so the first paint shows the full set. */
	let clip = $state<number | null>(null);
	let shift = $state(0);

	const collapsed = $derived(collapsible && narrow && !open);

	function measure() {
		if (!row) return;
		const full = row.scrollWidth;
		const current = buttons[periodKeys.indexOf(selected)];
		if (collapsible && narrow && !open && current) {
			clip = current.offsetWidth;
			shift = -current.offsetLeft;
		} else {
			clip = full;
			shift = 0;
		}
	}

	function choose(key: PeriodKey) {
		if (!interactive) return;
		if (!collapsible || !narrow) {
			onselect(key);
			return;
		}
		// Collapsed, the visible period is a handle: the tap opens the set rather than
		// re-selecting it. Choosing (or re-tapping the current one) closes it again.
		if (!open) {
			open = true;
			return;
		}
		onselect(key);
		open = false;
	}

	$effect(() => {
		if (!collapsible || !shell) return;
		const box = shell.closest('[class*="@container"]') ?? shell.parentElement ?? shell;
		const observer = new ResizeObserver(() => {
			const nowNarrow = box.clientWidth < COLLAPSE_BELOW;
			if (!nowNarrow) open = false;
			narrow = nowNarrow;
		});
		observer.observe(box);
		return () => observer.disconnect();
	});

	$effect(() => {
		// Read as dependencies: the indicator re-measures whenever the selection, the expanded
		// state, the narrow/wide mode or the rendered pills change.
		void [selected, open, narrow, buttons.length];
		void tick().then(measure);
	});

	$effect(() => {
		if (!open) return;
		const close = (event: PointerEvent) => {
			if (shell && !shell.contains(event.target as Node)) open = false;
		};
		const onKey = (event: KeyboardEvent) => {
			if (event.key === 'Escape') open = false;
		};
		document.addEventListener('pointerdown', close);
		document.addEventListener('keydown', onKey);
		return () => {
			document.removeEventListener('pointerdown', close);
			document.removeEventListener('keydown', onKey);
		};
	});
</script>

{#if tone === 'page'}
	<div class="flex items-center gap-1" role="group" aria-label="Performance period">
		{#each periodKeys as key (key)}
			<button
				type="button"
				disabled={!interactive}
				aria-pressed={selected === key}
				onclick={() => onselect(key)}
				class={cn(
					'relative h-8 rounded-sm px-2.5 text-xs font-medium tracking-wide tabular-nums transition-colors',
					'before:absolute before:-inset-x-1 before:-inset-y-1.5 before:content-[""]',
					selected === key
						? 'text-foreground underline decoration-from-font underline-offset-[6px]'
						: 'text-muted-foreground hover:text-foreground'
				)}
			>
				{key}
			</button>
		{/each}
	</div>
{:else}
	<!-- `p-1` is 4px a side, added to the measured row so the clip lands on a pill boundary. -->
	<div
		bind:this={shell}
		class="inline-flex max-w-full overflow-hidden rounded-full p-1 ring-1 motion-reduce:transition-none"
		style="background-color: color-mix(in oklch, var(--hero-foreground) 8%, transparent); --tw-ring-color: color-mix(in oklch, var(--hero-foreground) 10%, transparent);{clip ===
		null
			? ''
			: ` width: ${clip + 8}px; transition: width 300ms ${EASE};`}"
		role="group"
		aria-label="Performance period"
		data-expanded={collapsible && narrow ? open : undefined}
	>
		<div
			bind:this={row}
			class="flex gap-1 motion-reduce:transition-none"
			style="transform: translateX({shift}px); transition: transform 300ms {EASE};"
		>
			{#each periodKeys as key, i (key)}
				<button
					bind:this={buttons[i]}
					type="button"
					disabled={!interactive}
					aria-pressed={selected === key}
					aria-hidden={collapsed && selected !== key ? true : undefined}
					tabindex={collapsed && selected !== key ? -1 : undefined}
					onclick={() => choose(key)}
					class={cn(
						'relative h-8 shrink-0 rounded-full px-3 text-xs font-medium tabular-nums transition-colors',
						'before:absolute before:-inset-x-1 before:-inset-y-1.5 before:content-[""]',
						selected === key ? 'hero-toggle-on' : 'hero-toggle-off'
					)}
				>
					{key}
				</button>
			{/each}
		</div>
	</div>
{/if}
