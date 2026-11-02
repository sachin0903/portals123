/**
 * Presentation rules for a client's identity/AML screening status.
 *
 * `GET /crm/contacts/{id}` returns the LATEST check per kind from `crm.kyc_checks` (migration 0026),
 * written by the flow engine and by the ComplyCube result routing. This module is the one place that
 * decides how those rows read to an adviser — kept pure so the wording is unit-tested rather than
 * buried in a template. A passed check stands until something else replaces it: identity/AML passes
 * do not lapse, so a `passed` row always reads as its pass, whatever `valid_until` says. The firm sees
 * status and dates only, here and at `/clients/[id]/compliance`.
 *
 * Deliberately adviser-facing only. Screening detail never reaches the client portal: showing a
 * person their own sanctions/PEP/adverse-media position carries tipping-off risk, so the client
 * portal has no screening surface at all and `compliance.*` grants a firm status columns only. Nothing here should be imported from `apps/client` (its aml-leak tripwire is the guard),
 * which is why every exported name says `screening`, never the register's own vocabulary.
 */
import type { StatusTone } from '../../ui/status-badge/index.js';
import { formatDate } from '../../../dates.js';

/** The two check kinds `crm.kyc_checks.kind` allows. */
export type ScreeningKind = 'identity' | 'aml';

/** `crm.kyc_checks.status` — the store allows no `cancelled`; an abandoned check becomes `expired`. */
export type ScreeningStatus = 'pending' | 'passed' | 'failed' | 'referred' | 'expired';

/** One row of `ContactDetail.kyc` — the latest attempt of its kind. */
export type ScreeningCheck = {
	kind: ScreeningKind;
	status: ScreeningStatus;
	provider: string;
	verified_at: string | null;
	valid_until: string | null;
	updated_at: string;
};

/** A row as rendered in the Screening card: always both kinds, present or not. */
export type ScreeningRow = {
	kind: ScreeningKind;
	label: string;
	/** Absent when no check of this kind has ever run. */
	check: ScreeningCheck | null;
	statusLabel: string;
	tone: StatusTone;
	/** Secondary line: when it was verified, or why it needs attention. Null when there's nothing to add. */
	detail: string | null;
	/** True when this rail needs someone to act (never screened, failed, referred, or lapsed). */
	needsAttention: boolean;
};

const KIND_LABEL: Record<ScreeningKind, string> = { identity: 'Identity', aml: 'AML' };
const KINDS: ScreeningKind[] = ['identity', 'aml'];

/** `null` when absent: the date goes inside a sentence ("Verified 5 Aug 2026"), so no em dash. */
function shortDate(iso: string | null): string | null {
	return formatDate(iso, '') || null;
}

/** The single row for one kind. */
function rowFor(kind: ScreeningKind, check: ScreeningCheck | null): ScreeningRow {
	const label = KIND_LABEL[kind];
	if (!check) {
		return {
			kind,
			label,
			check: null,
			statusLabel: 'Not run',
			tone: 'neutral',
			detail: null,
			needsAttention: true
		};
	}

	switch (check.status) {
		case 'passed': {
			const journey = check.provider === 'manual';
			const detail = shortDate(check.verified_at)
				? journey
					? `Verified ${shortDate(check.verified_at)} (journey / manual)`
					: `Verified ${shortDate(check.verified_at)}`
				: journey
					? 'Verified in the journey or by a manual review'
					: null;
			return {
				kind,
				label,
				check,
				statusLabel: journey ? 'Verified (journey)' : 'Clear',
				tone: 'success',
				detail,
				needsAttention: false
			};
		}
		case 'pending':
			return {
				kind,
				label,
				check,
				statusLabel: 'In progress',
				tone: 'info',
				detail: null,
				needsAttention: false
			};
		case 'referred':
			return {
				kind,
				label,
				check,
				statusLabel: 'Referred',
				tone: 'warning',
				detail: 'Under review by Graphene',
				needsAttention: true
			};
		case 'failed':
			return {
				kind,
				label,
				check,
				statusLabel: 'Failed',
				tone: 'danger',
				detail: null,
				needsAttention: true
			};
		case 'expired':
			// A pass of details that have since changed, or a check that never finished.
			// The firm reads that as not started (the screening endpoint maps the same
			// status to `not_started`); "Lapsed" made a live re-screen look time-expired.
			return {
				kind,
				label,
				check,
				statusLabel: 'Not run',
				tone: 'neutral',
				detail: null,
				needsAttention: true
			};
	}
}

/** Both rails, identity first, whatever the API returned. A client with no checks still gets two
 *  "Not run" rows — an empty card would read as "nothing required" when the truth is "not started". */
export function screeningRows(checks: ScreeningCheck[] | undefined): ScreeningRow[] {
	const byKind = new Map<ScreeningKind, ScreeningCheck>();
	for (const c of checks ?? []) if (!byKind.has(c.kind)) byKind.set(c.kind, c);
	return KINDS.map((kind) => rowFor(kind, byKind.get(kind) ?? null));
}

/** Whether the whole client is screened: both rails currently clear. Drives the card's summary. */
export function isFullyScreened(rows: ScreeningRow[]): boolean {
	return rows.length > 0 && rows.every((r) => !r.needsAttention && r.check?.status === 'passed');
}
