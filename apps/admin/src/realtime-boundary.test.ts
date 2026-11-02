import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';

const ROOT = fileURLToPath(new URL('../../../', import.meta.url));
const TREES = ['apps/client/src', 'apps/platform/src', 'apps/admin/src', 'pkgs/ui/src'];

function filesIn(directory: string): string[] {
	return readdirSync(directory).flatMap((entry) => {
		const path = join(directory, entry);
		return statSync(path).isDirectory() ? filesIn(path) : [path];
	});
}

const sources = TREES.flatMap((tree) => filesIn(join(ROOT, tree)))
	.filter((path) => /\.(svelte|ts)$/.test(path))
	.filter((path) => !path.endsWith('.test.ts'))
	.map((path) => ({
		path: relative(ROOT, path).replaceAll('\\', '/'),
		text: readFileSync(path, 'utf8')
	}));

describe('public portal realtime boundary', () => {
	it('never gives browser code an API origin or a direct WebSocket', () => {
		const offenders = sources.flatMap(({ path, text }) => {
			const reasons = [
				text.includes('PUBLIC_API_URL') ? 'browser API origin' : null,
				/\bnew\s+WebSocket\s*\(/.test(text) ? 'direct WebSocket' : null
			].filter((reason): reason is string => reason !== null);
			return reasons.map((reason) => `${path}: ${reason}`);
		});
		expect(offenders).toEqual([]);
	});

	it('has no browser-facing ticket proxy left behind', () => {
		expect(
			sources.filter(({ path }) => path.endsWith('/ws-ticket/+server.ts')).map(({ path }) => path)
		).toEqual([]);
	});
});
