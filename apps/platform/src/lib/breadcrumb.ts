/** The shell breadcrumb for the Documents area and its sibling authoring sections.
 *
 * This used to be built from the pathname alone, with one hardcoded static child
 * (`documents/create`) and an otherwise unconditional entity-name lookup — so every OTHER static
 * child route fell through to `'Untitled'`: `/documents/requests` and `/documents/requests/{uid}`
 * both read "Documents / Untitled", and so did `/datasets/new`. The fix is general rather than a
 * second special case: the ROUTE decides what each segment is. A static segment gets a label —
 * title-cased from the segment unless it needs different wording — so the next static child route
 * reads correctly without touching this file. Only a dynamic (`[param]`) segment resolves the
 * loaded entity's name, and when nothing loaded it falls back to the singular of its parent crumb
 * ("Requests" → "Request") rather than claiming a record called "Untitled".
 */

import { clientName } from '@graphene/ui';

export type Crumb = { label: string; href?: string };

/** Top-level sections that get a breadcrumb (each is its own route tree).
 *
 * Every section in the sidebar's daily-work groups and Firm settings is here, so a drill-down
 * anywhere in the app reads as a trail back to its listing. Before the nav restructure only the four document
 * authoring sections were, and everything else fell back to the flat nav label — which meant
 * `/clients/12` and `/tasks/{uid}` showed no crumb at all. */
export const SECTION_LABELS: Record<string, string> = {
	// Daily work
	clients: 'Clients',
	accounts: 'Accounts',
	transactions: 'Transactions',
	reports: 'Reports',
	// The route is `/contributions` (matching the API and the permission); the section a user
	// sees is the nav label. Kept in step with `nav.ts`.
	contributions: 'Contributions',
	transfers: 'Transfers',
	journeys: 'Journeys',
	tasks: 'Tasks',
	reviews: 'Reviews',
	chats: 'Messages',
	documents: 'Documents',
	// Firm settings
	team: 'Team',
	profiles: 'Profiles',
	actions: 'Actions',
	branding: 'Studio',
	flows: 'Flows',
	automations: 'Automations',
	templates: 'Templates',
	content: 'Content',
	terms: 'Terms',
	forms: 'Forms',
	emails: 'Emails',
	activity: 'Activity',
	'data-sources': 'Data sources'
};

/** Labels for static child segments that shouldn't just be title-cased, keyed by route path.
 * Anything absent is title-cased from the segment (`requests` → "Requests", `edit` → "Edit"). */
const STATIC_LABELS: Record<string, string> = {
	'/documents/create': 'Generate document',
	'/templates/datasets/new': 'New dataset',
	'/transfers/new': 'New transfer',
	'/journeys/new': 'Start a journey',
	// The crumb is this listing's only title, so it carries the page's name, not the route's.
	'/flows/dead-letters': 'Failed journey actions',
	// The route segment is `chat` (singular); the sidebar/page label is "Messages".
	'/clients/[uid]/chat': 'Messages'
};

/**
 * Where a crumb LINKS, when the pathname is not the answer.
 *
 * A crumb's href is normally the path it sits at, which assumes every ancestor segment is itself a
 * page. Two shapes break that, and `/templates/emails/{uid}/edit` is both at once:
 *
 *   `/templates/emails`        — not a route. The Emails view is a QUERY on the templates listing
 *                                (`?kind=emails`), because switching view loads no page. The crumb
 *                                linked to a 404.
 *   `/templates/emails/[uid]`  — not a route either. An email template has no record page; `edit`,
 *                                `preview`, `send-test`, `images` and `versions` are its only
 *                                children, so the crumb bearing the template's NAME went nowhere.
 *
 * `null` means "label it, do not link it" — right for an ancestor that genuinely has no page, and
 * honest in a way a link to a 404 is not. Keyed by ROUTE path, like the two maps around it.
 */
const CRUMB_HREFS: Record<string, string | null> = {
	'/templates/emails': '/templates?kind=emails',
	'/templates/emails/[uid]': null,
	// Same shape as the email template above: a content item has no record page of its own —
	// `edit` is its only page, `guide`/`images`/`hero`/`guide-file` are upload endpoints, not
	// routes — so the item's own crumb would link to a path nothing serves.
	'/content/[uid]': null
};

/** Loader keys that hold "the entity this page is about", and the fields that name one. A request is
 * titled by the client it was sent to (the same title its PageHeader renders). */
const ENTITY_KEYS = [
	'doc',
	'template',
	'form',
	'dataset',
	'request',
	'account',
	'task',
	'flow',
	'review'
];
const NAME_FIELDS = ['name', 'contact_name', 'title'] as const;

/** The first of NAME_FIELDS this entity carries, trimmed. */
function nameOf(entity: unknown): string | null {
	if (!entity || typeof entity !== 'object' || Array.isArray(entity)) return null;
	for (const field of NAME_FIELDS) {
		const value = (entity as Record<string, unknown>)[field];
		if (typeof value === 'string' && value.trim()) return value.trim();
	}
	return null;
}

/** A client record's name — composed from title/first/surname (or the organisation name), with the
 * same helper the page's own header uses. NAME_FIELDS cannot serve it: a contact's `title` is "Mr",
 * which would render a breadcrumb reading "Clients / Mr". */
function contactLabel(bag: Record<string, unknown>): string | null {
	const contact = (bag.detail as Record<string, unknown> | undefined)?.contact;
	return contact && typeof contact === 'object'
		? clientName(contact as Parameters<typeof clientName>[0])
		: null;
}

