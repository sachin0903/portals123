/**
 * Flows/onboarding API ↔ UI types for the platform (adviser) portal. The FastAPI `/flows` and
 * `/cases` surfaces speak snake_case; these interfaces mirror the response models one-to-one so
 * the pages work directly in the API shapes (mirrors the platform's `$lib/forms.ts`). The flow *definition*
 * (`{steps, milestones}`) is opaque JSON the builder edits and posts back verbatim.
 */

/** The step-type vocabulary (mirrors app/flows/definitions.py `STEP_TYPES`). */
export type FlowStepType =
	| 'fill_form'
	| 'accept_terms'
	| 'upload_document'
	| 'setup_account'
	| 'verify_identity'
	| 'aml_check'
	| 'generate_document'
	| 'review'
	| 'task';

/** Who completes a step. */
export type FlowAssignee = 'client' | 'staff';

/** The tier a step runs at (mirrors app/flows/definitions.py `SCOPES`). */
export type FlowScope = 'participant' | 'account' | 'application';

/** A flow's lifecycle status (shared with versions). */
export type FlowStatus = 'draft' | 'published' | 'archived';

/** A raise-a-task side-effect on a step (docs/tasks.md): when the step hits the trigger, the
 *  engine enqueues a standalone task for a team (role slug), a named staff user, or the step's
 *  participant contact. Fire-and-forget — never blocks the flow. */
export interface FlowTaskSpec {
	on: 'completed' | 'failed' | 'blocked' | 'waiting';
	assign_to:
		{ kind: 'role'; role: string } | { kind: 'user'; user_id: string } | { kind: 'participant' };
	title: string;
	category?: 'review' | 'acknowledge' | 'upload' | 'follow_up' | 'generic';
	priority?: 'low' | 'normal' | 'high';
	due_days?: number | null;
	description?: string | null;
}

/** One authored step in a flow definition. `config` keys are type-specific (validated server-side
 *  against the step type's allowlist); `visibility` is opaque and reserved for later. */
export interface FlowStep {
	id: string;
	type: FlowStepType;
	title: string;
	required: boolean;
	assignee: FlowAssignee;
	/** Which tier this step runs at — participant (per person), account (per account), application. */
	scope: FlowScope;
	/** For account-scoped steps: fan out one instance per account owner (e.g. joint-account terms). */
	per_participant?: boolean;
	/** Step ids that must complete before this one (by definition key). */
	depends_on?: string[];
	visibility?: Record<string, unknown> | null;
	/**
	 * Canvas coordinate for the visual editor (UI-only; opaque to the engine).
	 *
	 * **Persisted and honoured** (flow-contract.json → `nodeEnvelope.$comment_position`): the builder
	 * saves where the author dropped a node, and the dagre pass places only the nodes whose position
	 * is null. So a definition where every node carries null still lays itself out, and one the
	 * author has arranged keeps its arrangement. It was written as null unconditionally before v3,
	 * which is why dragging had to be disabled — a dragged node snapped back on the next render.
	 */
	position?: { x: number; y: number } | null;
	config: Record<string, unknown>;
	/** Fire-and-forget tasks raised when this step completes/fails/blocks/waits. */
	tasks?: FlowTaskSpec[];
}

/** A milestone: a named status reached once the listed step ids complete. */
export interface FlowMilestone {
	status: string;
	reached_when: string[];
	/** What a client reads. Absent, the status slug is shown. */
	label?: string;
	/** One sentence under that label on the client's journey. */
	summary?: string;
}

/** A custom field the flow collects at kickoff (the "New journey" screen). */
export interface IntakeField {
	key: string;
	label: string;
	type: 'text' | 'textarea' | 'select' | 'date' | 'email' | 'number' | 'checkbox';
	required: boolean;
	options: string[];
}

/** What a flow collects to START a journey — the "New journey" screen is generated from this. */
export interface FlowIntake {
	subject: 'accounts' | 'person';
	collect_platform: boolean;
	collect_custody: boolean;
	collect_product: boolean;
	allow_new: boolean;
	/** A last page that restates the choices. Omitted means on for an account journey and off
	 *  for a person journey, which has nothing to restate beyond the clients just chosen. */
	review?: boolean;
	fields: IntakeField[];
}

export const DEFAULT_INTAKE: FlowIntake = {
	subject: 'accounts',
	collect_platform: true,
	collect_custody: true,
	collect_product: true,
	allow_new: true,
	review: true,
	fields: []
};

