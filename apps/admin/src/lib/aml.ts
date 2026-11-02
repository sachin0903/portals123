import {
	humanise,
	REFRESH_WHILE,
	type AmlMatchProfileData,
	type IdentityAnalysis,
	type IdentityUpload,
	type StatusTone
} from '@graphene/ui';

/**
 * The ComplyCube console's wire shapes and the words the console uses for them.
 *
 * One module for the client list, the client page and the client record's Compliance tab, so a
 * status reads the same — same word, same tone — wherever an operator meets it.
 */

export type AmlKind = 'aml' | 'identity';
export type AmlOrigin = 'portal' | 'partner';
export type AmlStatus = 'awaiting_screen' | 'pending' | 'refer' | 'clear' | 'rejected' | 'failed';

/** ComplyCube's overall and per-part risk levels (`GET /clients/{id}/riskProfile`). */
export type AmlRiskLevel = 'low' | 'medium' | 'high' | 'very_high' | 'not_set';

/** One client on the list: the latest check of each kind, the risk level, and whether anybody needs
 *  to act. */
export type AmlClientRow = {
	/** The client page's key: the client's id (their contact uid), or the `compliance.clients` uid
	 *  for a partner client, who has no contact. */
	key: string;
	client_name: string;
	contact_uid: string | null;
	tenant_uid: string;
	tenant_name: string;
	/** The firm's icon, from its platform-portal branding. Null when it has set none. */
	tenant_icon_url?: string | null;
	origin: AmlOrigin;
	identity: {
		uid: string;
		status: AmlStatus;
		attempt: number;
		decided: boolean;
		updated_at: string;
	} | null;
	/** `awaiting_screen` (with no uid) while the client is due a screen. */
	aml: {
		uid: string | null;
		status: AmlStatus;
		decided: boolean;
		monitoring: boolean;
		updated_at: string | null;
		/** Due a screen the worker already has queued: it starts by itself. */
		queued?: boolean;
	} | null;
	stale: boolean;
	risk: string | null;
	needs_attention: boolean;
	updated_at: string | null;
};

export type AmlMatchDecision = 'confirmed' | 'dismissed';

/** One match on a screen, with Graphene's decision on it. */
export type AmlMatch = AmlMatchProfileData & {
	uid: string;
	decision: AmlMatchDecision | null;
	decided_by: string | null;
	decided_at: string | null;
	comment: string | null;
};

/** One person on a client's team at their firm (`crm.client_advisers`). */
export type AmlAdviser = {
	uid: string | null;
	name: string | null;
	email: string | null;
	phone: string | null;
	job_title: string | null;
	fca_reference: string | null;
	/** The firm's headshot, else the sign-in provider's picture. */
	photo_url?: string | null;
	/** The role on this client — one of `CLIENT_TEAM_ROLES` (`@graphene/ui`). At most one is
	 *  `adviser`, and that is the one presented as primary. */
	role: string | null;
};

export type AmlClientFacts = {
	name: string;
	dob: string | null;
	nationality: string | null;
	country: string | null;
	contact_uid: string | null;
	tenant_uid: string;
	tenant_name: string;
	/** The firm's icon, from its platform-portal branding. Null when it has set none. */
	tenant_icon_url?: string | null;
	email?: string | null;
	phone?: string | null;
	/** One display line. */
	address?: string | null;
	gender?: string | null;
	occupation?: string | null;
	/** The adviser the firm assigned, by name. */
	adviser?: string | null;
	/** The client's whole team, adviser first, with how to reach them. */
	advisers?: AmlAdviser[];
	client_since?: string | null;
	/** A person or a business, from the contact, else ComplyCube. */
	type?: AmlClientType | null;
};

export type AmlCheckDetail = {
	check: {
		uid: string;
		kind: AmlKind;
		origin: AmlOrigin;
		status: AmlStatus;
		outcome: string | null;
		attempt: number;
		monitoring: boolean;
		stale_at: string | null;
		decided_by: string | null;
		decided_at: string | null;
		decision_comment: string | null;
		/** Who uploaded this check's photos for the client, by name: an operator, or one of the
		 *  firm's advisers (`uploaded_via`). Null when the client sent them. */
		uploaded_by?: string | null;
		/** Where the upload came from: this console, or the firm's portal. Null when the client sent
		 *  the photos. */
		uploaded_via?: 'operations' | 'firm' | null;
		/** Why they uploaded them: an operator's internal note, or an adviser's note to Graphene.
		 *  Never shown to the client. */
		upload_comment?: string | null;
		provider_check_id: string | null;
		created_at: string;
		updated_at: string;
	};
	client: AmlClientFacts;
	matches: AmlMatch[];
	identity: {
		document_type: string | null;
		breakdown: { key: string; label: string; outcome: string }[];
		/** ComplyCube's reasons, grouped as its own analysis breakdown (`identity_analysis`). */
		analysis?: IdentityAnalysis;
		/** Each photo's part, label and type. There is no link here: the page builds the same-origin
		 *  `/aml/checks/{uid}/images/{part}` relay URL itself from `part` and the check's own uid.
		 *  Null when the page was read without them (`images=false`): the workbench lists them only
		 *  once the profile is on screen, since listing them is recorded as a view. */
		images: IdentityImage[] | null;
		/** Why there are no photos, when there are none. Null while they are not listed. */
		images_missing?: ImagesMissing | null;
	} | null;
	history: { at: string; actor: string | null; action: string; detail: string | null }[];
};

