import type { NavGroup } from './types.js';

/**
 * The pure half of the sidebar's group behaviour: identity, ordering, the persisted open/closed map,
 * and what the collapsed icon rail does differently. It lives outside the component because every
 * one of these rules broke a nav restructure somewhere, and none of them is visible to svelte-check.
 */

/**
 * Stable identity for a group — the persistence key and the `{#each}` key.
 *
 * The label comes first because that is what the user sees and what a collapsible group is required
 * to have (the label IS the disclosure trigger). Falling back to the first item's href matters for
 * unlabelled groups whose membership is permission-gated: keying on the index instead would reuse
 * one group's DOM (and its open state) for another when a gate flips.
 */
export function groupKey(group: NavGroup, index: number): string {
	return group.label ?? group.items[0]?.href ?? `group-${index}`;
}

/**
 * Split the nav into the groups that flow from the top and the groups pinned to the bottom, each in
 * the order the app declared them. `pinned` groups render below a flexible spacer, above the footer.
 */
export function splitNav(nav: NavGroup[]): { main: NavGroup[]; pinned: NavGroup[] } {
	return {
		main: nav.filter((g) => !g.pinned),
		pinned: nav.filter((g) => g.pinned)
	};
}

/** localStorage key for the per-portal open/closed map. Portals keep separate nav shapes. */
export function navStateKey(portal: string): string {
	return `gp-nav-groups:${portal.toLowerCase().replace(/\s+/g, '-')}`;
}

/**
 * Parse the persisted map, tolerating anything. A user's localStorage is not a trusted schema: a
 * half-written value, an array, or a `null` must degrade to "no preference recorded" rather than
 * throw during sidebar init and take the whole shell down.
 */
export function parseNavState(raw: string | null | undefined): Record<string, boolean> {
	if (!raw) return {};
	let parsed: unknown;
	try {
		parsed = JSON.parse(raw);
	} catch {
		return {};
	}
	if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) return {};
	const out: Record<string, boolean> = {};
	for (const [key, value] of Object.entries(parsed)) {
		if (typeof value === 'boolean') out[key] = value;
	}
	return out;
}

/**
 * Read the persisted map, or `{}` where there is nowhere to read it from.
 *
 * Called at component INIT, not from an effect, and that is the whole point: on the server there is
 * no `localStorage`, so SSR renders each group's declared `defaultOpen`, and the client's very first
 * render — hydration — already reflects the stored map. A group the user collapsed therefore never
 * flashes open and then shut on a hard reload.
 *
 * That is only safe because the primitive keeps the panel MOUNTED and toggles a `hidden` attribute
 * (see ui/collapsible): server and client agree on structure and differ by one attribute, which
 * Svelte applies as it hydrates. If the panel were conditionally rendered this would be a hydration
 * mismatch and would have to move back into an effect.
 */
export function readNavState(portal: string): Record<string, boolean> {
	// Not just an SSR guard: touching localStorage throws outright when storage is blocked.
	try {
		if (typeof localStorage === 'undefined') return {};
		return parseNavState(localStorage.getItem(navStateKey(portal)));
	} catch {
		return {};
	}
}

/** A group's OWN open state — what the user last asked of it, ignoring the solo rule. */
function ownOpen(group: NavGroup, stored: Record<string, boolean>, key: string): boolean {
	if (!group.collapsible) return true;
	return stored[key] ?? group.defaultOpen ?? true;
}

/**
 * Is a `solo` group open, and therefore holding the other groups shut?
 *
 * Never in the rail — and this is the ONE place the rail still deliberately differs from the open
 * sidebar. Suppression is a spacing rule: a fourteen-row group opened beside the three work groups
 * pushes them off a short viewport, so it takes the panel to itself. The rail has no such problem,
 * and applying it there would delete Dashboard, Clients and Accounts from the icon column the whole
 * app is navigated by — a cliff, not a tidier picture. So a group's OWN state is honoured in the
 * rail (see `isGroupOpen`) and only the exclusivity is dropped.
 */
export function soloGroupOpen(
	nav: NavGroup[],
	stored: Record<string, boolean>,
	rail: boolean
): boolean {
	if (rail) return false;
	return nav.some((group, index) => group.solo && ownOpen(group, stored, groupKey(group, index)));
}