/** Fill the keys a stored intake may omit. A person journey does not grow a review page. */
export function intakeOf(raw: Partial<FlowIntake> | null | undefined): FlowIntake {
	const intake: FlowIntake = { ...DEFAULT_INTAKE, ...raw, fields: raw?.fields ?? [] };
	if (raw == null || raw.review === undefined) intake.review = intake.subject !== 'person';
	return intake;
}

/** The authored `{steps, milestones, intake}` JSON (validated by app/flows/definitions.py). */
export interface FlowDefinition {
	steps: FlowStep[];
	milestones: FlowMilestone[];
	intake?: FlowIntake | null;
}

/** `GET /flows` list / `GET /flows/{uid}` (FlowOut). */
export interface FlowOut {
	uid: string;
	code: string;
	name: string;
	description: string | null;
	entity: string | null;
	status: string;
	version_count: number;
	published_version: number | null;
	/** The published version's intake (what "New journey" collects), or null if unpublished. */
	intake?: FlowIntake | null;
}

/** `GET /flows/{uid}/versions[/{vid}]` (FlowVersionOut). */
export interface FlowVersionOut {
	uid: string;
	version: number;
	status: string;
	definition: FlowDefinition;
	published_at: string | null;
}

/** A snapshotted form field (frozen at instantiate) — enough to label a submitted answer. */
export interface CaseStepFormField {
	field_key: string;
	label: string;
	field_type: string;
}

/** One step of a running case (CaseStepOut) — the backing request/form/account links plus, for
 *  staff review, what the client submitted (answers / linked document / when). */
export interface CaseStep {
	step_id: string;
	step_type: string;
	title: string | null;
	assignee: string;
	required: boolean;
	status: string;
	request_uid?: string | null;
	request_item_uid?: string | null;
	form_uid?: string | null;
	account_id?: number | null;
	/** Frozen form definition (fill_form) — used to label the submitted answers. */
	form?: { name?: string | null; fields?: CaseStepFormField[] } | null;
	/** The client's submitted form answers (fill_form), keyed by field_key. */
	response?: Record<string, unknown> | null;
	/** The linked document (upload = uploaded file; agree/acknowledge = the reviewed doc). */
	document_uid?: string | null;
	document_name?: string | null;
	/** When the step was completed. */
	fulfilled_at?: string | null;
}

/** `GET /cases/{uid}` (CaseOut) — a running flow instance + its steps. */
export interface CaseOut {
	uid: string;
	flow_uid: string;
	flow_name: string;
	subject_contact_id: number | null;
	subject_name: string | null;
	status: string;
	initiation: string | null;
	steps: CaseStep[];
}

/** One row of `GET /cases` (the list projection; no steps). */
export interface CaseRow {
	uid: string;
	flow_name: string;
	status: string;
	initiation: string | null;
	subject_name: string | null;
	created_at: string;
}

/** A client the case subject can be (GET /documents/clients → ClientOut). */
export interface FlowClient {
	contact_id: number;
	name: string | null;
	email: string | null;
}

/** A product row (GET /catalog/products). Also the source of the platform picker: the distinct
 *  `provider` values are derived from this list. */
export interface CatalogProduct {
	id: number;
	uid: string;
	/** The wrapper. `code` is the same value as text; `product_type` is the enum column. */
	product_type: string | null;
	code: string | null;
	name: string;
	description: string | null;
	is_pension: boolean;
	enabled: boolean;
	allowed_custodies: ('onshore' | 'offshore')[];
	allowed_client_types: ('individual' | 'organisation')[];
	max_parties: number;
	min_age: number | null;
	max_age: number | null;
	/** Enabled custodian mappings for this firm, in provider-code form. */
	custodians: string[];
	/** The custodian holding it, from `platform.product_platforms` — NULL for a shelf item no
	 *  provider is mapped to yet, which is a real and ordinary state (a firm offers the product
	 *  before the mapping is wired). Preferred `role = 'custodian'`, since a product may also
	 *  name a scheme administrator, and `role` says which this is.
	 *
	 *  THE CODE IS THE IDENTITY (0131). There was a `platform_id` surrogate and a joined
	 *  `platform_name` here; `platform.platforms` is dropped and the code is both. */
	provider: string | null;
	role: 'custodian' | 'scheme_administrator' | 'wrapper_provider' | null;
}

