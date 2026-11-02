import { describe, expect, it, vi } from 'vitest';
import { createRum, watchHidden } from './session';

const registered = vi.hoisted(() => [] as { name: string; reportAllChanges?: boolean }[]);

vi.mock('web-vitals', () => ({
	onLCP: (_cb: unknown, opts?: { reportAllChanges?: boolean }) => {
		registered.push({ name: 'lcp', reportAllChanges: opts?.reportAllChanges });
	},
	onINP: (_cb: unknown, opts?: { reportAllChanges?: boolean }) => {
		registered.push({ name: 'inp', reportAllChanges: opts?.reportAllChanges });
	},
	onCLS: (_cb: unknown, opts?: { reportAllChanges?: boolean }) => {
		registered.push({ name: 'cls', reportAllChanges: opts?.reportAllChanges });
	},
	onTTFB: (_cb: unknown, opts?: { reportAllChanges?: boolean }) => {
		registered.push({ name: 'ttfb', reportAllChanges: opts?.reportAllChanges });
	}
}));

const trace = 'ab'.repeat(16);
const span = 'cd'.repeat(8);

function harness() {
	const sent: string[] = [];
	let clock = 1000;
	const rum = createRum('platform', {
		send: (body) => {
			sent.push(body);
		},
		now: () => clock
	});
	return {
		rum,
		sent,
		at(t: number) {
			clock = t;
		}
	};
}

