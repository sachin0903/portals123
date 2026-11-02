<script lang="ts">
	import { untrack } from 'svelte';
	import { Input } from '../../components/ui/input/index.js';
	import MailIcon from '@lucide/svelte/icons/mail';
	import { emailIssue } from '../editor/masks';

	let {
		value = '',
		onchange,
		placeholder = 'name@example.com',
		invalid = false,
		id,
		name,
		required = false,
		disabled = false,
		autofocus = false
	}: {
		value?: unknown;
		onchange?: (v: string) => void;
		placeholder?: string;
		invalid?: boolean;
		id?: string;
		/** Set when the field posts with a plain `<form>` rather than through `onchange`. */
		name?: string;
		required?: boolean;
		disabled?: boolean;
		autofocus?: boolean;
	} = $props();

	// Seeded at construction so an SSR render shows the address it was given.
	let text = $state(untrack(() => String(value ?? '')));

	// The answer wins when it changes from OUTSIDE — a reset, a form loading saved values. Guarded
	// on the two differing, so a re-render mid-entry cannot throw the caret to the end.
	$effect(() => {
		const incoming = String(value ?? '');
		untrack(() => {
			if (incoming !== text) text = incoming;
		});
	});

	/** The sentence under the box. Null while the field is empty. */
	const issue = $derived(emailIssue(text));
</script>

<div>
	<div class="relative">
		<MailIcon
			class="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
		/>
		<Input
			{id}
			{name}
			type="email"
			{placeholder}
			{required}
			{disabled}
			{autofocus}
			value={text}
			class="pl-9"
			autocomplete="off"
			autocapitalize="none"
			autocorrect="off"
			spellcheck={false}
			aria-invalid={invalid || !!issue || undefined}
			aria-describedby={issue && id ? `${id}-issue` : undefined}
			oninput={(e) => {
				text = e.currentTarget.value;
				onchange?.(text);
			}}
		/>
	</div>
	{#if issue}
		<!-- Stated in the field as it is typed, the same as the phone field, not only on save. -->
		<p id={id ? `${id}-issue` : undefined} class="text-destructive mt-1 text-xs">{issue}</p>
	{/if}
</div>
