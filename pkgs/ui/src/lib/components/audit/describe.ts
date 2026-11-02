/**
 * Humanising the action-event stream (`audit.actions`, api 0041).
 *
 * An event's `event_type` is `action.` + the route's OpenAPI operation id (`{tag}-{route-name}`,
 * e.g. `client.created`) — a closed machine vocabulary, not prose. These helpers turn
 * it into the sentence an operator scans ("Created a client") while the raw type stays on screen
 * as a quiet mono chip for precision. Pure functions, table-tested in `describe.test.ts`; the
 * ActionLog component defers to them so the platform portal can reuse the same reading later.
 */

/**
 * One entity an event touched, resolved at READ time by the API: the raw id (a path param or a
 * captured subject field) plus its CURRENT display label — the client's name, the login's email.
 * `label` is null when the entity is deleted/unknown; the UI shows the bare id, which is honest.
 */
export interface ActionEntity {
	/** `contact` | `account` | `user` | `tenant` | `template` | `document` | `task` | `flow` |
	 *  `form` | `model` | `instruction` | `transaction` | `conversation` — open vocabulary; render
	 *  unknowns via entityKindLabel. */
	kind: string;
	/** What the stream RECORDED — the path param the route actually received. For a contact or an
	 *  account that is the serial id, because those routes are serial-typed; it is history, and it
	 *  is not an address. */
	id: string;
	/** How a portal ADDRESSES the record today. Equal to `id` for the kinds whose routes were
	 *  already uid-typed, and null when the entity could not be resolved (deleted, or an older API
	 *  that did not send the field) — in which case there is nothing to link to. */
	uid?: string | null;
	label: string | null;
}

/** One row of `GET /admin/audit/actions` — verbatim stream columns plus the joined display names. */
export interface ActionEvent {
	id: number;
	event_type: string;
	created_at: string;
	method: string;
	/** The route TEMPLATE (`/firm/contacts/{contact_id}`), never a raw path. */
	route: string;
	status: number;
	path_params: Record<string, unknown>;
	metadata: {
		correlation_id?: string | null;
		query?: Record<string, unknown>;
		break_glass?: boolean;
		/** The write-time subject allowlist's capture (app/audit_actions.py::SUBJECT_FIELDS). */
		subject?: Record<string, unknown>;
		/** The write-time before-values pre-read (app/audit_actions.py::PREVIOUS_FIELDS) — what the
		 *  changed row held BEFORE the action, so the trail can say "Adviser → Compliance". */
		previous?: Record<string, unknown>;
		/** The NAMES of the top-level body keys an update carried, and never their values
		 *  (app/audit_actions.py::extract_changed_keys). This is what makes an ordinary "Updated a client"
		 *  row say something: the same sentence used to appear whether an email or a date of birth
		 *  moved. Names only, by design — the value is the client's data, and an audit row is read by
		 *  more people than the record is. */
		changed?: string[];
	} & Record<string, unknown>;
	/** Read-time id → label resolution (may be absent from older API responses). */
	entities?: ActionEntity[] | null;
	actor_id: string | null;
	actor_sub: string;
	actor_email: string | null;
	actor_name: string | null;
	tenant_id: number | null;
	tenant_uid: string | null;
	tenant_name: string | null;
	/** The acting firm's branding icon (theme `client.icon_url`), joined per row so the firm chip
	 *  can wear it. Null/absent (older API responses) ⇒ the name-only chip. */
	tenant_icon_url?: string | null;
	/** Present ⇒ a PROXIED action: ops acting on this firm via `?tenant=<uid>`. */
	target_tenant_id: number | null;
	target_tenant_uid: string | null;
	target_tenant_name: string | null;
	/** The proxied TARGET firm's branding icon, for the "Acting on {firm}" badge. */
	target_tenant_icon_url?: string | null;
}

/* The curated verb map: operation-id leading verb → past tense. Only common, unambiguous verbs —
   anything else falls back to the de-slugged form rather than guessing tense wrongly. */
const VERBS: Record<string, string> = {
	create: 'Created',
	update: 'Updated',
	set: 'Set',
	delete: 'Deleted',
	remove: 'Removed',
	add: 'Added',
	cancel: 'Cancelled',
	send: 'Sent',
	share: 'Shared',
	// Telling a client about a document they can already open (POST /documents/{uid}/notify) — a
	// separate act from sending, now that filing a document is what grants access. Without this the
	// de-slug reads "Notify document": an imperative, where every other row is a past-tense record.
	notify: 'Notified',
	resend: 'Resent',
	upload: 'Uploaded',
	generate: 'Generated',
	// Document activity folded in from documents.audit_log (see app/audit_actions.py::record_document_action):
	// a view (GET) and the worker render completing. `access` is the RETIRED share-links feature's
	// anonymous public-share hit — it writes nothing now, but its rows are in audit.actions for good
	// and must keep humanising ("Accessed a document share"), so the verb stays. Same for the
	// `shares` tag in `categoryFor` and the `action.shares-*` entry below.
	render: 'Rendered',
	view: 'Viewed',
	access: 'Accessed',
	archive: 'Archived',
	unarchive: 'Unarchived',
	rename: 'Renamed',
	rotate: 'Rotated',
	revoke: 'Revoked',
	grant: 'Granted',
	provision: 'Provisioned',
	sync: 'Synced',
	run: 'Ran',
	enable: 'Enabled',
	disable: 'Disabled',
	link: 'Linked',
	unlink: 'Unlinked',
	publish: 'Published',
	start: 'Started',
	complete: 'Completed',
	check: 'Checked',
	submit: 'Submitted',
	fulfil: 'Fulfilled',
	assign: 'Assigned',
	invite: 'Invited',
	reset: 'Reset',
	reveal: 'Revealed',
	screen: 'Screened',
	adjudicate: 'Adjudicated',
	deactivate: 'Deactivated',
	reactivate: 'Reactivated',
	activate: 'Activated',
	duplicate: 'Duplicated',
	export: 'Exported',
	import: 'Imported',
	retry: 'Retried',
	anonymise: 'Anonymised',
	connect: 'Connected',
	disconnect: 'Disconnected',
	save: 'Saved',
	mark: 'Marked',
	move: 'Moved',
	release: 'Released',
	replay: 'Replayed',
	request: 'Requested',
	approve: 'Approved',
	reject: 'Rejected',
	conflicted: 'Conflicted',
	// Contribution / transfer verbs — without these they de-slug to bare imperatives
	// ("Categorise contribution" rather than "Categorised a contribution").
	classify: 'Classified',
	categorise: 'Categorised',
	attach: 'Attached',
	detach: 'Detached',
	lodge: 'Lodged',
	record: 'Recorded',
	stop: 'Stopped',
	refresh: 'Refreshed',
	// Verbs the estate uses that were missing, so their operations de-slugged to bare imperatives
	// ("Restore client" beside "Archived a client" — the same list, two registers).
	restore: 'Restored',
	reassign: 'Reassigned',
	unassign: 'Unassigned',
	dismiss: 'Dismissed',
	issue: 'Issued',
	simulate: 'Simulated',
	validate: 'Validated',
	reorder: 'Reordered',
	acknowledge: 'Accepted',
	decide: 'Decided',
	push: 'Pushed',
	load: 'Loaded',
	register: 'Registered',
	verify: 'Verified',
	rebuild: 'Rebuilt',
	query: 'Queried',
	open: 'Opened',
	read: 'Read',
	test: 'Tested',
	preview: 'Previewed',
	// The route is spelt the American way (`/fulfill`); the label stays British, like the rest.
	fulfill: 'Fulfilled'
};

/* Acronyms and proper nouns as a READER writes them, applied per word after NOUNS. An operation id
   is lower-case-hyphenated, so de-slugging alone yields "Ran an aml screen" and "Recorded a
   contribution to morgan lloyd" — machine text in a list that otherwise reads as English, which is
   what makes the event-type picker feel generated rather than written. */
const WORD_CASING: Record<string, string> = {
	aml: 'AML',
	kyc: 'KYC',
	mfa: 'MFA',
	sso: 'SSO',
	ai: 'AI',
	api: 'API',
	pdf: 'PDF',
	url: 'URL',
	id: 'ID',
	sql: 'SQL',
	sipp: 'SIPP',
	isa: 'ISA',
	gia: 'GIA',
	jisa: 'JISA',
	ras: 'RAS',
	sei: 'SEI',
	ctc: 'CTC',
	seccl: 'Seccl',
	morgan: 'Morgan',
	lloyd: 'Lloyd',
	dcube: 'DCube',
	workos: 'WorkOS'
};

/* Product vocabulary over schema vocabulary: the stream says `contact`/`tenant`, the humans in
   the portals say client/firm. Applied per word so plurals map too. */
const NOUNS: Record<string, string> = {
	contact: 'client',
	contacts: 'clients',
	tenant: 'firm',
	tenants: 'firms'
};

/** The closed auth-event vocabulary (app/audit_actions.py::record_auth_action) — synthetic events with no
 *  entities to enrich, so each gets one fixed, readable label. */
const AUTH_LABELS: Record<string, string> = {
	'authentication.account_activated': 'Activated their account',
	'authentication.signed_out': 'Signed out',
	'authentication.mfa_enrolled': 'Enrolled multi-factor authentication',
	'authentication.mfa_removed': 'Removed multi-factor authentication',
	// …and the pre-rename spellings, because `audit.actions` is append-only: a firm's trail is mostly
	// HISTORY, and an un-humanised row renders as its raw id beside sentences.
	'auth-activated': 'Activated their account',
	'auth-logout': 'Signed out',
	'auth-mfa-enrolled': 'Enrolled multi-factor authentication',
	'auth-mfa-removed': 'Removed multi-factor authentication'
};

/** WorkOS-aligned auth method → the phrase woven into the sign-in sentence ("Signed in with a
 *  password"). `auth-login` / `auth-login-failed` are ENRICHED builders (below) that read the
 *  captured `method`; the rest of the auth family are fixed AUTH_LABELS above. */
const AUTH_METHOD_WORDS: Record<string, string> = {
	password: 'a password',
	sso: 'SSO',
	oauth: 'OAuth',
	mfa: 'MFA',
	magic: 'a magic link'
};

/** An export's captured `listing` and `format` → the words of "Exported the clients list as PDF".
 *  An unknown code falls back to "a listing" / its own spelling rather than vanishing. */
const EXPORT_LISTING_WORDS: Record<string, string> = {
	clients: 'the clients list',
	accounts: 'the accounts list',
	transactions: 'the transactions list',
	'client-transactions': 'a client’s transactions',
	'account-transactions': 'an account’s transactions',
	holdings: 'an account’s holdings'
};
const EXPORT_FORMAT_WORDS: Record<string, string> = { csv: 'CSV', xlsx: 'Excel', pdf: 'PDF' };

/** Words that are themselves the determiner, so no article is added in front of them. */
const DETERMINERS: ReadonlySet<string> = new Set(['all', 'every', 'own', 'their', 'the']);

/** "a client" / "an illustration" / "form fields" (no article on plurals/mass nouns). */
function withArticle(phrase: string): string {
	const words = phrase.split(' ');
	// A determiner already fills the article's slot: "Provisioned an all firm storage" and "Marked
	// an all notifications read" are what the plural test alone produced.
	if (DETERMINERS.has((words[0] ?? '').toLowerCase())) return phrase;
	// Crude plural test on the HEAD noun (the last word), deliberately: `form fields`, `roles`,
	// `members` skip the article, and the false positives it admits (`access`, `status`) read
	// correctly without one too.
	if ((words[words.length - 1] ?? '').endsWith('s')) return phrase;
	// Case-INSENSITIVE: `WORD_CASING` may have upper-cased the head word, and "a AML hit" is what a
	// lower-case-only test produces.
	return `${/^[aeiou]/i.test(words[0] ?? '') ? 'an' : 'a'} ${phrase}`;
}

/** The entity kinds as the product speaks them (`contact` → Client, `tenant` → Firm). Unknown
 *  kinds degrade to a capitalised form — the vocabulary can grow server-side first. */
