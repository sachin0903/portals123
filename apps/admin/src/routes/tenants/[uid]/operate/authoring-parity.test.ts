import { existsSync, readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { TENANT_CAPABILITY_MANIFEST } from '$lib/tenant-capabilities';
import { actions as formActions } from './forms/+page.server';
import { actions as templateActions } from './templates/+page.server';
import { actions as contentActions } from './content/+page.server';
import { actions as datasetActions } from './datasets/new/+page.server';

const root = 'src/routes/tenants/[uid]/operate';

describe('tenant authoring parity', () => {
	it('ships every forms, flows and automations route', () => {
		for (const route of [
			'forms',
			'forms/[formUid]',
			'forms/[formUid]/edit',
			'flows',
			'flows/[flowUid]',
			'flows/[flowUid]/insights',
			'flows/[flowUid]/dry-run',
			'flows/dead-letters',
			'automations',
			'automations/[automationUid]'
		]) {
			expect(existsSync(`${root}/${route}/+page.svelte`), route).toBe(true);
			expect(existsSync(`${root}/${route}/+page.server.ts`), route).toBe(true);
		}
	});

	it('publishes capabilities only after route parity exists', () => {
		expect(TENANT_CAPABILITY_MANIFEST.forms.available).toBe(true);
		expect(TENANT_CAPABILITY_MANIFEST.flows.available).toBe(true);
		expect(TENANT_CAPABILITY_MANIFEST.automations.available).toBe(true);
		expect(TENANT_CAPABILITY_MANIFEST.templates.available).toBe(true);
		expect(TENANT_CAPABILITY_MANIFEST.content.available).toBe(true);
		expect(TENANT_CAPABILITY_MANIFEST.emails.available).toBe(true);
		expect(TENANT_CAPABILITY_MANIFEST.activity.available).toBe(true);
		expect(TENANT_CAPABILITY_MANIFEST['data-sources'].available).toBe(true);
	});

	it('ships the remaining authoring and outbound-email route families', () => {
		for (const route of [
			'templates',
			'templates/[templateUid]',
			'templates/[templateUid]/edit',
			'content',
			'content/[contentUid]/edit',
			'datasets',
			'datasets/new',
			'datasets/[datasetUid]',
			'activity',
			'emails',
			'emails/[messageUid]',
			'data-sources'
		]) {
			expect(existsSync(`${root}/${route}/+page.svelte`), route).toBe(true);
			expect(existsSync(`${root}/${route}/+page.server.ts`), route).toBe(true);
		}
	});

	it('keeps every server route target-aware and presentation shared', () => {
		for (const capability of [
			'templates',
			'content',
			'datasets',
			'emails',
			'activity',
			'data-sources'
		]) {
			const files = readFileSync(`${root}/${capability}/+page.server.ts`, 'utf8');
			expect(files).toContain('$lib/operate');
			expect(files).not.toMatch(/\bapiUrl\b|\bbookUrl\b/);
		}
		expect(readFileSync(`${root}/templates/[templateUid]/edit/+page.svelte`, 'utf8')).toContain(
			"from '@graphene/ui/template-authoring'"
		);
		expect(readFileSync(`${root}/datasets/new/+page.svelte`, 'utf8')).toContain(
			"from '@graphene/ui/dataset-authoring'"
		);
		expect(readFileSync(`${root}/content/[contentUid]/edit/+page.svelte`, 'utf8')).toContain(
			"from '@graphene/ui/content-editor'"
		);
		expect(readFileSync(`${root}/emails/[messageUid]/+page.svelte`, 'utf8')).toContain(
			"from '@graphene/ui/comms'"
		);
	});

	it('keeps authoring mutations behind the tenant-portal write gate', () => {
		for (const route of [
			'templates/+page.server.ts',
			'templates/[templateUid]/+page.server.ts',
			'templates/[templateUid]/edit/+page.server.ts',
			'content/+page.server.ts',
			'content/[contentUid]/edit/+page.server.ts',
			'datasets/new/+page.server.ts',
			'datasets/[datasetUid]/+page.server.ts'
		]) {
			const source = readFileSync(`${root}/${route}`, 'utf8');
			expect(source, route).toContain('canOperateWrite');
			expect(source, route).toContain('readOnlyFailure');
			expect(source, route).toContain('targetMutation');
		}
		for (const route of [
			'content/[contentUid]/edit/images/+server.ts',
			'content/[contentUid]/edit/hero/+server.ts',
			'content/[contentUid]/edit/guide/+server.ts',
			'templates/[templateUid]/edit/preview/+server.ts',
			'templates/[templateUid]/edit/preview.pdf/+server.ts'
		]) {
			const source = readFileSync(`${root}/${route}`, 'utf8');
			expect(source, route).toContain('canOperateWrite');
			expect(source, route).toContain('$lib/operate');
		}
	});

	it('uses shared editors and target-aware API helpers', () => {
		const flow = readFileSync(`${root}/flows/[flowUid]/+page.svelte`, 'utf8');
		const form = readFileSync(`${root}/forms/[formUid]/edit/+page.svelte`, 'utf8');
		expect(flow).toContain("from '@graphene/ui/flow-editor'");
		expect(form).toContain("from '@graphene/ui/form-builder'");
		for (const route of [
			'forms/+page.server.ts',
			'forms/[formUid]/+page.server.ts',
			'forms/[formUid]/edit/+page.server.ts',
			'flows/+page.server.ts',
			'flows/[flowUid]/+page.server.ts',
			'flows/[flowUid]/insights/+page.server.ts',
			'flows/[flowUid]/dry-run/+page.server.ts',
			'flows/dead-letters/+page.server.ts',
			'automations/+page.server.ts',
			'automations/[automationUid]/+page.server.ts'
		]) {
			const source = readFileSync(`${root}/${route}`, 'utf8');
			expect(source, route).toContain('$lib/operate');
			expect(source, route).not.toMatch(/\bapiUrl\b|\bbookUrl\b/);
		}
	});

	it('refuses create mutations in read-only mode', async () => {
		const fetch = async () => {
			throw new Error('read-only actions must not fetch');
		};
		// SvelteKit action types are intentionally opaque to direct unit invocation.
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const result = await (formActions.create as any)({
			locals: { permissions: ['tenant_portal:read'], role: null },
			params: { uid: 'ten_target' },
			fetch,
			request: new Request('http://admin.test', { method: 'POST', body: new FormData() })
		});
		expect(result.status).toBe(403);
	});

	it.each([
		['template', templateActions.create],
		['content', contentActions.create],
		['dataset', datasetActions.default]
	])('refuses %s creation before reading the request', async (_name, action) => {
		const fetch = async () => {
			throw new Error('read-only actions must not fetch');
		};
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		const result = await (action as any)({
			locals: { permissions: ['tenant_portal:read'], role: null },
			params: { uid: 'ten_target' },
			fetch,
			request: new Request('http://admin.test', { method: 'POST', body: new FormData() })
		});
		expect(result.status).toBe(403);
	});
});
