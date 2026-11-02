/**
 * Composed portal-page loading — the server-side half of the pages contract (pkgs/ui
 * portal-pages.ts / app/branding.py). One function, `loadPortalPage`, turns a slug into
 * everything the shared `PortalPage` renderer needs: the page definition, its audience-resolved
 * blocks with CONCRETE variants (the renderer never sees 'auto'), and the union of the blocks'
 * data slices fetched best-effort.
 *
 * THE LEAK RULE (do not weaken): the returned `data` is ALWAYS a non-null object for a signed-in
 * client, even when every fetch failed. `data: null` is the studio preview's demo switch —
 * resolveSlices (pkgs/ui slices.ts) renders fixture documents/holdings/adviser wholesale for
 * null — so passing null here would show a real client fabricated data the moment the API
 * hiccuped. A failed slice stays absent and the block renders its empty state instead.
 */
import type { Branding, BrandingBlock, BrandingPage } from '@graphene/auth';
import {
	CLIENT_BLOCK_BY_TYPE,
	defaultPages,
	matchesText,
	resolveAudienceLayout,
	resolveVariant,
	type Adviser,
	type BlockType,
	type ClientDocument,
	type ClientHomeData,
	type DataSource,
	type DocumentCategory,
	type DocumentFormat,
	type ListingQuery,
	type NewsItem,
	type PortalPageData,
	type VariantContext,
	listingLinks,
	type ListingLinks
} from '@graphene/ui';
import { toActivity } from '$lib/activity';
import { isOpenAccount } from '$lib/account-facts';
import { adviserOrFirm } from '$lib/adviser';
import {
	apiUrl,
	authHeaders,
	fetchJson,
	type Holding as ApiHolding,
	type Transaction as ApiTransaction
} from '$lib/api';
import { toClientDoc, type ApiDocument } from '$lib/documents';
import { toPortalHolding } from '$lib/holdings';
import type { SliceFailure } from '$lib/load-errors';
import { formatDate, formatSize } from '$lib/format';

// Re-exported so the loader's consumers keep one import site; the IMPLEMENTATION lives in
// load-errors.ts because a component cannot import a value from this module (see its header).
export type { SliceFailure } from '$lib/load-errors';

/** What toClientDoc emits (the ClientDoc shape) — derived rather than imported so this module
 * stays outside the frozen `$lib/demo` importer list (demo.test.ts's boundary). */
type MappedDocument = ReturnType<typeof toClientDoc>;

/** What a composed route hands to the shared renderer (`PortalPage`). */
export interface ComposedPage {
	page: BrandingPage;
	/** Audience-resolved, visible blocks whose `settings.variant` is already concrete. */
	blocks: BrandingBlock[];
	/** Always non-null for a signed-in client — see the leak rule above. */
	data: PortalPageData;
	/** The server-side pagination window PortalPage's table variants render `?page={n}` links
	 * from. Null when no fetch was paginated this load — the pager must not appear under a slice
	 * that IS the whole dataset. */
	pagination: { page: number; pageSize: number } | null;
	/** Every account this client holds, named the way the listing rows name them — the account
	 * filter's options on a toolbar'd holdings/activity page. Null when no visible block shows a
	 * toolbar, in which case nothing asks for them.
	 *
	 * Supplied rather than left to the renderer because these toolbars run CONTROLLED: the
	 * server has already cut the rows down to the chosen account, so options derived from them
	 * would name only that one and the control would then hide itself — a filter in force with
	 * nothing on the page able to lift it. */
	listingAccounts: string[] | null;
	/** Where each listing card's "View all" points, from the firm's effective page set — null
	 * where no other page shows the source, so the card draws no link rather than one to a 404
	 * (`listingLinks`, @graphene/ui). */
	links: ListingLinks;
	/** Money actions this firm has switched off. The portfolio header omits those buttons.
	 *  A failed read is an empty list, so the controls stay. */
	disabledActions: string[];
	/** Which slice reads the API did not answer, if any. Empty when the page loaded cleanly.
	 *
	 * Every slice is best-effort by design — one failing read must not take the page down — but
	 * the consequence is that "the API returned nothing" and "you own nothing" render IDENTICALLY,
	 * and a client cannot tell the difference. That is the whole of why an empty portal took days
	 * to diagnose: a 401 from a misconfigured deployment looks exactly like a new client's first
	 * visit, in the browser AND in the page data. The reads still degrade; the page now says so.
	 */
	loadErrors: SliceFailure[];
}

/** One paginated page of a listing fetch. 25 rows is a screenful; PortalPage's pager derives
 * its page count from the same figure via `pagination.pageSize`, so it lives here once. */
export const PAGE_SIZE = 25;