const ENTITY_KIND_LABELS: Record<string, string> = {
	contact: 'Client',
	user: 'User',
	account: 'Account',
	tenant: 'Firm',
	template: 'Template',
	document: 'Document',
	task: 'Task',
	flow: 'Flow',
	form: 'Form',
	model: 'Model',
	instruction: 'Instruction',
	transaction: 'Transaction',
	conversation: 'Conversation',
	// The adviser whose channel it is. Without this the chip fell back to de-slugging the raw
	// key and read "Conversation_adviser" - a field name on a surface read by advisers.
	conversation_adviser: 'Adviser',
	// Added by the September 2026 event-content review — the kinds whose ids the stream had always
	// recorded and the read side could not name (api: admin._ENTITY_RESOLVERS).
	automation: 'Automation',
	content: 'Content',
	contribution: 'Contribution',
	transfer: 'Transfer',
	dataset: 'Dataset',
	document_request: 'Document request',
	document_type: 'Document type',
	email_template: 'Email template',
	journey: 'Journey',
	case: 'Journey case',
	message_group: 'Group chat',
	file: 'File',
	storage_credential: 'Storage credentials',
	environment: 'Environment',
	beneficiary: 'Beneficiary',
	product: 'Product',
	fee_package: 'Fee package',
	catalogue_provider: 'Provider',
	notification: 'Notification',
	notification_rule: 'Notification rule',
	aml_search: 'Screening search',
	aml_profile: 'Screening profile',
	aml_hit: 'Screening hit'
};

export function entityKindLabel(kind: string): string {
	return ENTITY_KIND_LABELS[kind] ?? kind.charAt(0).toUpperCase() + kind.slice(1);
}

/** The first RESOLVED entity of a kind — enrichment only ever weaves in labels, never bare ids
 *  (a sentence reading "Linked 550e84… to 96" would be worse than the plain fallback). */
function labelled(entities: ActionEntity[], kind: string): ActionEntity | undefined {
	return entities.find((e) => e.kind === kind && e.label);
}

/** The label of the SPECIFIC entity a captured id names — never positional (`labelled` picks the
 *  first of a kind, which is wrong the moment an event carries two models: old and new). */
function labelForId(entities: ActionEntity[], kind: string, id: unknown): string | null {
	if (id === null || id === undefined) return null;
	return entities.find((e) => e.kind === kind && e.id === String(id))?.label ?? null;
}

/** A non-empty string, or null — captured metadata is untyped at this boundary. */
function asString(value: unknown): string | null {
	return typeof value === 'string' && value ? value : null;
}

/** A list of strings, or null (the shape `permissions` / `contact_ids` captures). */
function asStringList(value: unknown): string[] | null {
	return Array.isArray(value) && value.every((v) => typeof v === 'string')
		? (value as string[])
		: null;
}

/** `compliance-officer` / `role_slug` → "Compliance officer" / "Role slug" — slugs and field
 *  names read as words. Product-vocabulary aliases apply per word (contact → client). */
export function deslug(slug: string): string {
	const words = slug
		.replace(/[-_]+/g, ' ')
		.trim()
		.split(' ')
		.map((w) => NOUNS[w] ?? w)
		.join(' ');
	return words.charAt(0).toUpperCase() + words.slice(1);
}

/** The words of a slug, with the estate's acronyms cased (`sei` → "SEI") and the product
 *  vocabulary applied. The shared half of `deslugCased` and `fieldWord`. */
function slugWords(slug: string): string[] {
	return slug
		.replace(/[-_]+/g, ' ')
		.trim()
		.split(' ')
		.filter(Boolean)
		.map((w) => WORD_CASING[w] ?? NOUNS[w] ?? w);
}

/** Is this word one the casing map owns — an acronym or a proper noun (SEI, AML, WorkOS)? Such a
 *  word must never be re-cased by a sentence position rule. */
function alreadyCased(word: string): boolean {
	return Object.values(WORD_CASING).includes(word);
}

/**
 * `deslug`, but the estate's acronyms survive: a provider slug reads "SEI", not "Sei". A separate
 * function rather than a fix to `deslug`, which renders firm-authored vocabulary where a collision
 * with this map ("id", "api") would shout a word the firm wrote in lower case. This one is for
 * values drawn from OUR closed vocabularies, where the acronym is the point.
 */
export function deslugCased(slug: string): string {
	const parts = slugWords(slug);
	const joined = parts.join(' ');
	const first = parts[0] ?? '';
	return alreadyCased(first) ? joined : joined.charAt(0).toUpperCase() + joined.slice(1);
}

/**
 * The added/removed sets between two captured permission lists (previous → subject), sorted for
 * stable rendering. Null when either side is missing or mis-shaped — a diff must never be
 * guessed from one side.
 */
export function permissionsDiff(
	previous: unknown,
	next: unknown
): { added: string[]; removed: string[] } | null {
	const prev = asStringList(previous);
	const nxt = asStringList(next);
	if (!prev || !nxt) return null;
	const prevSet = new Set(prev);
	const nextSet = new Set(nxt);
	return {
		added: [...nextSet].filter((slug) => !prevSet.has(slug)).sort(),
		removed: [...prevSet].filter((slug) => !nextSet.has(slug)).sort()
	};
}

/**
 * One captured field name as a reader should see it: `employer_name` → "employer name", with the
 * id/uid/slug suffix dropped and the estate's acronyms kept upper-case. Lower-case on purpose —
 * these words appear INSIDE a sentence, where `deslug`'s leading capital would read as a new one.
 */
export function fieldWord(field: string): string {
	// Already lower-case except where the casing map raised a word (AML, SQL, AI), which is exactly
	// the set that must survive a sentence-position rule.
	return slugWords(field.replace(/_(id|ids|uid|slug)$/, '')).join(' ');
}

/** The fields an update MOVED, preferring the recorded diff over the list the request carried.
 *
 *  `metadata.changed` is the names the caller SENT. A form posts every field it holds, so a save
 *  that moved one of them still carried three — and the row then said "bank details: account
 *  number, name, sort code" when only the account number was different. That is not a changed
 *  field, it is a submitted one.
 *
 *  `metadata.changes` (the row diff) is the answer when it exists: it is the difference between two
 *  reads of the row, so every key in it genuinely moved. `changed` stays as the fallback for the
 *  events that have no diff declared, and for rows written before either capture existed. */
export function changedFields(metadata: ActionEvent['metadata'] | null | undefined): string[] {
	const moved = metadata?.changes;
	if (moved && typeof moved === 'object' && !Array.isArray(moved)) {
		const keys = Object.keys(moved as Record<string, unknown>).filter((k) => k !== '_omitted');
		if (keys.length > 0) return keys;
	}
	const raw = metadata?.changed;
	if (!Array.isArray(raw)) return [];
	return raw.filter((f): f is string => typeof f === 'string' && f.length > 0);
}

/** How many changed fields a sentence names before it stops listing them. Three reads as a phrase;
 *  a dozen reads as a dump, and the expanded detail states the full list anyway. */
const MAX_NAMED_CHANGES = 3;

/** ": email, phone and 2 more" — appended to whatever sentence the event already had, so the
 *  enrichment can only ever ADD to the reading. Empty when nothing was captured. */
function changedClause(metadata: ActionEvent['metadata'] | null | undefined): string {
	const fields = changedFields(metadata);
	if (fields.length === 0) return '';
	const shown = fields.slice(0, MAX_NAMED_CHANGES).map(fieldWord);
	const rest = fields.length - shown.length;
	return `: ${shown.join(', ')}${rest > 0 ? ` and ${rest} more` : ''}`;
}

/** Everything a curated sentence builder may weave in: the read-time entity labels plus the
 *  write-time captures (subject = "to what", previous = "from what") and the path params. */
interface EnrichmentContext {
	entities: ActionEntity[];
	pathParams: Record<string, unknown>;
	subject: Record<string, unknown>;
	previous: Record<string, unknown>;
}

/** "Changed {member}'s platform role: Adviser → Compliance" — shared by the firm and admin
 *  member-role routes (same path-param names, same subject/previous shape). */
function roleChangeSentence({ entities, pathParams, subject, previous }: EnrichmentContext) {
	// BOTH SPELLINGS. `GrantPortal` accepts `role_slug` or `role_slugs` and the UI sends the LIST,
	// so a singular-only read showed the pre-read's "was Adviser" with nothing beside it. Historic
	// rows carry the singular, so both are read and the list wins.
	const roles = (v: unknown, one: unknown) =>
		asStringList(v) ?? (asString(one) ? [asString(one) as string] : null);
	const next = roles(subject['role_slugs'], subject['role_slug']);
	const old = roles(previous['role_slugs'], previous['role_slug']);
	if (!next && !old) return null;
	const member = labelled(entities, 'user')?.label ?? null;
	if (!member && !old) return null; // nothing beyond the generic sentence to say
	const who = member ?? 'a member';
	const portal = asString(pathParams['portal']);
	const scope = portal ? `${portal} role` : 'role';
	const words = (list: string[]) => list.map(deslug).join(', ');

	if (!old) return `Set ${who}'s ${scope} to ${words(next ?? [])}`;
	if (!next) return `Removed ${who}'s ${scope}`;

	// WHAT MOVED, not just what it is now. A reader of a role change is asking "did somebody gain
	// access or lose it", and a bare "Adviser" answers neither.
	const gained = next.filter((r) => !old.includes(r));
	const lost = old.filter((r) => !next.includes(r));
	if (gained.length === 0 && lost.length === 0) return `Left ${who}'s ${scope} unchanged`;
	if (gained.length === 0) return `Removed ${words(lost)} from ${who}'s ${scope}`;
	if (lost.length === 0) return `Gave ${who} the ${words(gained)} ${scope}`;
	return `Changed ${who}'s ${scope}: ${words(old)} → ${words(next)}`;
}

/** "Updated role Compliance: +2 permissions, −1" — the summary counts; the expanded grid shows
 *  the full added/removed chips. Shared by the firm and admin role-permission routes. */
function rolePermissionsSentence({ pathParams, subject, previous }: EnrichmentContext) {
	const slug = asString(pathParams['slug']);
	if (!slug || !asStringList(subject['permissions'])) return null;
	const diff = permissionsDiff(previous['permissions'], subject['permissions']);
	if (!diff) return `Updated role ${deslug(slug)}'s permissions`;
	const parts: string[] = [];
	if (diff.added.length > 0)
		parts.push(`+${diff.added.length} permission${diff.added.length === 1 ? '' : 's'}`);
	if (diff.removed.length > 0)
		parts.push(
			parts.length > 0
				? `−${diff.removed.length}`
				: `−${diff.removed.length} permission${diff.removed.length === 1 ? '' : 's'}`
		);
	if (parts.length === 0) return `Updated role ${deslug(slug)}: no permission changes`;
	return `Updated role ${deslug(slug)}: ${parts.join(', ')}`;
}

/** "Invited m.cole@example.com as Compliance officer" — shared by the firm + admin invite routes.
 *  The invited login is the response-captured `user_id` (RESPONSE_SUBJECT_FIELDS), the role the
 *  request's `role_slug`; names the person once the id resolves, the generic "Invited a member"
 *  until then. */
function inviteSentence({ entities, subject }: EnrichmentContext) {
	const user = labelled(entities, 'user');
	const role = asString(subject['role_slug']);
	if (user && role) return `Invited ${user.label} as ${deslug(role)}`;
	if (user) return `Invited ${user.label}`;
	return null;
}

/** "Moved Margaret Cole - GBP 500.00 from Matched to Lodged" — the same shape for a contribution
 *  and a transfer, which are the two records whose whole lifecycle is a status. The stage it came
 *  FROM is the write-time pre-read (`PREVIOUS_FIELDS`); an older row that has none still reads
 *  correctly, it just says less. */
function statusChangeSentence(kind: 'contribution' | 'transfer') {
	return ({ entities, subject, previous }: EnrichmentContext) => {
		const next = asString(subject['status']);
		if (!next) return null;
		const what = labelled(entities, kind)?.label ?? `a ${kind}`;
		const old = asString(previous['status']);
		return old && old !== next
			? `Moved ${what} from ${deslug(old)} to ${deslug(next)}`
			: `Set ${what} to ${deslug(next)}`;
	};
}

/** "Started a sync on SEI" — the pipeline acts, each of which names the provider it ran against
 *  and otherwise reads identically whichever custodian it was. The provider is a path param, so
 *  there is nothing to capture: the sentence just has to read it. */
function providerActSentence(lead: string) {
	return ({ pathParams }: EnrichmentContext) => {
		const provider = asString(pathParams['provider']);
		return provider ? `${lead} ${deslugCased(provider)}` : null;
	};
}

/**
 * The curated ENRICHED sentences, per event family: given the resolved entities and the
 * write-time captures, produce the line an operator actually wants ("Linked m.cole@example.com
 * to Margaret Cole as authorised"). Each builder returns null when the material it needs is
 * missing — the caller then falls back to the generic sentence, so enrichment can only ever
 * improve on it. Keyed by operation id (tag included: these are specific routes, not families
 * to fuzzy-match).
 */
