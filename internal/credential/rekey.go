package credential

import (
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
	"github.com/BullionBear/cpanel/internal/worker"
)

// Rekey copies credentials still sealed to Previous onto Current. Each row is its own
// transaction. An empty ciphertext, which is a deleted credential, is left alone.
// The account revision is not moved, and the audit written when the last old row is
// gone carries only a count and the two fingerprints.
type Rekey struct {
	DB       *pgxpool.Pool
	Current  *open.PrivateKey
	Previous *open.PrivateKey

	// stopAfter, when set, returns after that many rows have been resealed, as if the
	// process stopped between rows. Tests use it. Production leaves it 0.
	stopAfter int
}

// Handle is the worker handler for reconcile.CredentialRekey. It does not call an exchange.
func (k Rekey) Handle(ctx context.Context, job *worker.Job) (worker.Result, error) {
	if k.Current == nil {
		return worker.Result{}, errors.New("credential.rekey: no private key")
	}
	if k.Previous == nil {
		if err := CheckRecipients(ctx, k.DB, k.Current, nil); err != nil {
			return worker.Result{}, err
		}
		return worker.Result{}, nil
	}
	ids, err := k.oldIDs(ctx)
	if err != nil {
		return worker.Result{}, err
	}
	var resealed int
	halted := false
	for _, id := range ids {
		if k.halt(ctx, job, resealed) {
			halted = true
			break
		}
		wrote, err := k.reseal(ctx, id)
		if err != nil {
			return worker.Result{Items: resealed}, err
		}
		if wrote {
			resealed++
		}
	}
	if halted {
		return worker.Result{Items: resealed, More: true}, nil
	}
	if err := k.finishAudit(ctx, resealed); err != nil {
		return worker.Result{Items: resealed}, err
	}
	return worker.Result{Items: resealed}, nil
}

// halt reports that this run should commit what it has and leave the rest for the next one.
func (k Rekey) halt(ctx context.Context, job *worker.Job, done int) bool {
	if k.stopAfter > 0 && done >= k.stopAfter {
		return true
	}
	if ctx.Err() != nil {
		return true
	}
	// A handler called without the worker's budget, as the tests do, has no deadline.
	// Continue needs the lease the worker sets alongside that deadline.
	if job == nil || job.Budget.Deadline.IsZero() {
		return false
	}
	return !job.Continue(0)
}

func (k Rekey) oldIDs(ctx context.Context) ([]int64, error) {
	rows, err := k.DB.Query(ctx, `
		SELECT id FROM pms.credential
		WHERE octet_length(ciphertext) > 0
		  AND substring(ciphertext from 1 for 9) = $1
		ORDER BY id`, sealbox.RecipientHeader(k.Previous.Public()))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var ids []int64
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

// reseal opens one row with the previous key and writes it sealed to the current key.
// A row that is already on the current key, or whose ciphertext was cleared, is skipped.
func (k Rekey) reseal(ctx context.Context, id int64) (bool, error) {
	var wrote bool
	err := pgx.BeginFunc(ctx, k.DB, func(tx pgx.Tx) error {
		var accountID int64
		var ciphertext []byte
		err := tx.QueryRow(ctx, `
			SELECT exchange_account_id, ciphertext
			FROM pms.credential WHERE id = $1 FOR UPDATE`, id).Scan(&accountID, &ciphertext)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if len(ciphertext) == 0 || sealbox.SealedTo(k.Current.Public(), ciphertext) {
			return nil
		}
		if !sealbox.SealedTo(k.Previous.Public(), ciphertext) {
			return errors.New("credential.rekey: ciphertext is sealed to a key this process does not have")
		}
		cred, err := open.OpenCredentials(k.Previous, ciphertext, accountID, id)
		if err != nil {
			return err
		}
		sealed, err := sealbox.SealCredentials(k.Current.Public(), accountID, id, cred)
		if err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `
			UPDATE pms.credential SET ciphertext = $2
			WHERE id = $1 AND ciphertext = $3`, id, sealed, ciphertext)
		if err != nil {
			return err
		}
		wrote = tag.RowsAffected() == 1
		return nil
	})
	return wrote, err
}

// finishAudit records one rotation-complete row once nothing is left on the previous key.
// A run that resealed nothing, or that stopped with rows remaining, writes nothing.
func (k Rekey) finishAudit(ctx context.Context, resealed int) error {
	if resealed == 0 || k.Previous == nil {
		return nil
	}
	var left int
	err := k.DB.QueryRow(ctx, `
		SELECT count(*) FROM pms.credential
		WHERE octet_length(ciphertext) > 0
		  AND substring(ciphertext from 1 for 9) = $1`,
		sealbox.RecipientHeader(k.Previous.Public())).Scan(&left)
	if err != nil || left != 0 {
		return err
	}
	oldFP := k.Previous.Public().Fingerprint()
	newFP := k.Current.Public().Fingerprint()
	params, err := json.Marshal(map[string]any{
		"resealed":        resealed,
		"old_fingerprint": hex.EncodeToString(oldFP[:]),
		"new_fingerprint": hex.EncodeToString(newFP[:]),
	})
	if err != nil {
		return err
	}
	_, err = k.DB.Exec(ctx, `
		INSERT INTO meta.audit (actor, action, target, params, result, request_id)
		VALUES ('job:credential.rekey', 'sealbox.rekey', 'sealbox', $1, '{"status":"ok"}', '')`, params)
	return err
}

// CheckRecipients refuses to start when a non-empty ciphertext is sealed to neither key.
// previous may be nil. The error includes how many rows are left.
func CheckRecipients(ctx context.Context, db *pgxpool.Pool, current, previous *open.PrivateKey) error {
	if current == nil {
		return errors.New("sealbox: no private key")
	}
	var prev []byte
	if previous != nil {
		prev = sealbox.RecipientHeader(previous.Public())
	}
	var n int
	err := db.QueryRow(ctx, `
		SELECT count(*) FROM pms.credential
		WHERE octet_length(ciphertext) > 0
		  AND substring(ciphertext from 1 for 9) IS DISTINCT FROM $1
		  AND ($2::bytea IS NULL OR substring(ciphertext from 1 for 9) IS DISTINCT FROM $2)`,
		sealbox.RecipientHeader(current.Public()), prev).Scan(&n)
	if err != nil {
		return err
	}
	if n == 0 {
		return nil
	}
	return fmt.Errorf("sealbox: %d credential(s) are still sealed to another key; set CPANEL_SEALBOX_PREVIOUS_PRIVATE_KEY until credential.rekey finishes", n)
}

func openRow(current, previous *open.PrivateKey, sealed []byte, accountID, credentialID int64) (sealbox.Credentials, error) {
	return open.OpenAny([]*open.PrivateKey{current, previous}, sealed, accountID, credentialID)
}