describe('createRum', () => {
	it('opens the first view on enter and includes vitals from the start of the page', () => {
		const { rum, sent, at } = harness();
		at(40);
		rum.note('lcp')({ value: 180, id: 'v1' } as never);
		rum.after(
			{ type: 'enter', to: { route: { id: '/clients/[uid]' } } },
			{ traceId: trace, spanId: span }
		);
		at(90);
		rum.hide();
		expect(JSON.parse(sent[0])).toEqual({
			trace_id: trace,
			span_id: span,
			route: '/clients/[uid]',
			portal: 'platform',
			settle_ms: 40,
			lcp_ms: 180
		});
	});

	it('drops vitals from the previous view when the next navigation starts', () => {
		const { rum, sent, at } = harness();
		at(10);
		rum.note('lcp')({ value: 180 } as never);
		rum.after({ type: 'enter', to: { route: { id: '/' } } }, { traceId: trace, spanId: span });
		at(50);
		rum.before();
		at(80);
		rum.note('inp')({ value: 12 } as never);
		rum.after(
			{ type: 'link', to: { route: { id: '/clients/[uid]' } } },
			{ traceId: trace, spanId: span }
		);
		at(100);
		rum.hide();
		const first = JSON.parse(sent[0]);
		const second = JSON.parse(sent[1]);
		expect(first.lcp_ms).toBe(180);
		expect(second.settle_ms).toBe(30);
		expect(second.lcp_ms).toBeUndefined();
		expect(second.inp_ms).toBe(12);
	});

	it('keeps the landing view when enter arrives with no route id', () => {
		const { rum, sent, at } = harness();
		at(40);
		expect(rum.ensureEnter('/', { traceId: trace, spanId: span })).toBe(true);
		rum.after({ type: 'enter', to: { route: { id: null } } }, { traceId: trace, spanId: span });
		expect(sent).toEqual([]);
		at(90);
		rum.hide();
		expect(JSON.parse(sent[0]).route).toBe('/');
		expect(JSON.parse(sent[0]).settle_ms).toBe(40);
	});

	it('opens the landing view from mount when the enter callback was missed', () => {
		const { rum, sent, at } = harness();
		at(40);
		rum.ensureEnter('/', { traceId: trace, spanId: span });
		expect(rum.ensureEnter('/', { traceId: trace, spanId: span })).toBe(false);
		at(50);
		rum.before();
		at(80);
		rum.after({ type: 'link', to: { route: { id: '/team' } } }, { traceId: trace, spanId: span });
		// No LCP candidate yet, so the enter view waits for hide rather than going out without it.
		expect(sent).toEqual([]);
		at(90);
		rum.hide();
		const views = sent.map((body) => JSON.parse(body) as { route: string; settle_ms: number });
		expect(views.map((view) => view.route)).toEqual(['/', '/team']);
		expect(views[0]?.settle_ms).toBe(40);
		expect(views[1]?.settle_ms).toBe(30);
	});

	it('sends nothing for a missing trace and does not send the same view twice', () => {
		const { rum, sent } = harness();
		rum.after(
			{ type: 'enter', to: { route: { id: '/login' } } },
			{ traceId: 'nope', spanId: span }
		);
		rum.hide();
		expect(sent).toEqual([]);

		rum.after({ type: 'enter', to: { route: { id: '/' } } }, { traceId: trace, spanId: span });
		rum.hide();
		rum.hide();
		expect(sent).toHaveLength(1);
	});

	it('sends the enter view with the final CLS, INP and the settle time from navigation', () => {
		const { rum, sent, at } = harness();
		at(400);
		rum.after(
			{ type: 'enter', to: { route: { id: '/clients/[uid]' } } },
			{ traceId: trace, spanId: span }
		);
		at(2200);
		rum.note('lcp')({ value: 2200 } as never);
		at(2300);
		rum.note('cls')({ value: 0.04 } as never);
		at(2400);
		rum.note('cls')({ value: 0.11 } as never);
		at(2500);
		rum.note('inp')({ value: 48 } as never);
		at(5000);
		rum.hide();
		expect(JSON.parse(sent[0])).toEqual({
			trace_id: trace,
			span_id: span,
			route: '/clients/[uid]',
			portal: 'platform',
			settle_ms: 400,
			lcp_ms: 2200,
			cls: 0.11,
			inp_ms: 48
		});
	});

	it('sends a zero CLS and omits INP when nothing was interacted with', () => {
		const { rum, sent } = harness();
		rum.after({ type: 'enter', to: { route: { id: '/' } } }, { traceId: trace, spanId: span });
		rum.note('cls')({ value: 0 } as never);
		rum.hide();
		const body = JSON.parse(sent[0]) as { cls: number; inp_ms?: number; lcp_ms?: number };
		expect(body.cls).toBe(0);
		expect(body.inp_ms).toBeUndefined();
		expect(body.lcp_ms).toBeUndefined();
	});

	it('keeps a late LCP on the enter view and off the soft navigation', () => {
		const { rum, sent, at } = harness();
		at(20);
		rum.note('ttfb')({ value: 80 } as never);
		at(40);
		rum.after({ type: 'enter', to: { route: { id: '/' } } }, { traceId: trace, spanId: span });
		at(60);
		rum.note('cls')({ value: 0.01 } as never);
		at(80);
		rum.before();
		at(100);
		rum.after({ type: 'link', to: { route: { id: '/team' } } }, { traceId: trace, spanId: span });
		expect(sent).toEqual([]);
		at(200);
		rum.note('cls')({ value: 0.2 } as never);
		at(2200);
		rum.note('lcp')({ value: 2200 } as never);
		at(2300);
		rum.note('inp')({ value: 30 } as never);
		rum.hide();
		const enter = JSON.parse(sent[0]);
		const soft = JSON.parse(sent[1]);
		expect(enter).toMatchObject({
			route: '/',
			settle_ms: 40,
			lcp_ms: 2200,
			cls: 0.01,
			ttfb_ms: 80
		});
		expect(enter.inp_ms).toBeUndefined();
		expect(soft.lcp_ms).toBeUndefined();
		expect(soft.ttfb_ms).toBeUndefined();
		expect(soft.cls).toBe(0.2);
		expect(soft.inp_ms).toBe(30);
		expect(soft.settle_ms).toBe(20);
	});

	it('does not move a later LCP candidate onto the soft view once the enter view was sent', () => {
		const { rum, sent, at } = harness();
		at(10);
		rum.note('lcp')({ value: 1800 } as never);
		rum.after({ type: 'enter', to: { route: { id: '/' } } }, { traceId: trace, spanId: span });
		at(50);
		rum.before();
		at(80);
		rum.after({ type: 'link', to: { route: { id: '/team' } } }, { traceId: trace, spanId: span });
		at(90);
		rum.note('lcp')({ value: 2500 } as never);
		rum.hide();
		expect(JSON.parse(sent[0]).lcp_ms).toBe(1800);
		expect(JSON.parse(sent[1]).lcp_ms).toBeUndefined();
	});

	it('asks web-vitals for every LCP, INP and CLS change, once', () => {
		const { rum } = harness();
		rum.startVitals();
		rum.startVitals();
		expect(registered).toEqual([
			{ name: 'lcp', reportAllChanges: true },
			{ name: 'inp', reportAllChanges: true },
			{ name: 'cls', reportAllChanges: true },
			{ name: 'ttfb', reportAllChanges: undefined }
		]);
	});
});

