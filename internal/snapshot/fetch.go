package snapshot

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/platform/exchangeenv"
	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/snapshotkind"
	"github.com/BullionBear/cpanel/internal/venue/binance"
	"github.com/BullionBear/cpanel/internal/worker"
)

// Fetch is the snapshot handler (C6-03). For an account that still has an active
// read credential it calls the snapshot endpoints of each enabled kind in
// snapshotkind.Implemented, and writes the raw responses, the batch, each enabled
// kind's result and the ledger rows in one transaction. Other enabled kinds are
// recorded as unimplemented. An empty account still gets the batch.
//
// A failed call writes nothing (C6-D006): the job ends with the call's class and
// the hour is tried again by the next insert. A job that starts after StaleAfter
// calls nothing (C6-D004).
type Fetch struct {
	DB       *pgxpool.Pool
	Key      *open.PrivateKey
	Previous *open.PrivateKey // the key being replaced; a box it opens is still read
	Env      string           // the worker's exchange environment; a credential of another is refused
	// BinanceFutures replaces the fapi host of Env. Tests point it at httptest.
	BinanceFutures string
	Now            func() time.Time
}

type snapshotParams struct {
	ExchangeAccountID int64  `json:"exchange_account_id"`
	Bucket            string `json:"bucket"`
}

type account struct {
	credentialID int64
	ciphertext   []byte
	env          string
	kinds        []string // enabled kinds, sorted
}

// rawResponse is one row of raw.venue_snapshot.
type rawResponse struct {
	venue, path string
	body        []byte
	fetchedAt   time.Time
}

type capture struct {
	raw  []rawResponse
	rows Rows
}

// Handle implements worker.Handler.
func (f Fetch) Handle(ctx context.Context, job *worker.Job) (worker.Result, error) {
	var p snapshotParams
	if job == nil || json.Unmarshal(job.Params, &p) != nil {
		return worker.Result{}, errors.New("snapshot: params are not a snapshot job")
	}
	bucket, err := time.Parse(time.RFC3339, p.Bucket)
	if err != nil || p.ExchangeAccountID <= 0 {
		return worker.Result{}, fmt.Errorf("snapshot: job %d has no account or bucket", job.ID)
	}
	bucket = bucket.UTC()
	if f.now().After(bucket.Add(StaleAfter)) {
		return worker.Result{}, nil
	}
	acct, ok, err := f.load(ctx, p.ExchangeAccountID)
	if err != nil || !ok {
		return worker.Result{}, err
	}
	var capturable []string
	for _, kind := range acct.kinds {
		if slices.Contains(snapshotkind.Implemented(), kind) {
			capturable = append(capturable, kind)
		}
	}
	if len(capturable) == 0 {
		return worker.Result{}, nil
	}
	if !exchangeenv.Same(f.Env, acct.env) {
		return worker.Result{}, fmt.Errorf("snapshot: credential environment is %s; this worker is %s",
			exchangeenv.Normalize(acct.env), exchangeenv.Normalize(f.Env))
	}
	cred, err := open.OpenAny([]*open.PrivateKey{f.Key, f.Previous}, acct.ciphertext, p.ExchangeAccountID, acct.credentialID)
	if err != nil {
		return worker.Result{}, fmt.Errorf("snapshot: credential %d: sealed credentials are unreadable", acct.credentialID)
	}

	captures := map[string]capture{}
	requests := 0
	for _, kind := range capturable {
		c, n, err := f.fetch(ctx, kind, cred)
		requests += n
		if err != nil {
			return worker.Result{Requests: requests}, scrubError(err, cred)
		}
		captures[kind] = c
	}

	items := 0
	err = job.CommitPage(ctx, func(tx pgx.Tx) error {
		var batchID int64
		err := tx.QueryRow(ctx, `
			INSERT INTO ledger.snapshot_batch (exchange_account_id, bucket, captured_at)
			VALUES ($1, $2, $3)
			ON CONFLICT (exchange_account_id, bucket) DO NOTHING
			RETURNING id`, p.ExchangeAccountID, bucket, f.now()).Scan(&batchID)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil // the hour already has a batch
		}
		if err != nil {
			return err
		}
		items = 1
		for _, kind := range acct.kinds {
			status := "unimplemented"
			c, ok := captures[kind]
			if ok {
				status = "succeeded"
			}
			if _, err := tx.Exec(ctx, `
				INSERT INTO ledger.snapshot_batch_kind (batch_id, kind, status) VALUES ($1, $2, $3)`,
				batchID, kind, status); err != nil {
				return err
			}
			if !ok {
				continue
			}
			for _, r := range c.raw {
				params, err := json.Marshal(map[string]string{"path": r.path})
				if err != nil {
					return err
				}
				if _, err := tx.Exec(ctx, `
					INSERT INTO raw.venue_snapshot (exchange_account_id, venue, kind, bucket, params, payload, fetched_at)
					VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)`,
					p.ExchangeAccountID, r.venue, kind, bucket, string(params), string(r.body), r.fetchedAt); err != nil {
					return err
				}
			}
			for _, b := range c.rows.Balances {
				if err := AddBalance(ctx, tx, batchID, b); err != nil {
					return err
				}
			}
			for _, pos := range c.rows.Positions {
				if err := AddPosition(ctx, tx, batchID, pos); err != nil {
					return err
				}
			}
			items += len(c.rows.Balances) + len(c.rows.Positions)
		}
		return nil
	}, nil)
	if err != nil {
		return worker.Result{Requests: requests}, err
	}
	return worker.Result{Requests: requests, Items: items}, nil
}