const ENRICHED: Record<string, (ctx: EnrichmentContext) => string | null> = {
	// Auth sign-ins name the METHOD (WorkOS-aligned): "Signed in with a password". The method is a
	// captured subject enum; absent (an older row, or an un-plumbed method) ⇒ the plain verb.
	'authentication.succeeded': ({ subject }) => {
		const m = asString(subject['method']);
		const device = asString(subject['device']);
		const how = m ? `Signed in with ${AUTH_METHOD_WORDS[m] ?? m}` : 'Signed in';
		return device ? `${how} from ${device}` : how;
	},
	'authentication.failed': ({ subject }) => {
		const m = asString(subject['method']);
		return m ? `Failed to sign in with ${AUTH_METHOD_WORDS[m] ?? m}` : 'Failed sign-in';
	},
	// Data egress, every format: the act a reader scans for is a listing leaving the platform.
	'export.listing_exported': ({ subject }) => {
		const listing = asString(subject['listing']);
		const format = asString(subject['format']);
		const what = (listing && EXPORT_LISTING_WORDS[listing]) ?? 'a listing';
		return format
			? `Exported ${what} as ${EXPORT_FORMAT_WORDS[format] ?? format}`
			: `Exported ${what}`;
	},
	'client.portal_user_linked': ({ entities, subject }) => {
		const user = labelled(entities, 'user');
		const contact = labelled(entities, 'contact');
		// "In what capacity" — the subject's closed enum, woven in verbatim (authorised, poa…).
		const capacity = asString(subject['capacity']);
		const as = capacity ? ` as ${capacity}` : '';
		if (user && contact) return `Linked ${user.label} to ${contact.label}${as}`;
		if (contact) return `Linked a user to ${contact.label}${as}`;
		return null;
	},
	'client.portal_user_unlinked': ({ entities }) => {
		const user = labelled(entities, 'user');
		const contact = labelled(entities, 'contact');
		if (user && contact) return `Unlinked ${user.label} from ${contact.label}`;
		if (contact) return `Unlinked a user from ${contact.label}`;
		return null;
	},
	'client.adviser_assigned': ({ entities }) => {
		const user = labelled(entities, 'user');
		const contact = labelled(entities, 'contact');
		if (user && contact) return `Assigned ${user.label} as adviser to ${contact.label}`;
		if (contact) return `Assigned an adviser to ${contact.label}`;
		return null;
	},
	'client.adviser_unassigned': ({ entities }) => {
		const user = labelled(entities, 'user');
		const contact = labelled(entities, 'contact');
		if (user && contact) return `Removed ${user.label} as adviser from ${contact.label}`;
		if (contact) return `Removed an adviser from ${contact.label}`;
		return null;
	},
	'instruction.created': ({ entities }) => {
		const account = labelled(entities, 'account');
		const contact = labelled(entities, 'contact');
		if (account && contact)
			return `Created an instruction on ${account.label} for ${contact.label}`;
		if (contact) return `Created an instruction for ${contact.label}`;
		return null;
	},
	'member.portal_role_updated': roleChangeSentence,
	'admin-set-member-portal-role': roleChangeSentence,
	'role.permissions_updated': rolePermissionsSentence,
	'admin-set-tenant-role-permissions': rolePermissionsSentence,
	'member.invited': inviteSentence,
	'member.created': inviteSentence,
	'contribution.created': ({ entities }) => {
		const account = labelled(entities, 'account');
		return account ? `Booked a contribution on ${account.label}` : null;
	},
	'contribution.transaction_classified': ({ entities }) => {
		const txn = labelled(entities, 'transaction');
		return txn ? `Classified ${txn.label}` : null;
	},
	'transfer.created': ({ entities }) => {
		const account = labelled(entities, 'account');
		return account ? `Started a transfer on ${account.label}` : null;
	},
	'conversation.opened': ({ entities }) => {
		const contact = labelled(entities, 'contact');
		return contact ? `Opened a conversation with ${contact.label}` : null;
	},
	// Both ends of the channel emit this one event type, so the recipient is whichever end the
	// SENDER is not. A staff send carries the conversation on its route and that resolves to the
	// client on the channel; a client's own thread route names no conversation, so the id is lifted
	// off the response and resolved to the ADVISER instead (naming the conversation's client there
	// would name the sender — "Craig Currie sent a message to Craig Currie").
	//
	// Client first because a staff send carries BOTH: the path-named conversation is the specific
	// answer, the adviser the fallback. Never the message text (bodies are never captured;
	// docs/messaging.md).
	'message.sent': ({ entities }) => {
		const convo = labelled(entities, 'conversation');
		if (convo) return `Sent a message to ${convo.label}`;
		// The client's own send. The row is attributed to the ACTOR - the acting login, which is what
		// an audit trail owes you - and the login is not the client: it is an `auth.users` row acting
		// FOR a `crm.contacts` one, and one login may act for several contacts. So the sentence names
		// whose channel it is, or the row would say a login sent a message and never say for whom.
		const adviser = labelled(entities, 'conversation_adviser');
		const client = labelled(entities, 'contact');
		if (adviser && client) return `Sent a message to ${adviser.label} as ${client.label}`;
		if (adviser) return `Sent a message to ${adviser.label}`;
		if (client) return `Sent a message as ${client.label}`;
		return null;
	},
	// An attachment is the same act from either end of the channel, so ONE builder serves both: the
	// staff side carries a conversation id and names the recipient, the client's own thread carries
	// none and gets the plain verb. Never the message text (bodies are never captured;
	// docs/messaging.md).
	//
	// This used to be two entries, and the two ends of the channel emitted two event types. With one
	// type per act the recipient-naming builder has to be the one that survives — the plain one would
	// silently drop the recipient from every staff-sent attachment.
	'message.attachment_sent': ({ entities, subject }) => {
		// WHICH file, where it was captured. "Sent an attachment" is true of every one of these
		// rows; the filename is the only thing that tells two of them apart.
		const files = Array.isArray(subject?.attachment_filenames)
			? subject.attachment_filenames.filter((f): f is string => typeof f === 'string')
			: [];
		const what =
			files.length === 1
				? files[0]
				: files.length > 1
					? `${files.length} attachments`
					: 'an attachment';
		const convo = labelled(entities, 'conversation');
		if (convo) return `Sent ${what} to ${convo.label}`;
		const adviser = labelled(entities, 'conversation_adviser');
		const client = labelled(entities, 'contact');
		if (adviser && client) return `Sent ${what} to ${adviser.label} as ${client.label}`;
		if (adviser) return `Sent ${what} to ${adviser.label}`;
		if (client) return `Sent ${what} as ${client.label}`;
		return `Sent ${what}`;
	},
	'message_group.created': () => 'Created a message group',
	'message_group.message_sent': () => 'Sent a message to a group',
	'message_group.attachment_sent': () => 'Sent an attachment to a group',
	'message_group.document_requested': () => 'Requested documents in a group',
	'account.model_updated': ({ entities, subject, previous }) => {
		const account = labelled(entities, 'account');
		if (!account) return null;
		// From/to by SPECIFIC id — an event carrying previous + subject has TWO model entities,
		// and positional lookup would happily swap them.
		const newLabel = labelForId(entities, 'model', subject['model_id']);
		const oldLabel = labelForId(entities, 'model', previous['model_id']);
		if (oldLabel && newLabel) return `Changed ${account.label}'s model: ${oldLabel} → ${newLabel}`;
		const anyModel = newLabel ?? labelled(entities, 'model')?.label ?? null;
		if (anyModel) return `Set ${account.label} to model ${anyModel}`;
		return `Set ${account.label}'s model`;
	},
	// An upload NAMES what it created. The event carries the created uids as a list
	// (`metadata.subject.document_uids`, lifted off the 2xx response — the row does not exist when
	// the request arrives), resolved read-time to labels like every other entity chip. Before this it
	// read "Uploaded a document" on every row, about which one nobody could say — and the document's
	// own Events tab did not show its upload at all, because nothing tied the event to it.
	//
	// One request may carry several files, so the count is part of the sentence rather than a lie
	// about there being one. The client it was filed under is named when the upload declared one.
	'document.uploaded': ({ entities }) => {
		const docs = entities.filter((e) => e.kind === 'document' && e.label);
		const contact = labelled(entities, 'contact')?.label;
		const what =
			docs.length === 0
				? null
				: docs.length === 1
					? docs[0]!.label
					: `${docs[0]!.label} and ${docs.length - 1} more`;
		if (what && contact) return `Uploaded ${what} for ${contact}`;
		if (what) return `Uploaded ${what}`;
		// Unresolvable (deleted since) — the generic wording, never a raw uid.
		return contact ? `Uploaded a document for ${contact}` : null;
	},
	'document.generated': ({ entities }) => {
		const template = labelled(entities, 'template');
		const contact = labelled(entities, 'contact');
		if (template && contact) return `Generated ${template.label} for ${contact.label}`;
		if (template) return `Generated a document from ${template.label}`;
		if (contact) return `Generated a document for ${contact.label}`;
		return null;
	},
	'document.sent': ({ entities }) => {
		const contacts = entities.filter((e) => e.kind === 'contact' && e.label);
		if (contacts.length === 0) return null;
		const extra = contacts.length - 1;
		const doc = labelled(entities, 'document')?.label;
		const who = `${contacts[0]!.label}${extra > 0 ? ` and ${extra} more` : ''}`;
		return doc ? `Sent ${doc} to ${who}` : `Sent a document to ${who}`;
	},
	// Reading a document is the single most repeated event in the stream, so "viewed a document"
	// over and over says almost nothing — the row is only useful if it names WHICH document. The
	// label is already resolved on the event (entities[document].label, read-time from the uid on
	// the route path), so the sentence just has to use it; unresolvable (deleted) falls back to the
	// generic wording rather than printing a raw uid.
	// Who was told, and about what. The contacts come from the event's own captured `contact_ids`
	// (resolved to labels read-time); the document from the uid on the route path. Deliberately says
	// "about", never "sent" — a notice is a nudge about something the client could already open.
	'document.notified': ({ entities }) => {
		const contacts = entities.filter((e) => e.kind === 'contact' && e.label);
		const doc = labelled(entities, 'document')?.label;
		if (contacts.length === 0) return doc ? `Notified a client about ${doc}` : null;
		const extra = contacts.length - 1;
		const who = `${contacts[0]!.label}${extra > 0 ? ` and ${extra} more` : ''}`;
		return doc ? `Notified ${who} about ${doc}` : `Notified ${who} about a document`;
	},
	'document.viewed': ({ entities }) => {
		const doc = labelled(entities, 'document')?.label;
		return doc ? `Viewed ${doc}` : null;
	},
	'document.rendered': ({ entities }) => {
		const doc = labelled(entities, 'document')?.label;
		return doc ? `Rendered ${doc}` : null;
	},
	'document.archived': ({ entities }) => {
		const doc = labelled(entities, 'document')?.label;
		return doc ? `Archived ${doc}` : null;
	},
	'document.unarchived': ({ entities }) => {
		const doc = labelled(entities, 'document')?.label;
		return doc ? `Restored ${doc}` : null;
	},
	'document.updated': ({ entities }) => {
		const doc = labelled(entities, 'document')?.label;
		const contact = labelled(entities, 'contact');
		if (doc && contact) return `Filed ${doc} under ${contact.label}`;
		if (doc) return `Updated ${doc}`;
		return contact ? `Filed a document under ${contact.label}` : null;
	},
	'document_request.created': ({ entities }) => {
		const contact = labelled(entities, 'contact');
		return contact ? `Requested documents from ${contact.label}` : null;
	},
	'task.created': ({ entities }) => {
		const assignee = labelled(entities, 'user') ?? labelled(entities, 'contact');
		return assignee ? `Created a task for ${assignee.label}` : null;
	},
	/**
	 * The task's own name, which is the only part of this row worth scanning — "a task is due soon"
	 * is the same sentence on every one of them, and a reader with four in a row learns nothing.
	 *
	 * It needs a builder rather than a `LABEL_OVERRIDES` entry because an override SUPPRESSES the
	 * subject naming (`declaredLabel` is only reached when there is no override), so the two cannot
	 * both apply. ENRICHED runs first and falls through to that override when the task cannot be
	 * resolved — a deleted task still gets a sentence rather than a bare id.
	 *
	 * Reads as a statement, not an act: nobody did this, a clock did.
	 */
	'task.due_soon': ({ entities }) => {
		const task = labelled(entities, 'task');
		return task ? `${task.label} is due soon` : null;
	},
	/** Same reasoning, and the reason it needs saying: "Assigned File the signed declaration" reads
	 *  as though somebody assigned a file. The preposition is what makes it a sentence. */
	'task.assigned': ({ entities }) => {
		const task = labelled(entities, 'task');
		const assignee = labelled(entities, 'user') ?? labelled(entities, 'contact');
		if (task && assignee) return `Assigned ${task.label} to ${assignee.label}`;
		if (task) return `Assigned the task ${task.label}`;
		return assignee ? `Assigned a task to ${assignee.label}` : null;
	},
	'aml.screen_ran': ({ entities }) => {
		const contact = labelled(entities, 'contact');
		return contact ? `Screened ${contact.label}` : null;
	},
	'aml.profile_linked': ({ entities }) => {
		const contact = labelled(entities, 'contact');
		return contact ? `Linked an AML profile to ${contact.label}` : null;
	},
	// ---- added by the September 2026 event-content review ------------------------------------
	// Each of these had a sentence that named the KIND of thing and nothing else. The material was
	// either already on the row (a path param) or is captured now; these weave it in.
	'role.created': ({ subject }) => {
		const slug = asString(subject['slug']);
		if (!slug) return null;
		const portal = asString(subject['portal']);
		return portal
			? `Created the ${deslug(slug)} role for the ${portal} portal`
			: `Created the ${deslug(slug)} role`;
	},
	'member.portal_granted': ({ entities, pathParams, subject }) => {
		const member = labelled(entities, 'user')?.label;
		const portal = asString(pathParams['portal']);
		const role = asString(subject['role_slug']);
		if (!member && !portal) return null;
		const who = member ?? 'a member';
		const what = portal ? `the ${portal} portal` : 'a portal';
		return role ? `Gave ${who} ${what} as ${deslug(role)}` : `Gave ${who} ${what}`;
	},
	'member.portal_revoked': ({ entities, pathParams }) => {
		const member = labelled(entities, 'user')?.label;
		const portal = asString(pathParams['portal']);
		if (!member && !portal) return null;
		const what = portal ? `the ${portal} portal` : 'a portal';
		return `Took ${what} from ${member ?? 'a member'}`;
	},
	'member.provider_ref_updated': ({ entities, pathParams, subject }) => {
		const member = labelled(entities, 'user')?.label;
		const provider = asString(pathParams['provider']);
		const ref = asString(subject['external_id']);
		if (!member || !ref) return null;
		return provider
			? `Recorded ${member} as ${ref} at ${deslugCased(provider)}`
			: `Recorded ${member}'s custodian id as ${ref}`;
	},
	'client.portal_invited': ({ entities, subject }) => {
		const client = labelled(entities, 'contact')?.label;
		if (!client) return null;
		const roles = asStringList(subject['role_slugs']) ?? [];
		const single = asString(subject['role_slug']);
		const named = roles.length > 0 ? roles : single ? [single] : [];
		return named.length > 0
			? `Invited ${client} to the client portal as ${named.map(deslug).join(', ')}`
			: `Invited ${client} to the client portal`;
	},
	// A money record moving through its lifecycle. The stage it came FROM is the pre-read
	// (PREVIOUS_FIELDS); without it the row says a status changed and not what changed about it.
	'contribution.status_updated': statusChangeSentence('contribution'),
	'transfer.status_updated': statusChangeSentence('transfer'),
	'contribution.categorised': ({ entities, subject, previous }) => {
		const next = asString(subject['category']);
		if (!next) return null;
		const what = labelled(entities, 'contribution')?.label ?? 'a contribution';
		const old = asString(previous['category']);
		return old && old !== next
			? `Reclassified ${what}: ${deslug(old)} → ${deslug(next)}`
			: `Classified ${what} as ${deslug(next)}`;
	},
	'contribution.cancelled': ({ entities, subject }) => {
		const what = labelled(entities, 'contribution')?.label ?? 'a contribution';
		const reason = asString(subject['reason']);
		return reason ? `Cancelled ${what} — ${reason}` : `Cancelled ${what}`;
	},
	'journey.cancelled': ({ entities, subject }) => {
		const what = labelled(entities, 'journey')?.label;
		const reason = asString(subject['reason']);
		const lead = what ? `Cancelled the ${what} journey` : 'Cancelled a journey';
		return reason ? `${lead} — ${reason}` : what ? lead : null;
	},
	'account.beneficiary_added': ({ entities, subject }) => {
		const share = asString(subject['proportion']);
		const relationship = asString(subject['relationship']);
		const account = labelled(entities, 'account')?.label;
		if (!share && !relationship) return null;
		const who = relationship ? deslug(relationship) : 'a beneficiary';
		const how = share ? ` for ${share}%` : '';
		return account ? `Nominated ${who}${how} on ${account}` : `Nominated ${who}${how}`;
	},
	'aml.monitoring_updated': ({ entities, subject }) => {
		const on = subject['enabled'];
		if (typeof on !== 'boolean') return null;
		const who = labelled(entities, 'aml_search')?.label;
		const verb = on ? 'Turned monitoring on' : 'Turned monitoring off';
		return who ? `${verb} for ${who}` : verb;
	},
	'firm.data_reset_requested': ({ entities, subject }) => {
		const kind = asString(subject['kind']);
		const firm = labelled(entities, 'tenant')?.label;
		if (!kind) return null;
		return firm
			? `Requested a ${deslug(kind).toLowerCase()} data reset for ${firm}`
			: `Requested a ${deslug(kind).toLowerCase()} data reset`;
	},
	// The query IS the act. "Ran a warehouse query" records that somebody looked and not at what,
	// which is the one thing the row exists to say; the full SQL is in the expanded detail.
	'platform.queried': ({ pathParams, subject }) => {
		const provider = asString(pathParams['provider']);
		const sql = asString(subject['sql']);
		if (!sql) return null;
		const oneLine = sql.replace(/\s+/g, ' ').trim();
		const shown = oneLine.length > 80 ? `${oneLine.slice(0, 79)}…` : oneLine;
		return provider
			? `Ran a warehouse query on ${deslugCased(provider)}: ${shown}`
			: `Ran a warehouse query: ${shown}`;
	},
	'export.pdf_rendered': ({ subject }) => {
		const title = asString(subject['title']);
		return title ? `Exported ${title} as PDF` : null;
	},
	// The pipeline acts. The provider is a PATH param, so it was on the row all along and the
	// sentence simply never read it: "Started a provider sync" is the same line whichever
	// custodian was refreshed, which is the one thing an operator watching a pipeline needs.
	'platform.sync_started': providerActSentence('Started a sync on'),
	'platform.rebuilt': providerActSentence('Rebuilt the warehouse for'),
	'platform.all_firms_refreshed': providerActSentence('Refreshed every firm on'),
	// A batch generate is ONE row for up to 250 clients (the middleware emits one per request),
	// which is why it has its own event name. Say which template and how many clients — naming
	// them all would be a paragraph, and the entity chips below the sentence list them.
	'document.batch_generated': ({ entities, subject }) => {
		const template = labelled(entities, 'template')?.label;
		const recipients = asStringList(subject['contact_ids'])?.length ?? 0;
		if (!template && recipients === 0) return null;
		const what = template ? `${template}` : 'a template';
		if (recipients === 0) return `Generated ${what} for a batch of clients`;
		return `Generated ${what} for ${recipients} client${recipients === 1 ? '' : 's'}`;
	}
};

