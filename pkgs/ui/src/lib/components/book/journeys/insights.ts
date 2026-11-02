/**
 * The step-funnel vocabulary — the TypeScript side of `GET /flows/{uid}/insights`
 * (`apps/api/app/flows/analytics.py`).
 *
 * Types plus the presentation arithmetic: rates, a duration in words, the bar widths. The
 * aggregation itself is Postgres's and the shaping is the API's; nothing here recomputes a number
 * the server already produced, because two places that both know how to compute completion rate is
 * one place too many.
 */
import type { FlowNodeKind } from './nodes.js';

/** The `flows.case_steps` status vocabulary (mirrors `analytics.STEP_STATUSES`). */
export type FunnelStepStatus =
	'pending' | 'fulfilled' | 'skipped' | 'cancelled' | 'blocked' | 'waiting' | 'failed';

export const FUNNEL_STATUSES: readonly FunnelStepStatus[] = [
	'fulfilled',
	'waiting',
	'blocked',
	'failed',
	'pending',
	'skipped',
	'cancelled'
];

/** How each status reads to an author. `skipped`/`cancelled` are deliberately neutral, not
 *  negative: a step the firm's own conditions correctly excluded is the flow working. */
export const STATUS_LABEL: Record<FunnelStepStatus, string> = {
	pending: 'Not started',
	fulfilled: 'Completed',
	skipped: 'Not applicable',
	cancelled: 'Cancelled',
	blocked: 'Blocked',
	waiting: 'Waiting',
	failed: 'Failed'
};

export const STATUS_TONE: Record<FunnelStepStatus, 'success' | 'warning' | 'danger' | 'neutral'> = {
	pending: 'neutral',
	fulfilled: 'success',
	skipped: 'neutral',
	cancelled: 'neutral',
	blocked: 'warning',
	waiting: 'warning',
	failed: 'danger'
};

export interface FunnelArm {
	id: string;
	label: string;
	is_else: boolean;
	next: string | null;
	count: number;
}

export interface FunnelNode {
	node_id: string;
	kind: FlowNodeKind;
	type: string | null;
	title: string;
	scope: string | null;
	reached: number;
	completed: number;
	by_status: Partial<Record<FunnelStepStatus, number>>;
	instances: number;
	median_seconds: number | null;
	p90_seconds: number | null;
	is_work: boolean;
	arms?: FunnelArm[];
}

export interface FunnelEdge {
	from: string;
	to: string;
	completed: number;
	reached: number;
	lost: number;
	gained: number;
	/** null when nobody completed the source — no rate at all, which is not the same as zero. */
	rate: number | null;
}

export interface FunnelVersion {
	uid: string;
	version: number;
	status: string;
	published_at: string | null;
	case_count: number;
	is_current: boolean;
}

/** `GET /flows/{uid}/insights`. */
export interface FlowInsights {
	flow: { uid: string; code: string; name: string };
	version: FunnelVersion;
	versions: FunnelVersion[];
	range: { since: string; until: string };
	/** `legacy` means "no authored order, so counts per step and no edges" — a pre-v3 definition's
	 *  array order is presentation, and drop-off measured along it would be a measurement of
	 *  nothing. */
	shape: 'graph' | 'legacy' | 'unreadable';
	cases: {
		total: number;
		by_status: Record<string, number>;
		median_seconds: number | null;
	};
	nodes: FunnelNode[];
	edges: FunnelEdge[];
}

// ---------------------------------------------------------------------------------------------
// Presentation
// ---------------------------------------------------------------------------------------------

/** A duration in the coarsest unit that still says something: "4 hours", "3 days", "6 weeks".
 *  Null in, null out — a node nobody has completed has no median, and rendering "0s" would claim
 *  it is instant. */
export function durationLabel(seconds: number | null | undefined): string | null {
	if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return null;
	if (seconds < 90) return `${Math.max(1, Math.round(seconds))}s`;
	const minutes = seconds / 60;
	if (minutes < 90) return `${Math.round(minutes)} min`;
	const hours = minutes / 60;
	if (hours < 48) return `${Math.round(hours)} ${Math.round(hours) === 1 ? 'hour' : 'hours'}`;
	const days = hours / 24;
	if (days < 21) return `${Math.round(days)} days`;
	const weeks = days / 7;
	if (weeks < 10) return `${Math.round(weeks)} weeks`;
	return `${Math.round(days / 30)} months`;
}

