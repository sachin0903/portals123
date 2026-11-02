/** Tax wrapper types for an advised investment client. */
export type WrapperType = 'GIA' | 'ISA' | 'SIPP' | 'JISA';

export interface Wrapper {
	/** Product label on the card (demo uses WrapperType; real accounts may use any string —
	 * the card art falls back for unknown values). */
	type: string;
	/** Full name, e.g. "Stocks & Shares ISA". */
	name: string;
	/** Account holder's name (the logged-in user for their own accounts). */
	owner: string;
	/** True when this isn't the user's own account — they have delegated access. */
	delegated?: boolean;
	value: number;
	gain: number;
	gainPct: number | null;
	/** ISA/JISA annual allowance used + limit (omit for GIA/SIPP). */
	allowanceUsed?: number;
	allowanceLimit?: number;
	/** Recent value trend for the sparkline. */
	trend: number[];
	/** Card link target (real accounts → /accounts/[id]); defaults to /accounts/{type}. */
	href?: string;
	/** When this account's value was struck (ISO date, from /crm/home's provenance keys).
	 * Absent/null ⇒ no stamp is rendered — never fabricated. */
	value_as_of?: string | null;
	/** The account's lifecycle status, as the API reports it (free vocabulary — 'active',
	 * 'pending', 'closed', …). The accounts widgets chip anything NON-active
	 * (account-status.ts); absent/null renders no chip — status is surfaced, never assumed. */
	status?: string | null;
}

/** One slice of the allocation donut — an asset class, its value and its chart colour. */
export interface AllocationSlice {
	label: string;
	value: number;
	color: string;
}

export interface Holding {
	name: string;
	/** Stable instrument identifier used to open the instrument's transaction history. */
	instrumentId?: string;
	ticker?: string;
	isin?: string;
	sedol?: string;
	currency?: string;
	/** Owning account label (a wrapper type for demo, an account name for real data). */
	wrapper: string;
	units: number;
	price: number;
	value: number;
	bookCost?: number;
	unrealisedGain?: number;
	/** Return as a FRACTION, or null when the custodian gives no basis to compute one from.
	 *
	 *  Nullable on purpose. Coalescing an absent basis to 0 printed "+0.0%" — a claim that a
	 *  holding has neither gained nor lost — on more than half the rows of a real book, where the
	 *  truth is that the cost is unknown. The account record already said "—" for exactly those
	 *  rows, so one page claimed a flat year and the other admitted it did not know. */
	gainPct: number | null;
	asOf?: string | null;
	href?: string;
	/** Opens the fund's underlying portfolio while `href` remains the instrument activity link. */
	lookthroughHref?: string;
	/** The allocation donut's bucket for this row ("Equities", "Cash", …), named by the API so a
	 *  row and the slice it is drawn in never disagree. */
	assetClass?: string | null;
	/** Uninvested cash. The amount is `value`; units and price are not a holding and must not
	 *  be printed. A money-market fund is not this — it is an investment the client holds. */
	cash?: boolean;
}

/** The canonical movement kinds — `platform.transaction_type`, the enum every provider's rows
 *  are conformed to. Each has a word in `ACTIVITY_LABELS` and an icon on the feed. */
export type ActivityType =
	| 'deposit'
	| 'withdrawal'
	| 'dividend'
	| 'interest'
	| 'fee'
	| 'tax'
	| 'buy'
	| 'sell'
	| 'transfer_in'
	| 'transfer_out'
	| 'corporate_action'
	| 'adjustment';

