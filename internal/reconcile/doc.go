// Package reconcile is the ReconcileLoop (ARCHITECTURE §4.3). A round runs every rule, in order, in one
// transaction; each rule works out the jobs that should exist and inserts them with INSERT … ON CONFLICT
// (dedup_key) DO NOTHING. Every cpanel-worker replica runs a loop. ingest.job's UNIQUE dedup_key keeps their jobs
// apart, so there is no leader (C3-D002). A missed round costs nothing: the next one inserts what is still missing.
//
// It does no I/O but the database (I5). internal/archtest holds this package and everything under it to that: no
// net/http, no RPC or websocket client, no venue client, and net only through the Postgres driver. The Prometheus
// client links net/http, so a round's numbers leave the package as a Result and cpanel-worker exports them.
package reconcile