/** Clamp a raw `?page=` value (string, number, junk, absent) to a 1-based page number. Junk —
 * NaN, negatives, zero, '' — reads as page 1: a mangled link degrades to the first page, never
 * a 400 or a negative offset. */
export function clampPage(raw: unknown): number {
	const n = Math.floor(Number(raw));
	return Number.isFinite(n) && n >= 1 ? n : 1;
}

/** `GET /crm/context` — who the signed-in client is and how big their book is. The response
 * shape is frozen on the API side (the endpoint's docstring pins the exact key set). */
export type PortalContext = {
	contact_id: number | null;
	type: 'individual' | 'organisation' | null;
	service_type: string | null;
	account_count: number;
	valued_account_count: number;
	adviser: Adviser | null;
};

/** Just the keys the actionItems block consumes from `GET /tasks/me` (docs/tasks.md). */
type TaskOut = { uid: string; title: string; description: string | null; due_date: string | null };

/** Just enough of the API's `RequestOut` (`GET /documents/requests`) to count what is still
 * outstanding for this client — the documents block's `show_requests` banner. */
type ApiRequest = { status: string; items: { status: string }[] };

/** Just the fields of a bare `GET /crm/accounts` row the account-name → id resolution reads
 * (the toolbar's account values are display NAMES; /crm/holdings filters by `account_id`). */
type AccountLite = { id: number; uid: string; name: string | null; status?: string | null };

/** Can this account appear on a holdings or activity listing at all? A DRAFT is an application
 * in progress: no holdings (the API's `_VALUED` predicate excludes it) and no transactions, so
 * offering it as a filter option is a choice that filters to nothing — and, unnamed, it is
 * offered by its uid ("acc_b014d4b5…"), which is exactly the string a client must never see.
 * Closed accounts stay: they still have history on the activity listing. The same rule keeps a
 * draft off the transfer page's picker (`$lib/account-facts::isOpenAccount`). */
const listable = (a: AccountLite): boolean => isOpenAccount(a);

/** How an account is NAMED on a listing row, and therefore the value its filter carries.
 *
 * `COALESCE(name, uid, 'Account')` — the API's own labelling of a holding's owning account
 * (crm.py `_visible_accounts`). Written once here because the toolbar's options, the name → id
 * resolution and the rows all have to agree on the spelling: an option that does not match what
 * the rows carry filters to nothing, which reads as an empty book. */
const accountLabel = (a: AccountLite): string => a.name ?? a.uid ?? 'Account';

/** Just the progress counters (+ the milestone rollup) from `GET /applications/me` (the
 * onboardingProgress block). */
type ApplicationsMe = {
	done: number;
	total: number;
	stages?: { key: string; label: string; status: 'done' | 'current' | 'upcoming' }[];
	/** Steps the client can act on now — what the block phrases as a call to action. */
	available?: number;
};

type CrmContentCard = {
	uid: string;
	kind: string;
	title: string;
	summary: string | null;
	published_at: string | null;
	category_name: string | null;
	hero_url?: string | null;
};

type CrmContentList = { items: CrmContentCard[]; total: number };

export function toPortalNews(item: CrmContentCard): NewsItem {
	return {
		id: item.uid,
		source: item.category_name || (item.kind === 'news' ? 'Market news' : 'Insights'),
		headline: item.title,
		summary: item.summary ?? '',
		time: item.published_at ? formatDate(item.published_at) : '',
		href: `/insights/${item.uid}`,
		tag: item.kind === 'guide' ? 'Guide' : undefined,
		kind: item.kind === 'article' || item.kind === 'guide' ? item.kind : 'news',
		heroUrl: item.hero_url ?? null
	};
}

/** The home card reads the dozen newest NEWS items; the `library` variant is the whole published
 * library — every kind, the API's page ceiling — because it IS the Insights page and filters
 * by kind client-side. */
const NEWS_FEED_LIMIT = 12;
const NEWS_LIBRARY_LIMIT = 50;

export function wantsNewsLibrary(blocks: BrandingBlock[]): boolean {
	return blocks.some(
		(b) => b.type === 'news' && b.visible !== false && storedVariant(b) === 'library'
	);
}

export function contentQuery(blocks: BrandingBlock[]): string {
	return wantsNewsLibrary(blocks)
		? `limit=${NEWS_LIBRARY_LIMIT}`
		: `kind=news&limit=${NEWS_FEED_LIMIT}`;
}

/** The loader only reads these off `App.Locals`; typed as a Pick so tests can hand it a stub
 * without fabricating the whole WorkOS surface. */
type PageLocals = Pick<App.Locals, 'accessToken' | 'branding'> &
	Partial<Pick<App.Locals, 'classification' | 'appName'>>;