export interface Activity {
	date: string;
	/** A known ActivityType (icon) or any string (falls back to a generic icon). */
	type: ActivityType | string;
	description: string;
	/**
	 * Negative = money out / debit.
	 *
	 * NULL when the custodian has not stated one, which is NOT zero. A units-denominated "At Best"
	 * sell has no monetary value until it executes: Seccl publishes the quantity and no amount, and
	 * £0.00 against it says the movement moved nothing where the truth is that nobody knows yet.
	 * `Money` already renders an em-dash for a null amount.
	 */
	amount: number | null;
	/** ISO 4217 of the movement. Absent means the surface treats it as GBP. */
	currency?: string | null;
	/** Which account the movement belongs to (label). */
	wrapper?: string;
	/** The custodian's own words for the movement, when they say more than the kind does
	 *  ("Platform Fee", "Employer", "Accumulation Dividend"). Shown where the feed is wide
	 *  enough; absent for a bare provider code or a subtype that only repeats the kind. */
	detail?: string | null;
	/** Units traded, for a buy/sell/in-specie movement. Absent for cash. */
	quantity?: number | null;
	/** Unit price the trade was dealt at, in `currency`. */
	price?: number | null;
	/** The instrument's ISIN, when the movement names one. */
	isin?: string | null;
}

/** The real (or demo) data the client home renders. Shapes match the block components. */
export interface ClientHomeData {
	totals: { value: number; gain: number; gainPct: number | null };
	performance: { label: string; value: number }[];
	wrappers: Wrapper[];
	allocations: AllocationSlice[];
	holdings: Holding[];
	activity: Activity[];
	/** Recent documents for the home widget; omit when no source is wired (renders an empty state). */
	documents?: ClientDocument[];
	/** The client's assigned adviser for the "Your adviser" card; omit ⇒ a placeholder is shown. */
	adviser?: Adviser;
	/** The book's valuation basis (date-only ISO) — the most recent date the page's figures
	 * actually stand on (/crm/home's provenance note). Absent/null ⇒ no "as at" stamp. */
	as_of?: string | null;
	/** First/last date of the performance series, as the API returned it. Feeds the period
	 * label's honest basis ("past 6 months · to 29 Jul 2026"); null when there is no series. */
	performance_period?: { from: string; to: string } | null;
}

/** An open action item assigned to this client (tasks.tasks via GET /tasks/me). */
export interface PortalActionItem {
	uid: string;
	title: string;
	description: string | null;
	due_date: string | null;
}

/** One whole-journey milestone (from GET /applications/me's `stages`) — the client's own
 * "sign" steps plus the staff-only tiers they wait on, rolled up into a handful of stages a
 * client can make sense of. `status` is mutually exclusive: exactly one stage is 'current'
 * (the spine is sequential), everything before it 'done', everything after 'upcoming'. */
export interface PortalOnboardingStage {
	key: string;
	label: string;
	status: 'done' | 'current' | 'upcoming';
}

/** Onboarding progress for the onboardingProgress block (from GET /applications/me).
 * `stages` is additive and may be absent from an older cached response; a look that wants the
 * milestone stepper should treat a missing/empty array the same as "fall back to done/total". */
export interface PortalOnboarding {
	done: number;
	total: number;
	stages?: PortalOnboardingStage[];
	/** How many steps the client can act on RIGHT NOW (the API's `available`). Distinct from
	 * `total - done`, most of which is typically held behind the adviser's own work — so this is
	 * the only figure that can honestly be phrased as a call to action. Additive: an older cached
	 * response carries none, and a look that wants it must fall back to describing progress. */
	available?: number;
}

/** The step a client can act on NOW, named for the /onboarding hero. `index` is 1-based over the
 * steps they still owe (`total`), so a run of five terms declarations can say which one this is
 * without the component knowing anything about the flow that produced them. */
export interface OnboardingNextStep {
	step_type: string;
	title: string;
	index: number;
	total: number;
}

/** One product-type bucket of the accounts summary variant (wire shape — /crm/home's summary
 * mode groups the book server-side so a 300-account trust never ships 300 wrapper rows). */
export interface WrapperGroup {
	product_type: string;
	count: number;
	value: number;
}

/** Everything a composed portal page can feed its blocks. Every slice optional: the loader
 * fetches only the union of the page's visible blocks' sources. null/absent slice + non-null
 * payload ⇒ the block renders its empty state — NEVER demo data (see slices.ts). */
