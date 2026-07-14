import { apiGet, apiPost, apiPut } from './api';

export interface Credential {
	id: number;
	scheme: string;
	api_key: string;
	label: string | null;
	permissions: Record<string, unknown> | null;
	read_only: boolean | null;
	can_withdraw: boolean | null;
	tags: string[];
	status: string;
	verified_at: string | null;
	created_at: string;
}

export interface AccountInfo {
	id: number;
	exchange: string;
	label: string; // system-only nickname
	account_type: string;
	parent_id: number | null;
	native_uid: string | null;
	status: string;
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
	subaccount_freeze: boolean;
	api_key_create: boolean;
	api_key_modify: boolean;
	api_key_revoke: boolean;
	registration_only: boolean;
}

export interface RegisterAccountBody {
	exchange: string;
	label: string;
	account_type?: string;
	parent_id?: number | null;
	scheme?: string;
	api_key: string;
	secret: string;
	tags?: string[];
	cred_label?: string;
}

export interface RevealedSecret {
	api_key: string;
	secret: string;
}

export const fetchTree = () => apiGet<AccountNode[]>('/accounts/tree');

export const fetchCapabilities = () => apiGet<ExchangeCapabilities[]>('/accounts/exchanges');

/** Combined account + API key registration (the universal flow). */
export const registerAccount = (body: RegisterAccountBody) =>
	apiPost<AccountInfo>('/accounts/register', body);

export const revealSecret = (accountId: number, credentialId: number) =>
	apiPost<RevealedSecret>(`/accounts/${accountId}/credentials/${credentialId}/reveal`);

export const setTags = (accountId: number, credentialId: number, tags: string[]) =>
	apiPut<Credential>(`/accounts/${accountId}/credentials/${credentialId}/tags`, { tags });

export const revokeCredential = (accountId: number, credentialId: number) =>
	apiPost<void>(`/accounts/${accountId}/credentials/${credentialId}/revoke`);

export const modifyCredential = (
	accountId: number,
	credentialId: number,
	body: { permissions?: string[]; read_only?: boolean }
) => apiPost<Credential>(`/accounts/${accountId}/credentials/${credentialId}/modify`, body);

export const syncSubaccounts = (accountId: number) =>
	apiPost<AccountInfo[]>(`/accounts/${accountId}/subaccounts/sync`);

export const createSubaccount = (accountId: number, label: string) =>
	apiPost<AccountInfo>(`/accounts/${accountId}/subaccounts`, { label });

export const freezeSubaccount = (accountId: number, subId: number, frozen: boolean) =>
	apiPost<AccountInfo>(`/accounts/${accountId}/subaccounts/${subId}/freeze`, { frozen });

export const createSubApiKey = (
	accountId: number,
	body: { sub_account_id: number; permissions: string[]; tags?: string[] }
) => apiPost<{ credential: Credential; secret: string }>(`/accounts/${accountId}/api-keys`, body);
