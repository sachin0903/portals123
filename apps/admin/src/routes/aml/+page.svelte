<script lang="ts">
	// The operations workbench: every client that needs a person, one profile at a time, the longest
	// wait first. Any operator can work any profile. ←/→ move through the queue, which loops at
	// either end, and S skips a profile for the session. The open step expands inline with the client page's own sections and
	// acts, and a profile that leaves the queue gives way to the one that took its place.
	//
	// What it shows, the firm and the profile on screen (`?at=`) live in the URL. Only what it shows
	// and the firm reload the queue: a move marks `at` by shallow routing and reads the profile here,
	// at once when it was read ahead, photos and all. The skips live in the tab's session storage:
	// they are the operator's own, for today.
	import { onDestroy, onMount, tick, untrack } from 'svelte';
	import { afterNavigate, goto, invalidateAll, pushState, replaceState } from '$app/navigation';
	import { navigating } from '$app/state';
	import {
		Badge,
		Button,
		Card,
		CardContent,
		CardHeader,
		CardTitle,
		EmptyState,
		LoadErrorBanner,
		PropertyRow,
		REFRESH_WHILE,
		RefreshWhile,
		StatusBadge,
		Stepper,
		countryName,
		type StatusTone
	} from '@graphene/ui';
	import { ALL, FilterSelect } from '@graphene/ui/book/listing';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import ArrowUpRightIcon from '@lucide/svelte/icons/arrow-up-right';
	import CheckIcon from '@lucide/svelte/icons/check';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import ClipboardCheckIcon from '@lucide/svelte/icons/clipboard-check';
	import ScanFaceIcon from '@lucide/svelte/icons/scan-face';
	import {
		AGE_TONE,
		CLIENT_TYPE_LABEL,
		ORIGIN_LABEL,
		SCREEN_STARTING,
		SHOW_OPTIONS,
		STATUS_LABEL,
		ageText,
		atPlace,
		clientHref,
		heldOnConfirmedMatch,
		neighbour,
		nextProfile,
		parseWorkFilter,
		photoListHref,
		photosRequested,
		PHOTOS_UNAVAILABLE,
		profileCache,
		profileDataHref,
		readJson,
		readPhotoSet,
		recordPhotosViewed,
		referDecided,
		riskOf,
		shortDate,
		showCount,
		statusOf,
		stepToShow,
		undecidedMatches,
		unlistedPhotos,
		withPhotos,
		workBreakdown,
		workHref,
		type AmlCheckDetail,
		type AmlClientPage,
		type AmlShow,
		type AmlStepKey,
		type AmlWorkQuery,
		type AmlWorkStep,
		type CachedProfile,
		type PhotoListing
	} from '$lib/aml';
	import AmlSection from '$lib/compliance/aml-section.svelte';
	import ConsoleTabs from '$lib/compliance/console-tabs.svelte';
	import FirmBadge from '$lib/compliance/firm-badge.svelte';
	import IdentityDecisionDialog, {
		type CheckDecision
	} from '$lib/compliance/identity-decision-dialog.svelte';
	import IdentitySection from '$lib/compliance/identity-section.svelte';
	import RequestPhotosDialog from '$lib/compliance/request-photos-dialog.svelte';
	import RiskSummary from '$lib/compliance/risk-summary.svelte';
	import type { ActionData, PageData } from './$types';

	let { data, form }: { data: PageData; form: ActionData } = $props();

	// ── The queue and the profile on screen ──────────────────────────────────────────────────────

	/** The profile on screen. The first render's comes with the page; the page reads each after. */
	let page = $state.raw<AmlClientPage | null>(untrack(() => data.client));
	/** Why the profile could not be read, in its place. */
	let clientError = $state<string | null>(untrack(() => data.clientError));
	/** The profile moved to: the one on screen, or the one being read. */
	let target = $state<string | null>(
		untrack(() => data.client?.key ?? (data.query.at || data.items[0]?.key || null))
	);
	/** A move waits on a read: the profile on screen dims until it arrives. */
	let reading = $state(false);
	/** Counts each profile put on screen from a read, so a step picked on it lasts that read only. */
	let loads = $state(0);

	const keys = $derived(data.items.map((item) => item.key));
	const index = $derived(page ? keys.indexOf(page.key) : -1);

	function stepsOf(shown: AmlClientPage): AmlWorkStep[] {
		return shown.steps ?? data.items.find((item) => item.key === shown.key)?.steps ?? [];
	}

	const item = $derived(data.items.find((i) => i.key === page?.key) ?? null);
	const steps = $derived(page ? stepsOf(page) : []);
	const isOpen = $derived(steps.some((step) => step.state === 'open'));
	const doneSteps = $derived(steps.filter((step) => step.state === 'done'));
	const age = $derived(page?.age ?? item?.age ?? null);
	const type = $derived(page?.client.type ?? item?.type ?? null);
	const reasons = $derived(page?.reasons ?? item?.reasons ?? []);

	/** The step the operator picked on the stepper, for this load of the profile only: a reload
	 *  after a decision opens on the next open step. */
	let picked = $state.raw<{ load: number; step: AmlStepKey } | null>(null);
	const active = $derived(
		picked && picked.load === loads ? picked.step : stepToShow(steps, data.query.show)
	);

	function select(step: string) {
		if (page) picked = { load: loads, step: step as AmlStepKey };
	}

	// ── Skipped this session ─────────────────────────────────────────────────────────────────────

	const SKIPPED = 'aml-work:skipped';
	let skipped = $state<string[]>([]);
	const skippedHere = $derived(keys.filter((key) => skipped.includes(key)).length);

	function saveSkips() {
		try {
			sessionStorage.setItem(SKIPPED, JSON.stringify(skipped));
		} catch {
			/* no storage: the skips last as long as the page */
		}
	}

	function clearSkips() {
		skipped = [];
		saveSkips();
	}

	// Declared before `afterNavigate` below, so the skips are read before the first profile settles.
	onMount(() => {
		try {
			const kept: unknown = JSON.parse(sessionStorage.getItem(SKIPPED) ?? '[]');
			if (Array.isArray(kept)) skipped = kept.filter((key) => typeof key === 'string');
		} catch {
			/* no storage, or nothing readable in it: no skips */
		}
	});

	// ── Moving through the queue ─────────────────────────────────────────────────────────────────

	/** The index of the profile on screen, kept for when it leaves the queue. */
	let place = $state(0);
	/** The last profile seen in the queue: one that drops out of it on a reload has just left. */
	let lastInQueue: string | null = null;
	/** The latest read for the screen: an earlier one that arrives after it is dropped. */
	let ticket = 0;

	/** The profiles read ahead: each body without its photos listed, then its photos' listing and
	 *  bytes, none of it recorded. */
	const cache = profileCache(
		(key) => readJson<AmlClientPage>(fetch, profileDataHref(key, false)),
		(uid) => readPhotoSet(fetch, uid)
	);

	/** The URL names the profile on screen, by shallow routing (no load): a move is a history entry
	 *  of its own, so Back returns to the profile before; a profile the workbench chose replaces the
	 *  entry. */
	function mark(key: string, entry: 'push' | 'replace') {
		const href = workHref({ show: data.query.show, tenant: data.query.tenant, at: key });
		if (entry === 'push') pushState(href, {});
		else replaceState(href, {});
	}

	/** Show the profile `key`: at once when it was read ahead, else once it is read. `entry` marks
	 *  the move in the URL; Back and Forward have already moved it. */
	async function open(key: string, entry: 'push' | 'replace' | null) {
		if (entry) mark(key, entry);
		target = key;
		const mine = ++ticket;
		const held = cache.peek(key);
		reading = !held;
		if (held) return put(held.body, held);
		const answer = await cache.load(key);
		if (mine !== ticket) return;
		reading = false;
		if (answer.ok) put(answer.body.body, answer.body);
		else fail(key, answer);
	}

	/** Read the profile on screen again, with its photos: after an act. */
	async function refresh(key: string) {
		const mine = ++ticket;
		reading = false;
		const answer = await readJson<AmlClientPage>(fetch, profileDataHref(key, true));
		if (mine !== ticket) return;
		if (answer.ok) put(answer.body);
		else fail(key, answer);
	}

	/** A read failed. A key the console no longer knows gives way to the queue's first. */
	function fail(key: string, answer: { status: number; error: string }) {
		const first = firstProfile();
		if (answer.status === 404 && first && first !== key) return void open(first, 'replace');
		page = null;
		clientError = answer.status === 404 ? 'Client not found.' : answer.error;
	}

	/** Put a profile on screen, with any of its photos already read ahead. */
	function put(body: AmlClientPage, ahead: CachedProfile | null = null) {
		listed = new Set();
		shownAhead = ahead;
		let shown = body;
		for (const [uid, listing] of ahead?.ready ?? []) {
			shown = showListing(shown, uid, listing);
			listed.add(uid);
		}
		page = shown;
		clientError = null;
		target = body.key;
		loads += 1;
		arrived(body);
	}

	/** Note the profile's place in the queue, read the profiles either side of it ahead, and let
	 *  go of the rest: the cache keeps the profile on screen and its two neighbours. */
	function arrived(body: AmlClientPage) {
		const at = keys.indexOf(body.key);
		if (at >= 0) {
			place = at;
			lastInQueue = body.key;
		}
		const around = [
			nextProfile(keys, body.key, place, skipped),
			neighbour(keys, body.key, -1, skipped)
		];
		cache.retain([shownAhead?.key ?? body.key, body.key, ...around]);
		cache.preload(around);
	}

	/** Where a queue opens: its first profile not skipped, else its first. */
	function firstProfile(): string | null {
		return atPlace(keys, 0, skipped) ?? keys[0] ?? null;
	}

	function openFirst() {
		const first = firstProfile();
		if (first) return void open(first, 'replace');
		ticket += 1;
		cache.retain([]);
		target = null;
		page = null;
		clientError = null;
		reading = false;
	}

	function retry() {
		if (target) void open(target, null);
		else openFirst();
	}

	function move(step: 1 | -1) {
		const next = neighbour(keys, target ?? page?.key ?? null, step, skipped);
		if (next) void open(next, 'push');
	}

	/** The profile after this one: the next in the queue, which loops round to the first. For a
	 *  profile that left the queue, the one that took its place. */
	const upNext = $derived(nextProfile(keys, page?.key ?? null, place, skipped));

	function next() {
		if (upNext) void open(upNext, 'push');
	}

	/** Skip the profile for the session and move on. */
	function skip() {
		const shown = page;
		if (!shown) return;
		const passed = [...skipped, shown.key];
		const to = neighbour(keys, shown.key, 1, passed);
		if (!skipped.includes(shown.key)) {
			skipped = passed;
			saveSkips();
		}
		if (to) void open(to, 'push');
	}

	/** The queue was read again after an act: the profile on screen is read again with it, and one
	 *  that left the queue gives way to the one that took its place. */
	function reload() {
		const key = target;
		if (!key) return openFirst();
		if (!keys.includes(key) && lastInQueue === key) {
			// By place, never by the URL's `at`, which still names the one that left.
			lastInQueue = null;
			const replacement = atPlace(keys, place, skipped);
			if (replacement) return void open(replacement, 'replace');
		}
		void refresh(key);
	}

	// A new queue (a new "Show" or firm, an act) makes every profile read ahead stale. A navigation
	// picks its profile once it lands (below); an act reloads the queue in place.
	let seen = untrack(() => data);
	$effect(() => {
		const next = data;
		untrack(() => {
			if (next === seen) return;
			seen = next;
			cache.clear();
			if (!navigating.to) reload();
		});
	});

	afterNavigate(async ({ type }) => {
		if (type === 'enter') {
			// The first profile came with the page. The router starts just after this first call, and
			// the URL can be marked only then.
			await tick();
			if (!page) return;
			arrived(page);
			// The loader opens the queue's first profile when the URL names none; it cannot know what
			// was skipped.
			const unskipped =
				!data.query.at && skipped.includes(page.key) ? neighbour(keys, page.key, 1, skipped) : null;
			if (unskipped) void open(unskipped, 'replace');
			else mark(page.key, 'replace');
			return;
		}
		const at = new URL(location.href).searchParams.get('at')?.trim();
		if (at) void open(at, null);
		else openFirst();
	});

	/** Back or Forward between this queue's profiles: shallow entries, which the router leaves to
	 *  the page. Back to another queue or page is a navigation, and `afterNavigate` takes it. */
	function onPopstate() {
		const url = new URL(location.href);
		const { show, tenant } = parseWorkFilter(url.searchParams);
		if (url.pathname !== '/aml' || show !== data.query.show || tenant !== data.query.tenant) return;
		const at = url.searchParams.get('at')?.trim();
		if (at) void open(at, null);
		else openFirst();
	}

	// ── The photos ───────────────────────────────────────────────────────────────────────────────

	// Listing a check's photos is recorded as a view. A profile read ahead has its photos listed
	// without one and their bytes kept as object URLs, so they show at once; the view is recorded
	// when they do (`/viewed`), once each time the profile comes on screen. A profile with no photos
	// read ahead lists them on screen, which records the view, and each tile loads its own bytes.

	/** The checks whose photos were shown or asked for since the profile came on screen. */
	let listed = new Set<string>();
	/** The profile on screen as it was read ahead, while its photos may still be arriving. */
	let shownAhead: CachedProfile | null = null;
	// Leaving the page revokes every object URL the cache made.
	onDestroy(() => cache.dispose());

	/** Show one check's photos read ahead, and record that they were shown. */
	function showListing(shown: AmlClientPage, uid: string, listing: PhotoListing): AmlClientPage {
		void recordPhotosViewed(fetch, uid, listing.images.length);
		return withPhotos(shown, uid, listing);
	}

	$effect(() => {
		const shown = page;
		if (!shown) return;
		untrack(() => {
			for (const uid of unlistedPhotos(shown)) {
				if (listed.has(uid)) continue;
				listed.add(uid);
				void showPhotos(shown.key, uid);
			}
		});
	});

	/** Whether the profile on screen still waits on that check's photos. */
	function waiting(key: string, uid: string): boolean {
		return page?.key === key && unlistedPhotos(page).includes(uid);
	}

	async function showPhotos(key: string, uid: string) {
		const ahead = shownAhead?.body.key === key ? shownAhead.photos.get(uid) : undefined;
		const listing = ahead ? await ahead : null;
		if (!waiting(key, uid)) return;
		if (listing) {
			page = showListing(page as AmlClientPage, uid, listing);
			return;
		}
		const answer = await readJson<PhotoListing>(fetch, photoListHref(uid));
		if (!waiting(key, uid)) return;
		page = withPhotos(page as AmlClientPage, uid, answer.ok ? answer.body : PHOTOS_UNAVAILABLE);
	}

	// ── Identity decisions ───────────────────────────────────────────────────────────────────────

	let checkTarget = $state<{ check: AmlCheckDetail; decision: CheckDecision } | null>(null);
	let photoTarget = $state<AmlCheckDetail | null>(null);

	type FormState = { action?: string; error?: string };
	const f = $derived((form ?? {}) as unknown as FormState);

	/** ←/→ and S anywhere on the page, unless the operator is typing or a menu or dialog is open. */
	function onWindowKey(event: KeyboardEvent) {
		if (event.metaKey || event.ctrlKey || event.altKey) return;
		if (checkTarget || photoTarget) return;
		const typing = event.target as HTMLElement | null;
		if (
			typing?.closest(
				'input, textarea, select, [contenteditable], [role="listbox"], [role="dialog"], [role="menu"]'
			)
		)
			return;
		if (event.key === 'ArrowRight') {
			event.preventDefault();
			move(1);
		} else if (event.key === 'ArrowLeft') {
			event.preventDefault();
			move(-1);
		} else if (event.key === 's' || event.key === 'S') {
			skip();
		}
	}

	// ── The filters ──────────────────────────────────────────────────────────────────────────────

	/** A new "Show" or firm opens its queue from the front. */
	function narrow(extra: Partial<AmlWorkQuery>) {
		goto(workHref({ show: data.query.show, tenant: data.query.tenant, ...extra }));
	}

	function showLabel(show: AmlShow): string {
		const label = SHOW_OPTIONS[show].label;
		return data.summary ? `${label} (${showCount(show, data.summary)})` : label;
	}

	const showOptions = $derived(
		(Object.keys(SHOW_OPTIONS) as AmlShow[])
			.filter((show) => show !== 'all')
			.map((show) => ({ value: show, label: showLabel(show) }))
	);
	const firmOptions = $derived(data.firms.map((firm) => ({ value: firm.uid, label: firm.name })));
	const narrowed = $derived(data.query.show !== 'all' || !!data.query.tenant);
	const breakdown = $derived(data.summary ? workBreakdown(data.summary) : []);

	// ── The profile's facts ──────────────────────────────────────────────────────────────────────

	type Named = { label: string; tone: StatusTone };

	function screenPill(shown: AmlClientPage): Named {
		if (shown.awaiting_screen)
			return shown.screen_queued ? SCREEN_STARTING : STATUS_LABEL.awaiting_screen;
		if (!shown.aml) return { label: 'Not screened', tone: 'neutral' };
		if (heldOnConfirmedMatch(shown.aml)) return { label: 'Held', tone: 'warning' };
		if (shown.aml.check.status === 'refer' && shown.aml.check.decided_at)
			return referDecided(shown.aml.check.origin);
		return statusOf(shown.aml.check.status);
	}

	function identityPill(shown: AmlClientPage): Named {
		if (!shown.identity) return { label: 'Not started', tone: 'neutral' };
		if (photosRequested(shown.identity)) return { label: 'New photos requested', tone: 'info' };
		return statusOf(shown.identity.check.status);
	}

	type Fact = { label: string; text?: string; pill?: Named };

	/** What the operator weighs a profile on, each fact only where the console holds it. */
	const facts = $derived.by((): Fact[] => {
		if (!page) return [];
		const client = page.client;
		const screened = [
			page.aml?.check.created_at,
			...page.earlier.filter((c) => c.kind === 'aml').map((c) => c.created_at)
		]
			.filter((at): at is string => !!at)
			.sort();
		const alerts = reasons.find((r) => r.code === 'monitoring_alert')?.count ?? 0;
		const country = client.country ?? client.nationality;
		const list: (Fact | null)[] = [
			client.dob ? { label: 'Date of birth', text: shortDate(client.dob) } : null,
			country ? { label: 'Country', text: countryName(country) } : null,
			{ label: 'Risk', pill: riskOf(page.risk?.overall ?? null) },
			{ label: 'Identity', pill: identityPill(page) },
			{ label: 'Screen', pill: screenPill(page) },
			client.address ? { label: 'Address', text: client.address } : null,
			{
				label: 'Screens run',
				text: screened.length
					? `${screened.length} · last ${shortDate(screened.at(-1) ?? null)}`
					: 'None yet'
			},
			page.aml?.matches.length
				? {
						label: 'Undecided hits',
						text: `${undecidedMatches(page.aml)} of ${page.aml.matches.length}`
					}
				: null,
			page.reasons ? { label: 'Open alerts', text: alerts ? String(alerts) : 'None' } : null
		];
		return list.filter((fact): fact is Fact => fact !== null);
	});

	const KBD = 'bg-muted rounded border px-1';

	/** The identity and risk steps fit one screen from lg up (below). The AML screen's steps do not:
	 *  the page flows and scrolls, the card as tall as its hits. */
	const fits = $derived(active === 'identity' || active === 'risk');
	const ONE_SCREEN = 'lg:h-[calc(100svh-var(--topbar-h,4rem))] lg:overflow-y-auto';

	/** The open step's card from lg up: it takes the height the page has left (never less than
	 *  18rem) and its body scrolls inside it, the card's header staying put. */
	const STEP_CARD = 'lg:min-h-72 lg:flex-1';
	const STEP_BODY = 'lg:min-h-0 lg:flex-1 lg:overflow-y-auto';
