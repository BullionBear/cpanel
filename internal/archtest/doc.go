// Package archtest checks the invariants of ARCHITECTURE §2 that the import graph can show. It has no code of its
// own: the rules run as a test over `go list -deps`, so `make check` and CI enforce them. The layout follows
// mftiko's internal/archtest.
//
//   - I1: cpanel links nothing of mftiko but contract/ (not internal/, exchange/, migrations/ or anything else), and
//     no etcd client. This holds for test code too.
//   - I5: the ReconcileLoop, internal/reconcile and everything under it, does no I/O but the database. It links no
//     net/http, net/rpc or net/smtp, no RPC or websocket client (connect, grpc, fasthttp, coder/websocket,
//     gorilla/websocket) and no venue client: a REST client links net/http, and mftiko's exchange/ is already
//     excluded by I1. It reaches net only through the Postgres driver (pgx) and the logger (zerolog, which uses
//     only net's IP types); any other path to net is a violation.
//   - I6: cmd/cpanel-api, the public layer, does not link internal/platform/sealbox/open, the code that opens
//     sealed secrets.
//
// I5 and I6 are about what the binaries contain and ignore test code. The rules match on paths, so the packages they
// name must exist: internal/reconcile and internal/platform/sealbox/open are reserved until C3 and C5 fill them.
package archtest
