<script lang="ts">
	// One AML screen: whether it ran, what ComplyCube matched, and the acts on it — screen, run again,
	// monitoring, and a decision per match. A screen is never decided as a whole: it settles from its
	// matches. `compact` is for the workbench: a tighter card, its matches one above the other with
	// their details folded. The card never scrolls inside itself: it grows with its matches.
	import { enhance } from '$app/forms';
	import {
		Alert,
		AlertDescription,
		AlertTitle,
		Button,
		buttonVariants,
		DownloadLink,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		EmptyState,
		Label,
		StatusBadge,
		Textarea
	} from '@graphene/ui';
	import FileDownIcon from '@lucide/svelte/icons/file-down';
	import RadarIcon from '@lucide/svelte/icons/radar';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import ScanSearchIcon from '@lucide/svelte/icons/scan-search';
	import ShieldCheckIcon from '@lucide/svelte/icons/shield-check';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import {
		ORIGIN_LABEL,
		SCREEN_STARTING,
		STATUS_LABEL,
		canDecideMatches,
		heldOnConfirmedMatch,
		referDecided,
		canRerun,
		monitoringCovers,
		checkReportHref,
		confirmedReferral,
		dateTime,
		shortDate,
		statusOf,
		undecidedMatches,
		type AmlCheckDetail
	} from '$lib/aml';
	import CheckHistory from './check-history.svelte';
	import DecisionBox from './decision-box.svelte';
	import MatchCard from './match-card.svelte';
	import PendingPanel from './pending-panel.svelte';
	import { submit } from './submit';

	let {
		detail,
		title,
		awaiting = false,
		queued = false,
		stale = false,
		contactUid,
		canWrite,
		canAdjudicate,
		compact = false
	}: {
		detail: AmlCheckDetail | null;
		title: string;
		/** The client is due a screen: a pending AML request no check holds yet. */
		awaiting?: boolean;
		/** The worker already has that screen queued: it starts by itself, so no Screen button. */
		queued?: boolean;
		/** The client's details changed after this screen cleared. */
		stale?: boolean;
		/** The firm's client, for a screen. Null for a partner client. */
		contactUid: string | null;
		canWrite: boolean;
		canAdjudicate: boolean;
		/** A tighter card, its matches one above the other with their details folded: the
		 *  workbench, which works many in a row. */
		compact?: boolean;
	} = $props();

	const check = $derived(detail?.check ?? null);
	const held = $derived(detail !== null && heldOnConfirmedMatch(detail));
	const status = $derived(
		awaiting
			? queued
				? SCREEN_STARTING
				: STATUS_LABEL.awaiting_screen
			: !check
				? null
				: held
					? { label: 'Held', tone: 'warning' as const }
					: check.status === 'refer' && check.decided_at
						? referDecided(check.origin)
						: statusOf(check.status)
	);
	const open = $derived(detail ? undecidedMatches(detail) : 0);
	const decidable = $derived(canAdjudicate && detail !== null && canDecideMatches(detail));

	let allowNote = $state('');
	let allowing = $state(false);

	let screening = $state(false);
	let rerunning = $state(false);
	let monitorPending = $state(false);
</script>

