<script lang="ts">
	// A colour field, replacing Spectrum's third-party popover and the ~40 lines of `!important` that restyled it — docs/design-language.md forbids a native control, a browser popup and a hand-rolled copy alike. The value is an ARBITRARY CSS COLOUR STRING (`colour.ts`): the pad writes a hex only on a real drag, and what we cannot parse reads as unset rather than being replaced.
	// NO COLOUR LITERALS, so the gradients are computed and the pad's `white`/`black` axes are the instrument's own, not a themed surface — which is why this needs no `token-hygiene.test.ts` exemption.
	import * as Popover from '../popover/index.js';
	import { Button } from '../button/index.js';
	import { cn } from '../../../utils.js';
	import { FIELD_WRAPPER_CLASS } from '../../../field-shell.js';
	import {
		hexToHsv,
		hsvToHex,
		isIndirectColour,
		normaliseHex,
		padPosition,
		type Hsv
	} from './colour.js';

	let {
		value = $bindable(''),
		swatches = [],
		id,
		name,
		placeholder = 'none',
		disabled = false,
		clearable = true,
		class: className,
		onchange,
		oninput
	}: {
		value?: string;
		/** One-click colours offered above the pad — the firm's own brand palette, usually. */
		swatches?: readonly string[];
		id?: string;
		name?: string;
		placeholder?: string;
		disabled?: boolean;
		clearable?: boolean;
		class?: string;
		/** The COMMITTED value: one per gesture, at the end of it. */
		onchange?: (value: string) => void;
		/** The value MID-GESTURE — every pointer move of a pad drag, every keystroke in the hex field.
		 *  Falls back to `onchange` when absent, so a caller that wants one channel keeps today's
		 *  behaviour exactly. Split because a consumer with an undo stack needs the two apart: a drag
		 *  through a hundred shades is ONE thing the author did, and recording each of them buries the
		 *  colour they started from a hundred steps back. */
		oninput?: (value: string) => void;
	} = $props();

	let open = $state(false);
	// Held separately from the value, so dragging through a grey (where hue is undefined) does not throw the hue slider back to red mid-drag.
	let hsv = $state<Hsv>(hexToHsv(value) ?? { h: 217, s: 0.76, v: 0.96 });
	// Re-seed only while the picker is shut: reading the value back mid-drag would fight the pointer.
	$effect(() => {
		if (!open) {
			const next = hexToHsv(value);
			if (next) hsv = next;
		}
	});

	const parsed = $derived(normaliseHex(value));

	/** THE SWATCH IS TWO LAYERS and the BROWSER decides which you see: a hatch ("not set, so this inherits") under a pane painted with the author's own string, so an invalid declaration is dropped and the hatch shows through with nothing to detect.
	 *  It replaces a `parsed ? colour : hatch` test on `normaliseHex`, which hatched `black` — the default of every border colour in the rail. */
	const HATCH =
		'repeating-linear-gradient(45deg,var(--muted),var(--muted) 3px,transparent 3px,transparent 6px)';

	const hueStops = $derived(
		[0, 60, 120, 180, 240, 300, 360].map((h) => hsvToHex({ h, s: 1, v: 1 })).join(',')
	);
	const hueAt = $derived(hsvToHex({ h: hsv.h, s: 1, v: 1 }));

	/** `partial` means the gesture is still running. It routes to `oninput`, which falls back to
	 *  `onchange` so a single-channel caller is unaffected. */
	function commit(next: string, partial = false): void {
		value = next;
		if (partial) (oninput ?? onchange)?.(next);
		else onchange?.(next);
	}

	/** A drag writes a hex: the one path that overwrites a token, since the author just chose by eye. */
	function commitHsv(next: Hsv, partial = false): void {
		hsv = next;
		commit(hsvToHex(next), partial);
	}

	function track(
		event: PointerEvent & { currentTarget: HTMLElement },
		read: (x: number, y: number) => Hsv
	): void {
		if (disabled) return;
		const el = event.currentTarget;
		// Capture on the element, so a pointer dragged outside the pad keeps reporting to it — which is how a fully saturated colour is picked.
		el.setPointerCapture?.(event.pointerId);
		// PARTIAL while the pointer is down; `stop` sends the one committed value. The pointerdown's
		// own `apply` is partial too: a click with no movement still ends in `stop`, so it commits.
		const apply = (ev: PointerEvent): void => {
			const { x, y } = padPosition(el.getBoundingClientRect(), ev.clientX, ev.clientY);
			commitHsv(read(x, y), true);
		};
		apply(event);
		const move = (ev: PointerEvent): void => apply(ev);
		const stop = (): void => {
			el.removeEventListener('pointermove', move);
			el.removeEventListener('pointerup', stop);
			el.removeEventListener('pointercancel', stop);
			// THE ONE COMMITTED WRITE of the gesture, and the only thing an undo has to walk back.
			// From `hsv`, not from a fresh pointer read: `pointercancel` carries no useful position,
			// and this is the value the author is looking at either way.
			commitHsv(hsv);
		};
		el.addEventListener('pointermove', move);
		el.addEventListener('pointerup', stop);
		el.addEventListener('pointercancel', stop);
	}

	/** Keyboard nudges, because a pointer-only pad is unreachable without a mouse. */
	function padKey(event: KeyboardEvent): void {
		const step = event.shiftKey ? 0.1 : 0.02;
		const map: Record<string, Partial<Hsv>> = {
			ArrowLeft: { s: hsv.s - step },
			ArrowRight: { s: hsv.s + step },
			ArrowUp: { v: hsv.v + step },
			ArrowDown: { v: hsv.v - step }
		};
		const delta = map[event.key];
		if (!delta) return;
		event.preventDefault();
		commitHsv({ ...hsv, ...delta });
	}

	function hueKey(event: KeyboardEvent): void {
		const step = event.shiftKey ? 30 : 6;
		if (event.key === 'ArrowLeft') {
			event.preventDefault();
			commitHsv({ ...hsv, h: hsv.h - step });
		} else if (event.key === 'ArrowRight') {
			event.preventDefault();
			commitHsv({ ...hsv, h: hsv.h + step });
		}
	}
