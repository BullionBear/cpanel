package mirror

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/sym"
	"github.com/BullionBear/cpanel/internal/worker"
)

const (
	// SourceSym is mirror.sync_state.source for the whole SYM copy. It is updated only once
	// every table's loaded revision matches the remote revision.
	SourceSym = "sym"

	// maxInactive is how many deactivated rows one round asks SYM to finish (C4-D011).
	maxInactive = 100

	// maxScopeUIDs is how many scope UIDs missing from mirror.symbol one round asks SYM
	// about. Never-tried UIDs sort first, so a full table of misses cannot starve a new one
	// (C4-D012).
	maxScopeUIDs = 200
)

// Catalog is the part of the SYM client sym.sync uses.
type Catalog interface {
	Revisions(ctx context.Context) ([]sym.Table, error)
	List(ctx context.Context, venue, category string, offset uint32) (sym.Page, error)
	GetByUid(ctx context.Context, uid uint32, includeInactive bool) (sym.Instrument, error)
}

// SourceTable is mirror.sync_state.source for one (venue, category) table.
func SourceTable(venue, category string) string {
	return "sym:" + venue + ":" + category
}

// SymSync is the sym.sync handler. A nil client fails the job without calling out.
func SymSync(client Catalog) worker.Handler {
	return func(ctx context.Context, job *worker.Job) (worker.Result, error) {
		var res worker.Result
		if client == nil {
			return res, &classify.ClassifiedError{Class: classify.Unavailable, Detail: "SYM URL or token is not set"}
		}
		if err := job.CommitPage(ctx, func(tx pgx.Tx) error { return touchSource(ctx, tx, SourceSym) }, nil); err != nil {
			return res, err
		}
		if err := symBudget(ctx, job); err != nil {
			return res, err
		}
		tables, err := client.Revisions(ctx)
		if err != nil {
			return res, err
		}
		res.Requests++
		for _, table := range tables {
			if err := symBudget(ctx, job); err != nil {
				return res, err
			}
			var stored *int64
			err := job.CommitPage(ctx, func(tx pgx.Tx) error {
				var err error
				stored, err = noteRemote(ctx, tx, table)
				return err
			}, nil)
			if err != nil {
				return res, err
			}
			if stored == nil || *stored < table.Revision {
				if err := reloadTable(ctx, job, client, table, stored, &res); err != nil {
					return res, err
				}
				continue
			}
			if err := job.CommitPage(ctx, func(tx pgx.Tx) error { return markTableCurrent(ctx, tx, table) }, nil); err != nil {
				return res, err
			}
		}
		if err := fillInactive(ctx, job, client, &res); err != nil {
			return res, err
		}
		if err := fillScopeUIDs(ctx, job, client, &res); err != nil {
			return res, err
		}
		if err := job.CommitPage(ctx, func(tx pgx.Tx) error { return markSymCaughtUp(ctx, tx) }, nil); err != nil {
			return res, err
		}
		return res, nil
	}
}

func symBudget(ctx context.Context, job *worker.Job) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if !time.Now().Before(job.Budget.Deadline) {
		return &classify.ClassifiedError{Class: classify.Internal, Detail: "sym.sync stopped at the time budget"}
	}
	return nil
}

func touchSource(ctx context.Context, tx pgx.Tx, source string) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO mirror.sync_state (source, last_attempt_at)
		VALUES ($1, now())
		ON CONFLICT (source) DO UPDATE SET last_attempt_at = now()`, source)
	return err
}

// noteRemote records the revision SYM just reported and returns the revision already loaded.
// A missing row means the table has never been loaded.
func noteRemote(ctx context.Context, tx pgx.Tx, table sym.Table) (*int64, error) {
	source := SourceTable(table.Venue, table.Category)
	var stored *int64
	err := tx.QueryRow(ctx, `SELECT revision FROM mirror.sync_state WHERE source = $1`, source).Scan(&stored)
	if errors.Is(err, pgx.ErrNoRows) {
		stored = nil
	} else if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO mirror.sync_state (source, last_attempt_at, remote_revision)
		VALUES ($1, now(), $2)
		ON CONFLICT (source) DO UPDATE SET last_attempt_at = now(), remote_revision = EXCLUDED.remote_revision`,
		source, table.Revision)
	return stored, err
}

