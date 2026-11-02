<script lang="ts">
	// The journey (running case) detail — the platform portal's /journeys/[uid] page, extracted so
	// the admin portal can render the same record read-only (the portal waterfall). Data arrives as
	// typed props, every href comes from the injected `links`, and the mutations (step actions,
	// task completion, stop journey) only exist when the app passes `actions` — its own form-action
	// names plus SvelteKit's `enhance`, which this package cannot import.
	import UserIcon from '@lucide/svelte/icons/user';
	import WalletIcon from '@lucide/svelte/icons/wallet';
	import MailIcon from '@lucide/svelte/icons/mail';
	import PhoneIcon from '@lucide/svelte/icons/phone';
	import CakeIcon from '@lucide/svelte/icons/cake';
	import LockIcon from '@lucide/svelte/icons/lock';
	import CheckIcon from '@lucide/svelte/icons/check';
	import ClockIcon from '@lucide/svelte/icons/clock';
	import CircleIcon from '@lucide/svelte/icons/circle';
	import XIcon from '@lucide/svelte/icons/x';
	import FileCheckIcon from '@lucide/svelte/icons/file-check-2';
	import ListChecksIcon from '@lucide/svelte/icons/list-checks';
	import RotateIcon from '@lucide/svelte/icons/rotate-ccw';
	import PlugZapIcon from '@lucide/svelte/icons/plug-zap';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import ClipboardListIcon from '@lucide/svelte/icons/clipboard-list';
	import ShieldIcon from '@lucide/svelte/icons/shield';
	import ScanFaceIcon from '@lucide/svelte/icons/scan-face';
	import LandmarkIcon from '@lucide/svelte/icons/landmark';
	import FlagIcon from '@lucide/svelte/icons/flag';
	import KeyRoundIcon from '@lucide/svelte/icons/key-round';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import DownloadIcon from '@lucide/svelte/icons/download';
	import PrinterIcon from '@lucide/svelte/icons/printer';
	import { tick, type Component } from 'svelte';
	import { Alert, AlertDescription, AlertTitle } from '../../ui/alert/index.js';
	import { Badge } from '../../ui/badge/index.js';
	import { Button } from '../../ui/button/index.js';
	import { Card, CardContent } from '../../ui/card/index.js';
	import { Checkbox } from '../../ui/checkbox/index.js';
	import { ConfirmDialog } from '../../ui/confirm-dialog/index.js';
	import { DataTable, type DataTableColumn } from '../../ui/data-table/index.js';
	import * as Dialog from '../../ui/dialog/index.js';
	import * as DropdownMenu from '../../ui/dropdown-menu/index.js';
	import { FileDropzone } from '../../ui/file-dropzone/index.js';
	import { Label } from '../../ui/label/index.js';
	import { SectionHeading } from '../../ui/section-heading/index.js';
	import { StatusBadge } from '../../ui/status-badge/index.js';
	import { SelectMenu } from '../../ui/select-menu/index.js';
	import { Textarea } from '../../ui/textarea/index.js';
	import PageHeader from '../../shell/page-header.svelte';
	import { formatDayMonth } from '../../../dates.js';
	import ClientAvatar from '../../data/client-avatar.svelte';
	import { birthLine, clientKind } from '../../data/client-kind.js';
	import { withToast, type Enhance } from '../clients/form-enhance.js';
	import {
		ASSIGNEE_KIND_LABELS,
		dueLabel,
		isOpen,
		isOverdue,
		type TaskRow
	} from '../tasks/tasks.js';
	import {
		accountKickoffSummary,
		accountParties,
		activityEntries,
		isCompletedOnboarding,
		jisaChildName,
		kycSummary,
		portalStatusMeta,
		progress,
		roleLabel,
		statusLabel,
		stateLabel,
		stateTone,
		stepLabel
	} from './applications.js';
	import {
		actionableSteps,
		assignedTo,
		awaitingLine,
		clientOutstanding,
		clientOutstandingLine,
		firmWorkOwner,
		groupStageSteps,
		handoffNotice,
		identityProviderErrorLine,
		isCancellable,
		journeyTimeline,
		nextStaffForm,
		reasonLine,
		sectionProgress,
		stepAction,
		stepAnchor,
		stepGroupSubjects,
		stepOwnerLabel,
		stepPurpose,
		stepSubjectLine,
		workOwners,
		type JourneyCase,
		type JourneyExecution,
		type JourneyStep,
		type OrderedStep
	} from './steps.js';
	import { answerSections } from './answers.js';
	import IdentityEvidenceUpload from './identity-evidence-upload.svelte';
	import type { JourneyDetailActions, JourneyDetailLinks, JourneyStaff } from './types.js';
	import { humanise } from '../../../sentence-case.js';
	import { LINK_RECORD } from '../../../text-shell.js';

	let {
		execution,
		tasks = [],
		staff = [],
		links,
		actions = null
	}: {
		execution: JourneyExecution;
		/** Tasks raised against this journey (by its steps, or by hand). */
		tasks?: TaskRow[];
		/** Platform members the journey can be assigned to. Empty ⇒ the name is shown as text. */
		staff?: JourneyStaff[];
		links: JourneyDetailLinks;
		actions?: JourneyDetailActions | null;
	} = $props();

	const ex = $derived(execution);
	type AppCase = JourneyCase;
	type AppStep = JourneyStep;

	// Whether this mount may act at all: the app passes `actions` only when the user holds the
	// write permission, so absence doubles as the read-only posture (the admin default).
	const canManage = $derived(actions !== null);
	// `use:` needs a plain identifier: alias the injected enhance (never called when read-only —
	// every form is inside an `{#if actions}` branch, so the noop fallback only satisfies types).
	const enhanceForm: Enhance = $derived(actions?.enhance ?? (() => {}));

	const prog = $derived(progress(ex.cases));
	const profiles = $derived(ex.cases.filter((c) => c.kind === 'profile'));
	const accounts = $derived(ex.cases.filter((c) => c.kind === 'account'));
	/** ONE timeline: the stages ARE the headings and the work nests under the stage it belongs to.
	 *  It was a stage strip AND a flat 23-item work list computed from unrelated sources. */
	const timeline = $derived(journeyTimeline(ex, canManage));
	// Empty stages (Draft, and Completed when nothing maps to it) are real in the projection and
	// never hold work on this view. Showing them as "No work in this stage" just lengthens the card.
	const visibleStages = $derived(timeline.stages.filter((stage) => stage.steps.length > 0));
	const firmOwner = $derived(firmWorkOwner(ex));
	const resume = $derived.by(() => {
		const nextForm = nextStaffForm(ex.cases, canManage);
		if (!nextForm) return null;
		return {
			...nextForm,
			href: links.fillStep(nextForm.caseUid, nextForm.stepKey, nextForm.participant)
		};
	});
	const todo = $derived(actionableSteps(ex.cases, canManage));
	const parallelForms = $derived(todo.filter((t) => t.step.step_type === 'fill_form').length > 1);
	/** Highlight a single next step only when the frontier is one item — several parallel forms
	 *  must not read as a false sequence. */
	const next = $derived(parallelForms ? null : (todo[0] ?? null));
	/** What the client still owes — a summary, never a gate (the client's own page is authoritative). */
	const clientLine = $derived(clientOutstandingLine(clientOutstanding(ex.cases)));

	// Step-status circle treatment — theme tokens, keyed by engine status.
	const STEP_CIRCLE: Record<string, string> = {
		fulfilled: 'bg-success/15 text-success',
		failed: 'bg-destructive/15 text-destructive',
		waiting: 'bg-warning/15 text-warning',
		blocked: 'bg-info/15 text-info'
	};

	// Pending markers so a row's buttons disable while its action is in flight.
	let pendingStep = $state<string | null>(null);
	let pendingTask = $state<string | null>(null);
	let pendingInvite = $state<string | null>(null);
	// The invite form per applicant, hidden: the portal pill's menu item submits it, the same way
	// the client record's "No portal access" pill does. Keyed by the case uid.
	let inviteForms = $state<Record<string, HTMLFormElement | undefined>>({});
	function stepPendingKey(c: AppCase, s: AppStep): string {
		return `${c.uid}:${s.step_key}:${s.participant_subject_id ?? ''}`;
	}

	// A compliance decision (identity / AML / review) is recorded with a rationale, so the trail says
	// WHY as well as who and when. The dialog collects the note, then submits the row's form.
	let decisionOpen = $state(false);
	let approveOpen = $state(false);
	let approveForm: HTMLFormElement | null = $state(null);
	let decisionOutcome = $state<'passed' | 'referred'>('passed');
	let decisionNote = $state('');
	let decisionLabel = $state('');
	let decisionSubmitter: HTMLButtonElement | null = null;
	function openDecision(e: MouseEvent, outcome: 'passed' | 'referred', label: string) {
		e.preventDefault();
		decisionSubmitter = e.currentTarget as HTMLButtonElement;
		decisionOutcome = outcome;
		decisionLabel = label;
		decisionNote = '';
		decisionOpen = true;
	}
	function recordDecision() {
		decisionOpen = false;
		if (!decisionSubmitter) return;
		const form = decisionSubmitter.form;
		const noteField = form?.elements.namedItem('note') as HTMLInputElement | null;
		if (noteField) noteField.value = decisionNote;
		form?.requestSubmit(decisionSubmitter);
	}

	// Recording a terms acceptance taken in person. The adviser is attesting to another person's
	// agreement, so the record is the SIGNED COPY: the dialog owns the form (a file cannot be
	// injected into a row's hidden inputs the way a note can), requires the upload before it will
	// submit, and the API refuses the acceptance without it. The step it is for is carried in state.
	let attestOpen = $state(false);
	let attestNote = $state('');
	let attestLabel = $state('');
	let attestWho = $state<string | null>(null);
	let attestFiles = $state<File[]>([]);
	let attestTarget = $state<{
		caseUid: string;
		stepKey: string;
		participant: number | null;
		key: string;
	} | null>(null);
	let attestFormEl = $state<HTMLFormElement | null>(null);
	function openAttest(c: AppCase, s: AppStep, label: string, who: string | null) {
		attestTarget = {
			caseUid: c.uid,
			stepKey: s.step_key,
			participant: s.participant_subject_id,
			key: stepPendingKey(c, s)
		};
		attestLabel = label;
		attestWho = who;
		attestNote = '';
		attestFiles = [];
		attestOpen = true;
	}

	// The onboarding handoff is an attestation, not a generic task tick. Confirmation is posted as
	// evidence and the API refuses the invitation side effect without it.
	let declarationOpen = $state(false);
	let declarationConfirmed = $state(false);
	let declarationSubmitter: HTMLButtonElement | null = null;
	function openDeclaration(e: MouseEvent) {
		e.preventDefault();
		declarationSubmitter = e.currentTarget as HTMLButtonElement;
		declarationConfirmed = false;
		declarationOpen = true;
	}
	function recordDeclaration() {
		if (!declarationConfirmed || !declarationSubmitter) return;
		declarationOpen = false;
		const form = declarationSubmitter.form;
		const confirmed = form?.elements.namedItem('adviser_confirmed') as HTMLInputElement | null;
		if (confirmed) confirmed.value = 'true';
		form?.requestSubmit(declarationSubmitter);
	}

	// Stopping a journey withdraws the client's outstanding requests and frees them to start again.
	let cancelOpen = $state(false);
	let cancelReason = $state('');
	let cancelling = $state(false);
	let cancelFormEl = $state<HTMLFormElement | null>(null);

	/** The applicant's kind, off the contact's type and date of birth (`clientKind`). */
	function applicantKind(c: AppCase) {
		return clientKind({ type: c.contact_type, dob: c.dob });
	}
	function accountTitle(c: AppCase): string {
		return c.platform_name ? `${c.platform_name} account` : `Account #${c.subject_id}`;
	}
	/** Wrapper and catalogue name, pulled out of the kickoff line so the type is a real label. */
	function accountType(c: AppCase): { wrapper: string; name: string; rest: string } {
		const wrapper = (c.product_type ?? '').trim();
		const name = (c.product_name ?? '').trim();
		const kickoff = accountKickoffSummary(c);
		const lead = name || wrapper;
		let rest = kickoff;
		if (lead && kickoff === lead) rest = '';
		else if (lead && kickoff.startsWith(`${lead} · `)) rest = kickoff.slice(lead.length + 3);
		// The child of a JISA is a real party now, so the kickoff snapshot of their name would
		// print the same person twice — once anonymously in the meta line and once under Owner.
		const child = jisaChildName(c);
		if (child && accountParties(c).some((group) => group.names.includes(child))) {
			rest = rest
				.split(' · ')
				.filter((bit) => bit !== child)
				.join(' · ');
		}
		return { wrapper, name, rest };
	}
	function stepDone(s: AppStep): boolean {
		return s.status === 'fulfilled';
	}
	function groupTitles(items: OrderedStep[]): string {
		return [
			...new Set(items.map((item) => item.step.title ?? stepLabel(item.step.step_type)))
		].join(', ');
	}
	// Step keys are shown as their authored title where the journey still carries the step.
	const stepTitles = $derived(
		new Map(ex.cases.flatMap((c) => c.steps.map((s) => [s.step_key, s.title ?? s.step_key])))
	);
	// Repeated completions of one step (one per applicant or account) collapse. See `activityEntries`.
	const activity = $derived(activityEntries(ex.timeline, ex.cases));
	const activityDays = $derived.by(() => {
		const days: { label: string; entries: typeof activity }[] = [];
		for (const entry of activity) {
			const label = formatDayMonth(entry.at);
			const last = days[days.length - 1];
			if (last?.label === label) last.entries.push(entry);
			else days.push({ label, entries: [entry] });
		}
		return days;
	});
	function clock(iso: string): string {
		return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
	}
	function activityIcon(kind: string | null): Component {
		switch (kind) {
			case 'fill_form':
				return ClipboardListIcon;
			case 'accept_terms':
				return FileCheckIcon;
			case 'verify_identity':
				return ScanFaceIcon;
			case 'aml_check':
				return ShieldIcon;
			case 'setup_account':
				return LandmarkIcon;
			case 'task':
				return ListChecksIcon;
			case 'started':
				return FlagIcon;
			case 'stopped':
				return XIcon;
			default:
				return CircleIcon;
		}
	}
	const assigneeOptions = $derived([
		{ value: 'none', label: 'Unassigned' },
		...staff.map((person) => ({
			value: person.user_id,
			label: person.name || person.email || person.user_id
		}))
	]);
	// Undefined until the person changes it, so a reload of the journey replaces the draft.
	let assigneeDraft = $state<string | undefined>(undefined);
	const assigneePick = $derived(assigneeDraft ?? ex.assignee_user_id ?? 'none');
	let assignForm: HTMLFormElement | undefined = $state();
	async function saveAssignee(next: string) {
		assigneeDraft = next;
		await tick();
		assignForm?.requestSubmit();
	}

	// Due opens the table, soonest first; the actions column shows only when tasks can be completed.
	const columns = $derived<DataTableColumn<TaskRow>[]>([
		{ key: 'state', header: '', headerClass: 'w-14 pl-5', cellClass: 'pl-5' },
		{ key: 'title', header: 'Task', sortable: true, headerClass: 'w-[40%]' },
		{
			key: 'assignee_name',
			header: 'Assignee',
			sortable: true,
			hideBelowWidth: 'lg',
			headerClass: 'w-[28%]'
		},
		{
			key: 'due',
			header: 'Due',
			sortable: true,
			sortDefault: true,
			headerClass: 'w-[16%]',
			sortValue: (t) => (isOpen(t) ? t.due_date : null)
		},
		...(actions
			? [{ key: 'actions', header: '', align: 'right' as const, headerClass: 'w-[16%] min-w-28' }]
			: [])
	]);

	function when(iso: string): string {
		const d = new Date(iso);
		return d.toLocaleString(undefined, {
			day: 'numeric',
			month: 'short',
			hour: '2-digit',
			minute: '2-digit'
		});
	}
