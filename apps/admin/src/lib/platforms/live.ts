// Maps the API's platform overview (GET /admin/platforms/{provider}/overview — the
// interlace daemon snapshot + warehouse counts + registries) onto the PlatformDataset
// the platform page renders. Pure: runs server-side in the page load, unit-tested.

import type {
	ChangeState,
	ConnectionStatus,
	FeedEvent,
	FeedEventKind,
	LineageModel,
	Materialisation,
	PlatformDataset,
	PlatformFirm,
	PlatformMeta,
	PlatformSource,
	Run,
	RunStatus,
	RunTrigger,
	StreamInfo,
	StreamStatus
} from './types';

// `LIVE_PROVIDERS` used to live here — a slug -> provider key map, one of seven hard-coded tables
// over the same three custodians. The API resolves either form now (`_known_provider`), so a route
// passes `params.slug` straight through and this app holds no such map at all.

// ---- the API payload (mirrors app/platforms.py::overview) ----

export interface ApiModel {
	name: string;
	output: string; // table | view | ephemeral | stream | sink
	materialise: string;
	strategy: string;
	fingerprint: string;
	depends_on: string[];
	owner: string | null;
	schedule: { cron?: string; interval?: string } | null;
	columns: Record<string, string[]>; // out col -> ["<src model>.<src col>", ...]
	sql: string | null;
	tags?: string[];
}

export interface ApiRun {
	id: number;
	flow_selector: string[];
	state: string; // queued | running | succeeded | failed
	attempts: number;
	error: string | null;
	enqueued_at: string | null;
	idempotency_key: string | null;
	/** Overall run seconds when the daemon publishes it; events are preferred. */
	duration?: number | string | null;
}

export interface ApiEvent {
	seq: number;
	ts: string;
	type: string;
	entity: string | null;
	payload: {
		models?: string[];
		error?: string;
		state?: string;
		rows?: number;
		built?: string[];
		/** Per-model build seconds, published on run.succeeded. */
		timings?: Record<string, number>;
	} | null;
}

export interface ApiPlanChange {
	name: string;
	change_type: string; // added | modified | removed
	category: string | null; // breaking | nonbreaking
}

export interface ApiStream {
	name: string;
	schema: Record<string, string>;
	table: string;
	head: number;
	watermark: number;
}

export interface ApiOverview {
	provider: string;
	/** How this provider presents — from `providers.PROVIDER_META`, the one registry. */
	meta: LiveMeta;
	service:
		| {
				status: 'up';
				models: ApiModel[];
				plan: Record<string, ApiPlanChange>;
				runs: ApiRun[];
				events: ApiEvent[];
				streams: ApiStream[];
		  }
		| { status: 'down'; error: string };
	metrics?: {
		clients?: number | null;
		aum?: number | null;
		tenants?: number;
		runs_per_day?: { date: string; runs: number; failed: number }[];
		last_run_at?: string | null;
		next_run_at?: string | null;
	};
	counts: Record<string, number>;
	/** Column schemas per model from the warehouse (`DESCRIBE` on the promoted
	 *  relation): [name, type][]. The daemon only knows lineage for SQL models. */
	schemas: Record<string, [string, string][]>;
	sources: PlatformSource[];
	firms: PlatformFirm[];
	candidates?: { tenant_id: number; uid: string; name: string; alias: string }[];
}

// ---- mapping helpers ----

const MATS = new Set<Materialisation>(['stream', 'table', 'view', 'ephemeral', 'sink']);

function mat(model: ApiModel): Materialisation {
	if (model.output === 'sink') return 'sink';
	return MATS.has(model.materialise as Materialisation)
		? (model.materialise as Materialisation)
		: 'table';
}

function planState(change: ApiPlanChange | undefined): ChangeState {
	if (!change) return 'unchanged';
	if (change.category === 'breaking') return 'breaking';
	if (change.change_type === 'added') return 'added';
	return 'nonbreaking';
}

