import { globSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

/**
 * The client portal must carry NO AML/screening surface — not a field, not a label, not a type.
 *
 * Telling someone that they are under sanctions/PEP/adverse-media screening — or merely that a
 * search exists against them — is tipping off, an offence under POCA 2002 s.333A. The database is
 * the primary control (every `compliance.*` read policy omits `auth.contact_scope()`, so a client
 * session resolves zero rows; see apps/api/migrations/sql/0186_compliance.sql and
 * apps/api/tests/test_compliance_rls.py). This test guards the OTHER end of the same boundary.
 *
 * The realistic leak path is payload creep, not a deliberate feature: `GET /crm/contacts/{id}`
 * already returns a `kyc` array, apps/platform now renders it, and this portal calls that very
 * endpoint (`src/routes/account/+page.server.ts`). Today that load projects the fields it wants one
 * by one, so nothing spreads through — but the moment someone widens a type, spreads the response,
 * or copies a component across from the platform portal, screening vocabulary appears in this tree.
 * That is what this test fails on.
 *
 * Limitation, stated honestly: this catches *references* in source, not a blind pass-through of an
 * API payload that never names the field (e.g. `return { contact: detail.data.contact }`). It is a
 * tripwire on the cheap, likely mistake — not a proof of absence.
 *
 * A future legitimate match must be added to ALLOWED **deliberately**, with a reason, in the same
 * change that introduces it. Do not widen the exceptions to make a build green: if a screening term
 * has genuinely arrived in the client portal, the question to answer first is whether the data
 * behind it can reach an end user.
 */

/**
 * Case-insensitive substrings. `identity` is deliberately NOT here: the client portal legitimately
 * runs identity steps in onboarding (`verify_identity`), and banning it would train people to edit
 * this list.
 */
const BANNED = [
	'aml.', // the aml.* audit and event vocabulary
	'aml_', // aml_status, aml_check, …
	'kyc', // the `kyc` array on GET /crm/contacts/{id} — the actual leak path
	'screening',
	'hit_score',
	'truematch', // vendor disposition vocabulary (trueMatch)
	'falsepositive', // falsePositive
	'creditsafe', // the retired screening and identity providers
	'idenfy',
	'complycube' // the identity and screening provider: the portal names the check, never the vendor
];

/**
 * Deliberate exceptions, keyed by a snippet of the line that makes the match legitimate rather than
 * by line number (line numbers move; intent doesn't). Every entry needs a reason.
 *
 * All four are pre-existing and none discloses a screening outcome: two are client-facing copy in
 * the demo fixtures asking for photo ID, one is an onboarding *step key* in a test fixture, and one
 * is a comment naming the onboarding case type. None of them come from `compliance.*`.
 */
const ALLOWED: { snippet: string; why: string }[] = [
	{
		snippet: 'ongoing KYC requirements',
		why: 'src/lib/demo.ts — document-request copy asking the client for photo ID. A request for evidence, not a screening result.'
	},
	{
		snippet: 'keep your KYC records current',
		why: 'src/lib/demo.ts — the expired-passport document-request copy. Same: a request, not an outcome.'
	},
	{
		snippet: "'kyc_form'",
		why: 'src/lib/onboarding.test.ts — an onboarding step key in a fixture. Step keys come from the flow definition; this one collects information from the client, it does not report screening.'
	},
	{
		snippet: 'profile/KYC case',
		why: 'src/lib/onboarding.ts + src/routes/onboarding/+page.server.ts — comments naming the onboarding case type.'
	}
];

const SELF = 'src/lib/aml-leak.test.ts';

describe('client portal AML surface', () => {
	it('references no AML/screening vocabulary anywhere in src', () => {
		const files = globSync('src/**/*.{ts,js,svelte,json,html,css,md}').filter(
			// The banned words appear in this file by construction.
			(file) => file.replaceAll('\\', '/') !== SELF
		);
		// A guard that silently matches nothing is not a guard.
		expect(files.length).toBeGreaterThan(50);

		const offenders = files.flatMap((file) => {
			const lines = readFileSync(file, 'utf8').split('\n');
			return lines.flatMap((line, i) => {
				const lower = line.toLowerCase();
				const hit = BANNED.find((term) => lower.includes(term));
				if (!hit) return [];
				if (ALLOWED.some(({ snippet }) => line.includes(snippet))) return [];
				return [`${file}:${i + 1} mentions '${hit}' — ${line.trim().slice(0, 120)}`];
			});
		});

		expect(
			offenders,
			'The client portal must carry no AML/screening surface. Showing a client their own ' +
				'sanctions/PEP/adverse-media position — or that a search exists against them at all — is ' +
				'tipping off, an offence under POCA 2002 s.333A. The database already refuses to serve it ' +
				"(compliance.*'s RLS policy omits auth.contact_scope() on purpose), so a reference here " +
				'is either a screen that will never render data, or a sign someone is about to relax that ' +
				'policy to feed it. Remove the reference; add to ALLOWED only for something that is ' +
				'provably not a screening disclosure, with a reason.'
		).toEqual([]);
	});

	it('has no dead exceptions', () => {
		// An exception that no longer matches anything is a stale licence to leak: it should be
		// removed in the change that removes its last use, and this is what notices.
		const sources = globSync('src/**/*.{ts,js,svelte,json,html,css,md}')
			.filter((file) => file.replaceAll('\\', '/') !== SELF)
			.map((file) => readFileSync(file, 'utf8'));
		const dead = ALLOWED.filter(({ snippet }) => !sources.some((s) => s.includes(snippet))).map(
			({ snippet }) => snippet
		);
		expect(dead, 'unused AML-vocabulary exceptions — delete them').toEqual([]);
	});
});
