/** The merge fields an author can drop into an email, scoped PER CODE so the menu never offers a token
 *  that renders blank at real send. Mirrors `email/preview.py::_CODE_KEYS` less `NON_INSERTABLE_KEYS`;
 *  merge-fields.test.ts reads that Python source and fails on drift. */
export interface MergeField {
	label: string;
	token: string;
	/** What the field CONTAINS — the value Preview renders (`email/preview.py::_SAMPLE`), because
	 *  `{{ portal_label }}` names nothing on its own. `null` = this send deliberately carries no
	 *  value; ABSENT = there is no sample for it. */
	example?: string | null;
}

export interface MergeFieldGroup {
	group: string;
	fields: MergeField[];
}

const T = {
	// Three spellings of one person: a greeting wants one ("Hi Sarah"), a formal line another
	// ("Dear Ms Whitfield"). Every site that resolves a name answers all three.
	first_name: { label: 'First name', token: '{{ first_name }}', example: 'Sarah' },
	full_name: { label: 'Full name', token: '{{ full_name }}', example: 'Sarah Whitfield' },
	surname: { label: 'Surname', token: '{{ surname }}', example: 'Whitfield' },
	// `password_reset` alone carries no name (`_CODE_SAMPLE_OVERRIDES`): naming the recipient tells a
	// stranger the address has an account, so its rows state the blank instead of showing "Sarah".
	first_name_unset: { label: 'First name', token: '{{ first_name }}', example: null },
	full_name_unset: { label: 'Full name', token: '{{ full_name }}', example: null },
	surname_unset: { label: 'Surname', token: '{{ surname }}', example: null },
	// 'Firm name', not 'Brand name': "brand" is our word for the record, not the author's for themselves.
	// The two `brand.*` examples are ILLUSTRATIVE — a real send passes the firm's own resolved brand.
	brand_name: { label: 'Firm name', token: '{{ brand.name }}', example: 'Ridley & Co' },
	brand_support: {
		label: 'Support email',
		token: '{{ brand.support_email }}',
		example: 'support@ridleyandco.example'
	},
	sent_by: { label: 'Sent by (adviser)', token: '{{ sent_by }}', example: 'James Fletcher' },
	portal_label: { label: 'Portal label', token: '{{ portal_label }}', example: 'Client Portal' },
	portal_url: {
		label: 'Portal URL',
		token: '{{ portal_url }}',
		example: 'https://portal.example.com'
	},
	set_password_url: {
		label: 'Set-password link',
		token: '{{ set_password_url }}',
		example: 'https://portal.example.com/set-password?token=sample-token'
	},
	document_name: {
		label: 'Document name',
		token: '{{ document_name }}',
		example: 'Suitability Report'
	},
	due_date: { label: 'Due date', token: '{{ due_date }}', example: '2026-01-22' },
	message: {
		label: 'Covering note',
		token: '{{ message }}',
		example: 'Thanks — let me know if you have any questions.'
	},
	task_title: {
		label: 'Task title',
		token: '{{ task_title }}',
		example: 'Review Q2 suitability report'
	},
	// Decided by the send, not supplied by it (`email/defaults.py::derived_tokens`); mirrors `_SUBJECT_TOKENS`.
	// Its example is therefore what the sampled item count derives, not a sampled value.
	request_summary: {
		label: 'What is needed',
		token: '{{ request_summary }}',
		example: '3 things need your attention'
	},
	task_description: {
		label: 'Task description',
		token: '{{ task_description }}',
		example: 'Check the updated attitude-to-risk answers before the annual review call.'
	},
	// Conditional in the shipped default (`{% if button_url %}`), so both halves are ordinary tokens.
	button_label: { label: 'Button label', token: '{{ button_label }}', example: 'Open your portal' },
	button_url: {
		label: 'Button link',
		token: '{{ button_url }}',
		example: 'https://portal.example.com'
	},
	// Two actors, not one word: `sent_by` sent a document, `shared_by` shared one by link.
	shared_by: { label: 'Shared by', token: '{{ shared_by }}', example: 'James Fletcher' },
	account_name: {
		label: 'Account name',
		token: '{{ account_name }}',
		example: 'Sarah Whitfield ISA'
	},
	account_number: {
		label: 'Account number',
		token: '{{ account_number }}',
		example: 'SW-0041827'
	},
	// The 'notification' wrapper's slots, named so they don't read as the email's own subject and body.
	notif_title: {
		label: 'Notification title',
		token: '{{ title }}',
		example: 'A document was shared with you'
	},
	notif_body: {
		label: 'Notification message',
		token: '{{ body }}',
		example: 'Your adviser has shared a document. Open your portal to view it.'
	}
} as const;

