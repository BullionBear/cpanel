package reconcile

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// Persist projects mirror.scope into ingest.persist_symbol and upserts the streams that list calls for
// (C7-02). Rows are not deleted. since only moves earlier. The rule does not read credentials: an account
// with no active key still gets a list and streams, and C6-06 is what keeps those jobs from running.
//
// Cursor shape belongs to C7-03 and C7-05, so a stream insert leaves cursor null and a later round does not
// write it. Funding is one stream per account and venue, hourly; hot and cold apply to trade streams only.
var Persist = Rule{Name: "persist", Run: func(ctx context.Context, tx pgx.Tx, now time.Time) (int, error) {
	tag, err := tx.Exec(ctx, `
		INSERT INTO ingest.persist_symbol (exchange_account_id, venue, uid, source, since, first_seen_at)
		SELECT ea.id, sym.venue, s.uid, 'mftiko', min(s.since) - interval '1 minute', $1::timestamptz
		FROM mirror.scope s
		JOIN mirror.account a
		  ON a.api_id = s.api_id AND a.deleted_at IS NULL
		 AND a.exchange IS NOT NULL AND a.exch_account_uid IS NOT NULL
		JOIN pms.account_kind k ON k.kind = a.kind AND k.in_scope
		JOIN pms.exchange_account ea
		  ON ea.deleted_at IS NULL
		 AND ea.exchange = a.exchange
		 AND ea.exch_account_uid = a.exch_account_uid
		JOIN pms.exchange_account_kind ek
		  ON ek.exchange_account_id = ea.id
		 AND ek.kind = a.kind
		 AND ek.disabled_at IS NULL
		JOIN mirror.symbol sym ON sym.uid = s.uid
		GROUP BY ea.id, sym.venue, s.uid
		HAVING bool_or(sym.venue = ANY (k.venues))
		ORDER BY ea.id, sym.venue, s.uid
		ON CONFLICT (exchange_account_id, venue, uid, source) DO UPDATE
		SET since = EXCLUDED.since
		WHERE EXCLUDED.since < ingest.persist_symbol.since`, now)
	if err != nil {
		return 0, err
	}
	n := int(tag.RowsAffected())
	var planned int
	if err := tx.QueryRow(ctx, `
		WITH picked AS (
			SELECT DISTINCT ON (p.exchange_account_id, p.venue, p.uid)
			       p.exchange_account_id, p.venue, p.uid, p.source, p.since,
			       (
			           EXISTS (
			               SELECT 1 FROM ledger.trade t
			               WHERE t.exchange_account_id = p.exchange_account_id
			                 AND t.venue = p.venue AND t.uid = p.uid
			                 AND t.traded_at >= $1::timestamptz - interval '24 hours'
			           )
			           OR EXISTS (
			               SELECT 1
			               FROM ledger.position_snapshot ps
			               JOIN ledger.snapshot_batch b ON b.id = ps.batch_id
			               WHERE b.exchange_account_id = p.exchange_account_id
			                 AND ps.venue = p.venue AND ps.uid = p.uid
			                 AND b.bucket = (
			                     SELECT max(b2.bucket) FROM ledger.snapshot_batch b2
			                     WHERE b2.exchange_account_id = p.exchange_account_id
			                 )
			           )
			       ) AS hot
			FROM ingest.persist_symbol p
			JOIN pms.exchange_account ea
			  ON ea.id = p.exchange_account_id AND ea.deleted_at IS NULL
			ORDER BY p.exchange_account_id, p.venue, p.uid, p.since,
			         CASE p.source WHEN 'mftiko' THEN 0 ELSE 1 END
		),
		symbol_trade AS (
			INSERT INTO ingest.stream (kind, lease_key, key, source, tier, period, next_due_at)
			SELECT 'trade.history', 'account:' || p.exchange_account_id, p.venue || ':' || p.uid,
			       p.source,
			       CASE WHEN p.hot THEN 'hot' ELSE 'cold' END,
			       CASE WHEN p.hot THEN interval '2 minutes' ELSE interval '24 hours' END,
			       $1::timestamptz
			FROM picked p
			WHERE p.venue IN ('Binance', 'BinanceUM', 'BinanceCM')
			ORDER BY p.exchange_account_id, p.venue, p.uid
			ON CONFLICT (kind, lease_key, key) DO UPDATE
			SET source = EXCLUDED.source,
			    tier = EXCLUDED.tier,
			    period = EXCLUDED.period,
			    next_due_at = CASE
			        WHEN ingest.stream.tier = 'cold' AND EXCLUDED.tier = 'hot'
			             AND ingest.stream.next_due_at > $1::timestamptz
			        THEN $1::timestamptz
			        ELSE ingest.stream.next_due_at
			    END
			WHERE ingest.stream.source IS DISTINCT FROM EXCLUDED.source
			   OR ingest.stream.tier IS DISTINCT FROM EXCLUDED.tier
			   OR ingest.stream.period IS DISTINCT FROM EXCLUDED.period
			RETURNING 1
		),
		category_trade AS (
			INSERT INTO ingest.stream (kind, lease_key, key, source, tier, period, next_due_at)
			SELECT 'trade.history', 'account:' || p.exchange_account_id, p.venue || ':' || sym.category,
			       (array_agg(p.source ORDER BY p.since, CASE p.source WHEN 'mftiko' THEN 0 ELSE 1 END))[1],
			       CASE WHEN bool_or(p.hot) THEN 'hot' ELSE 'cold' END,
			       CASE WHEN bool_or(p.hot) THEN interval '2 minutes' ELSE interval '24 hours' END,
			       $1::timestamptz
			FROM picked p
			JOIN mirror.symbol sym ON sym.uid = p.uid AND sym.venue = p.venue
			WHERE p.venue IN ('Bybit', 'Okx')
			GROUP BY p.exchange_account_id, p.venue, sym.category
			ORDER BY p.exchange_account_id, p.venue, sym.category
			ON CONFLICT (kind, lease_key, key) DO UPDATE
			SET source = EXCLUDED.source,
			    tier = EXCLUDED.tier,
			    period = EXCLUDED.period,
			    next_due_at = CASE
			        WHEN ingest.stream.tier = 'cold' AND EXCLUDED.tier = 'hot'
			             AND ingest.stream.next_due_at > $1::timestamptz
			        THEN $1::timestamptz
			        ELSE ingest.stream.next_due_at
			    END
			WHERE ingest.stream.source IS DISTINCT FROM EXCLUDED.source
			   OR ingest.stream.tier IS DISTINCT FROM EXCLUDED.tier
			   OR ingest.stream.period IS DISTINCT FROM EXCLUDED.period
			RETURNING 1
		),
		funding AS (
			INSERT INTO ingest.stream (kind, lease_key, key, source, tier, period, next_due_at)
			SELECT DISTINCT ON (p.exchange_account_id, p.venue)
			       'funding.history', 'account:' || p.exchange_account_id, p.venue,
			       'system', 'hot', interval '1 hour', $1::timestamptz
			FROM picked p
			WHERE p.venue IN ('BinanceUM', 'BinanceCM', 'Bybit', 'Okx')
			ORDER BY p.exchange_account_id, p.venue
			ON CONFLICT (kind, lease_key, key) DO NOTHING
			RETURNING 1
		)
		SELECT (SELECT count(*) FROM symbol_trade)
		     + (SELECT count(*) FROM category_trade)
		     + (SELECT count(*) FROM funding)`, now).Scan(&planned); err != nil {
		return 0, err
	}
	return n + planned, nil
}}
