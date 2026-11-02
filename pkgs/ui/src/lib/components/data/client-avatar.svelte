<script lang="ts">
	/**
	 * The disc that stands for a person or an entity.
	 *
	 * ONE MUTED DISC, and the ICON is the only thing that varies — a person, or a building for a
	 * trust or a company. It used to be deterministic coloured initials, one hue per name rotated
	 * off the firm's brand. That reads well on a single profile and badly everywhere the product
	 * actually uses it: down a column, or stacked in a rail, it is thirty competing hues beside the
	 * money, each one meaning nothing. `clients-table.svelte` had already stopped using it and
	 * hand-rolled exactly this disc for that reason; this is that decision, made once, for
	 * everywhere.
	 *
	 * The name is still required and still the thing the disc stands for — it is simply not
	 * rendered, because the name is always beside the disc in every surface that mounts one.
	 */
	import BabyIcon from '@lucide/svelte/icons/baby';
	import Building2Icon from '@lucide/svelte/icons/building-2';
	import UserIcon from '@lucide/svelte/icons/user';
	import UsersIcon from '@lucide/svelte/icons/users';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import { cn } from '../../utils.js';
	import type { AvatarKind } from './client-kind.js';

	let {
		name,
		kind = 'individual',
		size = 'md',
		class: className
	}: {
		/** Who the disc stands for. Not rendered — the name is always stated beside it — but kept
		 *  required so a caller cannot mount a disc for nobody. */
		name: string;
		/** A building for a trust or a company, several people for a GROUP, a person for everyone
		 *  else. Defaults to a person: most of the book is individuals, and a surface that does not
		 *  know the type is better showing the common case than an "unknown" glyph nobody can read.
		 *
		 *  `group` exists because the messages list stacks groups and clients in one column, and
		 *  with one glyph for both "The Mitchell Household" was indistinguishable from a person.
		 *
		 *  `child` is THE child glyph for the whole product: a JISA holder on a journey, in the
		 *  client list and on their own record wears the same mark, so an adult and a child are
		 *  never told apart by reading a date. Decide it with `clientKind` (`client-kind.ts`).
		 *
		 *  `account` is the wallet — the glyph the accounts listing, the journey's account cards,
		 *  the dashboard and the notifications already use for an account — for the one record that
		 *  is about a thing rather than a person. */
		kind?: AvatarKind;
		size?: 'sm' | 'md' | 'lg';
		class?: string;
	} = $props();

	const px = { sm: 'size-7', md: 'size-9', lg: 'size-12' };
	const glyph = { sm: 'size-3.5', md: 'size-4', lg: 'size-5' };
</script>

<span
	class={cn(
		'bg-muted text-muted-foreground ring-foreground/10 inline-flex shrink-0 items-center justify-center rounded-full ring-1',
		px[size],
		className
	)}
	aria-hidden="true"
	title={name}
>
	{#if kind === 'organisation'}
		<Building2Icon class={glyph[size]} />
	{:else if kind === 'group'}
		<UsersIcon class={glyph[size]} />
	{:else if kind === 'child'}
		<BabyIcon class={glyph[size]} />
	{:else if kind === 'account'}
		<WalletIcon class={glyph[size]} />
	{:else}
		<UserIcon class={glyph[size]} />
	{/if}
</span>
