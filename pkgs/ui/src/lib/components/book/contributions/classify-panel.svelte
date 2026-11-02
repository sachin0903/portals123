<script lang="ts">
	// The detail half of the workbench: classify, see the consequences, act.
	//
	// A panel rather than a drawer. A drawer covers the queue, so an adviser working through
	// twenty payments loses their place on every one; a panel keeps the list and the decision on
	// screen together. The drawer stays for "record a contribution" from an account page, where
	// there is no queue behind it to preserve.
	//
	// TWO MODES, and the difference is deliberate:
	//
	//  * **Editable** (up to `confirmed`) — a form. The gross-up recomputes live off the PENDING
	//    selection, because the point is to show what the classification will do before it is
	//    committed.
	//  * **Recorded** (past `confirmed` — the back office holds it) — a plain details view, not a
	//    form with everything greyed out. A disabled form invites an adviser to try, and reads as
	//    something broken rather than something settled. It also shows the figures AS STORED
	//    rather than recomputed: relief was calculated at a point in time, and re-deriving it from
	//    today's earnings on file would quietly contradict what was actually claimed.
	import CheckIcon from '@lucide/svelte/icons/check';
	import RefreshIcon from '@lucide/svelte/icons/refresh-cw';
	import XIcon from '@lucide/svelte/icons/x';
	import { CLASSIFIABLE, RELIEF_CLAIMED, stageOf } from './status';
	import { isDetected } from './detected';
	import {
		CONTRIBUTION_CATEGORIES,
		CREATABLE_CONTRIBUTION_CATEGORIES,
		IS_OTHER_OUTCOME,
		NEEDS_EMPLOYER,
		OTHER_CONTRIBUTION_OUTCOMES,
		PAID_BY_OPTIONS,
		UNPUSHABLE_CATEGORIES,
		paidByFor,
		type Contribution,
		type ContributionCategory,
		type ContributionLinks,
		type PaidBy
	} from './types';
	import GrossUp from './gross-up.svelte';
	import MlStatusBadge from './ml-status-badge.svelte';
	import StatusBadge from './status-badge.svelte';
	import { Alert, AlertDescription } from '../../ui/alert/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Input } from '../../ui/input/index.js';
	import { Label } from '../../ui/label/index.js';
	import { Money } from '../../ui/money/index.js';
	import Segmented from '../../ui/segmented/segmented.svelte';
	import PersonAvatar from '../../data/person-avatar.svelte';
	import { Separator } from '../../ui/separator/index.js';
	import { LINK_RECORD } from '../../../text-shell.js';
	import { formatDate } from '../../../dates.js';
	import { cn } from '../../../utils.js';

	let {
		contribution,
		busy = false,
		links,
		onsave,
		onlodge,
		oncancel,
		onrefresh,
		onclose
	}: {
		contribution: Contribution | null;
		busy?: boolean;
		/** Injected navigation — the same builder the queue takes. Absent ⇒ plain text. */
		links?: ContributionLinks;
		/** Saves the classification AND records it with the scheme administrator — one act, because
		 *  they are one decision (see the footer). */
		onsave?: (uid: string, patch: Record<string, unknown>) => void;
		/** Retry the SEI payment expectation for a contribution booked ahead. A READ-side repair,
		 *  not a step in the lifecycle — it is offered inside the "we never told the custodian"
		 *  alert and nowhere else. */
		onlodge?: (uid: string) => void;
		/** Stops the contribution, recording why. The reason is required by the DB CHECK as well
		 *  as by good sense — a cancelled HMRC-reportable record with no reason is not a trail. */
		oncancel?: (uid: string, reason: string) => void;
		/** Re-read `cam_status` from Morgan Lloyd. This is what moves a contribution through
		 *  Application → Active → Received: those transitions happen at the scheme administrator
		 *  and at HMRC, so the only honest way to learn them is to ask. */
		onrefresh?: (uid: string) => void;
		/** Closes the panel. The workbench opens it only when a row is picked, so the header carries
		 *  the way back out; absent, there is no X. */
		onclose?: () => void;
	} = $props();

	/** The cancel confirm. Inline rather than a dialog: the reason has to be typed, and a
	 *  one-field dialog over a panel that is already the detail view is a layer for nothing.
	 *
	 *  Keyed on the uid rather than reset by an effect: that way selecting a different
	 *  contribution closes the confirm implicitly, and a reason typed against one row can never
	 *  be submitted against the next. */
	let cancellingFor = $state<string | null>(null);
	let cancelReason = $state('');
	const cancelling = $derived(!!contribution && cancellingFor === contribution.uid);

	// ONE PENDING EDIT: the category. Everything else this used to ask has been answered somewhere
	// it belongs better.
	//
	// "Paid by" restated the category, so the two could disagree, and the tax year is derived from
	// the contribution date server-side, so choosing one here could only contradict it — both went
	// in Aug 2026 and are still on the wire, derived. Three more went now:
	//
	//  * **Ad-hoc or regular, and the period.** A credit that has landed is one payment. Whether the
	//    client pays monthly is a fact about the standing arrangement, not about this £500 — and
	//    asking it here let an adviser mark a single detected credit "quarterly", which is what
	//    `cam_frequency` would then carry for ever. A stored frequency is still shown on the settled
	//    view and still sent unchanged (`categorise` COALESCEs an omitted one).
	//  * **The contributor block** — employer name, address, town, postcode, email. It is a fact
	//    about the CLIENT'S EMPLOYER, not about one payment, so it lives on the client record now
	//    (`crm.contacts.employer_name`) and the API reads it from there. Morgan Lloyd marks the
	//    contributor's address and email as not required, so those four fields asked for something
	//    nobody needed, once per payment, of an adviser who could not have known it.
	let category = $state<ContributionCategory>('personal_contribution');

	const paidBy = $derived<PaidBy>(paidByFor(category));

	$effect(() => {
		const c = contribution;
		if (!c) return;
		category = c.category;
	});

	/** A DISPOSAL IS ALWAYS REVERSIBLE. `not_a_contribution` is written AND cancelled in the same
	 *  call, so it lands on a terminal status `CLASSIFIABLE` excludes — which made a mis-click
	 *  permanent: the credit left the queue (the point) with no way back but SQL. Reopening one is
	 *  safe for a stated reason and not a convenient one — nothing downstream ever held it: no
	 *  `cam_contribution`, no lodged expectation, no relief reclaimed. The API applies the same
	 *  rule (`contributions.py::DISPOSALS`) and refuses anyway if one somehow reached Morgan
	 *  Lloyd, so this is the affordance and not the enforcement. */
	const reopenable = $derived(
		!!contribution &&
			UNPUSHABLE_CATEGORIES.has(contribution.category) &&
			!contribution.ml_contribution_id
	);
	const editable = $derived(
		!!contribution && (CLASSIFIABLE.has(contribution.status) || reopenable)
	);
	/** A credit the feed detected, with no `platform.contributions` row behind it yet. Saving it
	 *  CREATES the contribution (`POST /contributions/from-transaction`) rather than amending
	 *  one, so the verbs change: "Classify", not "Save classification". */
	const detected = $derived(!!contribution && isDetected(contribution.uid));

	/** The employer is the CLIENT'S, read from their record. An employer contribution cannot reach
	 *  Morgan Lloyd without one (`contributions_employer_named`, 0051), and the API refuses rather
	 *  than inventing a name — so the panel says so before the adviser presses Classify, and names
	 *  where to fix it. `employer_name` on the contribution itself is the fallback for a row
	 *  classified before the client had one on file. */
	const employerName = $derived(
		(contribution?.employer_name ?? contribution?.client_employer_name ?? '').trim()
	);
	const employerMissing = $derived(!!contribution && NEEDS_EMPLOYER.has(category) && !employerName);
	/** A disposal or a third-party payer, rather than one of the two contribution types. Shown as a
	 *  stated outcome instead of an unselected button group, which is what a `Segmented` holding a
	 *  value outside its options renders as. */
	const otherOutcome = $derived(IS_OTHER_OUTCOME.has(category));
	const pushable = $derived(!UNPUSHABLE_CATEGORIES.has(category));
	/** Cancelling is the ONLY act once the administrator holds it — and the API refuses past
	 *  `ras_applied`, because stopping our record without reversing a reclaim already in flight at
	 *  HMRC leaves the two disagreeing. */
	const cancellable = $derived(
		!!contribution &&
			contribution.status !== 'cancelled' &&
			!RELIEF_CLAIMED.has(contribution.status)
	);
	/** The stage's own words, so the footer, the badge, the metrics and the filter agree. */
	const stage = $derived(contribution ? stageOf(contribution.status) : null);

	const categoryLabel = (value: ContributionCategory) =>
		CONTRIBUTION_CATEGORIES.find((c) => c.value === value)?.label ?? value;
	const paidByLabel = (value: PaidBy) =>
		PAID_BY_OPTIONS.find((p) => p.value === value)?.label ?? value;

	/** The payment, as the custodian described it: when it moved · what they called it · how it
	 *  arrived. Built by filtering rather than by interpolation so a missing segment costs no
	 *  separator — "12 Aug 2026 ·  · " reads as a rendering fault, not as a quiet custodian. */
	const provenance = $derived(
		contribution
			? [
					contribution.transaction_date ? formatDate(contribution.transaction_date) : null,
					[contribution.transaction_type_label, contribution.transaction_description_code]
						.filter(Boolean)
						.join(' / ') || null,
					contribution.transaction_method
				].filter((part): part is string => !!part)
			: []
	);

	/** The contributor, as one line — the payer of a non-personal contribution. */
	const contributorLine = $derived(
		contribution
			? [
					contribution.employer_name,
					contribution.address_line1,
					contribution.city,
					contribution.postcode
				]
					.filter(Boolean)
					.join(', ')
			: ''
	);

	function save() {
		if (!contribution) return;
		// No `tax_year` and no `frequency`: both are derived or stored, and `categorise` COALESCEs an
		// omitted parameter, so leaving them out keeps whatever was worked out at creation rather
		// than overwriting it with whatever this panel happened to show. No `employer` either — it is
		// the client's, and the API reads it from their record.
		onsave?.(contribution.uid, { category, paid_by: paidBy });
	}