/** An earlier check on the client page: what it was and how it ended. */
export type AmlCheckSummary = {
	uid: string;
	kind: AmlKind;
	origin: AmlOrigin;
	status: AmlStatus;
	attempt: number;
	monitoring: boolean;
	stale: boolean;
	decided: boolean;
	needs_attention: boolean;
	created_at: string;
	updated_at: string;
};

export type AmlRiskFactor = {
	key: 'country' | 'political_exposure' | 'occupation' | 'watchlist';
	label: string;
	risk: string;
	/** The riskiest country's code for `country`; the occupation assessed for `occupation`. */
	detail: string | null;
};

export type AmlRisk = { overall: string; updated_at: string | null; factors: AmlRiskFactor[] };

export type AmlClientPage = {
	/** The client's lasting key: differs from the URL's once a contact key's client is screened. */
	key: string;
	client: AmlClientFacts & { origin: AmlOrigin };
	awaiting_screen: boolean;
	/** Due a screen the worker already has queued: it starts by itself in a few seconds. */
	screen_queued?: boolean;
	stale: boolean;
	needs_attention: boolean;
	/** Null until ComplyCube's profile has been fetched. */
	risk: AmlRisk | null;
	identity: AmlCheckDetail | null;
	aml: AmlCheckDetail | null;
	earlier: AmlCheckSummary[];
	/** Graphene's internal notes on the client, newest first. Never shown to the firm. */
	notes?: AmlClientNote[];
	/** Whether operations can upload new identity photos for the client now. */
	identity_upload?: IdentityUpload;
	/** Why the client needs a person, the oldest wait first: empty when nobody does. */
	reasons?: AmlWorkReason[];
	/** The client's five steps, as the work queue draws them. */
	steps?: AmlWorkStep[];
	/** When the oldest of `reasons` started waiting. */
	waiting_since?: string | null;
	age?: AmlWorkAge | null;
};

/** One internal note an operator wrote on a client. `author` is their name as written. */
export type AmlClientNote = { uid: string; body: string; author: string; created_at: string };

/** One identity file we hold: the ID's front or back, or the selfie. A side of the document may be
 *  a PDF scan rather than a photo. `part` selects the same-origin relay route that streams its
 *  bytes — there is no `url`, since the object store has no public endpoint to presign a link for. */
export type IdentityImage = {
	part: 'front' | 'back' | 'selfie';
	label: string;
	content_type: string;
	/** `graphene` when ComplyCube no longer gives the photo and Graphene's kept copy is shown. */
	source?: 'complycube' | 'graphene';
	/** The photo's bytes already in the page, as an object URL: the workbench read them ahead, so
	 *  the tile shows them instead of asking the relay. Never from the API. */
	src?: string;
};

/** Why a check shows no photos (`decisions.ImagesMissing`). */
export type ImagesMissing = 'none' | 'not_configured' | 'not_held' | 'unavailable';

const IMAGES_MISSING_TEXT: Record<ImagesMissing, string> = {
	none: 'No photos were sent for this check.',
	not_configured: 'ComplyCube is not set up here, and Graphene holds no copy of these photos.',
	not_held:
		'ComplyCube does not hold this check’s photos (it may have run under another key, live or test), and Graphene holds no copy.',
	unavailable: 'ComplyCube did not answer, and Graphene holds no copy. Reload to try again.'
};

/** What the identity card says when a check shows no photos. */
export function imagesMissingText(reason: ImagesMissing | null | undefined): string {
	return (reason && IMAGES_MISSING_TEXT[reason]) || 'No photos can be shown for this check.';
}

/** A PDF opens as a link: the browser cannot show one in an `<img>`. */
export function isPdf(image: Pick<IdentityImage, 'content_type'>): boolean {
	return image.content_type === 'application/pdf';
}

/** The most identity tries a client gets. Ops can ask for new photos until the last one. */

type Named = { label: string; tone: StatusTone };

export const STATUS_LABEL: Record<AmlStatus, Named> = {
	awaiting_screen: { label: 'Awaiting screen', tone: 'info' },
	pending: { label: 'Pending', tone: 'neutral' },
	refer: { label: 'Refer', tone: 'warning' },
	clear: { label: 'Clear', tone: 'success' },
	rejected: { label: 'Rejected', tone: 'danger' },
	failed: { label: 'Failed', tone: 'danger' }
};

export const KIND_LABEL: Record<AmlKind, string> = {
	aml: 'AML screen',
	identity: 'Identity'
};

export const ORIGIN_LABEL: Record<AmlOrigin, string> = {
	portal: 'Portal',
	partner: 'Partner API'
};

export const MATCH_DECISION_LABEL: Record<AmlMatchDecision, Named> = {
	confirmed: { label: 'Confirmed', tone: 'danger' },
	dismissed: { label: 'Rejected', tone: 'success' }
};

/** A match nobody has decided yet. */
export const MATCH_UNDECIDED: Named = { label: 'Undecided', tone: 'warning' };

/** The two decisions on a match: a true match is `confirmed`, a false positive `dismissed` (sent
 *  to ComplyCube as `rejected`). */
export const MATCH_ACTIONS: { value: AmlMatchDecision; label: string }[] = [
	{ value: 'confirmed', label: 'True match' },
	{ value: 'dismissed', label: 'False positive' }
];

export const RISK_LABEL: Record<AmlRiskLevel, Named> = {
	low: { label: 'Low', tone: 'success' },
	medium: { label: 'Medium', tone: 'warning' },
	high: { label: 'High', tone: 'danger' },
	very_high: { label: 'Very high', tone: 'danger' },
	not_set: { label: 'Not set', tone: 'neutral' }
};

