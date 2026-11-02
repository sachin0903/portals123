/**
 * The seeded onboarding journey's step list, as `app/flows/onboarding_seed.py::_build_definition`
 * emits it — the definition that actually matters, because every tenant gets exactly this one out
 * of the box and it is the flow the legacy → v3 upgrade must not disturb.
 *
 * Generated from the Python seed (form/document uids replaced with stable placeholders, and the
 * fact-find's 30-entry `mapping` truncated to two — it is a field map, and none of the rules under
 * test read it). Regenerate it the same way if the seed changes; it is a fixture, not a mirror
 * anything asserts against, so it does not need a pin test of its own.
 */
import type { FlowStep } from './flows.js';

export const ONBOARDING_SEED_STEPS: FlowStep[] = [
	{
		id: 'core',
		type: 'fill_form',
		title: 'Applicant details',
		scope: 'participant',
		assignee: 'staff',
		visibility: {
			field: 'applicant.type',
			operator: 'equals',
			value: 'individual'
		},
		config: {
			form_uid: 'form_applicant_core',
			mode: 'gather',
			mapping: [
				{
					source: 'first_name',
					target: 'first_name',
					transforms: ['trim']
				},
				{
					source: 'middle_name',
					target: 'middle_name',
					transforms: ['trim']
				}
			],
			title: 'Applicant details'
		}
	},
	{
		id: 'org',
		type: 'fill_form',
		title: 'Organisation details',
		scope: 'participant',
		assignee: 'staff',
		visibility: {
			field: 'applicant.type',
			operator: 'equals',
			value: 'organisation'
		},
		config: {
			form_uid: 'form_organisation_core',
			title: 'Organisation details'
		}
	},
	{
		id: 'additional_info',
		type: 'fill_form',
		title: 'Additional information',
		scope: 'participant',
		assignee: 'staff',
		visibility: {
			logic: 'and',
			conditions: [
				{
					field: 'application.custody',
					operator: 'equals',
					value: 'offshore'
				},
				{
					field: 'applicant.type',
					operator: 'equals',
					value: 'individual'
				}
			]
		},
		config: {
			form_uid: 'form_applicant_offshore',
			title: 'Additional information'
		}
	},
	{
		id: 'pension',
		type: 'fill_form',
		title: 'Pension information',
		scope: 'participant',
		assignee: 'staff',
		visibility: {
			field: 'application.has_pension_product',
			operator: 'equals',
			value: true
		},
		config: {
			form_uid: 'form_applicant_pension',
			title: 'Pension information'
		}
	},
	{
		id: 'employer',
		type: 'fill_form',
		title: 'Employer contributions',
		scope: 'participant',
		assignee: 'staff',
		visibility: {
			field: 'application.has_sipp_product',
			operator: 'equals',
			value: true
		},
		config: {
			form_uid: 'form_applicant_employer',
			title: 'Employer contributions'
		}
	},
	{
		id: 'idv',
		type: 'verify_identity',
		title: 'Verify identity',
		scope: 'participant',
		assignee: 'staff',
		depends_on: ['core'],
		config: {
			provider: 'manual'
		}
	},
	{
		id: 'aml',
		type: 'aml_check',
		title: 'AML screening',
		scope: 'participant',
		assignee: 'staff',
		depends_on: ['idv'],
		config: {}
	},
	{
		id: 'activity',
		type: 'fill_form',
		title: 'Account activity',
		scope: 'account',
		assignee: 'staff',
		visibility: {
			field: 'account.custody',
			operator: 'equals',
			value: 'offshore'
		},
		config: {
			form_uid: 'form_account_activity',
			title: 'Account activity'
		}
	},
	{
		id: 'terms_general',
		type: 'accept_terms',
		title: 'General declaration',
		scope: 'account',
		per_participant: true,
		assignee: 'client',
		visibility: {
			field: 'applicant.type',
			operator: 'equals',
			value: 'individual'
		},
		config: {
			document_uid: 'doc_general_declaration',
			title: 'General declaration',
			category: 'terms'
		}
	},
	{
		id: 'terms_isa',
		type: 'accept_terms',
		title: 'ISA declaration',
		scope: 'account',
		per_participant: true,
		assignee: 'client',
		visibility: {
			logic: 'and',
			conditions: [
				{
					field: 'applicant.type',
					operator: 'equals',
					value: 'individual'
				},
				{
					field: 'account.product',
					operator: 'equals',
					value: 'ISA'
				}
			]
		},
		config: {
			document_uid: 'doc_isa_declaration',
			title: 'ISA declaration',
			category: 'terms'
		}
	},
	{
		id: 'terms_transfer',
		type: 'accept_terms',
		title: 'Transfer declaration',
		scope: 'account',
		per_participant: true,
		assignee: 'client',
		visibility: {
			logic: 'and',
			conditions: [
				{
					field: 'applicant.type',
					operator: 'equals',
					value: 'individual'
				},
				{
					field: 'account.has_transfer',
					operator: 'equals',
					value: true
				}
			]
		},
		config: {
			document_uid: 'doc_transfer_declaration',
			title: 'Transfer declaration',
			category: 'terms'
		}
	},
	{
		id: 'terms_platform',
		type: 'accept_terms',
		title: 'Platform terms',
		scope: 'account',
		per_participant: true,
		assignee: 'client',
		visibility: {
			field: 'applicant.type',
			operator: 'equals',
			value: 'individual'
		},
		config: {
			document_uid: 'doc_platform_terms',
			title: 'Platform terms',
			category: 'terms'
		}
	},
	{
		id: 'offshore_review',
		type: 'review',
		title: 'Offshore compliance review',
		scope: 'account',
		assignee: 'staff',
		visibility: {
			field: 'account.custody',
			operator: 'equals',
			value: 'offshore'
		},
		config: {
			gate: 'offshore_approved',
			category: 'offshore',
			title: 'Offshore compliance review'
		}
	},
	{
		id: 'setup',
		type: 'setup_account',
		title: 'Open the account',
		scope: 'account',
		assignee: 'staff',
		config: {}
	}
] as unknown as FlowStep[];