/**
 * `client.created` → "Created a client".
 *
 * The operation id is `{tag}-{route-name}`; the tag (router prefix: firm/admin/crm/…) is context
 * the sentence doesn't need, so it is dropped. A leading curated verb becomes past tense; the
 * remaining words are de-slugged, mapped through the product-vocabulary aliases, and given an
 * article. Anything unrecognised degrades to a readable de-slugged phrase — never an empty
 * string, never a throw (this renders evidence; it must cope with event types minted after it).
 *
 * `pathParams` sharpens one case: a `portal` path param names WHICH portal was granted/revoked
 * ("Granted client portal access" rather than the generic "Granted portal access").
 *
 * `entities` (the API's read-time id → label resolution) and `metadata` (the write-time
 * subject/previous captures) upgrade the curated families to the entity-rich sentence —
 * "Changed m.cole@example.com's platform role: Adviser → Compliance" — and fall back to the
 * generic form whenever the material isn't there. Enrichment only ever adds; it never degrades.
 */
/**
 * The API's router TAGS - the first segment of every operation id (`app/audit_actions.py::operation_id_for`
 * builds `{tag}-{route name}`). Longest first, so a multi-word tag is stripped WHOLE.
 *
 * Why a list is needed at all rather than "drop the first word": two shapes made the generic path
 * produce machine text, and both are common.
 *
 *   * A MULTI-WORD tag. `email-templates-create-email-template` lost only `email`, so the label read
 *     "Templates create email template".
 *   * A route whose own name REPEATS its tag - `admin_create_email_template` under tag `admin`,
 *     `firm_update_notification_rule` under `firm`, every `messaging_*` handler. Dropping one word
 *     left the other, so the label read "Admin create email template", which also made the ops route
 *     and the firm route for the same act read as two unrelated entries in the picker.
 *
 * 26 of the estate's operations are the second shape and 9 the first - a third of the vocabulary.
 * `describe.test.ts` asserts every operation id in the estate starts with one of these, so a NEW
 * router tag fails a test rather than shipping a label with the tag still in it.
 */
const ROUTER_TAGS: readonly string[] = [
	'email-templates',
	'contributions',
	'notifications',
	'applications',
	'automations',
	'catalogue',
	'documents',
	'datasets',
	'messaging',
	'platforms',
	'reports',
	'internal',
	'templates',
	'transfers',
	'storage',
	'shares',
	'admin',
	'files',
	'flows',
	'forms',
	'tasks',
	'auth',
	'aml',
	'crm',
	'firm'
];

/**
 * The operation id with its router tag removed - including a copy the route name repeats.
 *
 * An id whose tag is not in `ROUTER_TAGS` falls back to dropping the first word, which is what this
 * did for every id before: a new tag then reads slightly wrong rather than not at all.
 */
export function stripRouterTag(opId: string): string {
	for (const tag of ROUTER_TAGS) {
		if (opId === tag) return opId; // the tag IS the whole id - nothing else to say
		if (!opId.startsWith(`${tag}-`)) continue;
		const rest = opId.slice(tag.length + 1);
		// `admin-admin-create-...` / `messaging-messaging-thread-send`: the name repeats the tag.
		if (rest === tag) return rest;
		return rest.startsWith(`${tag}-`) ? rest.slice(tag.length + 1) : rest;
	}
	const words = opId.split('-').filter(Boolean);
	return words.length > 1 ? words.slice(1).join('-') : opId;
}

/**
 * Operations whose label the grammar cannot reach, written out.
 *
 * The generic path turns `verb-noun-noun` into a sentence, which covers most of the estate. These are
 * the ones it cannot: a VERB-LAST name (`conversation-read`), a name that is a noun phrase rather than
 * an act (`account-drawdown-session`, `ws-ticket`), and the handful where the honest label needs a
 * word the id does not contain ("a dry run", "every firm"). Keyed on the FULL operation id, so the two
 * routes for one act can be named apart where that distinction is the point.
 *
 * Consulted AFTER `ENRICHED` (a real subject name still wins) and before the generic path.
 */
const OPERATION_LABELS: Record<string, string> = {
	// Platforms console.
	'admin-platform-query': 'Ran a warehouse query',
	'admin-platform-sync': 'Started a provider sync',
	'admin-platform-rebuild': 'Rebuilt a provider warehouse',
	'admin-refresh-platform-tenants': 'Refreshed every firm’s provider data',
	'admin-refresh-tenant-provider': 'Refreshed a firm’s provider data',
	'admin-refresh-tenant-usage': 'Refreshed a firm’s usage',
	'admin-set-tenant-provider': 'Set a firm’s provider',
	// Firm configuration.
	'admin-set-tenant-ai': 'Set a firm’s AI settings',
	'admin-reset-tenant-member-mfa': 'Reset a member’s MFA',
	'admin-provision-tenant-storage': 'Provisioned a firm’s storage',
	'admin-provision-all-tenant-storage': 'Provisioned storage for every firm',
	'admin-set-automation-platform-pause': 'Set the platform automation pause',
	'admin-set-notification-config': 'Set the notification settings',
	'admin-load-notification-defaults': 'Loaded the default notification rules',
	'admin-refresh-environment-registry': 'Refreshed the environment registry',
	// Email templates - the two routes for one act, named apart.
	'admin-admin-email-templates-load-defaults': 'Loaded the built-in email templates',
	'admin-admin-email-templates-provision': 'Provisioned the email-template preset',
	// Screening.
	'aml-set-aml-monitoring': 'Set AML monitoring',
	// Journeys.
	'applications-acknowledge-application-terms': 'Accepted a declaration',
	'applications-action-application-step': 'Actioned a journey step',
	'flows-simulate-flow': 'Simulated a journey (a dry run)',
	'flows-validate-flow-definition': 'Validated a journey definition',
	'automations-test-automation': 'Tested an automation (a dry run)',
	'automations-reorder-automations': 'Reordered the automations',
	// Money.
	'crm-account-drawdown-session': 'Started a drawdown session',
	// Storage credentials.
	'storage-issue-own-storage-credentials': 'Issued their own storage credentials',
	'storage-issue-member-storage-credentials': 'Issued storage credentials for a member',
	// Messaging + the transport handshake (hidden from the feed, but named for the raw stream).
	'messaging-messaging-conversation-send': 'Sent a message in a conversation',
	'messaging-messaging-conversation-read': 'Read a conversation',
	'messaging-messaging-thread-read': 'Read their messages',
	'messaging-messaging-conversations-open': 'Opened a conversation',
	'messaging-messaging-ws-ticket': 'Opened a live connection',
	'notifications-ws-ticket': 'Opened a live connection',
	'notifications-dismiss-all-notifications': 'Dismissed every notification',
	'notifications-mark-all-notifications-read': 'Marked every notification read'
};

