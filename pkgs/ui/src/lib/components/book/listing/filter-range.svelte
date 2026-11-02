<script lang="ts">
	// A slider over the column's span, and the two bounds beneath it. The slider never replaces the
	// boxes: over six orders of magnitude it cannot address most of its own range at pixel
	// resolution. With no track to draw, the boxes stand alone.
	import { Slider } from '../../ui/slider/index.js';
	import MoneyInput from '../../ui/money-input/money-input.svelte';
	import { cn } from '../../../utils.js';
	import { FIELD_CLASS } from '../../../field-shell.js';
	import { capFromTo, niceRange, rangeFromSlider, snapToTrack } from './applied.js';

	let {
		value = [],
		label,
		/** The ends of the column, as the listing knows them. Both needed, or there is no track. */
		min,
		max,
		/** Render and read the bounds as money in this currency. */
		currency,
		onchange
	}: {
		value?: string[];
		label: string;
		min?: string;
		max?: string;
		currency?: string;
		onchange: (next: string[]) => void;
	} = $props();

	const BOUNDS = [0, 1] as const;

	const track = $derived(niceRange(Number(min), Number(max)));

	function asNumber(raw: string | undefined): number | null {
		if (raw === undefined || raw === '') return null;
		const n = Number(raw);
		return Number.isFinite(n) ? n : null;
	}

	function handleAt(raw: string | undefined, fallback: number): number {
		return asNumber(raw) ?? fallback;
	}

	/** Write ONE end without disturbing the other: typing in "From" must not drop a "To" already
	 *  set, and a pair emptied at both ends is no filter at all.
	 *
	 *  From is capped as it is typed — a half-written From that already exceeds To is refused.
	 *  To is not: "100" is typed as "1", then "10", then "100", and capping on that first digit
	 *  would throw away a From of 400. To applies the cap when the field is left. The slider's
	 *  positions are whole amounts, so it caps immediately. */
	function setBound(bound: 0 | 1, raw: string): string[] {
		const pair = [value[0] ?? '', value[1] ?? ''];
		pair[bound] = raw.trim();
		const next = bound === 0 ? capFromTo(pair) : pair[0] || pair[1] ? pair : [];
		onchange(next);
		return next;
	}

	function capOnLeave(): void {
		const next = capFromTo([value[0] ?? '', value[1] ?? '']);
		if ((next[0] ?? '') === (value[0] ?? '') && (next[1] ?? '') === (value[1] ?? '')) return;
		onchange(next);
	}

	// The slider SNAPS an off-step value and writes it back. A typed amount is almost never on a
	// step (the step is a hundredth of the column's span), and a number within half a step of an
	// end is then read as "no bound" — so typing 100 into From cleared the field, while 1,000,
	// sitting on a step, survived. The handles are only ever given a stop, and a write that is
	// just those stops correcting the boxes is ignored. Only a gesture on the slider itself may
	// write, and the gesture ends when the pointer does, wherever it is released: the box sits
	// under the track, so a pointerup on the wrapper alone never arrived.
	let gesture = $state(false);
	let endGesture: (() => void) | undefined;

	function armGesture(pointer: boolean): void {
		endGesture?.();
		gesture = true;
		const end = () => {
			gesture = false;
			window.removeEventListener('pointerup', end, true);
			window.removeEventListener('pointercancel', end, true);
			window.removeEventListener('keyup', end, true);
			if (endGesture === end) endGesture = undefined;
		};
		endGesture = end;
		if (pointer) {
			window.addEventListener('pointerup', end, true);
			window.addEventListener('pointercancel', end, true);
		} else {
			window.addEventListener('keyup', end, true);
		}
	}

	$effect(() => () => endGesture?.());

	function fromSlider(next: number[]): void {
		if (!gesture || !track) return;
		const pair = rangeFromSlider(next, [value[0] ?? '', value[1] ?? ''], track);
		// `[]` is a drag back to the ends and must still clear. `null` is the track echoing the
		// boxes, which must not.
		if (pair === null) return;
		onchange(pair);
	}

	function keyStartsGesture(key: string): boolean {
		return (
			key.startsWith('Arrow') ||
			key === 'Home' ||
			key === 'End' ||
			key === 'PageUp' ||
			key === 'PageDown'
		);
	}
</script>

<div class="flex flex-col gap-1.5">
	{#if track}
		<!-- Capture runs before the thumb handles the event, so the write below still counts as
		     the gesture. The listener that ends it is on the window: the pointer is usually
		     released on the box, outside this element. A snap that arrives with no gesture, or
		     one that only repeats the stops the boxes already sit on, is ignored. -->
		<div
			onpointerdowncapture={() => armGesture(true)}
			onkeydowncapture={(event) => {
				if (keyStartsGesture(event.key)) armGesture(false);
			}}
		>
			<Slider
				{label}
				min={track.min}
				max={track.max}
				step={track.step}
				value={[
					snapToTrack(handleAt(value[0], track.min), track),
					snapToTrack(handleAt(value[1], track.max), track)
				]}
				onValueChange={fromSlider}
			/>
		</div>
	{/if}

	<!-- Never `type="number"`: its scroll wheel silently edits the value under the pointer, which
	     design-language.md bans. `inputmode="decimal"` still gives a phone the keypad.
	     stopPropagation: a menu that is still open type-aheads on the same keys, and the digit
	     never reaches the box. -->
	<div class="grid grid-cols-2 gap-2">
		{#each BOUNDS as bound (bound)}
			<label class="flex flex-col gap-1">
				<span class="text-muted-foreground text-[0.7rem]">{bound === 0 ? 'From' : 'To'}</span>
				{#if currency}
					<!-- From's max is To, so a digit that would pass it is refused as it is typed.
					     To itself waits until the field is left: the first digit of a new amount
					     is not the amount. -->
					<MoneyInput
						value={asNumber(value[bound])}
						{currency}
						max={bound === 0 ? (asNumber(value[1]) ?? undefined) : undefined}
						placeholder="Any"
						class="h-9"
						aria-label="{label} {bound === 0 ? 'from' : 'to'}"
						onkeydown={(event) => event.stopPropagation()}
						onchange={(next) => setBound(bound, next === null ? '' : String(next))}
						onleave={() => bound === 1 && capOnLeave()}
					/>
				{:else}
					<input
						inputmode="decimal"
						value={value[bound] ?? ''}
						placeholder="Any"
						aria-label="{label} {bound === 0 ? 'from' : 'to'}"
						class={cn(FIELD_CLASS, 'placeholder:text-muted-foreground h-9')}
						onkeydown={(event) => event.stopPropagation()}
						oninput={(event) => {
							const next = setBound(bound, event.currentTarget.value);
							const shown = next[bound] ?? '';
							if (event.currentTarget.value !== shown) event.currentTarget.value = shown;
						}}
						onblur={() => bound === 1 && capOnLeave()}
					/>
				{/if}
			</label>
		{/each}
	</div>
</div>
