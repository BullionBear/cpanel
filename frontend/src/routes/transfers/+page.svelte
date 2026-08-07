<script lang="ts">
	import { onDestroy, onMount } from 'svelte';
	import { ApiError } from '$lib/api';
	import { subscribeBalanceUpdates } from '$lib/events';
	import { fetchHoldings, fetchOverview, type Holdings, type OverviewItem } from '$lib/portfolio';
	import {
		fetchCapabilities,
		fetchTree,
		type AccountInfo,
		type AccountNode,
		type ExchangeCapabilities
	} from '$lib/accounts';
	import {
		createTransfer,
		createWithdrawal,
		failTransfer,
		fetchAvailableNetworks,
		fetchDepositAddress,
		fetchRecentDeposits,
		fetchTransfers,
		previewTransfer,
		previewWithdrawal,
		retryTransfer,
		type AssetNetworkOption,
		type DepositAddress,
		type DepositRow,
		type TransferPreview,
		type TransferRow,
		type WithdrawPreview
	} from '$lib/transfers';

	type Mode = 'transfer' | 'withdraw' | 'deposit';

	let accounts: OverviewItem[] = [];
	let transfers: TransferRow[] = [];
	let loading = false;
	let listError = '';
	let expanded: number | null = null;
	let pollTimer: ReturnType<typeof setInterval> | undefined;
	let unsubEvents: (() => void) | undefined;

	let mode: Mode = 'transfer';

	// -- form state --
	let fromId = '';
	let toId = '';
	let asset = 'USDT';
	let amount = '';
	let fromWallet = '';
	let toWallet = '';
	let network = '';
	let confirmed = false;
	let preview: TransferPreview | null = null;
	let previewing = false;
	let submitting = false;
	let formError = '';
	let formNotice = '';

	// -- withdraw state --
	let wFromId = '';
	let wAsset = 'USDT';
	let wAmount = '';
	let wNetwork = '';
	let wAddress = '';
	let wTag = '';
	let wConfirmed = false;
	let wPreview: WithdrawPreview | null = null;
	let wPreviewing = false;

	// -- selected-account balances (from the latest snapshot) --
	let holdings: Holdings | null = null;
	let holdingsLoading = false;
	let holdingsFor: number | null = null;
	const holdingsCache = new Map<number, Holdings>();

	// -- deposit state --
	let dAccountId = '';
	let dAsset = 'USDT';
	let dNetwork = '';
	let dAddress: DepositAddress | null = null;
	let dDeposits: DepositRow[] = [];
	let dLoading = false;
	let dLoaded = false;

	// Wallet structure differs per exchange (Binance: SPOT/FUNDING/FUTURES,
	// Bybit: UNIFIED/FUNDING), served by the capabilities endpoint.
	let walletsByExchange: Record<string, string[]> = {};
	let exchangeCaps: Record<string, ExchangeCapabilities> = {};
	/** Accounts whose active credential can withdraw (venue-verified). */
	let withdrawableIds = new Set<number>();

	// Venue-derived network options for the active tab.
	let networkOptions: AssetNetworkOption[] = [];
	let networksLoading = false;
	let networksError = '';
	let networksKey = '';
	let networkFetchTimer: ReturnType<typeof setTimeout> | undefined;

	$: fromAccount = accounts.find((a) => String(a.account_id) === fromId);
	$: toAccount = accounts.find((a) => String(a.account_id) === toId);
	$: intraWallets = fromAccount ? (walletsByExchange[fromAccount.exchange] ?? []) : [];
	$: cross = !!fromAccount && !!toAccount && fromAccount.exchange !== toAccount.exchange;
	$: sameAccount = !!fromAccount && !!toAccount && fromAccount.account_id === toAccount.account_id;

	$: withdrawAccounts = accounts.filter(
		(a) => withdrawableIds.has(a.account_id) && exchangeCaps[a.exchange]?.withdrawals
	);
	$: depositAccounts = accounts.filter((a) => exchangeCaps[a.exchange]?.deposits);

	function resetPreview() {
		preview = null;
		confirmed = false;
	}

	function flattenAccounts(nodes: AccountNode[]): AccountInfo[] {
		const out: AccountInfo[] = [];
		for (const node of nodes) {
			out.push(node);
			out.push(...node.children);
		}
		return out;
	}

	function networkLabel(n: AssetNetworkOption): string {
		return `${n.network} · fee ${n.withdraw_fee} · min ${n.withdraw_min}`;
	}

	function applySuggestedNetwork(
		suggested: string | null,
		current: string,
		assign: (value: string) => void
	) {
		if (suggested && (!current || !networkOptions.some((n) => n.network === current))) {
			assign(suggested);
			return;
		}
		if (current && !networkOptions.some((n) => n.network === current)) {
			assign('');
		}
	}

	async function loadNetworks(
		key: string,
		params: { asset: string; from_account_id?: number; to_account_id?: number },
		onSuggest: (suggested: string | null) => void
	) {
		networksKey = key;
		networksError = '';
		if (!params.asset.trim() || (params.from_account_id == null && params.to_account_id == null)) {
			networkOptions = [];
			return;
		}
		networksLoading = true;
		try {
			const result = await fetchAvailableNetworks({
				...params,
				asset: params.asset.trim()
			});
			if (networksKey !== key) return;
			networkOptions = result.networks;
			onSuggest(result.suggested_network);
			if (result.networks.length === 0) {
				networksError = 'No enabled networks for this asset on the selected account(s).';
			}
		} catch (e) {
			if (networksKey !== key) return;
			networkOptions = [];
			networksError = e instanceof ApiError ? e.message : 'Could not load networks';
		} finally {
			if (networksKey === key) networksLoading = false;
		}
	}

	function scheduleNetworkLoad() {
		clearTimeout(networkFetchTimer);
		networkFetchTimer = setTimeout(() => {
			if (mode === 'withdraw' && wFromId && wAsset.trim()) {
				loadNetworks(
					`w:${wFromId}:${wAsset.trim().toUpperCase()}`,
					{ from_account_id: Number(wFromId), asset: wAsset },
					(suggested) => {
						const before = wNetwork;
						applySuggestedNetwork(suggested, wNetwork, (v) => (wNetwork = v));
						if (wNetwork !== before) resetWithdrawPreview();
					}
				);
			} else if (mode === 'transfer' && cross && fromId && toId && asset.trim()) {
				loadNetworks(
					`c:${fromId}:${toId}:${asset.trim().toUpperCase()}`,
					{
						from_account_id: Number(fromId),
						to_account_id: Number(toId),
						asset
					},
					(suggested) => {
						const before = network;
						applySuggestedNetwork(suggested, network, (v) => (network = v));
						if (network !== before) resetPreview();
					}
				);
			} else if (mode === 'deposit' && dAccountId && dAsset.trim()) {
				loadNetworks(
					`d:${dAccountId}:${dAsset.trim().toUpperCase()}`,
					{ to_account_id: Number(dAccountId), asset: dAsset },
					(suggested) => {
						applySuggestedNetwork(suggested, dNetwork, (v) => (dNetwork = v));
						dAddress = null;
						dLoaded = false;
					}
				);
			} else {
				networksKey = '';
				networkOptions = [];
				networksError = '';
				networksLoading = false;
			}
		}, 250);
	}

	$: {
		mode;
		wFromId;
		wAsset;
		fromId;
		toId;
		asset;
		dAccountId;
		dAsset;
		cross;
		scheduleNetworkLoad();
	}

	// Sole withdrawable account → preselect on the withdraw tab.
	$: if (mode === 'withdraw' && !wFromId && withdrawAccounts.length === 1) {
		wFromId = String(withdrawAccounts[0].account_id);
	}
	$: if (mode === 'deposit' && !dAccountId && depositAccounts.length === 1) {
		dAccountId = String(depositAccounts[0].account_id);
	}

	async function loadTransfers() {
		try {
			const previous = transfers;
			transfers = await fetchTransfers({ limit: '100' });
			listError = '';
			// Cross/withdraw paths finish via the progress job — refresh holdings then.
			const newlyCompleted = transfers.filter(
				(t) =>
					t.status === 'completed' &&
					previous.some((p) => p.id === t.id && p.status === 'in_progress')
			);
			for (const t of newlyCompleted) {
				await invalidateHoldings(t.from_account_id, t.to_account_id);
			}
		} catch (e) {
			listError =
				e instanceof ApiError && e.status === 401
					? 'Please log in to manage transfers.'
					: 'Failed to load transfers.';
		}
	}

	async function runPreview() {
		if (!fromAccount || !toAccount || !amount || !network) return;
		previewing = true;
		formError = '';
		try {
			preview = await previewTransfer({
				from_account_id: fromAccount.account_id,
				to_account_id: toAccount.account_id,
				asset,
				amount,
				network
			});
		} catch (e) {
			preview = null;
			formError = e instanceof ApiError ? e.message : 'Preview failed';
		} finally {
			previewing = false;
		}
	}

	async function submit() {
		if (!fromAccount || !toAccount || !amount || submitting) return;
		submitting = true;
		formError = '';
		formNotice = '';
		try {
			const created = await createTransfer({
				from_account_id: fromAccount.account_id,
				to_account_id: toAccount.account_id,
				asset,
				amount,
				from_wallet: fromWallet || undefined,
				to_wallet: toWallet || undefined,
				network: cross ? network : undefined,
				confirm: cross ? confirmed : undefined
			});
			formNotice = `Transfer #${created.id} ${created.status.replace('_', ' ')}`;
			amount = '';
			resetPreview();
			expanded = created.id;
			await loadTransfers();
			if (created.status === 'completed') {
				await invalidateHoldings(created.from_account_id, created.to_account_id);
			}
		} catch (e) {
			formError = e instanceof ApiError ? e.message : 'Transfer failed to start';
		} finally {
			submitting = false;
		}
	}

	function switchMode(next: Mode) {
		if (mode === next) return;
		mode = next;
		formError = '';
		formNotice = '';
		networksKey = '';
		networkOptions = [];
		networksError = '';
	}

	// The account whose balances to show: the source account of the active tab.
	$: balanceAccountId = mode === 'transfer' ? fromId : mode === 'withdraw' ? wFromId : dAccountId;
	$: balanceAccount = accounts.find((a) => String(a.account_id) === balanceAccountId) ?? null;
	$: loadHoldings(balanceAccount);

	async function loadHoldings(account: OverviewItem | null, force = false) {
		if (!account) {
			holdings = null;
			holdingsFor = null;
			return;
		}
		if (!force && holdingsFor === account.account_id) return;
		holdingsFor = account.account_id;
		if (!force) {
			const cached = holdingsCache.get(account.account_id);
			if (cached) {
				holdings = cached;
				return;
			}
		}
		holdings = null;
		holdingsLoading = true;
		try {
			const h = await fetchHoldings(account.exchange, account.label);
			holdingsCache.set(account.account_id, h);
			if (holdingsFor === account.account_id) holdings = h;
		} catch {
			if (holdingsFor === account.account_id) holdings = null;
		} finally {
			holdingsLoading = false;
		}
	}

	/** Drop cached holdings for accounts touched by a transfer and refetch the panel. */
	async function invalidateHoldings(...accountIds: Array<number | null | undefined>) {
		const ids = [...new Set(accountIds.filter((id): id is number => id != null))];
		for (const id of ids) holdingsCache.delete(id);
		const current = balanceAccount;
		if (current && ids.includes(current.account_id)) {
			await loadHoldings(current, true);
		}
	}

	/** Clicking a balance row fills the active tab's asset field. */
	function useAsset(assetName: string) {
		if (mode === 'transfer') {
			asset = assetName;
			resetPreview();
		} else if (mode === 'withdraw') {
			wAsset = assetName;
			resetWithdrawPreview();
		} else {
			dAsset = assetName;
		}
	}

	function resetWithdrawPreview() {
		wPreview = null;
		wConfirmed = false;
	}

	async function runWithdrawPreview() {
		if (!wFromId || !wAmount || !wNetwork) return;
		wPreviewing = true;
		formError = '';
		try {
			wPreview = await previewWithdrawal({
				from_account_id: Number(wFromId),
				asset: wAsset,
				amount: wAmount,
				network: wNetwork
			});
		} catch (e) {
			wPreview = null;
			formError = e instanceof ApiError ? e.message : 'Preview failed';
		} finally {
			wPreviewing = false;
		}
	}

	async function submitWithdrawal() {
		if (!wFromId || !wAmount || !wAddress || !wNetwork || submitting) return;
		submitting = true;
		formError = '';
		formNotice = '';
		try {
			const created = await createWithdrawal({
				from_account_id: Number(wFromId),
				asset: wAsset,
				amount: wAmount,
				address: wAddress,
				tag: wTag || undefined,
				network: wNetwork,
				confirm: wConfirmed
			});
			formNotice = `Withdrawal #${created.id} ${created.status.replace('_', ' ')}`;
			wAmount = '';
			wAddress = '';
			wTag = '';
			resetWithdrawPreview();
			expanded = created.id;
			await loadTransfers();
			if (created.status === 'completed') {
				await invalidateHoldings(created.from_account_id);
			}
		} catch (e) {
			formError = e instanceof ApiError ? e.message : 'Withdrawal failed to start';
		} finally {
			submitting = false;
		}
	}

	async function loadDepositInfo() {
		if (!dAccountId || !dAsset || !dNetwork || dLoading) return;
		dLoading = true;
		formError = '';
		dAddress = null;
		try {
			dAddress = await fetchDepositAddress(Number(dAccountId), dAsset, dNetwork);
		} catch (e) {
			formError = e instanceof ApiError ? e.message : 'Could not resolve the deposit address';
		}
		try {
			dDeposits = await fetchRecentDeposits(Number(dAccountId), dAsset);
			dLoaded = true;
		} catch {
			dDeposits = [];
		}
		dLoading = false;
	}

	async function retry(transfer: TransferRow) {
		try {
			await retryTransfer(transfer.id);
			await loadTransfers();
		} catch (e) {
			listError = e instanceof ApiError ? e.message : 'Retry failed';
		}
	}

	async function markFailed(transfer: TransferRow) {
		const reason = prompt('Reason for failing this transfer (venue side is not touched):');
		if (!reason) return;
		try {
			await failTransfer(transfer.id, reason);
			await loadTransfers();
		} catch (e) {
			listError = e instanceof ApiError ? e.message : 'Could not mark failed';
		}
	}

	function accountLabel(id: number): string {
		const account = accounts.find((a) => a.account_id === id);
		return account ? `${account.exchange} · ${account.label}` : `#${id}`;
	}

	function shortAddr(address: string | null): string {
		if (!address) return '';
		return address.length > 14 ? `${address.slice(0, 8)}…${address.slice(-4)}` : address;
	}

	function num(value: string | null): string {
		if (value === null) return '—';
		return value.includes('.') ? value.replace(/\.?0+$/, '') : value;
	}

	function ts(value: string | null): string {
		return value ? new Date(value).toLocaleString() : '—';
	}

	function stepDetail(detail: Record<string, unknown> | null): string {
		if (!detail) return '';
		return Object.entries(detail)
			.filter(([, v]) => v !== null && v !== '')
			.map(([k, v]) => `${k}: ${v}`)
			.join(' · ');
	}

	$: hasLive = transfers.some((t) => t.status === 'in_progress');

	// Selecting an account on another exchange invalidates a picked wallet.
	$: {
		if (fromWallet && !intraWallets.includes(fromWallet)) fromWallet = '';
		if (toWallet && !intraWallets.includes(toWallet)) toWallet = '';
	}

	onMount(async () => {
		loading = true;
		try {
			accounts = await fetchOverview();
		} catch {
			accounts = [];
		}
		try {
			const caps = await fetchCapabilities();
			walletsByExchange = Object.fromEntries(caps.map((c) => [c.exchange, c.wallets]));
			exchangeCaps = Object.fromEntries(caps.map((c) => [c.exchange, c]));
		} catch {
			walletsByExchange = {};
			exchangeCaps = {};
		}
		try {
			const tree = await fetchTree();
			withdrawableIds = new Set(
				flattenAccounts(tree)
					.filter((a) =>
						a.credentials.some((c) => c.status === 'active' && c.can_withdraw === true)
					)
					.map((a) => a.id)
			);
		} catch {
			withdrawableIds = new Set();
		}
		await loadTransfers();
		loading = false;
		// Live step status while anything is pending.
		pollTimer = setInterval(() => {
			if (hasLive) loadTransfers();
		}, 10_000);
		unsubEvents = subscribeBalanceUpdates((ev) => {
			void invalidateHoldings(ev.account_id);
			void fetchOverview()
				.then((rows) => {
					accounts = rows;
				})
				.catch(() => {
					/* keep prior overview */
				});
		});
	});

	onDestroy(() => {
		clearInterval(pollTimer);
		clearTimeout(networkFetchTimer);
		unsubEvents?.();
	});
