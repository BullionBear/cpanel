package main

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/rs/zerolog"
)

// opsCollector reads stream and job health from the database on each scrape (C3-D005).
// The series are the source of the Prometheus rules in deploy/prometheus. A restart
// does not drop a pause or a run of failures, because both are rows.
type opsCollector struct {
	db  *pgxpool.Pool
	log zerolog.Logger

	lag        *prometheus.Desc
	failures   *prometheus.Desc
	paused     *prometheus.Desc
	egress     *prometheus.Desc
	jobs       *prometheus.Desc
	oldest     *prometheus.Desc
	runs       *prometheus.Desc
	okAge      *prometheus.Desc
	rows       *prometheus.Desc
	conflicts  *prometheus.Desc
	behind     *prometheus.Desc
	partial    *prometheus.Desc
	unresolved *prometheus.Desc
	unmatched  *prometheus.Desc
	outOfScope *prometheus.Desc
}

func newOpsCollector(db *pgxpool.Pool, log zerolog.Logger) *opsCollector {
	return &opsCollector{
		db:  db,
		log: log,
		lag: prometheus.NewDesc(
			"cpanel_stream_lag_seconds",
			"Seconds since a stream last succeeded, or since it was created. Paused streams are omitted.",
			[]string{"kind", "stream"}, nil),
		failures: prometheus.NewDesc(
			"cpanel_stream_consecutive_failures",
			"Consecutive failures of a stream that is not paused.",
			[]string{"kind", "stream"}, nil),
		paused: prometheus.NewDesc(
			"cpanel_streams_paused",
			"Streams with a paused_reason.",
			nil, nil),
		egress: prometheus.NewDesc(
			"cpanel_egress_pauses",
			"ingest.pause rows that are still in force.",
			nil, nil),
		jobs: prometheus.NewDesc(
			"cpanel_jobs_unfinished",
			"Unfinished jobs.",
			[]string{"status"}, nil),
		oldest: prometheus.NewDesc(
			"cpanel_job_oldest_age_seconds",
			"Age of the oldest unfinished job, or 0 when there is none.",
			nil, nil),
		runs: prometheus.NewDesc(
			"cpanel_job_runs",
			"job_run rows finished in the last hour.",
			[]string{"status", "error_class"}, nil),
		okAge: prometheus.NewDesc(
			"cpanel_mirror_ok_age_seconds",
			"Seconds since a mirror source last succeeded, or since its first attempt when it never has.",
			[]string{"source"}, nil),
		rows: prometheus.NewDesc(
			"cpanel_mirror_rows",
			"Rows in a mirror table.",
			[]string{"table"}, nil),
		conflicts: prometheus.NewDesc(
			"cpanel_mirror_account_identity_conflicts",
			"Mirrored accounts that refused a changed exchange identity.",
			nil, nil),
		behind: prometheus.NewDesc(
			"cpanel_mirror_sym_tables_behind",
			"SYM tables whose loaded revision is missing or behind the remote revision.",
			nil, nil),
		partial: prometheus.NewDesc(
			"cpanel_mirror_scope_unavailable_regions",
			"Regions mftiko omitted from the latest finished scope read.",
			nil, nil),
		unresolved: prometheus.NewDesc(
			"cpanel_mirror_scope_uids_unresolved",
			"Scope UIDs that are not in the symbol mirror.",
			nil, nil),
		unmatched: prometheus.NewDesc(
			"cpanel_mftiko_accounts_unmatched",
			"Mirrored exchange identities the vault does not cover. Out-of-scope kinds are not included.",
			[]string{"traded", "reason"}, nil),
		outOfScope: prometheus.NewDesc(
			"cpanel_mftiko_kinds_out_of_scope",
			"Mirrored kinds cpanel does not trade yet, counted per exchange identity and kind.",
			[]string{"kind", "traded"}, nil),
	}
}

func (c *opsCollector) Describe(ch chan<- *prometheus.Desc) {
	ch <- c.lag
	ch <- c.failures
	ch <- c.paused
	ch <- c.egress
	ch <- c.jobs
	ch <- c.oldest
	ch <- c.runs
	ch <- c.okAge
	ch <- c.rows
	ch <- c.conflicts
	ch <- c.behind
	ch <- c.partial
	ch <- c.unresolved
	ch <- c.unmatched
	ch <- c.outOfScope
}