type Fetch = typeof globalThis.fetch;

/** The tenant's effective page set: a non-empty custom set wins, else the defaults — home,
 * portfolio, activity, documents and insights. */
export function pagesOf(branding: Branding | null | undefined): BrandingPage[] {
	const pages = branding?.pages;
	return pages && pages.length > 0 ? pages : defaultPages();
}

/** The per-audience layout key for a contact type. Null/unknown types (an unlinked principal,
 * a degraded context fetch) read as 'individual' — the safe default layout. */
export function audienceOf(type: string | null | undefined): 'individual' | 'organisation' {
	return type === 'organisation' ? 'organisation' : 'individual';
}

/** The union of the given blocks' data sources — the page fetches only what its blocks read.
 * Unknown/future block types contribute nothing (they render a placeholder, not data). */
export function sourceUnion(blocks: BrandingBlock[]): Set<DataSource> {
	const union = new Set<DataSource>();
	for (const b of blocks) {
		for (const s of CLIENT_BLOCK_BY_TYPE[b.type]?.sources ?? []) union.add(s);
	}
	return union;
}

/** The slices that all ride the one `GET /crm/home` payload (it carries totals, performance,
 * wrappers, allocations, holdings, activity and the adviser in a single lake read). */
const HOME_SOURCES: readonly DataSource[] = [
	'portfolio',
	'accounts',
	'holdings',
	'activity',
	'adviser'
];

// ── variant-aware source fetching ────────────────────────────────────────────────────────────
// The stored (pre-resolution) variant decides WHAT to fetch, before any data exists to resolve
// 'auto' against. Two of the auto rules are decidable pre-fetch — accounts keys only off
// /crm/context's account_count, and holdings' organisation arm off the audience — so those
// drive dedicated fetches; the rest resolve after the data lands, as before.

/** A block's stored variant setting, unresolved — junk and absent read as 'auto', exactly as
 * resolveVariant will later treat them. */
function storedVariant(b: BrandingBlock): string {
	const v = b.settings?.['variant'];
	return typeof v === 'string' ? v : 'auto';
}

/** The accounts looks that need the FULL per-account wrappers list to render every row. */
const FULL_WRAPPER_VARIANTS = ['stack', 'cards', 'table'];

/**
 * Which `wrappers` mode to ask `/crm/home` for. The accounts auto rule keys ONLY off
 * `ctx.accountCount` (variants.ts), which /crm/context already supplied — so the mode is
 * decidable before the home fetch:
 *
 * - an accounts block explicitly pinned to stack/cards/table ⇒ 'full' — an explicit choice must
 *   not be summarised out from under the firm, so this wins even when a sibling accounts block
 *   would summarise (that block derives its groups client-side from the full list);
 * - otherwise an accounts block resolving to 'summary' (explicit or auto past the cards
 *   threshold) ⇒ 'summary' — the top-10 + wrapper_groups digest is all it renders;
 * - otherwise ⇒ 'auto' — no accounts block (or one auto-resolving to a small look), so let the
 *   API keep the payload bounded for a big book: home totals stay cheap either way.
 */
export function wrappersModeOf(
	blocks: BrandingBlock[],
	preCtx: VariantContext
): 'full' | 'auto' | 'summary' {
	const accounts = blocks.filter((b) => b.type === 'accounts');
	if (accounts.some((b) => FULL_WRAPPER_VARIANTS.includes(storedVariant(b)))) return 'full';
	if (accounts.some((b) => resolveVariant('accounts', b.settings, preCtx) === 'summary'))
		return 'summary';
	return 'auto';
}

/**
 * Whether a holdings block wants the dedicated paginated `/crm/holdings` fetch instead of the
 * home payload's top-5: stored 'table', or stored 'auto' (or junk) for an organisation — the
 * two arms of the holdings auto rule that are decidable BEFORE any data exists (explicit
 * choice; audience). The rule's third arm — an individual whose book is past holdingsTableMin —
 * needs the holdings count, which isn't knowable pre-fetch, so that client keeps 'top' on
 * composed pages: an accepted gap. An explicit 'table' or the organisation audience unlocks
 * the full listing.
 */
function wantsHoldingsListing(blocks: BrandingBlock[], audience: string): boolean {
	return blocks.some(
		(b) =>
			b.type === 'holdings' &&
			(storedVariant(b) === 'table' ||
				storedVariant(b) === 'grouped' ||
				(storedVariant(b) !== 'top' && audience === 'organisation'))
	);
}

/** The 'grouped' holdings variant sections the WHOLE book by asset class, so it reads one window
 * big enough for any client's book rather than a 25-row page: grouping a page would print a
 * class subtotal and share that are a page's, not the portfolio's. */