</script>

<svelte:window onkeydown={onWindowKey} onpopstate={onPopstate} />

<svelte:head><title>Workbench · AML and identity · Operations</title></svelte:head>

<!-- A check that comes back has to appear in the queue without a refresh. The page is the queue,
     so it keeps asking while it is open, including while it is empty. A decision dialog holds
     the poll so the form under it is not replaced. -->
<RefreshWhile
	active={!checkTarget && !photoTarget}
	interval={REFRESH_WHILE.hitsQueue}
	onpoll={() => invalidateAll()}
/>

<!-- One screen from lg up for the identity and risk steps: the page fills the inset below the topbar
     (the shell exposes --topbar-h) and never scrolls; the open step's card takes the height left and
     scrolls inside itself, so the profile above it and the decision in it stay in view. The negative
     margin cancels the shell's content padding so the fixed-height box fills the area exactly, then
     re-adds its own padding inside. Only when even the step card's least height does not fit does
     the box scroll. Below lg, and for the AML screen's steps, the page flows as any other. -->
<div class="-m-4 flex min-w-0 flex-col p-4 md:-m-6 md:p-6 {fits ? ONE_SCREEN : ''}">
	<ConsoleTabs current="work" work={data.summary?.total ?? null} tenant={data.query.tenant} />

	<div class="flex min-h-0 flex-1 flex-col gap-3">
		{#if data.workError}
			<LoadErrorBanner message={data.workError} onRetry={() => invalidateAll()} />
		{/if}

		<div class="flex flex-wrap items-center gap-2">
			<FilterSelect
				label="Show"
				value={data.query.show === 'all' ? ALL : data.query.show}
				allLabel={showLabel('all')}
				options={showOptions}
				onchange={(next) => narrow({ show: next === ALL ? 'all' : (next as AmlShow) })}
				class="h-9 min-w-[14rem]"
			/>
			<FilterSelect
				label="Firm"
				value={data.query.tenant || ALL}
				allLabel="All firms"
				options={firmOptions}
				searchable={firmOptions.length > 8}
				onchange={(next) => narrow({ tenant: next === ALL ? '' : next })}
			/>
			<span class="ml-auto flex items-center gap-1.5 text-sm font-medium tabular-nums">
				{data.total} to clear
				{#if data.summary?.overdue}
					<span class="text-muted-foreground" aria-hidden="true">·</span>
					<StatusBadge tone={AGE_TONE.overdue}>{data.summary.overdue} overdue</StatusBadge>
				{/if}
			</span>
			{#if breakdown.length}
				<!-- How the work splits, each count a way into its own "Show" choice. -->
				<span class="text-muted-foreground flex items-center text-xs">
					{#each breakdown as part, i (part.show)}
						{#if i}<span aria-hidden="true">·</span>{/if}
						<Button
							variant="link"
							size="xs"
							class="px-1 tabular-nums"
							href={workHref({ show: part.show, tenant: data.query.tenant })}
							aria-current={data.query.show === part.show ? 'page' : undefined}
						>
							{part.label}
						</Button>
					{/each}
				</span>
			{/if}
			<span class="text-muted-foreground hidden text-xs sm:inline">
				<kbd class={KBD}>←</kbd>/<kbd class={KBD}>→</kbd> to move · <kbd class={KBD}>S</kbd> to skip
			</span>
		</div>

		{#if data.truncated || skippedHere}
			<p class="text-muted-foreground flex flex-wrap items-center gap-x-3 text-xs">
				{#if data.truncated}
					<span>The {data.items.length} longest waits of {data.total} are here.</span>
				{/if}
				{#if skippedHere}
					<span>
						{skippedHere} skipped this session ·
						<button type="button" class="hover:text-foreground underline" onclick={clearSkips}>
							Show them again
						</button>
					</span>
				{/if}
			</p>
		{/if}

		{#if clientError}
			<LoadErrorBanner message={clientError} onRetry={retry} />
		{/if}

		{#if page}
			<Card
				glass
				size="sm"
				class="transition-opacity {reading || navigating.to ? 'opacity-60' : ''}"
			>
				<CardHeader class="border-border border-b">
					<!-- The client, the button and the steps on one row when the card has the width (the
					     steps between the two); else the steps under them, a row of their own. -->
					<div
						class="flex flex-wrap items-start justify-between gap-3 @5xl/card-header:grid @5xl/card-header:grid-cols-[minmax(0,1fr)_minmax(24rem,1.25fr)_auto] @5xl/card-header:items-center @5xl/card-header:gap-x-6"
					>
						<div class="min-w-0 space-y-1">
							<h2 class="flex min-w-0 flex-wrap items-center gap-2">
								<span class="truncate text-lg font-semibold">
									{page.client.name || 'Unnamed client'}
								</span>
								{#if type}<Badge variant="outline">{CLIENT_TYPE_LABEL[type]}</Badge>{/if}
								{#if age}
									<StatusBadge tone={AGE_TONE[age.band]}>{ageText(age)}</StatusBadge>
								{/if}
							</h2>
							<p class="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-sm">
								<FirmBadge name={page.client.tenant_name} iconUrl={page.client.tenant_icon_url} />
								<span class="text-muted-foreground text-xs">
									· {ORIGIN_LABEL[page.client.origin]} client
									{#if reasons.length}· {reasons.map((r) => r.label).join(', ')}{/if}
									{#if index >= 0}· {index + 1} of {keys.length}{/if}
								</span>
							</p>
						</div>
						<Button
							variant="outline"
							size="sm"
							href={clientHref(page.key)}
							target="_blank"
							rel="noopener"
							class="@5xl/card-header:col-start-3 @5xl/card-header:row-start-1"
						>
							Open full profile <ArrowUpRightIcon class="size-4" />
						</Button>
						<Stepper
							{steps}
							{active}
							onselect={select}
							class="w-full min-w-0 @5xl/card-header:col-start-2 @5xl/card-header:row-start-1 @5xl/card-header:w-auto"
						/>
					</div>
				</CardHeader>
				{#if facts.length}
					<CardContent>
						<!-- As many columns as fit, so the facts take as few rows as they can. -->
						<div class="grid grid-cols-[repeat(auto-fill,minmax(min(16rem,100%),1fr))] gap-x-6">
							{#each facts as fact (fact.label)}
								<PropertyRow label={fact.label} class="py-0.5">
									{#if fact.pill}
										<span><StatusBadge tone={fact.pill.tone}>{fact.pill.label}</StatusBadge></span>
									{:else}
										<span class="truncate text-sm" title={fact.text}>{fact.text}</span>
									{/if}
								</PropertyRow>
							{/each}
						</div>
					</CardContent>
				{/if}
			</Card>

			{#if !isOpen}
				<!-- Nothing on the profile waits on a person any more: a colleague finished it, or it was
				     opened by its key. -->
				<Card glass>
					<CardContent class="flex flex-wrap items-center justify-between gap-3">
						<p class="flex items-center gap-2 text-sm">
							<CircleCheckIcon class="text-success size-5 shrink-0" aria-hidden="true" />
							<span>
								<span class="font-medium">Complete.</span>
								<span class="text-muted-foreground">Nothing on this profile waits on a person.</span
								>
							</span>
						</p>
						{#if upNext}
							<Button size="sm" onclick={next}>
								Next profile <ArrowRightIcon class="size-4" />
							</Button>
						{:else}
							<span class="text-muted-foreground text-sm">Nothing else to clear here.</span>
						{/if}
					</CardContent>
				</Card>
			{/if}

			{#if active === 'identity'}
				{#if page.identity}
					{@const identity = page.identity}
					<IdentitySection
						detail={identity}
						title="Identity"
						canAdjudicate={data.canAdjudicate}
						upload={page.identity_upload ?? null}
						compact
						class={STEP_CARD}
						bodyClass={STEP_BODY}
						ondecide={(decision) => (checkTarget = { check: identity, decision })}
						onrequestphotos={() => (photoTarget = identity)}
					/>
				{:else}
					<Card glass size="sm" class={STEP_CARD}>
						<CardHeader class="border-border border-b">
							<CardTitle class="flex items-center gap-2 text-base">
								<ScanFaceIcon class="text-muted-foreground size-4" /> Identity
							</CardTitle>
						</CardHeader>
						<CardContent class={STEP_BODY}>
							<EmptyState
								compact
								icon={ScanFaceIcon}
								title="No identity check"
								description="The client has not taken an identity check with Graphene."
							/>
						</CardContent>
					</Card>
				{/if}
			{:else if active === 'risk'}
				<Card glass size="sm" class={STEP_CARD}>
					<CardContent class="text-sm {STEP_BODY}">
						<RiskSummary risk={page.risk} />
					</CardContent>
				</Card>
			{:else if active}
				<!-- No inner scroll: the card grows with its hits, and the page scrolls. -->
				<AmlSection
					detail={page.aml}
					title="AML screen"
					awaiting={page.awaiting_screen}
					queued={!!page.screen_queued}
					stale={page.stale}
					contactUid={page.client.contact_uid}
					canWrite={data.canWrite}
					canAdjudicate={data.canAdjudicate}
					compact
				/>
			{/if}

			{#if doneSteps.length}
				<div class="flex flex-wrap items-center gap-2" role="group" aria-label="Done steps">
					{#each doneSteps as step (step.key)}
						<button
							type="button"
							title={step.hint ?? undefined}
							aria-pressed={active === step.key}
							onclick={() => select(step.key)}
							class="border-success-line bg-success-subtle text-success-strong hover:bg-success-subtle/70 focus-visible:ring-ring/50 inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 text-xs font-medium outline-none focus-visible:ring-2"
						>
							<CheckIcon class="size-3" aria-hidden="true" />
							{step.label}
						</button>
					{/each}
				</div>
			{/if}
		{:else if !keys.length && !data.workError && !clientError}
			{#snippet everything()}
				<Button variant="outline" size="sm" onclick={() => goto(workHref())}>
					Show all profiles
				</Button>
			{/snippet}
			<EmptyState
				icon={ClipboardCheckIcon}
				title="Nothing to clear"
				description={narrowed
					? 'Nothing here needs a person. Widen what the workbench shows.'
					: 'Every client Graphene screens is up to date.'}
				action={narrowed ? everything : undefined}
			/>
		{/if}
	</div>
</div>

{#if checkTarget && page}
	<IdentityDecisionDialog
		target={checkTarget}
		clientName={page.client.name}
		error={f.action === 'decideCheck' ? f.error : null}
		onclose={() => (checkTarget = null)}
	/>
{/if}

{#if photoTarget && page}
	<!-- Mounted per open, like the check dialog above. -->
	<RequestPhotosDialog
		detail={photoTarget}
		clientName={page.client.name}
		error={f.action === 'requestPhotos' ? f.error : null}
		onclose={() => (photoTarget = null)}
	/>
{/if}