/** A custodian, derived from the product catalogue. `id` IS the provider code (0131). */
export interface CatalogPlatform {
	id: string;
	name: string;
	/** Custody locations this custodian can open. Seccl is onshore only; SEI follows
	 *  the tenant's `custody_orgs` map. Absent means both, matching an unconfigured SEI firm. */
	openable_custodies?: ('onshore' | 'offshore')[];
}

/** An investment model (GET /catalog/models). */
export interface CatalogModel {
	id: number;
	/** Null = firm-wide model (show for every custodian). The provider code since 0131. */
	provider: string | null;
	name: string;
}

/** A fee package (GET /catalog/fee-packages). */
export interface CatalogFeePackage {
	id: number;
	provider: string | null;
	product_id: number | null;
	model_id: number | null;
	name: string;
	category?: string | null;
	fee_type?: string | null;
	frequency?: string | null;
	/** Shape depends on fee_type — amount / rate / tiers. Untyped on purpose. */
	rule?: unknown;
	currency?: string | null;
	description?: string | null;
	vatable?: boolean | null;
	access?: string | null;
	is_default?: boolean;
}

/** A ceding scheme (GET /catalog/transfer-providers). */
export interface CatalogTransferProvider {
	id: number;
	name: string;
	masterparty_id: string | null;
}

/** A form the `fill_form` step can point at (GET /forms → FormOut, trimmed to what the picker needs). */
export interface FlowForm {
	uid: string;
	code: string;
	name: string;
	status: string;
}

/** A document an `accept_terms` step can point at (GET /documents, trimmed to the picker's needs). */
export interface FlowDocument {
	uid: string;
	name: string;
}

/** A published library term an `accept_terms` step can pin (GET /terms, published only). */
export interface FlowTerm {
	uid: string;
	title: string;
}

/** A template a `generate_document` step can render (GET /templates, trimmed to the picker). */
export interface FlowTemplate {
	uid: string;
	name: string;
}

/** A field the visibility editor can key a condition on, with a label for the picker. These mirror
 *  the tiered planning context (app/flows/planning.py): applicant facts on participant-scope steps,
 *  application/account facts on account-scope steps — so an authored branch matches the seeded
 *  onboarding (offshore / pension / ISA / organisation). */
export const VISIBILITY_FIELDS: { field: string; label: string }[] = [
	{ field: 'applicant.type', label: 'Applicant type (individual / organisation)' },
	{ field: 'application.custody', label: 'Custody (onshore / offshore)' },
	{ field: 'application.has_pension_product', label: 'Application has a pension product' },
	{ field: 'application.has_sipp_product', label: 'Application has a SIPP' },
	{ field: 'application.has_transfer', label: 'Application has a transfer' },
	{ field: 'account.product', label: 'Account product (GIA / ISA / SIPP…)' },
	{ field: 'account.custody', label: 'Account custody (onshore / offshore)' },
	{ field: 'account.has_transfer', label: 'Account has a transfer' }
];

/** The condition operators the engine understands (app/flows/visibility.py), for the editor. */
export const VISIBILITY_OPERATORS: { op: string; label: string }[] = [
	{ op: 'equals', label: 'Is' },
	{ op: 'notEquals', label: 'Is not' },
	{ op: 'isNotEmpty', label: 'Is set' },
	{ op: 'isEmpty', label: 'Is not set' }
];

/** Order custodians for a picker: SEI first (it is the default pick on a new journey), then the
 *  rest alphabetically. */
export function seiFirst(platforms: CatalogPlatform[]): CatalogPlatform[] {
	const sei = (name: string) => name.trim().toLowerCase() === 'sei';
	return [...platforms].sort((a, b) => {
		if (sei(a.name) !== sei(b.name)) return sei(a.name) ? -1 : 1;
		return a.name.localeCompare(b.name);
	});
}

/** Derive the distinct custodians from a product list (name from the joined `platform_name`).
 *  The fallback for a caller with no custodian list of its own — it can only ever name the
 *  custodians the firm holds a shelf for, so prefer `GET /catalog/custodians` where reachable. */
export function platformsFromProducts(products: CatalogProduct[]): CatalogPlatform[] {
	const seen = new Map<string, string>();
	for (const p of products) {
		// A product with no custodian mapped contributes NO platform. It is still a shelf item —
		// the products list carries it — but putting a `null` id in a picker offers a custodian
		// that does not exist, and every downstream read (`model.provider === provider`) would
		// then match on null.
		if (p.provider == null) continue;
		if (!seen.has(p.provider)) seen.set(p.provider, p.provider);
	}
	return seiFirst([...seen.entries()].map(([id, name]) => ({ id, name })));
}

