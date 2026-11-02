/**
 * The wire types the automations surface reads, and the two injection seams every shared book
 * surface in this package uses.
 *
 * Structurally typed on purpose: the platform portal reads `/automations` and a future admin mount
 * would read the same rows through `?tenant=`, so nothing here names an app. Loaders and form
 * actions stay app-side; the components take rows in and hand intent back out through `links`
 * (where to navigate) and `actions` (which of the HOST page's form actions to post to). An absent
 * `actions` object means read-only — every write affordance disappears rather than rendering a
 * control that 403s.
 */
import type { Enhance } from '../book/clients/form-enhance.js';

/** A stored condition tree, exactly as the API round-trips it (the contract's grammar). */
export type ConditionJson = Record<string, unknown> | null;

/** `{type, config}` — the contract's action shape. */
export interface AutomationAction {
	type: string;
	config: Record<string, unknown>;
}

/** `{cron, timezone}` for a scheduled trigger. */
export interface AutomationSchedule {
	cron: string;
	timezone?: string | null;
}

/** One automation, as `GET /automations` returns it. */
export interface AutomationRow {
	uid: string;
	name: string;
	trigger_key: string;
	/** The friendly label from the registry; `null` when the stored key is not in this build. */
	trigger_label: string | null;
	trigger_source: string | null;
	subject_kind: string | null;
	/** False when nothing emits this trigger yet — the row is stored but cannot fire. */
	supported: boolean;
	conditions: ConditionJson;
	action: AutomationAction | null;
	config: Record<string, unknown>;
	priority: number;
	enabled: boolean;
	rearm_minutes: number | null;
	schedule: AutomationSchedule | null;
	allow_automation_source: boolean;
	flow_uid: string | null;
	flow_code: string | null;
	flow_name: string | null;
	flow_status: string | null;
	last_fired_at: string | null;
	last_outcome: string | null;
	last_detail: string | null;
	created_at?: string;
	updated_at?: string;
}

/** One row of the run log (`flows.automation_deliveries`). */
export interface DeliveryRow {
	id: number;
	event_id: number | null;
	event_key: string;
	subject_kind: string | null;
	subject_id: string | null;
	case_uid: string | null;
	outcome: string;
	detail: string | null;
	error: string | null;
	chain_depth: number;
	attempts: number;
	retry_after: string | null;
	created_at: string;
}

/** A published journey an automation may start. */
export interface FlowTarget {
	uid: string;
	code: string;
	name: string;
}

/** The tenant's automation settings, as `GET /automations/settings` returns them. */
export interface AutomationSettings {
	paused: boolean;
	paused_reason: string | null;
	timezone: string;
	effective: Record<string, number>;
	defaults: Record<string, unknown>;
	ceilings: Record<string, number>;
	max_rules: number;
	/** False until the action executor's own publish preflight has landed. */
	preflight_available: boolean;
}

/** The result of a dry run (`POST /automations/{uid}/test`). */
export interface DryRunResult {
	/** Always false. On the wire because the promise is worth restating where it is read. */
	performed: boolean;
	would_fire: boolean;
	matched: boolean;
	blockers: string[];
	subject: { kind: string; id: number | null; found: boolean };
	trigger: { key: string; label: string; source: string };
	target: Record<string, unknown>;
	explanation: ExplainNode;
	facts: Record<string, unknown>;
}

/** One node of the dry run's per-leaf verdict, mirroring the authored condition structure. */
export type ExplainNode =
	| { kind: 'always'; passed: boolean }
	| { kind: 'group'; logic: string; passed: boolean; conditions: ExplainNode[] }
	| {
			kind: 'leaf';
			field: unknown;
			operator: unknown;
			value: unknown;
			actual: unknown;
			passed: boolean;
	  };

/** Where the surface navigates. Injected, so a second mount can live under a different prefix. */
export interface AutomationLinks {
	/** The listing itself (the sheet's cancel target, the run-log back link). */
	index: string;
	/** One automation's own page/panel. */
	automation: (uid: string) => string;
	/** The flow editor, for "there are no published journeys yet". Omitted ⇒ no link offered. */
	flows?: string;
}

/**
 * The host page's form-action names, plus its own `enhance`.
 *
 * Absent ⇒ the surface is read-only. Present-but-partial is legitimate too: a principal with
 * `automations:read` + `automations:pause` gets `pause` and nothing else, and every control keyed
 * off a missing name simply does not render. That is the house rule — no control that lies.
 */
export interface AutomationActions {
	enhance: Enhance;
	create?: string;
	update?: string;
	remove?: string;
	toggle?: string;
	reorder?: string;
	pause?: string;
}