const GROUPED_HOLDINGS_LIMIT = 500;
const wantsGroupedHoldings = (blocks: BrandingBlock[]): boolean =>
	blocks.some((b) => b.type === 'holdings' && storedVariant(b) === 'grouped');

/**
 * Whether an activity block wants the dedicated `/crm/transactions?limit=200` fetch instead of
 * the home payload's six recent rows: stored 'table', or stored 'auto' for an organisation.
 * NOTE the activity auto rule itself has NO audience arm (variants.ts resolves purely on row
 * count) — the organisation trigger here is a fetch heuristic only: an organisation's book is
 * big enough that the six-row home slice would pin auto on 'list' forever, so fetch the full
 * window and let the count rule resolve honestly. 200 rows is the ceiling, as the legacy
 * /activity page: the endpoint has no `{items, total}` envelope (no `activity_total` in the
 * contract), so activity never paginates.
 *
 * A TOOLBAR'd block is a listing whatever its variant: the default Activity page is the home
 * card's `list` with the toolbar on (one style for activity, not a table on the tab and rows on
 * home), and a search box over the home payload's six rows would be searching nothing.
 */
function wantsActivityListing(blocks: BrandingBlock[], audience: string): boolean {
	return blocks.some(
		(b) =>
			b.type === 'activity' &&
			(storedVariant(b) === 'table' ||
				b.settings?.['toolbar'] === true ||
				(storedVariant(b) !== 'list' && audience === 'organisation'))
	);
}

// NOTE: `GET /crm/accounts` (paginated + `?q=` on the API) is still NOT what feeds the accounts
// BLOCK — it renders from the wrappers slice on the home payload, so `accounts_total` stays
// unset and the accounts table never shows a pager this phase. That also means `?q=` has no
// server fetch to thread into on the /accounts page (the accounts block carries no toolbar
// either). Paginating/searching the accounts table against /crm/accounts is a follow-up; the
// summary variant is the trust path. The only /crm/accounts read below is the account-name → id
// resolution for a toolbar'd holdings block's account filter.

/** Whether a visible block of `type` has the listing toolbar switched on. The stored setting —
 * not anything resolved — because PortalPage renders the toolbar off the same raw flag. */
function hasToolbar(blocks: BrandingBlock[], type: BlockType): boolean {
	return blocks.some((b) => b.type === type && b.settings?.['toolbar'] === true);
}

// `toPortalHolding` moved to `$lib/holdings` (a universal module) so the account record's
// component can import it; re-exported here for the loaders that always read it from this file.
export { toPortalHolding };

// ── documents: API shape → the renderer's ClientDocument ────────────────────────────────────
// The DocumentsList block renders pkgs/ui's ClientDocument (five fixed display categories, a
// short format badge, pre-formatted date/size), while the API's document_type is a free-text
// slug. Bucket the slugs into the five categories the block can group under — anything
// unrecognised lands in Correspondence rather than vanishing from the grouped view.

const CATEGORY_BUCKETS: readonly [RegExp, DocumentCategory][] = [
	[/agreement/, 'Agreements'],
	[/illustration/, 'Illustrations'],
	[/statement|report|review|valuation/, 'Statements'],
	[/tax/, 'Tax']
];

function documentCategory(slug: string): DocumentCategory {
	const s = slug.toLowerCase();
	for (const [re, category] of CATEGORY_BUCKETS) if (re.test(s)) return category;
	return 'Correspondence';
}

/** The block's format badge. ClientDocument.format is a closed three-member union, so anything
 * that isn't CSV or Word (images included) shows as PDF — the badge is a hint, not a claim. */
function documentFormat(contentType: string | undefined): DocumentFormat {
	if (contentType === 'text/csv') return 'CSV';
	if (contentType?.includes('word')) return 'DOCX';
	return 'PDF';
}

/** Project one `toClientDoc`-mapped document into the shape the documents block renders.
 * `unread` rides through so the toolbar's unread note works for real clients — it stays
 * OPTIONAL on ClientDocument, and listing.ts's unreadNote renders nothing when no document
 * carries the field (a loader that doesn't know read state must not imply "everything's read"). */
export function toPortalDocument(d: MappedDocument): ClientDocument {
	return {
		id: d.uid,
		name: d.name,
		category: documentCategory(d.category),
		date: formatDate(d.createdAt),
		isoDate: d.createdAt,
		size: formatSize(d.sizeBytes),
		format: documentFormat(d.contentType),
		unread: d.unread
	};
}

