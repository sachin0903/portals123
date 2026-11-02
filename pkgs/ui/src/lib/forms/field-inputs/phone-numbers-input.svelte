<script lang="ts">
	import { untrack } from 'svelte';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import { Button } from '../../components/ui/button/index.js';
	import { SelectMenu } from '../../components/ui/select-menu/index.js';
	import PhoneNumberInput from './phone-number-input.svelte';
	import { PHONE_NUMBER_LABELS, type PhoneNumber } from '../types.js';

	let {
		value,
		onchange,
		invalid = false,
		/** How many numbers this may hold. ONE by default: a client has a primary number and a
		 *  secondary number, and the open-ended list was more flexibility than the book ever used —
		 *  the one integration that wants a second number has always read element [0] and ignored
		 *  the rest. A caller that genuinely needs more raises it; nothing does today. */
		max = 1
	}: {
		value?: unknown;
		onchange: (value: PhoneNumber[]) => void;
		invalid?: boolean;
		max?: number;
	} = $props();

	/** The stored answer is opaque jsonb, so every element is coerced back to the two keys rather
	    than trusted: a row saved in an older shape (a bare string, a missing label) has to arrive as
	    an editable row somebody can correct, not vanish. */
	function normalise(raw: unknown): PhoneNumber[] {
		if (!Array.isArray(raw)) return [];
		return raw.map((entry) => {
			const item = entry && typeof entry === 'object' ? (entry as Record<string, unknown>) : {};
			return { label: String(item.label ?? 'home'), number: String(item.number ?? '') };
		});
	}

	const isBlank = (row: PhoneNumber): boolean => row.number.trim() === '';
	const same = (a: PhoneNumber[], b: PhoneNumber[]): boolean =>
		a.length === b.length &&
		a.every((row, i) => row.label === b[i]?.label && row.number === b[i]?.number);

	// UNLIKE `beneficiaries-input`, the rows on screen are NOT `$derived(value)`. A blank row must
	// never reach the answer — an "Add number" somebody thought better of would otherwise persist as
	// `{ label: 'home', number: '' }`, so the record would read "no other numbers" while the array is
	// non-empty and everything counting `.length` would disagree with the screen. Filtering on the way
	// UP and deriving on the way DOWN would delete the row the caret is sitting in, so this component
	// owns the blank rows and the answer only ever carries the filled ones. Seeded at construction
	// (effects do not run on the server, and an SSR render must state the numbers it was given).
	/** A SINGLE-VALUE field shows its empty row rather than an "Add number" button. With `max` of 1
	 *  there is nothing to add a second of, so a button that reveals the only row it will ever
	 *  reveal is a click between a client and the answer — which onboarding, where most of these are
	 *  first typed, could least afford. Blank rows still never reach the answer (`commit` filters
	 *  them), so an untouched row is the same as no number. */
	const blankRow = (): PhoneNumber => ({ label: 'home', number: '' });
	const withEmptyRow = (rows: PhoneNumber[]): PhoneNumber[] =>
		rows.length === 0 && max === 1 ? [blankRow()] : rows;

	let draft = $state<PhoneNumber[]>(untrack(() => withEmptyRow(normalise(value))));

	// Re-seed when the ANSWER disagrees with the filled rows on screen — a reset, a prefill, a form
	// loading its saved values. Compared against the filtered rows, so the component's own blank rows
	// never look like a disagreement and typing cannot clobber itself. `untrack` because the check
	// both reads and writes `draft`.
	$effect(() => {
		const incoming = normalise(value);
		untrack(() => {
			if (
				!same(
					incoming,
					draft.filter((row) => !isBlank(row))
				)
			)
				draft = withEmptyRow(incoming);
		});
	});

	function commit(next: PhoneNumber[]): void {
		draft = next;
		onchange(next.filter((row) => !isBlank(row)));
	}

	const full = $derived(draft.length >= max);

	function add(): void {
		if (full) return;
		commit([...draft, blankRow()]);
	}

	function patch(index: number, change: Partial<PhoneNumber>): void {
		commit(draft.map((row, i) => (i === index ? { ...row, ...change } : row)));
	}

	function remove(index: number): void {
		commit(draft.filter((_, i) => i !== index));
	}
</script>

<div class="space-y-2" aria-invalid={invalid || undefined}>
	<!-- NO card around each row, and NO column captions. A bordered box with its own padding pushed
	     every caption 12px right of Title / Email / Primary number, so one card had two left edges;
	     the rows are a plain grid on the SAME left edge as the fields above. The "Label"/"Number"
	     captions went with the list: they were a header for a table of many rows, and above ONE row
	     they name what the picker and the placeholder already say — and they sat under the field's
	     own "Secondary number" label, so the card read two headings deep before a value. -->
	{#each draft as row, index (index)}
		<div class="grid items-start gap-2 sm:grid-cols-[6.5rem_1fr_2rem]">
			<SelectMenu
				items={PHONE_NUMBER_LABELS}
				value={row.label}
				onValueChange={(label) => patch(index, { label })}
			/>
			<!-- The SAME masked input the single phone field uses. A second hand-wired one is a
			     second place for the rule to drift, and the rule is not obvious: a typed letter has
			     to be refused at the keystroke, because re-deriving the display cannot clear a
			     character that did not change the canonical value. -->
			<PhoneNumberInput
				icon={false}
				value={row.number}
				placeholder="+44 20 7946 0958"
				onchange={(number) => patch(index, { number })}
			/>
			<Button
				type="button"
				variant="ghost"
				size="icon"
				class="text-muted-foreground hover:text-destructive size-9"
				aria-label={max === 1 ? 'Remove number' : `Remove number ${index + 1}`}
				onclick={() => remove(index)}
			>
				<Trash2Icon class="size-4" aria-hidden="true" />
			</Button>
		</div>
	{/each}

	<!-- The way to add is GONE at the cap, not disabled: a permanently greyed button on a record
	     that will never allow another number is a control that only ever says no. At `max` of one
	     the row above is the whole field, and the remove button is how you change your mind. -->
	<!-- Only where there is genuinely a second one to add. At `max` of 1 the row above IS the field,
	     and the bin beside it is how you change your mind. -->
	{#if !full && max > 1}
		<Button type="button" variant="outline" size="sm" class="mt-1" onclick={add}>
			<PlusIcon class="mr-1.5 size-4" aria-hidden="true" />
			Add number
		</Button>
	{/if}
</div>
