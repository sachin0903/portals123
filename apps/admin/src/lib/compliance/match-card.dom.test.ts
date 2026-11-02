import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import type { AmlMatch } from '$lib/aml';
import MatchCard from './match-card.svelte';

// The admin app has no jsdom, so the card is rendered on the server: these check the markup it
// sends — what shows, and the rules on every third-party image and link — not the clicks.

function match(overrides: Partial<AmlMatch> = {}): AmlMatch {
	return {
		uid: 'm1',
		name: 'Nicola Sturgeon',
		match_types: ['pep'],
		categories: ['PEP level 1'],
		score: 93,
		dob: '1970-07-19',
		gender: 'female',
		countries: ['GB'],
		addresses: [],
		sources: [],
		references: [],
		images: [],
		names: [],
		aliases: [],
		roles: [],
		birth_places: [],
		country_details: [],
		dates: [],
		entity_type: null,
		notes: null,
		associations: [],
		source_links: [],
		types: [],
		active: null,
		deceased: null,
		last_reviewed: null,
		decision: null,
		decided_by: null,
		decided_at: null,
		comment: null,
		...overrides
	};
}

function card(overrides: Partial<AmlMatch> = {}): string {
	return render(MatchCard, {
		props: { match: match(overrides), checkUid: 'c1', canDecide: true }
	}).body;
}

/** What the card reads as, without its markup. */
function text(html: string): string {
	return html
		.replace(/<!--[\s\S]*?-->/g, '')
		.replace(/<[^>]+>/g, ' ')
		.replace(/\s+/g, ' ')
		.trim();
}

const tags = (html: string, name: string) => html.match(new RegExp(`<${name}\\b[^>]*>`, 'g')) ?? [];