/**
 * The past-tense verbs a declared event name can end in — the tail of `resource.verb`.
 *
 * The vocabulary is `resource.verb_past` (`document.sent`, `email_template.version_published`), and a
 * compound verb puts its qualifier FIRST (`version_published`), so the last word is the verb and
 * whatever precedes it describes the thing. This set is how the two are told apart without a table
 * per event.
 */
const PAST_VERBS: ReadonlySet<string> = new Set(Object.values(VERBS).map((v) => v.toLowerCase()));

/**
 * The sentences the derivation cannot reach, written out.
 *
 * `resource.verb` is regular enough that most names read correctly as "{Verb} a {resource}
 * {qualifier}". These are the ones where that is wrong rather than merely plain: a verb whose tail is
 * not the verb (`recorded_at_morgan_lloyd`), a qualifier that is a determiner rather than a noun
 * (`all_read`), and the handful where the honest sentence needs a word the name does not contain.
 */
const LABEL_OVERRIDES: Record<string, string> = {
	// A possessive reads better than a bare noun stack wherever the qualifier belongs TO the resource
	// ("Updated a firm provider data" vs "Refreshed a firm's provider data"). The derivation cannot
	// know which qualifiers are possessive, so these are stated.
	'account.beneficiary_added': 'Nominated a beneficiary on an account',
	'account.beneficiary_removed': 'Withdrew a beneficiary nomination',
	'account.model_updated': 'Set an account’s model',
	'account.regular_movements_updated': 'Updated an account’s regular payments and withdrawals',
	// The ACCOUNT's own, and its own sentence: an account's bank details and the client's are
	// different facts that resolve in that order (account first, client second), so an audit
	// reader has to be able to tell which one a withdrawal was paid from.
	'account.bank_accounts_updated': 'Updated an account’s bank details',
	'client.bank_account_updated': 'Updated a client’s bank details',
	'client.identity_images_viewed': 'Viewed a client’s identity photos',
	'client.compliance_report_downloaded': 'Downloaded a client’s compliance report',
	'review.requested': 'Asked for a peer review',
	'review.approved': 'Approved a peer review',
	'review.rejected': 'Rejected a peer review',
	'review.cancelled': 'Cancelled a peer review',
	'review.conflicted': 'A peer review conflicted',
	'review.policy_updated': 'Updated a peer-review policy',
	// "Remind" is not a verb the derivation knows, and the thing to say is who was reminded.
	'action_run.reminded': 'Reminded the approvers of a held run',
	'client.vulnerability_updated': 'Updated a client’s vulnerability',
	'client.fatca_document_uploaded': 'Uploaded a client’s signed FATCA form',
	'client.portal_invited': 'Invited a client to the portal',
	// A member of staff looking at the client portal AS the client (read-only, with a reason on
	// the session row). "Viewed as" is not a verb the derivation knows, and the thing to say is
	// whose screen it was.
	'client.portal_viewed_as': 'Opened a client’s portal as them',
	'client.portal_view_ended': 'Left a client’s portal',
	// Derives as "Book pinned a client book pinned" — the resource and the verb are the same two
	// words. The act is overriding the matching rules for one person, which is what a file review
	// is looking for, so the label says pinned rather than just "set".
	'client.book_pinned': 'Pinned a client to a book',
	// Derives as "Set a client groups" — the verb's qualifier is read as part of the noun.
	'client.groups_set': 'Set a client’s groups',
	'firm.provider_data_refreshed': 'Refreshed a firm’s provider data',
	'firm.provider_updated': 'Updated a firm’s provider',
	// Derives as "Updated a member provider ref", which reads as machinery. The act is naming the
	// person to a custodian, and the provider itself is on the row as a path param.
	'member.provider_ref_updated': 'Set a member’s custodian id',
	// Derives as "Updated a member profile", which is not wrong but says less than it could:
	// the act is a firm stating who somebody is — staff or client, adviser or not — alongside
	// their name and the profile a client sees on the adviser card.
	'member.profile_updated': 'Updated what a firm says about a member',
	'firm.storage_provisioned': 'Provisioned a firm’s storage',
	'firm.usage_refreshed': 'Refreshed a firm’s usage',
	'firm.ai_settings_updated': 'Updated a firm’s AI settings',
	// Derives as "Rendered an export PDF", which describes the machinery rather than the act. What
	// a reader of a firm's trail wants to know is that a listing left the platform as a file.
	'export.pdf_rendered': 'Exported a listing as PDF',
	'branding.updated': 'Updated the branding',
	'automation.reordered': 'Reordered the automations',
	'illustration.investment_created': 'Created an investment illustration',
	'illustration.model_facts_updated': 'Updated a model’s illustration facts',
	// The derivation reads "io" as its own word ("linked to io an integration..."), not the
	// Intelliflo Office abbreviation it actually is.
	'integration.client_account_linked_to_io': 'Linked a client’s account to Intelliflo Office',
	'integration.client_linked_to_io': 'Linked a client to Intelliflo Office',
	'integration.account_linked_to_io_plan': 'Linked an account to an Intelliflo Office plan',
	'integration.account_unlinked_from_io': 'Unlinked an account from Intelliflo Office',
	'integration.client_unlinked_from_io': 'Unlinked a client from Intelliflo Office',
	'client.linked_to_io': 'Linked a client to Intelliflo Office',
	'account.linked_to_io': 'Linked an account to Intelliflo Office',
	'mi_report.switched': 'Switched a management report',
	// `actioned` is not a past-tense verb the map knows, so the derivation would repeat it.
	'journey.step_actioned': 'Actioned a journey step',
	'flow.validated': 'Validated a journey definition',
	// THE DERIVATION CANNOT REACH THESE, and each fails it in a different way — which is why the
	// test beside this table scans every declared type rather than trusting the list to be complete.
	//
	//   * the verb is not the LAST word (`valuation_filed_at_morgan_lloyd`, `cancelled_at_…`), so
	//     the tail is a proper noun and nothing reads as an act;
	//   * the verb is not one the vocabulary knows (`reseeded`, `resumed`, `due_soon`), so the whole
	//     name is used as both the verb AND the qualifier — "Reseeded a flow reseeded";
	//   * the qualifier is a determiner rather than a noun (`all_dismissed`), giving "…a
	//     notification all".
	'account.valuation_filed_at_morgan_lloyd': 'Filed an account valuation with Morgan Lloyd',
	'transfer.cancelled_at_morgan_lloyd': 'Cancelled a transfer at Morgan Lloyd',
	'transfer.cancelled_at_seccl': 'Cancelled a transfer at Seccl',
	'transfer.recorded_at_morgan_lloyd': 'Recorded a transfer at Morgan Lloyd',
	'transfer.recorded_at_seccl': 'Recorded a transfer at Seccl',
	'transfer.recorded_at_sei': 'Recorded a transfer at SEI',
	'transfer.refreshed_from_morgan_lloyd': 'Refreshed a transfer from Morgan Lloyd',
	'transfer.refreshed_from_seccl': 'Refreshed a transfer from Seccl',
	'transfer.refreshed_from_sei': 'Refreshed a transfer from SEI',
	'transfer.note_updated': 'Updated a transfer note',
	'firm.provider_refresh_resumed': 'Resumed a firm’s provider refresh',
	'flow.reseeded': 'Reseeded a journey definition',
	// Clock-driven, so it names no actor and reads as a statement rather than an act — the only
	// sentence in this table that is not something somebody DID.
	'task.due_soon': 'A task is due soon',
	'notification.all_dismissed': 'Dismissed every notification',
	'notification.all_read': 'Marked every notification read',
	'notification.read': 'Marked a notification read',
	'notification.dismissed': 'Dismissed a notification',
	'notification.stream_opened': 'Opened a live connection',
	'message.stream_opened': 'Opened a live connection',
	'contribution.recorded_at_morgan_lloyd': 'Recorded a contribution at Morgan Lloyd',
	'contribution.refreshed_from_morgan_lloyd': 'Refreshed a contribution from Morgan Lloyd',
	'contribution.stopped_at_morgan_lloyd': 'Stopped a contribution at Morgan Lloyd',
	'client.pushed_to_morgan_lloyd': 'Pushed a client to Morgan Lloyd',
	'client.synced_to_morgan_lloyd': 'Synced a client to Morgan Lloyd',
	'account.synced_to_morgan_lloyd': 'Pushed an account to Morgan Lloyd',
	'firm.storage_provisioned_for_all': 'Provisioned storage for every firm',
	'platform.all_firms_refreshed': 'Refreshed every firm’s provider data',
	'storage_credential.issued_own': 'Issued their own storage credentials',
	'storage_credential.issued_for_member': 'Issued storage credentials for a member',
	'flow.simulated': 'Simulated a journey (a dry run)',
	'automation.tested': 'Tested an automation (a dry run)',
	'journey.terms_accepted': 'Accepted a declaration',
	'journey.identity_handed_to_staff': 'Asked for their identity to be checked by a person',
	'journey.identity_taken_by_adviser': 'Took an identity check onto documents',
	'journey.identity_phone_link_created': 'Made a phone link for their identity photos',
	'journey.identity_phone_link_withdrawn': 'Withdrew the phone link for their identity photos',
	'journey.identity_approved': 'Approved an identity check held for review',
	'journey.identity_images_viewed': 'Viewed a client’s identity photos',
	'email_template.test_sent': 'Sent a test email',
	'email_template.image_added': 'Added an image to the email library',
	'email_template.defaults_loaded': 'Loaded the built-in email templates',
	'email_template.preset_provisioned': 'Provisioned the email-template preset',
	'notification_rule.defaults_loaded': 'Loaded the default notification rules',
	'notification.settings_updated': 'Updated the notification settings',
	'notification.preferences_updated': 'Updated their notification preferences',
	'notification.client_preferences_updated': 'Updated a client’s notification preferences',
	'automation.platform_pause_updated': 'Set the platform automation pause',
	'environment.registry_refreshed': 'Refreshed the environment registry',
	'terminology.updated': 'Renamed a term',
	'form.response_submitted': 'Submitted a form',
	'document.type_classified': 'Classified a document’s type',
	'aml.sync_ran': 'Ran an AML sync',
	'aml.monitoring_updated': 'Updated AML monitoring',
	'aml.check_rerun': 'Ran an AML check again',
	'aml.photos_requested': 'Asked a client for new identity photos',
	'aml.report_downloaded': 'Downloaded a compliance report',
	'aml.identity_photos_uploaded': 'Uploaded identity photos for a client',
	'aml.journey_allowed': 'Allowed a client’s journey to continue past an AML match',
	'aml.client_note_added': 'Added an internal note on a client',
	'platform.queried': 'Ran a warehouse query',
	'platform.sync_started': 'Started a provider sync',
	'platform.rebuilt': 'Rebuilt a provider warehouse',
	'platform.action_ran': 'Ran a provider action',
	'platform.provider_source_updated': 'Set a provider source',
	'content.created': 'Created a content item',
	'content.updated': 'Updated a content item',
	'content.published': 'Published a content item',
	'content.archived': 'Archived a content item',
	'content.guide_uploaded': 'Uploaded a PDF guide',
	'content.hero_uploaded': 'Uploaded a content hero image',
	'content.image_added': 'Added an image to a content item'
};

