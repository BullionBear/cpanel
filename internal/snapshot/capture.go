package snapshot

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/mirror"
)

const (
	// JobKind is the snapshot job. The worker registers Fetch for it when it has
	// the sealbox key.
	JobKind = "snapshot"

	// StaleAfter is how late a snapshot job may start and still write the hour
	// (C6-D004). A later start finishes the job and leaves the hour as a gap.
	StaleAfter = 15 * time.Minute

	// WalletUM is the single USD-M futures wallet (C6-D005).
	WalletUM = "um"

	// NormVersion is the normalizer version stored on ledger detail rows.
	NormVersion = 1
)

// Balance is one wallet row of a batch. Amounts are decimal strings.
type Balance struct {
	Kind, Wallet, Asset                                        string
	Balance, AvailableBalance, CrossWalletBalance, MaxWithdraw string
}

// AddBalance writes one balance row at NormVersion.
func AddBalance(ctx context.Context, tx pgx.Tx, batchID int64, b Balance) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO ledger.balance_snapshot (
			batch_id, kind, wallet, asset, balance, available_balance,
			cross_wallet_balance, max_withdraw_amount, norm_version)
		VALUES ($1, $2, $3, $4, $5::numeric, $6::numeric, $7::numeric, $8::numeric, $9)`,
		batchID, b.Kind, b.Wallet, b.Asset,
		b.Balance, b.AvailableBalance, b.CrossWalletBalance, b.MaxWithdraw, NormVersion)
	return err
}

// Position is one open position. A zero position_amt is rejected by the table.
// The exchange sends no category; AddPosition takes it from the mirror.
type Position struct {
	Kind, Venue, ExchTicker, PositionSide                                string
	PositionAmt, EntryPrice, MarkPrice, UnrealizedProfit                 string
	IsolatedMargin, InitialMargin, PositionInitialMargin, IsolatedWallet string
}

// AddPosition looks up the category and UID by (venue, exch_ticker) (C6-D007). A ticker
// the mirror does not have is stored with both null.
func AddPosition(ctx context.Context, tx pgx.Tx, batchID int64, p Position) error {
	category, uid, ok, err := mirror.LookupTicker(ctx, tx, p.Venue, p.ExchTicker)
	if err != nil {
		return err
	}
	var storedCategory, storedUID any
	if ok {
		storedCategory, storedUID = category, int64(uid)
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO ledger.position_snapshot (
			batch_id, kind, venue, category, exch_ticker, uid, position_side,
			position_amt, entry_price, mark_price, unrealized_profit,
			isolated_margin, initial_margin, position_initial_margin, isolated_wallet,
			norm_version)
		VALUES ($1, $2, $3, $4, $5, $6, $7,
		        $8::numeric, $9::numeric, $10::numeric, $11::numeric,
		        $12::numeric, $13::numeric, $14::numeric, $15::numeric, $16)`,
		batchID, p.Kind, p.Venue, storedCategory, p.ExchTicker, storedUID, p.PositionSide,
		p.PositionAmt, p.EntryPrice, p.MarkPrice, p.UnrealizedProfit,
		p.IsolatedMargin, p.InitialMargin, p.PositionInitialMargin, p.IsolatedWallet,
		NormVersion)
	return err
}
