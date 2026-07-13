<script lang="ts">
	import { onMount } from 'svelte';
	import {
		getAccountsV2,
		getSymbols,
		getMyTradesDb,
		syncMyTrades,
		type AccountV2,
		type SymbolInfo,
		type TradeItemV2,
		type SyncResponse
	} from '$lib/api';

	let activeTab = 'trading';

	let accounts: AccountV2[] = [];
	let symbols: SymbolInfo[] = [];
	let acctMap: Record<number, AccountV2> = {};
	let exchIdToName: Record<number, string> = {};
	let symbolByName: Record<string, SymbolInfo> = {};

	let trades: TradeItemV2[] = [];
	let loading = false;
	let initLoading = true;
	let error: string | null = null;
	let expandedIdx: number | null = null;

	const PAGE_SIZE = 50;
	let page = 0;
	$: totalPages = Math.max(1, Math.ceil(trades.length / PAGE_SIZE));
	$: pagedTrades = trades.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);

	let syncing = false;
	let syncResult: SyncResponse | null = null;

	let selectedAcctIds: Set<number> = new Set();
	let acctDropdownOpen = false;
	let filterSide = 'all';
	let filterStartDate = '';
	let filterEndDate = '';

	onMount(() => {
		document.addEventListener('click', closeAcctDropdown);

		(async () => {
			try {
				const [accts, syms] = await Promise.all([
					getAccountsV2(),
					getSymbols(500).catch(() => [] as SymbolInfo[])
				]);
				accounts = accts;
				symbols = syms;
				acctMap = {};
				for (const a of accounts) {
					acctMap[a.id] = a;
					exchIdToName[a.exch_id] = a.exchange;
				}
				symbolByName = {};
				for (const s of symbols) {
					const key = `${s.exchange.id}:${s.name}`;
					symbolByName[key] = s;
				}
			} catch (e) {
				error = e instanceof Error ? e.message : 'Failed to load data';
			} finally {
				initLoading = false;
			}
		})();

		return () => document.removeEventListener('click', closeAcctDropdown);
	});

	function resolveExchange(exchId: number): string {
		return exchIdToName[exchId] ?? `#${exchId}`;
	}

	function resolveAccount(acctId: number): string {
		return acctMap[acctId]?.name ?? `#${acctId}`;
	}

	function resolveTicker(trade: TradeItemV2): string {
		const key = `${trade.exch_id}:${trade.symbol}`;
		return symbolByName[key]?.universal_ticker ?? trade.symbol;
	}

	function trimDecimal(v: string): string {
		if (!v.includes('.')) return v;
		return v.replace(/\.?0+$/, '');
	}

	function shortId(v: number | string): string {
		const raw = String(v);
		const digits = raw.replace(/\D/g, '');
		const suffix = digits.slice(-8);
		if (!suffix) return raw;
		const trimmed = suffix.replace(/^0+/, '');
		return trimmed || '0';
	}

	function formatTime(iso: string): string {
		const d = new Date(iso);
		const pad = (n: number) => String(n).padStart(2, '0');
		return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
	}

	function toggleAcct(id: number) {
		if (selectedAcctIds.has(id)) {
			selectedAcctIds.delete(id);
		} else {
			selectedAcctIds.add(id);
		}
		selectedAcctIds = new Set(selectedAcctIds);
	}

	function acctFilterLabel(): string {
		if (selectedAcctIds.size === 0) return 'All Accounts';
		if (selectedAcctIds.size === 1) {
			const id = [...selectedAcctIds][0];
			return acctMap[id]?.name ?? `#${id}`;
		}
		return `${selectedAcctIds.size} accounts`;
	}

	function closeAcctDropdown(e: MouseEvent) {
		const t = e.target as HTMLElement;
		if (t && !t.closest('.acct-multi')) acctDropdownOpen = false;
	}

	async function search() {
		loading = true;
		error = null;
		expandedIdx = null;
		acctDropdownOpen = false;
		page = 0;
		try {
			const params: Record<string, any> = {};
			if (selectedAcctIds.size > 0) params.acct_id = [...selectedAcctIds].join(',');
			if (filterSide !== 'all') params.side = filterSide;
			if (filterStartDate) params.start_ms = new Date(filterStartDate).getTime();
			if (filterEndDate) params.end_ms = new Date(filterEndDate).getTime();
			params.limit = 1000;
			trades = await getMyTradesDb(params);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Failed to fetch trades';
			trades = [];
		} finally {
			loading = false;
		}
	}

	function toggleDetail(idx: number) {
		expandedIdx = expandedIdx === idx ? null : idx;
	}

	function exportCsv() {
		if (trades.length === 0) return;
		const headers = ['Time', 'Exchange', 'TID', 'OID', 'Account', 'Ticker', 'Side', 'Qty', 'Price', 'Fee', 'Fee Currency', 'Maker', 'Product ID', 'Symbol'];
		const esc = (v: string) => (v.includes(',') || v.includes('"') ? `"${v.replace(/"/g, '""')}"` : v);
		const rows = trades.map((t) => [
			formatTime(t.exec_time),
			resolveExchange(t.exch_id),
			String(t.exch_tid),
			String(t.order_id),
			resolveAccount(t.acct_id),
			resolveTicker(t),
			t.side,
			trimDecimal(t.exec_qty),
			trimDecimal(t.exec_px),
			trimDecimal(t.exec_fee),
			t.fee_currency,
			t.is_maker ? 'Yes' : 'No',
			String(t.product_id),
			t.symbol
		].map(esc).join(','));
		const csv = [headers.join(','), ...rows].join('\n');
		const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
		const url = URL.createObjectURL(blob);
		const a = document.createElement('a');
		a.href = url;
		a.download = `trading_history_${new Date().toISOString().slice(0, 10)}.csv`;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		URL.revokeObjectURL(url);
	}

	async function handleSync() {
		syncing = true;
		syncResult = null;
		error = null;
		try {
			syncResult = await syncMyTrades();
			if (syncResult.total_inserted > 0) {
				await search();
			}
		} catch (e) {
			error = e instanceof Error ? e.message : 'Sync failed';
		} finally {
			syncing = false;
		}
	}
