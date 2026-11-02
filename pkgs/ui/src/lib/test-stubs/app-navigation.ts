/** Stand-in so this package typechecks its `$app/navigation` imports without depending on SvelteKit.
 *  The portals resolve the real `$app/navigation` when they compile the component. */

type AfterNavigate = (navigation: {
	type: string;
	to: { url: { pathname: string } } | null;
}) => void;

export const afterNavigate: (fn: AfterNavigate) => void = () => {};

export const invalidateAll = async (): Promise<void> => {};
