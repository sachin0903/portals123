import { describe, expect, it } from 'vitest';
import {
	applyDaemonEvent,
	enrichDataset,
	mapLiveOverview,
	retainCatalogue,
	runTrigger,
	timeOf,
	formatRunWhen,
	type ApiOverview
} from './live';
import { RUN_TRIGGER_LABEL, STATUS_TONE } from './status';
import { PlatformStore } from './store.svelte';

function overview(): ApiOverview {
	return {
		provider: 'morgan_lloyd',
		// The registry travels ON the payload now — `providers.PROVIDER_META`, server-side. This
		// file used to rely on `LIVE_META`, a local copy, so the test and the page could agree
		// with each other while both disagreed with the API.
		meta: {
			name: 'Morgan Lloyd',
			role: 'scheme_administrator',
			description: 'SIPP scheme administrator.',
			transports: ['api'],
			kpis: [
				['Contacts', 'platform.clients'],
				['Transfers', 'platform.transfers']
			]
		},
		service: {
			status: 'up',
			models: [
				{
					name: 'raw.contacts',
					output: 'table',
					materialise: 'table',
					strategy: 'full_merge',
					fingerprint: 'abcdef1234567890',
					depends_on: [],
					owner: 'data-eng',
					schedule: { cron: '0 2 * * *' },
					columns: {},
					sql: null
				},
				{
					name: 'raw.adviser_contacts',
					output: 'table',
					materialise: 'table',
					strategy: 'full_merge',
					fingerprint: 'bbbb1234',
					depends_on: [],
					owner: 'data-eng',
					schedule: { cron: '0 2 * * *' },
					columns: {},
					sql: null
				},
				{
					name: 'platform.clients',
					output: 'view',
					materialise: 'view',
					strategy: 'view',
					fingerprint: 'cccc1234',
					depends_on: ['raw.contacts', 'raw.adviser_contacts'],
					owner: null,
					schedule: null,
					columns: {
						contact_id: ['raw.contacts.contactid'],
						source: [] // literal — no upstream ref
					},
					sql: 'select contactid as contact_id from raw.contacts'
				}
			],
			plan: {
				'platform.clients': {
					name: 'platform.clients',
					change_type: 'modified',
					category: 'nonbreaking'
				}
			},
			runs: [
				{
					id: 7,
					flow_selector: ['raw.contacts', 'platform.clients'],
					state: 'succeeded',
					attempts: 1,
					error: null,
					enqueued_at: '2026-07-19 02:00:03',
					idempotency_key: 'cron:raw.contacts:2026-07-19T02:00:00'
				},
				{
					id: 8,
					flow_selector: ['platform.clients'],
					state: 'running',
					attempts: 1,
					error: null,
					enqueued_at: '2026-07-19 09:15:00',
					idempotency_key: 'api:dev:deadbeef'
				}
			],
			events: [
				{
					seq: 3,
					ts: '2026-07-19 02:00:41',
					type: 'run.succeeded',
					entity: '7',
					payload: {
						models: ['raw.contacts', 'platform.clients'],
						timings: { 'raw.contacts': 5.3, 'platform.clients': 0.8 }
					}
				},
				{
					seq: 2,
					ts: '2026-07-19 02:00:03',
					type: 'run.started',
					entity: '7',
					payload: { models: ['raw.contacts', 'platform.clients'] }
				},
				{ seq: 1, ts: '2026-07-19 02:00:03', type: 'run.enqueued', entity: 'cron:…', payload: null }
			],
			streams: []
		},
		counts: { 'raw.contacts': 9, 'platform.clients': 9 },
		schemas: {
			'raw.contacts': [
				['contactid', 'VARCHAR'],
				['fullname', 'VARCHAR'],
				['ml_tenant_id', 'VARCHAR']
			]
		},
		sources: [{ external_id: 'ms-guid-1', name: 'Clifton (dev)' }],
		firms: [
			{
				tenant_id: 3,
				uid: 'ten_ab12',
				name: 'ML E2E Firm',
				alias: 'ml-e2e-firm',
				config: { tenant_ids: ['ms-guid-1'] },
				enabled: true,
				last_fanout_at: '2026-07-17T12:00:00Z'
			}
		]
	};
}

