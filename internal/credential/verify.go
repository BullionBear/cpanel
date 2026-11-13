// Package credential runs credential.verify and credential.probe. It opens a sealed key
// and asks the exchange whether that key is read-only and belongs to the account.
// cpanel-api does not import this package: it cannot unseal and it does not call an exchange.
package credential

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/venue"
	"github.com/BullionBear/cpanel/internal/venue/binance"
	"github.com/BullionBear/cpanel/internal/venue/bybit"
	"github.com/BullionBear/cpanel/internal/venue/okx"
	"github.com/BullionBear/cpanel/internal/worker"
)

// maxReason is how much of a failure is stored. The api key and the secret are removed first.
const maxReason = 500

// Checker is the account-level and kind-level reads one exchange client provides.
type Checker interface {
	CheckAccount(context.Context) (venue.Check, error)
	ReadFamily(context.Context, string) error
	Calls() int
}

// Endpoints overrides the public hosts. Empty fields use the exchange defaults.
type Endpoints struct {
	BinanceSpot    string
	BinanceFutures string
	Bybit          string
	OKX            string
}

// Verify opens credentials with Key and checks them. Open, when set, replaces the
// exchange client; tests use it.
type Verify struct {
	DB        *pgxpool.Pool
	Key       *open.PrivateKey
	Endpoints Endpoints
	Open      func(exchange string, cred sealbox.Credentials) (Checker, error)
}

// Handle is the worker handler for reconcile.CredentialVerifyKind.
func (v Verify) Handle(ctx context.Context, job *worker.Job) (worker.Result, error) {
	var params struct {
		CredentialID int64 `json:"credential_id"`
	}
	if job == nil || json.Unmarshal(job.Params, &params) != nil || params.CredentialID <= 0 {
		return worker.Result{}, errors.New("credential.verify: credential_id is required")
	}
	row, err := v.load(ctx, params.CredentialID)
	if err != nil {
		return worker.Result{}, err
	}
	if row.deleted || row.status != "unverified" {
		return worker.Result{}, nil
	}
	cred, err := open.OpenCredentials(v.Key, row.ciphertext, row.accountID, row.id)
	if err != nil {
		if err := v.reject(ctx, row, "sealed credentials are unreadable", "", ""); err != nil {
			return worker.Result{}, err
		}
		return worker.Result{}, nil
	}
	client, err := v.checker(row.exchange, cred)
	if err != nil {
		return worker.Result{}, err
	}
	check, err := client.CheckAccount(ctx)
	if err != nil {
		return worker.Result{Requests: client.Calls()}, err
	}
	if !check.ReadOnly || check.UID != row.uid {
		reason := check.Reason
		if check.UID != row.uid {
			reason = fmt.Sprintf("exchange uid %q does not match the account", check.UID)
		}
		if reason == "" {
			reason = "api key is not read-only"
		}
		if err := v.reject(ctx, row, scrub(reason, cred), check.UID, check.Summary); err != nil {
			return worker.Result{Requests: client.Calls()}, err
		}
		return worker.Result{Requests: client.Calls()}, nil
	}
	var passed, failed []kindResult
	for _, k := range row.kinds {
		var kerr error
		if row.exchange == "binance" && k.family == "api" {
			kerr = nil
		} else {
			kerr = client.ReadFamily(ctx, k.family)
		}
		if kerr != nil {
			failed = append(failed, kindResult{kind: k.kind, err: scrub(kerr.Error(), cred)})
			continue
		}
		passed = append(passed, kindResult{kind: k.kind})
	}
	if err := v.activate(ctx, job, row, check, passed, failed); err != nil {
		return worker.Result{Requests: client.Calls()}, err
	}
	return worker.Result{Requests: client.Calls()}, nil
}

type kindRow struct {
	kind   string
	family string
}

type kindResult struct {
	kind string
	err  string
}

type credRow struct {
	id         int64
	accountID  int64
	exchange   string
	uid        string
	status     string
	ciphertext []byte
	deleted    bool
	kinds      []kindRow
}

func (v Verify) load(ctx context.Context, id int64) (credRow, error) {
	var row credRow
	err := v.DB.QueryRow(ctx, `
		SELECT c.id, c.exchange_account_id, a.exchange, a.exch_account_uid, c.status, c.ciphertext, a.deleted_at IS NOT NULL
		FROM pms.credential c
		JOIN pms.exchange_account a ON a.id = c.exchange_account_id
		WHERE c.id = $1`, id).Scan(&row.id, &row.accountID, &row.exchange, &row.uid, &row.status, &row.ciphertext, &row.deleted)
	if errors.Is(err, pgx.ErrNoRows) {
		return credRow{}, fmt.Errorf("credential.verify: credential %d not found", id)
	}
	if err != nil {
		return credRow{}, err
	}
	rows, err := v.DB.Query(ctx, `
		SELECT k.kind, ak.api_family
		FROM pms.exchange_account_kind k
		JOIN pms.account_kind ak ON ak.kind = k.kind
		WHERE k.exchange_account_id = $1 AND k.disabled_at IS NULL
		ORDER BY k.kind`, row.accountID)
	if err != nil {
		return credRow{}, err
	}
	defer rows.Close()
	for rows.Next() {
		var k kindRow
		if err := rows.Scan(&k.kind, &k.family); err != nil {
			return credRow{}, err
		}
		row.kinds = append(row.kinds, k)
	}
	if err := rows.Err(); err != nil {
		return credRow{}, err
	}
	return row, nil
}

