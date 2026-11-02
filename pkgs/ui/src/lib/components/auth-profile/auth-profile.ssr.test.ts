import { describe, expect, it } from 'vitest';
import { render } from 'svelte/server';
import AuthProfile, { type AuthProfileData } from './auth-profile.svelte';

const profile = (over: Partial<AuthProfileData> = {}): AuthProfileData => ({
	user: {
		id: '3f1c',
		uid: 'profile12345',
		email: 'person@example.test',
		name: 'Example Person',
		mobile: null,
		avatar: null,
		enabled: true,
		types: ['client'],
		created_at: '2026-08-01T09:00:00Z',
		updated_at: '2026-08-01T09:00:00Z',
		invitation_sent_at: null,
		invitation_expires_at: null,
		invitation_pending: false,
		invitation_accepted_at: null,
		last_sign_in: '2026-09-01T09:00:00Z'
	},
	memberships: [
		{
			portal: 'client',
			status: 'active',
			created_at: '2026-08-01T09:00:00Z',
			role_slugs: ['client'],
			permissions: []
		}
	],
	effective_permissions: [],
	client_access: [],
	staff_access: [],
	activity: {
		recorded_actions: 1,
		last_recorded_action: '2026-09-01T09:00:00Z',
		successful_sign_ins: 1,
		failed_sign_ins: 0
	},
	recent_activity: [
		{
			id: 1,
			event_type: 'authentication.succeeded',
			created_at: '2026-09-01T09:00:00Z',
			method: 'POST',
			route: '/auth/succeeded',
			status: 200,
			path_params: {},
			metadata: {}
		}
	],
	security: {
		available: true,
		email_verified: true,
		mfa_enrolled: true,
		factor_count: 1,
		active_sessions: 2
	},
	...over
});

describe('AuthProfile', () => {
	it('combines authentication posture, effective access and activity', () => {
		const { body } = render(AuthProfile, { props: { profile: profile() } });
		expect(body).toContain('Last sign-in');
		expect(body).toContain('Enrolled (1)');
		expect(body).toContain('Live sessions');
		expect(body).toContain('Client Portal');
		expect(body).toContain('Signed in');
		expect(body).toContain('Page views');
	});

	it('states a staff member who may advise', () => {
		const { body } = render(AuthProfile, {
			props: {
				profile: profile({
					user: {
						id: '3f1c',
						uid: 'profile12345',
						email: 'adviser@example.test',
						name: 'Example Adviser',
						mobile: null,
						avatar: null,
						enabled: true,
						types: ['staff'],
						is_adviser: true,
						job_title: 'Chartered Financial Planner',
						created_at: '2026-08-01T09:00:00Z',
						updated_at: '2026-08-01T09:00:00Z',
						invitation_sent_at: null,
						invitation_expires_at: null,
						invitation_pending: false,
						invitation_accepted_at: null,
						last_sign_in: '2026-09-01T09:00:00Z'
					}
				})
			}
		});
		expect(body).toContain('Adviser');
		expect(body).toContain('Chartered Financial Planner');
	});

	it('warns when a client login resolves to no client scope', () => {
		const { body } = render(AuthProfile, { props: { profile: profile() } });
		expect(body).toContain('Not linked to a client record');
		expect(body).toContain('their client scope resolves to nothing');
	});

	it('groups permission verbs and keeps a session to two lines', () => {
		const userAgent = 'Mozilla/5.0 Chrome/120.0';
		const { body } = render(AuthProfile, {
			props: {
				profile: profile({
					effective_permissions: ['actions:write', 'actions:read', 'clients:read'],
					security: {
						available: true,
						sessions: [
							{
								id: 'sess_1',
								ip_address: '203.0.113.8',
								user_agent: userAgent,
								device: 'Chrome on macOS',
								created_at: '2026-10-02T14:13:00Z',
								expires_at: '2026-10-09T12:00:00Z',
								current: true
							}
						]
					}
				})
			}
		});
		expect(body).toContain('actions:read,write');
		expect(body).toContain('clients:read');
		expect(body).not.toContain('>actions:read<');
		expect(body).toContain('Chrome on macOS');
		expect(body).toContain(userAgent);
		expect(body).not.toContain('No user agent recorded');
		expect(body).toContain('203.0.113.8');
		expect(body).toContain('Expires 9 Oct 2026');
		expect(body).not.toContain('9 Oct 2026,');
	});

	it('shows mirrored sessions when the factor check is unavailable', () => {
		const { body } = render(AuthProfile, {
			props: {
				profile: profile({
					security: {
						available: false,
						active_sessions: 1,
						sessions: [
							{
								id: 'sess_1',
								ip_address: '203.0.113.8',
								user_agent: 'Mozilla/5.0',
								device: 'Chrome on macOS',
								created_at: '2026-10-02T14:13:00Z',
								expires_at: null,
								current: true
							}
						]
					}
				})
			}
		});
		expect(body).toContain('Chrome on macOS');
		expect(body).toContain('Unavailable');
		expect(body).not.toContain('Sessions could not be loaded.');
	});

	it('lists a page view beside the actions, newest first', () => {
		const { body } = render(AuthProfile, {
			props: {
				profile: profile({
					recent_page_views: [
						{
							id: 4,
							portal: 'platform',
							path: '/team/profile12345',
							created_at: '2026-10-02T15:00:00Z'
						}
					]
				})
			}
		});
		const viewed = body.indexOf('Viewed /team/profile12345');
		const signedIn = body.indexOf('Signed in');
		expect(viewed).toBeGreaterThan(-1);
		expect(signedIn).toBeGreaterThan(viewed);
		expect(body).toContain('Platform · /team/profile12345');
	});

	it('offers a category tag and a second page once the list is longer than one page', () => {
		const recent_activity = Array.from({ length: 21 }, (_, index) => ({
			id: index + 1,
			event_type: index === 0 ? 'member.updated' : 'authentication.succeeded',
			created_at: '2026-09-01T09:00:00Z',
			method: 'POST',
			route: '/firm/members',
			status: 200,
			path_params: {},
			metadata: {}
		}));
		const { body } = render(AuthProfile, {
			props: { profile: profile({ recent_activity }) }
		});
		expect(body).toContain('People');
		expect(body).toContain('Security');
		expect(body).toContain('aria-label="Pagination"');
	});
});