describe('mapLiveOverview', () => {
	const dataset = mapLiveOverview(overview());

	it('satisfies the dataset invariants the canvas relies on', () => {
		const names = new Set(dataset.models.map((m) => m.name));
		expect(names.size).toBe(dataset.models.length);
		const layer = new Map(dataset.models.map((m) => [m.name, m.layer]));
		for (const m of dataset.models) {
			for (const dep of m.deps) {
				expect(names).toContain(dep);
				expect(layer.get(dep)!).toBeLessThan(m.layer);
			}
			for (const c of m.cols) {
				expect(names.has(c.srcModel) || c.srcModel === 'api').toBe(true);
			}
		}
		const layers = [...new Set(dataset.models.map((m) => m.layer))].sort((a, b) => a - b);
		expect(layers).toEqual(layers.map((_, i) => i));
		for (const l of layers) {
			expect(dataset.models.some((m) => m.layer === l && m.layerLabel)).toBe(true);
		}
		for (const status of [
			dataset.meta.connection.status,
			...dataset.runs.flatMap((r) => [r.status, ...r.steps.map((s) => s.status)])
		]) {
			expect(STATUS_TONE[status]).toBeDefined();
		}
		// And the store can be constructed over it.
		const store = new PlatformStore(dataset);
		expect(store.selectedModel?.name).toBe('platform.clients');
	});

	it('fills SQL and row counts onto an overview that did not wait for them', () => {
		const source = overview();
		const up = source.service as Extract<ApiOverview['service'], { status: 'up' }>;
		const light = mapLiveOverview({
			...source,
			counts: {},
			schemas: {},
			service: {
				...up,
				models: up.models.map((model) => ({ ...model, columns: {}, sql: null }))
			}
		});
		expect(light.models.find((model) => model.name === 'platform.clients')?.rows).toBe('—');
		const enriched = enrichDataset(light, {
			status: 'up',
			models: [
				{
					name: 'platform.clients',
					columns: { contact_id: ['raw.contacts.contactid'] },
					sql: 'select contactid as contact_id from raw.contacts'
				}
			],
			counts: { 'platform.clients': 9 },
			schemas: { 'raw.contacts': [['contactid', 'VARCHAR']] }
		});
		const clients = enriched.models.find((model) => model.name === 'platform.clients')!;
		expect(clients.rows).toBe('9');
		expect(clients.sql).toContain('select contactid');
		expect(clients.cols).toContainEqual({
			out: 'contact_id',
			srcModel: 'raw.contacts',
			srcCol: 'contactid'
		});
		const raw = enriched.models.find((model) => model.name === 'raw.contacts')!;
		expect(raw.cols.map((col) => col.out)).toEqual(['contactid']);
		expect(raw.rows).toBe('—');
	});

	it('keeps catalogue details when a reload arrives without them', () => {
		const source = overview();
		const up = source.service as Extract<ApiOverview['service'], { status: 'up' }>;
		const light = mapLiveOverview({
			...source,
			counts: {},
			schemas: {},
			service: {
				...up,
				models: up.models.map((model) => ({ ...model, columns: {}, sql: null }))
			}
		});
		const enriched = enrichDataset(light, {
			status: 'up',
			models: [
				{
					name: 'platform.clients',
					columns: { contact_id: ['raw.contacts.contactid'] },
					sql: 'select contactid as contact_id from raw.contacts'
				}
			],
			counts: { 'platform.clients': 9 },
			schemas: { 'raw.contacts': [['contactid', 'VARCHAR']] }
		});
		const kept = retainCatalogue(enriched, light);
		const clients = kept.models.find((model) => model.name === 'platform.clients')!;
		expect(clients.rows).toBe('9');
		expect(clients.sql).toContain('select contactid');
		expect(clients.cols).toContainEqual({
			out: 'contact_id',
			srcModel: 'raw.contacts',
			srcCol: 'contactid'
		});
		const raw = kept.models.find((model) => model.name === 'raw.contacts')!;
		expect(raw.cols.map((col) => col.out)).toEqual(['contactid']);

		const store = new PlatformStore(enriched);
		store.replace(light);
		expect(store.data.models.find((model) => model.name === 'platform.clients')?.sql).toContain(
			'select contactid'
		);
	});

	it('maps layers, plan states, running flags and warehouse relations', () => {
		const consumption = dataset.models.find((m) => m.name === 'platform.clients')!;
		expect(consumption.layer).toBe(1);
		expect(consumption.state).toBe('nonbreaking');
		expect(consumption.running).toBe(true); // run 8 is running over it
		// Relations are unconditionally bare: production promotes unprefixed, and production is
		// the only environment the API reads.
		expect(consumption.relation).toBe('platform.clients');
		expect(consumption.rows).toBe('9');
		expect(consumption.cols).toContainEqual({
			out: 'contact_id',
			srcModel: 'raw.contacts',
			srcCol: 'contactid'
		});
		expect(consumption.cols).toContainEqual({ out: 'source', srcModel: 'api', srcCol: '—' });
		const raw = dataset.models.find((m) => m.name === 'raw.contacts')!;
		expect(raw.layer).toBe(0);
		const label = dataset.models.find((m) => m.layer === 0 && m.layerLabel)?.layerLabel;
		expect(label).toBe('raw');
		expect(raw.state).toBe('unchanged');
		expect(raw.schedule).toBe('0 2 * * *');
		expect(raw.sql).toContain('Definition is managed by the provider service');
		// No lineage from the daemon (Python extractor) => warehouse schema columns.
		expect(raw.cols.map((c) => c.out)).toEqual(['contactid', 'fullname', 'ml_tenant_id']);
		expect(raw.cols[0]).toEqual({ out: 'contactid', srcModel: 'api', srcCol: '—' });
	});

	it('flags a run whose selector matches no model — it built nothing', () => {
		// Exactly the live defect: the Seccl cron enqueues `raw_notifications` while the model is
		// `raw.notifications`, so the run reports success having built nothing. Without the flag the
		// only symptom is a run list full of names that do not exist.
		const stale = mapLiveOverview({
			...overview(),
			service: {
				...(overview().service as { status: 'up' } & Record<string, unknown>),
				runs: [
					{
						id: 99,
						flow_selector: ['raw_notifications'],
						state: 'succeeded',
						attempts: 1,
						error: null,
						enqueued_at: '2026-07-27 06:00:00',
						idempotency_key: 'cron:raw_notifications:2026-07-27T06:00:00'
					}
				]
			}
		} as Parameters<typeof mapLiveOverview>[0]);
		const run = stale.runs[0]!;
		expect(run.unknownModels).toEqual(['raw_notifications']);
		expect(run.steps[0]!.known).toBe(false);
		// The daemon still calls it a success — the flag is the only signal.
		expect(run.status).toBe('succeeded');
	});

	it('maps runs with triggers from the enqueue key and real timestamps', () => {
		expect(dataset.runs.map((r) => r.id)).toEqual(['r_8', 'r_7']);
		expect(dataset.runs[0]!.trigger).toBe('api');
		expect(dataset.runs[1]!.trigger).toBe('cron');
		expect(dataset.runs[1]!.started).toBe('19 Jul 2026 02:00:03');
		expect(dataset.runs[0]!.started).toBe('19 Jul 2026 09:15:00');
		expect(dataset.runs[1]!.steps).toHaveLength(2);
	});

	it('derives run durations from lifecycle events and step timings/strategies', () => {
		const run = dataset.runs.find((r) => r.id === 'r_7')!;
		expect(run.duration).toBe('38s'); // run.started 02:00:03 -> run.succeeded 02:00:41
		// Steps are enriched from the compiled graph — the daemon's run payload has only the
		// selector and a duration, so output/depends-on/known are joined on by name.
		expect(run.steps[0]).toMatchObject({
			model: 'raw.contacts',
			status: 'succeeded',
			duration: '5.3s',
			strategy: 'full_merge',
			output: 'table',
			dependsOn: [],
			known: true
		});
		expect(run.steps[1]!.duration).toBe('0.8s');
		expect(run.steps[1]!.strategy).toBe('view');
		// The running run has no lifecycle end yet.
		expect(dataset.runs.find((r) => r.id === 'r_8')!.duration).toBeNull();
	});

	it('maps the event log to timestamped feed entries', () => {
		expect(dataset.feedScript[0]).toEqual({
			kind: 'succeeded',
			model: 'raw.contacts',
			message: 'run succeeded (+1 more)',
			t: '02:00:41'
		});
		// The live feed resumes after this, rather than replaying the log.
		expect(dataset.eventSeq).toBe(3);
	});

	it('carries KPIs, firms and sources through', () => {
		expect(dataset.meta.kpis.find((k) => k.label === 'Contacts')?.value).toBe('9');
		expect(dataset.meta.kpis.find((k) => k.label === 'Enabled firms')?.value).toBe('1');
		expect(dataset.live).toBe(true);
		expect(dataset.firms?.sources[0]?.name).toBe('Clifton (dev)');
		expect(dataset.firms?.enablements[0]?.alias).toBe('ml-e2e-firm');
		expect(dataset.meta.connection.status).toBe('connected');
	});

	it('maps a seccl overview: provider meta + webhook streams', () => {
		const o = overview();
		o.provider = 'seccl';
		// The whole point: the mapper takes the payload's word for who this is. It used to look
		// the provider up locally and fall back to Morgan Lloyd's metadata for an unlisted one.
		o.meta = {
			name: 'Seccl',
			role: 'custodian',
			description: 'Custodian.',
			transports: ['api', 'webhooks'],
			kpis: [['Clients', 'platform.clients']]
		};
		if (o.service.status === 'up') {
			o.service.streams = [
				{
					name: 'transactions',
					schema: { _event_key: 'text', id: 'text' },
					table: 'streams.transactions',
					head: 12,
					watermark: 10
				},
				{
					name: 'clients',
					schema: { client_id: 'text' },
					table: 'streams.clients',
					head: 0,
					watermark: 0
				}
			];
		}
		const mapped = mapLiveOverview(o);
		expect(mapped.meta.name).toBe('Seccl');
		expect(mapped.meta.kpis.map((k) => k.label)).toContain('Clients');
		const txns = mapped.streams.find((s) => s.name === 'transactions')!;
		expect(txns.status).toBe('live');
		expect(txns.lag).toBe('2 events behind');
		expect(txns.schema).toContainEqual(['id', 'text']);
		expect(mapped.streams.find((s) => s.name === 'clients')?.status).toBe('idle');
	});

	it('degrades to an error connection (empty graph, firms intact) when the daemon is down', () => {
		const down = overview();
		down.service = { status: 'down', error: 'connect ECONNREFUSED' };
		const mapped = mapLiveOverview(down);
		expect(mapped.meta.connection.status).toBe('error');
		expect(mapped.meta.connection.detail).toContain('ECONNREFUSED');
		expect(mapped.models).toEqual([]);
		expect(mapped.runs).toEqual([]);
		expect(mapped.firms?.enablements).toHaveLength(1);
		// The store still constructs over an empty graph.
		expect(new PlatformStore(mapped).selected).toBe('');
	});
});