export function statusOf(status: string): Named {
	return STATUS_LABEL[status as AmlStatus] ?? { label: humanise(status), tone: 'neutral' };
}

/** A risk level as a pill. No profile yet reads "Not set", as ComplyCube's own `not_set` does. */
export function riskOf(level: string | null): Named {
	if (!level) return RISK_LABEL.not_set;
	return RISK_LABEL[level as AmlRiskLevel] ?? { label: humanise(level), tone: 'neutral' };
}

const NOT_STARTED: Named = { label: 'Not started', tone: 'neutral' };

/** The list's identity pill. A refer an operator asked new photos for waits on the client, not on
 *  Graphene, so it does not read as a bare "Refer". */
export function identityPill(identity: AmlClientRow['identity']): Named {
	if (!identity) return NOT_STARTED;
	if (identity.status === 'refer' && identity.decided)
		return { label: 'New photos requested', tone: 'info' };
	return statusOf(identity.status);
}

/** The list's AML pill. A decided refer is a confirmed match: the client stays referred. */
/** A screen the worker has queued and is about to start. */
export const SCREEN_STARTING: Named = { label: 'Screen starting', tone: 'info' };

/** How often the client page asks again while a queued screen starts: the worker picks it up in
 *  seconds, so the page does not wait the full pending interval. */
export const SCREEN_STARTING_REFRESH_MS = REFRESH_WHILE.screenStarting;

export function amlPill(aml: AmlClientRow['aml'], origin: AmlOrigin = 'portal'): Named {
	if (!aml) return NOT_STARTED;
	if (aml.status === 'awaiting_screen' && aml.queued) return SCREEN_STARTING;
	if (aml.status === 'refer' && aml.decided) return referDecided(origin);
	return statusOf(aml.status);
}

/** A decided AML refer: a portal client's journey was allowed to continue past a confirmed match;
 *  a partner client is referred. */
export function referDecided(origin: AmlOrigin): Named {
	return origin === 'portal'
		? { label: 'Allowed to continue', tone: 'success' }
		: { label: 'Referred', tone: 'danger' };
}

export function shortDate(value: string | null): string {
	if (!value) return '—';
	const parsed = new Date(value);
	return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString('en-GB');
}

export function dateTime(value: string | null): string {
	if (!value) return '—';
	const parsed = new Date(value);
	return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString('en-GB');
}

/** How long ago a check was sent, said plainly: "just now", "4 min ago", "2 h ago", "3 days ago". */
export function sentAgo(value: string, now: number = Date.now()): string {
	const sent = new Date(value).getTime();
	if (Number.isNaN(sent)) return value;
	const minutes = Math.floor(Math.max(0, now - sent) / 60_000);
	if (minutes < 1) return 'just now';
	if (minutes < 60) return `${minutes} min ago`;
	const hours = Math.floor(minutes / 60);
	if (hours < 24) return `${hours} h ago`;
	const days = Math.floor(hours / 24);
	return `${days} ${days === 1 ? 'day' : 'days'} ago`;
}

/** A check has waited longer than ComplyCube usually takes: 30 minutes. */
export const SLOW_WAIT_MS = 30 * 60_000;

export function waitingLong(value: string, now: number = Date.now()): boolean {
	const sent = new Date(value).getTime();
	return !Number.isNaN(sent) && now - sent > SLOW_WAIT_MS;
}

/** The page asks again for the latest result this often while a check waits on ComplyCube. */
export const PENDING_REFRESH_MS = REFRESH_WHILE.pendingCheck;

/** A list row whose check is still with ComplyCube, or whose screen the worker is about to start.
 *  A refer is a person. An awaiting screen with nothing queued is a button, not a wait. */
export function rowAwaitingResult(row: Pick<AmlClientRow, 'identity' | 'aml'>): boolean {
	if (row.identity?.status === 'pending') return true;
	const aml = row.aml;
	if (!aml) return false;
	if (aml.status === 'pending') return true;
	return aml.status === 'awaiting_screen' && aml.queued === true;
}

export function matchStatus(match: Pick<AmlMatch, 'decision'>): Named {
	return match.decision ? MATCH_DECISION_LABEL[match.decision] : MATCH_UNDECIDED;
}

/** Matches still waiting on a decision. */
export function undecidedMatches(detail: AmlCheckDetail): number {
	return detail.matches.filter((m) => m.decision === null).length;
}

/** A match can be decided, and a decided one changed, while its AML check is in refer: once the
 *  check is clear or rejected its matches are settled, and so are a portal screen's once Graphene
 *  has allowed the journey to continue. */
export function canDecideMatches(detail: AmlCheckDetail): boolean {
	return (
		detail.check.kind === 'aml' &&
		detail.check.status === 'refer' &&
		!(detail.check.origin === 'portal' && detail.check.decided_at)
	);
}

/** Every match decided and at least one confirmed. */
function settledOnAConfirmedMatch(detail: AmlCheckDetail): boolean {
	return (
		detail.check.kind === 'aml' &&
		detail.check.status === 'refer' &&
		undecidedMatches(detail) === 0 &&
		detail.matches.some((m) => m.decision === 'confirmed')
	);
}

/** A portal screen held on a confirmed match: the journey waits until Graphene allows it to
 *  continue. The firm sees the screen as in progress. */