func reloadTable(ctx context.Context, job *worker.Job, client Catalog, table sym.Table, stored *int64, res *worker.Result) error {
	before := table.Revision
	var all []sym.Instrument
	var offset uint32
	for page := 0; ; page++ {
		if err := symBudget(ctx, job); err != nil {
			return err
		}
		if page >= maxPages {
			return &classify.ClassifiedError{Class: classify.Internal, Detail: "sym.sync passed the page limit of 200"}
		}
		got, err := client.List(ctx, table.Venue, table.Category, offset)
		if err != nil {
			return err
		}
		res.Requests++
		all = append(all, got.Instruments...)
		if len(got.Instruments) == 0 || got.Total == 0 {
			break
		}
		next := offset + uint32(len(got.Instruments))
		if next <= offset {
			return &classify.ClassifiedError{Class: classify.Internal, Detail: "sym.sync page offset wrapped"}
		}
		offset = next
		if offset >= got.Total {
			break
		}
	}
	if err := symBudget(ctx, job); err != nil {
		return err
	}
	again, err := client.Revisions(ctx)
	if err != nil {
		return err
	}
	res.Requests++
	after, found := revisionAfter(again, table.Venue, table.Category, before)
	// revision is the value seen before List. A table missing from the second
	// Revisions keeps whatever was already loaded, so a partial read is retried.
	revision := stored
	remote := before
	deactivate := false
	if found {
		revision = &before
		remote = after
		deactivate = after == before
	}
	return job.CommitPage(ctx, func(tx pgx.Tx) error {
		return writeTable(ctx, tx, table, revision, remote, deactivate, all)
	}, nil)
}

func revisionAfter(tables []sym.Table, venue, category string, before int64) (int64, bool) {
	for _, table := range tables {
		if table.Venue == venue && table.Category == category {
			return table.Revision, true
		}
	}
	return before, false
}

func writeTable(ctx context.Context, tx pgx.Tx, table sym.Table, revision *int64, remote int64, deactivate bool, rows []sym.Instrument) error {
	for i := range rows {
		rows[i].Active = true
		if err := upsertSymbol(ctx, tx, rows[i]); err != nil {
			return err
		}
	}
	// A revision that moved during the list is not a consistent snapshot, so rows missing
	// from it are not delistings (C4-D011).
	if deactivate {
		uids := make([]int64, len(rows))
		for i := range rows {
			uids[i] = int64(rows[i].UID)
		}
		if _, err := tx.Exec(ctx, `
			UPDATE mirror.symbol
			SET active = false, inactive_since = COALESCE(inactive_since, now())
			WHERE venue = $1 AND category = $2 AND active
			  AND status NOT IN ('EXPIRED', 'DELISTED')
			  AND NOT (uid = ANY($3::bigint[]))`, table.Venue, table.Category, uids); err != nil {
			return err
		}
	}
	var items int64
	if err := tx.QueryRow(ctx, `SELECT count(*) FROM mirror.symbol WHERE venue = $1 AND category = $2`,
		table.Venue, table.Category).Scan(&items); err != nil {
		return err
	}
	_, err := tx.Exec(ctx, `
		INSERT INTO mirror.sync_state (source, last_attempt_at, last_ok_at, last_complete_at, items, revision, remote_revision)
		VALUES ($1, now(), now(), CASE WHEN $2::bool THEN now() ELSE NULL END, $3, $4, $5)
		ON CONFLICT (source) DO UPDATE SET
			last_attempt_at = now(),
			last_ok_at = now(),
			last_complete_at = CASE WHEN $2::bool THEN now() ELSE mirror.sync_state.last_complete_at END,
			items = EXCLUDED.items,
			revision = COALESCE(EXCLUDED.revision, mirror.sync_state.revision),
			remote_revision = EXCLUDED.remote_revision`,
		SourceTable(table.Venue, table.Category), deactivate, items, revision, remote)
	return err
}

func markTableCurrent(ctx context.Context, tx pgx.Tx, table sym.Table) error {
	_, err := tx.Exec(ctx, `
		UPDATE mirror.sync_state SET
			last_attempt_at = now(),
			last_ok_at = now(),
			last_complete_at = now(),
			items = (SELECT count(*) FROM mirror.symbol WHERE venue = $2 AND category = $3),
			remote_revision = $4
		WHERE source = $1`,
		SourceTable(table.Venue, table.Category), table.Venue, table.Category, table.Revision)
	return err
}

func fillInactive(ctx context.Context, job *worker.Job, client Catalog, res *worker.Result) error {
	var uids []int64
	err := job.CommitPage(ctx, func(tx pgx.Tx) error {
		rows, err := tx.Query(ctx, `
			SELECT uid FROM mirror.symbol
			WHERE active = false AND status NOT IN ('EXPIRED', 'DELISTED')
			ORDER BY inactive_since NULLS FIRST, uid
			LIMIT $1`, maxInactive)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var uid int64
			if err := rows.Scan(&uid); err != nil {
				return err
			}
			uids = append(uids, uid)
		}
		return rows.Err()
	}, nil)
	if err != nil {
		return err
	}
	for _, uid := range uids {
		if err := symBudget(ctx, job); err != nil {
			return err
		}
		inst, err := client.GetByUid(ctx, uint32(uid), true)
		res.Requests++
		if classify.Is(err, classify.NotFound) {
			continue
		}
		if err != nil {
			return err
		}
		inst.Active = false
		if inst.UID == 0 {
			inst.UID = uint32(uid)
		}
		if err := job.CommitPage(ctx, func(tx pgx.Tx) error { return upsertSymbol(ctx, tx, inst) }, nil); err != nil {
			return err
		}
		res.Items++
	}
	return nil
}

