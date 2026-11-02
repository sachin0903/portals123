import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const root = 'src/routes/tenants/[uid]/operate/content/[contentUid]/edit';

describe('tenant content upload proxies', () => {
	it('rebuilds multipart bodies and targets the selected firm', () => {
		for (const route of ['images', 'hero', 'guide']) {
			const source = readFileSync(`${root}/${route}/+server.ts`, 'utf8');
			expect(source).toContain('new FormData()');
			expect(source).toContain("out.set('file', file, file.name)");
			expect(source).toContain('params.uid');
			expect(source).toContain('targetRequest');
		}
	});

	it('refuses uploads when Operate is read-only', () => {
		for (const route of ['images', 'hero', 'guide']) {
			const source = readFileSync(`${root}/${route}/+server.ts`, 'utf8');
			expect(source).toContain('canOperateWrite(locals)');
			expect(source).toContain('status: 403');
		}
	});
});
