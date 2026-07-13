import { apiGet, apiPost } from './api';

export interface Credential {
	id: number;
	scheme: string;
	api_key: string;
	label: string | null;
	permissions: Record<string, unknown> | null;
	read_only: boolean | null;
	can_withdraw: boolean | null;
	status: string;
	verified_at: string | null;
	created_at: string;
}

export interface AccountInfo {
	id: number;
	exchange: string;
	label: string;
	account_type: string;
	parent_id: number | null;
	native_uid: string | null;
	created_at: string;
	credentials: Credential[];
}

export interface AccountNode extends AccountInfo {
	children: AccountInfo[];
}

export interface ExchangeCapabilities {
	exchange: string;
	subaccount_list: boolean;
	subaccount_create: boolean;
	api_key_create: boolean;
	api_key_revoke: boolean;
}

export const fetchTree = () => apiGet<AccountNode[]>('/accounts/tree');

export const fetchCapabilities = () => apiGet<ExchangeCapabilities[]>('/accounts/exchanges');

export const createAccount = (body: {
	exchange: string;
	label: string;
	account_type: string;
	parent_id?: number | null;
	native_uid?: string | null;
}) => apiPost<AccountInfo>('/accounts', body);

export const addCredential = (
	accountId: number,
	body: { scheme: string; api_key: string; secret: string; label?: string }
) => apiPost<Credential>(`/accounts/${accountId}/credentials`, body);

export const revokeCredential = (accountId: number, credentialId: number) =>
	apiPost<void>(`/accounts/${accountId}/credentials/${credentialId}/revoke`);

export const syncSubaccounts = (accountId: number) =>
	apiPost<AccountInfo[]>(`/accounts/${accountId}/subaccounts/sync`);

export const createSubaccount = (accountId: number, label: string) =>
	apiPost<AccountInfo>(`/accounts/${accountId}/subaccounts`, { label });

export const createSubApiKey = (
	accountId: number,
	body: { sub_account_id: number; permissions: string[] }
) => apiPost<{ credential: Credential; secret: string }>(`/accounts/${accountId}/api-keys`, body);