describe('MatchCard', () => {
	it('shows one photo large, with arrows and dots for the rest, opening in the page', () => {
		const html = card({
			images: [
				'https://bbc.co.uk/a.jpg',
				'javascript:alert(1)',
				'https://forbes.com/b.jpg',
				'https://bbc.co.uk/a.jpg'
			]
		});
		// One photo at a time: no thumbnail strip.
		const images = tags(html, 'img');
		expect(images).toHaveLength(1);
		expect(images[0]).toContain('src="https://bbc.co.uk/a.jpg"');
		expect(images[0]).toContain('aspect-[4/3]');
		expect(images[0]).toContain('referrerpolicy="no-referrer"');
		expect(html).not.toContain('javascript:');
		// A photo never navigates: it is a button that opens the pop-up viewer.
		expect(tags(html, 'a')).toHaveLength(0);
		const buttons = tags(html, 'button');
		expect(
			buttons.some((b) => b.includes('aria-label="View the photo of Nicola Sturgeon (1 of 2)"'))
		).toBe(true);
		expect(buttons.some((b) => b.includes('aria-label="Previous photo"'))).toBe(true);
		expect(buttons.some((b) => b.includes('aria-label="Next photo"'))).toBe(true);
		const dots = html.match(/<span class="size-1\.5 rounded-full[^"]*"/g) ?? [];
		expect(dots).toHaveLength(2);
	});

	it('shows a single photo without arrows', () => {
		const html = card({ images: ['https://bbc.co.uk/a.jpg'] });
		expect(html).toContain('aria-label="View the photo of Nicola Sturgeon"');
		expect(html).not.toContain('Previous photo');
		expect(html).not.toContain('Next photo');
	});

	it('leaves the photo out when there is none', () => {
		expect(tags(card(), 'img')).toHaveLength(0);
	});

	it('says whether the person is still listed and deceased, and what kind of person', () => {
		const shown = text(
			card({
				active: false,
				deceased: true,
				types: ['Politically Exposed Person (PEP)'],
				entity_type: 'person'
			})
		);
		expect(shown).toContain('No longer listed');
		expect(shown).toContain('Deceased: Yes');
		expect(shown).toContain('Politically Exposed Person (PEP)');
		expect(shown).toContain('Person · Female · United Kingdom');
		const living = text(card({ active: true, deceased: false }));
		expect(living).toContain('Currently listed');
		expect(living).toContain('Deceased: No');
	});

	it('puts the date and place of birth on one line, with the other names under it', () => {
		const shown = text(
			card({
				birth_places: ['Irvine, United Kingdom'],
				aliases: ['Nicola Ferguson Sturgeon', 'Никола Стерджен']
			})
		);
		expect(shown).toContain('Born 19 Jul 1970 · Irvine, United Kingdom');
		expect(shown).toContain('Also known as Nicola Ferguson Sturgeon, Никола Стерджен');
		expect(shown).not.toContain('Date of birth');
	});

	it('shows the first three roles, with when each was held and a way to see the rest', () => {
		const role = (title: string, from: string | null, current = false) => ({
			title,
			category: 'PEP level 1',
			type: current ? 'Primary Occupation' : 'Previous Roles',
			from,
			to: current ? null : '2023-03',
			current
		});
		const shown = text(
			card({
				roles: [
					role('First Minister', '2014-11', true),
					role('Deputy First Minister', '2007'),
					role('MSP', '1999'),
					role('Party leader', null),
					role('Councillor', '1990')
				]
			})
		);
		expect(shown).toContain('Roles (5)');
		expect(shown).toContain('First Minister Nov 2014 – now PEP level 1 · Primary Occupation');
		expect(shown).toContain('Deputy First Minister 2007 – Mar 2023');
		expect(shown).toContain('MSP');
		expect(shown).not.toContain('Party leader');
		expect(shown).toContain('Show all 5');
	});

	it('clamps a long note with a way to read it all, and leaves a short one whole', () => {
		const long = card({ notes: 'x'.repeat(400) });
		expect(long).toContain('line-clamp-4');
		expect(text(long)).toContain('Show more');
		const short = card({ notes: 'A short note.' });
		expect(short).not.toContain('line-clamp-4');
		expect(text(short)).not.toContain('Show more');
	});

	it('folds the full detail into sections, each with its count', () => {
		const html = card({
			names: [
				{ name: 'Nicola Sturgeon', type: 'Primary' },
				{ name: 'Никола Стерджен', type: 'Localised name' }
			],
			country_details: [
				{ country: 'GB', type: 'Born in' },
				{ country: 'GB', type: 'Citizenship' }
			],
			dates: [{ type: 'Inactive as of (PEP)', date: '2026-05-14' }],
			associations: [
				{
					name: 'Alexander Salmond',
					relationship: 'Friend',
					current: false,
					categories: ['PEP level 1'],
					entity_type: 'person',
					image: 'https://bbc.co.uk/salmond.jpg'
				}
			],
			source_links: ['https://www.bbc.co.uk/news/1', 'javascript:alert(1)'],
			last_reviewed: '14-May-2026'
		});
		const shown = text(html);
		expect(shown).toContain('Names (2)');
		expect(shown).toContain('Localised name Никола Стерджен');
		expect(shown).toContain('Countries (1)');
		expect(shown).toContain('United Kingdom · Born in, Citizenship');
		expect(shown).toContain('Dates (1)');
		expect(shown).toContain('Inactive as of (PEP) 14 May 2026');
		expect(shown).toContain('Associates (1)');
		expect(shown).toContain('Alexander Salmond · Friend · Former PEP level 1');
		expect(shown).toContain('Source links (1)');
		expect(shown).toContain('bbc.co.uk');
		expect(shown).toContain('Last reviewed by ComplyCube: 14-May-2026');
		expect(html).not.toContain('javascript:');
		const portrait = tags(html, 'img').find((tag) => tag.includes('salmond.jpg'));
		expect(portrait).toContain('referrerpolicy="no-referrer"');
		expect(html).toContain('aria-label="View the photo of Alexander Salmond"');
		expect(tags(html, 'a').some((tag) => tag.includes('salmond.jpg'))).toBe(false);
		const source = tags(html, 'a').find((tag) => tag.includes('bbc.co.uk/news/1'));
		expect(source).toContain('rel="noopener noreferrer"');
		expect(source).toContain('referrerpolicy="no-referrer"');
	});

	it('keeps the decision form, with the comment optional', () => {
		const html = card();
		const shown = text(html);
		expect(shown).toContain('Comment (optional)');
		expect(shown).toContain('True match');
		expect(shown).toContain('False positive');
		expect(tags(html, 'textarea')[0]).not.toContain('required');
		const actions = tags(html, 'button').filter((tag) => tag.includes('name="decision"'));
		expect(actions).toHaveLength(2);
		// The attribute, not the `disabled:` utility classes every Button carries.
		for (const action of actions) expect(action).not.toMatch(/\sdisabled(?=[\s=>])/);
	});

	it('lays itself out by its own width: two columns when wide, the folded detail in a grid', () => {
		const html = card({
			roles: [
				{
					title: 'First Minister',
					category: null,
					type: null,
					from: null,
					to: null,
					current: true
				}
			],
			names: [{ name: 'Nicola Sturgeon', type: 'Primary' }]
		});
		expect(html).toContain('@container');
		expect(html).toContain('@2xl:grid-cols-2');
		expect(html).toContain('@4xl:grid-cols-3');
	});

	it('greys the text toggles, so they do not read as links', () => {
		const role = { title: 'MSP', category: null, type: null, from: null, to: null, current: false };
		const html = card({ roles: [role, role, role, role] });
		const toggle = tags(html, 'button').find((tag) => tag.includes('mt-1'));
		expect(toggle).toContain('text-muted-foreground');
		expect(toggle).not.toContain('text-primary');
	});
});

