import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const source = readFileSync(new URL('./audit-log.svelte', import.meta.url), 'utf8');

describe('audit log actor column', () => {
	it('sorts by the same System fallback the cell displays', () => {
		expect(source).toContain("return event.actor_email ?? 'System';");
		expect(source).toContain('sortValue: actorLabel');
		expect(source).toContain('{actorLabel(e)}');
		expect(source).not.toContain("e.actor_email ?? e.actor_name ?? ''");
	});
});
