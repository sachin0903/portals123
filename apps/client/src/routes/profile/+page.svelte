<script lang="ts">
	import {
		Alert,
		AlertDescription,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		EmptyState,
		LoadErrorBanner,
		ThemeToggle
	} from '@graphene/ui';
	// The one label-casing rule the staff listings use, so "landline" reads the same word here as
	// it does on the record an adviser typed it into.
	import { invalidateAll } from '$app/navigation';
	import InfoIcon from '@lucide/svelte/icons/info';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import UserIcon from '@lucide/svelte/icons/user-round';

	let { data } = $props();

	const profile = $derived(data.profile);
	// Name and email fall back to the signed-in identity when the CRM record leaves them blank —
	// both are real, just from different sources, and this page is now only ever about the person
	// signed in. (It used to also render whichever client the switcher pointed at, under
	// `?view=client`; that was a second "account" for one login and is gone.)
	const firstName = $derived(profile?.firstName ?? data.account.firstName);
	const lastName = $derived(profile?.lastName ?? data.account.lastName);
	const email = $derived(profile?.email ?? data.account.email);
	const address = $derived(profile?.address ?? null);
	const phone = $derived(profile?.phone ?? '');
	const addressLines = $derived(
		[address?.line1, address?.line2, address?.city, address?.postcode, address?.country]
			.map((part) => (part ?? '').trim())
			.filter(Boolean)
	);
</script>

<div class="space-y-6">
	{#if data.loadError}
		<LoadErrorBanner
			message="We couldn’t load your details. Please try again."
			onRetry={() => invalidateAll()}
		/>
	{/if}

	{#if data.unresolved}
		<!-- The loader could not tie this login to a party held as *themselves*, so there is no
		     record to show. It deliberately does NOT substitute another party the caller is linked
		     to (a relative they hold power of attorney for, say) — that would put a stranger's name,
		     phone and address under "your details". -->
		<Alert variant="warning">
			<TriangleAlertIcon class="size-4" />
			<AlertDescription>
				We couldn’t find all the details your firm holds for you. Contact your adviser for help.
			</AlertDescription>
		</Alert>
	{:else}
		<!-- Editing is not wired: `crm.contacts` is read-only to a client session and the only write
		     path is the staff `contacts:write` one. Saying so is the honest option — the previous form
		     validated edits and threw them away. -->
		<Alert>
			<InfoIcon class="size-4" />
			<AlertDescription>
				These are the details your firm holds for you. Contact your adviser if anything needs to
				change.
			</AlertDescription>
		</Alert>
	{/if}

	<Card glass>
		<CardHeader>
			<CardTitle>Personal details</CardTitle>
			<CardDescription>Your name as it appears on your account.</CardDescription>
		</CardHeader>
		<CardContent class="grid gap-4 sm:grid-cols-2">
			<div class="grid gap-1">
				<span class="text-muted-foreground text-xs font-medium tracking-wide">First name</span>
				<span class="text-sm">{firstName || 'Not recorded'}</span>
			</div>
			<div class="grid gap-1">
				<span class="text-muted-foreground text-xs font-medium tracking-wide">Last name</span>
				<span class="text-sm">{lastName || 'Not recorded'}</span>
			</div>
		</CardContent>
	</Card>

	<Card glass>
		<CardHeader>
			<CardTitle>Contact details</CardTitle>
			<CardDescription>How we and your adviser reach you.</CardDescription>
		</CardHeader>
		<CardContent class="grid gap-4 sm:grid-cols-2">
			<div class="grid gap-1">
				<span class="text-muted-foreground text-xs font-medium tracking-wide">Email address</span>
				<span class="text-sm break-words">{email || 'Not recorded'}</span>
			</div>
			<div class="grid gap-1">
				<span class="text-muted-foreground text-xs font-medium tracking-wide">Mobile</span>
				<span class="text-sm">{profile?.mobilePhone || 'Not recorded'}</span>
			</div>
			<div class="grid gap-1">
				<span class="text-muted-foreground text-xs font-medium tracking-wide">Phone</span>
				<span class="text-sm">{phone || 'Not recorded'}</span>
			</div>
		</CardContent>
	</Card>

	<Card glass>
		<CardHeader>
			<CardTitle>Address</CardTitle>
			<CardDescription>Your registered correspondence address.</CardDescription>
		</CardHeader>
		<CardContent>
			{#if addressLines.length}
				<address class="text-sm not-italic">
					{#each addressLines as line (line)}
						<div>{line}</div>
					{/each}
				</address>
			{:else}
				<!-- Two different facts under one title. An unreadable client record is not an empty
				     address field: the first is something wrong that the firm should hear about, the
				     second is a blank nobody has filled in. -->
				<EmptyState
					compact
					tone={data.unresolved ? 'warning' : 'neutral'}
					icon={UserIcon}
					title={data.unresolved ? 'We couldn’t find your record' : 'No address on file'}
					description={data.unresolved
						? 'We couldn’t load your address. Contact your adviser for help.'
						: 'Your firm hasn’t recorded a correspondence address for you.'}
				/>
			{/if}
		</CardContent>
	</Card>

	<!-- The light/dark/system switcher, moved here from the top bar: a colour scheme is a setting
	     a client picks once, not an action for every visit. The scheme itself is server-rendered
	     from the `gp-theme` cookie in hooks.server.ts; this only writes the choice. -->
	<Card glass>
		<CardHeader>
			<CardTitle>Appearance</CardTitle>
		</CardHeader>
		<CardContent class="flex flex-wrap items-center justify-between gap-3">
			<span class="text-sm">Colour theme</span>
			<ThemeToggle inline />
		</CardContent>
	</Card>
</div>
