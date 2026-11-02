<script module lang="ts">
	import type { FilterDef } from '../book/listing/filter-panel.svelte';

	/** A pure function so it can be pinned in a test without rendering the panel, which lives
	 *  behind a popover shut at SSR. */
	export function registryFilterDefs(input: {
		categoryOptions: FilterDef['options'];
	}): FilterDef[] {
		return [{ key: 'category', label: 'Category', options: input.categoryOptions }];
	}
</script>

<script lang="ts">
	// The authorable notification-rule builder — the events list is the whole surface: built-in
	// defaults + the tenant's own rules, each row carrying inline channel/mandatory/enabled switches.
	// "Add notification" / Edit open a side SHEET so the list stays visible behind. Shared by the
	// admin surface (per tenant) and, in firm mode, the platform firm surface. Data + persistence
	// stay app-side: the app injects `onSave`/`onDelete` (which POST to its proxies) and passes the
	// catalogue + rules as typed props (the book-surface idiom).
	import { untrack } from 'svelte';
	import { cn } from '../../utils.js';
	import { sentenceCase } from '../../sentence-case.js';
	import { Button } from '../ui/button/index.js';
	import { Input } from '../ui/input/index.js';
	import { Textarea } from '../ui/textarea/index.js';
	import { Label } from '../ui/label/index.js';
	import { Switch } from '../ui/switch/index.js';
	import { Badge } from '../ui/badge/index.js';
	import { Card, CardContent } from '../ui/card/index.js';
	import { DataTable, type DataTableColumn, type DataTableSort } from '../ui/data-table/index.js';
	import { RecordId } from '../ui/record-id/index.js';
	import EmptyState from '../ui/empty-state/empty-state.svelte';
	import { ListingToolbar, matches } from '../book/listing/index.js';
	import * as Dialog from '../ui/dialog/index.js';
	import * as Collapsible from '../ui/collapsible/index.js';
	import { ConfirmDialog } from '../ui/confirm-dialog/index.js';
	import PlusIcon from '@lucide/svelte/icons/plus';
	import Trash2Icon from '@lucide/svelte/icons/trash-2';
	import XIcon from '@lucide/svelte/icons/x';
	import PencilIcon from '@lucide/svelte/icons/pencil';
	import ChevronDownIcon from '@lucide/svelte/icons/chevron-down';
	import SparklesIcon from '@lucide/svelte/icons/sparkles';
	import BellIcon from '@lucide/svelte/icons/bell';
	import SearchXIcon from '@lucide/svelte/icons/search-x';
	import UserRoundIcon from '@lucide/svelte/icons/user-round';
	import BriefcaseIcon from '@lucide/svelte/icons/briefcase';
	import Settings2Icon from '@lucide/svelte/icons/settings-2';
	import HistoryIcon from '@lucide/svelte/icons/history';
	import CheckIcon from '@lucide/svelte/icons/check';
	import TriangleAlertIcon from '@lucide/svelte/icons/triangle-alert';
	import RuleHistoryDialog from './rule-history-dialog.svelte';
	import {
		audienceKind,
		categoryMeta,
		configuredMeta,
		configuredState,
		resourceCategory,
		stateLabel,
		toneClasses,
		triggersFrom
	} from './describe.js';
	import { filterParams, groupParams, tokensIn, unknownTokens } from './merge-picker.js';
	import { WRAPPER_CODE } from './rule-types.js';
	import type {
		CatalogueParam,
		CatalogueTrigger,
		EmailTemplateOption,
		NotificationRule,
		RegistryEvent,
		RuleDraft
	} from './rule-types.js';

	let {
		rules = [],
		catalogue = [],
		emailTemplates = [],
		canManage = true,
		firmMode = false,
		emailAvailable = true,
		saving = false,
		onSave,
		onSaveMany,
		onDelete,
		onLoadDefaults,
		historyUrl,
		groupBy = 'audience',
		events = [],
		automationsHref,
		activityHref,
		class: className
	}: {
		rules?: NotificationRule[];
		catalogue?: CatalogueTrigger[];
		/** The email templates a rule may render — the "Email template" dropdown options. */
		emailTemplates?: EmailTemplateOption[];
		canManage?: boolean;
		/** Firm posture: hide the ops-only guardrails; only `firm_editable` rules are editable. */
		firmMode?: boolean;
		emailAvailable?: boolean;
		saving?: boolean;
		/** Create/edit one rule (the Add/Edit sheet). */
		onSave?: (draft: RuleDraft) => Promise<void> | void;
		/** Persist a batch of inline toggle edits in one round-trip (the Save button). */
		onSaveMany?: (drafts: RuleDraft[]) => Promise<void> | void;
		onDelete?: (uid: string) => Promise<void> | void;
		/** Seed the shipped built-in defaults as real rules (the "Load defaults" action). When absent
		 *  (e.g. a firm surface), no button is shown — the list stays whatever it actually holds. */
		onLoadDefaults?: () => Promise<void> | void;
		/** Builds the change-history endpoint for a rule uid. Present ⇒ a History action per row that
		 *  opens the audit dialog; absent ⇒ no history affordance. */
		historyUrl?: (uid: string) => string;
		/**
		 * How the list is grouped, and therefore what it IS.
		 *
		 * `audience` (the default, and what the admin surface keeps) is the rule builder: two
		 * sections, For advisers and For clients, listing the tenant's own stored rules.
		 *
		 * `event` is the EVENT REGISTRY: a DataTable (one row per catalogue pair), and the
		 * shipped built-ins rendered as rows rather than left invisible. The difference matters
		 * because a firm receiving the shipped `document.shared` notice has nothing stored — under
		 * `audience` its registry reads as "nothing configured" while clients are being emailed.
		 *
		 * One component rather than two sharing an extracted dialog: this is the same list of
		 * catalogue pairs grouped two ways, and `displayRows` already walks the catalogue to build
		 * it. Splitting would have moved the whole editor out from under three source-scanning
		 * tests to gain nothing the flag does not.
		 */
		groupBy?: 'audience' | 'event';
		/**
		 * The registry's own rows — every declared event with its state. Supplied INSTEAD of
		 * `catalogue` in `event` mode: the registry lists all 207 events, most of which have no
		 * catalogue trigger at all, so a `CatalogueTrigger[]` cannot express them. The triggers are
		 * read back off the assignable ones, so the editor is unchanged.
		 */
		events?: RegistryEvent[];
		/** Where an automation count links to. Absent ⇒ the count is shown but not linked. */
		automationsHref?: string;
		/** Where a send count links to — the Activity feed, filtered to this event. */
		activityHref?: (eventTypes: string[]) => string;
		class?: string;
	} = $props();

	// The change-history dialog (who edited a rule, and what) — opened per row from the History action.
	let historyOpen = $state(false);
	let historyTarget = $state<DisplayRow | null>(null);
	// Who LAST changed a rule: the version scope if any, else derived from who first made it. Drives
	// the "Graphene" badge so it reflects the latest editor, not merely the original author.
	const lastScope = (r: NotificationRule) =>
		r.last_editor_scope ?? (r.authored_by === 'firm' ? 'firm' : 'operations');

	// Busy state for the Load-defaults action (its own, so it doesn't borrow the save spinner).
	let loadingDefaults = $state(false);
	async function loadDefaults() {
		if (!onLoadDefaults) return;
		loadingDefaults = true;
		try {
			await onLoadDefaults();
		} finally {
			loadingDefaults = false;
		}
	}

	// The general wrapper's code — the fallback template when a rule chooses none.

	/** A rule's email design, by name. On the LISTING and not only inside the edit sheet: "does this
	 *  send an email, and which one" is the question the consolidation was about, and a row that shows
	 *  an Email switch and nothing else cannot answer it without opening every rule in turn. */
	function templateLabel(code: string | null): string | null {
		if (!code) return null;
		return emailTemplates.find((t) => t.code === code)?.label ?? code;
	}

	/**
	 * The editor holds a SET of drafts, not one.
	 *
	 * An event may notify several audiences — the client and their adviser off the same send — and
	 * the registry opens on the EVENT, so the popup has to be able to show and add all of them.
	 * Rules are keyed `(trigger, audience)`, so "several notifications on one event" means one per
	 * audience and no more: the strip in the dialog is that trigger's audience list, which is why it
	 * can offer "add" and "edit" as the same control.
	 *
	 * `activeIx` is the one on screen. Drafts are only ever appended while the dialog is open, so
	 * the parallel arrays stay aligned; `snapshots` is what makes Save write only what changed
	 * rather than re-patching every rule on the event (each patch writes a version).
	 */
	let drafts = $state<RuleDraft[]>([]);
	let draftIsNew = $state<boolean[]>([]);
	let snapshots = $state<string[]>([]);
	let activeIx = $state(0);
	const draft = $derived(drafts[activeIx] ?? null);
	const isNew = $derived(draftIsNew[activeIx] ?? false);
	// A RULE SUPPLIES NO VARIABLES. It used to offer name/value rows here, and every one of them was
	// dropped at send, so the rows validated, saved and re-displayed while the token rendered blank in
	// the client's inbox. The send path no longer reads them at all. The COLUMN and the router field
	// stay — dropping them needs a migration and they reach the rule VERSION history — so a value set
	// by a direct API call is still stored and still ignored.

	function closeEditor() {
		drafts = [];
		draftIsNew = [];
		snapshots = [];
		activeIx = 0;
	}

	/** Open the editor on one draft — the builder's door, and the registry's when nothing is attached. */
	function openOne(d: RuleDraft, fresh: boolean) {
		drafts = [d];
		draftIsNew = [fresh];
		snapshots = [JSON.stringify(d)];
		activeIx = 0;
		paramSearch = '';
		syncLinkChoice();
	}
	// The variables-panel search box; reset whenever the editor (re)opens so a stale query from the
	// last edit never hides a new trigger's variables.
	let paramSearch = $state('');

	// Delete is a hard delete (no archive) behind a confirmation — a rule removed by accident is a
	// silent gap in what a firm's people get told. `toDelete` holds the row the dialog is asking about.
	let confirmOpen = $state(false);
	let toDelete = $state<DisplayRow | null>(null);

	/** In registry mode the catalogue is derived from the events that have a trigger — the editor,
	 *  the merge-field picker and the link targets all read this, unchanged. */
	const effectiveCatalogue = $derived(
		groupBy === 'event' ? (triggersFrom(events) as CatalogueTrigger[]) : catalogue
	);
	const triggerByKey = $derived(new Map(effectiveCatalogue.map((t) => [t.key, t])));
	const audiencesFor = (key: string) => triggerByKey.get(key)?.audiences ?? [];
	const triggerLabel = (key: string) => triggerByKey.get(key)?.label ?? key;
	// The trigger the Event box currently names - its description and the audit event types it sits
	// on are shown beneath the box, since the box itself now carries the machine name.
	const selectedTrigger = $derived(draft ? triggerByKey.get(draft.trigger_key) : undefined);
	const audienceLabel = (r: { trigger_key: string; audience: string }) =>
		audiencesFor(r.trigger_key).find((a) => a.key === r.audience)?.label ?? r.audience;
	const category = (key: string) => triggerByKey.get(key)?.category ?? 'tasks';
	const paramsFor = (key: string) => triggerByKey.get(key)?.params ?? [];

	// Merge fields COPY to the clipboard — the author pastes the `{key}` token wherever they want it
	// (more intuitive than an insert-into-last-focused-field, and it works the same whether the copy
	// lives in this dialog or the separate email editor). `copied` flags the last one for feedback.
	let copied = $state<string | null>(null);
	let copiedTimer: ReturnType<typeof setTimeout> | null = null;
	function copyToken(p: CatalogueParam) {
		const token = `{${p.key}}`;
		void navigator.clipboard?.writeText(token);
		copied = p.key;
		if (copiedTimer) clearTimeout(copiedTimer);
		copiedTimer = setTimeout(() => (copied = null), 1200);
	}

	// A local preview of rendered copy — mirrors app/notifications/render.py: substitute `{dotted}`
	// tokens from the trigger's example values, drop the unresolved ones and tidy the spaces they
	// leave. Kept deliberately in step with the backend (same drop-and-tidy rule).
	function previewOf(template: string, params: CatalogueParam[]): string {
		if (!template) return '';
		const examples = new Map(params.map((p) => [p.key, p.example]));
		const out = template.replace(
			/\{([a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*)\}/gi,
			(_m, key) => examples.get(key) ?? ''
		);
		return out
			.replace(/[ \t]{2,}/g, ' ')
			.split('\n')
			.map((l) => l.trim())
			.join('\n')
			.replace(/^\n+|\n+$/g, '');
	}

	const canEdit = (r: NotificationRule) => canManage && (!firmMode || r.firm_editable);
	// A firm may edit an ops rule it's allowed to, but only ever DELETE its own (the API 403s a
	// firm deleting a Graphene-authored rule) — so don't offer a Delete that would fail.
	const canDelete = (r: NotificationRule) => canManage && (!firmMode || r.authored_by === 'firm');

	// What the list shows: the catalogue's BUILT-IN defaults (the baseline a tenant gets out of the
	// box) followed by the tenant's own rules. A stored rule for the same trigger+audience supersedes
	// its built-in, so nothing is shown twice.
	type DisplayRow = {
		key: string;
		builtin: boolean;
		/**
		 * Registry rows only. `event` is the raw event type the row is about, and `state`/`reason`
		 * carry whether anything can be attached to it — a `ready` or `blocked` row has NO rule and
		 * no controls, it exists to say the event happens and why it cannot notify anyone yet.
		 */
		event?: string;
		state?: 'assignable' | 'ready' | 'declarable' | 'actor_only';
		reason?: string | null;
		automations?: number;
		emails30d?: number;
		/** Registry rows: the event's human label, and every rule attached to it. ONE row per event
		 *  — an event with two notifications is still one line, with two chips. */
		eventLabel?: string;
		resource?: string;
		/** Registry table: first row of its category run, so the cell renders once per group. */
		showCategory?: boolean;
		attached?: DisplayRow[];
		trigger_key: string;
		audience: string;
		title: string;
		// Carried from the catalogue default (built-in) or the stored rule, so toggling a built-in and
		// saving an override keeps its shipped copy instead of blanking body/link.
		body: string;
		link: string;
		in_app: boolean;
		email: boolean;
		mandatory: boolean;
		enabled: boolean;
		// The email template this row renders (an email.templates code); null → the general wrapper.
		email_code: string | null;
		rule?: NotificationRule;
	};
	const fromRule = (r: NotificationRule): DisplayRow => ({
		key: `rule:${r.uid}`,
		builtin: false,
		trigger_key: r.trigger_key,
		audience: r.audience,
		title: r.title,
		body: r.body ?? '',
		link: r.link ?? '',
		in_app: r.default_in_app,
		email: r.default_email,
		mandatory: r.mandatory,
		enabled: r.enabled,
		email_code: r.email_code,
		rule: r
	});
	/**
	 * A catalogue built-in as a row — what the firm receives today with nothing stored.
	 *
	 * `rule` is deliberately absent, which is what every affordance keyed on a uid already tests:
	 * no History (there are no versions of something never authored) and no Delete (there is
	 * nothing to delete — switching it off is an override, and that is a save).
	 */
	const fromDefault = (
		t: CatalogueTrigger,
		a: CatalogueTrigger['audiences'][number]
	): DisplayRow => ({
		key: `default:${t.key}:${a.key}`,
		builtin: true,
		trigger_key: t.key,
		audience: a.key,
		title: a.default_title,
		body: a.default_body ?? '',
		link: a.default_link ?? '',
		in_app: a.default_in_app,
		email: a.default_email,
		// Not on the TS catalogue shape; a built-in's mandatory flag only matters once stored, and
		// claiming `true` here would grey out a switch the firm can in fact change.
		mandatory: false,
		enabled: true,
		email_code: a.default_email_code ?? null
	});
	// The list shows ONLY the tenant's OWN stored rules — nothing is fabricated, so an empty tenant
	// reads as empty (the shipped defaults are materialised on demand via "Load defaults", never
	// shown as phantom rows). Ordered by the STABLE catalogue order (trigger, then audience) so a row
	// keeps its slot as it's edited; any stored rule with no catalogue slot (defensive) trails after.
	const displayRows = $derived.by(() => {
		const byPair = new Map(rules.map((r) => [`${r.trigger_key}:${r.audience}`, r]));
		const emitted = new Set<string>();
		const out: DisplayRow[] = [];

		// THE REGISTRY: one row per declared EVENT, in the endpoint's order. An assignable event
		// expands to its trigger's audience pairs (a stored rule, or the shipped built-in shown as a
		// default); a `ready` or `blocked` event is one flat row carrying its reason. Listing the
		// unavailable ones is the point — "this happens and cannot notify anyone, because…" is an
		// answer, where a missing row is not.
		if (groupBy === 'event') {
			for (const ev of events) {
				const trigger = ev.trigger;
				// EVERY event gets exactly one row. The previous shape emitted a row per
				// trigger-audience pair, so an assignable event with nothing stored and no built-in
				// audience produced NO row and vanished from a list whose whole purpose is to show
				// every event — which emptied the "can be assigned" section entirely.
				const attached: DisplayRow[] = [];
				if (trigger !== null) {
					for (const a of trigger.audiences) {
						const pair = `${trigger.key}:${a.key}`;
						const stored = byPair.get(pair);
						if (stored) {
							attached.push(fromRule(stored));
							emitted.add(pair);
						} else if (a.builtin) {
							attached.push(fromDefault(trigger, a));
						}
					}
				}
				out.push({
					key: `event:${ev.event_type}`,
					builtin: false,
					trigger_key: trigger?.key ?? ev.event_type,
					audience: '',
					title: '',
					body: '',
					link: '',
					in_app: false,
					email: false,
					mandatory: false,
					enabled: true,
					email_code: null,
					event: ev.event_type,
					eventLabel: trigger?.label,
					resource: ev.resource,
					state: ev.state,
					reason: ev.unavailable_reason,
					automations: ev.automation_count,
					emails30d: ev.email_count_30d,
					attached
				});
			}
			return out;
		}

		for (const t of catalogue) {
			for (const a of t.audiences) {
				const pair = `${t.key}:${a.key}`;
				// AUDIENCE mode only — the registry returned above. Stored rules alone here: the
				// built-ins are shown as defaults on the REGISTRY, and the admin builder keeps its
				// original behaviour of listing nothing it has not been given.
				const stored = byPair.get(pair);
				if (stored) {
					out.push(fromRule(stored));
					emitted.add(pair);
				}
			}
		}
		for (const r of rules) {
			if (!emitted.has(`${r.trigger_key}:${r.audience}`)) out.push(fromRule(r));
		}
		return out;
	});

	// A mutable mirror of the derived list so the inline switches are OPTIMISTIC: a switch bound
	// straight to the derived value snaps back while the async save is in flight (the "Mandatory
	// flips back on" bug). Resynced whenever the loader passes fresh rules/catalogue — i.e. after a
	// save reloads, the row reflects what actually persisted.
	//
	// Seeded from `displayRows` at creation (NOT `[]`): the $effect below doesn't run during SSR and
	// runs only AFTER the first client render, so an empty initial value made the "No notifications"
	// empty state flash before the rows appeared. Seeding it means the very first render already has
	// the rows.
	// `untrack`: the initial capture is deliberate — the $effect below owns re-syncing, so this must
	// NOT become a reactive read (which is also what silences `state_referenced_locally`).
	/**
	 * The rows that carry inline switches — the unit a channel/mandatory flag belongs to.
	 *
	 * In the builder that is every row. In the REGISTRY a row is an EVENT, which has no channels of
	 * its own: the switches belong to the notifications ATTACHED to it, and an event may carry
	 * several. So the mirror below tracks the attached rows, not the 207 event rows — which is also
	 * what keeps the copy cheap.
	 */
	const switchRows = $derived(
		groupBy === 'event' ? displayRows.flatMap((r) => r.attached ?? []) : displayRows
	);

	/**
	 * A mutable MIRROR of `switchRows`, so the inline switches are optimistic — bound straight to
	 * the derived value they snap back while the async save is in flight (the "Mandatory flips back
	 * on" bug). Resynced whenever the loader passes fresh rules, so after a save the row reflects
	 * what actually persisted.
	 */
	// Seeded unconditionally: this runs ONCE, and reading a prop here would capture only its first
	// value (svelte's `state_referenced_locally`). Seeded from the rows rather than `[]` because the
	// $effect below does not run during SSR and runs only AFTER the first client render, so an empty
	// initial value flashed the empty state before the rows appeared.
	let localRows = $state<DisplayRow[]>(untrack(() => switchRows).map((r) => ({ ...r })));
	// Keys the user has inline-toggled since the last Save/Discard. On an UNRELATED reload (e.g. a
	// sheet save's invalidateAll re-runs this $effect) their pending edits are carried over, so only
	// an explicit Discard/Save clears them — never a save of some other row.
	const touched = new Set<string>();
	$effect(() => {
		const fresh = switchRows.map((r) => ({ ...r }));
		if (touched.size) {
			untrack(() => {
				const prev = new Map(localRows.map((r) => [r.key, r]));
				for (const row of fresh) {
					const p = touched.has(row.key) ? prev.get(row.key) : undefined;
					if (!p) continue;
					row.in_app = p.in_app;
					row.email = p.email;
					row.mandatory = p.mandatory;
					row.enabled = p.enabled;
				}
			});
		}
		localRows = fresh;
	});

	/**
	 * What the LIST reads: the registry lists events (whose switches live on `attached`, mirrored
	 * separately and read through `switched()`), the builder lists the mirrored rows themselves.
	 */
	const rows = $derived(groupBy === 'event' ? displayRows : localRows);

	/**
	 * The mirrored copy of an attached notification — what a registry switch is bound to.
	 *
	 * Falls back to the derived row, so a first render before the mirror has synced still draws the
	 * loaded values rather than nothing.
	 */
	const localByKey = $derived(new Map(localRows.map((r) => [r.key, r])));
	const switched = (row: DisplayRow) => localByKey.get(row.key) ?? row;

	// Both audience sections start expanded; each header collapses its own.
	/**
	 * Which sections the reader has COLLAPSED. Empty means every section is open.
	 *
	 * An override map rather than a seeded one, because the BUILDER's section keys are the two
	 * audiences (`adviser`/`client`). The registry is a DataTable and has none. A fixed initial
	 * map used to miss keys — and `Collapsible.Root`'s `open` is `$bindable(false)`, so binding
	 * undefined to it throws `props_invalid_value` and takes the whole page down. Controlled with
	 * `open`/`onOpenChange` instead of `bind:`, so the value passed is always a boolean.
	 */
	let collapsed = $state<Record<string, boolean>>({});
	const sectionOpen = (key: string) => !collapsed[key];
	// The raw-event-metadata disclosure in the editor's variables panel (collapsed by default).
	let rawOpen = $state(false);
	// Which view of that disclosure: a representative EVENT payload (what actually fires — a compact
	// audit.actions row) or the full TRIGGER DEFINITION (the builder config, not sent with the event).
	let rawTab = $state<'event' | 'definition'>('event');
	// Example subject shape per subject kind, for the representative event payload (the real event
	// carries the subject under metadata.subject).
	const SUBJECT_EXAMPLE: Record<string, Record<string, number>> = {
		contact: { contact_id: 128 },
		account: { account_id: 4210 },
		task: { task_id: 77 },
		application: { application_id: 12 }
	};

	// The click destination is chosen from a friendly list, not typed as a raw path:
	//   • a NAMED TARGET — the specific item the event is about ("The document", "The account",
	//     "The client", "The task"), stored as a token path (/documents/{document.uid}) the consumer
	//     renders per-notification into the real URL. Filtered to those valid for the audience — a
	//     client's portal has /documents/{id} and /accounts/{id} but no /clients or task view.
	//   • a named SECTION — a fixed in-portal page.
	//   • "Custom path…" — the escape hatch (the old free-text field).
	const PAGE_OPTIONS_STAFF = [
		{ path: '/clients', label: 'Clients' },
		{ path: '/accounts', label: 'Accounts' },
		{ path: '/transactions', label: 'Transactions' },
		{ path: '/documents', label: 'Documents' },
		{ path: '/tasks', label: 'Tasks' },
		{ path: '/journeys', label: 'Journeys' },
		{ path: '/contributions', label: 'Contributions' },
		{ path: '/transfers', label: 'Transfers' }
	];
	const PAGE_OPTIONS_CLIENT = [
		{ path: '/documents', label: 'Documents' },
		{ path: '/accounts', label: 'Accounts' }
	];
	const CUSTOM = '__custom__';

	let linkChoice = $state<string>(CUSTOM);
	const draftTrigger = $derived(draft ? triggerByKey.get(draft.trigger_key) : undefined);
	// Specific-item destinations valid for THIS audience (client-only targets drop for staff and
	// vice-versa).
	const linkTargets = $derived(
		(draftTrigger?.link_targets ?? []).filter((t) => draft?.audience !== 'client' || t.client)
	);
	const pageOptions = $derived(
		draft?.audience === 'client' ? PAGE_OPTIONS_CLIENT : PAGE_OPTIONS_STAFF
	);
	// A one-line explanation when a specific target is chosen.
	const selectedTarget = $derived(linkTargets.find((t) => t.href === linkChoice));

	function applicableTargets(audience: string): { href: string }[] {
		const all = draft ? (triggerByKey.get(draft.trigger_key)?.link_targets ?? []) : [];
		return all.filter((t) => audience !== 'client' || t.client);
	}
	// Which option a stored link corresponds to: a known target href or page path → that option; a
	// blank link → the first specific target if the event has one (so a new rule defaults to the
	// item); anything else → custom.
	function linkChoiceFor(link: string, audience: string): string {
		const targets = applicableTargets(audience);
		if (link && targets.some((t) => t.href === link)) return link;
		const opts = audience === 'client' ? PAGE_OPTIONS_CLIENT : PAGE_OPTIONS_STAFF;
		if (link && opts.some((o) => o.path === link)) return link;
		if (link) return CUSTOM;
		return targets[0]?.href ?? CUSTOM;
	}
	function syncLinkChoice() {
		if (!draft) return;
		linkChoice = linkChoiceFor(draft.link ?? '', draft.audience);
		applyLinkChoice(); // reconcile the draft's link with the shown option (blank → first target)
	}
	// Push the chosen destination onto the draft: a named target/page sets that path; custom keeps
	// whatever's in the box for the author to edit.
	function applyLinkChoice() {
		if (!draft) return;
		if (linkChoice !== CUSTOM) draft.link = linkChoice;
	}

	/**
	 * Open the editor for a NEW notification on a named event.
	 *
	 * The registry's "Assign" — the event is already chosen, so the editor opens on it rather than
	 * on whatever happens to be first in the catalogue. `startNew` is the builder's door, where
	 * there is no event in hand and the author picks one.
	 */
	function startNewFor(triggerKey: string) {
		startNew(triggerByKey.get(triggerKey));
	}

	/** A blank draft on one trigger+audience, prefilled from that audience's catalogue defaults. */
	function blankDraft(
		t: CatalogueTrigger | undefined,
		a: CatalogueTrigger['audiences'][number] | undefined
	): RuleDraft {
		return {
			trigger_key: t?.key ?? '',
			audience: a?.key ?? '',
			label: t?.label ?? '',
			title: a?.default_title ?? '',
			body: a?.default_body ?? '',
			link: a?.default_link ?? '',
			default_in_app: a?.default_in_app ?? true,
			default_email: a?.default_email ?? false,
			mandatory: false,
			firm_editable: true,
			enabled: true,
			email_code: a?.default_email_code ?? null
		};
	}

	function startNew(preset?: CatalogueTrigger) {
		const t = preset ?? effectiveCatalogue[0];
		const a = t?.audiences[0];
		openOne(blankDraft(t, a), true);
	}

	// Open the editor for ANY row. A built-in default has no stored rule, so there's no uid — saving
	// creates a firm OVERRIDE for that trigger+audience; a stored rule is edited in place. Either way
	// the trigger+audience are fixed (isNew = false locks the selects).
	/** The draft for an existing (or built-in) notification, reading the MIRROR so a pending switch
	    the author has already flicked is what the editor opens on. */
	function draftForEdit(row: DisplayRow): RuleDraft {
		const live = switched(row);
		return {
			uid: row.rule?.uid,
			trigger_key: row.trigger_key,
			audience: row.audience,
			label: row.rule?.label ?? triggerLabel(row.trigger_key),
			title: row.title,
			body: row.body,
			link: row.link,
			default_in_app: live.in_app,
			default_email: live.email,
			mandatory: live.mandatory,
			firm_editable: row.rule?.firm_editable ?? true,
			enabled: live.enabled,
			email_code: row.email_code
		};
	}

	function startEditRow(row: DisplayRow) {
		openOne(draftForEdit(row), false);
	}

	/**
	 * Open the editor on a whole EVENT — every notification attached to it, with the one clicked
	 * active.
	 *
	 * This is what the registry's Edit does, and why the control is not "Add another": adding is one
	 * of the things this popup does, not a second door beside it.
	 */
	function startEditEvent(row: DisplayRow, focus?: DisplayRow) {
		const attached = row.attached ?? [];
		if (!attached.length) {
			startNewFor(row.trigger_key);
			return;
		}
		const ds = attached.map(draftForEdit);
		drafts = ds;
		draftIsNew = ds.map(() => false);
		snapshots = ds.map((d) => JSON.stringify(d));
		const ix = focus ? attached.findIndex((a) => a.key === focus.key) : 0;
		activeIx = ix < 0 ? 0 : ix;
		paramSearch = '';
		syncLinkChoice();
	}

	/**
	 * Move to another notification on the open event.
	 *
	 * The drafts are held, not re-read, so unsaved edits to the one being left survive the move and
	 * are still there on the way back — and Save then writes every one that changed.
	 */
	function selectDraft(ix: number) {
		if (ix === activeIx || !drafts[ix]) return;
		activeIx = ix;
		paramSearch = '';
		syncLinkChoice();
	}

	/**
	 * Remove one of the open event's notifications.
	 *
	 * Three cases, and they are genuinely different: a STORED rule is a real deletion and goes
	 * through the same confirmation the list uses (a rule removed by accident is a silent gap in
	 * what a firm's people get told); an UNSAVED draft has never existed anywhere and is simply
	 * dropped; a BUILT-IN with no stored override cannot be deleted at all — it ships with the
	 * platform — so dropping its draft reverts it to the shipped default, which is what discarding
	 * an override means. Switching it off is the way to silence one, and that is a switch on the line.
	 */
	function removeDraft(ix: number) {
		const d = drafts[ix];
		if (!d) return;
		if (d.uid) {
			toDelete = {
				key: `rule:${d.uid}`,
				builtin: false,
				trigger_key: d.trigger_key,
				audience: d.audience,
				title: d.title,
				body: d.body ?? '',
				link: d.link ?? '',
				in_app: d.default_in_app,
				email: d.default_email,
				mandatory: d.mandatory,
				enabled: d.enabled,
				email_code: d.email_code,
				rule: rules.find((r) => r.uid === d.uid)
			};
			confirmOpen = true;
			return;
		}
		dropDraft(ix);
	}

	/** Drop a draft from the open set, keeping the parallel arrays and the active index aligned. */
	function dropDraft(ix: number) {
		if (drafts.length <= 1) {
			closeEditor();
			return;
		}
		drafts = drafts.filter((_, i) => i !== ix);
		draftIsNew = draftIsNew.filter((_, i) => i !== ix);
		snapshots = snapshots.filter((_, i) => i !== ix);
		activeIx = Math.min(activeIx > ix ? activeIx - 1 : activeIx, drafts.length - 1);
		paramSearch = '';
		syncLinkChoice();
	}

	/** Add a notification for an audience the open event does not notify yet. */
	function addAudience(audienceKey: string) {
		const cur = drafts[activeIx];
		if (!cur) return;
		const t = triggerByKey.get(cur.trigger_key);
		const a = t?.audiences.find((x) => x.key === audienceKey);
		if (!t || !a) return;
		drafts = [...drafts, blankDraft(t, a)];
		draftIsNew = [...draftIsNew, true];
		snapshots = [...snapshots, ''];
		activeIx = drafts.length - 1;
		paramSearch = '';
		syncLinkChoice();
	}

	/**
	 * The strip at the top of the editor: every audience the open event can notify, whether it is
	 * already notified, and which draft it is.
	 *
	 * Only meaningful in the registry (the builder opens on a single rule with no event context), so
	 * it is empty there and the strip does not render.
	 */
	const editorAudiences = $derived.by(() => {
		const cur = drafts[activeIx];
		if (!cur || groupBy !== 'event') return [];
		const t = triggerByKey.get(cur.trigger_key);
		if (!t || t.audiences.length < 2) return [];
		return t.audiences.map((a) => ({
			key: a.key,
			label: a.label,
			ix: drafts.findIndex((d) => d.audience === a.key)
		}));
	});

	// When the trigger/audience changes on a NEW rule, prefill copy + channels from the audience's
	// catalogue defaults (an edit never re-defaults — it keeps the authored values).
	function applyAudienceDefaults() {
		if (!draft || !isNew) return;
		const auds = audiencesFor(draft.trigger_key);
		const a = auds.find((x) => x.key === draft!.audience) ?? auds[0];
		if (!a) return;
		draft.audience = a.key;
		draft.label = triggerLabel(draft.trigger_key);
		draft.title = a.default_title;
		draft.body = a.default_body;
		draft.link = a.default_link ?? '';
		draft.default_in_app = a.default_in_app;
		draft.default_email = a.default_email;
		draft.email_code = a.default_email_code ?? null;
		syncLinkChoice();
	}

	/**
	 * Persist the editor.
	 *
	 * Only the drafts that are NEW or actually changed are written: a patch writes a version, so
	 * re-saving an untouched sibling would put a no-op entry in its history every time its neighbour
	 * was edited. One changed draft takes the single-save path, which is what every caller already
	 * implements; several go through `onSaveMany` when the caller offers it and one at a time when
	 * it does not.
	 */
	async function save() {
		if (!draft) return;
		const changed = drafts.filter(
			(d, i) => draftIsNew[i] || JSON.stringify(d) !== (snapshots[i] ?? '')
		);
		if (changed.length > 1 && onSaveMany) await onSaveMany(changed);
		else for (const d of changed) await onSave?.(d);
		closeEditor();
	}

	// Whether a row's inline switches are editable: a stored rule follows canEdit; a built-in default
	// is editable whenever the caller can manage (flipping it writes an override rule below).
	const rowEditable = (row: DisplayRow) => (row.rule ? canEdit(row.rule) : canManage);

	// Flip one channel/flag on a row — LOCAL only (no auto-save). The switch is bound to the local
	// row so it moves at once; nothing persists until the Save button (batched via onSaveMany), so a
	// stray toggle is undoable and a burst of edits is one round-trip.
	function toggle(
		row: DisplayRow,
		field: 'in_app' | 'email' | 'mandatory' | 'enabled',
		v: boolean
	) {
		if (!rowEditable(row)) return;
		row[field] = v;
		touched.add(row.key);
	}

	// A row is dirty when its local channels/flags differ from the loaded value (matched by key).
	/**
	 * The sections the list is divided into.
	 *
	 * Registry mode groups by the event's own CATEGORY, in first-seen catalogue order — so the
	 * order is the catalogue's one decision rather than an alphabetical accident, and a category a
	 * firm's catalogue does not carry never renders an empty heading. The builder keeps its two
	 * fixed audience sections.
	 */
	/**
	 * The registry is a TABLE, flat — no sections. With 207 rows the grouping was three collapsible
	 * boxes a reader had to open to find anything, and the same explanatory sentence repeated down
	 * every one of them. Columns scan; sections did not. `DataTable` is the same frame the Activity
	 * listing and the book listings use, so this reads as part of the platform.
	 */
	// The registry's filters. Client-side on purpose: all 207 rows are already in hand (one request,
	// no paging), so narrowing them is instant and a round trip would be slower and no more correct.
	// Contrast the Activity feed, which filters SERVER-side because it pages.
	let registryCategory = $state<string[]>([]);
	let registryQuery = $state('');
	let registrySort = $state<DataTableSort | null>({
		key: 'category',
		dir: 'asc'
	});
	/**
	 * Whether the events that can reach nobody are shown.
	 *
	 * Off by default: 172 of 207 events name no client, account or task, so listing them all put a
	 * wall of "—" between a reader and the 35 they can act on. Hidden rather than removed, because
	 * "this event happens and cannot notify anyone" is still an answer — and the note below says how
	 * many are hidden, so the list never quietly claims to be everything.
	 */
	let showUnreachable = $state(false);

	/**
	 * States that cannot reach a recipient TODAY, and are therefore hidden unless asked for.
	 *
	 * `declarable` belongs here with `actor_only`, even though it is one declaration from working:
	 * the event records a subject but `automation._SUBJECTS` names no source for it, so every
	 * resolver returns nobody. From the reader's side those two are the same answer — "this cannot
	 * notify anyone" — and separating them on screen only made 14 rows look assignable when they
	 * are not. The distinction is kept in the state and in docs/notifications.md; it just is not
	 * what the default view is for.
	 */
	/**
	 * The registry's switch columns, declared once so the header, the aria-label and the field
	 * they write cannot drift apart. The HEADER names them — repeating the word beside every
	 * track is what packed Notifies. `enabled` is deliberately not among them: a notification
	 * that should not fire is one to remove, and an off switch beside the two channels reads as
	 * a third channel. It is still honoured on a stored rule, and the dot reports it.
	 */
	const CHANNEL_SWITCHES = [
		{
			field: 'in_app',
			label: 'In-app',
			hint: 'Shown in the recipient’s notifications'
		},
		{ field: 'email', label: 'Email', hint: 'Emailed to the recipient' },
		{
			field: 'mandatory',
			label: 'Mandatory',
			hint: 'The recipient cannot turn this notification off'
		}
	] as const;

	/** The dot's key, in the order the states escalate. */
	const DOT_LEGEND = [
		{ state: 'live' as const, text: 'Notifying' },
		// KEPT even though the line no longer offers an off switch: a stored rule can still be
		// disabled (by an operator, or by a rule authored before that switch went), and
		// `configuredState` still renders it amber. A colour on screen with nothing naming it is
		// the "present but unlabelled, which reads as not offered at all" failure this legend is
		// here to avoid — so it goes when the state does, not before.
		{ state: 'off' as const, text: 'Configured, switched off' },
		{ state: 'none' as const, text: 'Nothing configured' }
	];

	const UNREACHABLE_STATES = new Set(['declarable', 'actor_only']);
	const unreachable = $derived(rows.filter((r) => UNREACHABLE_STATES.has(r.state ?? 'assignable')));

	/**
	 * The rows the table would show with everything EXCEPT the category filter applied.
	 *
	 * The category options come from HERE, not from every row: a category whose events are all
	 * hidden — Security, where every event is actor-only — would otherwise be offered and then
	 * select an empty table. And not from the final rows either, or choosing a category would
	 * shrink the options to that one category and strand the reader; that is the self-shrinking
	 * filter `audit_stream`'s facet window exists to avoid.
	 */
	const beforeCategory = $derived(
		rows.filter((row) => {
			if (UNREACHABLE_STATES.has(row.state ?? 'assignable') && !showUnreachable) return false;
			const term = registryQuery.trim().toLowerCase();
			if (!term) return true;
			// The event name and its human label — what a reader has in mind when they search.
			return (
				(row.event ?? '').toLowerCase().includes(term) ||
				(row.eventLabel ?? '').toLowerCase().includes(term)
			);
		})
	);

	const registryCategoryOptions = $derived(
		[...new Set(beforeCategory.map((r) => resourceCategory(r.resource ?? '')))]
			.sort()
			.map((value) => ({ value, label: value }))
	);

	const filterDefs = $derived(registryFilterDefs({ categoryOptions: registryCategoryOptions }));
	const registryBarValue = $derived<Record<string, string[]>>(
		registryCategory.length ? { category: registryCategory } : {}
	);
	function applyRegistryFilters(next: Record<string, string[]>): void {
		registryCategory = next.category ?? [];
	}

	const registryRows = $derived(
		beforeCategory.filter((r) => matches(registryCategory, resourceCategory(r.resource ?? '')))
	);

	const registryFiltered = $derived(registryCategory.length > 0 || registryQuery.trim() !== '');

	/** True when any visible row has figures — otherwise the Activity column is dead width. */
	const anyUsage = $derived(registryRows.some((r) => r.automations || r.emails30d));

	const registryColumns = $derived.by((): DataTableColumn<DisplayRow>[] => {
		const cols: DataTableColumn<DisplayRow>[] = [
			{
				key: 'event',
				header: 'Event',
				sortable: true,
				sortValue: (r) => (r.eventLabel ?? r.event ?? '').toLowerCase(),
				headerClass: 'w-[22%]',
				cellClass: 'align-top'
			},
			{
				key: 'category',
				header: 'Category',
				sortable: true,
				sortValue: (r) => resourceCategory(r.resource ?? ''),
				headerClass: 'w-[14%]',
				hideBelowWidth: 'md'
			},
			{
				key: 'notifies',
				header: 'Notifies',
				headerClass: anyUsage ? 'w-[22%]' : 'w-[28%]',
				cellClass: 'align-top'
			},
			...CHANNEL_SWITCHES.map((ch): DataTableColumn<DisplayRow> => ({
				key: ch.field,
				header: ch.label,
				align: 'center',
				headerClass: 'w-[6rem]',
				cellClass: 'align-top'
			}))
		];
		if (anyUsage) {
			cols.push({
				key: 'activity',
				header: 'Activity',
				sortable: true,
				align: 'right',
				defaultDir: 'desc',
				sortValue: (r) => (r.emails30d ?? 0) + (r.automations ?? 0),
				headerClass: 'w-[12%]'
			});
		}
		cols.push({
			key: 'actions',
			header: '',
			align: 'right',
			headerClass: 'w-[12%]',
			cellClass: 'align-top'
		});
		return cols;
	});

	/** Whether the header row has any button to show. An empty flex row still spends its margin,
	 *  and in registry mode both of its buttons are hidden. */
	const headerHasActions = $derived(
		canManage && ((!!onLoadDefaults && displayRows.length > 0) || groupBy !== 'event')
	);

	/** The BUILDER's two sections. The registry is a flat table and has none. */
	const sections = [
		{ key: 'adviser', label: 'For advisers' },
		{ key: 'client', label: 'For clients' }
	];

	const displayByKey = $derived(new Map(switchRows.map((r) => [r.key, r])));
	const dirtyRows = $derived(
		localRows.filter((r) => {
			const d = displayByKey.get(r.key);
			return (
				!!d &&
				(r.in_app !== d.in_app ||
					r.email !== d.email ||
					r.mandatory !== d.mandatory ||
					r.enabled !== d.enabled)
			);
		})
	);

	// The RuleDraft for a display row — a built-in carries no uid (⇒ create), a stored rule its uid
	// (⇒ patch). Deliberately omits the email DESIGN fields (email_html/subject/…): a plain save must
	// never blank a design it isn't editing (the API's exclude_none keeps them).
	function draftForRow(row: DisplayRow): RuleDraft {
		return {
			uid: row.rule?.uid,
			trigger_key: row.trigger_key,
			audience: row.audience,
			label: row.rule?.label ?? triggerLabel(row.trigger_key),
			title: row.title,
			body: row.body,
			link: row.link,
			default_in_app: row.in_app,
			default_email: row.email,
			mandatory: row.mandatory,
			firm_editable: row.rule?.firm_editable ?? true,
			enabled: row.enabled,
			email_code: row.email_code
		};
	}

	// Persist every changed row in one batch: a built-in becomes an override (no uid → create), a
	// stored rule is updated (uid → patch). One round-trip, one reload — which then resyncs localRows.
	async function saveChanges() {
		if (!dirtyRows.length) return;
		await onSaveMany?.(dirtyRows.map(draftForRow));
		// The batch is persisted; the reload above now reflects it, so drop the pending marks.
		touched.clear();
	}

	// Throw away pending inline edits — resync the local mirror to the loaded values.
	function discardChanges() {
		touched.clear();
		localRows = switchRows.map((r) => ({ ...r }));
	}
</script>

{#snippet loadDefaultsAction()}
	<Button size="sm" disabled={loadingDefaults} onclick={loadDefaults} class="gap-1.5">
		<SparklesIcon class="size-4" />
		{loadingDefaults ? 'Loading…' : 'Load defaults'}
	</Button>
{/snippet}

{#snippet noRegistryMatches()}
	<!-- "No events match these filters." stated the fact and stopped. The narrowing that traps
	     people is not one of the two filters: 172 of the 207 declared events reach nobody today and
	     are hidden behind `showUnreachable`, so a search for one of them finds nothing and the
	     reader cannot tell the event exists.
	     SO THE ACTION IS THE ONE THAT CAN TURN THE ANSWER AROUND, which is revealing them — not
	     Clear. An earlier cut of this offered Clear while promising the hidden events would appear,
	     and `clearRegistryFilters` sets `showUnreachable = false`: it HIDES exactly what the
	     sentence said it would show. Once they are already shown, Clear is the only narrowing left
	     to undo. -->
	<EmptyState
		icon={SearchXIcon}
		title="No events match these filters"
		description={showUnreachable
			? 'Try a different category or search term, or clear the filters to see every event.'
			: `Try a different category or search term. ${unreachable.length} events that can’t reach anyone yet are hidden from this list.`}
		action={showUnreachable ? clearRegistryFilters : revealUnreachable}
		compact
		chromeless
	/>
{/snippet}

{#snippet revealUnreachable()}
	<Button variant="outline" size="sm" onclick={() => (showUnreachable = true)}>
		Show the hidden events
	</Button>
{/snippet}

{#snippet noRegistryEvents()}
	<!-- NOT `chromeless` alongside `tone="warning"`: chromeless suppresses the container
	     (`empty-state.svelte:79`) and the container is where the warning is painted, so the two
	     together render a failure that looks exactly like an absence — the distinction this state
	     exists to draw, discarded by the prop meant to tidy it. -->
	<!-- Its own body says something has gone wrong, so it may not wear the neutral "nothing here"
	     treatment — that is the whole distinction `empty-state.svelte` draws. The registry is 207
	     platform-declared events; an empty one is never a legitimate state of this firm's data. -->
	<EmptyState
		tone="warning"
		icon={TriangleAlertIcon}
		title="The event registry couldn’t be read"
		description="Events are declared by the platform, so this list is never legitimately empty. Reload the page, and tell Graphene if it stays like this."
		compact
	/>
{/snippet}

{#snippet clearRegistryFilters()}
	<Button
		variant="outline"
		size="sm"
		onclick={() => {
			registryCategory = [];
			registryQuery = '';
			showUnreachable = false;
		}}>Clear filters</Button
	>
{/snippet}

<div class={cn('w-full', groupBy === 'event' && 'flex min-h-0 flex-1 flex-col', className)}>
	{#if headerHasActions}
		<div class="mb-3 flex justify-end gap-2">
			<!-- ONE Load defaults at a time. While the list is empty the EMPTY STATE owns the action — it
			     is the only thing on screen and it explains what defaults are — so a second button in the
			     header was two controls for one act, and a QA pass reported clicking "the" button and
			     nothing happening. Once there are rules the empty state is gone and this is the only door. -->
			{#if onLoadDefaults && displayRows.length}
				<Button
					variant="outline"
					size="sm"
					disabled={loadingDefaults}
					onclick={loadDefaults}
					class="gap-1.5"
				>
					<SparklesIcon class="size-4" />
					{loadingDefaults ? 'Loading…' : 'Load defaults'}
				</Button>
			{/if}
			{#if groupBy !== 'event'}
				<!-- The registry assigns from a ROW, so the event is always known. A free "Add" here
				     would open on whatever happened to be first in the catalogue. -->
				<Button size="sm" onclick={() => startNew()} class="gap-1.5">
					<PlusIcon class="size-4" /> Add notification
				</Button>
			{/if}
		</div>
	{/if}

	{#snippet registryWho(row: DisplayRow)}
		{@const canAssign = row.state === 'assignable'}
		{#if row.attached?.length}
			<!-- One LINE per attached notification. An event may carry several; this column is WHO,
			     and the channel columns to the right carry one switch per line, same order. -->
			<div class="-my-0.5">
				{#each row.attached as item (item.key)}
					{@const editable = canManage && rowEditable(item)}
					{@const kind = audienceKind(item.audience)}
					<div class="flex h-8 items-center">
						<button
							type="button"
							class="hover:text-foreground group flex min-w-0 items-center gap-1.5 text-left text-xs"
							disabled={!editable}
							onclick={() => startEditEvent(row, item)}
							title={item.title}
						>
							<!-- WHO this reaches, at a glance. Only `client` leaves the firm — a notice to a
							     client goes to a real customer and is the one to look twice at before
							     switching a channel on; adviser/assignee/actor are all "somebody here" and
							     three marks would say nothing. The icon is a mark BESIDE the words, never
							     instead of them: the label already names the audience. -->
							<span
								class={cn(
									'flex size-5 shrink-0 items-center justify-center rounded',
									kind === 'client'
										? 'bg-primary/10 text-primary'
										: 'bg-muted text-muted-foreground'
								)}
								title={kind === 'client' ? 'Goes to the client' : 'Goes to firm staff'}
							>
								{#if kind === 'client'}
									<UserRoundIcon class="size-3" />
								{:else}
									<BriefcaseIcon class="size-3" />
								{/if}
							</span>
							<span
								class={cn(
									'min-w-0 truncate underline-offset-2 group-hover:underline',
									kind === 'client' && 'font-medium'
								)}
							>
								{audienceLabel(item)}
							</span>
							{#if item.builtin}<span class="text-muted-foreground shrink-0">· default</span>{/if}
						</button>
					</div>
				{/each}
			</div>
		{:else if canAssign}
			<!-- States the ABSENCE plainly. "Nobody yet" read as a stray label; this says what is
			     true of the event — nothing is being sent when it fires. -->
			<span class="text-muted-foreground text-xs">Not notifying anyone</span>
		{:else}
			<!-- SHORT, and from `stateLabel` rather than a ternary here: a hardcoded pair drifted
			     the moment a third state existed. -->
			<span class="text-muted-foreground/70 text-xs">
				{stateLabel(row.state ?? 'assignable')}
			</span>
		{/if}
	{/snippet}

	{#snippet registrySwitch(row: DisplayRow, field: 'in_app' | 'email' | 'mandatory')}
		{@const ch = CHANNEL_SWITCHES.find((c) => c.field === field)!}
		{#if row.attached?.length}
			<div class="-my-0.5">
				{#each row.attached as item (item.key)}
					{@const live = switched(item)}
					{@const editable = canManage && rowEditable(item)}
					<div class="flex h-8 items-center justify-center">
						<Switch
							size="sm"
							checked={live[field]}
							disabled={!editable}
							title={ch.hint}
							onCheckedChange={(v) => toggle(live, field, v)}
							aria-label={`${audienceLabel(item)} — ${ch.label}`}
						/>
					</div>
				{/each}
			</div>
		{:else}
			<span class="text-muted-foreground text-xs">—</span>
		{/if}
	{/snippet}

	{#snippet rowItem(row: DisplayRow)}
		<!-- The audience-grouped BUILDER's row (the admin surface). The registry has its own
		     who/switch cells on the table. -->
		{@const meta = categoryMeta(category(row.trigger_key))}
		{@const Icon = meta.icon}
		{@const editable = rowEditable(row)}
		<div class="flex items-center gap-3 px-4 py-3" class:opacity-60={!row.enabled}>
			<span
				class={'flex size-8 shrink-0 items-center justify-center rounded-full ' +
					toneClasses(meta.tone)}
			>
				<Icon class="size-4" />
			</span>
			<div class="min-w-0 flex-1">
				<div class="flex items-center gap-2">
					<p class="truncate text-sm font-medium">{triggerLabel(row.trigger_key)}</p>
					<!-- This branch is the audience-grouped BUILDER only; the registry renders its own
						     row above, so nothing here needs to ask which grouping it is in. -->
					<Badge variant="outline" class="shrink-0 font-normal">{meta.label}</Badge>
					{#if firmMode && row.rule && lastScope(row.rule) === 'operations'}
						<!-- Last changed by Graphene on the firm's behalf (via the admin portal). Reflects the
						     LATEST editor (not just who first made it); the full trail is in History. -->
						<Badge
							variant="secondary"
							class="shrink-0 font-normal"
							title="Last changed by Graphene — see History"
						>
							Graphene
						</Badge>
					{/if}
				</div>
				<p class="text-muted-foreground truncate text-xs">→ {audienceLabel(row)} · {row.title}</p>
				{#if row.email}
					{@const design = templateLabel(row.email_code)}
					<!-- Only where an email is actually sent. The wrapper is NAMED rather than left implied:
					     a blank `email_code` means the generic design, which is a real answer and not an
					     absence — and it is the one an author is most likely to want to change. -->
					<p class="text-muted-foreground truncate text-xs">
						Email design: <span class="text-foreground">{design ?? 'General notification'}</span>
					</p>
				{/if}
			</div>
			<div class="flex shrink-0 items-center gap-5">
				<label class="flex flex-col items-center gap-1">
					<span class="text-muted-foreground text-[0.7rem]">Enabled</span>
					<Switch
						checked={row.enabled}
						disabled={!editable}
						onCheckedChange={(v) => toggle(row, 'enabled', v)}
						aria-label={`${triggerLabel(row.trigger_key)} enabled`}
					/>
				</label>
				<label class="flex flex-col items-center gap-1" class:opacity-40={!row.enabled}>
					<span class="text-muted-foreground text-[0.7rem]">In-app</span>
					<Switch
						checked={row.in_app}
						disabled={!editable || !row.enabled}
						onCheckedChange={(v) => toggle(row, 'in_app', v)}
						aria-label={`${triggerLabel(row.trigger_key)} in-app`}
					/>
				</label>
				<label
					class="flex flex-col items-center gap-1"
					class:opacity-40={!row.enabled || !emailAvailable}
				>
					<span class="text-muted-foreground text-[0.7rem]">Email</span>
					<Switch
						checked={row.email}
						disabled={!editable || !row.enabled || !emailAvailable}
						onCheckedChange={(v) => toggle(row, 'email', v)}
						aria-label={`${triggerLabel(row.trigger_key)} email`}
					/>
				</label>
				<label class="flex flex-col items-center gap-1" class:opacity-40={!row.enabled}>
					<span class="text-muted-foreground text-[0.7rem]">Mandatory</span>
					<Switch
						checked={row.mandatory}
						disabled={!editable || !row.enabled}
						onCheckedChange={(v) => toggle(row, 'mandatory', v)}
						aria-label={`${triggerLabel(row.trigger_key)} mandatory`}
					/>
				</label>
			</div>
			{#if canManage}
				<div class="flex shrink-0 items-center">
					{#if historyUrl && row.rule}
						<Button
							variant="ghost"
							size="icon"
							aria-label="Change history"
							title="Change history — who changed this, and what"
							onclick={() => {
								historyTarget = row;
								historyOpen = true;
							}}
						>
							<HistoryIcon class="size-4" />
						</Button>
					{/if}
					<Button
						variant="ghost"
						size="icon"
						aria-label="Edit"
						disabled={!editable}
						onclick={() => startEditRow(row)}
					>
						<PencilIcon class="size-4" />
					</Button>
					<Button
						variant="ghost"
						size="icon"
						aria-label="Delete"
						disabled={!row.rule || !canDelete(row.rule)}
						title={row.rule
							? undefined
							: 'Built-in default — edit to customise, or turn Enabled off'}
						onclick={() => {
							if (row.rule) {
								toDelete = row;
								confirmOpen = true;
							}
						}}
					>
						<Trash2Icon class="size-4" />
					</Button>
				</div>
			{/if}
		</div>
	{/snippet}

	{#if groupBy === 'event'}
		<!-- Toolbar and table share ONE glass card, the frame every other book listing uses. -->
		<Card glass class="flex min-w-0 flex-col md:min-h-0 md:flex-1">
			<CardContent class="flex min-w-0 flex-col gap-3 md:min-h-0 md:flex-1 md:overflow-hidden">
				<ListingToolbar
					class="shrink-0"
					bind:query={registryQuery}
					placeholder="Search events…"
					dirty={registryFiltered}
					onclear={() => {
						registryCategory = [];
						registryQuery = '';
						showUnreachable = false;
					}}
					{filterDefs}
					filterValue={registryBarValue}
					onfilters={applyRegistryFilters}
				/>

				{#if unreachable.length && !showUnreachable}
					<p class="text-muted-foreground shrink-0 text-xs">
						Showing only events a notification can reach someone from.
						<span class="text-foreground">{unreachable.length}</span>
						more are recorded but cannot resolve a recipient, so nobody can be notified of them.
						<Button
							type="button"
							variant="link"
							size="sm"
							class="h-auto px-0"
							onclick={() => (showUnreachable = true)}
						>
							Show them
						</Button>
					</p>
				{:else if showUnreachable}
					<p class="text-muted-foreground shrink-0 text-xs">
						Showing every event, including the
						<span class="text-foreground">{unreachable.length}</span>
						that cannot resolve a recipient.
						<Button
							type="button"
							variant="link"
							size="sm"
							class="h-auto px-0"
							onclick={() => (showUnreachable = false)}
						>
							Hide those
						</Button>
					</p>
				{/if}

				<p
					class="text-muted-foreground flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 text-xs"
				>
					{#each DOT_LEGEND as item (item.state)}
						{@const meta = configuredMeta(item.state)}
						<span class="inline-flex items-center gap-1.5">
							<span class={cn('size-2 rounded-full', meta.classes)} aria-hidden="true"></span>
							{item.text}
						</span>
					{/each}
				</p>

				<DataTable
					columns={registryColumns}
					rows={registryRows}
					bind:sort={registrySort}
					scroll
					fixed
					getKey={(r) => r.key}
					label="Notification events"
					empty={registryFiltered ? noRegistryMatches : noRegistryEvents}
				>
					{#snippet cell(row, column)}
						{@const canAssign = row.state === 'assignable'}
						{#if column.key === 'event'}
							{@const cfg = configuredMeta(configuredState((row.attached ?? []).map(switched)))}
							<div class="flex min-w-0 items-start gap-2">
								<span
									class={cn('mt-1.5 size-2 shrink-0 rounded-full', cfg.classes)}
									title={cfg.label}
									aria-hidden="true"
								></span>
								<div class="min-w-0">
									<span class="block truncate font-medium">
										{row.eventLabel ?? row.event}
									</span>
									{#if row.eventLabel}
										<RecordId value={row.event} />
									{/if}
								</div>
							</div>
						{:else if column.key === 'category'}
							<Badge variant="outline" class="font-normal"
								>{resourceCategory(row.resource ?? '')}</Badge
							>
						{:else if column.key === 'notifies'}
							{@render registryWho(row)}
						{:else if column.key === 'in_app' || column.key === 'email' || column.key === 'mandatory'}
							{@render registrySwitch(row, column.key)}
						{:else if column.key === 'activity'}
							<div class="text-muted-foreground/80 text-xs tabular-nums">
								{#if row.automations}
									<span class="block">
										{#if automationsHref}
											<a class="underline-offset-2 hover:underline" href={automationsHref}
												>{row.automations} auto</a
											>
										{:else}{row.automations} auto{/if}
									</span>
								{/if}
								{#if row.emails30d}
									<span class="block">
										{#if activityHref && row.event}
											<a class="underline-offset-2 hover:underline" href={activityHref([row.event])}
												>{row.emails30d} sent</a
											>
										{:else}{row.emails30d} sent{/if}
									</span>
								{/if}
							</div>
						{:else if column.key === 'actions'}
							{#if canAssign && canManage && onSave}
								<Button
									variant="outline"
									size="sm"
									class="gap-1.5"
									onclick={() => startEditEvent(row)}
								>
									<Settings2Icon class="size-3.5" /> Configure
								</Button>
							{:else}
								<span class="text-muted-foreground text-xs">—</span>
							{/if}
						{/if}
					{/snippet}
				</DataTable>
			</CardContent>
		</Card>
	{:else}
		{#each sections as section (section.key)}
			<!-- The audience-grouped BUILDER (the admin surface). The registry renders its own table
			     above, so this branch never has to ask which grouping it is in. -->
			{@const sectionRows = localRows.filter((r) =>
				section.key === 'client' ? r.audience === 'client' : r.audience !== 'client'
			)}
			{#if sectionRows.length}
				<Collapsible.Root
					open={sectionOpen(section.key)}
					onOpenChange={(next: boolean) => (collapsed[section.key] = !next)}
					class="mt-4"
				>
					<Collapsible.Trigger
						class="text-muted-foreground hover:text-foreground mb-2 flex w-full items-center gap-1.5 text-xs font-semibold tracking-wide uppercase"
					>
						<ChevronDownIcon
							class={'size-3.5 transition-transform ' +
								(sectionOpen(section.key) ? '' : '-rotate-90')}
						/>
						{section.label}
						<span class="text-muted-foreground/70 normal-case">({sectionRows.length})</span>
					</Collapsible.Trigger>
					<Collapsible.Content>
						<div class="divide-border/60 divide-y rounded-lg border">
							{#each sectionRows as row (row.key)}
								{@render rowItem(row)}
							{/each}
						</div>
					</Collapsible.Content>
				</Collapsible.Root>
			{/if}
		{/each}
	{/if}

	{#if !displayRows.length && groupBy !== 'event'}
		<EmptyState
			icon={BellIcon}
			title="No notifications configured"
			description={canManage && onLoadDefaults
				? 'Load the shipped defaults to get the standard set of notifications, then edit or remove any of them — or add your own.'
				: canManage
					? 'Add a notification to choose an event, who it tells, and how.'
					: 'No notifications have been set up yet.'}
			compact
			action={canManage && onLoadDefaults ? loadDefaultsAction : undefined}
		/>
	{/if}

	{#if canManage && dirtyRows.length}
		<div
			class="bg-background/95 supports-[backdrop-filter]:bg-background/80 sticky bottom-0 z-10 mt-3 flex shrink-0 items-center justify-between gap-3 rounded-lg border px-4 py-3 shadow-sm backdrop-blur"
		>
			<p class="text-muted-foreground text-sm">
				{dirtyRows.length} unsaved {dirtyRows.length === 1 ? 'change' : 'changes'}
			</p>
			<div class="flex items-center gap-2">
				<Button variant="ghost" size="sm" disabled={saving} onclick={discardChanges}>Discard</Button
				>
				<Button size="sm" disabled={saving} onclick={saveChanges}>
					{saving ? 'Saving…' : 'Save changes'}
				</Button>
			</div>
		</div>
	{/if}
</div>

<Dialog.Root open={draft !== null} onOpenChange={(o) => (o ? null : closeEditor())}>
	<Dialog.Content class="flex max-h-[88vh] w-[94%] flex-col gap-0 overflow-hidden p-0 sm:max-w-4xl">
		{#if draft}
			{@const params = paramsFor(draft.trigger_key)}
			{@const trig = triggerByKey.get(draft.trigger_key)}
			{@const usedTokens = tokensIn(draft.title, draft.body)}
			{@const unknownList = unknownTokens(usedTokens, params)}
			{@const paramGroups = groupParams(filterParams(params, paramSearch))}
			<div class="border-b px-6 pt-6 pb-4">
				<Dialog.Title>{isNew ? 'Add notification' : 'Edit notification'}</Dialog.Title>
				<Dialog.Description>Choose the event, who it notifies, and the message.</Dialog.Description>

				{#if editorAudiences.length}
					<!-- Every audience this event can notify, as a SEGMENTED control: the popup edits the
					     whole EVENT, so moving between its notifications, adding one and removing one all
					     belong here rather than behind three separate doors. A rule is keyed
					     (trigger, audience), so this list IS the set the event can carry - one each, no more.
					     Unsaved edits survive a switch: the outgoing draft is written back first. -->
					<div class="mt-3 flex flex-wrap items-center gap-2">
						<div class="bg-muted/60 inline-flex items-center gap-0.5 rounded-lg p-1">
							{#each editorAudiences.filter((a) => a.ix >= 0) as aud (aud.key)}
								{@const active = aud.ix === activeIx}
								<span
									class={cn(
										'inline-flex items-center rounded-md text-xs transition-colors',
										active ? 'bg-background shadow-xs' : 'hover:bg-background/60'
									)}
								>
									<button
										type="button"
										class={cn(
											'flex items-center gap-1.5 py-1 pl-2.5',
											active ? 'font-medium' : 'text-muted-foreground'
										)}
										aria-current={active ? 'true' : undefined}
										onclick={() => selectDraft(aud.ix)}
									>
										<!-- The same client/staff mark the line carries, so the two surfaces read alike. -->
										{#if audienceKind(aud.key) === 'client'}
											<UserRoundIcon class={cn('size-3', active && 'text-primary')} />
										{:else}
											<BriefcaseIcon class="size-3" />
										{/if}
										{aud.label}
										{#if draftIsNew[aud.ix]}<span class="text-muted-foreground">· new</span>{/if}
									</button>
									<!-- Removing is offered on the ACTIVE tab only: a row of little crosses invites
									     the accidental click, and this deletes a notification a firm's people are
									     relying on. A stored rule goes through the same confirmation the list uses;
									     an unsaved draft is just dropped, and a built-in reverts to the shipped
									     default (which is what discarding its override means). -->
									<button
										type="button"
										class={cn(
											'text-muted-foreground hover:text-destructive rounded-md py-1 pr-1.5 pl-1',
											!active && 'pointer-events-none opacity-0'
										)}
										tabindex={active ? 0 : -1}
										aria-hidden={active ? undefined : 'true'}
										aria-label={`Remove the notification for ${aud.label}`}
										title="Remove this notification"
										onclick={() => removeDraft(aud.ix)}
									>
										<XIcon class="size-3" />
									</button>
								</span>
							{/each}
						</div>
						{#each editorAudiences.filter((a) => a.ix < 0) as aud (aud.key)}
							<button
								type="button"
								class="text-muted-foreground hover:text-foreground hover:border-foreground/30 inline-flex items-center gap-1 rounded-lg border border-dashed px-2.5 py-1.5 text-xs"
								onclick={() => addAudience(aud.key)}
							>
								<PlusIcon class="size-3" />
								{#if audienceKind(aud.key) === 'client'}
									<UserRoundIcon class="size-3" />
								{:else}
									<BriefcaseIcon class="size-3" />
								{/if}
								{aud.label}
							</button>
						{/each}
					</div>
				{/if}
			</div>

			<div class="flex flex-1 flex-col overflow-hidden sm:flex-row">
				<!-- The form. -->
				<div class="flex-1 space-y-4 overflow-y-auto px-6 py-5">
					<!-- Event + audience read as one line: "When X happens, notify Y". -->
					<!-- `items-start`: the Event cell carries two lines of helper text under its box and the
					     Notify cell does not, so the default stretch/centre put the two LABELS on different
					     lines - the pair reads as one sentence and must start level. -->
					<div class="grid items-start gap-4 sm:grid-cols-2">
						<div class="grid gap-1.5">
							<Label for="nr-trigger">Event</Label>
							<!-- Locked whenever the event is already chosen: an existing rule cannot move
							     trigger, and the REGISTRY always opens on the row you clicked, so offering 207
							     alternatives there would only invite picking the wrong one. -->
							<select
								id="nr-trigger"
								class="border-input bg-background h-9 w-full min-w-0 truncate rounded-md border px-3 text-sm disabled:opacity-50"
								bind:value={draft.trigger_key}
								disabled={!isNew || groupBy === 'event'}
								onchange={applyAudienceDefaults}
							>
								{#each effectiveCatalogue as t (t.key)}
									<!-- The trigger's own NAME, not its friendly sentence. This list is how you pick
									     the event a rule sits on, and the sentences ("When a document is shared with
									     a client") are long enough to truncate in the box and near-identical to read
									     past - the names are what the rule stores, what the API takes and what a log
									     line shows. The sentence is not lost: it moves under the box, where it
									     describes the ONE trigger you have chosen instead of competing with sixteen
									     others. Sentence case, as every other option in the product is:
									     `document.sent` reads "Document sent". -->
									<option value={t.key}>{sentenceCase(t.key)}</option>
								{/each}
							</select>
							{#if selectedTrigger}
								<p class="text-muted-foreground text-xs">
									{selectedTrigger.description}
									{#if selectedTrigger.event_types.length}
										<span class="block">Fires on {selectedTrigger.event_types.join(', ')}</span>
									{/if}
								</p>
							{/if}
						</div>
						<div class="grid gap-1.5">
							<Label for="nr-audience">Notify</Label>
							<select
								id="nr-audience"
								class="border-input bg-background h-9 w-full min-w-0 truncate rounded-md border px-3 text-sm disabled:opacity-50"
								bind:value={draft.audience}
								disabled={!isNew}
								onchange={applyAudienceDefaults}
							>
								{#each audiencesFor(draft.trigger_key) as a (a.key)}
									<option value={a.key}>{a.label}</option>
								{/each}
							</select>
						</div>
					</div>

					<div class="grid gap-1.5">
						<Label for="nr-title">Message</Label>
						<Input id="nr-title" bind:value={draft.title} placeholder="Title" />
						<Textarea
							id="nr-body"
							bind:value={draft.body}
							rows={3}
							placeholder="Message (optional)"
						/>
					</div>

					<div class="grid gap-1.5">
						<Label for="nr-dest">When clicked, open</Label>
						<select
							id="nr-dest"
							class="border-input bg-background h-9 w-full min-w-0 truncate rounded-md border px-3 text-sm"
							bind:value={linkChoice}
							onchange={applyLinkChoice}
						>
							{#each linkTargets as t (t.href)}
								<option value={t.href}>{t.label}</option>
							{/each}
							{#each pageOptions as o (o.path)}
								<option value={o.path}>{o.label}</option>
							{/each}
							<option value={CUSTOM}>Custom path…</option>
						</select>
						{#if selectedTarget}
							<p class="text-muted-foreground text-xs">
								Opens {selectedTarget.label.toLowerCase()} — the exact one this notification is about.
							</p>
						{:else if linkChoice === CUSTOM}
							<Input id="nr-link" bind:value={draft.link} placeholder="/documents" />
						{/if}
					</div>

					{#if emailTemplates.length}
						<div class="grid gap-1.5">
							<Label for="nr-email-template">Email template</Label>
							<select
								id="nr-email-template"
								class="border-input bg-background h-9 w-full min-w-0 truncate rounded-md border px-3 text-sm"
								value={draft.email_code ?? WRAPPER_CODE}
								onchange={(e) => draft && (draft.email_code = e.currentTarget.value)}
							>
								{#each emailTemplates as t (t.code)}
									<option value={t.code}>{t.label}</option>
								{/each}
							</select>
							<p class="text-muted-foreground text-xs">
								The branded email design used when this notification also sends an email. Edit the
								designs on the Emails page.
							</p>
						</div>
					{/if}

					<!-- Only what the TABLE cannot say. In-app/email/mandatory are switches on the line, one
					     set per notification, saved as a batch — a second copy here is the same flag with a
					     different save path, and two controls for one value end up disagreeing. Whether the
					     firm may edit the rule is Graphene's call and has no place on a firm's own table. -->
					{#if !firmMode}
						<div class="grid grid-cols-1 gap-x-10 gap-y-3 border-t pt-4 sm:grid-cols-2">
							<label class="flex items-center justify-between gap-3 text-sm">
								<span>Firm can edit this</span>
								<Switch bind:checked={draft.firm_editable} />
							</label>
						</div>
					{/if}
				</div>

				<!-- The right rail, tight to the top and side: a live PREVIEW, then the event's variables
				     (click to insert into the last-focused copy field), then the raw event metadata. -->
				<aside
					class="bg-muted/20 w-full shrink-0 space-y-5 overflow-y-auto border-t px-4 py-5 sm:w-72 sm:border-t-0 sm:border-l"
				>
					<div>
						<p
							class="text-muted-foreground mb-1.5 text-[0.7rem] font-semibold tracking-wide uppercase"
						>
							Preview
						</p>
						{#if previewOf(draft.title, params) || previewOf(draft.body, params)}
							<div class="bg-background rounded-md border px-3 py-2">
								{#if previewOf(draft.title, params)}
									<p class="text-sm font-medium">{previewOf(draft.title, params)}</p>
								{/if}
								{#if previewOf(draft.body, params)}
									<p class="text-muted-foreground text-xs whitespace-pre-line">
										{previewOf(draft.body, params)}
									</p>
								{/if}
							</div>
						{:else}
							<p class="text-muted-foreground text-xs">
								Your message preview appears here as you type.
							</p>
						{/if}
					</div>

					<div>
						<p class="text-sm font-medium">Available variables</p>
						<p class="text-muted-foreground mt-0.5 text-xs">
							From this event. Click to copy, then paste into your copy; each is filled in per
							notification.
						</p>
						{#if params.length}
							<Input
								bind:value={paramSearch}
								placeholder="Search variables…"
								aria-label="Search variables"
								class="mt-2 h-8 text-xs"
							/>
							<!-- Advisory only (never a save block): a stored rule may carry a token a later
							     catalogue dropped, so the server renders it blank rather than refusing it. -->
							{#if unknownList.length}
								<p class="text-warning mt-2 flex items-start gap-1.5 text-[0.7rem]">
									<TriangleAlertIcon class="mt-0.5 size-3 shrink-0" />
									<span>
										Unknown {unknownList.length === 1 ? 'variable' : 'variables'}:
										{unknownList.map((t) => `{${t}}`).join(', ')} — will render blank.
									</span>
								</p>
							{/if}
							<div class="mt-2 space-y-3">
								{#each paramGroups as group (group.namespace)}
									<div>
										<p
											class="text-muted-foreground/80 mb-0.5 px-2 text-[0.65rem] font-semibold tracking-wide uppercase"
										>
											{group.label}
										</p>
										<div class="space-y-0.5">
											{#each group.params as p (p.key)}
												<button
													type="button"
													onclick={() => copyToken(p)}
													title="Copy {`{${p.key}}`}"
													class="hover:bg-muted flex w-full flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left transition-colors"
												>
													<span class="flex w-full items-center justify-between gap-2">
														<span class="flex items-center gap-1 text-xs font-medium">
															{p.label}
															{#if usedTokens.has(p.key)}
																<!-- Already in the copy — a quiet tick so an author sees what's used. -->
																<CheckIcon class="text-success size-3" aria-label="Used" />
															{/if}
														</span>
														<code class="text-muted-foreground text-[0.7rem]">
															{copied === p.key ? 'Copied!' : `{${p.key}}`}
														</code>
													</span>
													<span class="text-muted-foreground text-[0.7rem]">e.g. {p.example}</span>
												</button>
											{/each}
										</div>
									</div>
								{/each}
								{#if !paramGroups.length}
									<p class="text-muted-foreground px-2 text-xs">
										No variables match “{paramSearch}”.
									</p>
								{/if}
							</div>
						{:else}
							<p class="text-muted-foreground mt-2 text-xs">This event carries no variables.</p>
						{/if}
					</div>

					{#if trig}
						<Collapsible.Root bind:open={rawOpen}>
							<Collapsible.Trigger
								class="text-muted-foreground hover:text-foreground flex w-full items-center gap-1.5 text-[0.7rem] font-semibold tracking-wide uppercase"
							>
								<ChevronDownIcon
									class={'size-3.5 transition-transform ' + (rawOpen ? '' : '-rotate-90')}
								/>
								Raw event metadata
							</Collapsible.Trigger>
							<Collapsible.Content>
								{@const sampleEvent = {
									id: 84213,
									event_type: trig.event_types[0],
									tenant_id: 42,
									actor_id: 'usr_9f3c…',
									metadata: { subject: SUBJECT_EXAMPLE[trig.subject_kind] ?? {} },
									created_at: '2026-01-15T10:22:04Z'
								}}
								<!-- Two views: the EVENT is a representative audit.actions row — what actually fires
								     when this happens; the DEFINITION is the trigger's builder config (how the rule is
								     described), which is NOT sent with the event. -->
								<div
									class="bg-muted text-muted-foreground mt-2 mb-2 inline-flex gap-0.5 rounded-md p-0.5 text-[0.7rem]"
								>
									<button
										type="button"
										onclick={() => (rawTab = 'event')}
										class={'rounded px-2 py-0.5 ' +
											(rawTab === 'event' ? 'bg-background text-foreground shadow-sm' : '')}
									>
										Event
									</button>
									<button
										type="button"
										onclick={() => (rawTab = 'definition')}
										class={'rounded px-2 py-0.5 ' +
											(rawTab === 'definition' ? 'bg-background text-foreground shadow-sm' : '')}
									>
										Definition
									</button>
								</div>
								{#if rawTab === 'event'}
									<p class="text-muted-foreground mb-1 text-[0.7rem]">
										A sample of the compact event that fires.
									</p>
									<pre
										class="bg-muted/50 text-foreground/80 max-h-72 overflow-auto rounded-md border p-2 text-[0.7rem] leading-relaxed whitespace-pre">{JSON.stringify(
											sampleEvent,
											null,
											2
										)}</pre>
								{:else}
									<p class="text-muted-foreground mb-1 text-[0.7rem]">
										Builder config — not part of the event.
									</p>
									<pre
										class="bg-muted/50 text-foreground/80 max-h-72 overflow-auto rounded-md border p-2 text-[0.7rem] leading-relaxed whitespace-pre">{JSON.stringify(
											trig,
											null,
											2
										)}</pre>
								{/if}
							</Collapsible.Content>
						</Collapsible.Root>
					{/if}
				</aside>
			</div>

			<div class="flex justify-end gap-2 border-t px-6 py-4">
				<Button variant="ghost" onclick={closeEditor}>Cancel</Button>
				<Button disabled={saving || !draft.title.trim()} onclick={save}>
					{saving ? 'Saving…' : isNew ? 'Add notification' : 'Save changes'}
				</Button>
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>

<ConfirmDialog
	bind:open={confirmOpen}
	title="Delete this notification?"
	description={toDelete
		? `“${triggerLabel(toDelete.trigger_key)} → ${audienceLabel(toDelete)}” will be deleted. You can add it again from the catalogue later.`
		: ''}
	confirmLabel="Delete"
	pendingLabel="Deleting…"
	destructive
	onconfirm={async () => {
		const uid = toDelete?.rule?.uid;
		if (!uid) return;
		await onDelete?.(uid);
		// The editor may be open on the very rule just deleted (removing from its strip is one of the
		// two doors into this dialog), and it must not go on showing a draft of something gone.
		const ix = drafts.findIndex((d) => d.uid === uid);
		if (ix >= 0) dropDraft(ix);
	}}
/>

<RuleHistoryDialog
	bind:open={historyOpen}
	url={historyTarget?.rule && historyUrl ? historyUrl(historyTarget.rule.uid) : null}
	ruleLabel={historyTarget ? triggerLabel(historyTarget.trigger_key) : ''}
/>
