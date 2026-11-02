/**
 * The platform account record's money-action routing — a source scan, because the drawdown
 * drawer and InstructionSheet render through portals that SSR does not run.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const source = readFileSync(
	fileURLToPath(new URL('./account-record.svelte', import.meta.url)),
	'utf8'
);

describe('account-record money actions', () => {
	it('starts a DCube session from a Morgan Lloyd pension Withdraw, not the cash form', () => {
		expect(source).toContain("kind === 'withdrawal' && data.canDrawdown");
		expect(source).toContain('void openDrawdown()');
		expect(source).toContain("fetch(`/accounts/${a.uid}/drawdown`, { method: 'POST' })");
		expect(source).toContain('ondrawdown={data.canDrawdown ? openDrawdown : undefined}');
		expect(source).toContain('Open the drawdown journey');
		expect(source).toContain('<Sheet.Content class="flex w-full flex-col gap-0 p-0 sm:max-w-xl">');
		expect(source).not.toContain('sippDrawdown');
		expect(source).not.toContain('Morgan Lloyd and CTC');
	});
});