function fakeDocument() {
	let visibilityState = 'visible';
	const stored: {
		type: string;
		fn: () => void;
		capture: boolean;
		target: 'window' | 'document';
	}[] = [];
	const listen =
		(target: 'window' | 'document') =>
		(type: string, fn: () => void, options?: boolean | { capture?: boolean }) => {
			const capture = options === true || (typeof options === 'object' && !!options.capture);
			stored.push({ type, fn, capture, target });
		};
	const run = (type: string, target: 'window' | 'document', capture: boolean) => {
		for (const listener of stored) {
			if (listener.type === type && listener.target === target && listener.capture === capture) {
				listener.fn();
			}
		}
	};
	return {
		document: {
			get visibilityState() {
				return visibilityState;
			},
			addEventListener: listen('document')
		},
		window: {
			get visibilityState() {
				return visibilityState;
			},
			addEventListener: listen('window')
		},
		pagehide() {
			run('pagehide', 'window', true);
			run('pagehide', 'window', false);
		},
		hide() {
			visibilityState = 'hidden';
			run('visibilitychange', 'window', true);
			run('visibilitychange', 'document', true);
			run('visibilitychange', 'document', false);
			run('visibilitychange', 'window', false);
		},
		conceal() {
			visibilityState = 'hidden';
		}
	};
}

describe('watchHidden', () => {
	it('flushes on hide after the capturing vital listener, not on a still-visible pagehide', () => {
		const { rum, sent, at } = harness();
		at(400);
		rum.after(
			{ type: 'enter', to: { route: { id: '/clients/[uid]' } } },
			{ traceId: trace, spanId: span }
		);
		at(2200);
		rum.note('lcp')({ value: 2200 } as never);
		at(2300);
		rum.note('cls')({ value: 0.04 } as never);
		const dom = fakeDocument();
		watchHidden(() => rum.hide(), dom);
		// Registered after ours. Capture on window still runs before the document listener.
		dom.window.addEventListener(
			'visibilitychange',
			() => {
				rum.note('cls')({ value: 0.11 } as never);
				rum.note('inp')({ value: 48 } as never);
			},
			{ capture: true }
		);
		at(5000);
		dom.pagehide();
		expect(sent).toEqual([]);
		dom.hide();
		expect(JSON.parse(sent[0])).toEqual({
			trace_id: trace,
			span_id: span,
			route: '/clients/[uid]',
			portal: 'platform',
			settle_ms: 400,
			lcp_ms: 2200,
			cls: 0.11,
			inp_ms: 48
		});
	});

	it('sends on pagehide when the document is already hidden', () => {
		const { rum, sent, at } = harness();
		at(40);
		rum.after({ type: 'enter', to: { route: { id: '/' } } }, { traceId: trace, spanId: span });
		at(80);
		rum.note('lcp')({ value: 100 } as never);
		const dom = fakeDocument();
		watchHidden(() => rum.hide(), dom);
		dom.conceal();
		dom.pagehide();
		expect(JSON.parse(sent[0]).lcp_ms).toBe(100);
		expect(JSON.parse(sent[0]).settle_ms).toBe(40);
	});
});
