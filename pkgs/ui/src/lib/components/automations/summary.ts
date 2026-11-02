/**
 * Turning an automation into one line of English.
 *
 * The listing's whole job is to let somebody scan a firm's rules and understand what the machine
 * will do — so the condition column is a SENTENCE, not a JSON blob or a "3 conditions" count. The
 * summariser is pure and unit-tested because it is read far more often than the builder is opened,
 * and a sentence that misreads a rule is worse than no sentence.
 *
 * Copy rules, learned from the audit log's `describeActionEvent`: name the thing in the firm's
 * vocabulary ("Client type", not `contact.type`), never render a raw slug where a label exists,
 * and degrade to something honest rather than blank when the vocabulary runs out.
 */
import { OPERATOR_SPECS, humaniseKey, ACTION_BY_TYPE } from './contract.js';
import type { StatusTone } from '../ui/status-badge/index.js';
import { formatDateTime } from '../../dates.js';
import type { AutomationRow, ConditionJson } from './types.js';

/** How each field scope reads in a sentence. */
const SCOPE_NOUNS: Record<string, string> = {
	contact: 'Client',
	application: 'Application',
	account: 'Account',
	case: 'Journey',
	memory: 'Memory',
	event: 'Event',
	time: 'Time'
};

/** `contact.service_type` → "Client service type". An unknown scope keeps its own name. */
export function fieldLabel(field: string): string {
	if (!field) return 'Something';
	const [scope, ...rest] = field.split('.');
	if (rest.length === 0) return humaniseKey(field);
	const noun = SCOPE_NOUNS[scope ?? ''] ?? humaniseKey(scope ?? '');
	return `${noun} ${rest.join(' ').replace(/_/g, ' ')}`;
}

function valuePhrase(value: unknown): string {
	if (value === null || value === undefined) return '';
	if (Array.isArray(value)) {
		const parts = value.map((v) => String(v));
		if (parts.length <= 1) return parts.join('');
		return `${parts.slice(0, -1).join(', ')} or ${parts[parts.length - 1]}`;
	}
	if (typeof value === 'object' && 'field' in (value as Record<string, unknown>)) {
		return fieldLabel(String((value as Record<string, unknown>).field));
	}
	return String(value);
}

/** One leaf as a clause: "Client type is individual". */
function leafPhrase(node: Record<string, unknown>): string {
	const field = fieldLabel(typeof node.field === 'string' ? node.field : '');
	const rawOp = String(node.operator ?? node.op ?? 'equals');
	const spec = OPERATOR_SPECS[rawOp];
	if (!spec) return `${field} ${rawOp} ${valuePhrase(node.value)}`.trim();
	// "is"/"is not", "contains"/"doesn't contain" — the polarity is carried by the operator name,
	// so it is rendered here rather than hoped for from the label.
	const verb = spec.negate ? negativeVerb(spec.label) : spec.label;
	if (spec.value === 'none') return `${field} ${verb}`;
	if (spec.value === 'days') return `${field} ${verb} ${valuePhrase(node.value)} days`;
	return `${field} ${verb} ${valuePhrase(node.value)}`.trim();
}

function negativeVerb(label: string): string {
	if (label === 'is') return 'is not';
	if (label === 'is one of') return 'is not one of';
	if (label === 'is empty') return 'is not empty';
	if (label === 'contains') return 'doesn’t contain';
	if (label === 'matches') return 'doesn’t match';
	if (label === 'is within the last') return 'is older than';
	return `not ${label}`;
}

function nodePhrase(node: unknown, depth: number): string {
	if (!node || typeof node !== 'object') return '';
	const record = node as Record<string, unknown>;
	// An empty object is "no condition", not a broken one — the same rule the Python evaluator
	// applies (`conditions.py::_node_issues`). Rendering it as a clause would print "Something is".
	if (Object.keys(record).length === 0) return '';
	if ('logic' in record || 'conditions' in record) {
		const members = Array.isArray(record.conditions) ? record.conditions : [];
		const joiner = String(record.logic ?? 'and').toLowerCase() === 'or' ? ' or ' : ' and ';
		const parts = members.map((m) => nodePhrase(m, depth + 1)).filter(Boolean);
		if (parts.length === 0) return '';
		if (parts.length === 1) return parts[0] ?? '';
		// Parenthesise nested groups only — the top level reads as a plain sentence.
		return depth > 0 ? `(${parts.join(joiner)})` : parts.join(joiner);
	}
	return leafPhrase(record);
}

/** The listing's conditions column. Empty conditions read as the truth: it always applies. */
export function summariseConditions(conditions: ConditionJson): string {
	const phrase = nodePhrase(conditions, 0);
	return phrase || 'Always';
}