describe('MatchCard on the workbench (compact)', () => {
	const full = {
		images: ['https://bbc.co.uk/a.jpg'],
		aliases: ['Nicola Ferguson Sturgeon'],
		notes: 'Former First Minister of Scotland.',
		types: ['PEP'],
		active: true,
		last_reviewed: '2026-01-01',
		names: [{ name: 'Никола Стерджен', type: 'Localised name' }],
		roles: [{ title: 'MSP', category: null, type: null, from: null, to: null, current: true }]
	};

	function compact(overrides: Partial<AmlMatch> = {}): string {
		return render(MatchCard, {
			props: { match: match(overrides), checkUid: 'c1', canDecide: true, compact: true }
		}).body;
	}

	it('shows the top and the decision, and folds the rest behind one closed Details', () => {
		const html = compact(full);
		const triggers = tags(html, 'button').filter((tag) => tag.includes('data-collapsible-trigger'));
		const at = html.indexOf('>Details');
		expect(at).toBeGreaterThan(0);
		// The top: the photo, who they are, the score and the chips.
		const top = text(html.slice(0, at));
		expect(top).toContain('Nicola Sturgeon');
		expect(top).toContain('Female · United Kingdom');
		expect(top).toContain('93%');
		expect(top).toContain('Currently listed');
		expect(top).toContain('PEP');
		for (const folded of ['Born', 'Also known as', 'Categories', 'Sources', 'Roles', 'Notes'])
			expect(top).not.toContain(folded);
		// One Details per match, closed, its content hidden until opened.
		expect(triggers[0]).toContain('aria-expanded="false"');
		const content = tags(html, 'div').filter((tag) => tag.includes('data-collapsible-content'));
		expect(content[0]).toContain('data-[state=closed]:hidden');
		expect(content[0]).toContain('data-state="closed"');
		const folded = text(html.slice(at));
		expect(folded).toContain('Born 19 Jul 1970');
		expect(folded).toContain('Last reviewed by ComplyCube');
		// The decision stays open, under the profile.
		expect(folded).toContain('True match');
		expect(folded).toContain('False positive');
	});

	it('shows a smaller photo', () => {
		const img = tags(compact(full), 'div').find((tag) => tag.includes('max-w-56'));
		expect(img).toContain('max-w-28!');
		expect(tags(card(full), 'div').find((tag) => tag.includes('max-w-56'))).not.toContain(
			'max-w-28!'
		);
	});

	it('leaves the full card as it was, with no Details', () => {
		const html = card(full);
		expect(html).not.toContain('>Details');
		const decision = html.indexOf('True match');
		expect(text(html.slice(0, decision))).toContain('Born 19 Jul 1970');
	});
});
