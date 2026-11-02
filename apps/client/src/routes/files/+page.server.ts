import { redirect } from '@sveltejs/kit';
import type { PageServerLoad } from './$types';

// Files now list on the Documents page ("Files on record") — no separate tab. Keep old links
// working via a redirect; /files/[uid]/download (the download route) is unchanged.
export const load: PageServerLoad = () => {
	redirect(307, '/documents');
};
