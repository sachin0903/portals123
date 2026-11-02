<script lang="ts">
	import { untrack } from 'svelte';
	import { Input } from '../../components/ui/input/index.js';
	import PhoneIcon from '@lucide/svelte/icons/phone';
	import {
		attachPhoneMask,
		canonicalPhoneNumber,
		formatPhoneNumber,
		phoneIssue
	} from '../editor/masks';

	let {
		value = '',
		onchange,
		placeholder = '+44 7700 900123',
		invalid = false,
		id,
		name,
		icon = true,
		disabled = false
	}: {
		value?: unknown;
		onchange?: (v: string) => void;
		placeholder?: string;
		invalid?: boolean;
		id?: string;
		/** Set when the field posts with a plain `<form>` rather than through `onchange`. */
		name?: string;
		/** The leading phone glyph. Off inside the other-numbers rows, where the row already has a
		 *  "Number" label beside a label picker and a glyph per row is noise. */
		icon?: boolean;
		disabled?: boolean;
	} = $props();

	let field = $state<HTMLInputElement | null>(null);
	// Seeded at construction, not in the effect: effects do not run on the server, and an SSR
	// render must show the number it was given. A STORED value is formatted because it is already
	// canonical; what the user types afterwards is left exactly as typed (see `attachPhoneMask`).
	let text = $state(untrack(() => formatPhoneNumber(String(value ?? ''))));

	// The answer wins when it changes from OUTSIDE — a prefill, a reset, a form loading saved
	// values. Guarded on the canonical forms differing, so a re-render mid-entry cannot reformat
	// what is being typed and throw the caret to the end.
	$effect(() => {
		const incoming = String(value ?? '');
		untrack(() => {
			if (canonicalPhoneNumber(incoming) !== canonicalPhoneNumber(text)) {
				text = formatPhoneNumber(incoming);
			}
		});
	});

	/** The sentence under the box. Null while the field is empty — an unanswered optional field is
	 *  not an error, and objecting before anything has been typed is noise. */
	const issue = $derived(phoneIssue(text));

	$effect(() => {
		const el = field;
		if (!el) return;
		// The mask listens on the ELEMENT, not through an `oninput` prop, for the reason
		// `attachDigitMask` documents: a prop loses to the component's own `bind:value`.
		return attachPhoneMask(el, {
			onchange: (next) => {
				// Canonical once it IS a number, raw while it is not. Emitting canonical
				// unconditionally would store `+1111111` for `1111111` — a value E.164 accepts and
				// nobody can ring, which is the case `phoneIssue` exists to catch.
				const issue = phoneIssue(next);
				const stored = issue ? next : canonicalPhoneNumber(next);
				// A finished UK trunk number (`07812 345678`) is shown as `+44 7812 345678`.
				// A half-typed one is left as typed: rewriting `078` to `+44 78` invents a country
				// code from the first digits and throws the caret to the end.
				const typedDigits = next.replace(/\D/g, '');
				const shown =
					!issue && typedDigits !== stored.replace(/\D/g, '') ? formatPhoneNumber(stored) : next;
				text = shown;
				if (el.value !== shown) el.value = shown;
				onchange?.(stored);
			}
		});
	});
</script>

<div>
	<div class="relative">
		{#if icon}
			<PhoneIcon
				class="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
			/>
		{/if}
		<Input
			{id}
			{name}
			bind:ref={field}
			bind:value={text}
			type="tel"
			{placeholder}
			{disabled}
			class={icon ? 'pl-9' : undefined}
			autocomplete="off"
			aria-invalid={invalid || !!issue || undefined}
			aria-describedby={issue && id ? `${id}-issue` : undefined}
		/>
	</div>
	{#if issue}
		<!-- Stated in the field, not only on save. The API refuses the same values, but a refusal
		     that only arrives on Save has already let somebody type a number nobody can ring. -->
		<p id={id ? `${id}-issue` : undefined} class="text-destructive mt-1 text-xs">{issue}</p>
	{/if}
</div>