/**
 * How to label a DYNAMIC segment, keyed by the route path it sits at.
 *
 * `entityLabel` below answers "what one record is this page about?", which is the right question
 * for a section whose records are one level deep. A NESTED record has two — `/clients/8/documents/
 * doc_1` is about a client and a document, and both are on `page.data` — so the generic answer puts
 * one name in both crumbs. Any route that nests a record inside another names each level here.
 */
const DYNAMIC_LABELS: Record<string, (bag: Record<string, unknown>) => string | null> = {
	'/clients/[uid]': contactLabel,
	'/clients/[uid]/documents/[doc]': (bag) => nameOf(bag.doc),
	'/clients/[uid]/journeys/[exec]': (bag) => {
		const journey = bag.journey;
		if (!journey || typeof journey !== 'object' || Array.isArray(journey)) return null;
		const name = (journey as { flow_name?: unknown }).flow_name;
		return typeof name === 'string' && name.trim() ? name.trim() : null;
	},
	// `item` isn't one of ENTITY_KEYS (it's not a name generic enough to add there without risking
	// a collision on some other route's own `item` loader key), and without an entry here this
	// unlinked segment fell back to `singular(parent)` — "Content" — reading "Content / Content /
	// Edit" instead of naming the item its own crumb is about.
	'/content/[uid]': (bag) => nameOf(bag.item)
};

/** The loaded entity's display name, or null when nothing on the page names one. */
function entityLabel(data: unknown): string | null {
	if (!data || typeof data !== 'object') return null;
	const bag = data as Record<string, unknown>;
	// A page may name its own crumb outright — the escape hatch for a record whose display name is
	// composed rather than a field (and the contract for any future section: return `crumb` from load).
	if (typeof bag.crumb === 'string' && bag.crumb.trim()) return bag.crumb.trim();
	const contact = contactLabel(bag);
	if (contact) return contact;
	for (const key of ENTITY_KEYS) {
		const label = nameOf(bag[key]);
		if (label) return label;
	}
	return null;
}

/** "request-items" → "Request items". */
function titleCase(segment: string): string {
	const words = segment.replace(/[-_]+/g, ' ').trim();
	return words ? words[0].toUpperCase() + words.slice(1) : segment;
}

/** Naive singular of a crumb label ("Requests" → "Request", "Address" → "Address"). Only ever used
 * for the placeholder on a dynamic segment whose entity didn't load. */
function singular(label: string): string {
	return /[^s]s$/i.test(label) ? label.slice(0, -1) : label;
}

const isDynamic = (routeSegment: string): boolean => routeSegment.startsWith('[');

/**
 * Crumbs for `pathname`, or `null` when it isn't one of the sections above (the caller falls back to
 * the nav label). `routeId` is `page.route.id` — the matched route, e.g. `/documents/requests/[uid]`.
 * `data` is `page.data`.
 */
export function sectionBreadcrumb(
	pathname: string,
	routeId: string | null | undefined,
	data: unknown
): Crumb[] | null {
	const segs = pathname.split('/').filter(Boolean);
	const section = segs[0] ?? '';
	if (!(section in SECTION_LABELS)) return null;

	// A section root is the page you are ON, so it gets no href: a crumb linking to itself is a dead
	// control. (It is also what every non-document section used to render via the nav-label fallback,
	// so widening SECTION_LABELS to the whole sidebar doesn't change how a listing page reads.)
	const crumbs: Crumb[] =
		segs.length > 1
			? [{ label: SECTION_LABELS[section], href: `/${section}` }]
			: [{ label: SECTION_LABELS[section] }];
	// Layout groups appear in a route id but never in a pathname.
	const routeSegs = (routeId ?? '')
		.split('/')
		.filter((s) => s && !(s.startsWith('(') && s.endsWith(')')));
	// No matched route (a 404), or a rest/optional param that doesn't line up 1:1 with the path: show
	// the section alone rather than inventing labels for segments we can't classify.
	if (routeSegs.length !== segs.length) return crumbs;

	for (let i = 1; i < segs.length; i++) {
		const routePath = `/${routeSegs.slice(0, i + 1).join('/')}`;
		const parent = crumbs[crumbs.length - 1].label;
		const bag = data && typeof data === 'object' ? (data as Record<string, unknown>) : {};
		// A route that names its own levels is AUTHORITATIVE for them: falling back to "the one
		// entity on the page" is exactly what would put the client's name in the document's crumb
		// when the document failed to load.
		const named = DYNAMIC_LABELS[routePath];
		const label = isDynamic(routeSegs[i])
			? ((named ? named(bag) : entityLabel(data)) ?? singular(parent))
			: (STATIC_LABELS[routePath] ?? titleCase(segs[i]));
		const last = i === segs.length - 1;
		if (last) {
			crumbs.push({ label });
			continue;
		}
		// An override wins over the path, and an explicit `null` means this ancestor has no page.
		const href =
			routePath in CRUMB_HREFS ? CRUMB_HREFS[routePath] : `/${segs.slice(0, i + 1).join('/')}`;
		crumbs.push(href === null ? { label } : { label, href });
	}
	return crumbs;
}

/** `/documents` and `/documents/requests` refuse a viewer without `documents:show` (their loaders
 *  check it), but a document record and a request detail stay reachable on `documents:read` alone,
 *  so a viewer without `documents:show` must not be handed a crumb to either. Strips just those
 *  hrefs; every other crumb is unaffected. */
const SHOW_GATED_HREFS = new Set(['/documents', '/documents/requests']);

export function withDocumentsGate(crumbs: Crumb[], canShowDocuments: boolean): Crumb[] {
	if (canShowDocuments) return crumbs;
	return crumbs.map((c) => (c.href && SHOW_GATED_HREFS.has(c.href) ? { label: c.label } : c));
}
