import { apiDelete, apiGet, apiPatch, apiPost, apiPut } from './api';

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
	/** The venue's wallet structure, in display order. */
	wallets: string[];
	subaccount_list: boolean;
	subaccount_create: boolean;
	subaccount_freeze: boolean;
	api_key_create: boolean;
	api_key_modify: boolean;
	api_key_revoke: boolean;
	transfers: boolean;
	withdrawals: boolean;
	deposits: boolean;
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

// Accounts are addressed by their natural key (exchange + nickname) and
// credentials by their api_key, so URLs read as the resource they name.
const enc = encodeURIComponent;
const accountPath = (exchange: string, label: string) =>
	`/accounts/${enc(exchange)}/${enc(label)}`;

/** Change an account's nickname (system-only label). */
export const renameAccount = (exchange: string, label: string, newLabel: string) =>
	apiPatch<AccountInfo>(accountPath(exchange, label), { label: newLabel });

/** Register an existing venue-made API key against an account. */
export const addCredential = (
	exchange: string,
	label: string,
	body: { scheme?: string; api_key: string; secret: string; tags?: string[]; label?: string }
) => apiPost<Credential>(`${accountPath(exchange, label)}/credentials`, body);

export const revealSecret = (exchange: string, label: string, apiKey: string) =>
	apiPost<RevealedSecret>(`${accountPath(exchange, label)}/credentials/${enc(apiKey)}/reveal`);

export const setTags = (exchange: string, label: string, apiKey: string, tags: string[]) =>
	apiPut<Credential>(`${accountPath(exchange, label)}/credentials/${enc(apiKey)}/tags`, { tags });

/** Revoke a key on the venue (where supported) and remove it from the panel. */
export const revokeCredential = (exchange: string, label: string, apiKey: string) =>
	apiPost<void>(`${accountPath(exchange, label)}/credentials/${enc(apiKey)}/revoke`);

/** Remove a stored key from the panel without touching the venue. */
export const deleteCredential = (exchange: string, label: string, apiKey: string) =>
	apiDelete<void>(`${accountPath(exchange, label)}/credentials/${enc(apiKey)}`);

export const modifyCredential = (
	exchange: string,
	label: string,
	apiKey: string,
	body: { permissions?: string[]; read_only?: boolean }
) => apiPost<Credential>(`${accountPath(exchange, label)}/credentials/${enc(apiKey)}/modify`, body);

export const syncSubaccounts = (exchange: string, label: string) =>
	apiPost<AccountInfo[]>(`${accountPath(exchange, label)}/subaccounts/sync`);

export const createSubaccount = (exchange: string, label: string, subLabel: string) =>
	apiPost<AccountInfo>(`${accountPath(exchange, label)}/subaccounts`, { label: subLabel });

export const freezeSubaccount = (
	exchange: string,
	label: string,
	subLabel: string,
	frozen: boolean
) => apiPost<AccountInfo>(`${accountPath(exchange, label)}/subaccounts/${enc(subLabel)}/freeze`, { frozen });

export interface SubKeyOptions {
	sub_label: string;
	permissions: string[];
	read_only?: boolean;
	ips?: string[];
	note?: string;
	tags?: string[];
}

export const createSubApiKey = (exchange: string, label: string, body: SubKeyOptions) =>
	apiPost<{ credential: Credential; secret: string }>(
		`${accountPath(exchange, label)}/api-keys`,
		body
	);