/**
 * The entity kind that IS the subject of an event on this resource — the thing the verb acted on.
 *
 * Mostly the resource itself, which is the point of naming events after resources; `client`/`member`/
 * `firm` differ only because the read side's kinds are the SCHEMA's words (contact/user/tenant).
 *
 * MEMBERSHIP IS NARROW ON PURPOSE — the entity must be the thing the verb ACTED ON, not merely a thing
 * the row mentions. The near misses are what make that rule worth stating:
 *
 *   - `document_share`, `document_request`: their resolvable entity is the DOCUMENT, so substituting
 *     would say "Created the 2026 suitability report" for creating a share OF it — a claim about a
 *     different act.
 *   - `conversation`, `message`: a conversation resolves to the CLIENT on it, so "Read a conversation"
 *     would become "Read Margaret Cole". Messaging keeps its own builders, which put the person where
 *     a person belongs ("Sent a message to Margaret Cole").
 *
 * A resource is also absent when the read side cannot resolve a label at all
 * (`admin._ENTITY_RESOLVERS` is the list that can) — an email template, a fee package, a provider.
 * Those keep the plain sentence rather than gaining a chip-shaped gap.
 */
const RESOURCE_ENTITY_KIND: Record<string, string> = {
	document: 'document',
	template: 'template',
	form: 'form',
	flow: 'flow',
	task: 'task',
	instruction: 'instruction',
	client: 'contact',
	account: 'account',
	model: 'model',
	member: 'user',
	firm: 'tenant',
	// Added by the September 2026 event-content review. Each of these has a resolver now, so a
	// single-verb sentence can name the record instead of describing its type: "Cancelled a
	// contribution" becomes "Cancelled Margaret Cole - GBP 500.00".
	automation: 'automation',
	content: 'content',
	contribution: 'contribution',
	transfer: 'transfer',
	dataset: 'dataset',
	document_request: 'document_request',
	document_type: 'document_type',
	email_template: 'email_template',
	journey: 'journey',
	message_group: 'message_group',
	file: 'file',
	beneficiary: 'beneficiary',
	product: 'product',
	fee_package: 'fee_package',
	provider: 'catalogue_provider',
	notification_rule: 'notification_rule'
};

/**
 * The entity kind an event is ABOUT — the read side's word for the resource in `resource.verb`
 * (`document.viewed` → `document`, `client.updated` → `contact`). Null when the resource has no
 * resolvable entity kind, which is the same rule `namedSubject` runs on.
 *
 * Exported for the detail pane's "go to the record" button: of the entities on a row, the SUBJECT
 * is the one a reader means by "the relevant item", so it is offered first.
 */
export function subjectEntityKind(eventType: string): string | null {
	const [resource = ''] = eventType.split('.');
	return RESOURCE_ENTITY_KIND[resource] ?? null;
}

/**
 * Name the SUBJECT instead of describing its type: "Viewed the 2026 suitability report" rather than
 * "Viewed a document".
 *
 * This is the whole value of the trail at a glance. "Viewed a document" answers a question nobody
 * asked — of course it was a document, the event type says so — while the document's NAME is the fact
 * a reader is scanning for, and the one that makes two adjacent rows different from each other.
 *
 * Applied ONLY when the sentence is exactly "{verb} a {resource}", i.e. the verb carries no qualifier.
 * With one, the noun phrase is about a PART of the subject ("Published an email template version",
 * "Updated a client's bank details") and substituting the subject's name would either lose the part or
 * read as its name ("Published Annual review version"). Those keep their derived sentence; several
 * have hand-written ones anyway.
 *
 * Returns null when there is nothing to name — no resolvable kind for the resource, no entity of that
 * kind on the row, or no label for it (a deleted record resolves to a null label, and the raw id is
 * the honest answer there, which the chips below the sentence already show).
 */
function namedSubject(eventType: string, entities: ActionEntity[]): string | null {
	const [resource = '', verb = ''] = eventType.split('.');
	const verbWords = verb.split('_').filter(Boolean);
	// A qualifier means the sentence is not about the subject as a whole — see above.
	if (verbWords.length !== 1) return null;
	const past = verbWords[0] ?? '';
	if (!PAST_VERBS.has(past)) return null;
	const kind = RESOURCE_ENTITY_KIND[resource];
	if (!kind) return null;
	const subject = labelled(entities, kind);
	if (!subject) return null;
	return `${past.charAt(0).toUpperCase() + past.slice(1)} ${subject.label}`;
}

/** Is this a declared `resource.verb` name, rather than a pre-rename `action.…` one? */
function isDeclaredName(eventType: string): boolean {
	return !eventType.startsWith('action.') && eventType.includes('.');
}

/**
 * The sentence for a declared `resource.verb` name.
 *
 * Mechanical, and it can be: the name already says the resource and the verb, which is the whole
 * reason for renaming. The verb's LAST word is the past-tense verb and anything before it qualifies
 * the resource — so `email_template.version_published` is "Published" + "an email template version".
 * A verb the set does not know is used whole ("Set" for `firm.provider_updated` would be wrong, so
 * `updated` is in the set and `provider` qualifies).
 */
/** Swap a POSSESSIVE PLACEHOLDER in a hand-written label for the record it is about.
 *
 *  21 of the overrides read "…a client’s bank details", "…an account’s model". The placeholder is
 *  there because the label is one fixed string and cannot know whose. But the row DOES know - the
 *  contact or the account is resolved and sitting right there as a chip - so the sentence can name
 *  it: "Updated Daniel Whitlock’s bank details".
 *
 *  `namedSubject` cannot do this: it only fires on the exact shape "{verb} a {resource}", so every
 *  label with a possessive in the middle of it kept saying "a client". Nothing changes when the
 *  entity did not resolve - the placeholder is still the honest answer then. */
const POSSESSIVE_PLACEHOLDERS: ReadonlyArray<[string, string]> = [
	['a client’s', 'contact'],
	['an account’s', 'account'],
	['a member’s', 'user']
];

function namePossessive(label: string, entities: ActionEntity[]): string {
	for (const [placeholder, kind] of POSSESSIVE_PLACEHOLDERS) {
		if (!label.includes(placeholder)) continue;
		const named = labelled(entities, kind);
		if (named?.label) return label.replace(placeholder, `${named.label}’s`);
	}
	return label;
}

function declaredLabel(eventType: string): string {
	const override = LABEL_OVERRIDES[eventType];
	if (override) return override;
	const [resource = '', verb = ''] = eventType.split('.');
	const verbWords = verb.split('_').filter(Boolean);
	const tail = verbWords[verbWords.length - 1] ?? '';
	const isPast = PAST_VERBS.has(tail);
	const qualifier = isPast ? verbWords.slice(0, -1) : verbWords;
	const nounWords = [...resource.split('_'), ...qualifier]
		.filter(Boolean)
		.map((w) => WORD_CASING[w] ?? NOUNS[w] ?? w);
	const verbWord = isPast ? tail : verbWords.join(' ');
	const noun = nounWords.join(' ');
	const verbLabel = verbWord.charAt(0).toUpperCase() + verbWord.slice(1);
	return noun ? `${verbLabel} ${withArticle(noun)}` : verbLabel;
}

/**
 * The category a RESOURCE belongs to — the primary path for `eventCategory`.
 *
 * A declared name says what it is about in its first segment, so the category is a lookup rather than
 * the word-matching heuristic the old tag-and-hyphen names needed. That heuristic stays for legacy
 * rows, where there is no resource to read.
 */
const RESOURCE_CATEGORY: Record<string, ActionCategory> = {
	document: 'documents',
	document_type: 'documents',
	document_request: 'documents',
	document_share: 'documents',
	template: 'documents',
	email_template: 'documents',
	content: 'documents',
	content_category: 'documents',
	form: 'documents',
	dataset: 'documents',
	file: 'documents',
	// A listing rendered to a file, alongside the other things that produce paperwork.
	export: 'documents',
	illustration: 'documents',
	client: 'people',
	client_group: 'settings',
	member: 'people',
	conversation: 'people',
	message: 'people',
	message_group: 'people',
	role: 'roles',
	account: 'money',
	instruction: 'money',
	contribution: 'money',
	transfer: 'money',
	product: 'money',
	model: 'money',
	fee_package: 'money',
	provider: 'money',
	task: 'tasks',
	journey: 'flows',
	flow: 'flows',
	automation: 'flows',
	aml: 'screening',
	authentication: 'security',
	storage_credential: 'security',
	firm: 'settings',
	branding: 'settings',
	email: 'settings',
	notification: 'settings',
	notification_rule: 'settings',
	environment: 'settings',
	platform: 'settings',
	terminology: 'settings'
};

/**
 * Every pre-rename event name, mapped to the declared one it became.
 *
 * `audit.actions` is APPEND-ONLY: a firm's trail, a client's document rail and a case's history are
 * mostly rows written before the `resource.verb` rename, and they keep the string they were written
 * with. Without this they would render as raw ids sitting among sentences — the single most visible
 * way a rename goes wrong, and one no test of today's events would catch.
 *
 * Keyed WITHOUT the `action.` prefix, because that is how the operation-id tables below are keyed and
 * `describeActionEvent` strips it before looking anything up.
 *
 * This map can only shrink, never grow: `test_actions_tripwire.py` refuses a route with no declared
 * name, so no new legacy spelling can be produced.
 */
