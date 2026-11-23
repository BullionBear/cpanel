package history

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
)

// Kind is one history stream. The rebuild command takes these names.
type Kind string

const (
	KindTrade   Kind = "trade"
	KindOrder   Kind = "order"
	KindFunding Kind = "funding"
)

// Raw is one immutable exchange response. NativeID is the venue's dedup key
// (C7-D003): symbol:id, symbol:orderId, or incomeType:tranId.
type Raw struct {
	ExchangeAccountID int64
	Venue             string
	NativeID          string
	Params            json.RawMessage
	Payload           json.RawMessage
	FetchedAt         time.Time
}

// Trade is one ledger.trade row. Amounts are decimal strings. Version is the
// normalizer's, not a field the normalizer fills in.
type Trade struct {
	UID             int64
	OrderNativeID   string
	Price           string
	Qty             string
	QuoteQty        string
	Side            string
	PositionSide    string
	TradedAt        time.Time
	Commission      string
	CommissionAsset string
	RealizedPnl     string
}

// Order is one ledger.order row. A nil cloid means the client order id did not
// decode. ExecutedQty must be greater than zero (D8).
type Order struct {
	UID           int64
	ClientOrderID *string
	CloidVer      *int
	Algo          *string
	CloidTsMs     *int64
	ExecutedQty   string
}

// Funding is one ledger.funding row. UID is nil when the income has no symbol.
type Funding struct {
	UID      *int64
	Asset    string
	Income   string
	FundedAt time.Time
}

// Result is the ledger row for one raw row. Skip writes nothing (an order that
// is not finished, for example). Otherwise the pointer for the normalizer's
// kind is set.
type Result struct {
	Skip    bool
	Trade   *Trade
	Order   *Order
	Funding *Funding
}

// Normalizer turns one raw row into at most one ledger row. One normalizer is
// one (venue, kind) at one version. It does not call an exchange.
type Normalizer interface {
	Venue() string
	Kind() Kind
	Version() int
	Normalize(ctx context.Context, tx pgx.Tx, raw Raw) (Result, error)
}

// Beginner starts the transaction Rebuild uses. *pgxpool.Pool and *pgx.Conn both do.
type Beginner interface {
	Begin(ctx context.Context) (pgx.Tx, error)
}

var (
	regMu sync.Mutex
	regs  = map[string]Normalizer{}
)

func regKey(venue string, kind Kind) string { return venue + "\x00" + string(kind) }

// Register makes n the normalizer rebuild uses for its venue and kind.
// The production worker registers none until a venue normalizer exists.
func Register(n Normalizer) {
	regMu.Lock()
	defer regMu.Unlock()
	regs[regKey(n.Venue(), n.Kind())] = n
}

// Unregister removes a normalizer. Tests use it to leave the registry empty.
func Unregister(venue string, kind Kind) {
	regMu.Lock()
	defer regMu.Unlock()
	delete(regs, regKey(venue, kind))
}

// Lookup returns the registered normalizer.
func Lookup(venue string, kind Kind) (Normalizer, bool) {
	regMu.Lock()
	defer regMu.Unlock()
	n, ok := regs[regKey(venue, kind)]
	return n, ok
}

// Apply writes one raw row and its ledger row in the caller's transaction.
// The caller moves the stream cursor in that same transaction (C3-D004).
// Apply itself does not touch the cursor.
func Apply(ctx context.Context, tx pgx.Tx, n Normalizer, raw Raw) error {
	if err := checkRaw(n, raw); err != nil {
		return err
	}
	params := raw.Params
	if len(params) == 0 {
		params = []byte(`{}`)
	}
	table, err := rawTable(n.Kind())
	if err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, fmt.Sprintf(`
		INSERT INTO %s (exchange_account_id, venue, native_id, params, payload, fetched_at)
		VALUES ($1, $2, $3, $4, $5, $6)`, table),
		raw.ExchangeAccountID, raw.Venue, raw.NativeID, params, []byte(raw.Payload), raw.FetchedAt.UTC()); err != nil {
		return err
	}
	res, err := n.Normalize(ctx, tx, raw)
	if err != nil {
		return err
	}
	if res.Skip {
		return nil
	}
	return insertLedger(ctx, tx, n, raw, res)
}

