import { describe, expect, it } from 'vitest';
import { emailTemplateState } from './template-state';

describe('emailTemplateState', () => {
	it('calls a published head version Published, and says it is what recipients get', () => {
		const s = emailTemplateState({ current_version_status: 'published', version_count: 3 });
		expect(s).toMatchObject({ label: 'Published', tone: 'success' });
		expect(s.hint).toMatch(/recipients get/);
	});

	it('calls a single draft version Draft, and names the fallback that is actually sent', () => {
		const s = emailTemplateState({ current_version_status: 'draft', version_count: 1 });
		expect(s).toMatchObject({ label: 'Draft', tone: 'neutral' });
		expect(s.hint).toMatch(/Graphene’s default is sent/);
	});

	it('calls a draft on top of earlier versions "Unpublished changes", claiming nothing more', () => {
		// "Draft" would read as "nothing of ours is live" for a template published for months; whether an earlier version is published is unknowable from this payload, so the hint asserts neither the default nor a last-published version.
		const s = emailTemplateState({ current_version_status: 'draft', version_count: 4 });
		expect(s).toMatchObject({ label: 'Unpublished changes', tone: 'warning' });
		expect(s.hint).not.toMatch(/default/i);
		expect(s.hint).not.toMatch(/last published/i);
	});

	it('treats no versions as Not published and names the fallback', () => {
		const s = emailTemplateState({ current_version_status: null, version_count: 0 });
		expect(s.label).toBe('Not published');
		expect(s.hint).toMatch(/Graphene’s default is sent/);
	});

	it('treats a retired head version as Not published, without naming a fallback it cannot prove', () => {
		const s = emailTemplateState({ current_version_status: 'archived', version_count: 5 });
		expect(s.label).toBe('Not published');
		expect(s.hint).not.toMatch(/default/i);
	});

	it('never uses the word Live — the labels are the version vocabulary', () => {
		// The badge and the version-history dialog read as one thing because both use the statuses the API publishes; "Live" would be a second word for Published.
		const inputs: Parameters<typeof emailTemplateState>[0][] = [
			{ current_version_status: 'published', version_count: 1 },
			{ current_version_status: 'draft', version_count: 1 },
			{ current_version_status: 'draft', version_count: 2 },
			{ current_version_status: null, version_count: 0 }
		];
		for (const i of inputs) expect(emailTemplateState(i).label).not.toMatch(/live/i);
	});
});
