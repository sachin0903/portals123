import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { instanceStage, showsOutboundEmailGuard } from './instance-stage';

describe('instance stage', () => {
	it('treats a blank stage as production', () => {
		expect(instanceStage(undefined)).toBe('production');
		expect(instanceStage('')).toBe('production');
		expect(instanceStage('  ')).toBe('production');
	});

	it('normalises case and surrounding space', () => {
		expect(instanceStage(' Staging ')).toBe('staging');
	});
});

describe('the outbound-email notice', () => {
	it('is shown only on dev and staging', () => {
		expect(showsOutboundEmailGuard('dev')).toBe(true);
		expect(showsOutboundEmailGuard('DEV')).toBe(true);
		expect(showsOutboundEmailGuard(' staging ')).toBe(true);
		expect(showsOutboundEmailGuard('production')).toBe(false);
		expect(showsOutboundEmailGuard('test')).toBe(false);
		expect(showsOutboundEmailGuard('')).toBe(false);
		expect(showsOutboundEmailGuard(undefined)).toBe(false);
	});

	it('is what the tenant page uses, so production cannot render the setting by accident', () => {
		const page = readFileSync('src/routes/tenants/[uid]/+page.svelte', 'utf8');
		expect(page).toContain('showsOutboundEmailGuard(data.appStage)');
		expect(page).toContain('Outbound email');
		expect(page).toContain('?/setEmailAllowlist');
	});
});
