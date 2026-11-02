<script lang="ts">
	// One identity check as one card: the photos we hold across the top, ComplyCube's scores in a row
	// under them, then its reasons (its analysis breakdown: what passed, what needs attention), with
	// Graphene's decision along the bottom. Sized to sit beside the AML screen, and laid out by its own
	// width (container queries), so it also reads well when it has the page to itself. A photo opens
	// in a pop-up over the page; the acts open the page's dialogs. `compact` is for a page that has to
	// fit one screen (the workbench): smaller photos and type, and, when the card has the width, the
	// photos with the decision under them beside ComplyCube's scores and reasons.
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
		IdentityAnalysisView,
		IdentityUploadButton,
		IdentityPhotoGrid,
		ImageViewer,
		StatusBadge,
		type IdentityUpload,
		type StatusTone,
		type IdentityPhoto,
		humanise
	} from '@graphene/ui';
	import CameraIcon from '@lucide/svelte/icons/camera';
	import ChevronLeftIcon from '@lucide/svelte/icons/chevron-left';
	import ChevronRightIcon from '@lucide/svelte/icons/chevron-right';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import CircleXIcon from '@lucide/svelte/icons/circle-x';
	import FileDownIcon from '@lucide/svelte/icons/file-down';
	import LoaderCircleIcon from '@lucide/svelte/icons/loader-circle';
	import ScanFaceIcon from '@lucide/svelte/icons/scan-face';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import {
		ORIGIN_LABEL,
		canRequestPhotos,
		checkDecisions,
		checkReportHref,
		dateTime,
		imagesMissingText,
		photosRequested,
		statusOf,
		uploadedByLine,
		type AmlCheckDetail,
		type IdentityImage,
		type IdentitySteps
	} from '$lib/aml';
	import CheckHistory from './check-history.svelte';
	import DecisionBox from './decision-box.svelte';
	import PendingPanel from './pending-panel.svelte';

	let {
		detail,
		title,
		canAdjudicate,
		upload = null,
		steps = null,
		compact = false,
		class: className = '',
		bodyClass = '',
		ondecide,
		onrequestphotos,
		onupload
	}: {
		detail: AmlCheckDetail;
		title: string;
		canAdjudicate: boolean;
		/** Whether new photos can be uploaded for the client, and whether a journey step waits on
		 *  them. Given on their latest identity check only: an earlier check never offers an
		 *  upload. */
		upload?: IdentityUpload | null;
		/** Through the client's identity tries, when they have more than one: where the older and
		 *  the newer try open (null at either end), and which try of how many this is, oldest 1. */
		steps?: IdentitySteps | null;
		/** Smaller photos and type, the photos and the reasons side by side when the card has the
		 *  width: for a page that has to fit one screen. */
		compact?: boolean;
		/** More classes for the card, and for its body: how a page fits the card to its height. */
		class?: string;
		bodyClass?: string;
		ondecide: (decision: 'clear' | 'rejected') => void;
		onrequestphotos: () => void;
		onupload?: () => void;
	} = $props();

	const check = $derived(detail.check);
	const status = $derived(statusOf(check.status));
	const uploadedBy = $derived(uploadedByLine(check));
	const decisions = $derived(checkDecisions(detail));
	// On the latest check, the client is asked only while a journey step waits on their identity:
	// with none, the API refuses, and an upload is the way to new photos.
	const askable = $derived(canRequestPhotos(detail) && (upload?.journey ?? true));
	const analysis = $derived(detail.identity?.analysis ?? null);
	const breakdown = $derived(detail.identity?.breakdown ?? []);
	const waiting = $derived(
		check.status === 'pending' && !analysis?.groups.length && !breakdown.length
	);
	const decision = $derived(
		photosRequested(detail)
			? { label: 'New photos requested', tone: 'info' as StatusTone }
			: check.decided_at
				? status
				: decisions.length
					? { label: 'Undecided', tone: 'warning' as StatusTone }
					: status
	);

	/** Where one photo's bytes stream from: a same-origin relay, since the object store has no
	 *  public endpoint to presign a link for. Bytes the workbench read ahead show from the page. */
	function imageSrc(image: Pick<IdentityImage, 'part' | 'src'>): string {
		return image.src ?? `/aml/checks/${check.uid}/images/${image.part}`;
	}

	/** The image the pop-up shows first, while it is open. */
	let viewing = $state<number | null>(null);
	const viewerImages = $derived<IdentityPhoto[]>(
		(detail.identity?.images ?? []).map((image) => ({
			url: imageSrc(image),
			label: image.label,
			contentType: image.content_type,
			note: image.source === 'graphene' ? 'Graphene’s copy' : null
		}))
	);