func markSymCaughtUp(ctx context.Context, tx pgx.Tx) error {
	_, err := tx.Exec(ctx, `
		UPDATE mirror.sync_state SET
			last_ok_at = now(),
			last_complete_at = now(),
			items = (SELECT count(*) FROM mirror.symbol)
		WHERE source = $1
		  AND NOT EXISTS (
			SELECT 1 FROM mirror.sync_state
			WHERE source LIKE 'sym:%'
			  AND (revision IS NULL OR remote_revision IS NULL OR revision <> remote_revision)
		  )`, SourceSym)
	return err
}

// LookupUID resolves an exchange spelling to a UID, the same way SYM's Reverse does:
// an active row wins; otherwise the newest sym_updated_at, then the largest uid.
func LookupUID(ctx context.Context, tx pgx.Tx, venue, category, exchTicker string) (uint32, bool, error) {
	var uid int64
	err := tx.QueryRow(ctx, `
		SELECT uid FROM mirror.symbol
		WHERE venue = $1 AND category = $2 AND exch_ticker = $3
		ORDER BY active DESC, sym_updated_at DESC NULLS LAST, uid DESC
		LIMIT 1`, venue, category, exchTicker).Scan(&uid)
	if errors.Is(err, pgx.ErrNoRows) {
		return 0, false, nil
	}
	if err != nil {
		return 0, false, err
	}
	return uint32(uid), true, nil
}

func upsertSymbol(ctx context.Context, tx pgx.Tx, inst sym.Instrument) error {
	filters, err := json.Marshal(inst.Filters)
	if err != nil {
		return err
	}
	if inst.Filters == nil {
		filters = []byte(`{}`)
	}
	var updated *time.Time
	if !inst.UpdatedAt.IsZero() {
		t := inst.UpdatedAt.UTC()
		updated = &t
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO mirror.symbol (
			uid, venue, category, exch_ticker, universal_ticker, base, quote, settle,
			contract_size, contract_value_ccy, price_ccy, expiry, strike, option_type,
			status, active, filters, sym_updated_at, first_seen_at, last_seen_at, inactive_since)
		VALUES (
			$1, $2, $3, $4, $5, $6, $7, $8,
			$9::numeric, $10, $11, $12, $13::numeric, $14,
			$15, $16, $17::jsonb, $18, now(), now(),
			CASE WHEN $16 THEN NULL ELSE now() END)
		ON CONFLICT (uid) DO UPDATE SET
			venue = CASE
				WHEN mirror.symbol.status IN ('EXPIRED', 'DELISTED') THEN mirror.symbol.venue
				WHEN EXCLUDED.venue = '' THEN mirror.symbol.venue
				ELSE EXCLUDED.venue END,
			category = CASE
				WHEN mirror.symbol.status IN ('EXPIRED', 'DELISTED') THEN mirror.symbol.category
				WHEN EXCLUDED.category = '' THEN mirror.symbol.category
				ELSE EXCLUDED.category END,
			exch_ticker = EXCLUDED.exch_ticker,
			universal_ticker = EXCLUDED.universal_ticker,
			base = EXCLUDED.base,
			quote = EXCLUDED.quote,
			settle = EXCLUDED.settle,
			contract_size = EXCLUDED.contract_size,
			contract_value_ccy = EXCLUDED.contract_value_ccy,
			price_ccy = EXCLUDED.price_ccy,
			expiry = EXCLUDED.expiry,
			strike = EXCLUDED.strike,
			option_type = EXCLUDED.option_type,
			filters = EXCLUDED.filters,
			sym_updated_at = EXCLUDED.sym_updated_at,
			status = CASE
				WHEN mirror.symbol.status IN ('EXPIRED', 'DELISTED') THEN mirror.symbol.status
				ELSE EXCLUDED.status END,
			active = CASE
				WHEN mirror.symbol.status IN ('EXPIRED', 'DELISTED') THEN false
				ELSE EXCLUDED.active END,
			inactive_since = CASE
				WHEN mirror.symbol.status IN ('EXPIRED', 'DELISTED') THEN COALESCE(mirror.symbol.inactive_since, now())
				WHEN EXCLUDED.active THEN NULL
				ELSE COALESCE(mirror.symbol.inactive_since, now()) END,
			last_seen_at = now()`,
		int64(inst.UID), inst.Venue, inst.Category, inst.ExchTicker, inst.UniversalTicker, inst.Base, inst.Quote,
		textOrNil(inst.Settle), numeric(inst.ContractSize), textOrNil(inst.ContractValueCcy), textOrNil(inst.PriceCcy),
		inst.Expiry, numeric(inst.Strike), textOrNil(inst.OptionType), inst.Status, inst.Active, filters, updated)
	return err
}

func numeric(v *float64) *string {
	if v == nil {
		return nil
	}
	s := strconv.FormatFloat(*v, 'f', -1, 64)
	return &s
}

func textOrNil(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}