/** Send-site keys deliberately NOT offered as inserts: `item_titles` is a LIST walked by the request
 *  default's `{% for %}`, so inserting it prints a Python-repr'd array into an inbox. Named rather than
 *  absent so the parity test can tell an exclusion from drift; each must still BE a Python key. */
export const NON_INSERTABLE_KEYS: readonly string[] = ['item_titles'];

/** GENERAL is the context every email has whatever it is about: the recipient's name, the firm's own
 *  details, and the link to send them to. Separate `Recipient`/`Firm`/`Links`/`People` headings read as
 *  four contexts where there is one, and a two-row group under its own band is mostly heading. */
const general = (...fields: MergeField[]): MergeFieldGroup => ({ group: 'General', fields });

const NAMES = [T.first_name, T.full_name, T.surname];
const NAMES_UNSET = [T.first_name_unset, T.full_name_unset, T.surname_unset];
const FIRM_FIELDS = [T.brand_name, T.brand_support];

const CODE_FIELDS: Record<string, MergeFieldGroup[]> = {
	invite: [general(...NAMES, ...FIRM_FIELDS, T.portal_label, T.set_password_url)],
	password_reset: [general(...NAMES_UNSET, ...FIRM_FIELDS, T.set_password_url)],
	document_sent: [
		general(...NAMES, ...FIRM_FIELDS, T.sent_by, T.portal_url),
		{ group: 'Document', fields: [T.document_name] },
		{ group: 'Message', fields: [T.message] }
	],
	request_raised: [
		general(...NAMES, ...FIRM_FIELDS, T.portal_url),
		{ group: 'Request', fields: [T.request_summary, T.due_date] },
		{ group: 'Message', fields: [T.message] }
	],
	// One send site (`app/tasks/notifier.py`), two codes: identical on purpose, and carrying no first name.
	task_assigned: [
		general(...FIRM_FIELDS, T.button_label, T.button_url),
		{ group: 'Task', fields: [T.task_title, T.task_description, T.due_date] }
	],
	task_reminder: [
		general(...FIRM_FIELDS, T.button_label, T.button_url),
		{ group: 'Task', fields: [T.task_title, T.task_description, T.due_date] }
	],
	// The ONE wrapper for every notification email; `email_consumer.py::_template_context` serves custom-
	// template rules off the same context, so their flat aliases are offered here too.
	notification: [
		{ group: 'Notification', fields: [T.notif_title, T.notif_body] },
		general(
			...NAMES,
			...FIRM_FIELDS,
			T.sent_by,
			T.shared_by,
			T.portal_url,
			T.button_label,
			T.button_url
		),
		{ group: 'Document', fields: [T.document_name] },
		{ group: 'Task', fields: [T.task_title, T.task_description] },
		{ group: 'Account', fields: [T.account_name, T.account_number] },
		{ group: 'Request', fields: [T.due_date] },
		{ group: 'Message', fields: [T.message] }
	]
};

/** Every token any send site passes — the reference vocabulary for the parity tests, NOT a menu: no
 *  send site passes all of these, so offering it to a code we cannot identify offers mostly blanks. */