</script>

<div class="history-page">
	<div class="page-header">
		<span class="header-icon">
			<svg width="24" height="24" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
				<path d="M12 8V12L15 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				<circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/>
			</svg>
		</span>
		<h1>History</h1>
	</div>

	<div class="tabs">
		<button class="tab" class:active={activeTab === 'trading'} on:click={() => (activeTab = 'trading')}>
			Trading History
		</button>
		<button class="tab" class:active={activeTab === 'order'} on:click={() => (activeTab = 'order')}>
			Order History
		</button>
	</div>

	<div class="tab-content">
		{#if activeTab === 'trading'}
			{#if initLoading}
				<div class="center-msg">Loading...</div>
			{:else}
				<div class="filters">
					<div class="filter-group acct-multi">
						<span class="filter-label">Account</span>
						<button
							type="button"
							class="multi-select-trigger"
							on:click|stopPropagation={() => (acctDropdownOpen = !acctDropdownOpen)}
						>
							<span class="multi-select-text">{acctFilterLabel()}</span>
							<svg width="12" height="12" viewBox="0 0 24 24" fill="none"><path d="M6 9L12 15L18 9" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
						</button>
						{#if acctDropdownOpen}
							<!-- svelte-ignore a11y-click-events-have-key-events a11y-no-static-element-interactions -->
							<div class="multi-select-dropdown" on:click|stopPropagation>
								{#each accounts as acct (acct.id)}
									<label class="multi-option">
										<input
											type="checkbox"
											checked={selectedAcctIds.has(acct.id)}
											on:change={() => toggleAcct(acct.id)}
										/>
										<span>{acct.name} <span class="multi-option-hint">({acct.exchange})</span></span>
									</label>
								{/each}
								{#if accounts.length === 0}
									<div class="multi-option-empty">No accounts</div>
								{/if}
							</div>
						{/if}
					</div>
					<div class="filter-group">
						<label for="f-side">Side</label>
						<select id="f-side" bind:value={filterSide}>
							<option value="all">All</option>
							<option value="buy">Buy</option>
							<option value="sell">Sell</option>
						</select>
					</div>
					<div class="filter-group">
						<label for="f-start">Start</label>
						<input id="f-start" type="datetime-local" bind:value={filterStartDate} />
					</div>
					<div class="filter-group">
						<label for="f-end">End</label>
						<input id="f-end" type="datetime-local" bind:value={filterEndDate} />
					</div>
					<button class="search-btn" on:click={search} disabled={loading}>
						{loading ? 'Searching...' : 'Search'}
					</button>
					<button class="sync-btn" on:click={handleSync} disabled={syncing || loading}>
						<svg width="14" height="14" viewBox="0 0 24 24" fill="none">
							<path d="M23 4V10H17" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
							<path d="M1 20V14H7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
							<path d="M3.51 9A9 9 0 0 1 20.49 5.64L23 10M1 14L3.51 18.36A9 9 0 0 0 20.49 15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
						</svg>
						{syncing ? 'Syncing...' : 'Sync'}
					</button>
				</div>

				{#if syncResult}
					<div class="sync-result">
						Synced {syncResult.total_inserted} trade{syncResult.total_inserted === 1 ? '' : 's'} from {syncResult.total_cursors} cursor{syncResult.total_cursors === 1 ? '' : 's'}.
						{#if syncResult.results.some((r) => r.error)}
							<span class="sync-warn">({syncResult.results.filter((r) => r.error).length} error{syncResult.results.filter((r) => r.error).length === 1 ? '' : 's'})</span>
						{/if}
						<button class="sync-dismiss" on:click={() => (syncResult = null)}>&times;</button>
					</div>
				{/if}

				{#if error}
					<div class="error-msg">{error}</div>
				{/if}

				{#if trades.length > 0}
					<div class="table-wrap">
						<table class="trades-table">
							<thead>
								<tr>
									<th>Time</th>
									<th>Exchange</th>
									<th>TID</th>
									<th>OID</th>
									<th>Account</th>
									<th>Ticker</th>
									<th>Side</th>
									<th class="num">Qty</th>
									<th class="num">Price</th>
									<th></th>
								</tr>
							</thead>
							<tbody>
								{#each pagedTrades as trade, idx (idx)}
									<tr class="trade-row" class:expanded={expandedIdx === idx}>
										<td class="mono">{formatTime(trade.exec_time)}</td>
										<td>{resolveExchange(trade.exch_id)}</td>
										<td class="mono">{shortId(trade.exch_tid)}</td>
										<td class="mono">{shortId(trade.order_id)}</td>
										<td>{resolveAccount(trade.acct_id)}</td>
										<td class="ticker">{resolveTicker(trade)}</td>
										<td>
											<span class="side-badge" class:buy={trade.side === 'Buy'} class:sell={trade.side === 'Sell'}>
												{trade.side}
											</span>
										</td>
										<td class="num mono">{trimDecimal(trade.exec_qty)}</td>
										<td class="num mono">{trimDecimal(trade.exec_px)}</td>
										<td>
											<button class="detail-btn" on:click={() => toggleDetail(idx)}>
												{expandedIdx === idx ? 'Hide' : 'Detail'}
											</button>
										</td>
									</tr>
									{#if expandedIdx === idx}
										<tr class="detail-row">
											<td colspan="10">
												<div class="detail-grid">
													<div class="detail-item">
														<span class="detail-label">Fee</span>
														<span class="detail-value">{trimDecimal(trade.exec_fee)} {trade.fee_currency}</span>
													</div>
													<div class="detail-item">
														<span class="detail-label">Maker</span>
														<span class="detail-value">{trade.is_maker ? 'Yes' : 'No'}</span>
													</div>
													<div class="detail-item">
														<span class="detail-label">Product ID</span>
														<span class="detail-value">{trade.product_id}</span>
													</div>
													<div class="detail-item">
														<span class="detail-label">Symbol</span>
														<span class="detail-value">{trade.symbol}</span>
													</div>
													<div class="detail-item">
														<span class="detail-label">Exchange ID</span>
														<span class="detail-value">{trade.exch_id}</span>
													</div>
													<div class="detail-item">
														<span class="detail-label">Account ID</span>
														<span class="detail-value">{trade.acct_id}</span>
													</div>
												</div>
											</td>
										</tr>
									{/if}
								{/each}
							</tbody>
						</table>
					</div>
					<div class="result-footer">
						<span class="result-count">{trades.length} record{trades.length === 1 ? '' : 's'}</span>
						<div class="footer-actions">
							{#if totalPages > 1}
								<div class="pagination">
									<button class="page-btn" disabled={page === 0} on:click={() => { page = 0; expandedIdx = null; }}>&laquo;</button>
									<button class="page-btn" disabled={page === 0} on:click={() => { page--; expandedIdx = null; }}>&lsaquo;</button>
									<span class="page-info">{page + 1} / {totalPages}</span>
									<button class="page-btn" disabled={page >= totalPages - 1} on:click={() => { page++; expandedIdx = null; }}>&rsaquo;</button>
									<button class="page-btn" disabled={page >= totalPages - 1} on:click={() => { page = totalPages - 1; expandedIdx = null; }}>&raquo;</button>
								</div>
							{/if}
							<button class="export-btn" on:click={exportCsv}>
								<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M21 15V19C21 20.1046 20.1046 21 19 21H5C3.89543 21 3 20.1046 3 19V15" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M7 10L12 15L17 10" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M12 15V3" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
								Export CSV
							</button>
						</div>
					</div>
				{:else if !loading && !error}
					<div class="center-msg muted">No trades to display. Use the filters above and click Search.</div>
				{/if}
			{/if}
		{:else if activeTab === 'order'}
			<div class="coming-soon">
				<svg width="48" height="48" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
					<path d="M9 5H7C5.89543 5 5 5.89543 5 7V19C5 20.1046 5.89543 21 7 21H17C18.1046 21 19 20.1046 19 19V7C19 5.89543 18.1046 5 17 5H15" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
					<path d="M9 5C9 3.89543 9.89543 3 11 3H13C14.1046 3 15 3.89543 15 5C15 6.10457 14.1046 7 13 7H11C9.89543 7 9 6.10457 9 5Z" stroke="#9ca3af" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
				</svg>
				<h2>Order History</h2>
				<p>Coming soon.</p>
			</div>
		{/if}
	</div>
</div>

<style>
	.history-page {
		max-width: 1200px;
		margin: 0 auto;
		padding: 0 4px;
	}

	.page-header {
		display: flex;
		align-items: center;
		gap: 10px;
		margin-bottom: 24px;
	}

	.page-header h1 {
		font-size: 24px;
		font-weight: 700;
		color: #111827;
		margin: 0;
	}

	.header-icon {
		display: flex;
		color: #374151;
	}

	.tabs {
		display: flex;
		gap: 8px;
		border-bottom: 1px solid #e5e7eb;
		margin-bottom: 24px;
	}

	.tab {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 12px 20px;
		background: transparent;
		border: none;
		border-bottom: 2px solid transparent;
		font-size: 14px;
		font-weight: 500;
		color: #6b7280;
		cursor: pointer;
		transition: all 0.2s;
	}

	.tab:hover {
		color: #374151;
	}

	.tab.active {
		color: #667eea;
		border-bottom-color: #667eea;
	}

	.tab-content {
		background: white;
		border-radius: 12px;
		border: 1px solid #e5e7eb;
		padding: 24px;
	}

	/* Filters */
	.filters {
		display: flex;
		flex-wrap: wrap;
		gap: 12px;
		align-items: flex-end;
		margin-bottom: 20px;
		padding-bottom: 16px;
		border-bottom: 1px solid #f3f4f6;
	}

	.filter-group {
		display: flex;
		flex-direction: column;
		gap: 4px;
	}

	.filter-group label,
	.filter-label {
		font-size: 12px;
		font-weight: 500;
		color: #6b7280;
	}

	.filter-group select,
	.filter-group input {
		padding: 7px 10px;
		border: 1px solid #d1d5db;
		border-radius: 6px;
		font-size: 13px;
		background: white;
		color: #111827;
		min-width: 140px;
	}

	/* Multi-select account dropdown */
	.acct-multi {
		position: relative;
	}

	.multi-select-trigger {
		display: flex;
		align-items: center;
		justify-content: space-between;
		gap: 8px;
		padding: 7px 10px;
		border: 1px solid #d1d5db;
		border-radius: 6px;
		font-size: 13px;
		background: white;
		color: #111827;
		min-width: 180px;
		cursor: pointer;
		text-align: left;
	}

	.multi-select-trigger:hover {
		border-color: #9ca3af;
	}

	.multi-select-text {
		overflow: hidden;
		text-overflow: ellipsis;
		white-space: nowrap;
	}

	.multi-select-dropdown {
		position: absolute;
		top: 100%;
		left: 0;
		margin-top: 4px;
		background: white;
		border: 1px solid #e5e7eb;
		border-radius: 6px;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
		min-width: 220px;
		max-height: 240px;
		overflow-y: auto;
		z-index: 50;
		padding: 4px;
	}

	.multi-option {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 7px 10px;
		border-radius: 4px;
		font-size: 13px;
		color: #111827;
		cursor: pointer;
		transition: background 0.1s;
	}

	.multi-option:hover {
		background: #f3f4f6;
	}

	.multi-option input[type='checkbox'] {
		min-width: auto;
		width: 15px;
		height: 15px;
		accent-color: #667eea;
		cursor: pointer;
	}

	.multi-option-hint {
		color: #9ca3af;
		font-size: 12px;
	}

	.multi-option-empty {
		padding: 12px;
		text-align: center;
		color: #9ca3af;
		font-size: 13px;
	}

	.search-btn {
		padding: 8px 20px;
		background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
		color: white;
		border: none;
		border-radius: 6px;
		font-size: 13px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
		align-self: flex-end;
	}

	.search-btn:hover:not(:disabled) {
		transform: translateY(-1px);
		box-shadow: 0 4px 12px rgba(102, 126, 234, 0.4);
	}

	.search-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.sync-btn {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 8px 16px;
		background: white;
		color: #374151;
		border: 1px solid #d1d5db;
		border-radius: 6px;
		font-size: 13px;
		font-weight: 500;
		cursor: pointer;
		transition: all 0.2s;
		align-self: flex-end;
	}

	.sync-btn:hover:not(:disabled) {
		background: #f3f4f6;
		border-color: #9ca3af;
	}

	.sync-btn:disabled {
		opacity: 0.6;
		cursor: not-allowed;
	}

	.sync-result {
		display: flex;
		align-items: center;
		gap: 8px;
		padding: 10px 14px;
		background: #f0fdf4;
		border: 1px solid #bbf7d0;
		border-radius: 6px;
		color: #166534;
		font-size: 13px;
		margin-bottom: 12px;
	}

	.sync-warn {
		color: #b45309;
	}

	.sync-dismiss {
		margin-left: auto;
		background: none;
		border: none;
		font-size: 18px;
		color: #6b7280;
		cursor: pointer;
		padding: 0 4px;
		line-height: 1;
	}

	.sync-dismiss:hover {
		color: #111827;
	}

	/* Table */
	.table-wrap {
		overflow-x: auto;
	}

	.trades-table {
		width: 100%;
		border-collapse: collapse;
		font-size: 13px;
	}

	.trades-table th {
		text-align: left;
		padding: 10px 8px;
		font-weight: 600;
		color: #6b7280;
		font-size: 12px;
		text-transform: uppercase;
		letter-spacing: 0.03em;
		border-bottom: 2px solid #e5e7eb;
		white-space: nowrap;
	}

	.trades-table th.num {
		text-align: right;
	}

	.trades-table td {
		padding: 9px 8px;
		border-bottom: 1px solid #f3f4f6;
		color: #111827;
		white-space: nowrap;
	}

	.trades-table td.num {
		text-align: right;
	}

	.trade-row:hover {
		background: #f9fafb;
	}

	.trade-row.expanded {
		background: #f0f4ff;
	}

	.mono {
		font-family: 'Courier New', monospace;
		font-size: 12px;
	}

	.ticker {
		font-weight: 500;
		color: #4338ca;
	}

	/* Side badge */
	.side-badge {
		display: inline-block;
		padding: 2px 8px;
		border-radius: 4px;
		font-size: 12px;
		font-weight: 600;
	}

	.side-badge.buy {
		background: #dcfce7;
		color: #166534;
	}

	.side-badge.sell {
		background: #fef2f2;
		color: #dc2626;
	}

	/* Detail */
	.detail-btn {
		padding: 3px 10px;
		font-size: 12px;
		border: 1px solid #d1d5db;
		border-radius: 4px;
		background: white;
		color: #374151;
		cursor: pointer;
		transition: all 0.15s;
	}

	.detail-btn:hover {
		background: #f3f4f6;
		border-color: #9ca3af;
	}

	.detail-row td {
		padding: 0 8px 12px 8px;
		border-bottom: 1px solid #e5e7eb;
		background: #fafaff;
	}

	.detail-grid {
		display: grid;
		grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
		gap: 12px;
		padding: 12px 4px;
	}

	.detail-item {
		display: flex;
		flex-direction: column;
		gap: 2px;
	}

	.detail-label {
		font-size: 11px;
		font-weight: 500;
		color: #6b7280;
		text-transform: uppercase;
		letter-spacing: 0.03em;
	}

	.detail-value {
		font-size: 13px;
		color: #111827;
		font-family: 'Courier New', monospace;
	}

	.result-footer {
		display: flex;
		justify-content: space-between;
		align-items: center;
		margin-top: 12px;
	}

	.result-count {
		font-size: 13px;
		color: #6b7280;
	}

	.export-btn {
		display: flex;
		align-items: center;
		gap: 6px;
		padding: 6px 14px;
		font-size: 13px;
		font-weight: 500;
		color: #374151;
		background: white;
		border: 1px solid #d1d5db;
		border-radius: 6px;
		cursor: pointer;
		transition: all 0.15s;
	}

	.export-btn:hover {
		background: #f3f4f6;
		border-color: #9ca3af;
	}

	.footer-actions {
		display: flex;
		align-items: center;
		gap: 12px;
	}

	.pagination {
		display: flex;
		align-items: center;
		gap: 4px;
	}

	.page-btn {
		width: 30px;
		height: 30px;
		display: flex;
		align-items: center;
		justify-content: center;
		border: 1px solid #d1d5db;
		border-radius: 4px;
		background: white;
		color: #374151;
		font-size: 14px;
		cursor: pointer;
		transition: all 0.15s;
		padding: 0;
	}

	.page-btn:hover:not(:disabled) {
		background: #f3f4f6;
		border-color: #9ca3af;
	}

	.page-btn:disabled {
		opacity: 0.35;
		cursor: not-allowed;
	}

	.page-info {
		font-size: 13px;
		color: #6b7280;
		min-width: 60px;
		text-align: center;
	}

	/* Shared states */
	.center-msg {
		text-align: center;
		padding: 40px;
		color: #6b7280;
	}

	.center-msg.muted {
		color: #9ca3af;
	}

	.error-msg {
		padding: 10px 12px;
		background: #fef2f2;
		border: 1px solid #fecaca;
		border-radius: 6px;
		color: #dc2626;
		font-size: 14px;
		margin-bottom: 16px;
	}

	/* Coming soon */
	.coming-soon {
		text-align: center;
		padding: 60px 20px;
		color: #9ca3af;
	}

	.coming-soon h2 {
		margin: 16px 0 8px 0;
		font-size: 18px;
		color: #6b7280;
	}

	.coming-soon p {
		margin: 0;
		font-size: 14px;
	}

	@media (max-width: 768px) {
		.filters {
			flex-direction: column;
			align-items: stretch;
		}

		.filter-group select,
		.filter-group input {
			min-width: unset;
			width: 100%;
		}

		.search-btn {
			align-self: stretch;
		}
	}
</style>
