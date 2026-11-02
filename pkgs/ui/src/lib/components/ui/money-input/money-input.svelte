<script lang="ts">
	// A monetary field: the currency's symbol sits inside the control, the figure is grouped as it
	// is typed, and the bound value is a plain number in MAJOR units — pounds, not pence — which is
	// what `Money`/`splitGBP` render, so a value can be typed here and displayed there without a
	// conversion nobody documented.
	//
	// THE ONE MONEY FIELD. `forms/field-inputs/money-input.svelte` (the dynamic-form `money` kind)
	// is a thin adapter over this one, and the contribution drawer's two amounts were plain
	// `<Input inputmode="decimal">` boxes with no grouping and no symbol at all. Three
	// implementations of "type an amount" is three sets of behaviour for one concept.
	//
	// NOT type="number", deliberately. A number input increments on the scroll wheel and on arrow
	// keys, so a mouse passing over a focused fee field silently changes the fee; it also rejects
	// grouped input in some locales. text + inputmode="decimal" gives the numeric keypad on mobile
	// without either behaviour.
	//
	// LEFT-ALIGNED by default. Right alignment is for a COLUMN of figures, where a shared decimal
	// point is what makes them comparable; a form field is read left-to-right with its label, and
	// right-aligning one pushes the digits away from the £ that qualifies them. `align="right"` is
	// there for a money field rendered inside a table cell.
	import { cn } from '../../../utils.js';
	import { FIELD_WRAPPER_CLASS } from '../../../field-shell.js';
	import { currencySymbol } from '../../../currency-symbols.js';
	import { caretFor, liveFormat, parseAmount, settled, significant } from './format.js';

	let {
		value = $bindable(null),
		currency = 'GBP',
		/** Override the symbol when the caller has it from ref.currencies. */
		symbol,
		id,
		name,
		placeholder = '0.00',
		disabled = false,
		required = false,
		invalid = false,
		min,
		max,
		/** Decimal places. Zero-decimal currencies (JPY, KRW) exist and must not show pence. */
		precision = 2,
		/** `right` for a figure in a column; see the note above. */
		align = 'left',
		class: className,
		'aria-label': ariaLabel,
		onkeydown,
		onchange,
		onleave
	}: {
		value?: number | null;
		currency?: string;
		symbol?: string;
		id?: string;
		name?: string;
		placeholder?: string;
		disabled?: boolean;
		required?: boolean;
		invalid?: boolean;
		min?: number;
		max?: number;
		precision?: number;
		align?: 'left' | 'right';
		class?: string;
		'aria-label'?: string;
		onkeydown?: (event: KeyboardEvent) => void;
		onchange?: (value: number | null) => void;
		/** Fired after the field has settled what was typed. */
		onleave?: () => void;
	} = $props();

	// The seed's symbol (`ref.currencies`), so a field and a `Money` cell agree. A caller can
	// still pass `symbol` when it already resolved one. An unseeded code shows the ISO code.
	const prefix = $derived(symbol ?? currencySymbol(currency) ?? currency.toUpperCase());
	/** A field that cannot hold a negative should not let one be typed, let alone displayed. */
	const signed = $derived((min ?? -Infinity) < 0);

	// What the user is typing, kept separate from the numeric value: mirroring `value` straight
	// back into the box would rewrite "1." to "1" mid-keystroke and make the decimal point
	// impossible to type.
	let focused = $state(false);
	// Seeded, not left empty: the sync `$effect` below does not run during SSR, so an uninitialised
	// field would render blank on the server and only fill in after hydration.
	// Called rather than inlined: a prop read directly in a `$state` initialiser captures only its
	// first value, which Svelte warns about. The `$effect` below is what tracks later changes.
	const seed = () => settled(value, precision);
	let text = $state(seed());
	// The last number this field itself reported. A parent that echoes it (the keystroke round
	// trip) must not rewrite the box — "1." would become "1". A different number is someone else
	// (the range slider) and has to show even while the field is focused.
	let echoed = $state<number | null>(null);
	let field: HTMLInputElement | undefined = $state();

	$effect(() => {
		const incoming = value ?? null;
		if (focused && incoming === echoed) return;
		const next = settled(incoming, precision);
		text = next;
		echoed = incoming;
		// A focused box keeps whatever was typed; the framework will not overwrite it. An amount
		// that arrived from outside — the range slider — still has to land in the field.
		if (field && field.value !== next) field.value = next;
	});

	function commit(display: string): void {
		const next = parseAmount(display, { min, max });
		echoed = next;
		value = next;
		onchange?.(next);
	}

	function input(event: Event & { currentTarget: HTMLInputElement }) {
		const el = event.currentTarget;
		// Where the caret is, expressed in a way that survives regrouping.
		const anchor = significant(el.value.slice(0, el.selectionStart ?? el.value.length));
		const next = liveFormat(el.value, { precision, signed });
		commit(next);
		// min/max can refuse the figure just typed. `commit` has already stored the clamped
		// number; writing the live keystrokes back would leave the refused amount on screen.
		const committed = value;
		if (committed !== parseAmount(next)) {
			const shown = settled(committed, precision);
			text = shown;
			el.value = shown;
			el.setSelectionRange(shown.length, shown.length);
			return;
		}
		text = next;
		// Written straight to the DOM rather than left to the next render: the caret has to be
		// restored in the same frame as the value it belongs to, or it jumps to the end of the box
		// on every keystroke that inserts a separator.
		el.value = next;
		const caret = caretFor(next, anchor);
		el.setSelectionRange(caret, caret);
	}
</script>

<!-- The wrapper IS the field: the bordered box a reader sees and a thumb lands on, so a tap on
     the symbol or the padding focuses the input rather than doing nothing. A portal that sizes
     fields for touch (the client's 44px floor) sizes THIS box, by the slot — sizing the inner
     input instead made it taller than the 32px shell around it, and the dev overflow audit drew
     its red ring round every amount field. The inner input is waived from the touch audit for
     the same reason: its box can never be the 44 when the border and padding are the wrapper's. -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div
	data-slot="money-input"
	class={cn(
		FIELD_WRAPPER_CLASS,
		'flex cursor-text items-center gap-1.5',
		invalid && 'border-destructive',
		disabled && 'bg-input/50 pointer-events-none opacity-50',
		className
	)}
	onpointerdown={(event) => {
		if (event.target === field) return;
		event.preventDefault();
		field?.focus();
	}}
>
	<!-- The symbol is decoration, not content: it is announced as part of the field's label by the
	     caller, and reading "pound" before every amount to a screen reader adds nothing. -->
	<span class="text-muted-foreground shrink-0 text-sm select-none" aria-hidden="true">
		{prefix}
	</span>
	<input
		data-tap-ok
		{id}
		{name}
		{disabled}
		{required}
		{placeholder}
		type="text"
		inputmode="decimal"
		autocomplete="off"
		aria-invalid={invalid || undefined}
		aria-label={ariaLabel}
		bind:this={field}
		value={text}
		class={cn(
			'placeholder:text-muted-foreground w-full min-w-0 flex-1 self-stretch bg-transparent text-sm tabular-nums outline-none',
			align === 'right' ? 'text-right' : 'text-left'
		)}
		oninput={input}
		onkeydown={(event) => onkeydown?.(event)}
		onfocus={() => (focused = true)}
		onblur={() => {
			focused = false;
			// Normalise on the way out: "1.5" becomes "1.50", and an unparseable entry clears rather
			// than sitting there looking like a value.
			text = settled(value, precision);
			onleave?.();
		}}
	/>
</div>
