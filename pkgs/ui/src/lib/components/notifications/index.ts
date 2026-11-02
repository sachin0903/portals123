// In-app notifications (notifications.notifications, api 0063). The bell + badge for the shell, the
// full-page feed, and the staff channel-preferences surface — all portable across the three portals
// (no $app/* dependency; navigation via native <a>, mutations via injected callbacks).
export { default as NotificationBell } from './notification-bell.svelte';
// The same bell for a root layout that STREAMS its seed (`items`/`unreadCount` as promises): holds
// the place until the seed lands, then mounts the live bell with it. Plain values render at once.
export { default as StreamedNotificationBell } from './streamed-notification-bell.svelte';
export { default as NotificationFeed } from './notification-feed.svelte';
export { default as NotificationItem } from './notification-item.svelte';
export { default as NotificationToast } from './notification-toast.svelte';
export { default as NotificationPreferences } from './notification-preferences.svelte';
export { default as ClientNotificationPreferences } from './client-notification-preferences.svelte';
export { default as NotificationRules } from './notification-rules.svelte';
export type {
	CatalogueTrigger,
	CatalogueAudience,
	NotificationRule,
	RegistryEvent,
	RuleDraft,
	RuleVersion,
	EmailTemplateOption
} from './rule-types.js';
export { WRAPPER_CODE } from './rule-types.js';
export {
	categoryMeta,
	audienceKind,
	configuredMeta,
	configuredState,
	resourceCategory,
	stateLabel,
	triggersFrom,
	toneClasses,
	relativeTime,
	type NotificationItem as NotificationItemData,
	type NotificationPreference,
	type NotificationTone,
	type CategoryMeta
} from './describe.js';
// Generic, visibility-aware poll timer the bell uses for its own count — reused as-is by any
// other same-origin "is there anything new" badge (the Chats nav dot is the first other caller).
export {
	createPollController,
	pollWhileVisible,
	REFRESH_WHILE,
	type PollController
} from './poll-controller.js';
// The arrival-pop-up decision (what is new, what is merely re-listed, how many of a burst) — the
// bell applies it; exported because it is the part with rules worth reading, not the rendering.
export { arrivals, MAX_TOASTS_PER_BATCH, type Arrivals } from './arrivals.js';
// What the bell/feed do with the outcome of an optimistic write; apps' callbacks may return it.
export { settleWrite, type WriteOutcome } from './write-outcome.js';