</script>

<!-- Declared OUTSIDE the PageHeader tag: an inline `{#snippet actions()}` would shadow the
     component's own `actions` prop inside the header's children scope. -->
{#snippet headerActions()}
	<div class="flex items-center gap-2">
		{#if links.applicationPdf && isCompletedOnboarding(ex)}
			<Button
				variant="outline"
				size="sm"
				href={links.applicationPdf}
				target="_blank"
				rel="noopener"
			>
				<DownloadIcon class="mr-1.5 size-4" /> Download application
			</Button>
		{/if}
		<StatusBadge
			tone={stateTone(ex.status)}
			pulse={ex.status === 'active' || ex.status === 'waiting' || ex.status === 'blocked'}
		>
			{ex.application_status ? statusLabel(ex.application_status) : stateLabel(ex.status)}
		</StatusBadge>
		{#if actions && isCancellable(ex.status)}
			<Button
				variant="outline"
				size="sm"
				onclick={() => ((cancelReason = ''), (cancelOpen = true))}
			>
				<XIcon class="mr-1.5 size-4" /> Stop journey
			</Button>
		{/if}
	</div>
{/snippet}

<!-- Full content area, not a centred column: the journey is a timeline plus its work, and the
     shell's sidebar already frames it the way every other book surface is framed. -->
<div class="w-full">
	<PageHeader
		title={ex.flow_name ?? 'Onboarding application'}
		description={`${ex.cases.filter((c) => c.kind === 'profile').length} clients · ${accounts.length} account${accounts.length === 1 ? '' : 's'} · started ${when(ex.created_at)}`}
		back={{ href: links.journeys, label: 'Journeys' }}
		actions={headerActions}
		class="mb-4"
	/>

	{#if ex.status === 'cancelled'}
		<Alert class="mb-5">
			<AlertTitle>This journey was stopped</AlertTitle>
			<AlertDescription>
				Its outstanding steps were withdrawn and the client's checklist cleared. You can start a
				fresh journey for them.
			</AlertDescription>
		</Alert>
	{/if}

	<div
		class="grid gap-5 [grid-template-areas:'main'_'rail'] xl:grid-cols-[minmax(0,1fr)_22rem] xl:items-start xl:[grid-template-areas:'main_rail']"
	>
		<div class="min-w-0 space-y-5 [grid-area:main]">
			{#if profiles.length || accounts.length}
				<Card glass>
					<CardContent class="space-y-6">
						<!-- No card-level heading: the two columns below name themselves ("Applicants",
						     "Accounts") and a third title above them counted the same people and
						     accounts a second time. -->
						<div
							class="grid items-start gap-8 {profiles.length && accounts.length
								? 'lg:grid-cols-2 lg:gap-12'
								: ''}"
						>
							{#if profiles.length}
								<section class="space-y-4">
									<SectionHeading title="Applicants" count={profiles.length} class="mb-0" />
									{#each profiles as c (c.uid)}
										{@const kind = applicantKind(c)}
										{@const portal = portalStatusMeta(c.portal_status)}
										{@const born = birthLine(c.dob)}
										{@const name = c.subject_name ?? `Contact #${c.subject_id}`}
										<div
											class="border-border bg-muted/20 flex items-start gap-3 rounded-lg border p-3"
										>
											<!-- The disc is the glyph, not initials: a child, a person or a building,
											     the same mark the client list and record use for the same person. -->
											<ClientAvatar {name} {kind} />
											<div class="min-w-0 flex-1">
												<div class="flex flex-wrap items-center gap-x-2 gap-y-1">
													<!-- Linked only when the case resolved to a real contact: the
													     `Contact #{serial}` fallback names a record the app cannot
													     address, and the routes are uid-typed. -->
													{#if c.subject_uid && links.client}
														<a href={links.client(c.subject_uid)} class="font-medium {LINK_RECORD}"
															>{name}</a
														>
													{:else}
														<span class="font-medium">{name}</span>
													{/if}
													{#if kind === 'child'}
														<Badge variant="outline">Child</Badge>
													{/if}
													<StatusBadge tone={stateTone(c.status)}
														>{stateLabel(c.status)}</StatusBadge
													>
													{#if portal.badge}
														{@const canInvite =
															canManage &&
															!!actions?.invitePortal &&
															!!portal.action &&
															!!c.subject_id}
														<!-- The portal pill IS the control, as on the client record: click it
														     for the invite (or resend / re-invite, worded for the state). A
														     separate button column squeezed every other fact into wrapping.
														     Read-only, or nothing to do (active), it is a plain badge. -->
														{#if canInvite}
															<DropdownMenu.Root>
																<DropdownMenu.Trigger
																	class="focus-visible:ring-ring/50 inline-flex shrink-0 cursor-pointer rounded-full outline-none focus-visible:ring-2"
																	title="Portal access"
																	aria-label="Portal access"
																>
																	<StatusBadge tone={portal.tone}>
																		<KeyRoundIcon class="size-3" />
																		{portal.badge}
																	</StatusBadge>
																</DropdownMenu.Trigger>
																<DropdownMenu.Content align="start" class="w-52">
																	<DropdownMenu.Item
																		disabled={pendingInvite === c.uid || !c.contact_email}
																		onclick={() => inviteForms[c.uid]?.requestSubmit()}
																	>
																		{pendingInvite === c.uid ? 'Sending…' : portal.action}
																	</DropdownMenu.Item>
																	{#if !c.contact_email}
																		<DropdownMenu.Label class="text-muted-foreground font-normal">
																			This client has no email on file.
																		</DropdownMenu.Label>
																	{/if}
																</DropdownMenu.Content>
															</DropdownMenu.Root>
														{:else}
															<StatusBadge tone={portal.tone}>
																<KeyRoundIcon class="size-3" />
																{portal.badge}
															</StatusBadge>
														{/if}
													{/if}
												</div>
												<!-- ONE line of facts, not a row of tags: the roles, then the KYC state.
												     Six badges on a card said less than a sentence does. -->
												{#if c.roles.length || kycSummary(c)}
													<p class="text-muted-foreground mt-0.5 text-xs">
														{[c.roles.map(roleLabel).join(', '), kycSummary(c)]
															.filter(Boolean)
															.join(' · ')}
													</p>
												{/if}
												{#if born || c.contact_email || c.contact_phone}
													<dl
														class="text-muted-foreground mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs"
													>
														{#if born}
															<div class="flex items-center gap-1.5">
																<dt class="sr-only">Date of birth</dt>
																<CakeIcon class="size-3.5 shrink-0" aria-hidden="true" />
																<dd>{born}</dd>
															</div>
														{/if}
														{#if c.contact_email}
															<div class="flex min-w-0 items-center gap-1.5">
																<dt class="sr-only">Email</dt>
																<MailIcon class="size-3.5 shrink-0" aria-hidden="true" />
																<dd class="truncate">{c.contact_email}</dd>
															</div>
														{/if}
														{#if c.contact_phone}
															<div class="flex items-center gap-1.5">
																<dt class="sr-only">Phone</dt>
																<PhoneIcon class="size-3.5 shrink-0" aria-hidden="true" />
																<dd>{c.contact_phone}</dd>
															</div>
														{/if}
													</dl>
												{/if}
												{#if c.portal_login_email && c.portal_login_email !== c.contact_email}
													<!-- The login can be a different address from the client's own record (a
											     parent, a POA) — worth surfacing so "why hasn't the client had an
											     email" has an answer that isn't a bug. -->
													<p class="text-muted-foreground mt-0.5 text-xs">
														Login: {c.portal_login_email}
													</p>
												{/if}
											</div>
											{#if canManage && actions?.invitePortal && portal.action && c.subject_id}
												<!-- Hidden: the portal pill's menu submits it (`inviteForms[c.uid]`). -->
												<form
													method="POST"
													action={actions.invitePortal}
													bind:this={inviteForms[c.uid]}
													use:enhanceForm={withToast('Invite sent', {
														pending: (v) => (pendingInvite = v ? c.uid : null)
													})}
													class="hidden"
												>
													<input type="hidden" name="contact_uid" value={c.subject_uid} />
												</form>
											{/if}
										</div>
									{/each}
								</section>
							{/if}

							{#if accounts.length}
								<section class="space-y-4">
									<SectionHeading title="Accounts" count={accounts.length} class="mb-0" />
									{#each accounts as c (c.uid)}
										{@const product = accountType(c)}
										{@const named = accountParties(c)}
										<div
											class="border-border bg-muted/20 flex items-center gap-4 rounded-lg border p-4"
										>
											<span
												class="bg-muted text-muted-foreground flex size-10 shrink-0 items-center justify-center rounded-full"
											>
												<WalletIcon class="size-5" />
											</span>
											<div class="min-w-0 flex-1">
												<div class="flex flex-wrap items-center gap-2">
													<!-- `account_ref` is the opened account's uid, and it is null until the
													     account exists — a case still being opened has nothing to link to,
													     which is also when `accountTitle` falls back to `Account #{serial}`. -->
													{#if c.account_ref && links.account}
														<a href={links.account(c.account_ref)} class="font-medium {LINK_RECORD}"
															>{accountTitle(c)}</a
														>
													{:else}
														<span class="font-medium">{accountTitle(c)}</span>
													{/if}
													{#if product.wrapper}
														<Badge variant="outline">{product.wrapper}</Badge>
													{/if}
													{#if c.has_transfer}
														<Badge variant="secondary">Transfer in</Badge>
													{/if}
												</div>
												{#if product.name && product.name !== product.wrapper}
													<p class="mt-1 text-xs">{product.name}</p>
												{/if}
												{#if named.length}
													<p class="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-sm">
														{#each named as group (group.role)}
															<span>
																<span class="text-muted-foreground">{group.label}</span>
																{group.names}
															</span>
														{/each}
													</p>
												{/if}
												{#if product.rest}
													<p class="text-muted-foreground mt-0.5 text-xs">{product.rest}</p>
												{/if}
											</div>
										</div>
									{/each}
								</section>
							{/if}
						</div>
					</CardContent>
				</Card>
			{/if}

			<!-- THE timeline. The stages are the headings and each holds its own work (`journeyTimeline`),
	     where the page used to draw a stage strip and a flat work list from unrelated sources with
	     nothing saying which stage a step belonged to. It is scoped to what is NEXT rather than
	     being a complete map: the current stage is open, the rest state their counts. -->
			<Card glass>
				<CardContent>
					{#if resume}
						<div class="border-border mb-3 flex items-center justify-between gap-3 border-b pb-3">
							<p class="min-w-0 truncate text-sm">
								<span class="text-muted-foreground">
									{resume.started ? 'Continue with' : 'Start with'}
								</span>
								<span class="font-medium">{resume.title}</span>
								<span class="text-muted-foreground">· For {resume.who}</span>
							</p>
							<Button href={resume.href} size="sm" class="shrink-0">
								{resume.started ? 'Continue' : 'Start'}
							</Button>
						</div>
					{/if}
					<ol class="space-y-0">
						{#each visibleStages as st, i (st.key)}
							{@const outstanding = st.steps.filter((o) => o.step.status !== 'fulfilled').length}
							<li class="relative flex gap-3">
								<!-- The rail: a circle per stage, joined to the next. `last:hidden` on the line so
						     the final stage does not trail one into nothing. -->
								<div class="flex flex-col items-center">
									<span
										class="flex size-7 shrink-0 items-center justify-center rounded-full border-2 text-xs font-semibold
								{st.state === 'done'
											? 'border-brand bg-brand text-brand-foreground'
											: st.state === 'current'
												? 'border-brand text-brand'
												: 'border-border text-muted-foreground'}"
									>
										{#if st.state === 'done'}<CheckIcon class="size-4" />{:else}{i + 1}{/if}
									</span>
									{#if i < visibleStages.length - 1}
										<span
											class="w-0.5 flex-1 {st.state === 'done' ? 'bg-brand' : 'bg-border'}"
											aria-hidden="true"
										></span>
									{/if}
								</div>

								<div class="min-w-0 flex-1 pb-1">
									<details open={st.key === timeline.openStage}>
										<summary
											class="hover:text-brand -my-1 flex cursor-pointer list-none items-center gap-2 py-1"
										>
											<span
												class="text-sm font-semibold {st.state === 'todo'
													? 'text-muted-foreground'
													: ''}">{st.label}</span
											>
											<span class="text-muted-foreground text-xs">
												{st.done} of {st.steps.length} done
											</span>
											{#if st.waiting}
												<!-- Visible without opening the stage: the work here is waiting on a result,
												     not on anyone looking at this page. -->
												<StatusBadge tone="warning">
													<ClockIcon class="size-3" /> Waiting
												</StatusBadge>
											{:else if outstanding && st.state === 'current'}
												<StatusBadge tone="warning">{outstanding}</StatusBadge>
											{/if}
										</summary>
										{#if st.blockers.length}
											<!-- A hold is a reason ATTACHED TO THE STAGE it holds, not a card of its
										     own: "waiting on every owner's KYC" explains something here. -->
											{#each st.blockers as reason (reason)}
												<p
													class="border-warning/20 bg-warning/5 text-warning mt-2 flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs"
												>
													<LockIcon class="size-3.5 shrink-0" />
													<span>{reason}</span>
												</p>
											{/each}
										{/if}
										<ul class="mt-1 space-y-1">
											{#each groupStageSteps(st.steps) as group (group.key)}
												{@const first = group.items[0]}
												{@const progress = sectionProgress(group.items)}
												{#if first && group.items.length > 1}
													<li class="border-border/60 bg-muted/25 rounded-lg border">
														<div class="px-2.5 py-1.5">
															<details class="group min-w-0">
																<summary
																	class="flex cursor-pointer list-none items-start gap-2 [&::-webkit-details-marker]:hidden"
																>
																	<ChevronDownIcon
																		class="text-muted-foreground mt-0.5 size-4 shrink-0 -rotate-90 transition-transform group-open:rotate-0"
																		aria-hidden="true"
																	/>
																	<span class="min-w-0 flex-1">
																		<span class="flex flex-wrap items-baseline gap-x-2">
																			<span
																				class="text-sm font-medium {progress === 'Done'
																					? 'text-muted-foreground'
																					: ''}"
																			>
																				{group.kind === 'client_terms'
																					? stepSubjectLine(first.step, first.case)
																					: (first.step.title ?? stepLabel(first.step.step_type))}
																			</span>
																			<span
																				class="text-xs {progress === 'Done'
																					? 'text-success'
																					: 'text-muted-foreground'}"
																			>
																				{progress}
																			</span>
																		</span>
																		<span class="text-muted-foreground block truncate text-xs">
																			{#if group.kind === 'client_terms'}
																				{groupTitles(group.items)}
																				· {assignedTo(workOwners(group.items, firmOwner))}
																			{:else}
																				For {stepGroupSubjects(group.items)}
																				· {assignedTo(workOwners(group.items, firmOwner))}
																			{/if}
																		</span>
																	</span>
																</summary>
																{#if stepPurpose(first.step)}
																	<p class="text-muted-foreground mt-1 text-xs">
																		{stepPurpose(first.step)}
																	</p>
																{/if}
																<ul class="border-border/60 mt-1 space-y-0.5 border-t pt-1">
																	{#each group.items as item (item.case.uid + item.step.step_key + (item.step.participant_subject_id ?? ''))}
																		{@render stepRow(
																			item.case,
																			item.step,
																			group.kind !== 'client_terms',
																			group.kind !== 'client_terms',
																			true
																		)}
																	{/each}
																</ul>
															</details>
														</div>
													</li>
												{:else if first}
													{@render stepRow(first.case, first.step, false, true, false)}
												{/if}
											{/each}
										</ul>
									</details>
								</div>
							</li>
						{/each}
					</ol>

					{#if timeline.other.length}
						<!-- Work whose step type names no stage. Its own group rather than filed under a
				     guess — the stage vocabulary is onboarding's, and a firm can author anything. -->
						<details class="border-border mt-2 border-t pt-3">
							<summary
								class="text-muted-foreground cursor-pointer list-none text-xs hover:underline"
							>
								{timeline.other.length} other {timeline.other.length === 1 ? 'step' : 'steps'} in this
								journey
							</summary>
							<ul class="mt-2 space-y-2.5">
								{#each timeline.other as o (o.case.uid + o.step.step_key + (o.step.participant_subject_id ?? ''))}
									{@render stepRow(o.case, o.step, false, true, false)}
								{/each}
							</ul>
						</details>
					{/if}
				</CardContent>
			</Card>

			{#if tasks.length}
				<section>
					<SectionHeading title="Tasks" count={tasks.length} />
					<Card glass class="gap-0 overflow-hidden py-0">
						<DataTable {columns} rows={tasks} getKey={(t) => t.uid} fixed label="Journey tasks">
							{#snippet cell(t, column)}
								{#if column.key === 'state'}
									<span
										class="flex size-7 shrink-0 items-center justify-center rounded-full {t.status ===
										'done'
											? 'bg-success/10 text-success'
											: isOverdue(t)
												? 'bg-destructive/10 text-destructive'
												: 'bg-muted text-muted-foreground'}"
									>
										<ListChecksIcon class="size-3.5" />
									</span>
								{:else if column.key === 'title'}
									<span
										class="block min-w-0 truncate font-medium {isOpen(t)
											? ''
											: 'text-muted-foreground line-through'}"
									>
										{t.title}
									</span>
								{:else if column.key === 'assignee_name'}
									<span class="block min-w-0 truncate">
										<span class="text-muted-foreground text-xs">
											{ASSIGNEE_KIND_LABELS[t.assignee_kind]}:
										</span>
										{t.assignee_name ?? '—'}
									</span>
								{:else if column.key === 'due' && t.due_date && isOpen(t)}
									<span
										class="text-xs whitespace-nowrap {isOverdue(t)
											? 'text-destructive font-medium'
											: 'text-muted-foreground'}"
									>
										{dueLabel(t)}
									</span>
								{:else if column.key === 'actions' && actions && isOpen(t)}
									<form
										method="POST"
										action={actions.completeTask}
										class="flex justify-end"
										use:enhanceForm={withToast('Task completed', {
											pending: (v) => (pendingTask = v ? t.uid : null)
										})}
									>
										<input type="hidden" name="task_uid" value={t.uid} />
										<Button
											type="submit"
											variant="outline"
											size="sm"
											disabled={pendingTask === t.uid}
										>
											<CheckIcon class="mr-1 size-3.5" />
											{pendingTask === t.uid ? 'Working…' : 'Done'}
										</Button>
									</form>
								{/if}
							{/snippet}
						</DataTable>
					</Card>
				</section>
			{/if}
		</div>

		<aside class="space-y-5 [grid-area:rail] xl:sticky xl:top-4">
			<Card glass size="sm">
				<CardContent>
					<p class="text-xs font-medium">Progress</p>
					<p class="mt-0.5 text-xl font-semibold tracking-tight tabular-nums">{prog.pct}%</p>
					<div class="bg-muted mt-2 h-1 w-full overflow-hidden rounded-full">
						<div
							class="bg-brand h-full rounded-full transition-all"
							style="width:{prog.pct}%"
						></div>
					</div>
					<p class="text-muted-foreground mt-1.5 text-xs tabular-nums">
						{prog.done} of {prog.total} steps complete
					</p>
					{#if clientLine}
						<p class="text-muted-foreground mt-3 flex items-start gap-1.5 text-xs">
							<UserIcon class="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
							<span>{clientLine}</span>
						</p>
					{/if}
					<div class="border-border mt-4 space-y-3 border-t pt-3">
						<div>
							<p class="text-muted-foreground text-xs">Adviser</p>
							<p class="mt-0.5 text-sm">{ex.adviser_name ?? 'Unassigned'}</p>
						</div>
						<div>
							<p class="text-muted-foreground text-xs">Assigned to</p>
							{#if actions?.assign && staff.length}
								<form
									method="POST"
									action={actions.assign}
									class="mt-1"
									bind:this={assignForm}
									use:enhanceForm={withToast('Assignment updated')}
								>
									<input type="hidden" name="user_id" value={assigneePick} />
									<SelectMenu
										id="journey-assignee"
										items={assigneeOptions}
										value={assigneePick}
										size="sm"
										class="w-full"
										onValueChange={(next) => saveAssignee(next)}
									/>
								</form>
								<p class="text-muted-foreground mt-1 text-xs">
									Staff tasks on this journey go to this person.
								</p>
							{:else}
								<p class="mt-0.5 text-sm">{ex.assignee_name ?? 'Unassigned'}</p>
							{/if}
						</div>
					</div>
				</CardContent>
			</Card>
			{#if activity.length}
				<Card glass>
					<CardContent>
						<SectionHeading title="Activity" count={activity.length} class="mb-1" />
						{#each activityDays as day (day.label)}
							<p class="text-muted-foreground mt-3 text-xs font-medium">{day.label}</p>
							<ul>
								{#each day.entries as entry (entry.id)}
									{@const Icon = activityIcon(entry.kind)}
									<li class="border-border/60 flex gap-3 border-b py-2.5 last:border-0">
										<span
											class="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-lg {entry.attention
												? 'bg-destructive/10 text-destructive'
												: 'bg-muted text-muted-foreground'}"
										>
											<Icon class="size-4" />
										</span>
										<div class="min-w-0 flex-1">
											<div class="flex items-baseline justify-between gap-3">
												<p class="text-sm leading-5">{entry.title}</p>
												<time
													class="text-muted-foreground shrink-0 text-xs tabular-nums"
													datetime={entry.at}>{clock(entry.at)}</time
												>
											</div>
											{#if entry.subject || entry.actor}
												<dl class="mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
													{#if entry.subject}
														<div class="flex gap-1">
															<dt class="text-muted-foreground">For</dt>
															<dd class="text-foreground">{entry.subject}</dd>
														</div>
													{/if}
													{#if entry.actor}
														<div class="flex gap-1">
															<dt class="text-muted-foreground">By</dt>
															<dd class="text-foreground">{entry.actor}</dd>
														</div>
													{/if}
												</dl>
											{/if}
											{#if entry.note}
												<p class="text-muted-foreground mt-0.5 text-xs">{entry.note}</p>
											{/if}
										</div>
									</li>
								{/each}
							</ul>
						{/each}
					</CardContent>
				</Card>
			{/if}
		</aside>
	</div>
</div>

<ConfirmDialog
	bind:open={approveOpen}
	title="Approve this account opening?"
	description="The opening has been waiting for approval. Approving it sends it on."
	confirmLabel="Approve"
	onconfirm={() => approveForm?.requestSubmit()}
/>

<!-- Record a compliance decision with its rationale (the decider is stamped server-side). -->
<Dialog.Root bind:open={decisionOpen}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>
				{decisionOutcome === 'passed'
					? `Record ${decisionLabel} as passed?`
					: `Refer ${decisionLabel}?`}
			</Dialog.Title>
			<Dialog.Description>
				{decisionOutcome === 'passed'
					? 'This records the outcome and lets the journey move on.'
					: 'This holds the journey for manual review.'}
			</Dialog.Description>
		</Dialog.Header>
		<div class="space-y-1.5">
			<Label for="decision-note"
				>Rationale <span class="text-muted-foreground">(optional)</span></Label
			>
			<Textarea
				id="decision-note"
				rows={3}
				bind:value={decisionNote}
				placeholder="What you checked and why you reached this outcome…"
			/>
			<p class="text-muted-foreground text-xs">
				Recorded against the step and its audit trail, so the decision has a reason on file.
			</p>
		</div>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (decisionOpen = false)}>Cancel</Button>
			<Button
				variant={decisionOutcome === 'referred' ? 'destructive' : 'default'}
				onclick={recordDecision}
			>
				{decisionOutcome === 'passed' ? 'Record pass' : 'Refer'}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<!-- Record a terms acceptance taken with the client present. The acknowledgement is filed for real
	 and attributed to the adviser, so the trail distinguishes it from one the client clicked — and
	 it is recorded against the signed copy, which is required: without it this is one person's
	 word for another's agreement. The API refuses the acceptance with no file. -->
{#if actions}
	<Dialog.Root bind:open={attestOpen}>
		<Dialog.Content class="sm:max-w-lg">
			<Dialog.Header>
				<Dialog.Title>Record acceptance of the {attestLabel}?</Dialog.Title>
				<Dialog.Description>
					{attestWho
						? `This files the signed ${attestLabel} against ${attestWho}'s record, attributed to you.`
						: `This files the signed ${attestLabel} against the client’s record, attributed to you.`}
				</Dialog.Description>
			</Dialog.Header>
			<form
				method="POST"
				enctype="multipart/form-data"
				action={actions.acknowledge}
				bind:this={attestFormEl}
				class="space-y-4"
				use:enhanceForm={withToast('Acceptance recorded', {
					pending: (v) => (pendingStep = v ? (attestTarget?.key ?? null) : null),
					onSuccess: () => ((attestOpen = false), (attestFiles = [])),
					reset: false
				})}
			>
				<input type="hidden" name="case_uid" value={attestTarget?.caseUid ?? ''} />
				<input type="hidden" name="step_key" value={attestTarget?.stepKey ?? ''} />
				{#if attestTarget?.participant != null}
					<input type="hidden" name="participant_contact_id" value={attestTarget.participant} />
				{/if}
				<input type="hidden" name="note" value={attestNote} />
				{#if links.termsCopy && attestTarget}
					<!-- The copy comes BEFORE the signature: the client can only sign what they were
					     handed. A new tab, because the print sheet is a document of its own and this
					     dialog should still be here when they come back with the scan. -->
					<div
						class="border-border bg-muted/30 flex items-start justify-between gap-3 rounded-lg border p-3"
					>
						<p class="text-muted-foreground text-xs leading-5">
							Need a copy for the client to sign? Print the {attestLabel} first, then upload the signed
							copy below.
						</p>
						<Button
							href={links.termsCopy(
								attestTarget.caseUid,
								attestTarget.stepKey,
								attestTarget.participant
							)}
							target="_blank"
							rel="noopener"
							variant="outline"
							size="sm"
							class="shrink-0"
						>
							<PrinterIcon class="mr-1 size-3.5" />
							Print a copy
						</Button>
					</div>
				{/if}
				<div class="space-y-1.5">
					<p class="text-sm font-medium">Signed copy</p>
					<FileDropzone
						name="file"
						bind:files={attestFiles}
						title="Upload the signed copy"
						accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.tif,.tiff,.bmp,.heic,.heif,.avif"
						formats="A PDF scan or a photograph"
					/>
					<p class="text-muted-foreground text-xs">
						A scan or photograph of the {attestLabel} the client signed. The acceptance is recorded against
						this file and it is filed on the client's record.
					</p>
				</div>
				<div class="space-y-1.5">
					<Label for="attest-note"
						>How it was taken <span class="text-muted-foreground">(optional)</span></Label
					>
					<Textarea
						id="attest-note"
						rows={2}
						bind:value={attestNote}
						placeholder="e.g. signed in the 14 July meeting"
					/>
					<p class="text-muted-foreground text-xs">
						Only do this where the client has actually signed. Otherwise send them the declaration
						and let them accept it themselves.
					</p>
				</div>
			</form>
			<Dialog.Footer>
				<Button variant="ghost" onclick={() => (attestOpen = false)}>Cancel</Button>
				<Button
					disabled={!attestFiles.length ||
						(attestTarget != null && pendingStep === attestTarget.key)}
					onclick={() => attestFormEl?.requestSubmit()}
				>
					{attestTarget != null && pendingStep === attestTarget.key
						? 'Recording…'
						: 'Record acceptance'}
				</Button>
			</Dialog.Footer>
		</Dialog.Content>
	</Dialog.Root>
{/if}

<!-- Confirm the adviser-owned fact-find before access is handed to the client. -->
<Dialog.Root bind:open={declarationOpen}>
	<Dialog.Content class="sm:max-w-lg">
		<Dialog.Header>
			<Dialog.Title>Declare and send to the client?</Dialog.Title>
			<Dialog.Description>
				This locks the adviser forms and sends each eligible client their invitation to set up
				portal access.
			</Dialog.Description>
		</Dialog.Header>
		<div class="border-border flex items-start gap-3 rounded-lg border p-3">
			<Checkbox
				id="adviser-declaration"
				checked={declarationConfirmed}
				onCheckedChange={(value) => (declarationConfirmed = value === true)}
			/>
			<Label for="adviser-declaration" class="text-sm leading-5">
				I confirm that the information entered is correct, complete and not misleading.
			</Label>
		</div>
		<p class="text-muted-foreground text-xs">
			Each eligible client is invited to set up portal access.
		</p>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (declarationOpen = false)}>Go back</Button>
			<Button disabled={!declarationConfirmed} onclick={recordDeclaration}>Confirm and send</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<!-- Stop the journey: withdraws outstanding work and frees the client to start again. -->
{#if actions}
	<Dialog.Root bind:open={cancelOpen}>
		<Dialog.Content class="sm:max-w-lg">
			<Dialog.Header>
				<Dialog.Title>Stop this journey?</Dialog.Title>
				<Dialog.Description>
					Outstanding steps are cancelled, the client's requests are withdrawn and the journey's
					open tasks are closed. Completed work is kept. The client can then be started on a fresh
					journey.
				</Dialog.Description>
			</Dialog.Header>
			<form
				method="POST"
				action={actions.cancel}
				bind:this={cancelFormEl}
				class="space-y-1.5"
				use:enhanceForm={withToast('Journey stopped', {
					pending: (v) => (cancelling = v),
					onSuccess: () => (cancelOpen = false),
					reset: false
				})}
			>
				<Label for="cancel-reason"
					>Reason <span class="text-muted-foreground">(optional)</span></Label
				>
				<Textarea
					id="cancel-reason"
					name="reason"
					rows={2}
					bind:value={cancelReason}
					placeholder="e.g. started against the wrong client"
				/>
			</form>
			<Dialog.Footer>
				<Button variant="ghost" onclick={() => (cancelOpen = false)}>Keep it running</Button>
				<Button
					variant="destructive"
					disabled={cancelling}
					onclick={() => cancelFormEl?.requestSubmit()}
				>
					{cancelling ? 'Stopping…' : 'Stop journey'}
				</Button>
			</Dialog.Footer>
		</Dialog.Content>
	</Dialog.Root>
{/if}

<!-- ONE step row. Every step either offers its real action or says why it has none — a button that
	 silently does nothing is worse than an explained absence. Skipped steps never reach here:
	 `orderedSteps` drops them, because a step a condition ruled out describes the FLOW rather than
	 this journey. Cancelled is different and IS shown — it happened, and was withdrawn. -->
{#snippet stepRow(
	c: AppCase,
	s: AppStep,
	subjectFirst: boolean,
	showSubject: boolean,
	inGroup: boolean
)}
	{@const key = stepPendingKey(c, s)}
	{@const act = stepAction(s, canManage, c.status, ex.forms_amendable === true)}
	{@const reason = reasonLine(s)}
	{@const blocked = awaitingLine(s.awaiting, c.steps, stepTitles)}
	{@const who = stepSubjectLine(s, c)}
	{@const purpose = stepPurpose(s)}
	{@const handoff = handoffNotice(s)}
	{@const current = next != null && stepAnchor(c.uid, s) === next.anchor}
	{@const fillHref =
		act.kind === 'fill' || act.kind === 'amend'
			? links.fillStep(c.uid, s.step_key, s.participant_subject_id)
			: null}
	<li
		class="flex items-start gap-2 scroll-mt-24 rounded-lg px-1.5 py-1 {current
			? 'bg-brand/10 ring-brand/25 ring-1'
			: ''}"
		id={stepAnchor(c.uid, s)}
	>
		<span
			class="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full {STEP_CIRCLE[
				s.status
			] ?? 'bg-muted text-muted-foreground'}"
		>
			{#if stepDone(s)}<CheckIcon class="size-3.5" />
			{:else if s.status === 'failed'}<XIcon class="size-3.5" />
			{:else if s.status === 'waiting'}<ClockIcon class="size-3.5" />
			{:else if s.status === 'blocked' || s.awaiting?.length}<LockIcon class="size-3.5" />
			{:else}<CircleIcon class="size-2.5" />{/if}
		</span>
		<div class="min-w-0 flex-1">
			<div class="flex flex-wrap items-baseline gap-x-2 gap-y-1">
				{#if fillHref}
					<a
						href={fillHref}
						class="text-xs hover:underline {current ? 'font-medium' : ''} {stepDone(s)
							? 'text-muted-foreground'
							: ''}"
					>
						{subjectFirst ? who : (s.title ?? stepLabel(s.step_type))}
					</a>
				{:else}
					<p class="text-xs {stepDone(s) ? 'text-muted-foreground' : current ? 'font-medium' : ''}">
						{subjectFirst ? who : (s.title ?? stepLabel(s.step_type))}
					</p>
				{/if}
				{#if stepDone(s)}
					<span class="text-success text-xs">Done</span>
				{:else if s.status === 'pending' && !blocked && !reason}
					<span class="text-muted-foreground text-xs">Not started</span>
				{/if}
			</div>
			{#if (showSubject && !subjectFirst) || !inGroup || (purpose && !subjectFirst && !blocked && !reason)}
				<p class="text-muted-foreground text-xs">
					{#if showSubject && !subjectFirst}For {who}{/if}
					{#if showSubject && !subjectFirst && !inGroup}<span aria-hidden="true"> · </span>{/if}
					{#if !inGroup}
						{assignedTo(stepOwnerLabel(s, firmOwner, s.participant_name ?? c.subject_name))}
					{/if}
					{#if purpose && !subjectFirst && !blocked && !reason}
						{#if (showSubject && !subjectFirst) || !inGroup}<span aria-hidden="true"> · </span>{/if}
						{purpose}
					{/if}
				</p>
			{/if}
			{#if blocked}
				<p class="text-muted-foreground text-xs">{blocked}</p>
			{/if}
			{#if reason}
				<p class="text-muted-foreground text-xs">{reason}</p>
			{/if}
			{#if s.identity_provider_error}
				<!-- Graphene operations only: the API sends this to an operator in a firm's workspace,
				     never to the firm. -->
				<p class="text-muted-foreground text-xs">
					<span class="text-foreground font-medium">ComplyCube refused the last upload</span>
					{#if s.identity_provider_error.at}({when(s.identity_provider_error.at)}){/if}:
					{identityProviderErrorLine(s.identity_provider_error)}
				</p>
			{/if}
			{#if handoff}
				<p class="text-warning-strong mt-1 flex items-start gap-1.5 text-xs">
					<PlugZapIcon class="mt-0.5 size-3.5 shrink-0" />
					<span>{handoff}</span>
				</p>
			{/if}
			{#if s.note}
				<p class="text-muted-foreground text-xs italic">“{s.note}”</p>
			{/if}
			{#if s.document_uid}
				<!-- On a terms step this is the SIGNED COPY the adviser recorded the acceptance against
				     — the proof that the client agreed — so it is named as such rather than by file. -->
				<a href={links.document(s.document_uid)} class="text-brand text-xs hover:underline">
					{#if s.step_type === 'accept_terms'}
						Signed copy
						{#if s.document_name}<span class="text-muted-foreground">· {s.document_name}</span>{/if}
					{:else}
						{s.document_name ?? 'Generated document'}
					{/if}
					{#if s.document_status && s.document_status !== 'ready'}
						<span class="text-muted-foreground">· {humanise(s.document_status)}</span>
					{/if}
				</a>
			{/if}
			{#if s.answers && Object.keys(s.answers).length}
				{@const sections = answerSections(s.answers, s.answer_labels ?? {})}
				{@const n = sections.reduce((total, section) => total + section.rows.length, 0)}
				{#if n}
					<details class="mt-1.5">
						<summary
							class="text-muted-foreground hover:text-foreground cursor-pointer text-xs select-none"
						>
							{`${n} ${n === 1 ? 'answer' : 'answers'}`}
						</summary>
						<div class="bg-muted/40 mt-1.5 rounded-md px-3 py-1">
							{#each sections as section, si (`section-${si}`)}
								{#if section.heading}
									<p
										class="text-muted-foreground text-[11px] font-medium tracking-wide {si > 0
											? 'border-border/50 mt-1.5 border-t pt-2'
											: 'pt-1'}"
									>
										{section.heading}
									</p>
								{/if}
								<dl class="grid sm:grid-cols-2 sm:gap-x-8">
									{#each section.rows as row, i (`${row.key}:${i}`)}
										<div
											class="border-border/40 grid grid-cols-[8.5rem_minmax(0,1fr)] items-baseline gap-x-3 border-b py-1.5"
										>
											<dt class="text-muted-foreground text-xs leading-5">{row.label}</dt>
											<dd class="min-w-0 truncate text-xs leading-5" title={row.value}>
												{row.value}
											</dd>
										</div>
									{/each}
								</dl>
							{/each}
						</div>
					</details>
				{/if}
			{/if}
			<!-- Gated on the RECORDED evidence, not on `identity_choice`. That key is written when a
			     client picks the manual route or an adviser REQUIRES one at kickoff, and not when the
			     flow's own provider is already manual — which is the default wherever no digital
			     provider is configured. So the documents were raised and the panel that uploads them
			     was hidden, leaving a step with nothing on it. The documents ARE the check: it
			     completes on its own when the second one lands. -->
			{#if s.identity_documents?.length}
				<div class="mt-3 space-y-2">
					<p class="text-xs font-medium">Identity evidence</p>
					{#if s.identity_documents.some((d) => d.status === 'pending')}
						<p class="text-muted-foreground text-xs">
							{actions?.identityDocument
								? 'Both are needed; the check completes when the second one is received. Upload them here if the client has handed them over in person.'
								: 'Both are needed; the check completes when the second one is received.'}
						</p>
					{/if}
					<div class="grid items-start gap-3 sm:grid-cols-2">
						{#each s.identity_documents as document (document.item_uid)}
							<div class="rounded-md border p-3">
								<div class="flex items-start justify-between gap-3">
									<div class="min-w-0">
										<p class="text-xs font-medium">{document.title}</p>
										<p class="text-muted-foreground text-xs">{document.guidance}</p>
									</div>
									{#if document.status !== 'pending'}
										{#if document.document_uid}
											<a
												href={links.document(document.document_uid)}
												class="text-brand shrink-0 text-xs hover:underline"
											>
												View document
											</a>
										{:else}
											<span class="text-success shrink-0 text-xs">Received</span>
										{/if}
									{/if}
								</div>
								{#if document.status === 'pending' && actions?.identityDocument}
									<IdentityEvidenceUpload
										item={document}
										action={actions.identityDocument}
										caseUid={c.uid}
										stepKey={s.step_key}
										participantContactId={s.participant_subject_id}
										enhance={enhanceForm}
									/>
								{/if}
							</div>
						{/each}
					</div>
				</div>
			{/if}
		</div>
		{#if act.kind === 'amend' && fillHref}
			<!-- Back into a completed form, until declare-and-send. The form page itself is what
			     saves the amendment; this row only has to offer the way in. -->
			<Button href={fillHref} variant="outline" size="sm" class="shrink-0">
				<PencilIcon class="mr-1 size-3.5" />
				Amend
			</Button>
		{:else if actions?.identityManual && act.kind === 'provide'}
			<!-- The client's photos are one way. The documents are already the other, and the adviser
			     can file them from here when the client has handed them over. -->
			<form
				method="POST"
				action={actions.identityManual}
				use:enhanceForm={withToast('Documents requested', {
					pending: (v) => (pendingStep = v ? key : null)
				})}
			>
				<input type="hidden" name="case_uid" value={c.uid} />
				<input type="hidden" name="step_key" value={s.step_key} />
				{#if s.participant_subject_id}
					<input type="hidden" name="participant_contact_id" value={s.participant_subject_id} />
				{/if}
				<Button type="submit" variant="outline" size="sm" disabled={pendingStep === key}>
					{pendingStep === key ? 'Preparing…' : 'Provide documents'}
				</Button>
			</form>
		{:else if act.kind === 'provide'}
			<span class="text-muted-foreground/70 mt-0.5 shrink-0 text-xs">
				Completes when the identity check does
			</span>
		{:else if actions && act.kind === 'retry'}
			<form
				method="POST"
				action={actions.retryStep}
				use:enhanceForm={withToast('Retrying', {
					pending: (v) => (pendingStep = v ? key : null)
				})}
			>
				<input type="hidden" name="case_uid" value={c.uid} />
				<input type="hidden" name="step_key" value={s.step_key} />
				{#if s.participant_subject_id}
					<input type="hidden" name="participant_contact_id" value={s.participant_subject_id} />
				{/if}
				<Button type="submit" variant="outline" size="sm" disabled={pendingStep === key}>
					<RotateIcon class="mr-1 size-3.5" />
					{pendingStep === key ? 'Retrying…' : 'Retry'}
				</Button>
			</form>
		{:else if actions && act.kind === 'attest'}
			<!-- The form lives in the dialog: recording an acceptance means uploading the signed copy,
			     and a file cannot be filled into a row's hidden input the way a note can. -->
			<Button
				type="button"
				variant="outline"
				size="sm"
				class="shrink-0"
				disabled={pendingStep === key}
				onclick={() => openAttest(c, s, (s.title ?? stepLabel(s.step_type)).toLowerCase(), who)}
			>
				<FileCheckIcon class="mr-1 size-3.5" />
				{pendingStep === key ? 'Recording…' : 'Record acceptance'}
			</Button>
		{:else if actions?.approveRun && act.kind === 'approve' && s.action_run_uid}
			<form
				method="POST"
				action={actions.approveRun}
				use:enhanceForm={withToast('Approved', {
					pending: (v) => (pendingStep = v ? key : null),
					reset: false
				})}
			>
				<input type="hidden" name="run_uid" value={s.action_run_uid} />
				<Button
					type="button"
					size="sm"
					disabled={pendingStep === key}
					onclick={(e: MouseEvent) => {
						approveForm = (e.currentTarget as HTMLButtonElement).form;
						approveOpen = true;
					}}
				>
					{pendingStep === key ? 'Approving…' : 'Approve'}
				</Button>
			</form>
		{:else if actions && (act.kind === 'decide' || act.kind === 'complete' || act.kind === 'handoff')}
			<form
				method="POST"
				action={actions.action}
				use:enhanceForm={withToast('Recorded', {
					pending: (v) => (pendingStep = v ? key : null),
					reset: false
				})}
				class="flex shrink-0 gap-1.5"
			>
				<input type="hidden" name="case_uid" value={c.uid} />
				<input type="hidden" name="step_key" value={s.step_key} />
				<input type="hidden" name="note" value="" />
				{#if s.step_key === 'declaration'}
					<input type="hidden" name="adviser_confirmed" value="false" />
				{/if}
				{#if s.participant_subject_id}
					<input type="hidden" name="participant_contact_id" value={s.participant_subject_id} />
				{/if}
				{#if act.kind === 'decide'}
					<Button
						type="submit"
						name="outcome"
						value="passed"
						size="sm"
						disabled={pendingStep === key}
						onclick={(e: MouseEvent) =>
							openDecision(e, 'passed', (s.title ?? stepLabel(s.step_type)).toLowerCase())}
					>
						{pendingStep === key ? 'Recording…' : 'Pass'}
					</Button>
					<Button
						type="submit"
						name="outcome"
						value="referred"
						size="sm"
						variant="outline"
						disabled={pendingStep === key}
						onclick={(e: MouseEvent) =>
							openDecision(e, 'referred', (s.title ?? stepLabel(s.step_type)).toLowerCase())}
					>
						Refer
					</Button>
				{:else}
					<Button
						type="submit"
						name="outcome"
						value="passed"
						size="sm"
						disabled={pendingStep === key}
						onclick={s.step_key === 'declaration' ? openDeclaration : undefined}
					>
						{pendingStep === key
							? 'Recording…'
							: act.kind === 'handoff'
								? 'Record hand-off'
								: s.step_key === 'declaration'
									? 'Declare and send'
									: 'Complete'}
					</Button>
				{/if}
			</form>
		{:else if act.kind === 'none' && (act.reason ?? act.note)}
			<span class="text-muted-foreground/70 mt-0.5 shrink-0 text-xs">{act.reason ?? act.note}</span>
		{/if}
	</li>
{/snippet}
