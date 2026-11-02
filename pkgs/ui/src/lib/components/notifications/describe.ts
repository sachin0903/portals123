/**
 * Humanising in-app notifications (`notifications.notifications`, api 0063).
 *
 * A notification carries a `category` (the preference group) and free `title`/`body` copy composed
 * by the producer. These helpers give each category its icon + tone and turn the ISO timestamp into
 * the relative phrase the feed shows ("2h ago"). Pure functions, table-tested in `describe.test.ts`;
 * the bell + feed defer to them so every portal reads notifications the same way.
 */

import BellIcon from '@lucide/svelte/icons/bell';
import ListChecksIcon from '@lucide/svelte/icons/list-checks';
import FileTextIcon from '@lucide/svelte/icons/file-text';
import MessageSquareIcon from '@lucide/svelte/icons/message-square';
import UsersRoundIcon from '@lucide/svelte/icons/users-round';
import UserRoundCheckIcon from '@lucide/svelte/icons/user-round-check';
import WalletIcon from '@lucide/svelte/icons/wallet';

/** One row of `GET /notifications` (the fields the UI renders). */
export interface NotificationItem {
	uid: string;
	event_type: string;
	category: string;
	title: string;
	body: string | null;
	link: string | null;
	priority: string;
	subject_type: string | null;
	subject_id: string | null;
	read_at: string | null;
	created_at: string;
}

/** One category × channel preference row of `GET /notifications/preferences` (personal) or
 *  `GET /admin/tenants/{id}/notification-config` (firm defaults — carries `enabled`). */
export interface NotificationPreference {
	category: string;
	label: string;
	description: string;
	in_app: boolean;
	email: boolean;
	email_available: boolean;
	/** Firm-config posture only: the per-category firm-wide off-switch. */
	enabled?: boolean;
	/** Firm-config posture only: which audience this row configures ('adviser' | 'client'). */
	audience?: string;
	/** Firm-config posture only: force the default on this audience, even over an opt-out. */
	mandatory?: boolean;
	/** Client posture only: the specific notification (catalogue trigger) this row is for; the
	 *  client surface is per-notification, grouped by `category`. */
	trigger_key?: string | null;
}

/** Tone → the icon-bubble colour classes. Uses the branded status tokens, never a raw palette. */
export type NotificationTone = 'default' | 'info' | 'warning' | 'success';

export interface CategoryMeta {
	label: string;
	icon: typeof BellIcon;
	tone: NotificationTone;
}

const CATEGORY_META: Record<string, CategoryMeta> = {
	tasks: { label: 'Task', icon: ListChecksIcon, tone: 'info' },
	documents: { label: 'Document', icon: FileTextIcon, tone: 'info' },
	clients: { label: 'Client', icon: UsersRoundIcon, tone: 'info' },
	accounts: { label: 'Account', icon: WalletIcon, tone: 'info' },
	// A live category on BOTH portals (registry.CATEGORIES) with two triggers in the rules builder,
	// and the one missing from this table — so every chat notice, and every messaging row in the
	// rules list, wore the neutral "Update" bell. Present but unlabelled reads as absent, which is
	// why messaging looked like it was not offered at all. Info tone, like the rest of the chat
	// and CRM family.
	messages: { label: 'Message', icon: MessageSquareIcon, tone: 'info' },
	reviews: { label: 'Peer review', icon: UserRoundCheckIcon, tone: 'info' }
};

/** Category presentation, with a neutral fallback for a category the UI has not seen. */
export function categoryMeta(category: string): CategoryMeta {
	return CATEGORY_META[category] ?? { label: 'Update', icon: BellIcon, tone: 'default' };
}

/** The icon-bubble classes for a tone (background tint + foreground), theme-token based. */
export function toneClasses(tone: NotificationTone): string {
	switch (tone) {
		case 'info':
			return 'bg-primary/10 text-primary';
		case 'warning':
			return 'bg-[var(--warning,theme(colors.amber.500))]/15 text-[var(--warning,theme(colors.amber.600))]';
		case 'success':
			return 'bg-[var(--success,theme(colors.emerald.500))]/15 text-[var(--success,theme(colors.emerald.600))]';
		default:
			// `bg-muted-foreground/10`, not `bg-muted`: the three tinted tones above are all a 10%
			// wash of a colour, and a flat `bg-muted` next to them is so close to the card that the
			// bubble disappears and that one row reads as having no icon chrome at all. Same
			// construction, neutral hue — an unknown category should look unremarkable, not absent.
			return 'bg-muted-foreground/10 text-muted-foreground';
	}
}

/**
 * A compact relative phrase for a past ISO timestamp: "just now", "5m", "2h", "3d", else a short
 * date. `now` is injectable so the unit test is deterministic (no wall clock in an assertion).
 *
 * A FUTURE timestamp reads "just now", never a negative age. The server's clock and the browser's
 * disagree by seconds routinely and by minutes on a machine back from sleep, so a notification
 * written moments ago can legitimately carry a stamp ahead of the reader — and an unclamped age
 * renders that as "-1m ago", since `Math.floor(-50 / 60)` is -1 rather than 0. "Just now" is the
 * honest reading of a moment that has not arrived yet; a negative one is just wrong on screen.
 */
