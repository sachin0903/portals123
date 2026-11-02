/**
 * The browser-tab title. One element in the root layout owns it, because SvelteKit leaves
 * `document.title` untouched when a page renders no `<title>` — a section that set one and a
 * section that set none left the first title stuck ("Notifications · Account" on Security).
 *
 * Profile is three sections under one path prefix, so the first segment ("profile") is not
 * enough: each section names itself.
 */

const TITLE_LABELS: Record<string, string> = {
	'': 'Home',
	login: 'Sign in',
	mfa: 'Two-factor authentication',
	'set-password': 'Set your password',
	'forgot-password': 'Reset your password',
	verify: 'Verify your email',
	adviser: 'Your adviser',
	insights: 'Insights'
};

function profileSection(pathname: string): string {
	const rest = pathname.slice('/profile'.length);
	if (rest === '/notifications' || rest.startsWith('/notifications/')) return 'Notifications';
	if (rest === '/security' || rest.startsWith('/security/')) return 'Security';
	return 'Profile';
}

export function documentTitle(
	pathname: string,
	brand: string,
	pageLabels: ReadonlyMap<string, string> = new Map()
): string {
	const seg = pathname.split('/')[1] ?? '';
	if (seg === 'profile') return `${profileSection(pathname)} · ${brand}`;
	const label =
		pageLabels.get(seg === '' ? 'home' : seg) ??
		TITLE_LABELS[seg] ??
		(seg ? seg[0].toUpperCase() + seg.slice(1) : 'Home');
	return `${label} · ${brand}`;
}