// fetch calls the snapshot endpoints of kind and normalizes them. It returns the number
// of requests sent, also on error.
func (f Fetch) fetch(ctx context.Context, kind string, cred sealbox.Credentials) (capture, int, error) {
	switch kind {
	case snapshotkind.BinanceUM:
		client := &binance.Client{
			Futures: f.BinanceFutures,
			Key:     cred.APIKey, Secret: cred.APISecret, KeyType: cred.KeyType,
			Env: exchangeenv.Normalize(f.Env),
		}
		_, balanceBody, err := client.UMBalance(ctx)
		if err != nil {
			return capture{}, client.Calls(), err
		}
		balanceAt := f.now()
		_, positionBody, err := client.UMPositionRisk(ctx)
		if err != nil {
			return capture{}, client.Calls(), err
		}
		positionAt := f.now()
		rows, err := NormalizeUMRaw(balanceBody, positionBody)
		if err != nil {
			return capture{}, client.Calls(), err
		}
		return capture{
			raw: []rawResponse{
				{venue: VenueBinanceUM, path: binance.UMBalancePath, body: balanceBody, fetchedAt: balanceAt},
				{venue: VenueBinanceUM, path: binance.UMPositionRiskPath, body: positionBody, fetchedAt: positionAt},
			},
			rows: rows,
		}, client.Calls(), nil
	default:
		return capture{}, 0, fmt.Errorf("snapshot: no fetch for kind %s", kind)
	}
}

// load reads the account's active read credential and enabled kinds. ok is false when
// the account is deleted or has no active credential: the job ends without a batch.
func (f Fetch) load(ctx context.Context, accountID int64) (account, bool, error) {
	var a account
	err := f.DB.QueryRow(ctx, `
		SELECT c.id, c.ciphertext, c.exchange_env,
		       ARRAY(
		           SELECT k.kind FROM pms.exchange_account_kind k
		           WHERE k.exchange_account_id = x.id AND k.disabled_at IS NULL
		           ORDER BY k.kind)
		FROM pms.exchange_account x
		JOIN pms.credential c
		  ON c.exchange_account_id = x.id
		 AND c.purpose = 'read'
		 AND c.status = 'active'
		 AND octet_length(c.ciphertext) > 0
		WHERE x.id = $1 AND x.deleted_at IS NULL`, accountID).Scan(&a.credentialID, &a.ciphertext, &a.env, &a.kinds)
	if errors.Is(err, pgx.ErrNoRows) {
		return account{}, false, nil
	}
	if err != nil {
		return account{}, false, err
	}
	return a, true, nil
}

func (f Fetch) now() time.Time {
	if f.Now != nil {
		return f.Now().UTC()
	}
	return time.Now().UTC()
}

// scrubError removes the api key and secret from an error before the worker stores it.
// The class, status and Retry-After are kept.
func scrubError(err error, cred sealbox.Credentials) error {
	var ce *classify.ClassifiedError
	if errors.As(err, &ce) {
		out := *ce
		out.Detail = scrub(ce.Detail, cred)
		return &out
	}
	return errors.New(scrub(err.Error(), cred))
}

func scrub(s string, cred sealbox.Credentials) string {
	for _, secret := range []string{cred.APIKey, cred.APISecret, cred.Passphrase} {
		if secret != "" {
			s = strings.ReplaceAll(s, secret, "")
		}
	}
	return s
}
