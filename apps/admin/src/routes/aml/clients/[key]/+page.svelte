<script lang="ts">
	// One client in Graphene's AML console: who they are, ComplyCube's risk profile, their latest
	// identity check and AML screen, and every earlier check. Every decision goes to ComplyCube as a
	// validation and is recorded under the operator.
	import { invalidateAll } from '$app/navigation';
	import {
		Button,
		buttonVariants,
		DownloadLink,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		Collapsible,
		EmptyState,
		IdentityUploadButton,
		IdentityUploadDialog,
		LoadErrorBanner,
		Logo,
		RefreshWhile,
		formatPhoneNumber,
		PersonAvatar,
		StatusBadge,
		Stepper,
		clientTeamRoleLabel,
		countryName,
		humanise,
		orderClientTeam
	} from '@graphene/ui';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import FileDownIcon from '@lucide/svelte/icons/file-down';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import MailIcon from '@lucide/svelte/icons/mail';
	import MapPinIcon from '@lucide/svelte/icons/map-pin';
	import PhoneIcon from '@lucide/svelte/icons/phone';
	import ScanFaceIcon from '@lucide/svelte/icons/scan-face';
	import {
		KIND_LABEL,
		ORIGIN_LABEL,
		PENDING_REFRESH_MS,
		SCREEN_STARTING_REFRESH_MS,
		clientHref,
		clientReportHref,
		hasChecks,
		identitySteps,
		shortDate,
		statusOf,
		type AmlAdviser,
		type AmlCheckDetail,
		type IdentitySteps
	} from '$lib/aml';
	import AmlSection from '$lib/compliance/aml-section.svelte';
	import IdentityDecisionDialog, {
		type CheckDecision
	} from '$lib/compliance/identity-decision-dialog.svelte';
	import IdentitySection from '$lib/compliance/identity-section.svelte';
	import RequestPhotosDialog from '$lib/compliance/request-photos-dialog.svelte';
	import RiskSummary from '$lib/compliance/risk-summary.svelte';
	import NotesSection from './notes-section.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	const page = $derived(data.page);
	const client = $derived(page.client);

	/** Who the client is: the screening facts always, the rest when Graphene has them. */
	const personal = $derived(
		(
			[
				['Date of birth', shortDate(client.dob)],
				['Nationality', countryName(client.nationality) || '—'],
				['Country of residence', countryName(client.country) || '—'],
				['Gender', client.gender ? humanise(client.gender) : null],
				['Occupation', client.occupation ?? null],
				['Client since', client.client_since ? shortDate(client.client_since) : null]
			] as [string, string | null][]
		).filter((fact): fact is [string, string] => !!fact[1])
	);

	/** How to reach the client, each line with its icon. */
	const contact = $derived(
		[
			{ label: 'Email', value: client.email, icon: MailIcon },
			{
				label: 'Phone',
				value: client.phone ? formatPhoneNumber(client.phone) : null,
				icon: PhoneIcon
			},
			{ label: 'Address', value: client.address, icon: MapPinIcon }
		].filter((line): line is typeof line & { value: string } => !!line.value)
	);

	/** The client's team, adviser first; the adviser's name alone when that is all there is. The
	 *  adviser is the one with `role === 'adviser'` (at most one per client) — not merely the first
	 *  row, and not anyone who happens to be allowed to advise. */
	const advisers = $derived(
		orderClientTeam(
			client.advisers?.length
				? client.advisers
				: client.adviser
					? [
							{
								uid: null,
								name: client.adviser,
								email: null,
								phone: null,
								job_title: null,
								fca_reference: null,
								role: 'adviser'
							}
						]
					: []
		)
	);
	/** "Pat (Paraplanner)" — the rest of the team by their role on this client, never all
	 *  "advisers". */
	const teamMember = (a: AmlAdviser) => {
		const role = clientTeamRoleLabel(a.role);
		const name = a.name ?? a.email ?? 'Unnamed';
		return role ? `${name} (${role})` : name;
	};

	/** The client's record at their firm; a partner client has none here. */
	const clientRecordHref = $derived(
		client.contact_uid
			? `/tenants/${client.tenant_uid}/operate/clients/${client.contact_uid}`
			: null
	);

	/** A section label inside the client card. */
	const SECTION = 'text-muted-foreground text-[0.7rem] font-semibold tracking-wide uppercase';

	type FormState = { action?: string; error?: string; match?: string };
	const f = $derived((form ?? {}) as unknown as FormState);

	// ── The client's steps ───────────────────────────────────────────────────────────────────────

	const steps = $derived(page.steps ?? []);

	// ── Identity decision ────────────────────────────────────────────────────────────────────────

	let checkTarget = $state<{ check: AmlCheckDetail; decision: CheckDecision } | null>(null);

	// ── Ask for new photos ───────────────────────────────────────────────────────────────────────

	let photoTarget = $state<AmlCheckDetail | null>(null);

	// ── Upload new photos ────────────────────────────────────────────────────────────────────────

	let uploading = $state(false);

	// While a check waits on ComplyCube, ask for the page again so its result shows without a reload.
	// Only while the tab is in view, and never while a dialog is open on a decision or an upload.
	// A queued screen starts within seconds, so the page asks more often until it has.
	const starting = $derived(page.awaiting_screen && !!page.screen_queued);
	const waiting = $derived(
		starting || page.identity?.check.status === 'pending' || page.aml?.check.status === 'pending'
	);