/** Topological depth: models with no in-graph deps sit at layer 0. */
function layers(models: ApiModel[]): Map<string, number> {
	const byName = new Map(models.map((m) => [m.name, m]));
	const depth = new Map<string, number>();
	const visiting = new Set<string>();
	const of = (name: string): number => {
		const known = depth.get(name);
		if (known !== undefined) return known;
		if (visiting.has(name)) return 0; // cycle guard — the daemon's graph is a DAG
		visiting.add(name);
		const deps = (byName.get(name)?.depends_on ?? []).filter((d) => byName.has(d));
		const d = deps.length === 0 ? 0 : Math.max(...deps.map(of)) + 1;
		visiting.delete(name);
		depth.set(name, d);
		return d;
	};
	for (const m of models) of(m.name);
	return depth;
}

const schemaOf = (name: string): string => (name.includes('.') ? name.split('.')[0]! : 'main');

export interface ApiCatalogue {
	status: 'up' | 'down';
	models: { name: string; columns: Record<string, string[]>; sql: string | null }[];
	counts: Record<string, number>;
	schemas: Record<string, [string, string][]>;
	error?: string;
}

/** Column lineage from the daemon, or the warehouse schema when the daemon has none. */
function columnLineage(
	columns: Record<string, string[]>,
	names: Set<string>,
	schema: [string, string][]
): { out: string; srcModel: string; srcCol: string }[] {
	const lineageCols = Object.entries(columns).map(([out, refs]) => {
		const ref = refs[0];
		if (!ref) return { out, srcModel: 'api', srcCol: '—' };
		const split = ref.lastIndexOf('.');
		const srcModel = ref.slice(0, split);
		const srcCol = ref.slice(split + 1);
		return names.has(srcModel) ? { out, srcModel, srcCol } : { out, srcModel: 'api', srcCol: '—' };
	});
	return lineageCols.length
		? lineageCols
		: schema.map(([out]) => ({ out, srcModel: 'api', srcCol: '—' }));
}

/**
 * interlace promotes its default environment unprefixed, and that default is production — the
 * only environment the API reads. Mirrors `providers.promoted_relation`; there is no environment
 * prefix any more (the old DATALAKE_ENV knob only ever produced a `dev__` nothing promoted to).
 */
const promotedRelation = (schema: string, table: string): string => `${schema}.${table}`;

export const timeOf = (stamp: string | null | undefined): string =>
	stamp?.match(/\d{2}:\d{2}:\d{2}/)?.[0] ?? '—';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** Date and time, so a 09:19 today sorts after a 23:04 yesterday. */
export const formatRunWhen = (stamp: string | null | undefined): string => {
	if (!stamp) return '—';
	const match = stamp.match(/(\d{4})-(\d{2})-(\d{2})[T ](\d{2}:\d{2}:\d{2})/);
	if (!match) return timeOf(stamp);
	const month = MONTHS[Number(match[2]) - 1];
	return `${Number(match[3])} ${month} ${match[1]} ${match[4]}`;
};

const TRIGGERS: [prefix: string, trigger: RunTrigger][] = [
	['cron:', 'cron'],
	['interval:', 'cron'],
	['api:', 'api'],
	['stream:', 'stream'],
	['file:', 'file']
];

export function runTrigger(key: string | null): RunTrigger {
	for (const [prefix, trigger] of TRIGGERS) if (key?.startsWith(prefix)) return trigger;
	return 'manual';
}

const RUN_STATUS = new Set<RunStatus>([
	'running',
	'succeeded',
	'failed',
	'queued',
	'cancelling',
	'cancelled'
]);

const runStatus = (state: string): RunStatus =>
	RUN_STATUS.has(state as RunStatus) ? (state as RunStatus) : 'queued';

const EVENT_KIND: Record<string, FeedEventKind> = {
	'run.succeeded': 'succeeded',
	'run.started': 'started',
	'run.enqueued': 'enqueued',
	'run.failed': 'failed',
	'apply.started': 'flush',
	'apply.finished': 'flush',
	'apply.blocked': 'failed',
	'stream.flushed': 'flush',
	'gc.finished': 'flush'
};