export function heldOnConfirmedMatch(detail: AmlCheckDetail): boolean {
	return (
		detail.check.origin === 'portal' && !detail.check.decided_at && settledOnAConfirmedMatch(detail)
	);
}

/** A partner screen settled on a confirmed match: the client is referred. */
export function confirmedReferral(detail: AmlCheckDetail): boolean {
	return detail.check.origin === 'partner' && settledOnAConfirmedMatch(detail);
}

/**
 * The check-level decisions on offer: an identity check only.
 *
 * A refer can be cleared or rejected. A technical failure can only be rejected — there is no result
 * to clear — which tells the firm or client to send new images. An AML check is never decided as a
 * whole: it settles from its matches, clear when every match was dismissed and a refer when any was
 * confirmed.
 */
export function checkDecisions(detail: AmlCheckDetail): ('clear' | 'rejected')[] {
	if (detail.check.kind !== 'identity' || detail.check.decided_at) return [];
	if (detail.check.status === 'refer') return ['rejected', 'clear'];
	if (detail.check.status === 'failed') return ['rejected'];
	return [];
}

/** A failed AML screen that nobody has run again yet. ComplyCube could not run it, so it is not a
 *  result: ops runs it again, for a portal or a partner client alike. */
export function canRerun(detail: AmlCheckDetail): boolean {
	return (
		detail.check.kind === 'aml' && detail.check.status === 'failed' && !detail.check.decided_at
	);
}

/** Ongoing monitoring covers a finished AML screen. A technical failure did not run, so ComplyCube
 *  is not re-screening it, even when the screen was started with monitoring asked for. */
export function monitoringCovers(check: {
	kind: string;
	status: string;
	monitoring: boolean;
}): boolean {
	return check.kind === 'aml' && check.monitoring && check.status !== 'failed';
}

// ── Where things live ────────────────────────────────────────────────────────────────────────

/** The client page, optionally opened on one of its earlier checks. */
export function clientHref(key: string, check?: string | null): string {
	const base = `/aml/clients/${encodeURIComponent(key)}`;
	return check ? `${base}?check=${encodeURIComponent(check)}` : base;
}

/** The client report, as a PDF from this app's same-origin relay. */
export function clientReportHref(key: string): string {
	return `/aml/clients/${encodeURIComponent(key)}/report`;
}

/** One check's report (identity or AML), as a PDF from this app's same-origin relay. */
export function checkReportHref(uid: string): string {
	return `/aml/checks/${encodeURIComponent(uid)}/report`;
}

/** Where the identity card's ‹ › go: the older and newer try (null at either end), and which
 *  try of how many is shown, oldest 1. */
export type IdentitySteps = {
	older: string | null;
	newer: string | null;
	position: number;
	count: number;
};

/**
 * The identity card's ‹ › through the client's identity tries: the current check, then the earlier
 * ones, newest first. The current check opens with no `?check=`. Null with only one try.
 */
export function identitySteps(
	key: string,
	current: string,
	earlier: Pick<AmlCheckSummary, 'uid' | 'kind' | 'created_at'>[],
	shown: string
): IdentitySteps | null {
	const older = earlier
		.filter((check) => check.kind === 'identity' && check.uid !== current)
		.sort((a, b) => b.created_at.localeCompare(a.created_at))
		.map((check) => check.uid);
	const tries = [current, ...older];
	if (tries.length < 2) return null;
	const at = Math.max(0, tries.indexOf(shown));
	const href = (i: number) => (i === 0 ? clientHref(key) : clientHref(key, tries[i]));
	return {
		older: at + 1 < tries.length ? href(at + 1) : null,
		newer: at > 0 ? href(at - 1) : null,
		position: tries.length - at,
		count: tries.length
	};
}

/** Whether the client has been checked: the page offers the client report only then. */
export function hasChecks(page: Pick<AmlClientPage, 'identity' | 'aml' | 'earlier'>): boolean {
	return !!page.identity || !!page.aml || page.earlier.length > 0;
}

/** Everything that narrows or orders the client list. It all lives in the URL: "the clients at
 *  Ashworth" has to survive a reload and be a link somebody can paste into a ticket. */
export type AmlListQuery = {
	tenant: string;
	q: string;
	/** Validated by the API against its own closed column map; anything else orders by default. */
	sort: string;
	dir: 'asc' | 'desc';
	page: number;
};

export function parseListQuery(params: URLSearchParams): AmlListQuery {
	return {
		tenant: params.get('tenant')?.trim() ?? '',
		q: params.get('q')?.trim() ?? '',
		sort: params.get('sort')?.trim() ?? '',
		dir: params.get('dir') === 'desc' ? 'desc' : 'asc',
		page: Math.max(1, Math.floor(Number(params.get('page') ?? 1)) || 1)
	};
}

/** The list's URL for a query. Empty values and page 1 are left out, so the unfiltered list stays
 *  at `/aml/clients`, and `dir` only travels with a `sort` — on its own it names a direction for
 *  nothing. */
export function listHref(query: AmlListQuery): string {
	const params = new URLSearchParams();
	if (query.tenant) params.set('tenant', query.tenant);
	if (query.q) params.set('q', query.q);
	if (query.sort) {
		params.set('sort', query.sort);
		params.set('dir', query.dir);
	}
	if (query.page > 1) params.set('page', String(query.page));
	return `/aml/clients${params.size ? `?${params}` : ''}`;
}

/** The client list's own narrowings. A link carrying any of them is a client-list link from when
 *  the list lived at `/aml`, where the workbench is now. */
