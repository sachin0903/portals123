<script lang="ts">
	// A firm's view of Graphene's identity and AML checks on one client.
	//
	// AML is status and date only, by design: AML is Graphene's regulatory responsibility as the
	// platform provider, and a firm that saw a possible match could tip its client off. The AML row
	// also says whether ongoing monitoring is on. Identity adds each check's status, date, document
	// and sender, and the shown check's photos and analysis. Only Graphene uploads photos and decides
	// every result. The AML matches and every decision never reach this page because the data never
	// arrives. The sidebar also gives the reports to download: the client report, the shown identity
	// check's, the AML screen's, and each application's summary.
	import { invalidateAll } from '$app/navigation';
	import { page } from '$app/state';
	import {
		Button,
		DownloadLink,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		Collapsible,
		documentTypeLabel,
		formatDate,
		hasAnalysis,
		IdentityAnalysisView,
		IdentityPhotoGrid,
		ImageViewer,
		LoadErrorBanner,
		personComplianceFacts,
		screeningRows,
		REFRESH_WHILE,
		RefreshWhile,
		StatusBadge,
		type ViewerImage
	} from '@graphene/ui';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import FileDownIcon from '@lucide/svelte/icons/file-down';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import FileTextIcon from '@lucide/svelte/icons/file-text';
	import type { PageData } from './$types';
	import type { TermAcceptance } from './+page.server';
	import AcceptedTermsDialog from './accepted-terms-dialog.svelte';
	import ConfirmedMatch from './confirmed-match.svelte';
	import {
		identityImageHref,
		identityStatus,
		photosMissingText,
		sentBy,
		shownIndex,
		type FirmIdentityCheck
	} from './identity';

	let { data }: { data: PageData } = $props();

	const rows = $derived(screeningRows(data.kyc));
	// A firm's "In progress" covers a check ComplyCube is running and one Graphene is still
	// deciding. The firm cannot see those apart, so the page asks while either is open.
	const inProgress = $derived(data.kyc.some((check) => check.status === 'pending'));
	const personFacts = $derived(
		data.detail.contact.type === 'organisation' ? [] : personComplianceFacts(data.detail.contact)
	);

	const identity = $derived(data.identity);
	const checks = $derived(identity?.checks ?? []);
	/** The check on show, newest first: 0 is the current check. */
	const at = $derived(identity ? shownIndex(identity) : 0);
	const shown = $derived<FirmIdentityCheck | undefined>(checks[at]);
	/** Newer to the right, older to the left. */
	const older = $derived<FirmIdentityCheck | undefined>(checks[at + 1]);
	const newer = $derived<FirmIdentityCheck | undefined>(at > 0 ? checks[at - 1] : undefined);

	/** This tab showing one check: the current one needs no `?check=`. */
	function checkHref(check: FirmIdentityCheck): string {
		return check.uid === checks[0]?.uid
			? page.url.pathname
			: `${page.url.pathname}?check=${encodeURIComponent(check.uid)}`;
	}

	/** The shown check's photos, as the viewer shows them. */
	const photos = $derived<ViewerImage[]>(
		shown && identity
			? identity.images.map((image) => ({
					url: identityImageHref(data.detail.contact.uid, shown.uid, image.part),
					label: image.label,
					contentType: image.content_type
				}))
			: []
	);

	/** The photo the pop-up shows first, while it is open. */
	let viewing = $state<number | null>(null);

	/** The reports to download, each from this portal's same-origin relay, or why it cannot be yet.
	 *  The ID check report is the shown check's. The API says whether the latest check's report is
	 *  ready; an earlier check's is offered, and the relay gives the reason if it has none. */
	const reportRows = $derived.by(() => {
		const reports = data.reports;
		if (!reports) return [];
		const base = `/clients/${encodeURIComponent(data.detail.contact.uid)}/compliance/reports`;
		const idCheck = shown?.uid ?? reports.identity.check_uid;
		const idLatest = !shown || shown.uid === reports.identity.check_uid;
		return [
			{
				label: 'Client report',
				href: `${base}/client`,
				available: reports.client.available,
				reason: reports.client.reason
			},
			{
				label: 'ID check report',
				href: idCheck
					? `${base}/identity?check=${encodeURIComponent(idCheck)}`
					: `${base}/identity`,
				available: idLatest ? reports.identity.available : true,
				reason: idLatest ? reports.identity.reason : null
			},
			{
				label: 'AML screen report',
				href: `${base}/aml`,
				available: reports.aml.available,
				reason: reports.aml.reason
			}
		];
	});

	/** "Passport · 5 Aug 2026 · Uploaded by Sam Lee". */
	function checkLine(check: FirmIdentityCheck): string {
		return [
			check.document_type ? documentTypeLabel(check.document_type) : null,
			formatDate(check.created_at),
			sentBy(check)
		]
			.filter(Boolean)
			.join(' · ');
	}

	const channelLabel = (channel: string) => (channel === 'in_person' ? 'In person' : 'Portal');

	/** The acceptance whose terms are open, while they are. */
	let reading = $state<TermAcceptance | null>(null);