</script>

<div
	class={cn(
		FIELD_WRAPPER_CLASS,
		'flex items-center gap-2 pr-2.5 pl-1.5',
		disabled && 'bg-input/50 pointer-events-none opacity-50',
		className
	)}
>
	<Popover.Root bind:open>
		<Popover.Trigger
			{disabled}
			aria-label="Choose a colour"
			class="border-border focus-visible:ring-ring/50 relative size-5 shrink-0 overflow-hidden rounded border transition-[box-shadow] focus-visible:ring-2 focus-visible:outline-none"
			style={`background:${HATCH}`}
		>
			<!-- The painted pane: an unpaintable value leaves this transparent and the hatch below shows. -->
			<span class="absolute inset-0" style={`background-color:${value ?? ''}`}></span>
		</Popover.Trigger>
		<Popover.Content align="end" class="w-56 gap-3">
			{#if swatches.length > 0}
				<!-- The firm's own colours, so nobody has to know a hex. A swatch writes the LITERAL colour and not `{{ brand.accent }}`, so it does not follow a later rebrand (`emailBrandColours`). -->
				<div class="flex flex-wrap gap-1.5">
					{#each swatches as swatch (swatch)}
						<button
							type="button"
							title={swatch}
							aria-label={swatch}
							aria-pressed={parsed === normaliseHex(swatch)}
							onclick={() => commit(swatch)}
							class={cn(
								'border-border focus-visible:ring-ring/50 size-5 rounded border focus-visible:ring-2 focus-visible:outline-none',
								// OFFSET, so the ring sits on the popover's surface: against a swatch of a similar colour it would be invisible, and which one is chosen is all this row has to say.
								parsed === normaliseHex(swatch) &&
									'ring-ring ring-offset-popover ring-2 ring-offset-2'
							)}
							style={`background:${swatch}`}
						></button>
					{/each}
				</div>
			{/if}

			<!-- Saturation across, value down. Both axes are the instrument's own — see the header on why `white`/`black` appear here and nowhere else. -->
			<div
				role="slider"
				tabindex={0}
				aria-label="Saturation and brightness"
				aria-valuetext={`saturation ${Math.round(hsv.s * 100)}%, brightness ${Math.round(hsv.v * 100)}%`}
				aria-valuenow={Math.round(hsv.s * 100)}
				class="border-border focus-visible:ring-ring/50 relative h-28 w-full cursor-crosshair rounded-md border focus-visible:ring-2 focus-visible:outline-none"
				style={`background:linear-gradient(to top, black, transparent), linear-gradient(to right, white, ${hueAt})`}
				onpointerdown={(e) => track(e, (x, y) => ({ h: hsv.h, s: x, v: 1 - y }))}
				onkeydown={padKey}
			>
				<span
					class="border-background pointer-events-none absolute size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 shadow-sm ring-1 ring-inset"
					style={`left:${hsv.s * 100}%; top:${(1 - hsv.v) * 100}%; background:${hsvToHex(hsv)}`}
				></span>
			</div>

			<div
				role="slider"
				tabindex={0}
				aria-label="Hue"
				aria-valuemin={0}
				aria-valuemax={360}
				aria-valuenow={Math.round(hsv.h)}
				class="border-border focus-visible:ring-ring/50 relative h-3 w-full cursor-ew-resize rounded-full border focus-visible:ring-2 focus-visible:outline-none"
				style={`background:linear-gradient(to right, ${hueStops})`}
				onpointerdown={(e) => track(e, (x) => ({ h: x * 360, s: hsv.s, v: hsv.v }))}
				onkeydown={hueKey}
			>
				<span
					class="border-background pointer-events-none absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 shadow-sm ring-1 ring-inset"
					style={`left:${(hsv.h / 360) * 100}%; background:${hueAt}`}
				></span>
			</div>

			{#if clearable}
				<!-- Clearing is not picking white, and the old rail had no way to do it at all: an empty value means "not set", so the element inherits. -->
				<Button
					type="button"
					variant="ghost"
					size="sm"
					class="justify-start"
					disabled={!value}
					onclick={() => commit('')}
				>
					Clear
				</Button>
			{/if}
		</Popover.Content>
	</Popover.Root>

	<input
		{id}
		{disabled}
		{placeholder}
		type="text"
		autocomplete="off"
		spellcheck="false"
		aria-label="Colour value"
		value={value ?? ''}
		class="placeholder:text-muted-foreground w-full min-w-0 flex-1 bg-transparent text-sm outline-none"
		oninput={(e) => commit(e.currentTarget.value, true)}
		onblur={(e) => {
			// Normalise a hex on the way out, so two properties set the same colour by different routes read the same. Anything else is left as typed: guessing is how a token gets destroyed.
			// AND COMMIT UNCONDITIONALLY: every keystroke above was partial, so without a committed
			// write here the typed colour would be applied and unrecorded — invisible to undo.
			const tidy = normaliseHex(e.currentTarget.value);
			commit(tidy && tidy !== e.currentTarget.value ? tidy : e.currentTarget.value);
		}}
	/>
	{#if isIndirectColour(value)}
		<!-- Nothing for the swatch to paint, so this says the property IS set rather than leaving the hatch reading as an empty field. `isIndirectColour` and not `!isHex`, or it would badge most of the rail. -->
		<span class="text-muted-foreground shrink-0 text-[0.65rem] tracking-wide uppercase">set</span>
	{/if}
</div>
{#if name}
	<input type="hidden" {name} value={value ?? ''} />
{/if}
