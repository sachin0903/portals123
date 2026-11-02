/** Stand-in so this package typechecks `page.status` without depending on SvelteKit.
 *  The portals resolve the real `$app/state` when they compile the component. */

export const page = {
	status: 200,
	url: { pathname: '/' }
};