export const MERGE_FIELD_GROUPS: MergeFieldGroup[] = [
	general(
		...NAMES,
		...FIRM_FIELDS,
		T.sent_by,
		T.shared_by,
		T.portal_label,
		T.portal_url,
		T.set_password_url
	),
	{ group: 'Document', fields: [T.document_name] },
	{ group: 'Account', fields: [T.account_name, T.account_number] },
	{ group: 'Request', fields: [T.due_date] },
	{ group: 'Task', fields: [T.task_title, T.task_description] },
	{ group: 'Button', fields: [T.button_label, T.button_url] },
	{ group: 'Message', fields: [T.message] }
];

/** Provided but not worth printing, so a condition can ask what the insert menu will not offer
 *  (`conditions/fields.ts` derives its picker from the INSERTABLE set). An unpassed field is always false. */
export interface ConditionOnlyField {
	field: string;
	/** The author's word for it. Read as "Show when <label> is set". */
	label: string;
}

/** The field the masthead depends on — its image's `src` AND its fallback's trigger, which have to be
 *  the same reference or the fallback fires on the wrong thing. A mismatch between the two once left
 *  the masthead compiling `{% if brand.logo_url %}` against a field that was no longer its `src`, so
 *  a firm WITH a stored logo got the wordmark. Declared here, the leaf, and read by `create.ts`. */
export const BRAND_LOGO_FIELD = 'brand.logo_url';

const BRAND_LOGO: ConditionOnlyField = { field: BRAND_LOGO_FIELD, label: 'Firm logo' };

/** The firm's MARK — what the Brand icon block places, and what a condition asks about.
 *
 *  Declared here beside `BRAND_LOGO_FIELD` and for the same reason: the block's `src` and its
 *  visibility rule have to name ONE field, or the block hides on a different fact from the one it
 *  draws. `app/email/transport.py` resolves it into `brand.icon_url` for exactly this — "the firm's
 *  MARK alone, so a template can place a badge a resolved logo cannot" — and until the block existed
 *  the editor let an author ask whether the icon was there while giving them no way to show it. */
export const BRAND_ICON_FIELD = 'brand.icon_url';

/** "Is there a square mark", where `brand.logo_url` asks "is there a masthead image"; both universal. */
const BRAND_ICON: ConditionOnlyField = { field: BRAND_ICON_FIELD, label: 'Firm icon' };

const CONDITION_ONLY: Record<string, readonly ConditionOnlyField[]> = {
	invite: [BRAND_LOGO, BRAND_ICON],
	password_reset: [BRAND_LOGO, BRAND_ICON],
	document_sent: [BRAND_LOGO, BRAND_ICON],
	request_raised: [BRAND_LOGO, BRAND_ICON],
	// The universal pair and nothing else: the notifier passes no flag either template could ask about.
	task_assigned: [BRAND_LOGO, BRAND_ICON],
	task_reminder: [BRAND_LOGO, BRAND_ICON],
	notification: [BRAND_LOGO]
};

/** What a condition may ask about beyond the insertable tokens; a custom code gets the universal pair. */
export function conditionOnlyFieldsFor(
	code: string | null | undefined
): readonly ConditionOnlyField[] {
	return (code && CONDITION_ONLY[code]) || [BRAND_LOGO, BRAND_ICON];
}

/** LIST fields a row can repeat over, per code: one the send site does not pass gives an empty loop. */
export interface RepeatableField {
	field: string;
	/** The author's word for it. Read as "Repeats for each <label>". */
	label: string;
}

const REQUEST_ITEMS: RepeatableField = { field: 'item_titles', label: 'requested item' };

const REPEATABLE: Record<string, readonly RepeatableField[]> = {
	request_raised: [REQUEST_ITEMS],
	notification: [REQUEST_ITEMS]
};

/** The lists a row may repeat over. NONE for a code that declares none: a repeat over a list its send
 *  site does not pass renders the row zero times, which reads as the row having been deleted. */