export interface PortalPageData extends Partial<ClientHomeData> {
	tasks?: PortalActionItem[] | null;
	onboarding?: PortalOnboarding | null;
	/** Server-grouped accounts for the summary variant (wire-named; rides /crm/home's summary
	 * mode). Absent ⇒ the renderer derives groups from the wrappers slice (deriveWrapperGroups). */
	wrapper_groups?: WrapperGroup[] | null;
	/** True account count when the wrappers slice is a summary/page, not the whole book. */
	wrappers_total?: number | null;
	/** True when the wrappers slice was summarised server-side (the book was too big to ship). */
	wrappers_summarised?: boolean | null;
	/** Total rows behind a paginated accounts table slice (/crm/accounts envelope). */
	accounts_total?: number | null;
	/** Total rows behind a paginated holdings table slice (/crm/holdings envelope). */
	holdings_total?: number | null;
	/** How many document requests are awaiting the client (the legacy /documents page's
	 * outstanding-requests banner). Absent/null ⇒ the documents block renders NO banner even
	 * with `show_requests` on — a count is stated only when the loader really fetched one,
	 * never fabricated (the leak rule; slices.ts). */
	requests_outstanding?: number | null;
	/** Live market news / insights cards. Absent ⇒ empty (never demo) for a signed-in client. */
	news?: NewsItem[] | null;
}

export interface Adviser {
	name: string;
	firm: string;
	/** Empty string ⇒ the firm has given no address/number, and the looks draw no Email/Call
	 * action rather than a `mailto:` to nowhere. */
	email: string;
	phone: string;
	/** Who this contact IS. Absent or `'adviser'` ⇒ a named person from `crm.client_advisers`.
	 * `'firm'` ⇒ nobody is assigned and the FIRM stands in: `name` is the firm's name, `photoUrl`
	 * its icon, `email`/`phone` its support contacts (branding `support`). The looks then drop
	 * the "Your adviser" heading (the logo and name introduce the firm) and never print a job
	 * title or firm line under the name — the firm cannot be its own employer. The conversation still works: staff who reply are named. */
	kind?: 'adviser' | 'firm';
	/** The firm's own words about this person (`auth.users`, 0118). All OPTIONAL and all omitted
	 * rather than sent as "" when unset, so a look can tell "this firm hasn't filled it in" from
	 * "this adviser has no job title" — an empty string would draw an empty row.
	 *
	 * Until 0118 there was nowhere to store any of it — `auth.users` carried an email, a name, a
	 * mobile and WorkOS's `avatar`, and nothing a FIRM says about the person. Which is why all
	 * three looks drew an initials disc: not a design choice, the only thing the schema could
	 * supply. */
	jobTitle?: string;
	/** Designations the person holds (DipPFS, APFS, Chartered FCSI). A list because they compound. */
	qualifications?: string[];
	bio?: string;
	/** The firm's headshot, else WorkOS's profile picture. Absent ⇒ the initials disc, as before. */
	photoUrl?: string;
}

export interface NewsItem {
	id: string;
	source: string;
	headline: string;
	summary: string;
	/** Display date, e.g. "12 Jun 2026". */
	time: string;
	href: string;
	tag?: string;
	/** The library's kind switch keys off this; absent reads as news. */
	kind?: 'news' | 'article' | 'guide';
	/** Hero image for the library's lead slot; null/absent renders the text-only lead. */
	heroUrl?: string | null;
}

export type DocumentCategory =
	'Agreements' | 'Illustrations' | 'Statements' | 'Tax' | 'Correspondence';

export type DocumentFormat = 'PDF' | 'CSV' | 'DOCX';

export interface ClientDocument {
	id: string;
	name: string;
	category: DocumentCategory;
	/** Display date, e.g. "12 Jun 2026". */
	date: string;
	/** ISO creation instant, for the month-grouped (`timeline`) look. Optional: fixtures and older
	 * loaders carry only the display date, and those documents file under a single unnamed run. */
	isoDate?: string;
	/** Display size, e.g. "248 KB". */
	size: string;
	format: DocumentFormat;
	/** True when the client hasn't opened this document yet. OPTIONAL on purpose: the toolbar's
	 * unread note renders only when the payload actually carries the field on some document
	 * (listing.ts unreadNote) — a loader that doesn't know read state must not imply
	 * "everything's read". The client app's loader populates it next wave. */
	unread?: boolean;
}
