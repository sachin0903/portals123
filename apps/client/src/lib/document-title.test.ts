import { describe, expect, it } from 'vitest';
import { documentTitle } from './document-title';

const brand = 'Chester Rose';

describe('documentTitle', () => {
	it('gives each profile section its own title, including the firm', () => {
		expect(documentTitle('/profile', brand)).toBe('Profile · Chester Rose');
		expect(documentTitle('/profile/notifications', brand)).toBe('Notifications · Chester Rose');
		expect(documentTitle('/profile/security', brand)).toBe('Security · Chester Rose');
	});

	it('does not let a profile page label rename the sections', () => {
		const labels = new Map([['profile', 'Account']]);
		expect(documentTitle('/profile/security', brand, labels)).toBe('Security · Chester Rose');
	});

	it('still lets a firm rename a portal page', () => {
		const labels = new Map([['documents', 'Papers']]);
		expect(documentTitle('/documents', brand, labels)).toBe('Papers · Chester Rose');
	});

	it('titles the home tab from the empty segment', () => {
		expect(documentTitle('/', brand)).toBe('Home · Chester Rose');
	});
});
