<script module lang="ts">
	/** The select's value for "type a family name". Not a font, so it can never collide. */
	export const OTHER_FONT = '__other';

	/** Which select item a stored font maps to: the font itself when curated, `OTHER_FONT`
	 *  when the firm typed one in, '' when unset. Pure so the test can pin it. */
	export function fontSelection(
		value: string | null | undefined,
		options: readonly string[]
	): string {
		if (!value) return '';
		return options.includes(value) ? value : OTHER_FONT;
	}
</script>

<script lang="ts">
	import { untrack } from 'svelte';
	import { isFontFamily } from '@graphene/auth';
	import { Input } from '../ui/input/index.js';
	import SelectMenu from '../ui/select-menu/select-menu.svelte';

	/**
	 * A font chooser for the branding studio: the curated list, plus "Other…" for a Google Fonts
	 * family the list does not carry. The typed name is validated against the same rule the
	 * portal's head injection applies, so a name the save would refuse is refused here, with the
	 * reason, instead of landing in the record and silently falling back.
	 */
	let {
		id,
		value,
		options,
		defaultLabel,
		disabled = false,
		onchange
	}: {
		id: string;
		/** The stored family, or null for the design-system default. */
		value: string | null | undefined;
		/** The curated list the select offers. */
		options: readonly string[];
		/** What the empty choice is called — "System default (Inter)", "Same as body". */
		defaultLabel: string;
		disabled?: boolean;
		onchange: (next: string | null) => void;
	} = $props();

	const items = $derived([
		{ value: '', label: defaultLabel },
		...options.map((font) => ({ value: font, label: font })),
		{ value: OTHER_FONT, label: 'Other…' }
	]);

	// "Other…" chosen with nothing typed yet. The stored value is still whatever it was, so the
	// select would snap back to it on the next render without this.
	let typing = $state(false);
	const selection = $derived(typing ? OTHER_FONT : fontSelection(value, options));
	const custom = $derived(selection === OTHER_FONT);

	// What is in the box. Seeded from a stored custom name; a curated name is never shown here, so
	// switching to Other… starts blank.
	let draft = $state(untrack(() => (fontSelection(value, options) === OTHER_FONT ? value! : '')));
	// Follow the record when it changes under us — the editor swaps `value` when a different
	// design set is selected — without touching what the firm is mid-way through typing.
	$effect(() => {
		const stored = value;
		untrack(() => {
			if (stored && !options.includes(stored) && stored !== draft.trim()) draft = stored;
			else if (!stored && !typing) draft = '';
		});
	});
	const invalid = $derived(draft.trim() !== '' && !isFontFamily(draft.trim()));

	function pick(next: string) {
		if (next === OTHER_FONT) {
			typing = true;
			// A curated font was in force: keep it until a valid name replaces it, so the portal
			// does not drop to the default while the firm is still typing.
			if (value && options.includes(value)) draft = '';
			return;
		}
		typing = false;
		draft = '';
		onchange(next || null);
	}

	function type(raw: string) {
		draft = raw;
		const trimmed = raw.trim();
		if (trimmed === '') return;
		if (isFontFamily(trimmed)) onchange(trimmed);
	}

	function settle() {
		// Leaving the box empty means the firm changed their mind: back to the default.
		if (draft.trim() === '') {
			typing = false;
			onchange(null);
		}
	}
</script>

<div class="space-y-1.5">
	<SelectMenu {id} value={selection} {disabled} {items} onValueChange={pick} />
	{#if custom}
		<Input
			id={`${id}-custom`}
			value={draft}
			{disabled}
			placeholder="Google Fonts family, e.g. Figtree"
			maxlength={48}
			aria-label="Font family name"
			aria-invalid={invalid || undefined}
			oninput={(event) => type(event.currentTarget.value)}
			onblur={settle}
		/>
		{#if invalid}
			<p class="text-destructive text-[0.7rem] leading-snug">
				Letters, digits, spaces and hyphens only, up to 48 characters.
			</p>
		{:else}
			<p class="text-muted-foreground text-[0.7rem] leading-snug">
				Spelt as Google Fonts lists it. A family Google does not carry falls back to the default.
			</p>
		{/if}
	{/if}
</div>