/** What `/crm/home` ships: the ClientHomeData slices — which now include the provenance stamps
 * (`as_of`, `performance_period`, and `value_as_of` on each wrapper row; wire-named exactly as
 * the API emits them) — plus, in summary mode only, the three summary keys (wire-named exactly
 * as PortalPageData declares them). Spreading the payload into the data object carries all of
 * them through untouched, so the headers/cards can stamp "as at …" without loader plumbing. */
type HomePayload = ClientHomeData &
	Pick<PortalPageData, 'wrapper_groups' | 'wrappers_total' | 'wrappers_summarised'>;

/** The raw slice fetches a page's blocks asked for (null = not requested OR failed). */
export interface FetchedSlices {
	home: HomePayload | null;
	documents: ApiDocument[] | null;
	tasks: TaskOut[] | null;
	applications: ApplicationsMe | null;
	requests: ApiRequest[] | null;
	news: CrmContentList | null;
}

/**
 * Assemble the renderer's data object from whatever the slice fetches returned. Always a
 * non-null object (the leak rule): a failed slice is simply absent, so its block renders the
 * empty state — never the demo fixtures that `data: null` would switch on.
 */
export function assemblePageData(
	fetched: FetchedSlices,
	contextAdviser: Adviser | null
): PortalPageData {
	const data: PortalPageData = { ...(fetched.home ?? {}) };
	if (fetched.documents)
		data.documents = fetched.documents.map((d) => toPortalDocument(toClientDoc(d)));
	if (fetched.tasks)
		data.tasks = fetched.tasks.map(({ uid, title, description, due_date }) => ({
			uid,
			title,
			description,
			due_date
		}));
	if (fetched.applications)
		data.onboarding = {
			done: fetched.applications.done,
			total: fetched.applications.total,
			stages: fetched.applications.stages,
			available: fetched.applications.available
		};
	// The documents block's show_requests banner counts still-pending ITEMS on still-pending
	// requests (the legacy /documents page's reduction, verbatim). A failed fetch leaves the key
	// ABSENT — resolveSlices reads that as null and renders no banner: a count is stated only
	// when it was really fetched, never fabricated (the leak rule).
	if (fetched.requests)
		data.requests_outstanding = fetched.requests
			.filter((r) => r.status === 'pending')
			.reduce((n, r) => n + r.items.filter((i) => i.status === 'pending').length, 0);
	// The adviser block declares the (always-fetched) 'context' source, so /crm/context can feed
	// it on pages that never touch /crm/home; a home payload's adviser wins when both exist.
	if (!data.adviser && contextAdviser) data.adviser = contextAdviser;
	if (fetched.news) data.news = fetched.news.items.map(toPortalNews);
	return data;
}

/**
 * Copies of the blocks with `settings.variant` resolved to a CONCRETE look. PortalPage treats
 * the setting as already-resolved, so 'auto' (or junk) must be settled here, from the client's
 * own numbers. Copies — never mutate the blocks (they alias `locals.branding`, which is shared
 * request state).
 */
export function resolveBlockVariants(
	blocks: BrandingBlock[],
	data: PortalPageData,
	ctx: Pick<VariantContext, 'audience' | 'accountCount' | 'valuedAccountCount' | 'pageSlug'>
): BrandingBlock[] {
	const full: VariantContext = {
		...ctx,
		// A known total outranks the slice length: a paginated page of 25 must not read as
		// "25 holdings" when the book behind it has 400 — the auto rules size against the BOOK.
		// Activity/documents carry no totals (bare-list endpoints), so their lengths stand.
		holdingsCount: data.holdings_total ?? data.holdings?.length ?? 0,
		activityCount: data.activity?.length ?? 0,
		documentsCount: data.documents?.length ?? 0
	};
	return blocks.map((b) => ({
		...b,
		// Unknown types resolve to 'auto' inside resolveVariant (single-variant fallback), so the
		// cast is safe for future block types this app hasn't learnt yet.
		settings: { ...b.settings, variant: resolveVariant(b.type as BlockType, b.settings, full) }
	}));
}

/**
 * Copies of the blocks with the portfolio header's `actions` setting gated on the client's book.
 *
 * `docs/client-classifications.md`: `capabilities.money_movement` is THE gate for client-initiated
 * payments and withdrawals, and "the shopfront must not offer a form the advised book cannot
 * use". The transfer routes already 404 without it — but the header's Deposit/Withdraw pair is a
 * stored layout setting that knows nothing about who is looking, so an Advised client was shown
 * two buttons that both landed on "We couldn't find that page". The setting's own default is the
 * pair (`normaliseActions`), so anything that is not an explicit 'none' or 'contact' becomes
 * 'none' here when money movement is off: the row goes, rather than being swapped for a
 * "Contact your adviser" button — the shell's top bar already carries the message button on
 * every page, and a second one on the hero read as a form the client was being pushed towards
 * (Oct 2026). A firm that WANTS the contact button on the hero sets 'contact' explicitly and
 * keeps it. A missing classification (fetch failed, or a test stub) is treated as off: the safe
 * error is a client shown no money buttons, not a client sent to a 404.
 */