func (v Verify) checker(exchange string, cred sealbox.Credentials) (Checker, error) {
	return openChecker(v.Endpoints, v.Open, exchange, cred)
}

func openChecker(endpoints Endpoints, override func(string, sealbox.Credentials) (Checker, error), exchange string, cred sealbox.Credentials) (Checker, error) {
	if override != nil {
		return override(exchange, cred)
	}
	switch exchange {
	case "binance":
		return &binance.Client{Spot: endpoints.BinanceSpot, Futures: endpoints.BinanceFutures, Key: cred.APIKey, Secret: cred.APISecret}, nil
	case "bybit":
		return &bybit.Client{Host: endpoints.Bybit, Key: cred.APIKey, Secret: cred.APISecret}, nil
	case "okx":
		return &okx.Client{Host: endpoints.OKX, Key: cred.APIKey, Secret: cred.APISecret, Passphrase: cred.Passphrase}, nil
	default:
		return nil, fmt.Errorf("credential: unknown exchange %s", exchange)
	}
}

func (v Verify) reject(ctx context.Context, row credRow, reason, uid, summary string) error {
	return pgx.BeginFunc(ctx, v.DB, func(tx pgx.Tx) error {
		tag, err := tx.Exec(ctx, `
			UPDATE pms.credential
			SET status = 'rejected', failure_reason = $2, reported_uid = NULLIF($3, ''), permission_summary = NULLIF($4, ''), verified_at = NULL
			WHERE id = $1 AND status = 'unverified'`,
			row.id, reason, uid, summary)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return nil
		}
		return nil
	})
}

func (v Verify) activate(ctx context.Context, job *worker.Job, row credRow, check venue.Check, passed, failed []kindResult) error {
	return pgx.BeginFunc(ctx, v.DB, func(tx pgx.Tx) error {
		var status string
		err := tx.QueryRow(ctx, `SELECT status FROM pms.credential WHERE id = $1 FOR UPDATE`, row.id).Scan(&status)
		if err != nil {
			return err
		}
		if status != "unverified" {
			return nil
		}
		var disabled *int64
		err = tx.QueryRow(ctx, `
			UPDATE pms.credential SET status = 'disabled'
			WHERE exchange_account_id = $1 AND status = 'active' AND id <> $2
			RETURNING id`, row.accountID, row.id).Scan(&disabled)
		if errors.Is(err, pgx.ErrNoRows) {
			disabled = nil
		} else if err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `
			UPDATE pms.credential
			SET status = 'active', verified_at = now(), failure_reason = NULL,
			    consecutive_auth_failures = 0, next_probe_at = NULL,
			    reported_uid = $2, permission_summary = NULLIF($3, '')
			WHERE id = $1 AND status = 'unverified'`,
			row.id, check.UID, check.Summary)
		if err != nil {
			return err
		}
		if tag.RowsAffected() != 1 {
			return errors.New("credential.verify: the credential was no longer unverified")
		}
		for _, k := range passed {
			if _, err := tx.Exec(ctx, `
				UPDATE pms.exchange_account_kind
				SET verified_at = now(), last_error = NULL
				WHERE exchange_account_id = $1 AND kind = $2 AND disabled_at IS NULL`,
				row.accountID, k.kind); err != nil {
				return err
			}
		}
		for _, k := range failed {
			if _, err := tx.Exec(ctx, `
				UPDATE pms.exchange_account_kind
				SET verified_at = NULL, last_error = $3
				WHERE exchange_account_id = $1 AND kind = $2 AND disabled_at IS NULL`,
				row.accountID, k.kind, k.err); err != nil {
				return err
			}
		}
		if _, err := tx.Exec(ctx, `
			UPDATE pms.exchange_account SET revision = revision + 1
			WHERE id = $1 AND deleted_at IS NULL`, row.accountID); err != nil {
			return err
		}
		params := map[string]any{"exchange_account_id": row.accountID, "credential_id": row.id}
		if disabled != nil {
			params["disabled_credential_id"] = *disabled
		}
		raw, err := json.Marshal(params)
		if err != nil {
			return err
		}
		jobID := int64(0)
		if job != nil {
			jobID = job.ID
		}
		_, err = tx.Exec(ctx, `
			INSERT INTO meta.audit (actor, action, target, params, result, request_id)
			VALUES ('job:credential.verify', 'credential.activate', $1, $2, '{"status":"active"}', $3)`,
			fmt.Sprintf("credential:%d", row.id), raw, fmt.Sprintf("job:%d", jobID))
		return err
	})
}

func scrub(s string, cred sealbox.Credentials) string {
	if cred.APIKey != "" {
		s = strings.ReplaceAll(s, cred.APIKey, "")
	}
	if cred.APISecret != "" {
		s = strings.ReplaceAll(s, cred.APISecret, "")
	}
	if cred.Passphrase != "" {
		s = strings.ReplaceAll(s, cred.Passphrase, "")
	}
	s = strings.TrimSpace(s)
	if len(s) > maxReason {
		s = s[:maxReason]
	}
	return s
}

// Kind is the job name the worker registers. It matches the row reconcile inserts.
const Kind = reconcile.CredentialVerifyKind
