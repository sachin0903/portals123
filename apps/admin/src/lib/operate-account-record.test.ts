/**
 * Operate-as-firm account record: Withdraw on a Morgan Lloyd pension starts a DCube session.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(
	fileURLToPath(new URL('./operate-account-record.svelte', import.meta.url)),
	'utf8'
);

describe('operate-account-record money actions', () => {
	it('starts a DCube session from a Morgan Lloyd pension Withdraw, not the cash form', () => {
		expect(source).toContain("kind === 'withdrawal' && data.canDrawdown");
		expect(source).toContain('void drawdown()');
		expect(source).toContain('ondrawdown={data.canDrawdown ? drawdown : undefined}');
		expect(source).not.toContain('sippDrawdown');
		expect(source).not.toContain('Morgan Lloyd and CTC');
	});
});