export function isClientListQuery(params: URLSearchParams): boolean {
	return ['q', 'sort', 'dir', 'page'].some((name) => params.has(name));
}

// ── Ask for new photos ───────────────────────────────────────────────────────────────────────

/** Which of a client's identity photos a retake is asked for. */
export type PhotoChoice = 'document' | 'selfie';

/** A client-safe reason code for asking for new photos. */
export type PhotoRequestReason = 'blurry' | 'cropped' | 'glare' | 'face' | 'expired' | 'none';

/** The generic line the client reads when ops gives no specific reason. */
export const GENERIC_PHOTO_REQUEST_TEXT = 'We could not verify your identity. Please try again.';

/**
 * The client-safe reasons for asking for new photos, and what the client reads for each — a
 * fraud signal or a face mismatch is never one of them. Mirrors the API's list word for word
 * (`apps/api/app/complycube/identity.py::PHOTO_REQUEST_REASONS`); keep the two in step.
 */
export const PHOTO_REQUEST_REASONS: {
	value: PhotoRequestReason;
	/** What the operator picks from — for every code but `none`, exactly what the client reads. */
	label: string;
	/** Which chosen photo this reason fits, or `'always'` when it fits either. */
	photos: PhotoChoice | 'always';
}[] = [
	{
		value: 'blurry',
		label: 'The photo was not clear enough. Please retake it in good light.',
		photos: 'always'
	},
	{
		value: 'cropped',
		label: 'Part of your document was cut off. Make sure all four corners are in the photo.',
		photos: 'document'
	},
	{
		value: 'glare',
		label: 'There was glare on your document. Please avoid reflections.',
		photos: 'document'
	},
	{
		value: 'face',
		label: 'Your face was not clearly visible in the selfie. Please retake it in good light.',
		photos: 'selfie'
	},
	{
		value: 'expired',
		label: 'Your document has expired. Please use an in-date document.',
		photos: 'document'
	},
	{
		value: 'none',
		label: `No specific reason — “${GENERIC_PHOTO_REQUEST_TEXT}”`,
		photos: 'always'
	}
];

/** What the client actually reads for a reason code — unlike `label`, true to the word for `none`
 *  too. */
export function clientPhotoText(reason: PhotoRequestReason): string {
	return reason === 'none'
		? GENERIC_PHOTO_REQUEST_TEXT
		: (PHOTO_REQUEST_REASONS.find((r) => r.value === reason)?.label ?? '');
}

/** The reasons that fit the chosen photos: `document`-only and `selfie`-only reasons drop out
 *  until their photo is actually asked for. No photo chosen offers only the reasons that always
 *  fit. */
export function photoReasonsFor(photos: PhotoChoice[]): typeof PHOTO_REQUEST_REASONS {
	return PHOTO_REQUEST_REASONS.filter((r) => r.photos === 'always' || photos.includes(r.photos));
}

/** Ops can ask this check's client for new photos: an undecided portal identity refer, on any try
 *  (there is no limit, since every new try is ops' choice). A partner check has no portal client to
 *  ask. */
export function canRequestPhotos(detail: AmlCheckDetail): boolean {
	return (
		detail.check.kind === 'identity' &&
		detail.check.origin === 'portal' &&
		detail.check.status === 'refer' &&
		!detail.check.decided_at
	);
}

/** Who uploaded a check's photos, as the identity card says it: an adviser is named as the
 *  firm's. Null when the client sent them. */
export function uploadedByLine(
	check: Pick<AmlCheckDetail['check'], 'uploaded_by' | 'uploaded_via'>
): string | null {
	if (!check.uploaded_by) return null;
	const firm = check.uploaded_via === 'firm' ? ", the firm's adviser" : '';
	return `Photos uploaded by ${check.uploaded_by}${firm}`;
}

/** An identity check ops asked new photos for: still referred, but decided — so its decision box
 *  reads "New photos requested" rather than the bare "Refer" badge. */
export function photosRequested(detail: AmlCheckDetail): boolean {
	return (
		detail.check.kind === 'identity' &&
		detail.check.status === 'refer' &&
		detail.check.decided_at !== null
	);
}

// ── The work queue: every client that needs a person ─────────────────────────────────────────

export type AmlClientType = 'individual' | 'organisation';

export const CLIENT_TYPE_LABEL: Record<AmlClientType, string> = {
	individual: 'Individual',
	organisation: 'Organisation'
};

/** A client's five steps, in the order the queue draws them. */
export type AmlStepKey = 'identity' | 'screened' | 'monitoring' | 'hits' | 'risk';
export type AmlStepState = 'done' | 'open' | 'waiting' | 'advisory' | 'none';

/** One step of a client's AML work. A client is in the queue exactly when one of its steps is
 *  `open`. */
export type AmlWorkStep = {
	key: AmlStepKey;
	label: string;
	state: AmlStepState;
	count: number | null;
	hint: string | null;
};

/** One reason a client needs a person, and the step it opens. */
export type AmlWorkReason = {
	code: string;
	label: string;
	step: AmlStepKey;
	count: number;
	/** When the oldest of its kind started waiting. */
	since: string | null;
	/** The check that has waited longest. Null for a screen due, which has no check yet. */
	check_uid: string | null;
};

export type AmlAgeBand = 'fresh' | 'ageing' | 'overdue';

/** How long a client has waited, in Monday–Friday days on London dates. */
export type AmlWorkAge = { working_days: number; band: AmlAgeBand };