</script>

<svelte:head><title>Compliance · {data.clientName}</title></svelte:head>

<RefreshWhile
	active={inProgress}
	interval={REFRESH_WHILE.pendingCheck}
	onpoll={() => invalidateAll()}
/>

<div class="space-y-5">
	<!-- The identity check, and beside it any AML match Graphene confirmed; the status, the reports
	     and the accepted terms in a slim sidebar on a wide screen. On a narrow one the sidebar comes
	     first. -->
	<div
		class="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_16rem] xl:grid-cols-[minmax(0,1fr)_18rem]"
	>
		<div class="flex min-w-0 flex-col gap-4 lg:order-2">
			<Card glass size="sm" class="gap-2">
				<CardHeader>
					<CardTitle class="text-base">Status</CardTitle>
				</CardHeader>
				<CardContent class="space-y-2.5">
					{#each rows as row (row.kind)}
						{@const updated = row.check ? formatDate(row.check.updated_at, '') : ''}
						<div class="flex items-start justify-between gap-3 text-xs">
							<div class="min-w-0">
								<div class="truncate font-medium">{row.label}</div>
								{#if row.detail || updated || !row.check}
									<div class="text-muted-foreground">
										{row.detail ?? (updated ? `Updated ${updated}` : 'Not started')}
									</div>
								{/if}
								{#if row.kind === 'aml' && data.amlMonitored !== null}
									<div class="text-muted-foreground">
										Ongoing monitoring {data.amlMonitored ? 'on' : 'off'}
									</div>
								{/if}
								{#if row.kind === 'aml' && data.amlMatches.matches.length}
									<!-- The matches Graphene confirmed and let the onboarding continue past; the
									     card below lists them. -->
									<div class="text-warning-strong font-medium">
										{data.amlMatches.matches.length} confirmed
										{data.amlMatches.matches.length === 1 ? 'match' : 'matches'}
									</div>
								{/if}
							</div>
							<StatusBadge tone={row.tone}>{row.statusLabel}</StatusBadge>
						</div>
					{/each}
					<!-- The person's own compliance answers, as the client record states them. A trust or a
					     company has none: they are facts about a person. -->
					{#if personFacts.length}
						<dl class="space-y-1.5 border-t pt-2.5 text-xs">
							{#each personFacts as [name, value], i (i)}
								<div class="flex items-baseline justify-between gap-3">
									<dt class="text-muted-foreground">{name}</dt>
									<dd class="text-right font-medium">{value ?? '—'}</dd>
								</div>
							{/each}
						</dl>
					{/if}
				</CardContent>
			</Card>

			<!-- The reports to download, each saved in place: a report that cannot be made says why in a
			     toast, and this page stays put. -->
			<Card glass size="sm" class="gap-2">
				<CardHeader>
					<CardTitle class="text-base">Reports</CardTitle>
				</CardHeader>
				<CardContent class="space-y-3 text-xs">
					{#if !data.reports}
						<LoadErrorBanner
							message="The reports could not be loaded."
							onRetry={() => invalidateAll()}
						/>
					{:else}
						<ul class="-mx-1.5 space-y-0.5">
							{#each reportRows as report (report.label)}
								<li>
									{#if report.available}
										<DownloadLink
											href={report.href}
											name={report.label}
											class="hover:bg-muted/60 focus-visible:ring-ring/50 flex items-center gap-2 rounded-md px-1.5 py-1.5 font-medium outline-none focus-visible:ring-2"
										>
											{#snippet children({ busy })}
												{#if busy}
													<LoaderCircleIcon
														class="text-muted-foreground size-4 shrink-0 animate-spin"
														aria-hidden="true"
													/>
												{:else}
													<FileDownIcon
														class="text-muted-foreground size-4 shrink-0"
														aria-hidden="true"
													/>
												{/if}
												{report.label}
											{/snippet}
										</DownloadLink>
									{:else}
										<div class="flex items-start gap-2 px-1.5 py-1.5" aria-disabled="true">
											<FileDownIcon
												class="text-muted-foreground/50 size-4 shrink-0"
												aria-hidden="true"
											/>
											<span class="min-w-0">
												<span class="text-muted-foreground block font-medium">{report.label}</span>
												{#if report.reason}
													<span class="text-muted-foreground block">{report.reason}</span>
												{/if}
											</span>
										</div>
									{/if}
								</li>
							{/each}
						</ul>

						{#if data.canReadApplications && data.reports.applications.length}
							<div class="space-y-1 border-t pt-3">
								<div class="text-muted-foreground font-medium">Applications</div>
								<ul class="space-y-1.5">
									{#each data.reports.applications as application (application.uid)}
										<li class="flex items-start justify-between gap-3">
											<span class="min-w-0">
												<span class="block truncate font-medium">{application.title}</span>
												<span class="text-muted-foreground block">
													{formatDate(application.created_at)}
												</span>
											</span>
											<DownloadLink
												href={`/journeys/${encodeURIComponent(application.uid)}/application.pdf`}
												name="Application summary"
												class="text-primary flex shrink-0 items-center gap-1 font-medium underline-offset-4 hover:underline"
											>
												{#snippet children({ busy })}
													{#if busy}
														<LoaderCircleIcon class="size-3.5 animate-spin" aria-hidden="true" />
													{:else}
														<FileDownIcon class="size-3.5" aria-hidden="true" />
													{/if}
													Application summary
												{/snippet}
											</DownloadLink>
										</li>
									{/each}
								</ul>
							</div>
						{/if}
					{/if}
				</CardContent>
			</Card>

			<!-- What the client agreed to, newest first; each opens the very version they accepted. -->
			<Card glass size="sm" class="gap-3">
				<CardHeader>
					<CardTitle class="flex items-center gap-2 text-base">
						Accepted terms
						<span class="text-muted-foreground text-xs font-normal tabular-nums">
							{data.acceptances.length}
						</span>
					</CardTitle>
				</CardHeader>
				<CardContent class="text-sm">
					{#if data.acceptances.length === 0}
						<p class="text-muted-foreground text-xs">No recorded acceptances yet.</p>
					{:else}
						<ul class="divide-border -mx-1.5 divide-y">
							{#each data.acceptances as acceptance (acceptance.uid)}
								<li>
									<button
										type="button"
										class="hover:bg-muted/60 focus-visible:ring-ring/50 flex w-full items-center gap-3 rounded-md px-1.5 py-2 text-left outline-none focus-visible:ring-2"
										onclick={() => (reading = acceptance)}
									>
										<FileTextIcon
											class="text-muted-foreground size-4 shrink-0"
											aria-hidden="true"
										/>
										<span class="min-w-0 flex-1">
											<span class="block truncate font-medium">{acceptance.title}</span>
											<span class="text-muted-foreground block text-xs">
												v{acceptance.version_number} · {channelLabel(acceptance.channel)} · Accepted
												{formatDate(acceptance.accepted_at)}
											</span>
										</span>
										<span class="text-muted-foreground shrink-0 text-xs">View</span>
									</button>
								</li>
							{/each}
						</ul>
					{/if}
				</CardContent>
			</Card>
		</div>

		<!-- The identity check alone, or the identity check and the confirmed AML matches side by side,
		     the same height, once the column is wide enough for two. -->
		<div class="@container min-w-0 lg:order-1">
			<div class={['grid gap-4', data.amlMatches.matches.length > 0 && '@4xl:grid-cols-2']}>
				<Card glass size="sm" class="min-w-0 gap-3">
					<!-- The title, and the way through the client's tries on the same line: older to the left,
					     newer to the right. -->
					<CardHeader class="flex flex-row flex-wrap items-center justify-between gap-x-3 gap-y-1">
						<div class="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
							<CardTitle class="text-base">Identity check</CardTitle>
							<p class="text-muted-foreground text-xs">Graphene decides every result.</p>
						</div>
						{#if checks.length > 1}
							<nav
								class="-my-1 flex shrink-0 items-center gap-1 text-xs"
								aria-label="Identity checks"
							>
								<Button
									variant="ghost"
									size="icon-sm"
									href={older ? checkHref(older) : undefined}
									disabled={!older}
									aria-label="Older check"
									data-sveltekit-noscroll
									data-sveltekit-replacestate
								>
									<ChevronLeftIcon />
								</Button>
								<span class="text-muted-foreground tabular-nums">
									Check {checks.length - at} of {checks.length}
								</span>
								<Button
									variant="ghost"
									size="icon-sm"
									href={newer ? checkHref(newer) : undefined}
									disabled={!newer}
									aria-label="Newer check"
									data-sveltekit-noscroll
									data-sveltekit-replacestate
								>
									<ChevronRightIcon />
								</Button>
							</nav>
						{/if}
					</CardHeader>
					<CardContent class="space-y-2.5 text-sm">
						{#if !identity}
							<LoadErrorBanner
								message="The identity checks could not be loaded."
								onRetry={() => invalidateAll()}
							/>
						{:else if !shown}
							<p class="text-muted-foreground text-xs">
								No identity check has run for this client.
							</p>
						{:else}
							{@const status = identityStatus(shown.status)}
							<!-- The shown check — its status, its photos, large (a click opens one larger still),
							     and the scores read from them — and its outcomes: side by side when the card has
							     the width, the outcomes under the photos when not. -->
							<div class="@container">
								<div class="grid items-start gap-3 @4xl:grid-cols-2 @4xl:gap-6">
									<div class="min-w-0 space-y-2.5">
										<div class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs">
											<span class="font-medium">{at === 0 ? 'Current check' : 'Earlier check'}</span
											>
											<StatusBadge tone={status.tone}>{status.label}</StatusBadge>
											<span class="text-muted-foreground">{checkLine(shown)}</span>
										</div>
										{#if photos.length}
											<IdentityPhotoGrid {photos} size="large" onopen={(i) => (viewing = i)} />
										{:else}
											<p class="text-muted-foreground text-xs">
												{photosMissingText(identity.images_missing)}
											</p>
										{/if}
										<!-- The face and liveness scores, under the photos they were read from. -->
										{#if identity.analysis && hasAnalysis(identity.analysis)}
											<IdentityAnalysisView analysis={identity.analysis} compact show="scores" />
										{/if}
									</div>
									{#if identity.analysis && hasAnalysis(identity.analysis)}
										<IdentityAnalysisView analysis={identity.analysis} compact show="outcomes" />
									{/if}
								</div>
							</div>

							<!-- Every try at a glance, folded away until asked for; each opens with its photos. -->
							{#if checks.length > 1}
								<Collapsible.Root class="group/checks border-t pt-3">
									<Collapsible.Trigger
										class="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 flex cursor-pointer items-center gap-1 rounded-md text-xs font-medium outline-none focus-visible:ring-2"
									>
										All checks <span class="tabular-nums">({checks.length})</span>
										<ChevronDownIcon
											class="motion-toggle size-3.5 transition-transform group-data-[state=closed]/checks:-rotate-90"
										/>
									</Collapsible.Trigger>
									<Collapsible.Content>
										<ul class="mt-1.5 space-y-1">
											{#each checks as check, i (check.uid)}
												{@const was = identityStatus(check.status)}
												<li>
													<a
														href={checkHref(check)}
														data-sveltekit-noscroll
														data-sveltekit-replacestate
														aria-current={i === at ? 'true' : undefined}
														class="hover:bg-muted/60 aria-[current=true]:bg-muted -mx-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-1.5 py-1 text-xs"
													>
														<StatusBadge tone={was.tone}>{was.label}</StatusBadge>
														<span class="text-muted-foreground min-w-0">{checkLine(check)}</span>
													</a>
												</li>
											{/each}
										</ul>
									</Collapsible.Content>
								</Collapsible.Root>
							{/if}
						{/if}
					</CardContent>
				</Card>

				<!-- The matches Graphene confirmed on the latest AML screen, shown once it let the onboarding
		     continue past them. As screened: never Graphene's decision or comment. -->
				{#if data.amlMatches.matches.length}
					<Card glass size="sm" class="min-w-0 gap-3">
						<CardHeader>
							<CardTitle class="flex items-center gap-2 text-base">
								Confirmed AML matches
								<span class="text-muted-foreground text-xs font-normal tabular-nums">
									{data.amlMatches.matches.length}
								</span>
							</CardTitle>
							<p class="text-muted-foreground text-xs">
								Graphene reviewed these matches, confirmed them and let the onboarding continue{data
									.amlMatches.allowed_at
									? ` on ${formatDate(data.amlMatches.allowed_at)}`
									: ''}.
							</p>
						</CardHeader>
						<CardContent class="text-sm">
							<!-- Each match short, beside the identity check; its full profile opens in a side panel. -->
							<ul class="divide-border divide-y">
								{#each data.amlMatches.matches as match (match.uid)}
									<li class="py-3 first:pt-0 last:pb-0"><ConfirmedMatch {match} /></li>
								{/each}
							</ul>
						</CardContent>
					</Card>
				{/if}
			</div>
		</div>
	</div>
</div>

{#if viewing !== null && photos.length}
	<ImageViewer images={photos} index={viewing} onclose={() => (viewing = null)} />
{/if}

{#if reading}
	<AcceptedTermsDialog
		contactUid={data.detail.contact.uid}
		acceptance={reading}
		onclose={() => (reading = null)}
	/>
{/if}
