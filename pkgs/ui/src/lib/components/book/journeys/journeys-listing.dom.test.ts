/**
 * The row kebab is closed on the server, so the download link only exists once it is opened.
 */
import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import JourneysListing from './journeys-listing.svelte';
import type { AppExecutionRow } from './applications.js';
import type { JourneysListingLinks } from './types.js';

afterEach(() => {
	document.body.innerHTML = '';
});

const links: JourneysListingLinks = {
	journey: (uid) => `/x/journeys/${uid}`,
	applicationPdf: (uid) => `/x/journeys/${uid}/application.pdf`
};

const row = (over: Partial<AppExecutionRow> = {}): AppExecutionRow => ({
	uid: 'j1',
	status: 'active',
	created_at: '2026-07-01T10:00:00Z',
	application_status: 'with_client',
	flow_name: 'Onboarding',
	flow_code: 'onboarding',
	adviser_name: 'Alex Morgan',
	clients: 1,
	accounts: 1,
	steps_total: 10,
	steps_done: 0,
	client_names: 'Noah Bennett',
	...over
});

describe('Journeys listing row menu', () => {
	it('downloads the application from the kebab of a completed onboarding', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(JourneysListing, {
			target,
			props: {
				applications: [
					row({
						uid: 'done',
						status: 'completed',
						application_status: 'completed',
						client_names: 'Isla Morgan',
						steps_done: 10
					})
				],
				links,
				filters: { status: ['completed'] }
			}
		});
		flushSync();
		const trigger = [...target.querySelectorAll('button')].find(
			(button) => button.getAttribute('aria-label') === 'Actions for Isla Morgan'
		);
		expect(trigger).toBeDefined();
		trigger!.click();
		flushSync();
		await tick();
		const download = [...document.querySelectorAll('a')].find((anchor) =>
			/Download application/.test(anchor.textContent ?? '')
		);
		expect(download).toBeDefined();
		expect(download!.getAttribute('href')).toBe('/x/journeys/done/application.pdf');
		expect(download!.getAttribute('target')).toBe('_blank');
		unmount(component);
	});
});

describe('Journeys listing stat tiles', () => {
	it('press the filter bar: a tile cuts the list, pressing it again lifts the cut', async () => {
		const target = document.createElement('div');
		document.body.appendChild(target);
		const component = mount(JourneysListing, {
			target,
			props: {
				applications: [
					row({ uid: 'mine', client_names: 'Noah Bennett', owner_is_me: true }),
					row({ uid: 'theirs', client_names: 'Isla Morgan', assignee_name: 'Priya Nair' }),
					row({ uid: 'done', status: 'completed', client_names: 'Ada Byron' })
				],
				links
			}
		});
		flushSync();
		const tile = (label: string) =>
			[...target.querySelectorAll('button[aria-pressed]')].find((b) =>
				(b.textContent ?? '').includes(label)
			) as HTMLButtonElement;
		const names = () =>
			[...target.querySelectorAll('tbody a')].map((a) => a.textContent?.trim()).filter(Boolean);

		// Arrival: the journeys underway, no tile pressed and no filter applied, the finished
		// one out of sight.
		expect(tile('In flight').getAttribute('aria-pressed')).toBe('false');
		expect(target.querySelector('[aria-label="Show filters"]')).not.toBeNull();
		expect(names()).toEqual(['Noah Bennett', 'Isla Morgan']);

		tile('Need you').click();
		flushSync();
		await tick();
		expect(tile('Need you').getAttribute('aria-pressed')).toBe('true');
		expect(tile('In flight').getAttribute('aria-pressed')).toBe('false');
		expect(names()).toEqual(['Noah Bennett']);

		tile('Completed').click();
		flushSync();
		await tick();
		expect(names()).toEqual(['Ada Byron']);

		// The tile in force, pressed again: back to the quiet default, finished journeys out.
		tile('Completed').click();
		flushSync();
		await tick();
		expect(tile('Completed').getAttribute('aria-pressed')).toBe('false');
		expect(names()).toEqual(['Noah Bennett', 'Isla Morgan']);
		unmount(component);
	});
});
