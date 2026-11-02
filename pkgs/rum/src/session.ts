/**
 * The page-view beacon's state. Navigation hooks stay in `RumBeacon.svelte`: SvelteKit
 * implements them with `onMount`, which throws outside a component.
 */

import { onCLS, onINP, onLCP, onTTFB, type Metric } from 'web-vitals';
import { beaconBody } from './payload';

export type RumPortal = 'client' | 'platform' | 'admin';

type VitalName = 'lcp' | 'inp' | 'cls' | 'ttfb';

type Sample = { name: VitalName; value: number; at: number };

type OpenView = {
	route: string;
	traceId: string;
	spanId: string;
	settle: number;
	since: number;
	/** The document load. LCP is measured once per hard navigation, so only this view carries it. */
	enter: boolean;
	/**
	 * Set when this view was left before any LCP candidate. Samples at or after this belong to
	 * later views. LCP is exempt: a soft navigation does not remeasure it, so it still lands here.
	 */
	until?: number;
};

const TRACE = /^[0-9a-f]{32}$/;
const SPAN = /^[0-9a-f]{16}$/;

export type RumNav = {
	type: string;
	to?: { route: { id: string | null } | null } | null;
};

export type RumPageData = { traceId?: unknown; spanId?: unknown };

/** Web vitals observe the document, so one registration covers every view. */
let vitalsOn = false;

type HideTarget = {
	visibilityState: string;
	addEventListener: (type: string, listener: () => void) => void;
};

/**
 * Flush once the document is hidden, after web-vitals has reported.
 *
 * web-vitals 5 finalises LCP, INP and CLS from a capturing `visibilitychange` listener on
 * `window`. It does not listen for `pagehide`, and `pagehide` can run while the document is
 * still visible — before that report. Sending then would end the view and drop the callback.
 * A `pagehide` that finds the document already hidden is the fallback for a browser that
 * skips `visibilitychange`.
 */
export function watchHidden(
	hide: () => void,
	scope?: { document: HideTarget; window: HideTarget }
) {
	const doc = scope?.document ?? (document as unknown as HideTarget);
	const win = scope?.window ?? (window as unknown as HideTarget);
	const flush = () => {
		if (doc.visibilityState === 'hidden') hide();
	};
	doc.addEventListener('visibilitychange', flush);
	win.addEventListener('pagehide', flush);
}

export function createRum(
	portal: RumPortal | (() => RumPortal),
	options?: { send?: (body: string) => void; now?: () => number }
) {
	const currentPortal = () => (typeof portal === 'function' ? portal() : portal);
	const now = options?.now ?? (() => performance.now());
	const send =
		options?.send ??
		((body: string) => {
			navigator.sendBeacon('/rum', new Blob([body], { type: 'application/json' }));
		});

	// Every report, not only the latest. A later shift must not erase the value that belonged
	// to a view we have not sent yet.
	const samples: Sample[] = [];
	let open: OpenView | null = null;
	let held: OpenView | null = null;
	let navStart = 0;

	const note = (name: VitalName) => (metric: Metric) => {
		samples.push({ name, value: metric.value, at: now() });
	};

	const saw = (name: VitalName) => samples.some((sample) => sample.name === name);

	const valueOf = (name: VitalName, view: OpenView): number | undefined => {
		let value: number | undefined;
		for (const sample of samples) {
			if (sample.name !== name) continue;
			if (name === 'lcp') {
				if (view.enter) value = sample.value;
				continue;
			}
			if (sample.at < view.since) continue;
			if (view.until != null && sample.at >= view.until) continue;
			value = sample.value;
		}
		return value;
	};

	const flush = (view: OpenView) => {
		const payload: Record<string, unknown> = {
			trace_id: view.traceId,
			span_id: view.spanId,
			route: view.route,
			portal: currentPortal(),
			settle_ms: Math.max(0, Math.round(view.settle))
		};
		const lcp = valueOf('lcp', view);
		if (lcp != null) payload.lcp_ms = lcp;
		const inp = valueOf('inp', view);
		if (inp != null) payload.inp_ms = inp;
		const cls = valueOf('cls', view);
		if (cls != null) payload.cls = cls;
		const ttfb = valueOf('ttfb', view);
		if (ttfb != null) payload.ttfb_ms = ttfb;
		const body = beaconBody(payload);
		if (!body) return;
		send(body);
	};

	const leave = (view: OpenView) => {
		// The enter view is the one a click-through measures. Left before any LCP candidate,
		// it waits for hide: the candidate often arrives only after this navigation, and a
		// soft view must not carry it. Once a candidate exists, the navigation click is the
		// cutoff web-vitals uses, so the view can be sent now.
		if (view.enter && !saw('lcp')) {
			if (held) flush(held);
			const boundary = navStart > view.since ? navStart : now();
			held = { ...view, until: boundary };
			return;
		}
		flush(view);
	};

	return {
		/** Register once. A second call, including from a remount, leaves the first listeners in place. */
		startVitals() {
			if (vitalsOn) return;
			vitalsOn = true;
			// Candidates as they arrive. Without this, LCP, INP and CLS are reported only when
			// final, on the same hide or click that sends the beacon — and our flush can run first.
			onLCP(note('lcp'), { reportAllChanges: true });
			onINP(note('inp'), { reportAllChanges: true });
			onCLS(note('cls'), { reportAllChanges: true });
			onTTFB(note('ttfb'));
		},
		before() {
			navStart = now();
		},
		after(nav: RumNav, data: RumPageData) {
			const route = nav.to?.route?.id ?? null;
			// The first `enter` can arrive with no route id, after mount has already opened the
			// landing view. Dropping that view would lose the page the person is looking at.
			if (!route || !route.startsWith('/')) {
				if (nav.type === 'enter') return;
				if (open) leave(open);
				open = null;
				return;
			}
			if (open) leave(open);
			open = null;
			const traceId = typeof data.traceId === 'string' ? data.traceId : '';
			const spanId = typeof data.spanId === 'string' ? data.spanId : '';
			if (!TRACE.test(traceId) || !SPAN.test(spanId)) return;
			const enter = nav.type === 'enter';
			open = {
				route,
				traceId,
				spanId,
				settle: enter ? now() : Math.max(0, now() - navStart),
				since: enter ? 0 : navStart,
				enter
			};
		},
		/**
		 * The first load's `afterNavigate` runs before this component's `onMount` has registered,
		 * so the landing view would never open. Mount calls this once; a view already opened by
		 * that callback is left alone.
		 */
		ensureEnter(routeId: string | null, data: RumPageData) {
			if (open) return false;
			this.after({ type: 'enter', to: { route: { id: routeId } } }, data);
			return open !== null;
		},
		hide() {
			const pending = held;
			const current = open;
			held = null;
			open = null;
			if (pending) flush(pending);
			if (current) flush(current);
		},
		note
	};
}