function feedEntry(event: ApiEvent): FeedEvent {
	const models = event.payload?.models ?? event.payload?.built ?? [];
	const model = models[0] ?? event.entity ?? '';
	const rest = models.length > 1 ? ` (+${models.length - 1} more)` : '';
	const message =
		{
			'run.enqueued': `queued${rest}`,
			'run.started': `run started${rest}`,
			'run.succeeded': `run succeeded${rest}`,
			'run.failed': event.payload?.error ?? 'run failed',
			'apply.started': `apply started${rest}`,
			'apply.finished': `promoted${rest}`,
			'apply.blocked': event.payload?.error ?? 'apply blocked',
			'stream.flushed': `flushed ${event.payload?.rows ?? '?'} rows`
		}[event.type] ?? event.type;
	return { kind: EVENT_KIND[event.type] ?? 'enqueued', model, message, t: timeOf(event.ts) };
}

/**
 * Fold one daemon SSE event into the dataset the pages already render.
 *
 * Run terminals and model start/stop update in place. A full overview reload still
 * follows a finished run — row counts and the plan are not on the event.
 */
export function applyDaemonEvent(dataset: PlatformDataset, event: ApiEvent): PlatformDataset {
	const entity = event.entity ?? '';
	let models = dataset.models;
	let runs = dataset.runs;
	if (event.type === 'model.start' && entity) {
		models = models.map((m) => (m.name === entity ? { ...m, running: true } : m));
	} else if (event.type.startsWith('model.') && entity) {
		models = models.map((m) => (m.name === entity ? { ...m, running: false } : m));
	}
	const sameRun = (id: string) => id === entity || id === `r_${entity}`;
	if (event.type === 'run.cancel_requested' && /^\d+$/.test(entity)) {
		const next = event.payload?.state === 'cancelled' ? 'cancelled' : 'cancelling';
		runs = runs.map((r) => (sameRun(r.id) ? { ...r, status: next } : r));
	}
	if (event.type === 'run.started' && /^\d+$/.test(entity)) {
		runs = runs.map((r) => (sameRun(r.id) ? { ...r, status: 'running' } : r));
		const names = new Set(event.payload?.models ?? []);
		if (names.size) models = models.map((m) => (names.has(m.name) ? { ...m, running: true } : m));
	}
	if (
		(event.type === 'run.succeeded' ||
			event.type === 'run.failed' ||
			event.type === 'run.cancelled') &&
		/^\d+$/.test(entity)
	) {
		const status: RunStatus =
			event.type === 'run.cancelled'
				? 'cancelled'
				: event.type === 'run.failed'
					? 'failed'
					: 'succeeded';
		const error = event.payload?.error;
		runs = runs.map((r) =>
			sameRun(r.id)
				? {
						...r,
						status,
						...(error ? { error } : {})
					}
				: r
		);
		const named = event.payload?.models ?? runs.find((r) => sameRun(r.id))?.models ?? [];
		const names = new Set(named);
		models = models.map((m) => (names.has(m.name) ? { ...m, running: false } : m));
	}
	return {
		...dataset,
		models,
		runs,
		eventSeq: Math.max(dataset.eventSeq, event.seq),
		feedScript: [feedEntry(event), ...dataset.feedScript].slice(0, 40)
	};
}

const fmtCount = (n: number | undefined): string =>
	n === undefined ? '—' : n.toLocaleString('en-GB');

const parseTs = (ts: string): number =>
	Date.parse(ts.includes('T') ? ts : ts.replace(' ', 'T') + 'Z');

export const fmtSeconds = (seconds: number): string => {
	if (!Number.isFinite(seconds) || seconds < 0) return '—';
	if (seconds < 10) return `${seconds.toFixed(1)}s`;
	if (seconds < 60) return `${Math.round(seconds)}s`;
	return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
};

function durationFromApi(raw: unknown): string | null {
	if (typeof raw === 'number' && Number.isFinite(raw) && raw >= 0) return fmtSeconds(raw);
	if (typeof raw === 'string' && raw.trim()) {
		const n = Number(raw);
		if (Number.isFinite(n) && n >= 0) return fmtSeconds(n);
	}
	return null;
}

