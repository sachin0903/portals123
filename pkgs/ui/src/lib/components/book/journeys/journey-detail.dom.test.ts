/**
 * The Record acceptance dialog, which the server render never contains (it is closed, and it
 * carries a file input). Opened here to check what is in it: the printable copy offered before
 * the upload, built from the injected `termsCopy` link, and absent when the mount has none.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import JourneyDetail from './journey-detail.svelte';
import type { JourneyDetailActions, JourneyDetailLinks } from './types.js';
import type { JourneyCase, JourneyExecution } from './steps.js';

afterEach(() => {
	document.body.innerHTML = '';
});

const termsCase: JourneyCase = {
	uid: 'c1',
	kind: 'profile',
	subject_type: 'contact',
	subject_id: 7,
	subject_uid: 'con_7',
	subject_name: 'Joan Davis',
	contact_type: 'individual',
	contact_email: 'joan@example.com',
	roles: ['owner'],
	portal_status: 'invited',
	portal_login_email: null,
	account_ref: null,
	platform_name: null,
	status: 'active',
	steps: [
		{
			step_key: 'terms_general',
			step_type: 'accept_terms',
			title: 'General declaration',
			assignee: 'client',
			status: 'pending',
			participant_subject_id: 7,
			participant_name: 'Joan Davis',
			request_item_id: 11,
			awaiting: null,
			last_event: null,
			document_uid: null,
			document_name: null,
			document_status: null,
			answers: null
		}
	]
};

const execution: JourneyExecution = {
	uid: 'ex1',
	status: 'active',
	application_status: 'with_client',
	flow_name: 'Onboarding',
	custody: 'onshore',
	created_at: '2026-07-01T10:00:00Z',
	adviser_id: 'adv-1',
	adviser_name: 'Alex Morgan',
	assignee_user_id: null,
	assignee_name: null,
	cases: [termsCase],
	timeline: []
};

const links: JourneyDetailLinks = {
	journeys: '/x/journeys',
	fillStep: (c, s) => `/x/fill/${c}/${s}`,
	document: (uid) => `/x/documents/${uid}`
};

const actions: JourneyDetailActions = {
	enhance: () => {},
	action: '?/x-action',
	acknowledge: '?/x-acknowledge',
	retryStep: '?/x-retryStep',
	cancel: '?/x-cancel',
	completeTask: '?/x-completeTask'
};

async function openAttest(withLinks: JourneyDetailLinks) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = mount(JourneyDetail, {
		target,
		props: { execution, links: withLinks, actions }
	});
	flushSync();
	const trigger = [...target.querySelectorAll('button')].find((b) =>
		/Record acceptance/.test(b.textContent ?? '')
	);
	expect(trigger, 'the row offers Record acceptance').toBeDefined();
	trigger!.click();
	flushSync();
	await tick();
	return component;
}

describe('Record acceptance dialog', () => {
	it('offers a printable copy of the declaration, before the signed-copy upload', async () => {
		const component = await openAttest({
			...links,
			termsCopy: (c, s, p) => `/x/journeys/ex1/terms?case=${c}&step=${s}&participant=${p}`
		});
		const dialog = document.querySelector('[role="dialog"]');
		expect(dialog).not.toBeNull();
		const print = [...dialog!.querySelectorAll('a')].find((a) =>
			/Print a copy/.test(a.textContent ?? '')
		);
		expect(print).toBeDefined();
		expect(print!.getAttribute('href')).toBe(
			'/x/journeys/ex1/terms?case=c1&step=terms_general&participant=7'
		);
		expect(print!.getAttribute('target')).toBe('_blank');
		// Reading order: print, then upload.
		const upload = dialog!.querySelector('input[type="file"]');
		expect(upload).not.toBeNull();
		expect(print!.compareDocumentPosition(upload!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
		unmount(component);
	});

	it('offers the application PDF from the header of a completed onboarding', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(JourneyDetail, {
			target,
			props: {
				execution: { ...execution, status: 'completed', flow_code: 'onboarding' },
				links: { ...links, applicationPdf: '/x/journeys/ex1/application.pdf' },
				actions
			}
		});
		flushSync();
		const download = [...target.querySelectorAll('a')].find((a) =>
			/Download application/.test(a.textContent ?? '')
		);
		expect(download).toBeDefined();
		expect(download!.getAttribute('href')).toBe('/x/journeys/ex1/application.pdf');
		expect(download!.getAttribute('target')).toBe('_blank');
		unmount(component);
	});

	it('hides the application PDF until the onboarding is completed', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(JourneyDetail, {
			target,
			props: {
				execution,
				links: { ...links, applicationPdf: '/x/journeys/ex1/application.pdf' },
				actions
			}
		});
		flushSync();
		expect(target.textContent).not.toContain('Download application');
		unmount(component);
	});

	it('has no application download when the mount supplies no route for one', () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(JourneyDetail, {
			target,
			props: { execution, links, actions }
		});
		flushSync();
		expect(target.textContent).not.toContain('Download application');
		unmount(component);
	});

	it('has no print link when the mount supplies no route for one', async () => {
		const component = await openAttest(links);
		const dialog = document.querySelector('[role="dialog"]');
		expect(dialog).not.toBeNull();
		expect(dialog!.textContent).not.toContain('Print a copy');
		unmount(component);
	});
});
