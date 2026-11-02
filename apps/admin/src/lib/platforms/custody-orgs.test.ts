import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import {
	custodyFromLabel,
	hydrateCustodyOrgs,
	inferCustodyOrgs,
	subfirmIdsFromCustodyOrgs,
	UNASSIGNED_ORG
} from './custody-orgs';

describe('inferCustodyOrgs', () => {
	const subfirms = [
		{ value: '704254', label: 'Select Platform - Onshore' },
		{ value: '704766', label: 'Select Platform - Offshore' },
		{ value: '704724', label: 'Alpha Investment Office - Onshore' }
	];

	it('maps labelled onshore/offshore subfirms for the selected slice', () => {
		expect(inferCustodyOrgs(subfirms, ['704254', '704766'])).toEqual({
			onshore: '704254',
			offshore: '704766'
		});
	});

	it('keeps only the selected subfirm when the slice is one side', () => {
		expect(inferCustodyOrgs(subfirms, ['704254'])).toEqual({ onshore: '704254' });
	});

	it('treats a sole unlabelled subfirm as onshore', () => {
		expect(inferCustodyOrgs([{ value: '9001', label: 'Desk A' }])).toEqual({ onshore: '9001' });
	});

	it('cannot split two unlabelled subfirms', () => {
		expect(
			inferCustodyOrgs([
				{ value: 'a', label: 'Desk A' },
				{ value: 'b', label: 'Desk B' }
			])
		).toEqual({});
	});
});

describe('hydrateCustodyOrgs', () => {
	const subfirms = [
		{ value: '704254', label: 'Select Platform - Onshore' },
		{ value: '704766', label: 'Select Platform - Offshore' }
	];

	it('keeps a saved write map', () => {
		expect(hydrateCustodyOrgs({ onshore: '704254' }, subfirms, ['704766'])).toEqual({
			onshore: '704254'
		});
	});

	it('infers from a legacy subfirm slice when nothing is attached', () => {
		expect(hydrateCustodyOrgs({}, subfirms, ['704254', '704766'])).toEqual({
			onshore: '704254',
			offshore: '704766'
		});
	});

	it('leaves both sides blank when there is no map and no legacy slice', () => {
		expect(hydrateCustodyOrgs({}, subfirms)).toEqual({});
	});
});

describe('subfirmIdsFromCustodyOrgs', () => {
	it('derives the read slice from the write map', () => {
		expect(subfirmIdsFromCustodyOrgs({ onshore: '704254', offshore: '704766' })).toEqual([
			'704254',
			'704766'
		]);
	});

	it('drops the unassigned sentinel', () => {
		expect(subfirmIdsFromCustodyOrgs({ onshore: UNASSIGNED_ORG, offshore: '704766' })).toEqual([
			'704766'
		]);
	});
});

describe('custodyFromLabel', () => {
	it('reads the SEI suffix', () => {
		expect(custodyFromLabel('UCAP Asset Management (UK) Ltd - Offshore')).toBe('offshore');
		expect(custodyFromLabel('Select Platform - Onshore')).toBe('onshore');
		expect(custodyFromLabel('Not a split')).toBeNull();
	});
});

describe('the SEI platforms card', () => {
	const source = readFileSync(
		new URL('../../routes/tenants/[uid]/settings/platforms/ProviderCard.svelte', import.meta.url),
		'utf8'
	);
	const server = readFileSync(
		new URL('../../routes/tenants/[uid]/settings/platforms/+page.server.ts', import.meta.url),
		'utf8'
	);

	it('saves custody_orgs with the enablement, not on the product', () => {
		expect(source).toContain('name="custody_org_{key}"');
		expect(server).toContain('custody_orgs');
		expect(server).not.toContain('seiFirms');
	});

	it('is firm, DSR, then optional onshore and offshore — not a subfirm multi-select', () => {
		expect(source).not.toContain('id="{provider}-subfirms"');
		expect(source).not.toContain('name="subfirm_ids"');
		expect(source.indexOf('for="{provider}-firm"')).toBeLessThan(
			source.indexOf('for="{provider}-client-template-individual"')
		);
		expect(source.indexOf('for="{provider}-client-template-individual"')).toBeLessThan(
			source.indexOf('for="{provider}-client-template-organisation"')
		);
		expect(source.indexOf('for="{provider}-client-template-organisation"')).toBeLessThan(
			source.indexOf('for="{provider}-dsrs"')
		);
		expect(source).toContain('name="client_template_individual_id"');
		expect(source).toContain('name="client_template_organisation_id"');
		expect(server).toContain('client_template_individual_id');
		expect(server).toContain('client_template_organisation_id');
		expect(server).toContain('client_template_id');
		expect(source.indexOf('for="{provider}-dsrs"')).toBeLessThan(
			source.indexOf('{#each CUSTODY_KEYS')
		);
		expect(source).toContain('name="custody_org_{key}"');
		expect(source).toContain('name="custody_dsr_{key}"');
		expect(source).not.toContain('name="custody_org_onshore"');
		expect(source).not.toContain('name="custody_dsr_onshore"');
		expect(server).toContain('custody_dsrs');
		expect(source).toContain('grid grid-cols-2');
		expect(source).not.toContain('An individual is opened with the first template');
		expect(source).toContain('Restricts the tenant to the DSRs specified');
		expect(source).toContain('Onshore custody');
		expect(source).toContain('Offshore custody');
		expect(source).toContain('Custody defined by sub firms');
		// Per-model DSR teams moved off the card onto the model editor; the card gains the
		// custodian pay-in account (account name / sort code / account number) instead.
		expect(source).not.toContain('name="model_dsr_{model.uid}"');
		expect(source).toContain('Custodian pay-in account');
		expect(source).toContain('name="pay_in_account_name"');
		expect(source).toContain('name="pay_in_sort_code"');
		expect(source).toContain('name="pay_in_account_number"');
		expect(server).toContain('pay_in_account_name');
		expect(server).toContain('subfirmIdsFromCustodyOrgs');
		expect(source).toContain('id="{provider}-firm"');
		expect(source).not.toContain('placeholder="Firm id"');
	});
});
