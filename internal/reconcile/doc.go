// Package reconcile will hold the ReconcileLoop (ARCHITECTURE §4.3): each round reads the database, works out the
// jobs that should exist, and inserts them with INSERT … ON CONFLICT DO NOTHING. It arrives with C3.
//
// It does no I/O but the database (I5). internal/archtest holds this package and everything under it to that from
// now on: no net/http, no RPC or websocket client, no venue client, and net only through the Postgres driver.
package reconcile
