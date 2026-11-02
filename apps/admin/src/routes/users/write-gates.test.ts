import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

describe('cross-tenant user detail write gates', () => {
	it('refuses every mutation without tenants:write before calling the API', () => {
		const body = readFileSync(new URL('./[uid]/+page.server.ts', import.meta.url), 'utf8');
		expect(body).toContain("'tenants:write'");
		expect(body.match(/const refused = writeRefusal\(locals,/g)).toHaveLength(5);
		expect(body.match(/if \(refused\) return refused;/g)).toHaveLength(5);
	});
});
