import { describe, it, expect } from 'vitest';
import { parseCssDeclarations } from './css-validate';

describe('parseCssDeclarations', () => {
	it('parses valid declarations into a style map', () => {
		const { style, errors } = parseCssDeclarations('color: red; font-size: 14px;');
		expect(errors).toEqual([]);
		expect(style).toEqual({ color: 'red', 'font-size': '14px' });
	});

	it('preserves a url() value that contains semicolons', () => {
		const { style, errors } = parseCssDeclarations(
			'background: url("data:image/svg+xml;base64,AAAA");'
		);
		expect(errors).toEqual([]);
		expect(style.background).toBe('url("data:image/svg+xml;base64,AAAA")');
	});

	it('reports a declaration missing its colon (and applies nothing from it)', () => {
		const { style, errors } = parseCssDeclarations('color red;');
		expect(errors).toHaveLength(1);
		expect(style).toEqual({});
	});

	it('reports an invalid property name', () => {
		const { errors } = parseCssDeclarations('123: red;');
		expect(errors.some((e) => e.includes('valid CSS property'))).toBe(true);
	});

	it('reports a property with no value', () => {
		const { errors } = parseCssDeclarations('color: ;');
		expect(errors.some((e) => e.includes('no value'))).toBe(true);
	});

	it('accepts vendor- and custom-property names', () => {
		const { style, errors } = parseCssDeclarations('-webkit-transform: scale(1); --brand: #fff;');
		expect(errors).toEqual([]);
		expect(style['-webkit-transform']).toBe('scale(1)');
		expect(style['--brand']).toBe('#fff');
	});

	it('is empty for blank input', () => {
		expect(parseCssDeclarations('')).toEqual({ style: {}, errors: [] });
	});
});