export function relativeTime(iso: string, now: Date = new Date()): string {
	const then = new Date(iso);
	const seconds = Math.max(0, Math.floor((now.getTime() - then.getTime()) / 1000));
	if (!Number.isFinite(seconds)) return '';
	if (seconds < 45) return 'just now';
	const minutes = Math.floor(seconds / 60);
	if (minutes < 60) return `${minutes}m ago`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `${hours}h ago`;
	const days = Math.floor(hours / 24);
	if (days < 7) return `${days}d ago`;
	return then.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
}

/**
 * The RESOURCE half of an event type (`document` for `document.sent`) → a coarse category.
 *
 * ~40 distinct resources are far too many to scan as a column, so they fold into the handful of
 * things a firm actually thinks in. Anything unmapped is `System`, deliberately and not as a
 * guess: an event about branding, a role or a sync IS platform plumbing, and calling it that is
 * more use than inventing a category per resource.
 */
const RESOURCE_CATEGORY: Record<string, string> = {
	document: 'Documents',
	document_request: 'Documents',
	document_share: 'Documents',
	file: 'Documents',
	template: 'Documents',
	form: 'Documents',
	client: 'Clients',
	contact: 'Clients',
	conversation: 'Messages',
	message: 'Messages',
	message_group: 'Messages',
	account: 'Accounts',
	instruction: 'Accounts',
	transfer: 'Money',
	contribution: 'Money',
	transaction: 'Money',
	illustration: 'Money',
	fee: 'Money',
	task: 'Tasks',
	journey: 'Journeys',
	flow: 'Journeys',
	case: 'Journeys',
	automation: 'Journeys',
	aml: 'Compliance',
	screening: 'Compliance',
	kyc: 'Compliance',
	authentication: 'Security',
	member: 'People & access',
	role: 'People & access',
	user: 'People & access',
	notification: 'Notifications',
	email_template: 'Notifications'
};

/** The category an event belongs to, for the registry's Category column. */
export function resourceCategory(resource: string): string {
	return RESOURCE_CATEGORY[resource] ?? 'System';
}

/**
 * The event-registry states, in the words a reader uses. Pure and exported so the vocabulary can be
 * tested directly — the filter that offers it is Popover-based, so its options never reach an SSR
 * render and a test over the markup would silently assert nothing.
 */
const STATE_LABELS: Record<string, string> = {
	assignable: 'Can notify someone',
	ready: 'Not offered yet',
	declarable: 'Needs a subject source',
	actor_only: 'Actor or role only'
};

export function stateLabel(state: string): string {
	return STATE_LABELS[state] ?? state;
}

/**
 * The distinct catalogue triggers behind a set of registry events.
 *
 * **One trigger may cover SEVERAL events** — `message.received_by_client` fires on four message
 * types — so mapping the event rows yields the same trigger once per event. The editor's Event
 * select is a keyed `{#each}` over this list, and duplicate keys throw `each_key_duplicate` at
 * render: the dialog's overlay opens and its content never draws, which reads as "the button does
 * nothing" rather than as an error.
 *
 * Pure and exported so the rule has a test that can actually fail. A component test cannot catch it:
 * the dialog is portalled and does not mount under jsdom, so the offending `{#each}` never runs.
 */
export function triggersFrom(
	events: readonly { trigger: { key: string } | null }[]
): { key: string }[] {
	const byKey = new Map<string, { key: string }>();
	for (const event of events) {
		if (event.trigger && !byKey.has(event.trigger.key)) byKey.set(event.trigger.key, event.trigger);
	}
	return [...byKey.values()];
}

/** Whether an event notifies anybody, and — if it does — whether any of it is switched on. */
export type ConfiguredState = 'live' | 'off' | 'none';

/**
 * The dot on a registry line.
 *
 * Three states, not two, because "configured" and "working" are different answers: an event with
 * two notifications that are both switched off tells nobody anything, and showing it as configured
 * is how a firm concludes a notice is going out when it is not. `none` is deliberately hollow
 * rather than red — an event nobody has attached anything to is not a fault.
 */
export function configuredState(attached: readonly { enabled: boolean }[]): ConfiguredState {
	if (!attached.length) return 'none';
	return attached.some((a) => a.enabled) ? 'live' : 'off';
}

export function configuredMeta(state: ConfiguredState): { label: string; classes: string } {
	switch (state) {
		case 'live':
			return {
				label: 'Notifying',
				classes: 'bg-[var(--success,theme(colors.emerald.500))]'
			};
		case 'off':
			return {
				label: 'Configured, but switched off',
				classes: 'bg-[var(--warning,theme(colors.amber.500))]'
			};
		default:
			return { label: 'Not configured', classes: 'bg-transparent ring-1 ring-border' };
	}
}

/**
 * Who a notification reaches: the firm's own people, or the end client.
 *
 * The catalogue has four audiences — `client`, `adviser`, `assignee` and `actor` — and only the
 * first leaves the firm. That is the distinction worth showing on a line: a notice to a client goes
 * to a real customer of the firm, and is the one to look twice at before switching a channel on.
 * The other three are all "somebody at the firm" and are not worth three separate marks.
 *
 * Anything unrecognised counts as STAFF, deliberately: a new audience is far more likely to be an
 * internal role than a second way to reach a client, and the safe error is to under-claim
 * client-facing rather than to mark an internal notice as going to a customer.
 */
export type AudienceKind = 'client' | 'staff';

export function audienceKind(audience: string): AudienceKind {
	return audience === 'client' ? 'client' : 'staff';
}