/**
 * Whether a group's items are showing. The SAME answer in the rail as in the open sidebar.
 *
 * It used to force every group open in the rail, on the reasoning that a 56px rail has no room for a
 * group label and therefore no disclosure trigger, so a closed group would be unreachable. The cost
 * of that was worse than the thing it avoided: the platform's `Firm settings` is fourteen rows long
 * and `defaultOpen: false`, so COLLAPSING the sidebar added fourteen icons to it. Compacting the
 * chrome showed strictly MORE than leaving it open — which is not a trade-off, it is the control
 * doing the opposite of what it says.
 *
 * A group closed in the rail is reached by opening the sidebar, one click away on the toggle the
 * user just used, and the rail is now a smaller picture of the same nav rather than a second one.
 *
 * `soloOpen` (see `soloGroupOpen`) shuts every other collapsible group while a `solo` group is open.
 * It is applied here, over the stored map rather than into it, so the suppressed groups keep the
 * state the user gave them and get it back the moment the solo group closes.
 */
export function isGroupOpen(
	group: NavGroup,
	stored: Record<string, boolean>,
	key: string,
	soloOpen = false
): boolean {
	if (!group.collapsible) return true;
	if (soloOpen && !group.solo) return false;
	return ownOpen(group, stored, key);
}

/**
 * Does this group contain the item the reader is currently on?
 *
 * The rail's one force-open case. Dropping the blanket rail override meant the ACTIVE page could sit
 * inside a group that renders nothing there — no lit tile, no pending spinner, no unread dot, and no
 * label to say the section even exists. The rail would show the reader nothing about where they are.
 * So the group holding the active item opens in the rail whatever its stored state says; every other
 * group is left as the reader set it.
 *
 * Exact href match, not a prefix: the caller passes `activeHref`, which is already the
 * longest-match winner, and re-testing prefixes here would open a parent group as well.
 */
export function groupHoldsHref(group: NavGroup, href: string): boolean {
	return href !== '' && group.items.some((item) => item.href === href);
}

/**
 * Which groups get a hairline above them in the rail — the keys of every group that renders and has
 * a rendering group before it.
 *
 * Position alone is not enough, and that is the whole reason this is a function rather than an
 * `index > 0`. A group can now be CLOSED in the rail, and a closed group renders nothing there (its
 * label is hidden and its panel is shut), so a rule drawn by position lands above nothing —
 * or, with two adjacent groups closed, stacks two rules together, which reads as a rendering fault.
 *
 * `groups` must arrive in RENDER order (main groups, then pinned), which is not the order they were
 * declared in.
 */
export function railRuleKeys(groups: { key: string; open: boolean }[]): Set<string> {
	const keys = new Set<string>();
	let anyBefore = false;
	for (const { key, open } of groups) {
		if (!open) continue;
		if (anyBefore) keys.add(key);
		anyBefore = true;
	}
	return keys;
}

/**
 * The next open/closed map after a disclosure reports a change — the only writer of that state.
 *
 * Two rules beyond the obvious write, both there because a `solo` group makes the displayed state
 * and the stored state differ:
 *
 *  1. **Opening a group closes every other solo group.** A normal group's trigger would otherwise
 *     record a click the suppression then hides. A second solo group would otherwise stay open
 *     beside the one just opened, which is the two of them fighting for the column.
 *  2. **A `false` for a normal group while the solo group is open is ignored.** Its panel is already
 *     shut and its trigger would send `true`, so this can only be the primitive syncing itself to
 *     the suppressed value — and honouring it would overwrite the very state we exist to restore.
 */
export function applyGroupOpen(
	nav: NavGroup[],
	stored: Record<string, boolean>,
	key: string,
	open: boolean
): Record<string, boolean> {
	const solos = nav
		.map((group, index) => ({ group, key: groupKey(group, index) }))
		.filter(({ group }) => group.solo);
	const isSolo = solos.some((s) => s.key === key);
	const suppressing = solos.some((s) => ownOpen(s.group, stored, s.key));

	if (!isSolo && !open && suppressing) return stored;

	const next = { ...stored, [key]: open };
	// Opening anything closes every OTHER solo group. A normal group has to, or its trigger records
	// a click the suppression then hides. A second solo group has to as well — Firm settings and
	// Configure are both long panels pinned to the bottom, and leaving both open is the two of them
	// fighting for the column. Written, not derived: the one the user just left should stay shut.
	if (open) for (const s of solos) if (s.key !== key) next[s.key] = false;
	return next;
}

// The rail's monogram tile and the workspace switcher's, both by initials — shared with every
// other avatar-style monogram in `pkgs/ui`/`apps/client`. See `lib/initials.ts`.
export { initials, personInitials } from '../../initials.js';