export const AGE_TONE: Record<AmlAgeBand, StatusTone> = {
	fresh: 'neutral',
	ageing: 'warning',
	overdue: 'danger'
};

/** A wait as the queue says it: "Today", "1 working day", "3 working days". */
export function ageText(age: Pick<AmlWorkAge, 'working_days'>): string {
	if (age.working_days < 1) return 'Today';
	return `${age.working_days} working ${age.working_days === 1 ? 'day' : 'days'}`;
}

/** One client in the queue (`GET /aml/work`). */
export type AmlWorkItem = {
	key: string;
	name: string;
	type: AmlClientType | null;
	firm: { uid: string; name: string; icon_url: string | null };
	origin: AmlOrigin;
	waiting_since: string;
	age: AmlWorkAge | null;
	reasons: AmlWorkReason[];
	steps: AmlWorkStep[];
};

/** The queue's reason filters (`attention.WORK_FILTERS`). */
export type AmlWorkFilter = 'hits' | 'identity' | 'screens' | 'monitoring' | 'held';

/** The queue's counts (`GET /aml/work/summary`): every client that needs a person, by reason, and
 *  how many are overdue. */
export type AmlWorkSummary = {
	total: number;
	by_reason: Record<AmlWorkFilter, number>;
	overdue: number;
};

/** What the workbench's "Show" offers: every profile that needs a person, or one reason. */
export type AmlShow = 'all' | AmlWorkFilter;

/** Each "Show" choice and the reason it asks the queue for. */
export const SHOW_OPTIONS: Record<AmlShow, { label: string; reason: AmlWorkFilter | null }> = {
	all: { label: 'All profiles', reason: null },
	hits: { label: 'Hits to review', reason: 'hits' },
	identity: { label: 'Identity to decide', reason: 'identity' },
	screens: { label: 'Screens to run', reason: 'screens' },
	monitoring: { label: 'Monitoring alerts', reason: 'monitoring' },
	held: { label: 'Held on a confirmed match', reason: 'held' }
};

/** How many clients a "Show" choice holds. */
export function showCount(show: AmlShow, summary: AmlWorkSummary): number {
	const reason = SHOW_OPTIONS[show].reason;
	return reason ? summary.by_reason[reason] : summary.total;
}

/** The workbench's split of its work: how many profiles wait on an identity decision and how many
 *  on AML hits, each with the "Show" choice that lists them. */
export function workBreakdown(
	summary: Pick<AmlWorkSummary, 'by_reason'>
): { show: AmlWorkFilter; count: number; label: string }[] {
	const identity = summary.by_reason.identity;
	const hits = summary.by_reason.hits;
	return [
		{
			show: 'identity',
			count: identity,
			label: `${identity} identity ${identity === 1 ? 'check' : 'checks'}`
		},
		{ show: 'hits', count: hits, label: `${hits} with AML hits` }
	];
}

/** The step each reason filter is about. A held screen waits on its hits. */
export const WORK_FILTER_STEP: Record<AmlWorkFilter, AmlStepKey> = {
	hits: 'hits',
	identity: 'identity',
	screens: 'screened',
	monitoring: 'monitoring',
	held: 'hits'
};

/** The open step a profile opens on: the one the "Show" choice is about, else the first open
 *  one. Null when no step is open. */
export function stepToShow(
	steps: Pick<AmlWorkStep, 'key' | 'state'>[],
	show: AmlShow
): AmlStepKey | null {
	const reason = SHOW_OPTIONS[show].reason;
	const open = steps.filter((step) => step.state === 'open');
	return (
		(open.find((step) => reason && step.key === WORK_FILTER_STEP[reason]) ?? open[0])?.key ?? null
	);
}

/** The workbench's place in the URL: what it shows, for which firm, and the profile on screen. */
export type AmlWorkQuery = { show: AmlShow; tenant: string; at: string };

export function parseWorkQuery(params: URLSearchParams): AmlWorkQuery {
	return { ...parseWorkFilter(params), at: params.get('at')?.trim() ?? '' };
}

/** What the workbench shows and for which firm: the part of its place that picks the queue. */
export function parseWorkFilter(params: URLSearchParams): Omit<AmlWorkQuery, 'at'> {
	const show = params.get('show')?.trim() ?? '';
	return {
		show: (Object.keys(SHOW_OPTIONS) as string[]).includes(show) ? (show as AmlShow) : 'all',
		tenant: params.get('tenant')?.trim() ?? ''
	};
}

/** The workbench's URL. The default "Show" and empty values are left out, so the plain workbench
 *  stays at `/aml`. */
export function workHref(query: Partial<AmlWorkQuery> = {}): string {
	const params = new URLSearchParams();
	if (query.show && query.show !== 'all') params.set('show', query.show);
	if (query.tenant) params.set('tenant', query.tenant);
	if (query.at) params.set('at', query.at);
	return `/aml${params.size ? `?${params}` : ''}`;
}

/**
 * The next profile (`step` 1) or the previous one (-1) from `current`, passing over the keys
 * skipped this session. The queue loops: forward from the last is the first, back from the first
 * the last. Never `current` itself: null when no other profile is left to move to. From a key the
 * queue no longer holds, forward is the first profile and back the last.
 */
