<script lang="ts">
	import type { HTMLInputAttributes } from 'svelte/elements';

	// The floating-label text input used across the whole auth flow (login, email
	// verify, MFA). The `placeholder=" "` is required for the `:placeholder-shown`
	// trick that floats the label. Extra attributes (type, inputmode, autocomplete,
	// …) pass straight through to the input.
	//
	// The input is 16px on mobile (`text-base`), 14px from `sm` up (`sm:text-sm`):
	// iOS Safari auto-zooms a focused input whose font is under 16px, and that zoom
	// is what makes the card lurch/rescale as you tab between fields or autofill.
	//
	// `value` is `$bindable` so a caller can WATCH what is being typed — the password-strength row
	// needs the live value. It stays optional and one-way for everyone else: a caller passing a
	// plain string gets today's behaviour, because an unbound prop is simply never written back.
	let {
		id,
		name,
		label,
		value = $bindable(''),
		...rest
	}: { id: string; name: string; label: string; value?: string } & HTMLInputAttributes = $props();
</script>

<div class="relative">
	<input
		{id}
		{name}
		bind:value
		placeholder=" "
		required
		class="border-input bg-background/70 focus:border-brand focus:ring-brand/15 peer h-14 w-full rounded-xl border px-4 pt-4 text-base shadow-sm transition-[color,box-shadow,border-color] outline-none focus:ring-4 sm:text-sm"
		{...rest}
	/>
	<label
		for={id}
		class="text-muted-foreground peer-focus:text-brand pointer-events-none absolute top-4 left-4 text-sm transition-all peer-focus:top-2 peer-focus:text-xs peer-focus:font-medium peer-[:not(:placeholder-shown)]:top-2 peer-[:not(:placeholder-shown)]:text-xs peer-[:not(:placeholder-shown)]:font-medium"
	>
		{label}
	</label>
</div>
