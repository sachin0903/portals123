<script lang="ts">
	import { onDestroy, untrack } from 'svelte';
	import BankAccountCheckStatus from './bank-account-check-status.svelte';
	import {
		bankDigits,
		bankPairComplete,
		type BankCheckResult,
		type BankCheckStatus
	} from './types.js';

	let {
		sortCode,
		accountNumber,
		endpoint = '/api/postcoder',
		status = $bindable<BankCheckStatus>('idle'),
		result = $bindable<BankCheckResult | null>(null),
		onchecked
	}: {
		sortCode: string;
		accountNumber: string;
		/** Where the app mounted the proxy. The key never reaches this component. */
		endpoint?: string;
		status?: BankCheckStatus;
		result?: BankCheckResult | null;
		onchecked?: (status: BankCheckStatus, result: BankCheckResult | null) => void;
	} = $props();

	let inFlight: AbortController | null = null;
	let timer: ReturnType<typeof setTimeout> | null = null;

	function publish(next: BankCheckStatus, nextResult: BankCheckResult | null) {
		status = next;
		result = nextResult;
		onchecked?.(next, nextResult);
	}

	async function run(sort: string, number: string, controller: AbortController): Promise<void> {
		try {
			const response = await fetch(`${endpoint}/bank`, {
				method: 'POST',
				headers: { 'content-type': 'application/json' },
				body: JSON.stringify({ sort_code: sort, account_number: number }),
				signal: controller.signal
			});
			if (controller.signal.aborted) return;
			if (!response.ok) {
				publish(response.status === 400 ? 'rejected' : 'unavailable', null);
				return;
			}
			const body = (await response.json()) as BankCheckResult;
			if (controller.signal.aborted) return;
			publish(body.valid ? 'confirmed' : 'rejected', body);
		} catch (error) {
			if ((error as Error)?.name === 'AbortError') return;
			publish('unavailable', null);
		} finally {
			if (inFlight === controller) inFlight = null;
		}
	}

	// Only the two inputs are tracked. Publishing runs untracked: the parent's `onchecked`
	// reads and writes its own state, and tracking that here would re-run this effect forever.
	$effect(() => {
		const sort = bankDigits(sortCode);
		const number = bankDigits(accountNumber);
		return untrack(() => {
			if (!bankPairComplete(sort, number)) {
				inFlight?.abort();
				if (timer) clearTimeout(timer);
				publish('idle', null);
				return;
			}
			publish('checking', null);
			inFlight?.abort();
			const controller = new AbortController();
			inFlight = controller;
			if (timer) clearTimeout(timer);
			timer = setTimeout(() => void run(sort, number, controller), 300);
			return () => {
				controller.abort();
				if (timer) clearTimeout(timer);
			};
		});
	});

	onDestroy(() => {
		inFlight?.abort();
		if (timer) clearTimeout(timer);
	});
</script>

<div data-bank-check={status} class={status === 'idle' ? 'hidden' : undefined}>
	<BankAccountCheckStatus {status} {result} />
</div>