/** Human label for a step type. */
export const STEP_TYPE_LABELS: Record<FlowStepType, string> = {
	fill_form: 'Fill in a form',
	accept_terms: 'Accept terms',
	upload_document: 'Upload a document',
	setup_account: 'Set up an account',
	verify_identity: 'Verify identity',
	aml_check: 'AML check',
	generate_document: 'Generate a document',
	review: 'Compliance review',
	task: 'Manual task'
};

/** A one-line hint per step type, shown under the type picker in the builder. */
export const STEP_TYPE_HINTS: Record<FlowStepType, string> = {
	fill_form: 'The client completes a form; answers can populate their CRM profile.',
	accept_terms: 'The client reads and agrees to a library term, a written declaration or a PDF.',
	upload_document: 'The client uploads a document (ID, proof of address…).',
	setup_account: 'Staff open the investment account on the platform.',
	verify_identity: 'Identity check — manual review, or an IDV provider.',
	aml_check: 'AML/sanctions screening — Graphene screens the client automatically.',
	generate_document: 'Render a template to a document (suitability report, pack…).',
	review:
		'A staff compliance decision (e.g. offshore enhanced due diligence) that can gate progress.',
	task: 'A manual to-do for staff or the client.'
};

/** The default assignee per step type (mirrors app/flows/definitions.py). */
export const STEP_TYPE_DEFAULT_ASSIGNEE: Record<FlowStepType, FlowAssignee> = {
	fill_form: 'client',
	accept_terms: 'client',
	upload_document: 'client',
	setup_account: 'staff',
	verify_identity: 'staff',
	aml_check: 'staff',
	generate_document: 'staff',
	review: 'staff',
	task: 'staff'
};

/** The default tier per step type (mirrors app/flows/definitions.py `default_scope`). */
export const STEP_TYPE_DEFAULT_SCOPE: Record<FlowStepType, FlowScope> = {
	fill_form: 'participant',
	accept_terms: 'account',
	upload_document: 'participant',
	setup_account: 'account',
	verify_identity: 'participant',
	aml_check: 'participant',
	generate_document: 'application',
	review: 'account',
	task: 'application'
};

/** The `config` keys each step type accepts (mirrors the server allowlist). */
export const STEP_TYPE_CONFIG_KEYS: Record<FlowStepType, string[]> = {
	fill_form: ['form_uid', 'category', 'mapping', 'mode', 'title'],
	accept_terms: ['document_uid', 'content', 'category', 'title'],
	upload_document: ['category', 'format_hint', 'title'],
	setup_account: ['provider', 'product_id', 'model_id', 'fee_package_id', 'role'],
	verify_identity: ['provider', 'category'],
	aml_check: ['category'],
	generate_document: ['template_uid', 'dataset', 'category'],
	review: ['title', 'category', 'gate'],
	task: ['description', 'invite_clients']
};

/** Human label + description for each tier, for the builder's scope picker. */
export const SCOPE_LABELS: Record<FlowScope, string> = {
	participant: 'Per client (KYC)',
	account: 'Per account',
	application: 'Once for the application'
};

/** Map a flow/version status onto a StatusBadge tone. */
export function flowStatusTone(status: string): 'success' | 'warning' | 'neutral' {
	if (status === 'published') return 'success';
	if (status === 'draft') return 'warning';
	return 'neutral';
}

/** Map a case status onto a StatusBadge tone. */
export function caseStatusTone(
	status: string
): 'success' | 'warning' | 'info' | 'danger' | 'neutral' {
	switch (status) {
		case 'completed':
			return 'success';
		case 'in_progress':
			return 'info';
		case 'cancelled':
			return 'danger';
		case 'draft':
			return 'warning';
		default:
			return 'neutral';
	}
}

/** Map a case-step status onto a StatusBadge tone. */
export function stepStatusTone(status: string): 'success' | 'warning' | 'info' | 'neutral' {
	switch (status) {
		case 'completed':
			return 'success';
		case 'in_progress':
			return 'info';
		case 'pending':
			return 'warning';
		default:
			return 'neutral';
	}
}

