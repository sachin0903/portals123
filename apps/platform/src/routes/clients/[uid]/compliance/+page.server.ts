import { error } from '@sveltejs/kit';
import { can } from '@graphene/api-client/permissions';
import type { AmlMatchProfileData } from '@graphene/ui';
import { apiUrl, authHeaders } from '$lib/api';
import type { FirmIdentity } from './identity';
import type { PageServerLoad } from './$types';

export const load: PageServerLoad = async ({ locals, params, fetch, parent, url }) => {
	// Gated here as well as in the sidebar: nav visibility is cosmetic, the server is the control.
	if (!can(locals.permissions, locals.role, 'screening:read')) error(403, 'Not authorised');

	const [{ detail: contact }, acceptances, identity, amlMonitored, amlMatches, reports] =
		await Promise.all([
			parent(),
			loadAcceptances(locals.accessToken, fetch, params.uid),
			loadIdentity(locals.accessToken, fetch, params.uid, url.searchParams.get('check')),
			loadAmlMonitored(locals.accessToken, fetch, params.uid),
			loadAmlMatches(locals.accessToken, fetch, params.uid),
			loadReports(locals.accessToken, fetch, params.uid)
		]);
	return {
		// The latest check per kind, status and dates only. The result, the matches and the
		// decisions stay with Graphene.
		kyc: contact.kyc ?? [],
		// The identity checks in the firm's words, and the shown check's photos and analysis. Null
		// when the API could not answer: the rest of the page still shows.
		identity,
		acceptances,
		// Whether ongoing AML monitoring is on for the client. Null before any AML screen, or when the
		// API could not answer.
		amlMonitored,
		// The matches Graphene confirmed on the latest AML screen, once it let the journey continue
		// past them. Empty until then, and when the API could not answer.
		amlMatches,
		// Which compliance reports can be downloaded, and the client's applications. Null when the API
		// could not answer.
		reports,
		// The application summary PDF needs the journey read the Journeys pages need.
		canReadApplications: can(locals.permissions, locals.role, 'cases:read')
	};
};

/** One compliance report the firm can download, or why it cannot yet. */
export type ReportAvailability = { available: boolean; reason: string | null };

/** The client's compliance reports (`GET /crm/contacts/{uid}/compliance-reports`). */
export type ComplianceReports = {
	client: ReportAvailability;
	/** The latest identity check's report. `check_uid` is null when there is no check. */
	identity: ReportAvailability & { check_uid: string | null };
	/** The latest AML screen's report. `check_uid` is null when there is no screen. */
	aml: ReportAvailability & { check_uid: string | null };
	/** The client's applications, each with a summary PDF. */
	applications: { uid: string; title: string; status: string; created_at: string }[];
};

/** One match Graphene confirmed on the client's AML screen, as the AML console shows it, with no
 *  decision on it. */
export type ConfirmedAmlMatch = AmlMatchProfileData & { uid: string };

/** The confirmed matches of the latest AML screen (`GET /crm/contacts/{uid}/aml-matches`). */
export type ConfirmedAmlMatches = {
	check_uid: string | null;
	/** When Graphene let the journey continue past them. */
	allowed_at: string | null;
	matches: ConfirmedAmlMatch[];
};

const NO_MATCHES: ConfirmedAmlMatches = { check_uid: null, allowed_at: null, matches: [] };

/** The matches Graphene confirmed and let the journey continue past. None when it has not, or
 *  when the read fails: the rest of the page still shows. */
async function loadAmlMatches(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string
): Promise<ConfirmedAmlMatches> {
	try {
		const res = await fetch(`${apiUrl()}/crm/contacts/${encodeURIComponent(uid)}/aml-matches`, {
			headers: authHeaders(accessToken)
		});
		return res.ok ? ((await res.json()) as ConfirmedAmlMatches) : NO_MATCHES;
	} catch {
		return NO_MATCHES;
	}
}

export type TermAcceptance = {
	uid: string;
	title: string;
	version_number: number;
	accepted_at: string;
	channel: string;
	/** The signed copy an in-person acceptance was recorded against. */
	evidence_document_uid?: string | null;
};

/** The terms a client accepted, exactly as they read them (`GET …/term-acceptances/{uid}`). */
export type AcceptedTerms = TermAcceptance & {
	code: string;
	body_kind: string;
	body_html: string | null;
	has_file: boolean;
};

async function loadAcceptances(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string
): Promise<TermAcceptance[]> {
	const res = await fetch(`${apiUrl()}/contacts/${uid}/term-acceptances`, {
		headers: authHeaders(accessToken)
	});
	return res.ok ? ((await res.json()) as TermAcceptance[]) : [];
}

/** The client's identity checks, with the photos of `check` (`?check=`), else of the current
 *  one. A check that is not the client's shows the current one instead. */
async function loadIdentity(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string,
	check: string | null
): Promise<FirmIdentity | null> {
	const base = `${apiUrl()}/crm/contacts/${encodeURIComponent(uid)}/identity`;
	try {
		let res = await fetch(check ? `${base}?check=${encodeURIComponent(check)}` : base, {
			headers: authHeaders(accessToken)
		});
		if (check && res.status === 404) res = await fetch(base, { headers: authHeaders(accessToken) });
		return res.ok ? ((await res.json()) as FirmIdentity) : null;
	} catch {
		return null;
	}
}

/** Which compliance reports can be downloaded, and the client's applications. Null when the read
 *  fails: the rest of the page still shows. */
async function loadReports(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string
): Promise<ComplianceReports | null> {
	try {
		const res = await fetch(
			`${apiUrl()}/crm/contacts/${encodeURIComponent(uid)}/compliance-reports`,
			{ headers: authHeaders(accessToken) }
		);
		return res.ok ? ((await res.json()) as ComplianceReports) : null;
	} catch {
		return null;
	}
}

/** Whether ongoing AML monitoring is on, from the client's AML screening entry
 *  (`GET /crm/contacts/{uid}/screening`). Null before any AML screen, or when the read fails. */
async function loadAmlMonitored(
	accessToken: string | null,
	fetch: typeof globalThis.fetch,
	uid: string
): Promise<boolean | null> {
	try {
		const res = await fetch(`${apiUrl()}/crm/contacts/${encodeURIComponent(uid)}/screening`, {
			headers: authHeaders(accessToken)
		});
		if (!res.ok) return null;
		const { checks } = (await res.json()) as {
			checks?: { kind: string; monitored?: boolean | null }[];
		};
		return checks?.find((check) => check.kind === 'aml')?.monitored ?? null;
	} catch {
		return null;
	}
}