</script>

<svelte:head>
	<title>Transfers · CPanel</title>
</svelte:head>

<div class="page">
	<div class="page-head">
		<h1>Transfers</h1>
		<span class="count">{transfers.length} transfers</span>
	</div>

	<div class="card form-card">
		<div class="mode-tabs">
			<button class="tab" class:active={mode === 'transfer'} on:click={() => switchMode('transfer')}>
				Transfer
			</button>
			<button class="tab" class:active={mode === 'withdraw'} on:click={() => switchMode('withdraw')}>
				Withdraw
			</button>
			<button class="tab" class:active={mode === 'deposit'} on:click={() => switchMode('deposit')}>
				Deposit
			</button>
		</div>

		{#if mode === 'transfer'}
		<div class="form-grid">
			<label>
				<span>From account</span>
				<select bind:value={fromId} on:change={resetPreview}>
					<option value="">Select…</option>
					{#each accounts as account (account.account_id)}
						<option value={String(account.account_id)}>
							{account.exchange} · {account.label}
						</option>
					{/each}
				</select>
			</label>
			<label>
				<span>To account</span>
				<select bind:value={toId} on:change={resetPreview}>
					<option value="">Select…</option>
					{#each accounts as account (account.account_id)}
						<option value={String(account.account_id)}>
							{account.exchange} · {account.label}
						</option>
					{/each}
				</select>
			</label>
			<label>
				<span>Asset</span>
				<input type="text" bind:value={asset} on:input={resetPreview} placeholder="USDT" />
			</label>
			<label>
				<span>Amount</span>
				<input type="text" bind:value={amount} on:input={resetPreview} placeholder="0.0" />
			</label>

			{#if !cross}
				<label>
					<span>From wallet {sameAccount ? '(required)' : '(optional)'}</span>
					<select bind:value={fromWallet}>
						<option value="">Default</option>
						{#each intraWallets as wallet}
							<option value={wallet}>{wallet}</option>
						{/each}
					</select>
				</label>
				<label>
					<span>To wallet {sameAccount ? '(required)' : '(optional)'}</span>
					<select bind:value={toWallet}>
						<option value="">Default</option>
						{#each intraWallets as wallet}
							<option value={wallet}>{wallet}</option>
						{/each}
					</select>
				</label>
			{:else}
				<label>
					<span>Network</span>
					<select bind:value={network} on:change={resetPreview} disabled={networksLoading}>
						<option value="">
							{networksLoading
								? 'Loading networks…'
								: networkOptions.length
									? 'Select network…'
									: 'No networks available'}
						</option>
						{#each networkOptions as n (n.network)}
							<option value={n.network}>{networkLabel(n)}</option>
						{/each}
					</select>
					{#if networksError}<span class="field-hint error">{networksError}</span>{/if}
				</label>
			{/if}
		</div>

		{#if cross}
			<div class="cross-flow">
				<span class="badge cross-badge">cross-exchange · irreversible · on-chain withdrawal</span>
				<button
					class="btn small"
					on:click={runPreview}
					disabled={previewing || !fromId || !toId || !amount || !network}
				>
					{previewing ? 'Checking…' : 'Preview'}
				</button>
				{#if preview}
					{#if preview.ok}
						<div class="preview ok">
							Validated — withdrawal min {preview.withdraw_min ?? '—'}
							{asset}, expected fee {preview.expected_fee ?? '—'} {asset}
							{#if preview.deposit_via_master}· deposit tracked via the master key{/if}
						</div>
						<label class="confirm">
							<input type="checkbox" bind:checked={confirmed} />
							<span>
								I reviewed the preview and confirm sending {amount} {asset} over
								{network.toUpperCase()}
							</span>
						</label>
					{:else}
						<div class="preview bad">
							{#each preview.problems as problem}
								<div>{problem}</div>
							{/each}
						</div>
					{/if}
				{/if}
			</div>
		{/if}

		<div class="form-actions">
			<button
				class="btn"
				on:click={submit}
				disabled={submitting ||
					!fromId ||
					!toId ||
					!amount ||
					(cross && (!network || !confirmed || !preview?.ok))}
			>
				{submitting ? 'Submitting…' : cross ? 'Send cross-exchange transfer' : 'Transfer'}
			</button>
			{#if formError}<span class="form-msg error">{formError}</span>{/if}
			{#if formNotice}<span class="form-msg">{formNotice}</span>{/if}
		</div>

		{:else if mode === 'withdraw'}
		<div class="form-grid">
			<label>
				<span>From account</span>
				<select bind:value={wFromId} on:change={resetWithdrawPreview}>
					<option value="">Select…</option>
					{#each withdrawAccounts as account (account.account_id)}
						<option value={String(account.account_id)}>
							{account.exchange} · {account.label}
						</option>
					{/each}
				</select>
				{#if withdrawAccounts.length === 0}
					<span class="field-hint">No accounts with a withdrawal-enabled key.</span>
				{/if}
			</label>
			<label>
				<span>Asset</span>
				<input type="text" bind:value={wAsset} on:input={resetWithdrawPreview} placeholder="USDT" />
			</label>
			<label>
				<span>Amount</span>
				<input type="text" bind:value={wAmount} on:input={resetWithdrawPreview} placeholder="0.0" />
			</label>
			<label>
				<span>Network</span>
				<select
					bind:value={wNetwork}
					on:change={resetWithdrawPreview}
					disabled={networksLoading || !wFromId}
				>
					<option value="">
						{networksLoading
							? 'Loading networks…'
							: networkOptions.length
								? 'Select network…'
								: 'No networks available'}
					</option>
					{#each networkOptions as n (n.network)}
						<option value={n.network}>{networkLabel(n)}</option>
					{/each}
				</select>
				{#if networksError}<span class="field-hint error">{networksError}</span>{/if}
			</label>
			<label class="wide">
				<span>Destination address</span>
				<input type="text" bind:value={wAddress} placeholder="External wallet address" />
			</label>
			<label>
				<span>Tag / memo (optional)</span>
				<input type="text" bind:value={wTag} placeholder="—" />
			</label>
		</div>

		<div class="cross-flow">
			<span class="badge cross-badge">on-chain withdrawal · irreversible</span>
			<button
				class="btn small"
				on:click={runWithdrawPreview}
				disabled={wPreviewing || !wFromId || !wAmount || !wNetwork}
			>
				{wPreviewing ? 'Checking…' : 'Preview'}
			</button>
			{#if wPreview}
				{#if wPreview.ok}
					<div class="preview ok">
						Validated — withdrawal min {wPreview.withdraw_min ?? '—'}
						{wAsset}, expected fee {wPreview.expected_fee ?? '—'} {wAsset}
					</div>
					<label class="confirm">
						<input type="checkbox" bind:checked={wConfirmed} />
						<span>
							I verified the address and confirm withdrawing {wAmount} {wAsset} over
							{wNetwork.toUpperCase()}
						</span>
					</label>
				{:else}
					<div class="preview bad">
						{#each wPreview.problems as problem}
							<div>{problem}</div>
						{/each}
					</div>
				{/if}
			{/if}
		</div>

		<div class="form-actions">
			<button
				class="btn"
				on:click={submitWithdrawal}
				disabled={submitting ||
					!wFromId ||
					!wAmount ||
					!wAddress ||
					!wNetwork ||
					!wConfirmed ||
					!wPreview?.ok}
			>
				{submitting ? 'Submitting…' : 'Withdraw'}
			</button>
			{#if formError}<span class="form-msg error">{formError}</span>{/if}
			{#if formNotice}<span class="form-msg">{formNotice}</span>{/if}
		</div>

		{:else}
		<div class="form-grid">
			<label>
				<span>Account</span>
				<select
					bind:value={dAccountId}
					on:change={() => {
						dAddress = null;
						dLoaded = false;
					}}
				>
					<option value="">Select…</option>
					{#each depositAccounts as account (account.account_id)}
						<option value={String(account.account_id)}>
							{account.exchange} · {account.label}
						</option>
					{/each}
				</select>
			</label>
			<label>
				<span>Asset</span>
				<input type="text" bind:value={dAsset} placeholder="USDT" />
			</label>
			<label>
				<span>Network</span>
				<select bind:value={dNetwork} disabled={networksLoading || !dAccountId}>
					<option value="">
						{networksLoading
							? 'Loading networks…'
							: networkOptions.length
								? 'Select network…'
								: 'No networks available'}
					</option>
					{#each networkOptions as n (n.network)}
						<option value={n.network}>{networkLabel(n)}</option>
					{/each}
				</select>
				{#if networksError}<span class="field-hint error">{networksError}</span>{/if}
			</label>
		</div>

		<div class="form-actions">
			<button
				class="btn"
				on:click={loadDepositInfo}
				disabled={dLoading || !dAccountId || !dAsset || !dNetwork}
			>
				{dLoading ? 'Loading…' : 'Show deposit address'}
			</button>
			{#if formError}<span class="form-msg error">{formError}</span>{/if}
		</div>

		{#if dAddress}
			<div class="deposit-address">
				<div class="preview ok">
					Send only {dAddress.asset} over {dAddress.network} to this address — the venue
					resolves it fresh each time.
				</div>
				<div class="address-line mono">
					{dAddress.address}
					<button
						class="btn small secondary"
						on:click={() => navigator.clipboard.writeText(dAddress?.address ?? '')}
					>
						Copy
					</button>
				</div>
				{#if dAddress.tag}
					<div class="meta mono">tag / memo: {dAddress.tag} (required)</div>
				{/if}
			</div>
		{/if}

		{#if dLoaded}
			<div class="recent-deposits">
				<h2>Recent deposits</h2>
				{#if dDeposits.length === 0}
					<div class="meta">No recent {dAsset} deposits reported by the venue.</div>
				{:else}
					<table>
						<thead>
							<tr>
								<th>Credited</th>
								<th class="right">Amount</th>
								<th>Network</th>
								<th>Status</th>
								<th>Tx</th>
							</tr>
						</thead>
						<tbody>
							{#each dDeposits as d (d.native_id)}
								<tr>
									<td class="mono">{ts(d.credited_at)}</td>
									<td class="mono right">{num(d.amount)} {d.asset}</td>
									<td>{d.network ?? '—'}</td>
									<td>
										<span
											class="badge"
											class:active={d.status === 'CREDITED'}
											class:halted={d.status === 'FAILED'}
										>
											{d.status.toLowerCase()}
										</span>
									</td>
									<td class="mono">{d.tx_id ?? '—'}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				{/if}
			</div>
		{/if}
		{/if}

		{#if balanceAccount}
			<div class="balances">
				<div class="balances-head">
					<span class="balances-title">
						Balances · {balanceAccount.exchange} · {balanceAccount.label}
					</span>
					{#if holdings?.taken_at}
						<span class="balances-meta">snapshot {ts(holdings.taken_at)}</span>
					{/if}
				</div>
				{#if holdingsLoading}
					<div class="balances-empty">Loading balances…</div>
				{:else if !holdings || holdings.balances.length === 0}
					<div class="balances-empty">No snapshot balances for this account yet.</div>
				{:else}
					<div class="balances-scroll">
					<table>
						<thead>
							<tr>
								<th>Wallet</th>
								<th>Asset</th>
								<th class="right">Free</th>
								<th class="right">Locked</th>
								<th class="right">Total</th>
								<th class="right">Value ({holdings.quote_asset})</th>
							</tr>
						</thead>
						<tbody>
							{#each holdings.balances as b (b.wallet + b.asset)}
								<tr
									class="balance-row"
									title="Use {b.asset}"
									on:click={() => useAsset(b.asset)}
								>
									<td>{b.wallet}</td>
									<td class="mono strong">{b.asset}</td>
									<td class="mono right">{num(b.free)}</td>
									<td class="mono right">{num(b.locked)}</td>
									<td class="mono right">{num(b.total)}</td>
									<td class="mono right">{b.value_quote === null ? '—' : num(b.value_quote)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
					</div>
				{/if}
			</div>
		{/if}
	</div>

	{#if listError}
		<div class="notice">{listError}</div>
	{:else if loading}
		<div class="notice">Loading…</div>
	{:else if transfers.length === 0}
		<div class="notice">No transfers yet.</div>
	{:else}
		<div class="table-wrap">
			<table>
				<thead>
					<tr>
						<th></th>
						<th>Created</th>
						<th>Kind</th>
						<th>Route</th>
						<th class="right">Amount</th>
						<th class="right">Fee</th>
						<th>Status</th>
						<th></th>
					</tr>
				</thead>
				<tbody>
					{#each transfers as t (t.id)}
						<tr
							class="row"
							class:open={expanded === t.id}
							on:click={() => (expanded = expanded === t.id ? null : t.id)}
						>
							<td class="chevron">{expanded === t.id ? '▾' : '▸'}</td>
							<td class="mono">{ts(t.created_at)}</td>
							<td>{t.kind}</td>
							<td>
								{accountLabel(t.from_account_id)}
								{#if t.from_wallet}<span class="muted">({t.from_wallet})</span>{/if}
								<span class="muted">→</span>
								{#if t.to_account_id !== null}
									{accountLabel(t.to_account_id)}
								{:else}
									external <span class="mono muted">{shortAddr(t.address)}</span>
								{/if}
								{#if t.to_wallet}<span class="muted">({t.to_wallet})</span>{/if}
								{#if t.network}<span class="muted">via {t.network}</span>{/if}
							</td>
							<td class="mono right">{num(t.amount)} {t.asset}</td>
							<td class="mono right">{t.fee ? `${num(t.fee)} ${t.fee_asset ?? ''}` : '—'}</td>
							<td>
								<span
									class="badge"
									class:active={t.status === 'completed'}
									class:halted={t.status === 'failed'}
								>
									{t.status.replace('_', ' ')}
								</span>
								{#if t.stuck}<span class="badge halted">stuck</span>{/if}
							</td>
							<td class="actions" on:click|stopPropagation>
								{#if t.status === 'in_progress'}
									{#if t.stuck}
										<button class="btn small" on:click={() => retry(t)}>Retry</button>
									{/if}
									<button class="btn small secondary" on:click={() => markFailed(t)}>
										Fail
									</button>
								{/if}
							</td>
						</tr>
						{#if expanded === t.id}
							<tr class="detail-row">
								<td colspan="8">
									<div class="steps">
										{#each t.steps as step (step.seq)}
											<div class="step {step.status}">
												<span class="step-dot"></span>
												<span class="step-name">{step.name.replace(/_/g, ' ')}</span>
												<span class="step-status">{step.status}</span>
												{#if stepDetail(step.detail)}
													<span class="step-detail">{stepDetail(step.detail)}</span>
												{/if}
												{#if step.error}
													<span class="step-error">{step.error}</span>
												{/if}
											</div>
										{/each}
										{#if t.address}
											<div class="meta mono">
												address {t.address}{t.address_tag ? ` · tag ${t.address_tag}` : ''}
											</div>
										{/if}
										{#if t.tx_id}
											<div class="meta mono">tx {t.tx_id}</div>
										{/if}
										{#if t.received_amount}
											<div class="meta">received {num(t.received_amount)} {t.asset}</div>
										{/if}
										{#if t.error}
											<div class="meta error">{t.error}</div>
										{/if}
									</div>
								</td>
							</tr>
						{/if}
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</div>

<style>
	.page {
		max-width: 82rem;
		margin: 0 auto;
	}
	.page-head {
		display: flex;
		align-items: baseline;
		gap: 0.75rem;
		margin-bottom: 1rem;
	}
	h1 {
		margin: 0;
		font-size: 1.5rem;
	}
	h2 {
		margin: 0 0 0.75rem;
		font-size: 1rem;
	}
	.count {
		font-size: 0.875rem;
		color: var(--text-muted);
	}

	.card {
		padding: 1.25rem;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
	}
	.form-card {
		margin-bottom: 1.25rem;
	}
	.mode-tabs {
		display: flex;
		gap: 0.25rem;
		margin-bottom: 1rem;
		border-bottom: 1px solid var(--border);
	}
	.tab {
		padding: 0.45rem 0.9rem;
		font-size: 0.8125rem;
		font-weight: 600;
		color: var(--text-muted);
		background: none;
		border: none;
		border-bottom: 2px solid transparent;
		cursor: pointer;
	}
	.tab:hover {
		color: var(--text);
	}
	.tab.active {
		color: var(--text);
		border-bottom-color: var(--accent, currentColor);
	}
	.form-grid {
		display: grid;
		grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
		gap: 0.75rem;
	}
	.form-grid label {
		display: flex;
		flex-direction: column;
		gap: 0.3rem;
		font-size: 0.8125rem;
		color: var(--text-muted);
	}
	.form-grid label.wide {
		grid-column: span 2;
	}

	.balances {
		margin-top: 1.1rem;
		padding-top: 0.9rem;
		border-top: 1px solid var(--border-light);
	}
	.balances-head {
		display: flex;
		align-items: baseline;
		justify-content: space-between;
		gap: 0.75rem;
		margin-bottom: 0.4rem;
	}
	.balances-title {
		font-size: 0.6875rem;
		font-weight: 700;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
	}
	.balances-meta {
		font-size: 0.75rem;
		color: var(--text-muted);
	}
	.balances-empty {
		font-size: 0.8125rem;
		color: var(--text-muted);
		padding: 0.35rem 0;
	}
	.balances-scroll {
		max-height: 14rem;
		overflow-y: auto;
	}
	.balance-row {
		cursor: pointer;
	}
	.balance-row:hover {
		background: var(--border-light);
	}
	.strong {
		font-weight: 600;
	}

	.deposit-address {
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		margin-top: 1rem;
	}
	.address-line {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		font-size: 0.875rem;
		word-break: break-all;
	}
	.recent-deposits {
		margin-top: 1.25rem;
	}
	.recent-deposits .meta {
		padding-left: 0;
	}

	.cross-flow {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.75rem;
		margin-top: 0.9rem;
	}
	.cross-badge {
		text-transform: uppercase;
		letter-spacing: 0.04em;
	}
	.preview {
		font-size: 0.8125rem;
		padding: 0.5rem 0.75rem;
		border-radius: var(--radius-xs);
	}
	.preview.ok {
		background: color-mix(in srgb, var(--success) 12%, transparent);
		color: var(--text);
	}
	.preview.bad {
		background: color-mix(in srgb, var(--danger) 12%, transparent);
		color: var(--text);
	}
	.confirm {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.8125rem;
	}

	.form-actions {
		display: flex;
		align-items: center;
		gap: 0.75rem;
		margin-top: 1rem;
	}
	.form-msg {
		font-size: 0.8125rem;
		color: var(--text-muted);
	}
	.form-msg.error {
		color: var(--danger);
	}
	.field-hint {
		display: block;
		margin-top: 0.25rem;
		font-size: 0.75rem;
		color: var(--text-muted);
	}
	.field-hint.error {
		color: var(--danger);
	}

	.notice {
		padding: 2rem;
		text-align: center;
		color: var(--text-muted);
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
	}

	.table-wrap {
		overflow-x: auto;
		background: var(--surface);
		border: 1px solid var(--border);
		border-radius: var(--radius);
		box-shadow: var(--inset-hi);
	}
	table {
		width: 100%;
		border-collapse: collapse;
		font-size: 0.8125rem;
	}
	th,
	td {
		padding: 0.6rem 0.85rem;
		text-align: left;
		white-space: nowrap;
	}
	th {
		font-weight: 700;
		font-size: 0.6875rem;
		text-transform: uppercase;
		letter-spacing: 0.05em;
		color: var(--text-dim);
		border-bottom: 1px solid var(--border);
	}
	tbody tr {
		border-bottom: 1px solid var(--border-light);
	}
	tbody tr:last-child {
		border-bottom: none;
	}
	.row {
		cursor: pointer;
	}
	.row:hover {
		background: var(--border-light);
	}
	.chevron {
		width: 1rem;
		color: var(--text-dim);
	}
	.right {
		text-align: right;
	}
	.mono {
		font-family: var(--mono);
		font-variant-numeric: tabular-nums;
	}
	.muted {
		color: var(--text-muted);
	}
	.actions {
		text-align: right;
	}

	.detail-row td {
		background: var(--border-light);
		white-space: normal;
	}
	.steps {
		display: flex;
		flex-direction: column;
		gap: 0.4rem;
		padding: 0.35rem 0.25rem;
	}
	.step {
		display: flex;
		flex-wrap: wrap;
		align-items: center;
		gap: 0.5rem;
		font-size: 0.8125rem;
	}
	.step-dot {
		width: 0.55rem;
		height: 0.55rem;
		border-radius: 50%;
		background: var(--text-dim);
	}
	.step.succeeded .step-dot {
		background: var(--success);
	}
	.step.failed .step-dot {
		background: var(--danger);
	}
	.step.running .step-dot {
		background: var(--warning, #e2a300);
	}
	.step-name {
		font-weight: 600;
		min-width: 9.5rem;
	}
	.step-status {
		color: var(--text-muted);
		min-width: 5.5rem;
	}
	.step-detail {
		color: var(--text-muted);
		font-family: var(--mono);
		font-size: 0.75rem;
	}
	.step-error {
		color: var(--danger);
		font-size: 0.75rem;
	}
	.meta {
		font-size: 0.75rem;
		color: var(--text-muted);
		padding-left: 1.05rem;
	}
	.meta.error {
		color: var(--danger);
	}
</style>