interface RunLifecycle {
	started?: number;
	finished?: number;
	timings?: Record<string, number>;
}

/** Per-run lifecycle gleaned from the event log: overall duration + per-model timings. */
function runLifecycles(events: ApiEvent[]): Map<string, RunLifecycle> {
	const map = new Map<string, RunLifecycle>();
	for (const e of events) {
		const id = e.entity ?? '';
		if (!/^\d+$/.test(id)) continue; // worker lifecycle events are keyed by run id
		const entry = map.get(id) ?? {};
		if (e.type === 'run.started') entry.started = parseTs(e.ts);
		if (e.type === 'run.succeeded' || e.type === 'run.failed') {
			entry.finished = parseTs(e.ts);
			entry.timings = e.payload?.timings;
		}
		map.set(id, entry);
	}
	return map;
}

// ---- per-provider presentation (the parts an overview payload can't tell us) ----

/**
 * How a provider presents, as the API serves it (`providers.PROVIDER_META`).
 *
 * NOT a table here any more. `LIVE_META` and `LIVE_PROVIDERS` lived in this file, alongside five
 * other hard-coded copies elsewhere in the app, and had already drifted from each other. The one
 * that mattered was the FALLBACK: an unlisted provider used to resolve to `LIVE_META.morgan_lloyd`,
 * so every SEI page carried Morgan Lloyd's name and description above SEI's own data and its own
 * "Send to…" list — which is the confusion that gets an instruction sent to the wrong custodian.
 * The API keeps that rule now (`providers.meta` names only what it knows); this is the shape.
 */
export interface LiveMeta {
	name: string;
	role: PlatformMeta['role'];
	description: string;
	transports: PlatformMeta['transports'];
	/** [label, warehouse relation] pairs for the entity KPIs. */
	kpis: [string, string][];
}

// ---- the mapper ----