/** The listing's target column: what the rule DOES, in one phrase. */
export function summariseTarget(
	row: Pick<AutomationRow, 'flow_name' | 'flow_code' | 'action'>
): string {
	if (row.flow_name || row.flow_code) return `Start “${row.flow_name ?? row.flow_code}”`;
	if (!row.action) return 'Nothing'; // the database forbids it; the UI still tells the truth
	const spec = ACTION_BY_TYPE[row.action.type];
	const label = spec?.label ?? humaniseKey(row.action.type);
	const detail = actionDetail(row.action.type, row.action.config ?? {});
	return detail ? `${label}: ${detail}` : label;
}

/** The one config value worth showing inline per action type. */
function actionDetail(type: string, config: Record<string, unknown>): string {
	const key =
		{
			apply_tag: 'tag',
			remove_tag: 'tag',
			raise_task: 'title',
			send_email: 'template_code',
			set_case_status: 'status',
			request_document: 'category',
			generate_document: 'template_uid',
			send_document: 'document_uid',
			webhook_out: 'endpoint_key',
			lookup: 'endpoint_key'
		}[type] ?? '';
	const value = key ? config[key] : undefined;
	return value === undefined || value === null || value === '' ? '' : String(value);
}

/** "every weekday at 09:00" for the common expressions; the raw cron otherwise. */
export function summariseSchedule(
	schedule: { cron: string; timezone?: string | null } | null
): string {
	if (!schedule?.cron) return '';
	const [minute, hour, dom, month, dow] = schedule.cron.trim().split(/\s+/);
	if (minute === undefined || hour === undefined) return schedule.cron;
	const time =
		/^\d+$/.test(minute) && /^\d+$/.test(hour)
			? `${hour.padStart(2, '0')}:${minute.padStart(2, '0')}`
			: '';
	if (!time || dom !== '*' || month !== '*') return schedule.cron;
	const when =
		dow === '*'
			? 'every day'
			: dow === '1-5'
				? 'every weekday'
				: dow === '0,6' || dow === '6,0'
					? 'every weekend'
					: `on day ${dow} of the week`;
	return `${when} at ${time}`;
}

/** "Won't re-fire for the same client within 24 hours." */
export function summariseRearm(minutes: number | null, fallback: number): string {
	const value = minutes ?? fallback;
	if (value === 0) return 'Can re-fire immediately.';
	if (value % (60 * 24) === 0) {
		const days = value / (60 * 24);
		return `Won’t re-fire for the same subject within ${days} day${days === 1 ? '' : 's'}.`;
	}
	if (value % 60 === 0) {
		const hours = value / 60;
		return `Won’t re-fire for the same subject within ${hours} hour${hours === 1 ? '' : 's'}.`;
	}
	return `Won’t re-fire for the same subject within ${value} minutes.`;
}

// ── Run-log vocabulary ──────────────────────────────────────────────────────────────────────
// The delivery outcomes are a CLOSED set (the CHECK on flows.automation_deliveries), so they get
// real copy rather than a de-slugged enum. "Why didn't it fire?" is the question this log exists
// to answer, and `skipped_rearm` does not answer it.

const OUTCOMES: Record<string, { label: string; tone: StatusTone; hint: string }> = {
	dispatched: { label: 'Ran', tone: 'success', hint: 'The automation performed its target.' },
	deferred: {
		label: 'Queued',
		tone: 'info',
		hint: 'Held back by a rate or concurrency limit — it will still run.'
	},
	skipped_conditions: {
		label: 'No match',
		tone: 'neutral',
		hint: 'The event happened, but the conditions didn’t hold.'
	},
	skipped_rearm: {
		label: 'Too soon',
		tone: 'neutral',
		hint: 'Already fired for this subject inside the re-arm window.'
	},
	skipped_paused: {
		label: 'Paused',
		tone: 'warning',
		hint: 'Automations were paused for the firm at the time.'
	},
	skipped_automation: {
		label: 'Self-triggered',
		tone: 'neutral',
		hint: 'The event came from another automation, and this one isn’t opted in.'
	},
	budget_exhausted: {
		label: 'Budget spent',
		tone: 'warning',
		hint: 'This journey has already fired its allowance of automations.'
	},
	depth_exceeded: {
		label: 'Chain too deep',
		tone: 'warning',
		hint: 'The automation chain hit its depth cap.'
	},
	failed: { label: 'Failed', tone: 'danger', hint: 'The target raised an error.' }
};

export function outcomeLabel(outcome: string | null): string {
	if (!outcome) return 'Never run';
	return OUTCOMES[outcome]?.label ?? humaniseKey(outcome);
}

export function outcomeTone(outcome: string | null): StatusTone {
	if (!outcome) return 'neutral';
	return OUTCOMES[outcome]?.tone ?? 'neutral';
}

export function outcomeHint(outcome: string | null): string {
	if (!outcome) return 'This automation hasn’t been considered for an event yet.';
	return OUTCOMES[outcome]?.hint ?? '';
}

/** An absolute timestamp — the run log is evidence, so it says when, not "2 hours ago". */
export function formatWhen(iso: string | null): string {
	return formatDateTime(iso);
}
