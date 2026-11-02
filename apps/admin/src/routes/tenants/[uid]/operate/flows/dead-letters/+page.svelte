<script lang="ts">
	import { Button, Card, EmptyState, LoadErrorBanner, PageHeader } from '@graphene/ui';
	import CircleCheckIcon from '@lucide/svelte/icons/circle-check';
	import { enhance } from '$app/forms';
	import { page } from '$app/state';
	import type { PageData } from './$types';
	let { data }: { data: PageData } = $props();
</script>

<PageHeader
	title="Failed side-effects"
	description="Side-effects the flow worker gave up on."
	back={{ href: `/tenants/${page.params.uid}/operate/flows`, label: 'Flows' }}
/>
{#if data.loadFailed}<LoadErrorBanner
		message="Couldn’t load failed side-effects."
	/>{:else if !data.rows.length}<EmptyState
		icon={CircleCheckIcon}
		title="Nothing has failed"
		description="Every task this firm's flows raised and every document they generated went through."
	/>{:else}<div class="mt-5 space-y-3">
		{#each data.rows as row (row.kind + row.uid)}<Card class="p-4"
				><div class="flex justify-between gap-3">
					<div>
						<p class="font-medium">{row.summary ?? row.event_type}</p>
						<p class="text-muted-foreground text-xs">
							{row.attempts}/{row.max_attempts} attempts · {row.last_error ?? 'No error detail'}
						</p>
					</div>
					{#if data.canReplay}<form method="POST" action="?/replay" use:enhance>
							<input type="hidden" name="kind" value={row.kind} /><input
								type="hidden"
								name="uid"
								value={row.uid}
							/><Button variant="outline" size="sm">Replay</Button>
						</form>{/if}
				</div></Card
			>{/each}
	</div>{/if}
