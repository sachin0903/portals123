/**
 * Catalogue writes address a row by uid.
 *
 * `/admin/tenants/{tenant_uid}/catalogue/{kind}/{uid}` resolves with `WHERE uid = :uid`
 * (`addressing.catalogue_model_id` and its siblings). Posting the serial id therefore 404s as
 * "Model not found" — which is exactly how saving a target return / model charge used to fail,
 * and the same shape on products, fees and providers. A type system cannot see this: both
 * identifiers sit on the row, and `value={editing.id}` type-checks against a path that wanted
 * `editing.uid`.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const FORMS = [
	'src/lib/catalogue/models-section.svelte',
	'src/lib/catalogue/products-section.svelte',
	'src/lib/catalogue/fees-section.svelte',
	'src/lib/catalogue/providers-section.svelte',
	'src/lib/catalogue/transfer-providers-table.svelte'
];

describe('catalogue writes address a row by uid', () => {
	it('hidden identity fields post uid, never the serial id', () => {
		const offenders: string[] = [];
		for (const path of FORMS) {
			const source = readFileSync(path, 'utf8');
			for (const match of source.matchAll(/name="id"\s+value=\{([^}]+)\}/g)) {
				offenders.push(`${path}: still posts serial as id (${match[1]})`);
			}
			for (const match of source.matchAll(/name="uid"\s+value=\{([^}]+)\}/g)) {
				if (/\.id\b/.test(match[1]) && !/\.uid\b/.test(match[1])) {
					offenders.push(`${path}: uid field bound to serial (${match[1]})`);
				}
			}
		}
		expect(offenders).toEqual([]);
	});

	it('has uid fields to scan (otherwise the assertion above is vacuous)', () => {
		let found = 0;
		for (const path of FORMS) {
			found += [...readFileSync(path, 'utf8').matchAll(/name="uid"\s+value=\{/g)].length;
		}
		expect(found).toBeGreaterThanOrEqual(5);
	});

	it('the actions interpolate uid into the catalogue path', () => {
		const source = readFileSync('src/lib/catalogue/actions.ts', 'utf8');
		expect(source).not.toMatch(/PATCH',\s*`\$\{base\}\/\$\{id\}`/);
		expect(source).toMatch(/PATCH',\s*`\$\{base\}\/\$\{uid\}`/);
		expect(source).toContain(
			"models/${str(data, 'model_uid')}/versions/${str(data, 'version_uid')}/publish"
		);
	});

	it('the history drawer is opened by uid', () => {
		const source = readFileSync('src/lib/catalogue/models-section.svelte', 'utf8');
		expect(source).toMatch(/searchParams\.set\('model',\s*model\.uid\)/);
		expect(source).not.toMatch(/searchParams\.set\('model',\s*String\(model\.id\)\)/);
	});
});