/** The share of a node's cases that completed it, 0–1. Null when nobody reached it: a rate over
 *  zero is not zero, and showing 0% for a node nobody has ever seen reads as a failure. */
export function completionRate(node: FunnelNode): number | null {
	return node.reached ? node.completed / node.reached : null;
}

/** How many are stuck IN this node — reached, not completed, and not deliberately skipped.
 *  The number the funnel exists to surface, and it is a node property, not an edge one. */
export function outstanding(node: FunnelNode): number {
	const irrelevant = (node.by_status.skipped ?? 0) + (node.by_status.cancelled ?? 0);
	const relevant = node.reached - Math.min(irrelevant, node.reached);
	return Math.max(relevant - node.completed, 0);
}

export function percent(value: number | null): string {
	return value === null ? '—' : `${Math.round(value * 100)}%`;
}

/** Bar width as a percentage of the busiest node, so the eye compares volumes rather than each
 *  node against its own 100%. A funnel drawn per-node-100% is a stack of full bars. */
export function barWidth(node: FunnelNode, nodes: readonly FunnelNode[]): number {
	const peak = Math.max(1, ...nodes.map((n) => n.reached));
	return Math.round((node.reached / peak) * 100);
}

/** The single biggest leak: the node with the most cases stuck in it. Null when nothing is stuck,
 *  which is a real and good answer rather than an empty state. */
export function worstNode(nodes: readonly FunnelNode[]): FunnelNode | null {
	let worst: FunnelNode | null = null;
	for (const node of nodes) {
		if (!node.is_work) continue;
		if (!worst || outstanding(node) > outstanding(worst)) worst = node;
	}
	return worst && outstanding(worst) > 0 ? worst : null;
}

/** The edges INTO a node, so a row can show where its arrivals came from and what was lost on the
 *  way. Indexed once by the caller rather than filtered per row — a 200-node graph would otherwise
 *  be a quadratic scan on every render. */
export function edgesByTarget(edges: readonly FunnelEdge[]): Map<string, FunnelEdge[]> {
	const out = new Map<string, FunnelEdge[]>();
	for (const edge of edges) {
		const list = out.get(edge.to);
		if (list) list.push(edge);
		else out.set(edge.to, [edge]);
	}
	return out;
}

/** Drop-off into a node: how many cases completed a predecessor but never arrived. Summed across
 *  predecessors because a node with two ways in loses from both. */
export function lostInto(nodeId: string, byTarget: Map<string, FunnelEdge[]>): number {
	return (byTarget.get(nodeId) ?? []).reduce((total, edge) => total + edge.lost, 0);
}

/** A version's option label: "v3 · published · 41 journeys". The case count is load-bearing —
 *  comparing a 400-case cohort against a 3-case one is the mistake the picker exists to prevent. */
export function versionLabel(version: FunnelVersion): string {
	const parts = [`v${version.version}`];
	if (version.is_current) parts.push('live');
	else parts.push(version.status);
	parts.push(`${version.case_count} ${version.case_count === 1 ? 'journey' : 'journeys'}`);
	return parts.join(' · ');
}

/** The preset windows the range picker offers, in days. 'All' is deliberately absent: an unbounded
 *  aggregate over a firm's whole history is the query that gets slow first, and the version cohort
 *  already bounds the interesting comparison. */
export const RANGE_PRESETS: readonly { days: number; label: string }[] = [
	{ days: 30, label: '30 days' },
	{ days: 90, label: '90 days' },
	{ days: 180, label: '6 months' },
	{ days: 365, label: '12 months' }
];

/** ISO bounds for a preset, computed from a caller-supplied `now` so it is testable and so the
 *  server and the picker cannot disagree about where "today" ends. */
export function rangeFor(days: number, now: Date = new Date()): { since: string; until: string } {
	const until = new Date(now.getTime());
	const since = new Date(now.getTime() - days * 86_400_000);
	return { since: since.toISOString(), until: until.toISOString() };
}

/** Route knowledge for the funnel surface, injected — the platform and admin portals sit at
 *  different paths, and a hardcoded href is a broken page in one of them. */
export interface FlowFunnelLinks {
	/** The journey's builder/detail page (the back link). */
	flow: string;
	/** This same insights page with a different version / window selected. */
	insights: (params: { version?: string; days?: number }) => string;
}
