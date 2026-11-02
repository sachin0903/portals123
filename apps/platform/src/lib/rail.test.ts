import { describe, expect, it } from 'vitest';
import { railNotes, type RailDetail } from './rail';

const detail = (notes: NonNullable<RailDetail['notes']>): RailDetail => ({
	contact: null,
	notes
});

describe('railNotes', () => {
	it('carries the note UID, which is what the delete endpoint matches on', () => {
		// A serial here was a note that could be listed but never removed: the rail sent `id`
		// back to `DELETE /firm/contacts/{uid}/notes/{note_uid}`, which matches on `uid`, so every
		// delete answered 404.
		const [note] = railNotes(
			detail([{ id: 7, uid: 'n_abc', body: 'x', author: null, created_at: '2026-01-01T00:00:00Z' }])
		);
		expect(note.uid).toBe('n_abc');
	});

	it('falls back to the serial only when the read carries no uid', () => {
		const [note] = railNotes(
			detail([{ id: 7, body: 'x', author: null, created_at: '2026-01-01T00:00:00Z' }])
		);
		expect(note.uid).toBe('7');
	});

	it('is newest first and capped', () => {
		const rows = [1, 2, 3, 4, 5, 6].map((n) => ({
			id: n,
			uid: `n${n}`,
			body: `note ${n}`,
			author: null,
			created_at: `2026-01-0${n}T00:00:00Z`
		}));
		const notes = railNotes(detail(rows));
		expect(notes).toHaveLength(5);
		expect(notes[0].uid).toBe('n6');
	});
});
