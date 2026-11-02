import { describe, expect, it } from 'vitest';
import { kickoffHref, startJourneyLinks } from './start-journey';

describe('kickoffHref', () => {
	it('names the flow, and the client when the start is from their record', () => {
		expect(kickoffHref('fact_find')).toBe('/journeys/new?flow=fact_find');
		expect(kickoffHref('fact_find', 'y8Det_wwskoC')).toBe(
			'/journeys/new?flow=fact_find&client=y8Det_wwskoC'
		);
	});
});

describe('startJourneyLinks', () => {
	it('points every published flow at this client', () => {
		expect(
			startJourneyLinks(
				[
					{ code: 'onboarding', name: 'Onboarding' },
					{ code: 'fact_find', name: 'Fact find' }
				],
				'con_1'
			)
		).toEqual([
			{
				code: 'onboarding',
				name: 'Onboarding',
				href: '/journeys/new?flow=onboarding&client=con_1'
			},
			{ code: 'fact_find', name: 'Fact find', href: '/journeys/new?flow=fact_find&client=con_1' }
		]);
	});
});
