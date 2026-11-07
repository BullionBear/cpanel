// Package worker runs the jobs in ingest.job (ARCHITECTURE §4.4). Each of a worker's slots takes the lease of one
// lease_key, runs that key's due jobs one after another, each within its budget, and gives the key up when none
// is left. So one account's requests stay inside one process, where its rate limit is counted.
//
// Correctness does not rest on the lease (C3-D004). A handler writes each page with Job.CommitPage, which commits
// the page's rows and moves the stream's cursor in one transaction, guarded by a compare-and-swap on
// cursor_version. Two workers running the same stream, after a lease misjudged, cannot both move its cursor.
//
// A worker that is killed leaves its jobs running under its lease token. The next worker to take the lease after
// it expires sets them back to pending with attempt + 1. A worker that is stopped finishes the page in progress,
// sets its job back to pending without counting an attempt, and releases the lease at once.
package worker