describe('daemon events', () => {
	it('marks the model that just started and records a cancelled run', () => {
		const dataset = mapLiveOverview(overview());
		const started = applyDaemonEvent(dataset, {
			seq: 9,
			ts: '2026-07-19 09:15:01',
			type: 'model.start',
			entity: 'platform.clients',
			payload: null
		});
		expect(started.models.find((m) => m.name === 'platform.clients')?.running).toBe(true);
		expect(started.feedScript[0]?.model).toBe('platform.clients');

		const cancelled = applyDaemonEvent(started, {
			seq: 10,
			ts: '2026-07-19 09:16:00',
			type: 'run.cancelled',
			entity: '8',
			payload: null
		});
		const run = cancelled.runs.find((r) => r.id === 'r_8');
		expect(run?.status).toBe('cancelled');
		expect(cancelled.models.find((m) => m.name === 'platform.clients')?.running).toBe(false);
		expect(cancelled.eventSeq).toBe(10);
	});
});

describe('helpers', () => {
	it('derives triggers from key prefixes', () => {
		expect(runTrigger('cron:m:2026')).toBe('cron');
		expect(runTrigger('interval:m:2026')).toBe('cron');
		expect(runTrigger('api:dev:x')).toBe('api');
		expect(runTrigger('stream:clicks:9')).toBe('stream');
		expect(runTrigger(null)).toBe('manual');
	});

	it('names acronym triggers explicitly', () => {
		expect(RUN_TRIGGER_LABEL.api).toBe('API');
	});

	it('extracts display times from mixed timestamp formats', () => {
		expect(timeOf('2026-07-19 02:00:03')).toBe('02:00:03');
		expect(timeOf('2026-07-19T02:00:03.123Z')).toBe('02:00:03');
		expect(timeOf(null)).toBe('—');
		expect(formatRunWhen('2026-07-19 02:00:03')).toBe('19 Jul 2026 02:00:03');
		expect(formatRunWhen('2026-09-23T22:04:11Z')).toBe('23 Sep 2026 22:04:11');
	});
});