</script>

{#snippet pendingPanel()}
	<PendingPanel
		sent={check.created_at}
		title="Checking with ComplyCube"
		icon={ScanFaceIcon}
		checking={['Document', 'Face match', 'Liveness']}
		next="ComplyCube's reasons show here. Anything not clear comes to you to decide."
	/>
{/snippet}

{#snippet photos(identity: NonNullable<AmlCheckDetail['identity']>)}
	<!-- The photos, big enough to compare face to document; a click opens one large in a pop-up over
	     the page, with the check's other images a step away. Each is fetched from this app's own
	     same-origin relay, without a referrer. A PDF scan shows as a file tile: an <img> cannot draw
	     it, the pop-up can. Null photos are not listed yet: the workbench lists them now. -->
	{#if viewerImages.length}
		<IdentityPhotoGrid
			photos={viewerImages}
			size={compact ? 'compact' : 'default'}
			onopen={(i) => (viewing = i)}
		/>
	{:else if identity.images === null}
		<p class="text-muted-foreground flex items-center gap-1.5 text-xs">
			<LoaderCircleIcon class="size-3.5 animate-spin" aria-hidden="true" /> Loading the photos…
		</p>
	{:else}
		<p class="text-muted-foreground text-xs">
			{imagesMissingText(identity.images_missing)}
		</p>
	{/if}
{/snippet}

{#snippet reasons()}
	<!-- ComplyCube's scores, reasons and specimen links. While the check waits on ComplyCube, the
	     pending panel stands in for the reasons. -->
	<IdentityAnalysisView
		{analysis}
		{breakdown}
		{compact}
		empty="ComplyCube gave no breakdown."
		pending={waiting ? pendingPanel : undefined}
	/>
{/snippet}

{#snippet decide()}
	<DecisionBox label={decision.label} tone={decision.tone}>
		{#if check.decided_at}
			<div class="text-xs">
				<span class="text-muted-foreground">
					{check.decided_by ?? 'Graphene'} · {dateTime(check.decided_at)}
				</span>
				{#if check.decision_comment}<p class="mt-0.5 text-sm">{check.decision_comment}</p>{/if}
			</div>
		{/if}
		{#if canAdjudicate && (decisions.length || askable)}
			<div class="flex flex-wrap gap-2">
				{#if decisions.includes('clear')}
					<Button size="sm" onclick={() => ondecide('clear')}>
						<CircleCheckIcon class="size-4" /> Clear
					</Button>
				{/if}
				{#if decisions.includes('rejected')}
					<Button variant="outline" size="sm" onclick={() => ondecide('rejected')}>
						<CircleXIcon class="size-4" /> Reject
					</Button>
				{/if}
				{#if askable}
					<Button variant="outline" size="sm" onclick={onrequestphotos}>
						<CameraIcon class="size-4" /> Ask for new photos
					</Button>
				{/if}
				{#if canAdjudicate && upload && onupload}
					<IdentityUploadButton {upload} onclick={onupload} />
				{/if}
			</div>
		{:else}
			{#if check.status === 'pending'}
				<p class="text-muted-foreground text-xs">Waiting on ComplyCube.</p>
			{:else if !check.decided_at && (check.status === 'clear' || check.status === 'rejected')}
				<p class="text-muted-foreground text-xs">ComplyCube's own result.</p>
			{/if}
			{#if canAdjudicate && upload && onupload}
				<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
					<IdentityUploadButton {upload} onclick={onupload} />
				</div>
			{/if}
		{/if}
	</DecisionBox>
{/snippet}

<Card glass size={compact ? 'sm' : 'default'} class={className}>
	<!-- The title, the try, its status and its report on one row; the run line below it, full
	     width, so a long check id never pushes the status onto a row of its own. -->
	<CardHeader class="border-border border-b">
		<div class="flex flex-wrap items-center gap-2">
			<CardTitle class="flex items-center gap-2 text-base">
				<ScanFaceIcon class="text-muted-foreground size-4" />
				{title}
			</CardTitle>
			<!-- Through the client's tries: older to the left, newer to the right. -->
			{#if steps}
				<nav class="flex items-center text-xs" aria-label="Identity checks">
					<Button
						variant="ghost"
						size="icon-sm"
						href={steps.older ?? undefined}
						disabled={!steps.older}
						aria-label="Older check"
						data-sveltekit-noscroll
						data-sveltekit-replacestate
					>
						<ChevronLeftIcon />
					</Button>
					<span class="text-muted-foreground tabular-nums">
						Check {steps.position} of {steps.count}
					</span>
					<Button
						variant="ghost"
						size="icon-sm"
						href={steps.newer ?? undefined}
						disabled={!steps.newer}
						aria-label="Newer check"
						data-sveltekit-noscroll
						data-sveltekit-replacestate
					>
						<ChevronRightIcon />
					</Button>
				</nav>
			{/if}
			<span class="text-muted-foreground text-xs tabular-nums">Try {check.attempt}</span>
			<StatusBadge tone={status.tone}>{status.label}</StatusBadge>
			<DownloadLink
				href={checkReportHref(check.uid)}
				name="Identity check report"
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
		</div>
		<p class="text-muted-foreground text-xs">
			{#if detail.identity?.document_type}{humanise(detail.identity.document_type)} ·
			{/if}{ORIGIN_LABEL[check.origin]} · run {dateTime(check.created_at)}
			{#if check.provider_check_id}
				· <span class="font-mono break-all">{check.provider_check_id}</span>
			{/if}
		</p>
	</CardHeader>

	<CardContent class="@container space-y-3 text-sm {bodyClass}">
		{#if uploadedBy}
			<div class="text-xs">
				<span class="text-muted-foreground">{uploadedBy}</span>
				{#if check.upload_comment}
					<p class="mt-0.5 text-sm whitespace-pre-line">{check.upload_comment}</p>
				{/if}
			</div>
		{/if}

		{#if check.status === 'failed' && !check.decided_at}
			<Alert variant="warning">
				<TriangleAlertIcon class="size-4" />
				<AlertTitle>The identity check did not run</AlertTitle>
				<AlertDescription>
					ComplyCube could not check these images. Reject the check to ask for new ones.
				</AlertDescription>
			</Alert>
		{/if}

		{#if detail.identity && compact}
			<!-- The photos with the decision under them, ComplyCube's reasons beside them; one column,
			     in the order below, when the card is narrow. -->
			<div
				class="grid grid-cols-1 items-start gap-3 @4xl:grid-cols-2 @4xl:grid-rows-[auto_1fr] @4xl:gap-x-6"
			>
				<div class="min-w-0">{@render photos(detail.identity)}</div>
				<div class="min-w-0 @4xl:col-start-2 @4xl:row-span-2 @4xl:row-start-1">
					{@render reasons()}
				</div>
				<div class="min-w-0 space-y-3 @4xl:col-start-1 @4xl:row-start-2">
					{@render decide()}
					<CheckHistory history={detail.history} />
				</div>
			</div>
		{:else}
			{#if !detail.identity}
				<p class="text-muted-foreground">ComplyCube has not sent a result yet.</p>
			{:else}
				{@render photos(detail.identity)}
				{@render reasons()}
			{/if}

			{@render decide()}

			<CheckHistory history={detail.history} />
		{/if}
	</CardContent>
</Card>

{#if viewing !== null && viewerImages.length}
	<ImageViewer images={viewerImages} index={viewing} onclose={() => (viewing = null)} />
{/if}
