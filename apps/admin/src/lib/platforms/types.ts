// The shapes the Platforms section renders. Every value in them now comes from the API —
// `PlatformMeta` mirrors `providers.PROVIDER_META`, which is the one registry. The fixture
// datasets these types were written for are gone.
//
// `streamsLabel` went with them: it named the ingestion tab per provider, and SEI's live value
// said 'Streams' while its fixture said 'File feeds' — so a custodian whose entire ingestion is
// a nightly file drop got a nav item promising streams over a page saying it had none. The
// section is 'Ingestion' for all three, and the PAGE is transport-shaped instead.

/** Open, not a union of the three we happen to have: the registry is the API's. */
export type PlatformSlug = string;
export type PlatformRole = 'custodian' | 'scheme_administrator' | 'wrapper_provider';
export type Transport = 'api' | 'webhooks' | 'sftp';
export type ConnectionStatus = 'connected' | 'degraded' | 'error' | 'paused';

export interface PlatformKpi {
	label: string;
	value: string;
	hint?: string;
	/** Optional trend series rendered as a Sparkline. */
	trend?: number[];
}

export interface PlatformMeta {
	/** The provider CODE, which IS the URL segment since 0138 — `morgan-lloyd`, hyphen included.
	 *  Named `provider` and not `slug` because there is no longer a second spelling to convert
	 *  between: `PROVIDER_META['slug']` was deleted with `by_slug()` and `_known_provider`'s
	 *  conversion, and this field went on reading it. `PlatformMeta['slug']` was typed `string`,
	 *  so the `as` cast at the mapping site silenced the compiler and every link in the
	 *  /platforms tree pointed at `/platforms/undefined`. */
	provider: string;
	name: string;
	role: PlatformRole;
	description: string;
	transports: Transport[];
	connection: { status: ConnectionStatus; since: string; detail: string };
	kpis: PlatformKpi[];
}

export type Materialisation = 'stream' | 'table' | 'view' | 'ephemeral' | 'sink';
export type ChangeState = 'unchanged' | 'added' | 'nonbreaking' | 'breaking';

/** One output column and where it came from; srcCol '—' = literal/aggregate. */
export interface ColumnLineage {
	out: string;
	srcModel: string;
	srcCol: string;
}

export interface LineageModel {
	name: string;
	/** Queryable relation in the warehouse when it differs from the model name
	 *  (live catalogues promote `schema.model` to `{env}__schema.model`). */
	relation?: string;
	/** 0-based canvas column. */
	layer: number;
	/** Caption drawn above the layer's column (only needed on one model per layer). */
	layerLabel?: string;
	mat: Materialisation;
	strategy: string;
	state: ChangeState;
	running?: boolean;
	fingerprint: string;
	rows: string;
	schedule: string;
	owner: string;
	deps: string[];
	/** Daemon tags (`tenant:…`, `raw`, …). Empty when the overview predates them. */
	tags: string[];
	cols: ColumnLineage[];
	/**
	 * The relation's actual columns and types, from the warehouse `DESCRIBE`. Distinct from
	 * `cols` (which is column *lineage*, and is empty for Python extractors and `SELECT *`
	 * views) — this is what the relation contains, whether or not anything can explain how.
	 * Absent until the model has been promoted.
	 */
	schema?: { name: string; type: string }[];
	sql: string;
	description?: string;
}

export type RunTrigger = 'cron' | 'manual' | 'api' | 'stream' | 'file';
export type RunStatus = 'running' | 'succeeded' | 'failed' | 'queued' | 'cancelling' | 'cancelled';

export interface RunStep {
	model: string;
	status: RunStatus;
	duration: string | null;
	/** The model's merge strategy (full, full_merge, …) — live datasets only. */
	strategy?: string;
	/** Materialisation the model produces (table / view / sink). */
	output?: string;
	/** Upstream models, from the daemon's compiled graph. */
	dependsOn?: string[];
	/** False when the selector matches no model the daemon knows — it built nothing. */
	known?: boolean;
}

export interface Run {
	id: string;
	trigger: RunTrigger;
	status: RunStatus;
	started: string;
	/** Original enqueue stamp, for sorting across days. */
	startedAt?: string;
	attempts?: number;
	duration: string | null;
	models: string[];
	steps: RunStep[];
	error?: string;
	/**
	 * Selectors this run targeted that match no model the daemon currently knows. Surfaced
	 * rather than hidden: a cron enqueuing `raw_notifications` when the model is
	 * `raw.notifications` "succeeds" while building nothing, and the only visible symptom is a
	 * run list full of names that don't exist.
	 */
	unknownModels?: string[];
}

