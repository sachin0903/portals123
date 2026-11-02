import { describe, expect, it } from 'vitest';
import contract from './flow-contract.json' with { type: 'json' };
import { MAPPING_TARGETS, MAPPING_TRANSFORMS, mappingLabel, readMapping } from './flows.js';

describe('the fill_form mapping vocabulary', () => {
	it("is the contract's, not a restatement of it", () => {
		// Restating it here is how the editor comes to offer a transform the mapper cannot run —
		// which is a MappingError at publish, i.e. a legal-looking choice that fails on save.
		expect([...MAPPING_TRANSFORMS]).toEqual(contract.mapping.transforms);
		expect([...MAPPING_TARGETS]).toEqual(contract.mapping.targets);
	});

	it('reads a column target as a person would say it', () => {
		expect(mappingLabel('national_identifier_type')).toBe('National identifier type');
	});
});

describe('readMapping', () => {
	it('keeps a well-formed row whole', () => {
		expect(
			readMapping([{ source: 'dob', target: 'dob', transforms: ['iso_date'], required: true }])
		).toEqual([{ source: 'dob', target: 'dob', transforms: ['iso_date'], required: true }]);
	});

	it('survives anything a hand-edited config can hold', () => {
		// `config` is free-form jsonb. A definition written by an older build, or edited by hand,
		// must not take the inspector down — a crashed panel is how an author loses the rest of the
		// step's settings too.
		expect(readMapping(null)).toEqual([]);
		expect(readMapping('nonsense')).toEqual([]);
		expect(readMapping([null, 42, 'x'])).toEqual([]);
		expect(readMapping([{ source: 'a' }])).toEqual([]); // no target
		expect(readMapping([{ target: 'b' }])).toEqual([]); // no source
	});

	it('normalises a row that omits its optional halves', () => {
		expect(readMapping([{ source: 'a', target: 'b' }])).toEqual([
			{ source: 'a', target: 'b', transforms: [], required: false }
		]);
	});

	it('drops a non-string transform rather than passing it to the mapper', () => {
		expect(
			readMapping([{ source: 'a', target: 'b', transforms: ['trim', 7] }])[0].transforms
		).toEqual(['trim']);
	});
});
