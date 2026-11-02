<script lang="ts">
	// One custodian, as the API describes it. No KPI grid: the two figures that used to sit here
	// were fixture text — "Webhook events today 12.4k", "Rows loaded 1.2M", "Outbound queue
	// depth 7" — rendered directly beneath a live status badge with nothing marking them as
	// invented. `enabled_firms` is real and comes from the same payload as the badge.
	import {
		Badge,
		Card,
		CardContent,
		CardDescription,
		CardHeader,
		CardTitle,
		humanise,
		StatusBadge
	} from '@graphene/ui';
	import ArrowRightIcon from '@lucide/svelte/icons/arrow-right';
	import { connectionDetail, connectionStatus, type PlatformStatus } from '../registry';
	import { ROLE_LABEL, STATUS_PULSES, STATUS_TONE, TRANSPORT_LABEL } from '../status';

	let { status }: { status: PlatformStatus } = $props();
	const meta = $derived(status.meta);
	const connection = $derived(connectionStatus(status.service));
</script>

<a href="/platforms/{status.provider}" class="group block">
	<Card glass class="h-full transition-shadow group-hover:shadow-lg">
		<CardHeader class="gap-1">
			<div class="flex items-start justify-between gap-2">
				<CardTitle class="text-xl">{meta.name}</CardTitle>
				<StatusBadge tone={STATUS_TONE[connection]} pulse={STATUS_PULSES.has(connection)}
					>{humanise(connection)}</StatusBadge
				>
			</div>
			<CardDescription>{meta.description}</CardDescription>
		</CardHeader>
		<CardContent class="space-y-3">
			<div class="flex flex-wrap items-center gap-1.5">
				<Badge variant="outline">{ROLE_LABEL[meta.role]}</Badge>
				{#each meta.transports as transport (transport)}
					<Badge variant="ghost" class="font-mono text-[11px]">
						{TRANSPORT_LABEL[transport]}
					</Badge>
				{/each}
			</div>
			<p class="text-muted-foreground text-xs">{connectionDetail(status)}</p>
			<div
				class="text-muted-foreground group-hover:text-foreground flex items-center gap-1 text-sm font-medium transition-colors"
			>
				Open pipeline <ArrowRightIcon class="size-4" />
			</div>
		</CardContent>
	</Card>
</a>