/** A blank step of the given type, with sensible defaults for the builder. */
export function blankStep(type: FlowStepType, id: string): FlowStep {
	return {
		id,
		type,
		title: '',
		required: true,
		assignee: STEP_TYPE_DEFAULT_ASSIGNEE[type],
		scope: STEP_TYPE_DEFAULT_SCOPE[type],
		per_participant: false,
		depends_on: [],
		visibility: null,
		config: {}
	};
}

/** A `DynamicForm` instance, as this module needs to see it. */
export interface StepAnswerSource {
	validate: () => boolean;
	collect: () => Record<string, unknown>;
}

/**
 * Attach a staff-fulfilled step's answers to the outgoing form body, **synchronously**, at submit
 * time. Returns false when the answers don't validate (the caller cancels the submission).
 *
 * Why this exists rather than a `$state`-backed hidden input: Svelte 5 batches state → DOM writes
 * into a microtask, while SvelteKit's `enhance` builds the request body synchronously in the submit
 * listener (`new FormData(form, submitter)`). Assigning to a `$state` and calling `requestSubmit()`
 * in the same tick therefore posted the PREVIOUS value — `'{}'` on the first click — and the API
 * happily wrote an empty `form_responses` row and completed the step. Building the body here means
 * there is no ordering dependency left to get wrong.
 */
export function attachStepAnswers(
	formData: FormData,
	source: StepAnswerSource | undefined | null
): boolean {
	if (!source || !source.validate()) return false;
	formData.set('answers', JSON.stringify(source.collect()));
	return true;
}

/**
 * Read the posted answers on the server side of the step-fulfilment action.
 *
 * The `answers` entry only exists because `attachStepAnswers` put it there, inside `use:enhance` —
 * the questions themselves are rendered by `DynamicForm`, which is not inside the posting `<form>`
 * and has no native inputs to fall back on. So a submission with the field ABSENT means JavaScript
 * did not run, and the honest answer is to say so: defaulting to `{}` posted an empty submission
 * that the API accepts for an all-optional form, completing a fact-find with nothing recorded.
 *
 * `null` → "no answers were posted"; a malformed string → "malformed"; otherwise the parsed object.
 */
export type ParsedStepAnswers =
	{ ok: true; answers: Record<string, unknown> } | { ok: false; reason: 'absent' | 'malformed' };

export function parseStepAnswers(raw: FormDataEntryValue | null): ParsedStepAnswers {
	if (raw === null) return { ok: false, reason: 'absent' };
	let parsed: unknown;
	try {
		parsed = JSON.parse(String(raw));
	} catch {
		return { ok: false, reason: 'malformed' };
	}
	if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
		return { ok: false, reason: 'malformed' };
	}
	return { ok: true, answers: parsed as Record<string, unknown> };
}

// ── The fill_form mapping vocabulary ──────────────────────────────────────────────────────────
// Read from the contract rather than restated, so the editor can only ever offer what the mapper
// implements and the writer whitelists (pinned both sides — apps/api/tests/test_flows_graph.py).
import contract from './flow-contract.json' with { type: 'json' };

const rawMapping = contract.mapping as { transforms: string[]; targets: string[] };

/** Transform names, composed left to right. All are `str -> str`. */
export const MAPPING_TRANSFORMS: readonly string[] = rawMapping.transforms;

/** The `crm.contacts` columns a mapping may set directly. Anything else an author types is a
 *  dotted path nested into the `data` jsonb — legal, and how the fact-find's overflow is stored. */
export const MAPPING_TARGETS: readonly string[] = rawMapping.targets;

/** One authored mapping row. Mirrors `app/flows/mapping.py::FieldMapping`. */
export type FieldMapping = {
	source: string;
	target: string;
	transforms?: string[];
	required?: boolean;
};

/** A stored value as a person reads it: `national_identifier_type` -> "National identifier type". */
export function mappingLabel(value: string): string {
	return value.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** Parse whatever is on a step's config into rows the editor can bind to. Defensive because the
 *  config is free-form jsonb: a hand-edited or older definition must not crash the inspector. */
export function readMapping(raw: unknown): FieldMapping[] {
	if (!Array.isArray(raw)) return [];
	return raw.flatMap((m) => {
		if (!m || typeof m !== 'object') return [];
		const row = m as Record<string, unknown>;
		if (typeof row.source !== 'string' || typeof row.target !== 'string') return [];
		return [
			{
				source: row.source,
				target: row.target,
				transforms: Array.isArray(row.transforms)
					? row.transforms.filter((t): t is string => typeof t === 'string')
					: [],
				required: row.required === true
			}
		];
	});
}
