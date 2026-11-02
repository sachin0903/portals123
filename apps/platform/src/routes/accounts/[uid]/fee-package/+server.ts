import { json } from '@sveltejs/kit';
import type { RequestHandler } from './$types';

// Fees are view only for now (feedback, Oct 2026): the write is refused here and the API is never
// called. Restore the PUT to `/crm/accounts/{uid}/fee-packages` to re-enable editing.
export const PUT: RequestHandler = async () =>
	json({ error: 'Fees are view only.' }, { status: 403 });
