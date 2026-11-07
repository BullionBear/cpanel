// Package migrations holds cpanel's PostgreSQL schema as numbered up/down SQL pairs
// (NNNN_name.up.sql, NNNN_name.down.sql). cpanel-migrate embeds them and applies the up files.
// Down files are for tests; a deployment does not downgrade the schema. cpanel-api and cpanel-worker
// do not migrate at startup: they only check that the database is at this version.
package migrations

import "embed"

// FS holds every migration file.
//
//go:embed *.sql
var FS embed.FS
