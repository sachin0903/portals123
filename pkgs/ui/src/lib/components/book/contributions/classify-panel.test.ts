/**
 * The classify panel asks ONE question and offers ONE act.
 *
 * It grew, over the life of the workbench, into a form with five controls and three buttons: the
 * contribution type, ad-hoc-or-regular, the period, a five-field contributor block, then "Save
 * classification", "Record to Morgan Lloyd" and "Cancel". Every one of those was a real thing the
 * vendor wants — and asking them here was still wrong, because they are facts about different
 * subjects:
 *
 *  - the FREQUENCY describes a standing arrangement, not the £500 credit on screen;
 *  - the CONTRIBUTOR is the client's employer, a fact about the person, now on the client record
 *    (`crm.contacts.employer_name`) where it is stated once instead of re-typed per payment;
 *  - RECORDING to the administrator was never a separate decision from classifying, and the
 *    `confirmed` state between the two presses reads as done while nobody downstream has been told.
 *
 * So the panel asks the contribution type and presses Classify, which records it too; once the
 * administrator has it, the only act is Cancel. What is left is the tax question and nothing else.
 *
 * A SOURCE SCAN, as `contribution-form.test.ts` is: what matters is which controls exist and what
 * `save()` puts on the wire, and the panel's interactive states are out of SSR's reach.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { CREATABLE_CONTRIBUTION_CATEGORIES, OTHER_CONTRIBUTION_OUTCOMES } from './types.js';

const source = readFileSync(
	fileURLToPath(new URL('./classify-panel.svelte', import.meta.url)),
	'utf8'
);
/** Comments stripped: they name the very controls that were removed, and a raw scan would report
 *  the explanation as the defect — the false positive `switch-columns.test.ts` records hitting. */
const template = source
	.slice(source.indexOf('</script>'))
	.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\/[^\n]*/g, '');
/** What `save()` sends. */
const payload = source.slice(source.indexOf('onsave?.('), source.indexOf('</script>'));

describe('the classify panel asks', () => {
	it('the contribution type as a button group over the creatable pair', () => {
		expect(template).toContain('options={CREATABLE_CONTRIBUTION_CATEGORIES}');
		expect(template).toMatch(/<Segmented[\s\S]*?CREATABLE_CONTRIBUTION_CATEGORIES/);
		expect(CREATABLE_CONTRIBUTION_CATEGORIES).toHaveLength(2);
	});

	it('and sizes that group to its own labels, never to the panel', () => {
		// `Segmented`'s root is already `inline-flex`. `w-full` stretched the pill across the 420px
		// panel while its two buttons kept their padding, so "Personal" and "Employer" sat in the
		// left third of a wide grey trough that reads as a broken control.
		expect(template).toMatch(/<Segmented bind:value=\{category\}[^>]*\/>/);
		expect(template).not.toMatch(/<Segmented[\s\S]{0,160}class="w-full"/);
	});

	it('nothing else — the type is the whole form', () => {
		expect(payload).toContain('category');
		// `paid_by` is derived from it rather than asked, so the two cannot disagree.
		expect(payload).toContain('paid_by: paidBy');
		expect(source).toContain('paidByFor(category)');
	});
});

describe('the classify panel never asks', () => {
	it('who paid', () => {
		expect(template).not.toContain('cls-paid-by');
		expect(template).not.toContain('Paid by</Label>');
	});

	it('for the tax year', () => {
		expect(template).not.toContain('cls-tax-year');
		expect(template).not.toContain('Tax year</Label>');
		// Nor sends one: `categorise` COALESCEs the parameter, so omitting it leaves the derived
		// value standing rather than overwriting it with whatever the panel happened to show.
		expect(payload).not.toContain('tax_year');
	});

	it('ad-hoc or regular, or a period', () => {
		// A credit that has landed is one payment; whether the client pays monthly is a fact about
		// the standing arrangement. Asking it here let a single detected credit be marked
		// "quarterly", which is what `cam_frequency` would then carry for ever.
		expect(template).not.toContain('options={PAYMENT_TYPES}');
		expect(template).not.toContain('REGULAR_FREQUENCIES');
		expect(template).not.toContain('cls-frequency');
		// And sends no frequency at all, for the same COALESCE reason as the tax year.
		expect(payload).not.toContain('frequency');
	});

	it('for the contributor, at any of its five fields', () => {
		for (const id of ['cls-employer', 'cls-line1', 'cls-city', 'cls-postcode', 'cls-email']) {
			expect(template, id).not.toContain(id);
		}
		expect(payload).not.toContain('employer:');
		// It reads the client's own employer instead, and says so before the API refuses.
		expect(source).toContain('client_employer_name');
	});

	it('for HMRC eligibility checks', () => {
		expect(template).not.toContain('HMRC checks');
		expect(template).not.toContain('EligibilityList');
		expect(template).not.toContain('Classify despite');
		expect(source).not.toContain('eligibilityChecks');
		expect(source).not.toContain('isBlocked');
	});
});