export function gateHeaderActions(
	blocks: BrandingBlock[],
	classification: App.Locals['classification'] | undefined
): BrandingBlock[] {
	if (classification?.capabilities?.money_movement === true) return blocks;
	return blocks.map((b) => {
		if (b.type !== 'portfolioHeader') return b;
		const actions = b.settings?.['actions'];
		if (actions === 'none' || actions === 'contact') return b;
		return { ...b, settings: { ...b.settings, actions: 'none' } };
	});
}

/** One best-effort slice fetch: parsed payload or null. fetchJson already retries transient
 * failures and never throws — a transient failure must not read as "no data", and equally must
 * never take the whole page down. */
async function slice<T>(
	fetch: Fetch,
	url: string,
	headers: Record<string, string>,
	failures?: SliceFailure[]
): Promise<T | null> {
	const res = await fetchJson<T>(fetch, url, headers);
	if (!res.ok) failures?.push({ slice: sliceName(url), status: res.status });
	return res.ok ? res.data : null;
}

/** The endpoint's own name, without the base URL or the query string — enough to say WHICH read
 * failed, and nothing that would put an access token or a client's filter terms on screen. */
function sliceName(url: string): string {
	const path = url.replace(apiUrl(), '');
	return path.split('?')[0] || path;
}

/** Appends `contact_id` to a slice URL when a login is linked to more than one crm.contacts
 * row and one is actively selected (the nav bar's client switcher) — `null` (unlinked staff,
 * or a login with exactly one contact) leaves every URL exactly as it was before this existed. */
function withContact(url: string, activeContactId: number | null): string {
	if (activeContactId === null) return url;
	return `${url}${url.includes('?') ? '&' : '?'}contact_id=${activeContactId}`;
}

/** Compose one resolved page definition: audience, blocks, slice fetches, variants.
 * `listing` is the URL-owned toolbar state (`?q=`/`?account=`, parsed by listing-url.ts) — the
 * composed pages' toolbars run CONTROLLED (composed-page.svelte provides onlistingchange), so
 * client-side q/account filtering is off in pkgs/ui and the answer must be produced here.
 * `activeContactId` narrows every slice to one linked contact (see `withContact`). */