export function neighbour(
	keys: readonly string[],
	current: string | null,
	step: 1 | -1,
	skipped: Iterable<string> = []
): string | null {
	const pass = new Set(skipped);
	const n = keys.length;
	const at = current === null ? -1 : keys.indexOf(current);
	// From outside the queue, start just beyond the end it enters from, and look at every key.
	const from = at === -1 ? (step === 1 ? -1 : n) : at;
	const steps = at === -1 ? n : n - 1;
	for (let k = 1; k <= steps; k++) {
		const key = keys[(((from + step * k) % n) + n) % n];
		if (!pass.has(key)) return key;
	}
	return null;
}

/** The profile that took a place in the queue once the one there left it: the one now at that
 *  place, else the next after it, passing over skipped keys. The queue loops, so a place past
 *  the end goes to the first. Null when none is left. */
export function atPlace(
	keys: readonly string[],
	place: number,
	skipped: Iterable<string> = []
): string | null {
	const pass = new Set(skipped);
	const n = keys.length;
	const start = place >= n ? 0 : Math.max(0, place);
	for (let k = 0; k < n; k++) {
		const key = keys[(start + k) % n];
		if (!pass.has(key)) return key;
	}
	return null;
}

/** The profile after `current`: the next in the queue, which loops round to the first. For a
 *  profile that left the queue, the one that took its `place`. */
export function nextProfile(
	keys: readonly string[],
	current: string | null,
	place: number,
	skipped: Iterable<string> = []
): string | null {
	return current && keys.includes(current)
		? neighbour(keys, current, 1, skipped)
		: atPlace(keys, place, skipped);
}

// ── The workbench's profiles, read in the page ───────────────────────────────────────────────

/** One read through this app's own relays: the body, or the refusal in the API's words. */
export type Read<T> = { ok: true; body: T } | { ok: false; status: number; error: string };

/** A same-origin JSON read from the page. A refusal keeps the API's reason (`detail`), or this
 *  app's own (`message`). */
export async function readJson<T>(fetch: typeof globalThis.fetch, href: string): Promise<Read<T>> {
	try {
		const res = await fetch(href, { headers: { accept: 'application/json' } });
		if (res.ok) return { ok: true, body: (await res.json()) as T };
		const body = (await res.json().catch(() => null)) as {
			detail?: unknown;
			message?: unknown;
		} | null;
		const reason = [body?.detail, body?.message].find((said) => typeof said === 'string');
		return { ok: false, status: res.status, error: (reason as string) ?? 'Something went wrong.' };
	} catch {
		return { ok: false, status: 503, error: 'The service is unavailable.' };
	}
}

/** A client's page body from this app's relay. Without `photos` the identity photos are not
 *  listed (`images` is null) and nothing is recorded: the read for a profile not yet on screen. */
export function profileDataHref(key: string, photos: boolean): string {
	return `/aml/clients/${encodeURIComponent(key)}/data${photos ? '' : '?images=false'}`;
}

/** One identity check's photo listing from this app's relay. A listing is recorded as a view;
 *  without `record` it is not, for photos read ahead of a profile not yet on screen. */
export function photoListHref(uid: string, record = true): string {
	return `/aml/checks/${encodeURIComponent(uid)}/images${record ? '' : '?record=false'}`;
}

/** One photo's bytes from this app's relay. Reading them records nothing. */
export function photoHref(uid: string, part: IdentityImage['part']): string {
	return `/aml/checks/${encodeURIComponent(uid)}/images/${part}`;
}

/** Where the workbench records that a check's photos read ahead were shown. */
export function photosViewedHref(uid: string): string {
	return `/aml/checks/${encodeURIComponent(uid)}/images/viewed`;
}

/** What `GET /aml/checks/{uid}/images` answers. */
export type PhotoListing = { images: IdentityImage[]; images_missing: ImagesMissing | null };

/** The listing that stands in when the photos could not be listed. */
export const PHOTOS_UNAVAILABLE: PhotoListing = { images: [], images_missing: 'unavailable' };

/** The identity checks on a profile whose photos are not listed yet, by uid. */
export function unlistedPhotos(page: AmlClientPage): string[] {
	return [page.identity, page.aml]
		.filter((detail) => detail?.identity && detail.identity.images === null)
		.map((detail) => (detail as AmlCheckDetail).check.uid);
}

/** The profile with one identity check's photos listed. */
export function withPhotos(page: AmlClientPage, uid: string, listing: PhotoListing): AmlClientPage {
	const fill = (detail: AmlCheckDetail | null): AmlCheckDetail | null =>
		detail?.identity && detail.check.uid === uid
			? {
					...detail,
					identity: {
						...detail.identity,
						images: listing.images,
						images_missing: listing.images_missing
					}
				}
			: detail;
	return { ...page, identity: fill(page.identity), aml: fill(page.aml) };
}

/** One identity check's photos read ahead: the listing, and each photo's bytes in its order (null
 *  where they could not be read: that tile asks the relay instead). */
export type PhotoSet = { listing: PhotoListing; blobs: (Blob | null)[] };

/** Read one check's photos ahead, recording nothing: the listing without a view, then each
 *  photo's bytes. Null when the listing fails. */
export async function readPhotoSet(
	fetch: typeof globalThis.fetch,
	uid: string
): Promise<PhotoSet | null> {
	const listed = await readJson<PhotoListing>(fetch, photoListHref(uid, false));
	if (!listed.ok) return null;
	const blobs = await Promise.all(
		listed.body.images.map(async (image) => {
			try {
				const res = await fetch(photoHref(uid, image.part));
				return res.ok ? await res.blob() : null;
			} catch {
				return null;
			}
		})
	);
	return { listing: listed.body, blobs };
}