export function mapLiveOverview(overview: ApiOverview): PlatformDataset {
	// Straight from the payload — there is no local table to fall back to, and no fallback to get
	// wrong. It used to be `LIVE_META[overview.provider] ?? LIVE_META.morgan_lloyd`, so every SEI
	// overview carried Morgan Lloyd's name, description and nav above SEI's own data and its own
	// "Send to…" list, which is the confusion that gets an instruction sent to the wrong
	// custodian. `providers.meta()` now owns the unnamed-beats-misnamed rule, once, server-side.
	const providerMeta: LiveMeta = overview.meta;
	const service = overview.service;
	const up = service.status === 'up';
	const apiModels = up ? service.models : [];
	const names = new Set(apiModels.map((m) => m.name));
	const depth = layers(apiModels);
	const labelled = new Set<number>();

	const models: LineageModel[] = [...apiModels]
		.sort((a, b) => depth.get(a.name)! - depth.get(b.name)! || a.name.localeCompare(b.name))
		.map((m) => {
			const layer = depth.get(m.name)!;
			const layerLabel = labelled.has(layer) ? undefined : schemaOf(m.name);
			labelled.add(layer);
			const running = up
				? service.runs.some((r) => r.state === 'running' && r.flow_selector.includes(m.name))
				: false;
			// No lineage (Python extractors, `SELECT *` views) => the warehouse schema.
			const cols = columnLineage(m.columns ?? {}, names, overview.schemas[m.name] ?? []);
			const schema = schemaOf(m.name);
			const table = m.name.includes('.') ? m.name.split('.').slice(1).join('.') : m.name;
			return {
				name: m.name,
				relation: promotedRelation(schema, table),
				layer,
				layerLabel,
				mat: mat(m),
				strategy: m.strategy || '—',
				state: planState(up ? service.plan[m.name] : undefined),
				running,
				fingerprint: m.fingerprint.slice(0, 8),
				rows: fmtCount(overview.counts[m.name]),
				schedule: m.schedule?.cron ?? m.schedule?.interval ?? '—',
				owner: m.owner ?? '—',
				deps: m.depends_on.filter((d) => names.has(d)),
				tags: m.tags ?? [],
				cols,
				// Warehouse DESCRIBE, carried through with types — the only column source a Python
				// extractor has, since the daemon compiles no lineage for one.
				schema: (overview.schemas[m.name] ?? []).map(([name, type]) => ({ name, type })),
				sql: m.sql ?? unloadedSql(m.name)
			};
		});

	// Joined onto run steps by name so a step shows what it was and what it depended on —
	// the daemon's run payload carries only the selector and a duration.
	const modelByName = new Map(apiModels.map((m) => [m.name, m]));
	const lifecycles = up ? runLifecycles(service.events) : new Map<string, RunLifecycle>();
	const runs: Run[] = up
		? [...service.runs]
				.sort((a, b) => {
					const later = (parseTs(b.enqueued_at ?? '') || 0) - (parseTs(a.enqueued_at ?? '') || 0);
					return later || b.id - a.id;
				})
				.map((r) => {
					const life = lifecycles.get(String(r.id));
					const fromEvents =
						life?.started !== undefined && life?.finished !== undefined
							? fmtSeconds((life.finished - life.started) / 1000)
							: null;
					return {
						id: `r_${r.id}`,
						trigger: runTrigger(r.idempotency_key),
						status: runStatus(r.state),
						started: formatRunWhen(r.enqueued_at),
						startedAt: r.enqueued_at ?? '',
						attempts: r.attempts,
						duration: fromEvents ?? durationFromApi(r.duration),
						models: r.flow_selector,
						// A selector matching no current model built nothing, however green the run looks.
						unknownModels: r.flow_selector.filter((m) => !modelByName.has(m)),
						steps: r.flow_selector.map((model) => {
							const seconds = life?.timings?.[model];
							const definition = modelByName.get(model);
							return {
								model,
								status: runStatus(r.state),
								duration: seconds !== undefined ? fmtSeconds(seconds) : null,
								strategy: definition?.strategy,
								output: definition?.output,
								dependsOn: definition?.depends_on,
								known: definition !== undefined
							};
						}),
						...(r.error ? { error: r.error } : {})
					};
				})
		: [];

	const events = up ? service.events : [];
	const lastSync = runs.find((r) => r.status === 'succeeded')?.startedAt ?? null;
	const enabled = overview.firms.filter((f) => f.enabled).length;
	const failing = up && runs[0]?.status === 'failed';
	const status: ConnectionStatus = !up ? 'error' : failing ? 'degraded' : 'connected';
	const detail = up
		? `Data service available · ${models.length} data sets${failing ? ` · latest run failed: ${runs[0]?.error ?? 'see runs'}` : ''}`
		: `Data service unavailable: ${service.error}`;

	// Webhook streams (Seccl): head vs watermark says whether deliveries are
	// flowing ahead of materialisation. Providers without streams get [].
	const streams: StreamInfo[] = up
		? (service.streams ?? []).map((s) => ({
				name: s.name,
				status: (s.head > 0 ? 'live' : 'idle') as StreamStatus,
				rate: '—',
				lag: s.head > s.watermark ? `${s.head - s.watermark} events behind` : 'caught up',
				retention: '—',
				target: s.table,
				offset: `head ${s.head}`,
				dlq: 0,
				schema: Object.entries(s.schema) as [string, string][],
				log: []
			}))
		: [];

	return {
		meta: {
			provider: overview.provider,
			name: providerMeta.name,
			role: providerMeta.role,
			description: providerMeta.description,
			transports: providerMeta.transports,
			connection: { status, since: '', detail },
			kpis: [
				...providerMeta.kpis.map(([label, key]) => ({
					label,
					value: fmtCount(overview.counts[key])
				})),
				{ label: 'Enabled firms', value: String(enabled) },
				{ label: 'Last sync', value: formatRunWhen(lastSync), hint: 'daily 02:00' }
			]
		},
		models,
		runs,
		streams,
		feeds: [],
		feedScript: events.map(feedEntry),
		eventSeq: events.reduce((max, event) => Math.max(max, event.seq), 0),
		live: true,
		firms: {
			provider: overview.provider,
			sources: overview.sources,
			enablements: overview.firms
		},
		candidates: overview.candidates ?? [],
		metrics: {
			// `?? null` not `?? 0`: a relation that hasn't been promoted must render "—", because
			// "no clients" and "no data yet" mean very different things to whoever is looking.
			clients: overview.metrics?.clients ?? null,
			aum: overview.metrics?.aum ?? null,
			tenants: overview.metrics?.tenants ?? 0,
			runsPerDay: overview.metrics?.runs_per_day ?? [],
			lastRunAt: overview.metrics?.last_run_at ?? null,
			nextRunAt: overview.metrics?.next_run_at ?? null
		}
	};
}