</script>

{#snippet field(label: string, value: string)}
	<div class="flex items-baseline justify-between gap-4 py-1.5 text-xs">
		<span class="text-muted-foreground">{label}</span>
		<span class="text-right font-medium">{value}</span>
	</div>
{/snippet}

<div class="border-border/70 bg-card flex h-full min-h-0 flex-col rounded-xl border">
	<div
		class={cn(
			'border-border/70 flex items-center justify-between gap-2 border-b',
			onclose ? 'py-2.5 pr-2 pl-4' : 'px-4 py-3'
		)}
	>
		<h2 class="truncate font-semibold">
			<!-- "Classify Payment", not "Classify this credit": the row is a payment whether or not it
			     has a `platform.contributions` row behind it yet, and two headings for one act made
			     the panel look like it changed function between rows. -->
			{detected ? 'Classify Payment' : editable ? 'Classify & record' : 'Contribution'}
		</h2>
		<div class="flex shrink-0 items-center gap-1.5">
			{#if contribution}
				<StatusBadge status={contribution.status} />
			{/if}
			{#if onclose}
				<Button
					variant="ghost"
					size="icon"
					class="text-muted-foreground hover:text-foreground size-8"
					aria-label="Close panel"
					title="Close (Esc)"
					onclick={onclose}
				>
					<XIcon class="size-4" />
				</Button>
			{/if}
		</div>
	</div>

	{#if !contribution}
		<div
			class="text-muted-foreground flex flex-1 flex-col items-center justify-center gap-2 p-10 text-center text-sm"
		>
			<p>Select a contribution from the queue to classify it and record it to the back office.</p>
		</div>
	{:else}
		{@const c = contribution}
		<div class="space-y-4 overflow-y-auto p-4">
			<div>
				<!-- The panel's own heading is the client's name, so it links for the same reason the
				     queue's does — this is where an adviser is actually looking when they wonder who
				     the person is. Linking one and not the other would read as the two names being
				     different kinds of thing. -->
				{#if links && c.contact_uid}
					<a href={links.client(c.contact_uid)} class="text-lg font-semibold {LINK_RECORD}">
						{c.client_name ?? 'Client'}
					</a>
				{:else}
					<div class="text-lg font-semibold">{c.client_name ?? 'Client'}</div>
				{/if}
				<div
					class="text-muted-foreground mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-[11px]"
				>
					<!-- Linked for the reason the client heading above it is: the panel is where an
					     adviser is looking when they wonder which account the money landed in, and
					     leaving one name live beside a dead one reads as a rendering fault. -->
					{#if links?.account && c.account_uid}
						<a href={links.account(c.account_uid)} class={LINK_RECORD}>
							{c.account_name ?? c.account_uid}
						</a>
					{:else}
						<span>{c.account_name ?? c.account_uid ?? 'Account'}</span>
					{/if}
					{#if c.product_type}<span>{c.product_type}</span>{/if}
					<span>{c.uid}</span>
				</div>
				<!-- THE PAYMENT'S OWN PROVENANCE, where an expectation badge used to sit.
				     That badge answered "has the custodian been told to expect this" — which for a
				     credit that has ALREADY ARRIVED is a question about a forecast nobody raised, so
				     it rendered a bare "—" on every detected row and a "Lodged" tag on the rest. The
				     same fact now has a stage and a metric ("Not lodged"), leaving this line free to
				     say what the row actually is: when the money moved, what the custodian called
				     it, and how it came in. Each segment is omitted when the custodian did not say
				     it — SEI publishes no method — rather than printed as "unknown". -->
				<div class="text-muted-foreground mt-2 flex flex-wrap items-center gap-x-1.5 text-xs">
					<!-- KEYED BY POSITION, not by the segment. Two segments can be equal: when the
					     custodian publishes no description code the middle collapses to the type
					     label alone, and type and method are drawn from overlapping free-text
					     vocabularies ("Bank Transfer", "Direct Debit"). Two identical keys raise
					     `each_key_duplicate` during reconciliation and the whole panel renders
					     nothing — so an adviser could not classify precisely the payment the
					     custodian had described most fully. -->
					{#each provenance as part, i (i)}
						{#if i > 0}<span aria-hidden="true" class="opacity-50">·</span>{/if}
						<span>{part}</span>
					{/each}
				</div>
				<div class="mt-2 flex flex-wrap items-center gap-2">
					<!-- Morgan Lloyd's own status sits beside ours because they answer different
					     questions and can disagree -- and because it is ML's, not ours, that decides
					     whether the contribution can still be amended. -->
					<MlStatusBadge status={c.ml_status} observedAt={c.ml_status_at} />
					{#if c.expectation_reference}
						<span class="text-muted-foreground font-mono text-[11px]">
							Custodian ref {c.expectation_reference}
						</span>
					{/if}
				</div>
				{#if c.status === 'cancelled' && c.cancelled_reason}
					<p class="text-muted-foreground mt-2 text-xs">Cancelled &mdash; {c.cancelled_reason}</p>
				{/if}
				{#if c.expectation_error}
					<!-- The custodian was not told, and why. Left visible rather than buried: an adviser
					     who thinks the expectation is lodged will not chase the payment. Retrying is a
					     real action we control, so it stays available even once the record is settled. -->
					<Alert class="mt-2">
						<AlertDescription class="text-xs">
							{c.expectation_error}
							{#if onlodge}
								<Button
									variant="link"
									size="sm"
									class="h-auto p-0 pl-1 text-xs"
									disabled={busy}
									onclick={() => onlodge?.(c.uid)}
								>
									Retry
								</Button>
							{/if}
						</AlertDescription>
					</Alert>
				{/if}
			</div>

			<!-- WHAT THE QUEUE'S ROW SAYS that the lines above do not, so opening a payment never loses
			     a fact the table had: the adviser (the search and the filter match on it), the
			     transaction reference, the date when no money has moved yet, when it was booked, and the
			     employer an employer contribution goes to Morgan Lloyd under. The settled view states
			     the date and the contributor in its own Classification block. -->
			<div class="divide-border/70 divide-y">
				<!-- The adviser wears the face the queue and the book give them, so one colleague reads
				     the same on every surface. -->
				<div class="flex items-center justify-between gap-4 py-1.5 text-xs">
					<span class="text-muted-foreground">Adviser</span>
					{#if c.adviser}
						<span class="inline-flex min-w-0 items-center gap-1.5 font-medium"
							><PersonAvatar name={c.adviser} size="xs" /><span class="truncate">{c.adviser}</span
							></span
						>
					{:else}
						<span class="text-right font-medium">Unassigned</span>
					{/if}
				</div>
				{#if c.transaction_reference}
					{@render field('Transaction', c.transaction_reference)}
				{/if}
				{#if editable && !c.transaction_date && c.contribution_date}
					{@render field('Date', formatDate(c.contribution_date))}
				{/if}
				{#if !detected}
					{@render field('Booked', formatDate(c.created_at))}
				{/if}
				{#if editable && NEEDS_EMPLOYER.has(category) && employerName}
					{@render field('Employer', employerName)}
				{/if}
			</div>

			<Separator />

			{#if editable}
				<div>
					<h3 class="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">
						Classification
					</h3>
					<div class="space-y-3">
						<div class="space-y-1.5">
							<Label>Contribution type</Label>
							{#if otherOutcome}
								<!-- Not a contribution type: a transfer, a credit being disposed of, or a
								     third-party payer on a row classified before that stopped being offered.
								     Stated rather than left as a button group with nothing selected, which is
								     what Segmented renders for a value outside its options. -->
								<div
									class="border-border/70 flex items-center justify-between gap-2 rounded-lg border px-3 py-1.5"
								>
									<span class="text-xs font-medium">{categoryLabel(category)}</span>
									<Button
										variant="ghost"
										size="sm"
										class="h-auto py-1 text-xs"
										onclick={() => (category = 'personal_contribution')}
									>
										Change
									</Button>
								</div>
							{:else}
								<!-- AUTO-WIDTH: no class at all, because Segmented's root is already
								     `inline-flex` and sizes to its own labels. It carried `w-full`, which
								     stretched the pill across the 420px panel while the two buttons kept their
								     `px-3.5` — so "Personal" and "Employer" sat in the left third of a wide
								     grey trough that read as a broken control rather than a two-way switch. -->
								<Segmented bind:value={category} options={CREATABLE_CONTRIBUTION_CATEGORIES} />
								<!-- The rest of the classification vocabulary, kept reachable but quiet. Without
								     it a credit that is neither a personal nor an employer contribution cannot
								     be disposed of, so it comes back in the queue for ever. -->
								<p class="text-muted-foreground flex flex-wrap items-center gap-x-1 text-xs">
									Something else?
									{#each OTHER_CONTRIBUTION_OUTCOMES as option (option.value)}
										<Button
											variant="link"
											size="sm"
											class="h-auto p-0 text-xs"
											onclick={() => (category = option.value)}
										>
											{option.label}
										</Button>
									{/each}
								</p>
							{/if}
						</div>

						{#if employerMissing}
							<!-- The employer is the CLIENT'S FACT and lives on their record. Said here rather
							     than discovered as a 400 after pressing Classify, and it names where to fix
							     it — the panel cannot fix it, because an employer is not a property of one
							     payment. -->
							<Alert variant="destructive">
								<AlertDescription class="text-xs">
									This client has no employer on file, and Morgan Lloyd needs one for an employer
									contribution. Add it under <strong>Employment</strong> on their client record.
								</AlertDescription>
							</Alert>
						{/if}
					</div>
				</div>

				<!-- NO RELIEF BLOCK FOR A DISPOSAL. A transfer in and a not-a-contribution earn no
				     basic-rate relief and are never sent to Morgan Lloyd, so a gross-up beside them
				     states an amount nobody will ever reclaim — and it moves as the adviser changes
				     the category, which is the moment it is most likely to be misread as a figure
				     that follows from the new answer. Same test as the footer's, so the panel cannot
				     promise relief on a row it also says is not recordable. -->
				{#if pushable}
					<div>
						<h3 class="text-muted-foreground mb-2 text-xs font-semibold tracking-wide uppercase">
							Relief at source
						</h3>
						<GrossUp amount={c.amount} {category} />
					</div>
				{/if}
			{:else}
				<!-- Settled: the details as recorded, not a form nobody can use. -->
				<div>
					<h3 class="text-muted-foreground mb-1 text-xs font-semibold tracking-wide uppercase">
						Classification
					</h3>
					<div class="divide-border/70 divide-y">
						{@render field('Contribution type', categoryLabel(c.category))}
						{@render field('Paid by', paidByLabel(c.paid_by))}
						<!-- NO FREQUENCY AND NO TAX YEAR. Neither is read anywhere: the panel stopped
						     asking for a frequency (a credit that has landed is one payment), the tax
						     year is derived server-side from the date beside it, and a details view is
						     for facts somebody acts on rather than every column the row happens to
						     hold. Both are still stored and still sent to Morgan Lloyd.
						     "Transaction date", not "Contribution date": it is the date the MONEY
						     moved, which is what `matching.link` stamps off the credit and what a
						     reconciliation is read against — "contribution date" invited it to be read
						     as the date somebody decided to contribute. -->
						{@render field(
							'Transaction date',
							formatDate(c.transaction_date ?? c.contribution_date)
						)}
						<!-- When the scheme administrator took it on. The stage vocabulary calls the
						     absence of this "Not lodged", so the record should be able to say when it
						     stopped being true. -->
						{@render field('Lodged on', formatDate(c.ml_recorded_at))}
						{#if contributorLine}
							{@render field('Contributor', contributorLine)}
						{/if}
					</div>
				</div>

				{#if !UNPUSHABLE_CATEGORIES.has(c.category)}
					<div>
						<h3 class="text-muted-foreground mb-1 text-xs font-semibold tracking-wide uppercase">
							Relief at source
						</h3>
						<!-- As claimed, not as recomputed. Relief was worked out at classification time; deriving
					     it again from today's earnings on file could contradict what was actually claimed. -->
						<div class="divide-border/70 divide-y">
							<div class="flex items-baseline justify-between gap-4 py-1.5 text-xs">
								<span class="text-muted-foreground">Amount paid</span>
								<Money amount={c.amount} class="font-medium" />
							</div>
							<div class="flex items-baseline justify-between gap-4 py-1.5 text-xs">
								<span class="text-muted-foreground">Relief reclaimed</span>
								<Money amount={c.relief_amount ?? 0} class="font-medium" />
							</div>
							<div class="flex items-baseline justify-between gap-4 py-1.5 text-xs">
								<span class="text-muted-foreground">Gross to pension</span>
								<Money amount={c.gross_amount ?? c.amount} class="font-medium" />
							</div>
							{#if c.prefunded_ras_amount}
								<div class="flex items-baseline justify-between gap-4 py-1.5 text-xs">
									<span class="text-muted-foreground">Relief paid in advance</span>
									<Money amount={c.prefunded_ras_amount} class="font-medium" />
								</div>
							{/if}
						</div>
					</div>
				{/if}

				{#if c.ml_contribution_id}
					<div>
						<h3 class="text-muted-foreground mb-1 text-xs font-semibold tracking-wide uppercase">
							Morgan Lloyd
						</h3>
						<div class="divide-border/70 divide-y">
							{@render field('Recorded', formatDate(c.ml_recorded_at))}
							{@render field('Reference', c.ml_contribution_id)}
						</div>
					</div>
				{/if}
			{/if}
		</div>

		<!-- TWO ACTS, AND NOTHING ELSE. A contribution is only ever at one of two points where a
		     person has anything to do: it needs classifying, or it is with the scheme administrator
		     and the only thing left is to stop it. Everything between those was a step this panel
		     asked somebody to remember — "Save classification", then "Record to Morgan Lloyd" as a
		     second press, with a `confirmed` limbo in between that reads as done and is not.
		     Classifying now DOES the record (see the page's `classify` action), so the two presses
		     are the one decision they always were, and a row that stays behind is a FAILED push
		     rather than an unpressed button — which is what the "Not lodged" metric counts. -->
		{#if editable}
			<div class="border-border/70 mt-auto space-y-2 border-t p-4">
				<Button class="w-full" disabled={busy || employerMissing} onclick={save}>
					<CheckIcon class="mr-1.5 size-4" />
					{#if !pushable}
						Classify as {categoryLabel(category).toLowerCase()}
					{:else}
						Classify
					{/if}
				</Button>
				<p class="text-muted-foreground text-xs">
					{#if !pushable}
						Recorded here. Morgan Lloyd does not support a
						{categoryLabel(category).toLowerCase()}.
					{:else}
						Records the contribution with Morgan Lloyd in the same step.
					{/if}
				</p>
			</div>
		{:else}
			<div class="border-border/70 mt-auto space-y-2 border-t p-4">
				<p class="text-muted-foreground text-xs">
					{#if c.status === 'cancelled'}
						This contribution is cancelled, so nothing further will happen to it.
					{:else if cancellable}
						With Morgan Lloyd. Reverse it there before reclassifying.
					{:else}
						<!-- Past `ras_applied` a basic-rate reclaim is in flight at HMRC and even cancelling
						     is refused (409), so there is genuinely nothing to offer. Saying so beats an
						     empty footer, which reads as a panel that failed to render its controls. -->
						At <strong class="text-foreground">{stage?.label ?? c.status}</strong>. No action is
						available here. {stage?.actor ?? 'The scheme administrator'} moves it on.
					{/if}
				</p>
				{@render cancelControl(c)}
				{#if onrefresh && c.ml_contribution_id && c.status !== 'cancelled'}
					<!-- Application -> Active -> Received happens at the scheme administrator and at
					     HMRC, not here. Asking is the only honest way to learn it, so it stays: it is a
					     READ, not a third act on the contribution. -->
					<Button
						variant="ghost"
						size="sm"
						class="text-muted-foreground w-full"
						disabled={busy}
						onclick={() => onrefresh?.(c.uid)}
					>
						<RefreshIcon class="mr-1.5 size-4" />
						Refresh from Morgan Lloyd
					</Button>
				{/if}
			</div>
		{/if}
	{/if}
</div>

<!-- NO "THE MONEY" BLOCK. It rendered the three-way reconciliation ("Booked £500 · Forecast £500 ·
     Received —"), the reason under it ("No credit has been matched to this contribution yet") and a
     sentence on relief still to be credited. Every one of those is a statement about a payment
     lifecycle the adviser classifying a credit has no part in: the money HAS arrived (that is why
     the row exists), the forecast is a row `from-transaction` deliberately never creates, and relief
     is reclaimed by the scheme in a monthly batch nobody here can chase. So the block spent a third
     of the panel restating the amount on the row above it and inviting an adviser to chase a break
     they cannot resolve.
     `ReconciliationPanel` and `matching.reconcile` are untouched and still exported — the position
     is real and server-computed; it just is not this surface's question. -->

{#snippet cancelControl(c: Contribution)}
	{#if oncancel && c.status !== 'cancelled' && !RELIEF_CLAIMED.has(c.status)}
		{#if cancelling}
			<div class="space-y-2">
				<Label for="cancel-reason" class="text-xs">Why is this being cancelled?</Label>
				<Input
					id="cancel-reason"
					bind:value={cancelReason}
					placeholder="e.g. client changed their mind"
					disabled={busy}
				/>
				<div class="flex gap-2">
					<Button
						variant="destructive"
						size="sm"
						class="flex-1"
						disabled={busy || !cancelReason.trim()}
						onclick={() => oncancel?.(c.uid, cancelReason.trim())}
					>
						Cancel contribution
					</Button>
					<Button variant="ghost" size="sm" disabled={busy} onclick={() => (cancellingFor = null)}>
						Keep it
					</Button>
				</div>
				{#if c.ml_contribution_id}
					<p class="text-muted-foreground text-xs">
						This also stops the record at Morgan Lloyd. If that call fails the contribution is still
						cancelled here — retry the stop from this panel.
					</p>
				{/if}
			</div>
		{:else}
			<Button
				variant="ghost"
				size="sm"
				class="text-muted-foreground w-full"
				disabled={busy}
				onclick={() => {
					cancellingFor = c.uid;
					cancelReason = '';
				}}
			>
				<XIcon class="mr-1.5 size-4" />
				Cancel this contribution
			</Button>
		{/if}
	{/if}
{/snippet}