{#snippet screenButton(label: string)}
	<form
		method="POST"
		action="?/screen"
		class="mt-2"
		use:enhance={submit('Screen started.', (b) => (screening = b))}
	>
		<input type="hidden" name="contact_uid" value={contactUid ?? ''} />
		<Button type="submit" variant="outline" size="sm" disabled={screening}>
			<ScanSearchIcon class="size-4" />
			{screening ? 'Screening…' : label}
		</Button>
	</form>
{/snippet}

{#snippet screenNow()}
	{@render screenButton('Screen')}
{/snippet}

<Card glass size={compact ? 'sm' : 'default'}>
	<!-- The title, the screen's matches, its badges and its report on one row; the run line below
	     it, full width, so a long check id never pushes the status onto a row of its own. -->
	<CardHeader class="border-border border-b">
		<div class="flex flex-wrap items-center gap-2">
			<CardTitle class="flex items-center gap-2 text-base">
				<ShieldCheckIcon class="text-muted-foreground size-4" />
				{title}
			</CardTitle>
			{#if detail && detail.matches.length}
				<span class="text-muted-foreground text-xs tabular-nums">
					{detail.matches.length}
					{detail.matches.length === 1 ? 'match' : 'matches'} · {open} undecided
				</span>
			{/if}
			{#if check && monitoringCovers(check)}<StatusBadge tone="info">Monitored</StatusBadge>{/if}
			{#if stale}<StatusBadge tone="warning">Stale</StatusBadge>{/if}
			{#if status}<StatusBadge tone={status.tone}>{status.label}</StatusBadge>{/if}
			{#if check}
				<DownloadLink
					href={checkReportHref(check.uid)}
					name="AML screen report"
					class="{buttonVariants({ variant: 'ghost', size: 'sm' })} ml-auto"
				>
					{#snippet children({ busy })}
						{#if busy}
							<LoaderCircleIcon class="size-4 animate-spin" />
						{:else}
							<FileDownIcon class="size-4" />
						{/if}
						Report
					{/snippet}
				</DownloadLink>
			{/if}
		</div>
		{#if check}
			<p class="text-muted-foreground text-xs">
				{ORIGIN_LABEL[check.origin]} · run {dateTime(check.created_at)}
				{#if check.provider_check_id}
					· <span class="font-mono break-all">{check.provider_check_id}</span>
				{/if}
			</p>
		{/if}
	</CardHeader>

	<CardContent class="@container text-sm {compact ? 'space-y-3' : 'space-y-4'}">
		{#if awaiting && queued}
			<!-- Identity passed and the screen is queued: the worker starts it in a few seconds and
			     the page asks again until it has. Nothing for the operator to press. -->
			<Alert variant="info">
				<LoaderCircleIcon class="size-4 animate-spin motion-reduce:animate-none" />
				<AlertTitle>Starting the AML screen</AlertTitle>
				<AlertDescription>
					Identity passed, so the screen runs by itself. This page updates when it starts.
				</AlertDescription>
			</Alert>
		{:else if awaiting}
			<!-- Due a screen with none queued: it did not start (ComplyCube refused it, or it is not
			     configured), so an operator screens the client here. -->
			<Alert variant="warning">
				<ScanSearchIcon class="size-4" />
				<AlertTitle>The AML screen did not start</AlertTitle>
				<AlertDescription>
					<span class="block"
						>The client is due a screen and none is running. Screen them here.</span
					>
					{#if canWrite && contactUid}{@render screenButton('Screen')}{/if}
				</AlertDescription>
			</Alert>
		{/if}

		{#if !detail || !check}
			{#if !awaiting}
				<!-- A journey's AML step screens the client by itself; a client with none (an identity-only
				     journey, or none at all) is screened here. -->
				<EmptyState
					compact
					icon={ShieldCheckIcon}
					title="Not screened yet"
					description="A journey's AML step screens the client by itself. Screen them here to run one now."
					action={canWrite && contactUid ? screenNow : undefined}
				/>
			{/if}
		{:else}
			{#if canRerun(detail)}
				<!-- A technical failure, not a result: nothing is decided about the client, and the
				     firm still sees the screen as in progress. -->
				<Alert variant="warning">
					<TriangleAlertIcon class="size-4" />
					<AlertTitle>The screen did not run</AlertTitle>
					<AlertDescription>
						<span class="block">ComplyCube could not complete this screen. Run it again.</span>
						{#if canWrite}
							<form
								method="POST"
								action="?/rerun"
								class="mt-2"
								use:enhance={submit('Screen started.', (b) => (rerunning = b))}
							>
								<input type="hidden" name="check" value={check.uid} />
								<Button type="submit" variant="outline" size="sm" disabled={rerunning}>
									<ScanSearchIcon class="size-4" />
									{rerunning ? 'Screening…' : 'Run it again'}
								</Button>
							</form>
						{/if}
					</AlertDescription>
				</Alert>
			{/if}

			{#if stale && check.stale_at}
				<Alert variant="warning">
					<TriangleAlertIcon class="size-4" />
					<AlertTitle>The client changed after this screen</AlertTitle>
					<AlertDescription>
						<span class="block">
							Their name, date of birth or nationality changed on {shortDate(check.stale_at)}, so
							this result no longer describes them.
						</span>
						{#if canWrite && contactUid}{@render screenButton('Screen again')}{/if}
					</AlertDescription>
				</Alert>
			{/if}

			{#if confirmedReferral(detail)}
				<Alert variant="destructive">
					<TriangleAlertIcon class="size-4" />
					<AlertTitle>Referred on a confirmed match</AlertTitle>
					<AlertDescription>
						Every match has a decision and at least one is confirmed. The partner firm sees this
						client as referred.
					</AlertDescription>
				</Alert>
			{/if}

			{#if check.decided_at}
				{@const settled =
					check.status === 'refer' ? referDecided(check.origin) : statusOf(check.status)}
				<DecisionBox label={settled.label} tone={settled.tone}>
					<div class="text-xs">
						<span class="text-muted-foreground">
							{check.decided_by ?? 'Graphene'} · {dateTime(check.decided_at)}
						</span>
						{#if check.decision_comment}
							<p class="mt-0.5 text-sm">{check.decision_comment}</p>
						{/if}
					</div>
				</DecisionBox>
			{/if}

			{#if check.status === 'pending' && !detail.matches.length}
				<PendingPanel
					sent={check.created_at}
					title="Screening with ComplyCube"
					icon={ScanSearchIcon}
					checking={['Sanctions', 'PEP', 'Watchlists', 'Adverse media']}
					next="Any matches show here for review. No match clears the screen."
				/>
			{:else if detail.matches.length}
				<div class="grid gap-3 {detail.matches.length > 1 && !compact ? '@4xl:grid-cols-2' : ''}">
					{#each detail.matches as match (match.uid)}
						<MatchCard
							{match}
							checkUid={check.uid}
							canDecide={decidable}
							lastOpen={match.decision === null && open === 1}
							portal={check.origin === 'portal'}
							{compact}
						/>
					{/each}
				</div>
			{:else}
				<EmptyState
					compact
					icon={ShieldCheckIcon}
					title="No matches"
					description={check.status === 'failed'
						? 'The screen did not run, so there is nothing to review.'
						: 'ComplyCube found no sanctions, PEP or adverse media match.'}
				/>
			{/if}

			{#if held}
				<!-- A confirmed match holds the journey: the AML step waits, and the firm sees the screen
				     as in progress, never the match. Only Graphene lets it continue. -->
				<Alert variant="warning">
					<TriangleAlertIcon class="size-4" />
					<AlertTitle>Held on a confirmed match</AlertTitle>
					<AlertDescription>
						<span class="block">
							The client's journey waits at the AML step until Graphene allows it to continue. The
							firm sees the screen as in progress.
						</span>
						{#if canAdjudicate}
							<form
								method="POST"
								action="?/allowJourney"
								class="mt-3 space-y-2"
								use:enhance={submit(
									'The journey continues.',
									(b) => (allowing = b),
									() => (allowNote = '')
								)}
							>
								<input type="hidden" name="check" value={check.uid} />
								<Label for={`allow-${check.uid}`} class="text-xs">Comment (optional)</Label>
								<Textarea
									id={`allow-${check.uid}`}
									name="comment"
									bind:value={allowNote}
									rows={2}
									placeholder="Why the client can go ahead, and any measures taken."
								/>
								<Button type="submit" size="sm" disabled={allowing}>
									{allowing ? 'Allowing…' : 'Allow journey to continue'}
								</Button>
							</form>
						{/if}
					</AlertDescription>
				</Alert>
			{/if}

			<div
				class="border-border flex flex-wrap items-center justify-between gap-3 border-t {compact
					? 'pt-3'
					: 'pt-4'}"
			>
				<!-- A finished screen that was started monitored is covered. A technical failure never
				     ran, so this card does not claim ComplyCube is re-screening it, and does not offer
				     Turn on — the action on a failed screen is Run it again. -->
				{#if check.status !== 'failed'}
					{#if monitoringCovers(check)}
						<p class="flex items-center gap-2 text-xs">
							<RadarIcon class="text-success size-4 shrink-0" aria-hidden="true" />
							<span>
								<span class="font-medium">Ongoing monitoring is on.</span>
								<span class="text-muted-foreground">
									ComplyCube re-screens them and raises a refer on a new match.
								</span>
							</span>
						</p>
					{:else}
						<div class="flex flex-wrap items-center gap-2 text-xs">
							<RadarIcon class="text-warning size-4 shrink-0" aria-hidden="true" />
							<span class="font-medium">Ongoing monitoring is off.</span>
							{#if canWrite}
								<form
									method="POST"
									action="?/monitoring"
									use:enhance={submit('Monitoring turned on.', (b) => (monitorPending = b))}
								>
									<input type="hidden" name="check" value={check.uid} />
									<input type="hidden" name="enabled" value="on" />
									<Button type="submit" variant="outline" size="sm" disabled={monitorPending}>
										{monitorPending ? 'Turning on…' : 'Turn on'}
									</Button>
								</form>
							{/if}
						</div>
					{/if}
				{/if}
				<CheckHistory history={detail.history} />
			</div>
		{/if}
	</CardContent>
</Card>
