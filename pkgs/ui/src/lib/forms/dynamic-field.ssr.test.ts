import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import DynamicField from './dynamic-field.svelte';
import type { FormField } from './types.js';

/**
 * SSR pins for how a field's declared type reaches the DOM.
 *
 * Two of these are regressions with a visible cost rather than hypotheticals: a select that
 * rendered a size shorter than the inputs beside it, and a closed two-way choice that hid half
 * its answer behind a click.
 */

const field = (over: Partial<FormField> = {}): FormField => ({
	uid: 'f-1',
	fieldKey: 'basis',
	fieldType: 'select',
	label: 'Basis',
	section: 'Plan',
	description: null,
	required: false,
	conditions: null,
	validation: null,
	options: [
		{ value: 'Accumulation', label: 'Accumulation' },
		{ value: 'Drawdown', label: 'Drawdown' }
	],
	sortOrder: 0,
	...over
});

const body = (over: Partial<FormField> = {}, values: Record<string, unknown> = {}) =>
	render(DynamicField, { props: { field: field(over), values, errors: {} } }).body;

describe('DynamicField — control choice', () => {
	it('renders a select at the default trigger size, matching the inputs beside it', () => {
		// It passed size="sm", which is h-7 while every Input is h-8 — a select sat a pixel short of
		// its row on every form in the app.
		const out = body();
		expect(out).toContain('data-size="default"');
		expect(out).not.toContain('data-size="sm"');
	});

	it('renders a segmented field as a button group, with every option visible', () => {
		const out = body({ fieldType: 'segmented' });
		expect(out).toContain('role="tablist"');
		expect(out).toContain('Accumulation');
		expect(out).toContain('Drawdown');
		// Not a dropdown: no trigger to open.
		expect(out).not.toContain('data-slot="select-trigger"');
	});

	it('marks the chosen segment selected, and only it', () => {
		const out = body({ fieldType: 'segmented' }, { basis: 'Drawdown' });
		const selected = [...out.matchAll(/aria-selected="(true|false)"/g)].map((m) => m[1]);
		expect(selected).toEqual(['false', 'true']);
	});

	it('uses square corners when the field asks for them, and pills otherwise', () => {
		expect(body({ fieldType: 'segmented' })).toContain('rounded-full');
		expect(body({ fieldType: 'segmented' })).not.toContain('rounded-md');
		expect(body({ fieldType: 'segmented', validation: { shape: 'square' } })).toContain(
			'rounded-md'
		);
	});

	it('leaves every segment unselected when there is no answer yet', () => {
		// Segmented takes `value?`, so "not answered" is a state it can render. A control that
		// defaulted to its first option would put an answer on the record nobody gave.
		const out = body({ fieldType: 'segmented' });
		expect(out).not.toContain('aria-selected="true"');
	});

	it('marks an optional field with a separate OPTIONAL tag, not a bracketed label', () => {
		const out = body();
		expect(out).toContain('>Optional<');
		expect(out).toContain('uppercase');
		// The label is the question. It must not absorb the footnote.
		expect(out).not.toContain('Basis (optional)');
	});

	it('says nothing at all on a required field', () => {
		expect(body({ required: true })).not.toContain('>Optional<');
	});

	it('says nothing on a display field either, which asks for nothing', () => {
		expect(body({ fieldType: 'display', required: false })).not.toContain('>Optional<');
	});

	it('offers gender from the shared list, as a button group, with nothing authored on the field', () => {
		const out = body({ fieldType: 'gender', options: null });
		expect(out).toContain('role="tablist"');
		expect(out).toContain('Male');
		expect(out).toContain('Female');
	});

	it('offers marital status as a select, and shows the shared label for the stored slug', () => {
		const out = body(
			{ fieldType: 'marital', fieldKey: 'marital_status', options: null },
			{ marital_status: 'widowed' }
		);
		expect(out).toContain('data-slot="select-trigger"');
		expect(out).toContain('Widowed');
	});

	it('derives the national identifier label and example from nationality', () => {
		const out = render(DynamicField, {
			props: {
				field: field({
					fieldKey: 'national_identifier',
					fieldType: 'nationalIdentifier',
					label: 'National identifier'
				}),
				values: { nationality: ['GB'] },
				errors: {},
				dataSources: {
					countries: [
						{
							code: 'GB',
							inEea: true,
							identifiers: [
								{
									name: 'National Insurance Number',
									localeName: 'National Insurance Number',
									example: 'QQ123456C',
									regex: [],
									priority: 0
								}
							]
						}
					]
				}
			}
		}).body;
		expect(out).toContain('National Insurance Number');
		expect(out).toContain('placeholder="QQ123456C"');
	});

	it('refuses a National Insurance number that is not one HMRC issues, as it is typed', () => {
		const out = render(DynamicField, {
			props: {
				field: field({
					fieldKey: 'national_identifier',
					fieldType: 'nationalIdentifier',
					label: 'National identifier'
				}),
				values: { nationality: ['GB'], national_identifier: 'QQsdchdsbcisuNNnnxsacsui' },
				errors: {},
				dataSources: {
					countries: [
						{
							code: 'GB',
							inEea: true,
							identifiers: [
								{
									name: 'National Insurance Number',
									localeName: 'National Insurance Number',
									example: 'QQ123456C',
									regex: [],
									priority: 0
								}
							]
						}
					]
				}
			}
		}).body;
		expect(out).toContain('Enter a valid National Insurance number');
		expect(out).toContain('aria-invalid="true"');
	});

	it('keeps a manual-address route beside Postcoder', () => {
		const out = body({
			fieldKey: 'address',
			fieldType: 'address',
			label: 'Residential address'
		});
		expect(out).toContain('Start typing an address or postcode');
		expect(out).toContain('Enter address manually');
	});

	it('renders an international country selector and country-aware address summary', () => {
		const out = render(DynamicField, {
			props: {
				field: field({
					fieldKey: 'address',
					fieldType: 'address',
					label: 'Residential address',
					required: true
				}),
				values: {
					address: {
						line1: '123 Rue Brizeux',
						city: 'Plouvien',
						region: 'Finistère',
						postcode: '29860',
						country: 'FR'
					}
				},
				errors: {},
				dataSources: {
					countries: [
						{ code: 'FR', name: 'France' },
						{ code: 'GB', name: 'United Kingdom' }
					]
				}
			}
		}).body;
		expect(out).toContain('>Country<');
		expect(out).toContain('France');
		expect(out).toContain('123 Rue Brizeux, Plouvien, Finistère, 29860, France');
		expect(out).toContain('Start typing an address, city or postal code');
	});
});
