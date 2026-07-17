import { apiGet, apiPost } from './api';

export interface TransferStepRow {
	seq: number;
	name: string;
	status: string; // pending | running | succeeded | failed
	detail: Record<string, unknown> | null;
	error: string | null;
	started_at: string | null;
	finished_at: string | null;
}

export interface TransferRow {
	id: number;
	kind: string; // intra | cross | withdrawal
	status: string; // in_progress | completed | failed
	stuck: boolean;
	asset: string;
	amount: string;
	from_account_id: number;
	to_account_id: number | null; // null: external withdrawal
	from_exchange: string;
	to_exchange: string;
	from_wallet: string | null;
	to_wallet: string | null;
	network: string | null;
	address: string | null;
	address_tag: string | null;
	native_transfer_id: string | null;
	native_withdrawal_id: string | null;
	tx_id: string | null;
	received_amount: string | null;
	fee: string | null;
	fee_asset: string | null;
	error: string | null;
	created_at: string;
	updated_at: string;
	completed_at: string | null;
	steps: TransferStepRow[];
}

export interface TransferPreview {
	ok: boolean;
	problems: string[];
	withdraw_min: string | null;
	expected_fee: string | null;
	deposit_enabled: boolean | null;
	deposit_via_master: boolean;
}

export interface TransferCreateBody {
	from_account_id: number;
	to_account_id: number;
	asset: string;
	amount: string;
	from_wallet?: string;
	to_wallet?: string;
	network?: string;
	confirm?: boolean;
}

export interface WithdrawPreview {
	ok: boolean;
	problems: string[];
	withdraw_min: string | null;
	expected_fee: string | null;
}

export interface WithdrawBody {
	from_account_id: number;
	asset: string;
	amount: string;
	address: string;
	tag?: string;
	network: string;
	confirm?: boolean;
}

export interface DepositAddress {
	asset: string;
	network: string;
	address: string;
	tag: string | null;
}

export interface DepositRow {
	native_id: string;
	asset: string;
	amount: string;
	status: string; // PENDING | CREDITED | FAILED
	network: string | null;
	tx_id: string | null;
	credited_at: string | null;
}

export interface AssetNetworkOption {
	network: string;
	withdraw_enabled: boolean;
	deposit_enabled: boolean;
	withdraw_min: string;
	withdraw_fee: string;
}

export interface AvailableNetworks {
	asset: string;
	networks: AssetNetworkOption[];
	suggested_network: string | null;
}

export const fetchTransfers = (params: Record<string, string> = {}) =>
	apiGet<TransferRow[]>('/transfers', params);

/** Venue-derived network options for withdraw / deposit / cross-venue moves. */
export const fetchAvailableNetworks = (params: {
	asset: string;
	from_account_id?: number;
	to_account_id?: number;
}) => {
	const query: Record<string, string> = { asset: params.asset };
	if (params.from_account_id != null) {
		query.from_account_id = String(params.from_account_id);
	}
	if (params.to_account_id != null) {
		query.to_account_id = String(params.to_account_id);
	}
	return apiGet<AvailableNetworks>('/transfers/networks', query);
};

export const fetchTransfer = (id: number) => apiGet<TransferRow>(`/transfers/${id}`);

/** Validate a cross-exchange transfer without moving anything. */
export const previewTransfer = (body: Omit<TransferCreateBody, 'confirm'>) =>
	apiPost<TransferPreview>('/transfers/preview', body);

export const createTransfer = (body: TransferCreateBody) =>
	apiPost<TransferRow>('/transfers', body);

/** Source-side validation of an external withdrawal (nothing moves). */
export const previewWithdrawal = (body: Omit<WithdrawBody, 'confirm' | 'address' | 'tag'>) =>
	apiPost<WithdrawPreview>('/transfers/withdraw/preview', body);

/** Withdraw to an external address, tracked like any transfer. */
export const createWithdrawal = (body: WithdrawBody) =>
	apiPost<TransferRow>('/transfers/withdraw', body);

/** Resolve an account's deposit address, fetched fresh from the venue. */
export const fetchDepositAddress = (account_id: number, asset: string, network: string) =>
	apiGet<DepositAddress>('/transfers/deposit-address', {
		account_id: String(account_id),
		asset,
		network
	});

/** An account's recent deposits, straight from the venue. */
export const fetchRecentDeposits = (account_id: number, asset?: string) =>
	apiGet<DepositRow[]>('/transfers/deposits', {
		account_id: String(account_id),
		...(asset ? { asset } : {})
	});

/** Retry a stuck transfer after verifying the venue-side outcome. */
export const retryTransfer = (id: number) => apiPost<TransferRow>(`/transfers/${id}/retry`);

/** Mark a transfer failed in the panel (the venue side is not touched). */
export const failTransfer = (id: number, reason: string) =>
	apiPost<TransferRow>(`/transfers/${id}/fail`, { reason });
