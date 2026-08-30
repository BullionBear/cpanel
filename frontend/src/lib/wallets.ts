/** Display labels for normalized WalletType values. */
const WALLET_LABELS: Record<string, string> = {
	SPOT: 'Spot',
	FUNDING: 'Funding',
	UMARGIN: 'USD-M',
	CMARGIN: 'Coin-M',
	UNIFIED: 'Unified'
};

export function walletLabel(wallet: string | null | undefined): string {
	if (!wallet) return '—';
	return WALLET_LABELS[wallet] ?? wallet;
}
