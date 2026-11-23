package snapshot

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/mirror"
	"github.com/BullionBear/cpanel/internal/worker"
)

const (
	// JobKind is the snapshot job. Production does not register a handler until
	// the exchange fetch exists; tests register Capture.
	JobKind = "snapshot"

	// StaleAfter is how late a snapshot job may start and still write the hour
	// (C6-D004). A later start finishes the job and leaves the hour as a gap.
	StaleAfter = 15 * time.Minute

	// WalletUM is the single USD-M futures wallet (C6-D005).
	WalletUM = "um"

	// NormVersion is the normalizer version stored on ledger detail rows.
	NormVersion = 1
)

// Capture writes an empty snapshot batch for a job that is still inside StaleAfter.
// It does not call an exchange. Enabled kinds in Kinds are marked succeeded; other
// enabled kinds are marked unimplemented. An empty account still gets the batch.
type Capture struct {
	Kinds []string
	Now   func() time.Time
}

type snapshotParams struct {
	ExchangeAccountID int64  `json:"exchange_account_id"`
	Bucket            string `json:"bucket"`
}

// Handle implements worker.Handler.
func (c Capture) Handle(ctx context.Context, job *worker.Job) (worker.Result, error) {
	var p snapshotParams
	if err := json.Unmarshal(job.Params, &p); err != nil {
		return worker.Result{}, err
	}
	bucket, err := time.Parse(time.RFC3339, p.Bucket)
	if err != nil || p.ExchangeAccountID <= 0 {
		return worker.Result{}, fmt.Errorf("snapshot: job %d has no account or bucket", job.ID)
	}
	now := time.Now().UTC()
	if c.Now != nil {
		now = c.Now().UTC()
	}
	if now.After(bucket.Add(StaleAfter)) {
		return worker.Result{}, nil
	}
	kinds := c.Kinds
	if kinds == nil {
		kinds = []string{}
	}
	err = job.CommitPage(ctx, func(tx pgx.Tx) error {
		var id int64
		if err := tx.QueryRow(ctx, `
			INSERT INTO ledger.snapshot_batch (exchange_account_id, bucket, captured_at)
			VALUES ($1, $2, $3)
			RETURNING id`, p.ExchangeAccountID, bucket.UTC(), now).Scan(&id); err != nil {
			return err
		}
		_, err := tx.Exec(ctx, `
			INSERT INTO ledger.snapshot_batch_kind (batch_id, kind, status)
			SELECT $1, k.kind,
			       CASE WHEN k.kind = ANY($2::text[]) THEN 'succeeded' ELSE 'unimplemented' END
			FROM pms.exchange_account_kind k
			WHERE k.exchange_account_id = $3 AND k.disabled_at IS NULL
			ORDER BY k.kind`, id, kinds, p.ExchangeAccountID)
		return err
	}, nil)
	if err != nil {
		return worker.Result{}, err
	}
	return worker.Result{Items: 1}, nil
}

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
type Position struct {
	Kind, Venue, Category, ExchTicker, PositionSide                      string
	PositionAmt, EntryPrice, MarkPrice, UnrealizedProfit                 string
	IsolatedMargin, InitialMargin, PositionInitialMargin, IsolatedWallet string
}

// AddPosition looks up the UID and keeps exch_ticker when the mirror has no row.
func AddPosition(ctx context.Context, tx pgx.Tx, batchID int64, p Position) error {
	uid, ok, err := mirror.LookupUID(ctx, tx, p.Venue, p.Category, p.ExchTicker)
	if err != nil {
		return err
	}
	var stored any
	if ok {
		stored = int64(uid)
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
		batchID, p.Kind, p.Venue, p.Category, p.ExchTicker, stored, p.PositionSide,
		p.PositionAmt, p.EntryPrice, p.MarkPrice, p.UnrealizedProfit,
		p.IsolatedMargin, p.InitialMargin, p.PositionInitialMargin, p.IsolatedWallet,
		NormVersion)
	return err
}