const LEGACY_EVENT_TYPES: Record<string, string> = {
	'admin-admin-create-email-template': 'email_template.created',
	'admin-admin-delete-email-template': 'email_template.deleted',
	'admin-admin-archive-email-template': 'email_template.archived',
	'admin-admin-unarchive-email-template': 'email_template.unarchived',
	'admin-admin-email-templates-load-defaults': 'email_template.defaults_loaded',
	'admin-admin-email-templates-provision': 'email_template.preset_provisioned',
	'admin-admin-preview-email-template': 'email_template.previewed',
	'admin-admin-publish-email-template-version': 'email_template.version_published',
	'admin-admin-restore-email-template-version': 'email_template.version_restored',
	'admin-admin-save-email-template-version': 'email_template.version_saved',
	'admin-admin-send-test-email-template': 'email_template.test_sent',
	'admin-admin-update-email-template': 'email_template.updated',
	'admin-create-notification-rule': 'notification_rule.created',
	'admin-create-tenant': 'firm.created',
	'admin-create-tenant-member': 'member.created',
	'admin-create-tenant-role': 'role.created',
	'admin-deactivate-tenant-member': 'member.deactivated',
	'admin-delete-notification-rule': 'notification_rule.deleted',
	'admin-delete-tenant': 'firm.deleted',
	'admin-grant-member-portal': 'member.portal_granted',
	'admin-load-notification-defaults': 'notification_rule.defaults_loaded',
	'admin-platform-query': 'platform.queried',
	'admin-platform-rebuild': 'platform.rebuilt',
	'admin-platform-sync': 'platform.sync_started',
	'admin-provision-all-tenant-storage': 'firm.storage_provisioned_for_all',
	'admin-provision-tenant-storage': 'firm.storage_provisioned',
	'admin-reactivate-tenant-member': 'member.reactivated',
	'admin-refresh-environment-registry': 'environment.registry_refreshed',
	'admin-refresh-platform-tenants': 'platform.all_firms_refreshed',
	'admin-refresh-tenant-provider': 'firm.provider_data_refreshed',
	'admin-refresh-tenant-usage': 'firm.usage_refreshed',
	'admin-register-environment': 'environment.registered',
	'admin-resend-tenant-member-invite': 'member.invite_resent',
	'admin-revoke-tenant-member-invitation': 'member.invitation_revoked',
	'admin-reset-tenant-member-mfa': 'member.mfa_reset',
	'admin-reveal-tenant-member-invitation': 'member.invitation_revealed',
	'admin-revoke-member-portal': 'member.portal_revoked',
	'admin-run-platform-action': 'platform.action_ran',
	'admin-set-automation-platform-pause': 'automation.platform_pause_updated',
	'admin-set-branding': 'branding.updated',
	'admin-set-environment-secrets': 'environment.secrets_updated',
	'admin-set-member-portal-role': 'member.portal_role_updated',
	'admin-set-notification-config': 'notification.settings_updated',
	'admin-set-provider-source': 'platform.provider_source_updated',
	'admin-set-tenant-ai': 'firm.ai_settings_updated',
	'admin-set-tenant-provider': 'firm.provider_updated',
	'admin-set-tenant-role-permissions': 'role.permissions_updated',
	'admin-sync-tenant': 'firm.synced',
	'admin-sync-tenant-roles': 'firm.roles_synced',
	'admin-update-notification-rule': 'notification_rule.updated',
	'admin-update-tenant': 'firm.updated',
	'admin-upload-branding-asset': 'branding.asset_uploaded',
	'aml-connect-aml-operator': 'aml.operator_connected',
	'aml-decide-aml-hit': 'aml.hit_decided',
	'aml-disconnect-aml-operator': 'aml.operator_disconnected',
	'aml-link-aml-profile': 'aml.profile_linked',
	'aml-run-aml-screen': 'aml.screen_ran',
	'aml-run-aml-sync': 'aml.sync_ran',
	'aml-set-aml-monitoring': 'aml.monitoring_updated',
	'applications-acknowledge-application-terms': 'journey.terms_accepted',
	'applications-my-identity-manual': 'journey.identity_handed_to_staff',
	'applications-take-identity-manual': 'journey.identity_taken_by_adviser',
	'applications-action-application-step': 'journey.step_actioned',
	'applications-assign-application': 'journey.assigned',
	'applications-cancel-application': 'journey.cancelled',
	'applications-create-application': 'journey.created',
	'applications-retry-application-step': 'journey.step_retried',
	'applications-start-application': 'journey.started',
	'applications-submit-application-step-form': 'journey.step_form_submitted',
	'auth-activated': 'authentication.account_activated',
	'auth-login': 'authentication.succeeded',
	'auth-login-failed': 'authentication.failed',
	'auth-logout': 'authentication.signed_out',
	'auth-mfa-enrolled': 'authentication.mfa_enrolled',
	'auth-mfa-removed': 'authentication.mfa_removed',
	'automations-create-automation': 'automation.created',
	'automations-delete-automation': 'automation.deleted',
	'automations-disable-automation': 'automation.disabled',
	'automations-enable-automation': 'automation.enabled',
	'automations-reorder-automations': 'automation.reordered',
	'automations-set-automation-settings': 'automation.settings_updated',
	'automations-test-automation': 'automation.tested',
	'automations-update-automation': 'automation.updated',
	'catalogue-create-commentary': 'model.commentary_created',
	'catalogue-create-fee-package': 'fee_package.created',
	'catalogue-create-model': 'model.created',
	'catalogue-create-model-version': 'model.version_created',
	'catalogue-create-product': 'product.created',
	'catalogue-create-provider': 'provider.created',
	'catalogue-publish-model-version': 'model.version_published',
	'catalogue-set-product-platform': 'product.platform_reference_updated',
	'catalogue-set-term': 'terminology.updated',
	'catalogue-update-commentary': 'model.commentary_updated',
	'catalogue-update-fee-package': 'fee_package.updated',
	'catalogue-update-model': 'model.updated',
	'catalogue-update-product': 'product.updated',
	'catalogue-update-provider': 'provider.updated',
	'contributions-attach-contribution-transaction': 'contribution.transaction_attached',
	'contributions-cancel-contribution': 'contribution.cancelled',
	'contributions-categorise-contribution': 'contribution.categorised',
	'contributions-classify-transaction': 'contribution.transaction_classified',
	'contributions-create-contribution': 'contribution.created',
	'contributions-detach-contribution-transaction': 'contribution.transaction_detached',
	'contributions-lodge-contribution-expectation': 'contribution.expectation_lodged',
	'contributions-record-contribution-to-morgan-lloyd': 'contribution.recorded_at_morgan_lloyd',
	'contributions-refresh-contribution-from-morgan-lloyd':
		'contribution.refreshed_from_morgan_lloyd',
	'contributions-run-contribution-matcher': 'contribution.matcher_ran',
	'contributions-set-contribution-status': 'contribution.status_updated',
	'contributions-stop-contribution-at-morgan-lloyd': 'contribution.stopped_at_morgan_lloyd',
	'contributions-update-contribution-application': 'contribution.application_updated',
	'crm-account-drawdown-session': 'account.drawdown_session_started',
	'crm-create-illustration': 'illustration.created',
	'crm-create-investment-illustration': 'illustration.investment_created',
	'crm-push-contact-to-morgan-lloyd': 'client.pushed_to_morgan_lloyd',
	// A nomination names a THIRD PARTY and a share of the fund, so who added or withdrew one is a
	// question a file review asks by name.
	'crm-add-account-beneficiary': 'account.beneficiary_added',
	'crm-remove-account-beneficiary': 'account.beneficiary_removed',
	'crm-set-account-model': 'account.model_updated',
	'crm-set-account-bank-accounts': 'account.bank_accounts_updated',
	'crm-set-contact-bank-account': 'client.bank_account_updated',
	'crm-set-contact-vulnerability': 'client.vulnerability_updated',
	'crm-upload-contact-fatca-document': 'client.fatca_document_uploaded',
	'crm-sync-contact-to-morgan-lloyd': 'client.synced_to_morgan_lloyd',
	'datasets-add-dataset-version': 'dataset.version_added',
	'datasets-create-dataset': 'dataset.created',
	'datasets-delete-dataset': 'dataset.deleted',
	'datasets-update-dataset': 'dataset.updated',
	'documents-archive-document': 'document.archived',
	'documents-cancel-document-request': 'document_request.cancelled',
	'documents-classify-document-type': 'document.type_classified',
	'documents-create-document-request': 'document_request.created',
	'documents-create-document-type': 'document_type.created',
	'documents-fulfill-request-item': 'document_request.item_fulfilled',
	'documents-generate-document': 'document.generated',
	'documents-reassign-request-item': 'document_request.item_reassigned',
	'documents-render-document': 'document.rendered',
	'documents-retry-document': 'document.render_retried',
	'documents-send-document': 'document.sent',
	'documents-submit-request-form-response': 'document_request.form_submitted',
	'documents-unarchive-document': 'document.unarchived',
	'documents-update-document': 'document.updated',
	'documents-update-document-request': 'document_request.updated',
	'documents-update-document-type': 'document_type.updated',
	'documents-upload-document': 'document.uploaded',
	'documents-view-document': 'document.viewed',
	'email-templates-create-email-template': 'email_template.created',
	'email-templates-delete-email-template': 'email_template.deleted',
	// Retired with archiving itself, and kept so the rows those routes wrote still read as
	// words rather than as raw ids (see app/audit_actions.py::EVENT_TYPES).
	'email-templates-archive-email-template': 'email_template.archived',
	'email-templates-unarchive-email-template': 'email_template.unarchived',
	'email-templates-preview-email-template': 'email_template.previewed',
	'email-templates-publish-email-template-version': 'email_template.version_published',
	'email-templates-restore-email-template-version': 'email_template.version_restored',
	'email-templates-save-email-template-version': 'email_template.version_saved',
	'email-templates-send-test-email-template': 'email_template.test_sent',
	'email-templates-update-email-template': 'email_template.updated',
	'files-delete-file': 'file.deleted',
	'files-upload-file': 'file.uploaded',
	'firm-add-contact-note': 'client.note_added',
	'firm-delete-contact-note': 'client.note_deleted',
	'firm-archive-contact': 'client.archived',
	'firm-assign-adviser': 'client.adviser_assigned',
	'firm-cancel-instruction': 'instruction.cancelled',
	'firm-create-contact': 'client.created',
	'firm-create-instruction': 'instruction.created',
	'firm-create-role': 'role.created',
	'firm-deactivate-member': 'member.deactivated',
	'firm-firm-create-notification-rule': 'notification_rule.created',
	'firm-firm-delete-notification-rule': 'notification_rule.deleted',
	'firm-firm-update-notification-rule': 'notification_rule.updated',
	'firm-grant-portal': 'member.portal_granted',
	'firm-invite-contact-to-portal': 'client.portal_invited',
	'firm-invite-member': 'member.invited',
	'firm-link-contact-user': 'client.portal_user_linked',
	'firm-reactivate-member': 'member.reactivated',
	'firm-resend-invite': 'member.invite_resent',
	'firm-revoke-invitation': 'member.invitation_revoked',
	'firm-restore-contact': 'client.restored',
	'firm-revoke-portal': 'member.portal_revoked',
	'firm-set-branding': 'branding.updated',
	'firm-set-portal-role': 'member.portal_role_updated',
	'firm-set-role-permissions': 'role.permissions_updated',
	'reviews-approve-review': 'review.approved',
	'reviews-reject-review': 'review.rejected',
	'reviews-cancel-review': 'review.cancelled',
	'reviews-set-review-policy': 'review.policy_updated',
	'firm-unassign-adviser': 'client.adviser_unassigned',
	'firm-unlink-contact-user': 'client.portal_user_unlinked',
	'firm-update-contact': 'client.updated',
	'email-templates-upload-email-template-image': 'email_template.image_added',
	'firm-upload-branding-asset': 'branding.asset_uploaded',
	'flows-create-flow': 'flow.created',
	'flows-publish-flow-version': 'flow.version_published',
	'flows-replay-dead-letter': 'flow.dead_letter_replayed',
	'flows-save-flow-version': 'flow.version_saved',
	'flows-simulate-flow': 'flow.simulated',
	'flows-update-flow': 'flow.updated',
	'flows-validate-flow-definition': 'flow.validated',
	'forms-create-form': 'form.created',
	'forms-create-form-response': 'form.response_submitted',
	'forms-delete-form': 'form.deleted',
	'forms-publish-form': 'form.published',
	'forms-set-form-fields': 'form.fields_updated',
	'forms-unarchive-form': 'form.unarchived',
	'forms-update-form': 'form.updated',
	'messaging-messaging-conversation-read': 'conversation.read',
	'messaging-messaging-conversation-send': 'message.sent',
	'messaging-messaging-conversation-send-attachment': 'message.attachment_sent',
	'messaging-messaging-conversations-open': 'conversation.opened',
	'messaging-messaging-group-create-request': 'message_group.document_requested',
	'messaging-messaging-group-send': 'message_group.message_sent',
	'messaging-messaging-group-send-attachment': 'message_group.attachment_sent',
	'messaging-messaging-groups-create': 'message_group.created',
	'messaging-messaging-thread-read': 'conversation.read',
	'messaging-messaging-thread-send': 'message.sent',
	'messaging-messaging-thread-send-attachment': 'message.attachment_sent',
	'messaging-messaging-ws-ticket': 'message.stream_opened',
	'notifications-dismiss-all-notifications': 'notification.all_dismissed',
	'notifications-dismiss-notification': 'notification.dismissed',
	'notifications-mark-all-notifications-read': 'notification.all_read',
	'notifications-mark-notification-read': 'notification.read',
	'notifications-set-notification-client-preferences': 'notification.client_preferences_updated',
	'notifications-set-notification-preferences': 'notification.preferences_updated',
	'notifications-ws-ticket': 'notification.stream_opened',
	'shares-access-document-share': 'document_share.accessed',
	'shares-create-document-share': 'document_share.created',
	'shares-revoke-share': 'document_share.revoked',
	'storage-issue-member-storage-credentials': 'storage_credential.issued_for_member',
	'storage-issue-own-storage-credentials': 'storage_credential.issued_own',
	'storage-revoke-storage-credentials': 'storage_credential.revoked',
	'tasks-complete-task': 'task.completed',
	'tasks-create-task': 'task.created',
	'tasks-update-task': 'task.updated',
	'templates-create-template': 'template.created',
	'templates-delete-template': 'template.deleted',
	'templates-duplicate-template': 'template.duplicated',
	'templates-publish-template-version': 'template.version_published',
	'templates-render-template-preview': 'template.preview_rendered',
	'templates-render-template-preview-pdf': 'template.preview_pdf_rendered',
	'templates-save-template-version': 'template.version_saved',
	'templates-unarchive-template': 'template.unarchived',
	'templates-update-template': 'template.updated',
	'transfers-attach-transfer-transaction': 'transfer.transaction_attached',
	'transfers-create-transfer': 'transfer.created',
	'transfers-detach-transfer-transaction': 'transfer.transaction_detached',
	'transfers-set-transfer-status': 'transfer.status_updated'
};

/**
 * Give every enrichment builder and fixed auth label its LEGACY name too.
 *
 * Done as a loop rather than a second key per entry: a builder is a multi-line function and pairs of
 * keys around one would be four lines of noise per event, with the two spellings free to drift. Here
 * the aliasing is one rule stated once, and adding a builder gets it for free.
 *
 * A legacy name that ALREADY has its own entry is left alone — an event whose meaning genuinely
 * changed with its name would say so there.
 */
for (const [legacy, declared] of Object.entries(LEGACY_EVENT_TYPES)) {
	const builder = ENRICHED[declared];
	if (builder && !(legacy in ENRICHED)) ENRICHED[legacy] = builder;
	const label = AUTH_LABELS[declared];
	if (label && !(legacy in AUTH_LABELS)) AUTH_LABELS[legacy] = label;
}