export function repeatableFieldsFor(code: string | null | undefined): readonly RepeatableField[] {
	return (code && REPEATABLE[code]) || [];
}

export const SCOPED_CODES: readonly string[] = Object.keys(CODE_FIELDS);

// What each email is FOR is the API's own answer: `email/catalog.py::KNOWN_CODES` carries the
// description and both catalogue endpoints (`GET /email-templates/catalog`, and the admin one) serve
// it on `EmailCodeOut`, which the platform and admin template pages already fetch. A surface that
// wants to name a send reads it from there rather than from a second copy of the same seven strings
// pinned by a regex over Python source.

/** A code we do not know has no send site to read a vocabulary off, so it gets the UNIVERSAL fields
 *  alone — the firm's own, passed on every send. */
export function mergeFieldsFor(code: string | null | undefined): MergeFieldGroup[] {
	return (code && CODE_FIELDS[code]) || [general(...FIRM_FIELDS)];
}

/** Every field flattened out of the groups — the shape the cross-language parity test compares. */
export const MERGE_FIELDS: MergeField[] = MERGE_FIELD_GROUPS.flatMap((g) => g.fields);

/** Filter the vocabulary by what the author typed — one matcher behind BOTH the rail's Insert flyout and
 *  typing `{{`. Label, token AND group, by SUBSTRING (a prefix finds nothing for "name"); empty groups drop. */
export function matchFields(
	groups: readonly MergeFieldGroup[],
	query: string | null | undefined
): MergeFieldGroup[] {
	const q = String(query ?? '')
		.trim()
		.toLowerCase();
	if (q === '') return groups.map((g) => ({ group: g.group, fields: [...g.fields] }));
	const out: MergeFieldGroup[] = [];
	for (const g of groups) {
		const hay = g.group.toLowerCase();
		const fields = g.fields.filter(
			(f) =>
				f.label.toLowerCase().includes(q) || f.token.toLowerCase().includes(q) || hay.includes(q)
		);
		if (fields.length > 0) out.push({ group: g.group, fields });
	}
	return out;
}

/** The first field a query matches — off `matchFields`, so Enter cannot pick a different row than shown. */
export function firstMatch(
	groups: readonly MergeFieldGroup[],
	query: string | null | undefined
): MergeField | null {
	return matchFields(groups, query)[0]?.fields[0] ?? null;
}

/** The bare token PATH out of a token, filter and all, so `{{ x | date }}` still maps to `x`. */
const pathOf = (token: string): string => token.replace(/^\{\{\s*|\s*(\|[^}]*)?\}\}$/g, '').trim();

/** path → field, reversed out of `T` so a rename cannot drift — over the WHOLE declared catalogue and
 *  deliberately NOT off `MERGE_FIELDS`. `MERGE_FIELD_GROUPS` is the reference vocabulary the parity
 *  tests iterate, not a menu, and the notification wrapper's own two slots (`{{ title }}`, `{{ body }}`)
 *  are declared here and offered by `CODE_FIELDS.notification` without ever appearing in it — so a
 *  lookup built off the flattened groups answers nothing for exactly the two fields that wrapper exists
 *  to place, and a caller that filters out what it cannot resolve drops them silently.
 *
 *  A KEY IS NOT A TOKEN — `notif_title` carries `{{ title }}` and `brand_name` carries
 *  `{{ brand.name }}` — so the map is keyed by the PATH. Two keys share one path when a code
 *  deliberately carries no value there (the `*_unset` trio, `password_reset`'s alone), and the SAMPLED
 *  entry wins: this answers "what does this token carry" for a caller that cannot name the send site,
 *  where the blank is one code's fact about its own send and never the token's. `mergeFieldsFor` is
 *  still the door for a code we CAN name, and it hands out the blank rows itself. Any other collision
 *  keeps the first declaration, so the answer never depends on which entry `T` happens to declare last. */
