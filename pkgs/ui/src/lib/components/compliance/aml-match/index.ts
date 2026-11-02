// An AML screening match's profile: the AML console decides on it, and a firm's compliance tab
// shows it once Graphene let the onboarding continue past it.
export { default as AmlMatchProfile } from './aml-match-profile.svelte';
export { default as AmlMatchPhotos } from './aml-match-photos.svelte';
export {
	addressLine,
	bornLine,
	countryReasons,
	groupByType,
	linkHost,
	matchScore,
	matchSubtitle,
	partialDate,
	roleDates,
	type AmlMatchProfileData,
	type MatchAddress,
	type MatchAssociation,
	type MatchCountry,
	type MatchDate,
	type MatchName,
	type MatchReference,
	type MatchRole
} from './aml-match.js';