describe('the classify panel offers', () => {
	/** The footer — the panel's action bar, which is where every control now lives. It opens with
	 *  the editable branch and the settled one is its `{:else}`. */
	const footer = template.slice(template.indexOf('mt-auto space-y-2 border-t p-4'));
	const [classifiable, settled] = [
		footer.slice(0, footer.indexOf('{:else}')),
		footer.slice(footer.indexOf('{:else}'))
	];

	it('Classify as the only act while it is still classifiable', () => {
		expect(classifiable).toMatch(/\bClassify\b/);
		// The second press is gone: classifying records to the administrator in the same act, so
		// there is no `confirmed` limbo that reads as done while nobody downstream has been told.
		expect(classifiable).not.toContain('Record to Morgan Lloyd');
		expect(classifiable).not.toContain('Save classification');
		expect(source).not.toContain('onrecord');
		// Nor anything else. An adviser who has not said what a credit is has nothing to cancel.
		expect(classifiable).not.toContain('cancelControl');
		expect(classifiable).not.toContain('Refresh from Morgan Lloyd');
	});

	it('Cancel once the administrator holds it', () => {
		expect(settled).toContain('@render cancelControl');
		// And the API refuses past `ras_applied`, so the control hides itself there rather than
		// offering a press that 409s — a reclaim already in flight at HMRC cannot be unwound here.
		expect(source).toContain('RELIEF_CLAIMED.has(c.status)');
	});

	it('no reconciliation and no relief-in-transit sentence', () => {
		// "Booked / Forecast / Received" and "£125 of basic-rate relief is still to be credited"
		// describe a lifecycle the person classifying a credit has no part in: the money has
		// already arrived, and relief is reclaimed by the scheme in a monthly batch.
		expect(template).not.toContain('ReconciliationPanel');
		expect(template).not.toContain('moneyPosition');
		expect(source).not.toContain('basic-rate\n');
	});
});

describe('the classify panel keeps', () => {
	it('the disposal routes reachable', () => {
		// Not in the button group — they are not contribution types — but reachable, because the
		// queue is only clearable if a credit can be said to be something else.
		expect(template).toContain('OTHER_CONTRIBUTION_OUTCOMES');
		expect(OTHER_CONTRIBUTION_OUTCOMES.map((o) => o.value)).toContain('not_a_contribution');
	});

	it('and offers no third-party payer among them', () => {
		// A payer variation dressed as a category: for relief purposes a third party's payment IS
		// the member's contribution, so the classification stays binary.
		expect(OTHER_CONTRIBUTION_OUTCOMES.map((o) => o.value)).not.toContain(
			'third_party_contribution'
		);
	});

	it('a stated outcome instead of an empty button group when one is chosen', () => {
		// A Segmented holding a value outside its options renders with no segment active, which reads
		// as "nothing is selected" on a record that is in fact classified.
		expect(source).toContain('IS_OTHER_OUTCOME.has(category)');
		expect(template).toMatch(/\{#if otherOutcome\}/);
	});

	it('the settled view showing the payer, the money date and when it was lodged', () => {
		// The controls went; the FACTS a file review needs did not.
		expect(template).toContain("field('Paid by'");
		// "Transaction date", not "Contribution date": it is the date the MONEY moved, which is what
		// `matching.link` stamps off the credit and what a reconciliation reads against.
		expect(template).toMatch(/field\(\s*'Transaction date'/);
		expect(template).not.toContain("field('Contribution date'");
		// The stage vocabulary calls the absence of this "Not lodged", so the record can say when
		// it stopped being true.
		expect(template).toContain("field('Lodged on'");
	});

	it('and states neither a frequency nor a tax year', () => {
		// Neither is read anywhere: the panel stopped ASKING for a frequency (a credit that has
		// landed is one payment), and the tax year is derived server-side from the date beside it.
		// Both are still stored and still sent to Morgan Lloyd.
		expect(template).not.toContain("field('Frequency'");
		expect(template).not.toContain("field('Tax year'");
	});

	it('no relief block on a disposal, in either mode', () => {
		// A transfer in and a not-a-contribution earn no relief and never reach Morgan Lloyd, so a
		// gross-up beside one states an amount nobody will ever reclaim.
		expect(template).toMatch(/\{#if pushable\}[\s\S]{0,400}Relief at source/);
		expect(template).toMatch(/\{#if !UNPUSHABLE_CATEGORIES\.has\(c\.category\)\}/);
	});

	it('a reversible disposal, so a mis-click is not permanent', () => {
		// `not_a_contribution` is written AND cancelled in one call, landing on a terminal status
		// that `CLASSIFIABLE` excludes — the credit left the queue with no way back but SQL.
		expect(source).toContain('UNPUSHABLE_CATEGORIES.has(contribution.category)');
		expect(source).toContain('!contribution.ml_contribution_id');
		expect(source).toMatch(/CLASSIFIABLE\.has\(contribution\.status\) \|\| reopenable/);
	});

	it('both names it prints addressable, each guarded on its own builder and uid', () => {
		// The panel names a person and an account, and the sub-heading naming the account was the
		// only one of the two that could not be opened. Both are guarded the same way — builder AND
		// uid — so a mount with no route for one of them renders that name as plain text rather than
		// a dead link. `links.account` is optional, which is what keeps the admin overlay correct.
		expect(template).toMatch(/\{#if links && c\.contact_uid\}/);
		expect(template).toMatch(/\{#if links\?\.account && c\.account_uid\}/);
		expect(template).toContain('href={links.account(c.account_uid)}');
		// By uid, never the serial the row also carries.
		expect(template).not.toContain('links.account(c.account_id)');
	});

	it('the payment provenance where the expectation badge used to be', () => {
		// That badge asked "has the custodian been told to expect this", which for money that has
		// already arrived is a question about a forecast nobody raised — a bare "—" on every
		// detected row.
		expect(source).not.toContain('ExpectationBadge');
		expect(source).toContain('transaction_method');
		expect(source).toContain('transaction_type_label');
	});
});