func (c *opsCollector) Collect(ch chan<- prometheus.Metric) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	if err := c.collect(ctx, ch); err != nil {
		c.log.Error().Err(err).Msg("ops metrics")
		ch <- prometheus.NewInvalidMetric(c.paused, err)
	}
}

func (c *opsCollector) collect(ctx context.Context, ch chan<- prometheus.Metric) error {
	rows, err := c.db.Query(ctx, `
		SELECT kind, id::text,
		       EXTRACT(EPOCH FROM (now() - COALESCE(last_ok_at, created_at)))::float8,
		       consecutive_failures
		FROM ingest.stream
		WHERE paused_reason IS NULL`)
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var kind, id string
		var lag float64
		var failures int
		if err := rows.Scan(&kind, &id, &lag, &failures); err != nil {
			return err
		}
		ch <- prometheus.MustNewConstMetric(c.lag, prometheus.GaugeValue, lag, kind, id)
		ch <- prometheus.MustNewConstMetric(c.failures, prometheus.GaugeValue, float64(failures), kind, id)
	}
	if err := rows.Err(); err != nil {
		return err
	}

	var paused int
	if err := c.db.QueryRow(ctx, `SELECT count(*) FROM ingest.stream WHERE paused_reason IS NOT NULL`).Scan(&paused); err != nil {
		return err
	}
	ch <- prometheus.MustNewConstMetric(c.paused, prometheus.GaugeValue, float64(paused))

	var egress int
	if err := c.db.QueryRow(ctx, `SELECT count(*) FROM ingest.pause WHERE until > now()`).Scan(&egress); err != nil {
		return err
	}
	ch <- prometheus.MustNewConstMetric(c.egress, prometheus.GaugeValue, float64(egress))

	counts := map[string]int{"pending": 0, "running": 0}
	jobRows, err := c.db.Query(ctx, `SELECT status, count(*) FROM ingest.job GROUP BY status`)
	if err != nil {
		return err
	}
	defer jobRows.Close()
	for jobRows.Next() {
		var status string
		var n int
		if err := jobRows.Scan(&status, &n); err != nil {
			return err
		}
		counts[status] = n
	}
	if err := jobRows.Err(); err != nil {
		return err
	}
	for _, status := range []string{"pending", "running"} {
		ch <- prometheus.MustNewConstMetric(c.jobs, prometheus.GaugeValue, float64(counts[status]), status)
	}

	var oldest float64
	if err := c.db.QueryRow(ctx, `
		SELECT COALESCE(EXTRACT(EPOCH FROM (now() - min(enqueued_at)))::float8, 0) FROM ingest.job`).Scan(&oldest); err != nil {
		return err
	}
	ch <- prometheus.MustNewConstMetric(c.oldest, prometheus.GaugeValue, oldest)

	runRows, err := c.db.Query(ctx, `
		SELECT status, COALESCE(error_class::text, 'none'), count(*)
		FROM ingest.job_run
		WHERE finished_at > now() - interval '1 hour'
		GROUP BY 1, 2`)
	if err != nil {
		return err
	}
	defer runRows.Close()
	for runRows.Next() {
		var status, class string
		var n int
		if err := runRows.Scan(&status, &class, &n); err != nil {
			return err
		}
		ch <- prometheus.MustNewConstMetric(c.runs, prometheus.GaugeValue, float64(n), status, class)
	}
	if err := runRows.Err(); err != nil {
		return err
	}

	ageRows, err := c.db.Query(ctx, `
		SELECT source, EXTRACT(EPOCH FROM (now() - COALESCE(last_ok_at, last_attempt_at)))::float8
		FROM mirror.sync_state
		WHERE last_ok_at IS NOT NULL OR last_attempt_at IS NOT NULL`)
	if err != nil {
		return err
	}
	defer ageRows.Close()
	for ageRows.Next() {
		var source string
		var age float64
		if err := ageRows.Scan(&source, &age); err != nil {
			return err
		}
		ch <- prometheus.MustNewConstMetric(c.okAge, prometheus.GaugeValue, age, source)
	}
	if err := ageRows.Err(); err != nil {
		return err
	}

	var accounts, symbols, scopes int
	if err := c.db.QueryRow(ctx, `SELECT count(*) FROM mirror.account`).Scan(&accounts); err != nil {
		return err
	}
	if err := c.db.QueryRow(ctx, `SELECT count(*) FROM mirror.symbol`).Scan(&symbols); err != nil {
		return err
	}
	if err := c.db.QueryRow(ctx, `SELECT count(*) FROM mirror.scope`).Scan(&scopes); err != nil {
		return err
	}
	ch <- prometheus.MustNewConstMetric(c.rows, prometheus.GaugeValue, float64(accounts), "account")
	ch <- prometheus.MustNewConstMetric(c.rows, prometheus.GaugeValue, float64(symbols), "symbol")
	ch <- prometheus.MustNewConstMetric(c.rows, prometheus.GaugeValue, float64(scopes), "scope")

	var partial int
	if err := c.db.QueryRow(ctx, `
		SELECT COALESCE((
			SELECT cardinality(unavailable_regions) FROM mirror.sync_state WHERE source = 'mftiko.scopes'
		), 0)`).Scan(&partial); err != nil {
		return err
	}
	ch <- prometheus.MustNewConstMetric(c.partial, prometheus.GaugeValue, float64(partial))

	var unresolved int
	if err := c.db.QueryRow(ctx, `
		SELECT count(DISTINCT s.uid)
		FROM mirror.scope s
		LEFT JOIN mirror.symbol y ON y.uid = s.uid
		WHERE y.uid IS NULL`).Scan(&unresolved); err != nil {
		return err
	}
	ch <- prometheus.MustNewConstMetric(c.unresolved, prometheus.GaugeValue, float64(unresolved))

	var conflicts int
	if err := c.db.QueryRow(ctx, `SELECT count(*) FROM mirror.account WHERE identity_conflict IS NOT NULL`).Scan(&conflicts); err != nil {
		return err
	}
	ch <- prometheus.MustNewConstMetric(c.conflicts, prometheus.GaugeValue, float64(conflicts))

	var behind int
	if err := c.db.QueryRow(ctx, `
		SELECT count(*) FROM mirror.sync_state
		WHERE source LIKE 'sym:%'
		  AND remote_revision IS NOT NULL
		  AND (revision IS NULL OR revision < remote_revision)`).Scan(&behind); err != nil {
		return err
	}
	ch <- prometheus.MustNewConstMetric(c.behind, prometheus.GaugeValue, float64(behind))

	unmatched := map[[2]string]float64{}
	for _, reason := range []string{"no_exchange_account", "missing_identity", "kind_not_enabled"} {
		for _, traded := range []string{"false", "true"} {
			unmatched[[2]string{traded, reason}] = 0
		}
	}
	unmatchedRows, err := c.db.Query(ctx, `
		SELECT reason, traded, count(*)
		FROM pms.unmatched
		WHERE reason <> 'kind_out_of_scope'
		GROUP BY 1, 2`)
	if err != nil {
		return err
	}
	defer unmatchedRows.Close()
	for unmatchedRows.Next() {
		var reason string
		var traded bool
		var n int
		if err := unmatchedRows.Scan(&reason, &traded, &n); err != nil {
			return err
		}
		label := "false"
		if traded {
			label = "true"
		}
		unmatched[[2]string{label, reason}] = float64(n)
	}
	if err := unmatchedRows.Err(); err != nil {
		return err
	}
	for key, n := range unmatched {
		ch <- prometheus.MustNewConstMetric(c.unmatched, prometheus.GaugeValue, n, key[0], key[1])
	}

	kindRows, err := c.db.Query(ctx, `
		SELECT kind, traded, count(*)
		FROM pms.unmatched
		WHERE reason = 'kind_out_of_scope'
		GROUP BY 1, 2`)
	if err != nil {
		return err
	}
	defer kindRows.Close()
	for kindRows.Next() {
		var kind string
		var traded bool
		var n int
		if err := kindRows.Scan(&kind, &traded, &n); err != nil {
			return err
		}
		label := "false"
		if traded {
			label = "true"
		}
		ch <- prometheus.MustNewConstMetric(c.outOfScope, prometheus.GaugeValue, float64(n), kind, label)
	}
	return kindRows.Err()
}
