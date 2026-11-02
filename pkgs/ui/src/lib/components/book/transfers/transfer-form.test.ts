/**
 * The raise-transfer drawer's SHAPE — the questions it asks and, more importantly, the ones it
 * does not.
 *
 * Cash vs in-specie is always asked and never defaulted (Cash means the ceding scheme sells).
 * Full vs partial and the pension facts are gated by `formSpecFor` off the receiving account.
 * The wrapper, the custodian and a custody-route picker are not asked: the account already
 * answers them, and restating them is a question with one correct answer an adviser can still
 * get wrong.
 *
 * A SOURCE SCAN, because the drawer's body renders through a portal that SSR does not run:
 * `render()` of a Sheet returns empty markup, which makes every "does not contain" assertion
 * pass for the wrong reason.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import {
	CEDING_CLIENT_ADDRESS_INFO,
	PENSION_TYPE_OPTIONS,
	SAFEGUARDED_BENEFITS_QUESTION,
	SAFEGUARDED_FCA_INFO,
	TRANSFER_BASIS_OPTIONS,
	TRANSFER_CRYSTALLISATION_OPTIONS,
	TRANSFER_EXTENT_OPTIONS
} from './api.js';

const source = readFileSync(
	fileURLToPath(new URL('./transfer-form.svelte', import.meta.url)),
	'utf8'
);
/** Comments stripped: this file's own header names the removed fields, and a scan over the raw
 *  text would report the explanation as the defect. */
const template = source
	.slice(source.indexOf('</script>'))
	.replace(/<!--[\s\S]*?-->/g, '')
	.replace(/\/\/[^\n]*/g, '');
/** The fetch body — what is actually sent, as distinct from what is on screen. */
const payload = source.slice(
	source.indexOf('body: JSON.stringify('),
	source.indexOf('const body =')
);

describe('the transfer drawer asks', () => {
	it('cash or in-specie, as a Segmented with no default', () => {
		// The options are narrowed per account (a SEI pension cannot take cash), so the control is
		// bound to the derived list rather than the constant.
		expect(template).toMatch(/<Segmented[\s\S]*?options=\{basisOptions\}/);
		expect(source).toMatch(/spec\.allowCash[\s\S]*?TRANSFER_BASIS_OPTIONS/);
		expect(source).toMatch(/filter\(\(option\) => option\.value !== 'cash'\)/);
		expect(TRANSFER_BASIS_OPTIONS.map((o) => o.value)).toEqual(['cash', 'in_specie']);
		// No default: Cash means the ceding scheme liquidates, and both values are valid.
		expect(source).toMatch(/let basis = \$state<string \| undefined>\(undefined\)/);
		expect(template).not.toMatch(/<Segmented[\s\S]{0,160}class="w-full"/);
	});

	it('full or partial when the account is not Seccl-only', () => {
		expect(template).toMatch(/\{#if spec\.askExtent\}[\s\S]*?TRANSFER_EXTENT_OPTIONS/);
		expect(TRANSFER_EXTENT_OPTIONS.map((o) => o.value)).toEqual(['full', 'partial']);
	});

	it('a transfer value on every account', () => {
		expect(template).toContain('id="transfer-value"');
		expect(payload).toContain('total_value');
	});

	it('crystallisation and safeguarded rights on a pension, pension type on an SEI SIPP', () => {
		expect(template).toMatch(
			/\{#if spec\.askPensionFacts\}[\s\S]*?TRANSFER_CRYSTALLISATION_OPTIONS/
		);
		expect(template).toMatch(/\{#if spec\.askPensionType\}[\s\S]*?id="transfer-pension-type"/);
		expect(template).toContain('PENSION_TYPE_OPTIONS');
		expect(template).toContain('SAFEGUARDED_BENEFITS_QUESTION');
		expect(template).toMatch(/onCheckedChange=\{\(on\) => \(safeguardedBenefits = on === true\)\}/);
		expect(template).toContain('Drawdown status');
		expect(template).toContain('SAFEGUARDED_FCA_INFO');
		expect(template).toMatch(/\{#if safeguardedBenefits\}[\s\S]*?id="transfer-fca"/);
		expect(template).not.toContain('askCedingAddress && safeguardedBenefits');
		expect(template).toContain('min-h-0 flex-1 space-y-4 overflow-y-auto');
		expect(source).toMatch(/safeguardedBenefits \? \{ fca_reference: fcaReference\.trim\(\) \}/);
		expect(TRANSFER_CRYSTALLISATION_OPTIONS.map((o) => o.value)).toEqual([
			'uncrystallised',
			'partial',
			'crystallised'
		]);
		expect(PENSION_TYPE_OPTIONS).toHaveLength(20);
		expect(SAFEGUARDED_BENEFITS_QUESTION).toContain('Pension Schemes Act 2015');
		expect(SAFEGUARDED_FCA_INFO).toContain('Safeguarded Rights');
		expect(payload).toContain('crystallisation');
		expect(payload).toContain('pension_type');
		expect(payload).toContain('safeguarded_benefits');
		expect(payload).toContain('crystallised_value');
		expect(template.indexOf('id="transfer-safeguarded"')).toBeGreaterThan(
			template.indexOf('id="transfer-reference"')
		);
	});

	it('the crystallised amount only when some of the pot has been crystallised', () => {
		expect(template).toMatch(/\{#if needsCrystallisedAmount\}[\s\S]*?id="transfer-crystallised"/);
		expect(template).toContain('The crystallised amount of the transfer');
		expect(template).not.toContain('id="transfer-uncrystallised"');
		expect(payload).not.toContain('uncrystallised_value');
	});

	it('starts from the client address and offers lookup when that is turned off', () => {
		expect(template).toContain('Use current client address');
		expect(template).toContain('CEDING_CLIENT_ADDRESS_INFO');
		expect(CEDING_CLIENT_ADDRESS_INFO).toContain('ceding provider');
		expect(template).toMatch(/useClientAddress[\s\S]*?<AddressLookup/);
		expect(template).toContain('Enter address manually');
		expect(template).not.toContain('nothing to choose');
	});

	it('and sends basis, extent, and the SEI lines when the account needs them', () => {
		expect(payload).toContain('basis');
		expect(payload).toContain('extent');
		expect(payload).not.toContain('needsSeiHoldings');
		expect(payload).not.toContain('assets:');
		expect(template).not.toContain('Add holding');
		expect(payload).toContain('external_account_type');
		expect(source).toContain('formSpecFor(account)');
	});

	it('is the money-movement form drawer, full-width on a phone', () => {
		expect(template).toContain('sm:max-w-xl');
		expect(template).not.toMatch(/!w-\[\d+vw\]/);
	});

	it('puts raise and cancel on one row', () => {
		expect(template).toContain('flex-row justify-end');
	});
});

describe('the transfer drawer never asks', () => {
	it('for a custody route or wrapper', () => {
		expect(template).not.toContain('ml_sei');
		expect(template).not.toContain('Custody');
		expect(template).not.toMatch(/\bSIPP\b/);
		expect(payload).not.toContain('transfer_type');
		expect(payload).not.toContain('route');
	});

	it('for two money boxes that can disagree with the total', () => {
		expect(template).not.toMatch(/askCrystallisedSplit/);
	});
});