const FIELDS_BY_PATH: ReadonlyMap<string, MergeField> = (() => {
	const out = new Map<string, MergeField>();
	for (const field of Object.values(T)) {
		const path = pathOf(field.token);
		const held = out.get(path);
		if (held === undefined || (held.example === null && field.example !== null))
			out.set(path, field);
	}
	return out;
})();

/** The field a token PATH names, across every DECLARED field rather than the menu vocabulary — what a
 *  caller assembling a group out of bare key names needs (`notificationGroup` below walks the
 *  notification consumer's own key list). Null for a path nothing declares, which is the caller's
 *  cue to skip the row rather than show an author a bare token with no label and no example. */
export function mergeFieldForPath(path: string | null | undefined): MergeField | null {
	return FIELDS_BY_PATH.get(String(path ?? '').trim()) ?? null;
}

/** The human label for a field PATH: the canvas has to NAME a chip in the badge, heading and layer tree. */
export function labelForField(path: string): string | null {
	return mergeFieldForPath(path)?.label ?? null;
}

/** The flat keys a notification send passes, mirroring `_template_context` in
 *  `app/notifications/email_consumer.py`. A notification rule's `email_code` is the only door a
 *  CUSTOM template has — the built-in send sites are keyed by code, and the flows `send_email`
 *  action resolves `documents.templates`, a different system. `notification-source.test.ts` reads
 *  the Python source, so a key added or renamed there fails here rather than drifting quietly. */
export const NOTIFICATION_SUPPLIED: readonly string[] = [
	'title',
	'body',
	'button_label',
	'button_url',
	'first_name',
	'full_name',
	'surname',
	'document_name',
	'sent_by',
	'shared_by',
	'task_title',
	'task_description',
	'account_name',
	'account_number',
	'portal_url',
	'message',
	'item_titles',
	'due_date'
];

/** The heading the notification vocabulary sits under. Named as a CONDITION, not a fact: these arrive
 *  only if a notification rule is what sends this template. */
export const NOTIFICATION_GROUP = 'If a notification sends this';

/** The notification vocabulary as an insertable group — the cheat sheet, in the menu rather than in a
 *  document nobody opens. Labels come from the field catalogue so there is one set of words for
 *  `{{ title }}` whichever list it appears in; a name the catalogue does not know is skipped rather
 *  than shown as a bare token, and `NON_INSERTABLE_KEYS` is honoured (`item_titles` is a LIST, and
 *  printing it puts a Python repr in an inbox). Null when nothing survives that. */
export function notificationGroup(): MergeFieldGroup | null {
	// Resolved against every DECLARED field, not against `MERGE_FIELD_GROUPS`: that constant is the
	// parity tests' reference vocabulary rather than a menu, and it carries no row for `{{ title }}`
	// or `{{ body }}` — so a lookup built off it silently dropped the two tokens the notification
	// wrapper exists to place, leaving this group offering 15 of the 17 keys a rule actually passes.
	const fields = NOTIFICATION_SUPPLIED.filter((k) => !NON_INSERTABLE_KEYS.includes(k))
		.map(mergeFieldForPath)
		.filter((f): f is MergeField => f !== null);
	return fields.length ? { group: NOTIFICATION_GROUP, fields } : null;
}

/** ONE list for both ways in — the rail's flyout and typing `{{` — so the two can never offer
 *  different vocabularies. */
export function insertVocabulary(code: string | null | undefined): MergeFieldGroup[] {
	const groups = [...mergeFieldsFor(code)];
	// A CUSTOM code only: a known one is offered exactly what its own send site passes, and adding a
	// second maybe-list there would undo the scoping. A custom code's only sender IS a notification
	// rule, so this is what it actually receives — the honest answer to "what is passed".
	if (!SCOPED_CODES.includes(String(code ?? ''))) {
		const notification = notificationGroup();
		if (notification) groups.push(notification);
	}
	return groups;
}