export function describeActionEvent(
	eventType: string,
	pathParams: Record<string, unknown> = {},
	entities: ActionEntity[] | null = null,
	metadata: ActionEvent['metadata'] | null = null
): string {
	// The base sentence first, then WHICH fields the request carried — appended rather than woven
	// in, so it applies to every path below (curated, named-subject, declared and de-slugged alike)
	// and can only ever ADD to the reading. A row with no `changed` capture is untouched.
	return baseSentence(eventType, pathParams, entities, metadata) + changedClause(metadata);
}

function baseSentence(
	eventType: string,
	pathParams: Record<string, unknown>,
	entities: ActionEntity[] | null,
	metadata: ActionEvent['metadata'] | null
): string {
	const opId = eventType.startsWith('action.') ? eventType.slice('action.'.length) : eventType;

	// Auth events are synthetic with a fixed label (no entities to weave in) — a straight lookup.
	const authLabel = AUTH_LABELS[opId];
	if (authLabel) return authLabel;

	const builder = ENRICHED[opId];
	if (builder) {
		const subject = metadata?.subject;
		const previous = metadata?.previous;
		const enriched = builder({
			entities: entities ?? [],
			pathParams,
			subject: subject && typeof subject === 'object' ? subject : {},
			previous: previous && typeof previous === 'object' ? previous : {}
		});
		if (enriched) return enriched;
	}

	// Written out where the grammar cannot reach it - after enrichment, so a real subject name wins.
	// A DECLARED name carries its own resource and verb, so it needs no table (see `declaredLabel`) —
	// but where the row also carries the SUBJECT, name it rather than describing its type.
	// Below this line is the LEGACY path: the pre-rename `action.{tag}-{name}` shape, kept because
	// `audit.actions` is append-only and a firm's trail is mostly rows written before the rename.
	if (isDeclaredName(eventType)) {
		// A RESOLVED subject name beats the hand-written label, and that order is the September 2026
		// review's correction: an override used to suppress naming entirely, so `content.published`
		// read "Published a content item" with the item's own title sitting resolved on the row. The
		// override exists because the DERIVED grammar reads badly ("Published a content"), which was
		// never an argument for preferring a category noun to the record's actual name. `namedSubject`
		// returns null unless the sentence is exactly "{verb} a {resource}", so the override still
		// owns every row it was written for.
		const named = namedSubject(eventType, entities ?? []);
		if (named) return named;
		return namePossessive(declaredLabel(eventType), entities ?? []);
	}

	// A legacy row can be named too: the alias map says which declared event it is, and that name
	// carries the resource. Without this a firm's trail — which is mostly history — would keep the
	// type-shaped sentence for ever while new rows named their subject, which reads as two different
	// products rather than one rename.
	const declaredEquivalent = LEGACY_EVENT_TYPES[opId];
	if (declaredEquivalent && !LABEL_OVERRIDES[declaredEquivalent]) {
		const named = namedSubject(declaredEquivalent, entities ?? []);
		if (named) return named;
	}

	const curated = OPERATION_LABELS[opId];
	if (curated) return curated;

	if (!opId) return eventType;
	const rest = stripRouterTag(opId).split('-').filter(Boolean);
	if (rest.length === 0) return eventType;

	const verb = VERBS[rest[0] ?? ''];
	let nounWords = (verb ? rest.slice(1) : rest).map((w) => WORD_CASING[w] ?? NOUNS[w] ?? w);

	const portal = pathParams['portal'];
	if (typeof portal === 'string' && portal && nounWords.includes('portal')) {
		nounWords = nounWords.map((w) => (w === 'portal' ? `${portal} portal` : w));
	}

	const noun = nounWords.join(' ');
	if (verb && noun) return `${verb} ${withArticle(noun)}`;
	if (verb) return verb;
	// No curated verb: a readable de-slugged phrase, capitalised ("Transition step"). `slice(1)`
	// keeps whatever casing `WORD_CASING` set, so "ws ticket" capitalises but "AML hit" is not lowered.
	return noun.charAt(0).toUpperCase() + noun.slice(1);
}

/** The actor's best display name: person name → email → the raw WorkOS subject. */
export function actorLabel(event: Pick<ActionEvent, 'actor_name' | 'actor_email' | 'actor_sub'>) {
	return event.actor_name || event.actor_email || event.actor_sub;
}

/* Every sentence describeActionEvent can lead with — the curated past-tense verbs plus the two
   the enriched builders mint directly. Lets actorLedSentence know when lower-casing the verb
   yields grammatical prose ("Alice changed …") versus when to fall back to a separator. */
const VERB_LED = new Set([
	...Object.values(VERBS),
	'Changed',
	'Cleared',
	// Verbs only the ENRICHED builders mint, so they are absent from the de-slug VERBS table. Each
	// must be listed or its sentence silently falls back to the "Actor · Sentence" separator form
	// and reads as a fragment rather than prose.
	'Filed',
	'Restored',
	'Booked',
	'Opened'
]);

/**
 * The Discord-audit-log row treatment: the ACTOR leads the sentence — "Alice Feed changed
 * m.cole's platform role" — because "who did it" is the first thing a trail reader scans for.
 * Verb-led sentences fold in grammatically (the verb is lower-cased); the de-slugged fallbacks
 * that don't start with a curated verb ("Transition step") keep their casing behind a quiet
 * separator rather than pretending to be prose.
 */
export function actorLedSentence(actor: string, sentence: string): string {
	if (!sentence) return actor;
	const first = sentence.split(' ', 1)[0] ?? '';
	if (VERB_LED.has(first))
		return `${actor} ${sentence.charAt(0).toLowerCase()}${sentence.slice(1)}`;
	return `${actor} · ${sentence}`;
}

/** The action families a row's leading glyph distinguishes — the Discord-style "what kind of
 *  thing happened" scan. Open vocabulary on the consumer side: unknown types get `settings`. */
export type ActionCategory =
	| 'people'
	| 'roles'
	| 'documents'
	| 'money'
	| 'tasks'
	| 'flows'
	| 'screening'
	| 'security'
	| 'settings';

/**
 * event type → glyph category, by operation-id words (most specific first: `firm-set-portal-role`
 * carries both `role` and `portal`, and it is a ROLE change; `firm-grant-portal` carries only
 * `portal` and is an access/people change). Pure and total — renders evidence, never throws.
 */
export function eventCategory(eventType: string): ActionCategory {
	// A declared name says its resource outright; only a legacy one needs the word heuristic below.
	if (isDeclaredName(eventType)) {
		const [resource = '', verb = ''] = eventType.split('.');
		// The VERB can override the resource's own category, and for one family it must: changing a
		// portal ROLE or a role's PERMISSIONS is a `member.*` event but it is authorisation work, and a
		// reader scanning for "who changed permissions" looks under Roles. Resource alone put those
		// under People, beside "invited a member" — a different question entirely.
		//
		// `portal` on its own is deliberately NOT here, matching the legacy heuristic below: granting
		// portal ACCESS, and linking a client to a login, are about the person and not their
		// permissions.
		if (/\b(role|roles|permission|permissions)\b/.test(verb.replace(/_/g, ' '))) return 'roles';
		const known = RESOURCE_CATEGORY[resource];
		if (known) return known;
	}
	const opId = eventType.startsWith('action.') ? eventType.slice('action.'.length) : eventType;
	const words = new Set(opId.split('-').filter(Boolean));
	const tag = opId.split('-', 1)[0] ?? '';
	const has = (...names: string[]) => names.some((n) => words.has(n));

	if (tag === 'aml' || has('screen', 'screening')) return 'screening';
	// Authentication events (record_auth_action): login / logout / MFA / activation.
	if (tag === 'auth') return 'security';
	if (
		// `shares` is a retired feature's tag, kept so its historic rows still file under Documents.
		['documents', 'templates', 'forms', 'shares', 'datasets'].includes(tag) ||
		has('document', 'documents', 'template', 'form', 'illustration', 'illustrations')
	)
		return 'documents';
	if (has('role', 'roles', 'permissions')) return 'roles';
	if (
		has(
			'instruction',
			'instructions',
			'transfer',
			'transfers',
			'drawdown',
			'model',
			'payment',
			'contribution',
			'contributions',
			'transaction',
			'transactions'
		)
	)
		return 'money';
	if (tag === 'tasks' || has('task')) return 'tasks';
	if (['applications', 'flows'].includes(tag) || has('flow', 'application', 'step')) return 'flows';
	if (
		tag === 'messaging' ||
		has(
			'contact',
			'contacts',
			'member',
			'members',
			'user',
			'users',
			'adviser',
			'invite',
			'client',
			'clients',
			'portal',
			'portals',
			'message',
			'messages',
			'conversation',
			'conversations'
		)
	)
		return 'people';
	return 'settings';
}

/** `PATCH /firm/contacts/{contact_id}` — the operation as an engineer reads it. Kept for the
 *  Metadata row, where the route is the point; `methodLabel` is what a reader sees elsewhere. */
export function methodRoute(event: Pick<ActionEvent, 'method' | 'route'>): string {
	return `${event.method} ${event.route}`;
}

/** The HTTP verb as an English word. `POST` is not a thing that happened to a client — it is the
 *  transport that carried it — and an events log read by advisers and compliance should not ask
 *  them to learn four of them. `DELETE` says Deleted rather than Removed because the audit record
 *  is the only place the distinction between an archive and a delete is legible. */
const METHOD_LABELS: Record<string, string> = {
	POST: 'Created',
	PUT: 'Replaced',
	PATCH: 'Updated',
	DELETE: 'Deleted',
	GET: 'Read'
};

export function methodLabel(method: string): string {
	return METHOD_LABELS[method.toUpperCase()] ?? method.toUpperCase();
}

/** The status FAMILY as a word. A bare `200` beside every row is a column of noise that says
 *  "nothing went wrong" in a dialect most readers of this log do not speak; the exact code still
 *  rides along in the badge's title for whoever wants it. */
export function statusLabel(status: number): string {
	if (status >= 200 && status < 300) return 'Succeeded';
	if (status === 403) return 'Refused';
	if (status === 404) return 'Not found';
	if (status === 409) return 'Conflicted';
	if (status >= 400 && status < 500) return 'Rejected';
	if (status >= 500) return 'Failed';
	return 'Pending';
}

/** HTTP status → the canonical status tone. The stream records effects (2xx by construction),
 *  so success is the norm — the other branches exist so the component cannot mis-tone data it
 *  didn't expect. */
export function statusTone(status: number): 'success' | 'warning' | 'danger' | 'neutral' {
	if (status >= 200 && status < 300) return 'success';
	if (status >= 500) return 'danger';
	if (status >= 400) return 'warning';
	return 'neutral';
}

export interface TimestampParts {
	/** Absolute, British: "14:32" today, "28 Jul, 14:32" this year, "28 Jul 2025, 14:32" older. */
	absolute: string;
	/** Coarse relative hint ("2 min ago"), or null once it stops being helpful (> ~30 days). */
	relative: string | null;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/**
 * The stream's provenance stamp: absolute British time with a relative hint — "14:32 · 2 min ago"
 * (the repo's "as at …" conventions: absolute truth first, recency as a hint). Formatted by hand
 * rather than `toLocaleString` so the output is deterministic across ICU builds — this is
 * evidence, and a test must be able to pin it.
 */
export function timestampParts(iso: string, now: Date = new Date()): TimestampParts {
	const d = new Date(iso);
	if (Number.isNaN(d.getTime())) return { absolute: iso, relative: null };

	const pad = (n: number) => String(n).padStart(2, '0');
	const time = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
	const sameDay =
		d.getFullYear() === now.getFullYear() &&
		d.getMonth() === now.getMonth() &&
		d.getDate() === now.getDate();
	const absolute = sameDay
		? time
		: d.getFullYear() === now.getFullYear()
			? `${d.getDate()} ${MONTHS[d.getMonth()]}, ${time}`
			: `${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}, ${time}`;

	const seconds = Math.round((now.getTime() - d.getTime()) / 1000);
	let relative: string | null = null;
	if (seconds >= 0) {
		if (seconds < 45) relative = 'just now';
		else if (seconds < 90) relative = '1 min ago';
		else if (seconds < 45 * 60) relative = `${Math.round(seconds / 60)} min ago`;
		else if (seconds < 90 * 60) relative = '1 hr ago';
		else if (seconds < 22 * 3600) relative = `${Math.round(seconds / 3600)} hr ago`;
		else if (seconds < 36 * 3600) relative = 'yesterday';
		else if (seconds < 30 * 86400) relative = `${Math.round(seconds / 86400)} days ago`;
	}
	return { absolute, relative };
}