async function composePage(
	page: BrandingPage,
	locals: PageLocals,
	fetch: Fetch,
	pageNumber: number,
	listing: ListingQuery = {},
	activeContactId: number | null = null
): Promise<ComposedPage> {
	const headers = authHeaders(locals.accessToken);
	const wanted = clampPage(pageNumber);
	// Collected across every slice below, so the page can say which reads the API refused rather
	// than rendering their absence as a client who owns nothing.
	const loadErrors: SliceFailure[] = [];

	// Context first — the audience picks the layout, and the layout decides what to fetch.
	// Best-effort like every other slice: a failure degrades to the individual audience with
	// zero counts rather than throwing (the page must render, whatever the API is doing).
	const context = await slice<PortalContext>(
		fetch,
		withContact(`${apiUrl()}/crm/context`, activeContactId),
		headers,
		loadErrors
	);
	const audience = audienceOf(context?.type);
	const accountCount = context?.account_count ?? 0;
	const valuedAccountCount = context?.valued_account_count ?? 0;
	const visible = resolveAudienceLayout(page, audience).filter((b) => b.visible !== false);
	const sources = sourceUnion(visible);

	// The pre-fetch variant context: the accounts auto rule reads ONLY accountCount and the
	// holdings/activity fetch triggers read only the stored variant + audience, so the
	// data-derived counts are safely zero here — nothing consulted below looks at them.
	const preCtx: VariantContext = {
		audience,
		accountCount,
		valuedAccountCount,
		holdingsCount: 0,
		activityCount: 0,
		documentsCount: 0,
		pageSlug: page.slug
	};
	const wrappersMode = wrappersModeOf(visible, preCtx);
	// The toolbar'd listing blocks run controlled, so an ACTIVE `?q=`/`?account=` also forces the
	// dedicated fetch even where the variant alone wouldn't (e.g. a toolbar'd 'top' holdings
	// block): the home payload's compact slice (top-5 / six rows) is a partial window, and
	// answering a search against a fraction of the book is a silently wrong answer. The trigger
	// gates on the block actually SHOWING a toolbar — a stray param on a toolbar-less page must
	// not filter data invisibly.
	const holdingsToolbar = hasToolbar(visible, 'holdings');
	const activityToolbar = hasToolbar(visible, 'activity');
	const filtered = Boolean(listing.q || listing.account);
	const fetchHoldings = wantsHoldingsListing(visible, audience) || (holdingsToolbar && filtered);
	const fetchActivity = wantsActivityListing(visible, audience) || (activityToolbar && filtered);
	const fetchRequests = visible.some(
		(b) => b.type === 'documents' && b.settings?.['show_requests'] === true
	);

	/**
	 * The client's own accounts, read once and used for two things: resolving the account
	 * filter's display NAME to the `account_id` /crm/holdings filters by, and supplying the
	 * toolbar's option list.
	 *
	 * Fetched whenever a toolbar is ON SCREEN rather than only when a filter is active — the
	 * options are needed precisely so a reader can pick a second account or clear the first, and
	 * a list fetched only while filtering cannot offer either. Started here, awaited inside the
	 * slice fetches, so it runs alongside them rather than in front of them.
	 */
	const accountsSlice =
		holdingsToolbar || activityToolbar
			? slice<AccountLite[]>(
					fetch,
					withContact(`${apiUrl()}/crm/accounts`, activeContactId),
					headers,
					loadErrors
				)
			: null;

	/** The paginated /crm/holdings window, with the toolbar's query answered SERVER-SIDE (`q`
	 * searches asset name + ISIN on the API, so a paged `total` is the filtered match count).
	 * The account filter's values are display NAMES (the labels the rows carry — listing.ts
	 * accountItems), while the API filters by `account_id`, so an active account filter first
	 * resolves the name against the bare /crm/accounts list (RLS-scoped; the same
	 * COALESCE(name, uid, 'Account') labelling the API tags holdings with — `accountLabel`).
	 * A name that no longer resolves — a stale link, a renamed account — DROPS the filter
	 * rather than erroring, and the toolbar still offers the unresolved value so the reader can
	 * see what is in force and switch it off (listing.ts `accountItems`, `active`). */
	async function holdingsWindow(): Promise<{ items: ApiHolding[]; total: number } | null> {
		const grouped = wantsGroupedHoldings(visible);
		const params = new URLSearchParams({
			limit: String(grouped ? GROUPED_HOLDINGS_LIMIT : PAGE_SIZE),
			offset: String(grouped ? 0 : (wanted - 1) * PAGE_SIZE)
		});
		if (holdingsToolbar && listing.q) params.set('q', listing.q);
		if (holdingsToolbar && listing.account) {
			const accounts = await accountsSlice;
			const id = accounts?.find((a) => accountLabel(a) === listing.account)?.id;
			if (id != null) params.set('account_id', String(id));
		}
		return slice<{ items: ApiHolding[]; total: number }>(
			fetch,
			withContact(`${apiUrl()}/crm/holdings?${params.toString()}`, activeContactId),
			headers,
			loadErrors
		);
	}

	// Fetch the union of the visible blocks' sources in parallel, each independently
	// best-effort — one failing slice empties its own blocks, not its neighbours'.
	const [
		accounts,
		home,
		holdingsPage,
		transactions,
		documents,
		tasks,
		applications,
		requests,
		news,
		moneyActions
	] = await Promise.all([
		accountsSlice,
		HOME_SOURCES.some((s) => sources.has(s))
			? slice<HomePayload>(
					fetch,
					withContact(`${apiUrl()}/crm/home?wrappers=${wrappersMode}`, activeContactId),
					headers,
					loadErrors
				)
			: null,
		fetchHoldings ? holdingsWindow() : null,
		fetchActivity
			? slice<ApiTransaction[]>(
					fetch,
					withContact(`${apiUrl()}/crm/transactions?limit=200`, activeContactId),
					headers,
					loadErrors
				)
			: null,
		sources.has('documents')
			? slice<ApiDocument[]>(
					fetch,
					withContact(`${apiUrl()}/documents`, activeContactId),
					headers,
					loadErrors
				)
			: null,
		sources.has('tasks')
			? slice<TaskOut[]>(
					fetch,
					withContact(`${apiUrl()}/tasks/me`, activeContactId),
					headers,
					loadErrors
				)
			: null,
		sources.has('applications')
			? slice<ApplicationsMe>(
					fetch,
					withContact(`${apiUrl()}/applications/me`, activeContactId),
					headers,
					loadErrors
				)
			: null,
		fetchRequests
			? slice<ApiRequest[]>(
					fetch,
					withContact(`${apiUrl()}/documents/requests`, activeContactId),
					headers,
					loadErrors
				)
			: null,
		sources.has('content')
			? slice<CrmContentList>(
					fetch,
					withContact(`${apiUrl()}/crm/content?${contentQuery(visible)}`, activeContactId),
					headers,
					loadErrors
				)
			: null,
		// Only a page that paints the portfolio header can show Payment / Withdraw. A failed
		// read leaves those controls visible, and is not a page error.
		visible.some((b) => b.type === 'portfolioHeader')
			? slice<{ disabled?: string[] }>(fetch, `${apiUrl()}/crm/money/actions`, headers)
			: null
	]);
	const disabledMoney = Array.isArray(moneyActions?.disabled) ? moneyActions.disabled : [];

	// Nobody assigned ⇒ the FIRM is the contact ($lib/adviser::firmContact): its name, icon and
	// support details, flagged `kind: 'firm'` so the adviser looks head the block "Your firm".
	// Only when the context read SUCCEEDED — a failed read is a gap the page reports, not a
	// client with no adviser (the leak rule).
	const data = assemblePageData(
		{ home, documents, tasks, applications, requests, news },
		context ? adviserOrFirm(context.adviser, locals.branding, locals.appName ?? null) : null
	);
	// The dedicated listing fetches override the home payload's compact slices (top-5 holdings,
	// six recent movements) with the full window their table variants render. A FAILED dedicated
	// fetch leaves the home slice standing — a degraded page beats an empty one, and the leak
	// rule holds either way (real data or an empty state, never fixtures).
	if (holdingsPage) {
		data.holdings = holdingsPage.items.map(toPortalHolding);
		data.holdings_total = holdingsPage.total;
	}
	if (transactions) data.activity = transactions.map(toActivity);
	// The activity toolbar's answer, produced HERE: /crm/transactions has no `q` param, and in
	// controlled mode pkgs/ui deliberately does no client-side q/account filtering. The 200-row
	// window is the complete legacy /activity dataset, so filtering it loader-side is honest —
	// same matching rules as the uncontrolled toolbar (listing.ts). Documents stay entirely
	// client-side: their FilteredListing is never controlled (PortalPage passes it no query).
	if (activityToolbar && filtered && data.activity) {
		data.activity = data.activity.filter(
			(a) =>
				matchesText([a.description, a.type], listing.q ?? '') &&
				(!listing.account || (a.wrapper ?? '').trim() === listing.account)
		);
	}

	const blocks = gateHeaderActions(
		resolveBlockVariants(visible, data, {
			audience,
			accountCount,
			valuedAccountCount,
			pageSlug: page.slug
		}),
		locals.classification
	);
	return {
		page,
		blocks,
		data,
		// Only the holdings fetch paginates this phase (activity is a bare 200-row list and
		// /crm/accounts isn't fetched — see the note above), and only when its envelope actually
		// arrived: a pager against data the fetch never delivered would advertise pages of nothing.
		pagination:
			holdingsPage && !wantsGroupedHoldings(visible) ? { page: wanted, pageSize: PAGE_SIZE } : null,
		// Null, never `[]`, when nothing asked: an empty array is a claim that this client holds
		// no accounts, and the toolbar would then fall back to nothing rather than to the rows.
		listingAccounts: accounts ? accounts.filter(listable).map(accountLabel) : null,
		links: listingLinks(pagesOf(locals.branding), audience, page.slug),
		disabledActions: disabledMoney,
		loadErrors
	};
}

