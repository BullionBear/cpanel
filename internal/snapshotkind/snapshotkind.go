// Package snapshotkind lists the account kinds the worker can snapshot (C6-03). The worker registers
// the snapshot handler for them and passes the list to the ReconcileLoop and its ops collector;
// cpanel-api reads gaps with the same list (C6-04). It imports nothing from cpanel, so cpanel-api
// can link it without linking the worker, the mirror or a venue client.
package snapshotkind

// BinanceUM is the USD-M futures wallet (docs/venues/binance-um.md).
const BinanceUM = "BinanceUM"

// Implemented returns the kinds that have a snapshot. The slice is a fresh copy.
func Implemented() []string {
	return []string{BinanceUM}
}