/** The listing as the screen shows it: each photo read ahead from its own object URL. */
export function shownListing(set: PhotoSet, url: (blob: Blob) => string): PhotoListing {
	return {
		...set.listing,
		images: set.listing.images.map((image, i) => {
			const blob = set.blobs[i];
			return blob ? { ...image, src: url(blob) } : image;
		})
	};
}

/** Record that a check's photos read ahead were shown: the view a listing records otherwise.
 *  A check with no photos has nothing to record. */
export async function recordPhotosViewed(
	fetch: typeof globalThis.fetch,
	uid: string,
	count: number
): Promise<void> {
	if (count < 1) return;
	try {
		await fetch(photosViewedHref(uid), {
			method: 'POST',
			headers: { 'content-type': 'application/json' },
			body: JSON.stringify({ image_count: Math.min(count, 3) })
		});
	} catch {
		/* the photos still show; the view goes unrecorded only when nothing answers */
	}
}

/** A profile read ahead: its page body without photos, and each identity check's photos. */
export type CachedProfile = {
	/** The key it was read by: a contact key's client can answer under its lasting key. */
	key: string;
	body: AmlClientPage;
	/** Each check's photos being read ahead, by uid: the listing, each photo whose bytes arrived
	 *  showing from an object URL. Null when they could not be read. */
	photos: Map<string, Promise<PhotoListing | null>>;
	/** The same, for the checks whose photos have arrived. */
	ready: Map<string, PhotoListing>;
};

/** The profiles the workbench has read ahead, by key. */
export type ProfileCache = {
	/** The profile held for `key`, when its read has arrived. */
	peek(key: string): CachedProfile | undefined;
	/** The profile for `key`: the one held, else the read already under way, else a new read. */
	load(key: string): Promise<Read<CachedProfile>>;
	/** Read the keys ahead that are neither held nor being read. */
	preload(keys: Iterable<string | null>): void;
	/** Keep only these profiles (the one on screen and its neighbours) and the reads for them.
	 *  Every other profile goes, its object URLs revoked. */
	retain(keys: Iterable<string | null>): void;
	/** Stop serving every profile held, and any read under way: the queue was read again. Their
	 *  object URLs last until the next `retain`, since the screen may still show them. */
	clear(): void;
	/** Forget everything and revoke every object URL: the page is leaving. */
	dispose(): void;
};

type Held = CachedProfile & { urls: string[]; alive: boolean };

/**
 * The workbench's read-ahead. `read` gives one profile without its photos (it never throws), and
 * `readPhotos` one check's photos without recording a view. A profile's photos are read as soon as
 * its body arrives, each photo's bytes kept as an object URL with the profile. A failed read is
 * not held, so the next move to it reads again; a read dropped by `retain` or `clear` is not held
 * and reads no photos.
 */
export function profileCache(
	read: (key: string) => Promise<Read<AmlClientPage>>,
	readPhotos: (uid: string) => Promise<PhotoSet | null>,
	urls: Pick<typeof URL, 'createObjectURL' | 'revokeObjectURL'> = URL
): ProfileCache {
	const held = new Map<string, Held>();
	const reading = new Map<string, Promise<Read<CachedProfile>>>();
	/** Profiles no longer served whose object URLs the screen may still show. */
	let stale: Held[] = [];

	function revoke(entry: Held) {
		entry.alive = false;
		for (const url of entry.urls.splice(0)) urls.revokeObjectURL(url);
	}

	function cached(key: string, body: AmlClientPage, ahead: boolean): Held {
		const entry: Held = { key, body, photos: new Map(), ready: new Map(), urls: [], alive: true };
		if (!ahead) return entry;
		for (const uid of unlistedPhotos(body)) {
			const photos = readPhotos(uid).then((set) => {
				// Evicted while its photos were on their way: nothing to show them, nothing to make.
				if (!set || !entry.alive) return null;
				const listing = shownListing(set, (blob) => {
					const url = urls.createObjectURL(blob);
					entry.urls.push(url);
					return url;
				});
				entry.ready.set(uid, listing);
				return listing;
			});
			entry.photos.set(uid, photos);
		}
		return entry;
	}

	function load(key: string): Promise<Read<CachedProfile>> {
		const entry = held.get(key);
		if (entry) return Promise.resolve({ ok: true, body: entry });
		const under = reading.get(key);
		if (under) return under;
		const answer: Promise<Read<CachedProfile>> = read(key).then((result) => {
			const wanted = reading.get(key) === answer;
			if (wanted) reading.delete(key);
			if (!result.ok) return result;
			const entry = cached(key, result.body, wanted);
			if (wanted) held.set(key, entry);
			return { ok: true, body: entry };
		});
		reading.set(key, answer);
		return answer;
	}

	return {
		peek: (key) => held.get(key),
		load,
		preload(keys) {
			for (const key of keys) if (key && !held.has(key) && !reading.has(key)) void load(key);
		},
		retain(keys) {
			const keep = new Set(keys);
			for (const [key, entry] of held)
				if (!keep.has(key)) {
					held.delete(key);
					revoke(entry);
				}
			for (const key of [...reading.keys()]) if (!keep.has(key)) reading.delete(key);
			for (const entry of stale.splice(0)) revoke(entry);
		},
		clear() {
			stale.push(...held.values());
			held.clear();
			reading.clear();
		},
		dispose() {
			for (const entry of [...held.values(), ...stale]) revoke(entry);
			stale = [];
			held.clear();
			reading.clear();
		}
	};
}