</script>

<RefreshWhile
	active={waiting && !checkTarget && !photoTarget && !uploading}
	interval={starting ? SCREEN_STARTING_REFRESH_MS : PENDING_REFRESH_MS}
	onpoll={() => invalidateAll()}
/>

{#snippet identity(
	detail: AmlCheckDetail,
	title: string,
	latest: boolean,
	steps: IdentitySteps | null = null
)}
	<IdentitySection
		{detail}
		{title}
		{steps}
		canAdjudicate={data.canAdjudicate}
		upload={latest ? (page.identity_upload ?? null) : null}
		ondecide={(decision) => (checkTarget = { check: detail, decision })}
		onrequestphotos={() => (photoTarget = detail)}
		onupload={() => (uploading = true)}
	/>
{/snippet}

{#snippet uploadAction()}
	{#if page.identity_upload}
		<div class="flex flex-col items-center gap-1">
			<IdentityUploadButton upload={page.identity_upload} onclick={() => (uploading = true)} />
		</div>
	{/if}
{/snippet}

{#snippet aml(detail: AmlCheckDetail | null, title: string, latest: boolean)}
	<AmlSection
		{detail}
		{title}
		awaiting={latest && page.awaiting_screen}
		queued={latest && !!page.screen_queued}
		stale={latest && page.stale}
		contactUid={client.contact_uid}
		canWrite={data.canWrite}
		canAdjudicate={data.canAdjudicate}
	/>
{/snippet}

<svelte:head><title>{client.name} · AML · Operations</title></svelte:head>

<!-- No page title: the client card below names the client. The back link matches the page header's. -->
<a
	href="/aml/clients"
	class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 -mx-1 mb-4 inline-flex items-center gap-1 rounded px-1 text-sm font-medium transition-colors focus-visible:ring-[3px] focus-visible:outline-none"
>
	<ChevronLeftIcon class="size-4" />
	AML and identity
</a>

<!-- One screen: the client in a slim strip, then the identity check and the AML screen side by side
     (stacked below xl), the AML screen wider: its match profiles carry the most to read. Earlier
     checks fold away under them. -->
<div class="space-y-4">
	<!-- The client as the clients view shows one: the firm (mark and name), the client's name (their
	     record at the firm), and their adviser in the top-right corner; under them, the client's steps.
	     Below, three parts side by side (stacked below lg): who the client is, how to reach them, and
	     ComplyCube's risk profile. -->
	<Card glass>
		<CardHeader
			class="border-border flex flex-row flex-wrap items-center justify-between gap-4 border-b"
		>
			<div class="flex min-w-0 flex-wrap items-center gap-x-5 gap-y-2">
				<!-- The firm: its mark and its name, a piece of its own. -->
				<a
					href={`/tenants/${client.tenant_uid}`}
					class="focus-visible:ring-ring/50 flex min-w-0 shrink-0 items-center gap-2 rounded-md text-sm font-medium outline-none hover:underline focus-visible:ring-2"
				>
					<Logo
						variant="mark"
						iconSrc={client.tenant_icon_url ?? undefined}
						name={client.tenant_name}
						markClass="size-8 rounded-md"
					/>
					<span class="truncate">{client.tenant_name}</span>
				</a>
				<span class="bg-border hidden h-8 w-px sm:block" aria-hidden="true"></span>
				<!-- The client: their name opens their record at the firm. -->
				<div class="min-w-0">
					<p class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
						{#if clientRecordHref}
							<a href={clientRecordHref} class="truncate text-base font-semibold hover:underline">
								{client.name || 'Unnamed client'}
							</a>
						{:else}
							<span class="truncate text-base font-semibold">{client.name || 'Unnamed client'}</span
							>
						{/if}
						{#if page.needs_attention}<StatusBadge tone="warning">Needs attention</StatusBadge>{/if}
					</p>
					<p class="text-muted-foreground truncate text-xs">{ORIGIN_LABEL[client.origin]} client</p>
				</div>
				{#if clientRecordHref}
					<Button variant="outline" size="sm" href={clientRecordHref}>Client record</Button>
				{/if}
				<!-- The client report, once the client has been checked. -->
				{#if hasChecks(page)}
					<DownloadLink
						href={clientReportHref(page.key)}
						name="Client report"
						class={buttonVariants({ variant: 'outline', size: 'sm' })}
					>
						{#snippet children({ busy })}
							{#if busy}
								<LoaderCircleIcon class="size-4 animate-spin" />
							{:else}
								<FileDownIcon class="size-4" />
							{/if}
							Client report
						{/snippet}
					</DownloadLink>
				{/if}
			</div>

			{#if advisers.length}
				{@const shown = advisers[0]}
				{@const shownRole = clientTeamRoleLabel(shown.role)}
				<!-- The adviser, in the top-right corner: photo first, then their name and how to reach
				     them, left-aligned. -->
				<div class="ml-auto flex min-w-0 items-center gap-2.5">
					{#if shown.photo_url}
						<img
							src={shown.photo_url}
							alt=""
							referrerpolicy="no-referrer"
							class="size-9 shrink-0 rounded-full object-cover"
						/>
					{:else}
						<PersonAvatar name={shown.name ?? shown.email ?? '?'} />
					{/if}
					<div class="min-w-0 text-left">
						<p class="flex items-center gap-2">
							<span class="truncate font-medium">{shown.name ?? shown.email ?? 'Unnamed'}</span>
							{#if shown.role === 'adviser'}
								<StatusBadge tone="info">Adviser</StatusBadge>
							{:else if shownRole}
								<!-- No adviser on this client: the first of the team, by their own role. -->
								<StatusBadge tone="neutral">{shownRole}</StatusBadge>
							{/if}
						</p>
						<p class="text-muted-foreground truncate text-xs">
							{[shown.job_title, shown.email, shown.phone ? formatPhoneNumber(shown.phone) : null]
								.filter(Boolean)
								.join(' · ')}
						</p>
						{#if advisers.length > 1}
							<p class="text-muted-foreground text-xs">
								and {advisers.slice(1).map(teamMember).join(', ')}
							</p>
						{/if}
					</div>
				</div>
			{:else}
				<p class="text-muted-foreground ml-auto text-xs">No adviser assigned.</p>
			{/if}

			{#if steps.length}
				<!-- The steps as the work queue draws them, read only: the workbench is where they open. -->
				<Stepper {steps} class="w-full min-w-0" />
			{/if}
		</CardHeader>

		<CardContent class="grid gap-x-8 gap-y-6 text-sm lg:grid-cols-3">
			<section class="min-w-0 space-y-2">
				<h2 class={SECTION}>Client</h2>
				<dl class="grid grid-cols-2 gap-x-6 gap-y-3">
					{#each personal as [label, value] (label)}
						<div class="min-w-0">
							<dt class="text-muted-foreground text-xs">{label}</dt>
							<dd class="truncate" title={value}>{value}</dd>
						</div>
					{/each}
				</dl>
			</section>

			<section class="border-border min-w-0 space-y-2 lg:border-l lg:pl-8">
				<h2 class={SECTION}>Contact</h2>
				{#if contact.length}
					<ul class="space-y-1.5">
						{#each contact as line (line.label)}
							<li class="flex min-w-0 items-start gap-2">
								<line.icon
									class="text-muted-foreground mt-0.5 size-4 shrink-0"
									aria-hidden="true"
								/>
								<span class="sr-only">{line.label}:</span>
								<span class="min-w-0 break-words">{line.value}</span>
							</li>
						{/each}
					</ul>
				{:else}
					<p class="text-muted-foreground">No contact details on record.</p>
				{/if}
			</section>

			<div class="border-border min-w-0 lg:border-l lg:pl-8">
				<RiskSummary risk={page.risk} />
			</div>
		</CardContent>
	</Card>

	<div class="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
		{#if page.identity}
			<!-- The identity try on show: the current one, or an earlier one stepped to with ‹ ›. -->
			{@const shown = data.earlier?.check.kind === 'identity' ? data.earlier : page.identity}
			{@render identity(
				shown,
				'Identity',
				shown === page.identity,
				identitySteps(page.key, page.identity.check.uid, page.earlier, shown.check.uid)
			)}
		{:else}
			<Card glass>
				<CardHeader class="border-border border-b">
					<CardTitle class="flex items-center gap-2 text-base">
						<ScanFaceIcon class="text-muted-foreground size-4" /> Identity
					</CardTitle>
				</CardHeader>
				<CardContent>
					<EmptyState
						compact
						icon={ScanFaceIcon}
						title="No identity check"
						description="The client has not taken an identity check with Graphene."
						action={data.canAdjudicate && page.identity_upload ? uploadAction : undefined}
					/>
				</CardContent>
			</Card>
		{/if}

		{@render aml(page.aml, 'AML screen', true)}
	</div>

	<NotesSection notes={page.notes ?? []} canWrite={data.canWrite} />

	{#if page.earlier.length}
		<Collapsible.Root class="group/earlier" open={data.earlier !== null}>
			<Collapsible.Trigger
				class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex cursor-pointer items-center gap-1 rounded-md text-sm font-medium outline-none focus-visible:ring-2"
			>
				Earlier checks <span class="tabular-nums">({page.earlier.length})</span>
				<ChevronDownIcon
					class="motion-toggle size-4 transition-transform group-data-[state=closed]/earlier:-rotate-90"
				/>
			</Collapsible.Trigger>
			<Collapsible.Content>
				<Card glass class="mt-2 gap-2 py-2">
					<CardContent>
						<ul class="divide-border divide-y text-sm">
							{#each page.earlier as item (item.uid)}
								{@const s = statusOf(item.status)}
								{@const opened = data.earlier?.check.uid === item.uid}
								<li class="flex flex-wrap items-center justify-between gap-3 py-2">
									<span class="min-w-0">
										<span class="font-medium">{KIND_LABEL[item.kind]}</span>
										<span class="text-muted-foreground tabular-nums">
											· {shortDate(item.created_at)}</span
										>
									</span>
									<span class="flex items-center gap-2">
										{#if item.needs_attention}
											<StatusBadge tone="warning">Needs attention</StatusBadge>
										{/if}
										<StatusBadge tone={s.tone}>{s.label}</StatusBadge>
										<Button
											variant="ghost"
											size="sm"
											href={opened ? clientHref(page.key) : clientHref(page.key, item.uid)}
											>{opened ? 'Close' : 'Open'}</Button
										>
									</span>
								</li>
							{/each}
						</ul>
					</CardContent>
				</Card>
			</Collapsible.Content>
		</Collapsible.Root>
	{/if}

	{#if data.earlierError}
		<LoadErrorBanner message={data.earlierError} />
	{/if}

	{#if data.earlier}
		{@const title = `Earlier ${KIND_LABEL[data.earlier.check.kind].toLowerCase()} · ${shortDate(data.earlier.check.created_at)}`}
		<!-- An earlier identity try shows in the identity card above, stepped to with ‹ ›. -->
		{#if data.earlier.check.kind !== 'identity' || !page.identity}
			{#if data.earlier.check.kind === 'identity'}
				{@render identity(data.earlier, title, false)}
			{:else}
				{@render aml(data.earlier, title, false)}
			{/if}
		{/if}
	{/if}
</div>

{#if checkTarget}
	<IdentityDecisionDialog
		target={checkTarget}
		clientName={client.name}
		error={f.action === 'decideCheck' ? f.error : null}
		onclose={() => (checkTarget = null)}
	/>
{/if}

{#if photoTarget}
	<!-- Mounted per open, like the check dialog above. -->
	<RequestPhotosDialog
		detail={photoTarget}
		clientName={client.name}
		error={f.action === 'requestPhotos' ? f.error : null}
		onclose={() => (photoTarget = null)}
	/>
{/if}

{#if uploading && page.identity_upload}
	<!-- Mounted per open, like the dialogs above. -->
	<IdentityUploadDialog
		clientName={client.name}
		upload={page.identity_upload}
		noteLabel="Note (internal — never shown to the client or firm)"
		notePlaceholder="Why these photos were uploaded, and where they came from."
		onclose={() => (uploading = false)}
	/>
{/if}
