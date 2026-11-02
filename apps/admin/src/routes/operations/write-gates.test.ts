import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = (path: string) => readFileSync(new URL(path, import.meta.url), 'utf8');

describe('operations management server guards', () => {
	it.each([
		['users', './users/+page.server.ts', 8],
		['roles', './roles/+page.server.ts', 2]
	])('%s refuses read-only operators before calling the API', (_name, path, actionCount) => {
		const body = source(path);
		expect(body).toContain("'operations:write'");
		expect(body.match(/const refused = writeRefusal\(locals,/g)).toHaveLength(actionCount);
		expect(body.match(/if \(refused\) return refused;/g)).toHaveLength(actionCount);
	});
});