/**
 * Load the composed page for a slug from the tenant's effective page set (custom set if
 * non-empty, else the defaults). Returns null when no page carries the slug — the `[slug]`
 * route 404s on that; `/` falls back instead (see loadHomePage).
 */
export async function loadPortalPage(
	slug: string,
	locals: PageLocals,
	fetch: Fetch,
	pageNumber = 1,
	listing: ListingQuery = {},
	activeContactId: number | null = null
): Promise<ComposedPage | null> {
	const page = pagesOf(locals.branding).find((p) => p.slug === slug);
	if (!page) return null;
	return composePage(page, locals, fetch, pageNumber, listing, activeContactId);
}

/**
 * The home page, which must ALWAYS render: a firm may delete or rename its 'home' page, and
 * `/` still has to serve something — fall back to the default set's home definition rather
 * than 404 the portal's front door.
 */
export async function loadHomePage(
	locals: PageLocals,
	fetch: Fetch,
	pageNumber = 1,
	listing: ListingQuery = {},
	activeContactId: number | null = null
): Promise<ComposedPage> {
	const page =
		pagesOf(locals.branding).find((p) => p.slug === 'home') ??
		defaultPages().find((p) => p.slug === 'home');
	if (!page) throw new Error('defaultPages() must always carry a home definition');
	return composePage(page, locals, fetch, pageNumber, listing, activeContactId);
}