// Rebuild deletes the ledger rows of one account, venue and kind, and writes
// them again from raw with n. It does not change raw or any stream cursor.
func Rebuild(ctx context.Context, db Beginner, n Normalizer, accountID int64) error {
	if n == nil {
		return errors.New("history: no normalizer")
	}
	if accountID <= 0 {
		return errors.New("history: account id is required")
	}
	if n.Version() < 1 {
		return fmt.Errorf("history: normalizer version %d", n.Version())
	}
	tx, err := db.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()
	table, err := ledgerTable(n.Kind())
	if err != nil {
		return err
	}
	rawName, err := rawTable(n.Kind())
	if err != nil {
		return err
	}
	if _, err := tx.Exec(ctx, fmt.Sprintf(`
		DELETE FROM %s WHERE exchange_account_id = $1 AND venue = $2`, table),
		accountID, n.Venue()); err != nil {
		return err
	}
	rows, err := tx.Query(ctx, fmt.Sprintf(`
		SELECT exchange_account_id, venue, native_id, params, payload, fetched_at
		FROM %s
		WHERE exchange_account_id = $1 AND venue = $2
		ORDER BY native_id`, rawName), accountID, n.Venue())
	if err != nil {
		return err
	}
	raws, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (Raw, error) {
		var raw Raw
		err := row.Scan(&raw.ExchangeAccountID, &raw.Venue, &raw.NativeID, &raw.Params, &raw.Payload, &raw.FetchedAt)
		return raw, err
	})
	if err != nil {
		return err
	}
	for _, raw := range raws {
		res, err := n.Normalize(ctx, tx, raw)
		if err != nil {
			return err
		}
		if res.Skip {
			continue
		}
		if err := insertLedger(ctx, tx, n, raw, res); err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func checkRaw(n Normalizer, raw Raw) error {
	if n == nil {
		return errors.New("history: no normalizer")
	}
	if n.Version() < 1 {
		return fmt.Errorf("history: normalizer version %d", n.Version())
	}
	if raw.ExchangeAccountID <= 0 || raw.Venue == "" || raw.NativeID == "" || raw.FetchedAt.IsZero() {
		return errors.New("history: raw row needs an account, venue, native id and fetched_at")
	}
	if raw.Venue != n.Venue() {
		return fmt.Errorf("history: raw venue %s, normalizer venue %s", raw.Venue, n.Venue())
	}
	if len(raw.Payload) == 0 {
		return errors.New("history: raw payload is empty")
	}
	return nil
}

func rawTable(kind Kind) (string, error) {
	switch kind {
	case KindTrade:
		return "raw.venue_trade", nil
	case KindOrder:
		return "raw.venue_order", nil
	case KindFunding:
		return "raw.venue_income", nil
	default:
		return "", fmt.Errorf("history: unknown kind %q", kind)
	}
}

func ledgerTable(kind Kind) (string, error) {
	switch kind {
	case KindTrade:
		return "ledger.trade", nil
	case KindOrder:
		return "ledger.order", nil
	case KindFunding:
		return "ledger.funding", nil
	default:
		return "", fmt.Errorf("history: unknown kind %q", kind)
	}
}

func insertLedger(ctx context.Context, tx pgx.Tx, n Normalizer, raw Raw, res Result) error {
	switch n.Kind() {
	case KindTrade:
		if res.Trade == nil {
			return errors.New("history: trade normalizer returned no row")
		}
		t := res.Trade
		_, err := tx.Exec(ctx, `
			INSERT INTO ledger.trade (
				exchange_account_id, venue, native_id, uid, order_native_id,
				price, qty, quote_qty, side, position_side, traded_at,
				commission, commission_asset, realized_pnl, norm_version)
			VALUES ($1, $2, $3, $4, $5, $6::numeric, $7::numeric, $8::numeric, $9, $10, $11,
			        $12::numeric, $13, $14::numeric, $15)`,
			raw.ExchangeAccountID, raw.Venue, raw.NativeID, t.UID, t.OrderNativeID,
			t.Price, t.Qty, t.QuoteQty, t.Side, t.PositionSide, t.TradedAt.UTC(),
			t.Commission, t.CommissionAsset, t.RealizedPnl, n.Version())
		return err
	case KindOrder:
		if res.Order == nil {
			return errors.New("history: order normalizer returned no row")
		}
		o := res.Order
		_, err := tx.Exec(ctx, `
			INSERT INTO ledger.order (
				exchange_account_id, venue, native_id, uid, client_order_id,
				cloid_ver, algo, cloid_ts_ms, executed_qty, norm_version)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::numeric, $10)`,
			raw.ExchangeAccountID, raw.Venue, raw.NativeID, o.UID, o.ClientOrderID,
			o.CloidVer, o.Algo, o.CloidTsMs, o.ExecutedQty, n.Version())
		return err
	case KindFunding:
		if res.Funding == nil {
			return errors.New("history: funding normalizer returned no row")
		}
		f := res.Funding
		_, err := tx.Exec(ctx, `
			INSERT INTO ledger.funding (
				exchange_account_id, venue, native_id, uid, asset, income, funded_at, norm_version)
			VALUES ($1, $2, $3, $4, $5, $6::numeric, $7, $8)`,
			raw.ExchangeAccountID, raw.Venue, raw.NativeID, f.UID, f.Asset, f.Income, f.FundedAt.UTC(), n.Version())
		return err
	default:
		return fmt.Errorf("history: unknown kind %q", n.Kind())
	}
}
