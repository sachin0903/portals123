<script lang="ts" module>
	/** One client a login may act for — the account menu's switcher section, for a login linked
	 * to more than one crm.contacts row (a guardian/delegate acting for more than one person). */
	export interface AccountMenuContact {
		id: number;
		name: string;
	}
</script>

<script lang="ts">
	import * as Avatar from '../ui/avatar/index.js';
	import LogOutIcon from '@lucide/svelte/icons/log-out';
	import UserRoundIcon from '@lucide/svelte/icons/user-round';
	import UserCogIcon from '@lucide/svelte/icons/user-cog';
	import {
		DropdownMenu,
		DropdownMenuContent,
		DropdownMenuItem,
		DropdownMenuRadioGroup,
		DropdownMenuRadioItem,
		DropdownMenuSeparator,
		DropdownMenuTrigger
	} from '../ui/dropdown-menu/index.js';

	// The avatar is always the trigger for a menu carrying "Account" + "Sign out" — the same
	// `/logout` POST the account area's own sign-out button submits, so there are two doors to the
	// same action rather than two mechanisms. With more than one linked contact, the SAME menu also
	// carries the client switcher (radio-checked on the active one) above a separator.
	let {
		user,
		accountHref = '/account',
		interactive = true,
		contacts = [],
		activeContactId = null,
		onSelectContact
	}: {
		user: { email: string };
		/** Where the login-settings item points. `/account` on the staff portals; the client
		 *  portal calls the same surface `/profile`. Named for what it addresses rather than
		 *  `href`, because `chrome.test.ts` scans this file's source for `href="/` to prove a
		 *  preview cannot navigate the host app. */
		accountHref?: string;
		/** `false` ⇒ inside the branding preview: no `href`, so the preview can't navigate the host app. */
		interactive?: boolean;
		/** More than one ⇒ the menu's Account/Sign out items sit below a client-switcher section;
		 * 0 or 1 ⇒ the same menu without the switcher. */
		contacts?: AccountMenuContact[];
		activeContactId?: number | null;
		/** Called with the chosen contact id; the caller owns persisting/re-fetching (a cookie +
		 * reload here, in principle whatever the host app's data flow needs). */
		onSelectContact?: (id: number) => void;
	} = $props();

	const initial = $derived(user.email.charAt(0).toUpperCase());
	const showSwitcher = $derived(interactive && contacts.length > 1);
	// One-way, not bind: — the caller (onSelectContact) owns what "active" means (a cookie write
	// + reload), and radioValue just reflects whatever activeContactId comes back as. $derived
	// can't be the target of bind: anyway (it has no setter of its own).
	const radioValue = $derived(activeContactId !== null ? String(activeContactId) : undefined);
	// The client record for whichever contact is currently active — distinct from "Profile"
	// (the login's own settings), this is "who am I currently viewing" (see account/+page.server.ts's
	// ?view=client branch). Falls back to the sole contact when there's no switcher.
</script>

{#snippet avatar()}
	<!-- The same liquid-glass round as the header's other controls (`client-chrome.ts`), the
	     same size, so the row reads as one set — but tinted in the firm's accent: the glass fill
	     and rim are `--brand` mixed to translucency rather than the neutral `--glass-bg`, with
	     the initial in `--brand-strong`. It is the one control in the row that is a person, and
	     the colour is what tells it apart from the three actions beside it. -->
	<Avatar.Root
		class="glass size-10 [--glass-bg:color-mix(in_oklch,var(--brand)_30%,transparent)] [--glass-border:color-mix(in_oklch,var(--brand)_35%,transparent)] md:size-9"
	>
		<Avatar.Fallback class="text-brand-strong bg-transparent font-semibold"
			>{initial}</Avatar.Fallback
		>
	</Avatar.Root>
	<span class="sr-only">Account</span>
{/snippet}

{#if interactive}
	<DropdownMenu>
		<DropdownMenuTrigger
			aria-label="Account"
			title="Account"
			class="focus-visible:ring-ring tap-target block rounded-full transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:outline-none"
		>
			{@render avatar()}
		</DropdownMenuTrigger>
		<!-- The base content is w-(--bits-dropdown-menu-anchor-width) -- bound to the AVATAR's own
		     tiny width, since that's its trigger. min-w overrides it (CSS clamps width up to
		     min-width, never down) -- wide enough for "Account (<name>)" with an ordinary full name
		     before truncate kicks in; the truncate+title on each item's name is the safety net for
		     anything past what this comfortably fits. -->
		<DropdownMenuContent align="end" class="min-w-64">
			{#if showSwitcher}
				<DropdownMenuRadioGroup
					value={radioValue}
					onValueChange={(v) => {
						const id = Number(v);
						if (!Number.isNaN(id)) onSelectContact?.(id);
					}}
				>
					{#each contacts as contact (contact.id)}
						<DropdownMenuRadioItem value={String(contact.id)}>
							<UserRoundIcon class="size-4" />
							<span class="truncate" title={contact.name}>{contact.name}</span>
						</DropdownMenuRadioItem>
					{/each}
				</DropdownMenuRadioGroup>
				<DropdownMenuSeparator />
			{/if}
			<DropdownMenuItem>
				{#snippet child({ props })}
					<a {...props} href={accountHref}>
						<UserCogIcon class="size-4" />
						Profile
					</a>
				{/snippet}
			</DropdownMenuItem>
			<DropdownMenuSeparator />
			<form method="POST" action="/logout">
				<DropdownMenuItem variant="destructive">
					{#snippet child({ props })}
						<button type="submit" {...props} class="{props.class} w-full">
							<LogOutIcon class="size-4" />
							Sign out
						</button>
					{/snippet}
				</DropdownMenuItem>
			</form>
		</DropdownMenuContent>
	</DropdownMenu>
{:else}
	<a
		href={undefined}
		aria-label="Account"
		title="Account"
		class="focus-visible:ring-ring block rounded-full transition-opacity hover:opacity-90 focus-visible:ring-2 focus-visible:outline-none"
	>
		{@render avatar()}
	</a>
{/if}
