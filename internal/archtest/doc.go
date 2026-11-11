// Package archtest checks the invariants of ARCHITECTURE §2 that the import graph can show. It has no code of its
// own: the rules run as a test over `go list -deps`, so `make check` and CI enforce them. The layout follows
// mftiko's internal/archtest.
//
//   - I1: cpanel links nothing of mftiko but contract/ (not internal/, exchange/, migrations/ or anything else), and
//     no etcd client.
//   - I5: the ReconcileLoop, internal/reconcile and everything under it, does no I/O but the database. It links no
//     net/http, net/rpc or net/smtp, no RPC or websocket client (connect, grpc, fasthttp, coder/websocket,
//     gorilla/websocket) and no venue client: a REST client links net/http, and mftiko's exchange/ is already
//     excluded by I1. It reaches net only through the Postgres driver (pgx) and the logger (zerolog, which uses
//     only net's IP types); any other path to net is a violation.
//   - I6: cmd/cpanel-api, the public layer, does not link internal/platform/sealbox/open, the code that opens
//     sealed secrets. cmd/cpanel-api and internal/reconcile do not link internal/venue; venue clients run only
//     in the worker (C5-03).
//
// The rules are about what the binaries contain, so they check production code and not test files. They match on
// paths, so the packages they name must exist. internal/platform/sealbox/open opens sealed secrets (C5-02);
// cmd/cpanel-api links the sealing package and not this one.
package archtest
