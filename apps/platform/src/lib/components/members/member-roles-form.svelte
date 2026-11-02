<script lang="ts">
	// One row's role set: compact chips and the checkbox menu that replaces the set immediately.
	// Shared by Team (staff roles) and Profiles (client roles) — the two post the same
	// `user_id` + `role_slugs` to their own `?/setRoles`, so a second copy could only drift.
	//
	// It exists as a COMPONENT for one reason: the selection has to be seeded synchronously, and a
	// page cannot do that for a list of rows. Both pages used to hold a `Record<userId, string[]>`
	// in `$state`, seed it from an `$effect`, and `bind:value={rowRoles[m.user_id]}`. That loses a
	// race it cannot win: `bind:` on a map entry that is still `undefined` writes MultiSelect's own
	// `[]` fallback BACK into the map during the child's first render, which is before any effect
	// runs — so the seeding guard (`if (!(id in rowRoles))`) then found the key already present and
	// left it empty. Every row rendered "Choose roles…" over a member who did hold roles, on both
	// pages, and the API was returning the roles correctly the whole time. A row that owns its own
	// state cannot lose that race, because a component is constructed before the child it binds to
	// renders.
	import { enhance } from '$app/forms';
	import { tick, untrack } from 'svelte';
	import { RolePicker, type RolePickerOption } from '@graphene/ui';
	import type { SubmitFunction } from '@sveltejs/kit';

	let {
		userId,
		initial,
		options,
		submit,
		pending = false,
		searchPlaceholder = 'Search roles…'
	}: {
		userId: string;
		/** The roles this membership holds now. Read once, at construction. */
		initial: string[];
		options: RolePickerOption[];
		/** The page's own `use:enhance` handler, so the toast copy stays with the page. */
		submit: SubmitFunction;
		pending?: boolean;
		searchPlaceholder?: string;
	} = $props();

	// `untrack` says out loud what the seeding IS — one read of the server's answer, at construction,
	// deliberately not followed. Svelte otherwise flags the bare read as capturing only the initial
	// value, which is the intent: the row is keyed by user, so a member whose roles change server-side
	// arrives as a new instance.
	let value = $state(untrack(() => [...initial]));
	let formEl = $state<HTMLFormElement | null>(null);

	async function save(): Promise<void> {
		// Hidden inputs are rendered from `value`; let Svelte flush the new set before submitting.
		await tick();
		formEl?.requestSubmit();
	}
</script>

<form method="POST" action="?/setRoles" use:enhance={submit} bind:this={formEl}>
	<input type="hidden" name="user_id" value={userId} />
	{#each value as slug (slug)}
		<input type="hidden" name="role_slugs" value={slug} />
	{/each}
	<RolePicker
		{options}
		bind:value
		onchange={save}
		disabled={pending}
		ariaLabel={searchPlaceholder}
	/>
</form>
