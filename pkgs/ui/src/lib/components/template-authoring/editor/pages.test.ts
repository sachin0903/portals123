import { describe, it, expect } from 'vitest';
import { authoredClasses } from './pages';

describe('authoredClasses', () => {
	it("drops the editor's own page classes", () => {
		// The defect: selecting a page made the rail's target strip read `<div> .page .pg-irx3`.
		// Neither is a style target — `page` is the shared shell and `pg-…` is regenerated on clone —
		// so the strip reported three targets on a page where the author had none.
		expect(authoredClasses(['page', 'pg-irx3'])).toEqual([]);
		expect(authoredClasses(['page', 'pg-irx3', 'cover'])).toEqual(['cover']);
	});

	it('keeps a class that merely starts with the same letters', () => {
		// Matched exactly, never by prefix: these are ordinary things for an author to write, and a
		// `^page` test would swallow them silently.
		expect(authoredClasses(['page-header', 'pages', 'pgx'])).toEqual([
			'page-header',
			'pages',
			'pgx'
		]);
	});

	it('leaves an authored list untouched and preserves order', () => {
		expect(authoredClasses(['intro', 'lead', 'note'])).toEqual(['intro', 'lead', 'note']);
		expect(authoredClasses([])).toEqual([]);
	});
});
