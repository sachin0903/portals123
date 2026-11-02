export {
	emailVerificationRequired,
	mfaChallengeRequired,
	mfaEnrollmentRequired,
	organizationSelectionRequired,
	pendingTokenInvalid,
	codeRefused,
	type PendingVerification,
	type MfaChallenge,
	type MfaEnrollment
} from './errors';
export { cookieOptions, pendingCookieOptions, type CookieOptions } from './cookies';
export {
	passwordAuth,
	verifyPassword,
	confirmPasswordReset,
	acceptInvitationAndSetPassword,
	isValidCookiePassword,
	COOKIE_PASSWORD_MIN_LENGTH,
	WEAK_PASSWORD_MESSAGE,
	type PasswordAuthResult,
	type PasswordAuthConfig,
	type PasswordCheck,
	type SetPasswordResult
} from './auth';
export { validateNewPassword, MIN_PASSWORD_LENGTH, PASSWORD_HINT } from './validation';
// The classifier the change-password pages need too: they were reading the error themselves and
// flattening every reason onto one message, so a too-long password read as "found in a breach".
export { passwordRejection, linkRejection } from './password-errors';
export {
	safeReturnTo,
	returnToFrom,
	withReturnTo,
	loginPath,
	loginNotice,
	RETURN_TO_PARAM
} from './return-to';
export {
	listLiveSessions,
	loadStoredSessions,
	refreshStoredSessions,
	revokeOtherSessions,
	revokeOwnedSession,
	sessionIdFromAccessToken,
	type LiveSession,
	type SessionSweep,
	type StoredSession
} from './sessions';
export {
	deviceLabel,
	fillCurrentBrowser,
	readSignInClient,
	signInClient,
	workosClientFields,
	type CurrentSessionFacts,
	type SignInClient
} from './client-context';
export {
	committedPagePath,
	handlePageViewRequest,
	recordPageView,
	sendPageView,
	shouldRecordPageView,
	type PageViewEvent
} from './page-view';
export {
	fetchMe,
	fetchPermissions,
	type FetchPermissionsOptions,
	type MeSnapshot
} from './permissions';
export {
	validatePortalSession,
	type PortalSessionOptions,
	type PortalSessionResult
} from './portal-session';
export { installPortalHeader, PORTAL_HEADER, type PortalName } from './portal-header';
export { currentTraceParent, mintPageTrace, withTraceParent, type PageTrace } from './trace';
export { privateSseResponse, type PrivateSseOptions } from './private-sse';
export {
	MAX_RELAY_BYTES,
	RelayTooLarge,
	readBounded,
	relayBuffered,
	type RelayOptions
} from './relay';
export { crossSiteRefusal, isCrossSiteMutation } from './csrf';
export {
	decideGate,
	gateRefusal,
	isNavigationRequest,
	refusePortal,
	type GateDecision,
	type GateInput
} from './gates';
export {
	devBypassAllowed,
	devPrincipal,
	requestHostname,
	type DevBypassRequest,
	type DevPrincipal
} from './dev-bypass';
export { applySecurityHeaders, DEFAULT_CSP, type SecurityHeadersOptions } from './security-headers';
export {
	resolveSession,
	type AuthenticateResult,
	type SealedSessionLike,
	type ResolveSessionOutcome,
	type ResolveSessionOptions
} from './session';
export {
	resolveEnvironment,
	consumeRateLimit,
	mfaChallengeHandle,
	requestPasswordReset,
	reportAuthEvent,
	safeClientAddress,
	notifyOnboardingComplete,
	clearEnvironmentCache,
	type EnvironmentConfig,
	type ResolveEnvironmentOptions
} from './environment';
export {
	brandingHead,
	brandingPreviewStyle,
	googleFontHref,
	isFontFamily,
	resolveClientPortalTemplate,
	COLOUR_VARS_KEYS,
	FIXED_COLOUR_KEYS,
	CARD_FINISHES,
	isSafeCardArt,
	isCardColor,
	staffPortalBranding,
	MONO_FONT_OPTIONS,
	DISPLAY_FONT_OPTIONS,
	type Branding,
	type BrandingColors,
	type CardDesign,
	type CardFinish,
	type BrandingGlass,
	type BrandingBlock,
	type BrandingPage,
	type BrandingPageLayouts,
	type ClientPortalTemplate,
	type PortalTemplateSelector,
	type Surface
} from './branding';
export {
	THEME_COOKIE,
	THEME_COOKIE_MAX_AGE,
	THEME_CHOICES,
	DARK_CLASS,
	CHOICE_ATTR,
	MODE_ATTR,
	COLOUR_SCHEME_HINT,
	parseThemeChoice,
	parseTenantMode,
	themeChoiceFromCookie,
	effectiveChoice,
	resolveScheme,
	prefersDarkFromHint,
	themeCookie,
	themeAttributes,
	themeHead,
	type ColourScheme,
	type ThemeChoice,
	type TenantMode,
	type ThemeInput
} from './theme';
export {
	generateTheme,
	heroInk,
	applyTheme,
	applyAccents,
	hexToOklch,
	colorToHex,
	contrastRatio,
	THEME_STYLES,
	THEME_STYLE_KEYS,
	THEME_PRESETS,
	FONT_OPTIONS,
	type Mode,
	type ThemeStyleKey,
	type ThemeStyle,
	type ThemePreset,
	type GeneratedTheme
} from './theme-generator';
