/** Is a nav item's href the current page? `href` may carry its own query/hash (e.g. the client
 *  profile tab's `?view=client`), but `pathname` never does — comparing them verbatim can never
 *  match, so strip href's query/hash first. The root path is exact-only so every other section
 *  doesn't read as "under /". */
export function isNavActive(href: string, pathname: string): boolean {
	const path = href.split(/[?#]/)[0];
	return path === '/' ? pathname === '/' : pathname === path || pathname.startsWith(path + '/');
}