export type StreamStatus = 'live' | 'idle' | 'paused';

/** A durable ingestion stream (Seccl webhooks) or outbound queue (Morgan Lloyd pushes). */
export interface StreamInfo {
	name: string;
	status: StreamStatus;
	rate: string;
	lag: string;
	retention: string;
	target: string;
	offset: string;
	dlq: number;
	schema: [name: string, type: string][];
	log: { t: string; kind: string; msg: string }[];
}

export type FileStatus = 'loaded' | 'failed' | 'processing';

/** A batch file feed (SEI SFTP DSR extracts). */
export interface BatchFeed {
	name: string;
	dsrType: string;
	cadence: string;
	sla: string;
	lastFile: { name: string; receivedAt: string; rows: number; status: FileStatus };
	quarantined: number;
	history: { name: string; receivedAt: string; rows: number; status: 'loaded' | 'failed' }[];
}

export type FeedEventKind =
	'succeeded' | 'started' | 'enqueued' | 'sink' | 'failed' | 'flush' | 'file';

export interface FeedEvent {
	kind: FeedEventKind;
	model: string;
	message: string;
	/** Real event time (HH:MM:SS). Present on live feeds — the ticker then shows
	 *  the actual log instead of cycling a script against a fake clock. */
	t?: string;
}

/** A named upstream source (e.g. a Morgan Lloyd Microsoft tenant = one firm's CRM). */
export interface PlatformSource {
	external_id: string;
	name: string;
	/**
	 * The vendor reference values this CONNECTION carries (`platform.providers.config`).
	 *
	 * Morgan Lloyd issues them per Dynamics org in their API Access Document and none is
	 * discoverable from the API. Note `firm_id` is NOT `external_id`: that is the Azure directory
	 * (the OAuth tenant, stamped as `ml_tenant_id`), this is a Dynamics `accounts` record inside it.
	 */
	config?: { firm_id?: string; scheme_type?: number; sipp_type?: number };
}

/** One firm's enablement of this platform (its slice filter + fan-out bookkeeping). */
export interface PlatformFirm {
	tenant_id: number;
	uid: string;
	name: string;
	alias: string;
	config: Record<string, unknown>;
	enabled: boolean;
	last_fanout_at: string | null;
	/** The upstream ids this firm's slice covers, with names where the warehouse knows them. */
	slices?: { id: string; name: string | null }[];
	/** Null (not 0) when the warehouse knows none of this firm's ids — see the API's
	 *  tenant_slice_metrics: "not promoted yet" must not render as "no clients". */
	clients?: number | null;
	aum?: number | null;
}

/** The firm-facing plane of a live platform: registered sources + enablements. */
export interface PlatformFirms {
	provider: string;
	sources: PlatformSource[];
	enablements: PlatformFirm[];
}

export interface PlatformDataset {
	meta: PlatformMeta;
	models: LineageModel[];
	runs: Run[];
	streams: StreamInfo[];
	feeds: BatchFeed[];
	/** Script the live event feed cycles through. */
	feedScript: FeedEvent[];
	/** Highest daemon event sequence already in this dataset. The live feed
	 *  resumes after it, so a page load does not replay the log. */
	eventSeq: number;
	/** Set when the dataset came from the API rather than the concept fixtures —
	 *  the Query console then runs against the real warehouse. */
	live?: boolean;
	/** Sources + firm enablements (live platforms only) — drives the tenants list. */
	firms?: PlatformFirms;
	/** Firms with no enablement row yet — the "Add tenant" picker. */
	candidates?: { tenant_id: number; uid: string; name: string; alias: string }[];
	/** Headline figures for the overview (live platforms only). */
	metrics?: PlatformMetrics;
}

/** Overview headline figures. Every field is nullable on purpose: "not promoted yet" and
 *  "genuinely zero" must not look the same, and a scheme administrator has no AUM at all. */
export interface PlatformMetrics {
	clients: number | null;
	aum: number | null;
	/** Firms with this platform enabled. */
	tenants: number;
	/** Oldest first, one entry per day including idle ones. */
	runsPerDay: { date: string; runs: number; failed: number }[];
	lastRunAt: string | null;
	nextRunAt: string | null;
}