/** The overview's stand-in when the daemon has not sent SQL yet. Not a real definition. */
const unloadedSql = (name: string): string =>
	`-- Definition is managed by the provider service\n-- ${name}`;

/**
 * Fill SQL, column lineage and row counts onto a dataset the overview mapped without them.
 * The overview returns before the catalogue scan; Models and Lineage apply this when it arrives.
 */
export function enrichDataset(dataset: PlatformDataset, catalogue: ApiCatalogue): PlatformDataset {
	const details = new Map(catalogue.models.map((model) => [model.name, model]));
	const names = new Set(dataset.models.map((model) => model.name));
	const models = dataset.models.map((model) => {
		const detail = details.get(model.name);
		const schema = catalogue.schemas[model.name] ?? [];
		const cols = columnLineage(detail?.columns ?? {}, names, schema);
		return {
			...model,
			cols: cols.length ? cols : model.cols,
			schema: schema.length ? schema.map(([name, type]) => ({ name, type })) : model.schema,
			sql: detail?.sql ?? model.sql,
			rows: model.name in catalogue.counts ? fmtCount(catalogue.counts[model.name]) : model.rows
		};
	});
	return { ...dataset, models };
}

/**
 * A reload maps a fresh overview before the catalogue request returns, and that overview has
 * empty lineage, no schema and a placeholder definition. Keep the details already on screen for
 * each model that is still there, so a failed or `down` catalogue does not replace them with
 * dashes. A later `enrichDataset` overwrites whatever the new scan actually published.
 */
export function retainCatalogue(previous: PlatformDataset, next: PlatformDataset): PlatformDataset {
	const prior = new Map(previous.models.map((model) => [model.name, model]));
	return {
		...next,
		models: next.models.map((model) => {
			const old = prior.get(model.name);
			if (!old) return model;
			return {
				...model,
				cols: model.cols.length ? model.cols : old.cols,
				schema: model.schema?.length ? model.schema : old.schema,
				sql: model.sql === unloadedSql(model.name) ? old.sql : model.sql,
				rows: model.rows === '—' ? old.rows : model.rows
			};
		})
	};
}

/**
 * A structurally valid, entirely empty dataset — what the store holds when the overview could not
 * be loaded.
 *
 * The layout used to fall back to `getPlatform(slug)`, a checked-in FIXTURE. With the daemon
 * unreachable, `/platforms/sei/models` then listed `sftp.dsr_accounts` with "1,696" rows, `/runs`
 * showed fabricated runs, and the model drawer rendered PRNG-generated client names — all beneath
 * one banner on the layout that no section repeated. Empty is the honest shape: the page renders
 * its chrome and says what failed.
 */
export function emptyDataset(slug: string): PlatformDataset {
	return {
		meta: {
			provider: slug,
			name: slug.replace(/-/g, ' '),
			role: 'custodian',
			description: '',
			transports: [],
			connection: { status: 'error', since: '', detail: '' },
			kpis: []
		},
		models: [],
		runs: [],
		streams: [],
		feeds: [],
		feedScript: [],
		eventSeq: 0,
		live: true,
		firms: { provider: slug, sources: [], enablements: [] },
		candidates: [],
		metrics: {
			clients: null,
			aum: null,
			tenants: 0,
			runsPerDay: [],
			lastRunAt: null,
			nextRunAt: null
		}
	};
}
