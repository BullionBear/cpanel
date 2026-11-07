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

	lag      *prometheus.Desc
	failures *prometheus.Desc
	paused   *prometheus.Desc
	egress   *prometheus.Desc
	jobs     *prometheus.Desc
	oldest   *prometheus.Desc
	runs     *prometheus.Desc
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
	return runRows.Err()
}
