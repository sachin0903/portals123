import type { Adviser as SharedAdviser } from '@graphene/ui';
import type { Branding } from '@graphene/auth';

/** The client's actual assigned adviser, resolved from `crm.client_advisers` for the caller's own
 *  contact scope — the `adviser` block of GET /crm/home (see apps/api/app/routers/crm.py). `null`
 *  when the firm hasn't assigned one, which the pages state plainly: showing a stand-in adviser
 *  told every client the same fiction. /messages loads it for the conversation header; the adviser
 *  blocks on a composed page load the same field. There is no separate adviser page.
 *
 *  AN ALIAS of the shared type, not a redeclaration. This was a four-field copy, and when the staff
 *  profile gave an adviser the firm's own words — job title, qualifications, bio, photo — a local
 *  copy silently dropped every one of them from both pages while the home-page card showed them:
 *  the same field set described two ways, and the narrower one wins wherever it is imported.
 *  `@graphene/ui` owns the shape. */
export type Adviser = SharedAdviser;

/** The FIRM as the client's contact when nobody is assigned (`kind: 'firm'`, `@graphene/ui`
 *  `Adviser`). Not a fiction: it is the firm's own name, its icon, and the support email/phone
 *  the firm set under Branding → support — the same details every email footer carries. The
 *  adviser blocks and the conversation header draw it where the empty "Your adviser will appear
 *  here" note used to sit, so a direct/execution-only client has someone to reach, and the thread
 *  still works: whoever on the staff replies is named on their own messages.
 *
 *  `email`/`phone` are `''` when the firm has not set them — the looks draw no Email/Call action
 *  for an empty string rather than a `mailto:` to nowhere. `appName` is the tenant's name as the
 *  hook resolved it (`locals.appName`); the Graphene default stands in when unbranded. */
export function firmContact(
	branding: Branding | null | undefined,
	appName: string | null
): Adviser {
	const name = appName?.trim() || 'Your firm';
	const icon = branding?.icon_url?.trim();
	return {
		kind: 'firm',
		name,
		firm: name,
		email: branding?.support?.email?.trim() ?? '',
		phone: branding?.support?.phone?.trim() ?? '',
		...(icon ? { photoUrl: icon } : {})
	};
}

/** The adviser if one is assigned, else the firm (above). The one place both pages decide. */
export function adviserOrFirm(
	adviser: Adviser | null | undefined,
	branding: Branding | null | undefined,
	appName: string | null
): Adviser {
	return adviser ?? firmContact(branding, appName);
}
