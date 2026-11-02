/**
 * Share sits on the illustration row. A ready PDF the client cannot yet see offers Share when
 * the caller holds documents:send; one already in the portal says Shared; a reader without the
 * permission, and a PDF that is not ready, get neither.
 */
import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import Illustrations, { type IllustrationDoc } from './illustrations.svelte';

const row = (over: Partial<IllustrationDoc> = {}): IllustrationDoc => ({
	uid: 'ill_1',
	document_uid: 'doc_1',
	name: 'SIPP illustration — Accumulation',
	created_at: '2026-10-16T09:00:00Z',
	product_type: 'SIPP',
	status: 'ready',
	shared_with_client: false,
	inputs: { existing_fund_value: 0, output_reference: 'CON1', quotation_type: 'Accumulation' },
	...over
});

const html = (props: {
	illustrations?: IllustrationDoc[];
	canShare?: boolean;
	canIllustrate?: boolean;
}) =>
	render(Illustrations, {
		props: {
			contactUid: 'con_1',
			clientName: 'Ada Client',
			illustrations: props.illustrations ?? [row()],
			canShare: props.canShare ?? false,
			canIllustrate: props.canIllustrate ?? false
		}
	}).body;

describe('illustration share', () => {
	it('offers Share on a ready illustration the client cannot see', () => {
		const body = html({ canShare: true });
		expect(body).toContain('>Share<');
		expect(body).not.toContain('>Shared<');
	});

	it('says Shared once the client can read it, and does not offer Share again', () => {
		const body = html({
			canShare: true,
			illustrations: [row({ shared_with_client: true })]
		});
		expect(body).toContain('>Shared<');
		expect(body).not.toContain('>Share<');
	});

	it('hides Share from a caller who cannot release a document', () => {
		const body = html({ canShare: false });
		expect(body).not.toContain('>Share<');
		expect(body).not.toContain('Share this illustration with Ada Client?');
	});

	it('hides Share while the PDF is still generating', () => {
		const body = html({
			canShare: true,
			illustrations: [row({ status: 'generating' })]
		});
		expect(body).not.toContain('>Share<');
	});
});
