package snapshot

import (
	"encoding/json"
	"fmt"
	"math/big"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/snapshotkind"
	"github.com/BullionBear/cpanel/internal/venue/binance"
)

// VenueBinanceUM is the venue of BinanceUM positions and raw rows, as in mirror.symbol.
const VenueBinanceUM = "BinanceUM"

// Rows are the ledger detail rows of one kind in one batch.
type Rows struct {
	Balances  []Balance
	Positions []Position
}

// NormalizeUMRaw is NormalizeUM over the two payloads as stored in raw.venue_snapshot.
// Normalizing the same payloads again gives the same rows.
func NormalizeUMRaw(balanceBody, positionBody []byte) (Rows, error) {
	balances, err := binance.ParseUMBalance(balanceBody)
	if err != nil {
		return Rows{}, err
	}
	positions, err := binance.ParseUMPositions(positionBody)
	if err != nil {
		return Rows{}, err
	}
	return NormalizeUM(balances, positions)
}

// NormalizeUM turns the fapi v3 balance and positionRisk rows into ledger rows at
// NormVersion (docs/venues/binance-um.md §2). An asset whose four amounts are all zero
// is left out, and so is a position whose positionAmt is zero. Amounts and positionSide
// are kept as sent. A row that is missing a field, or repeats a key, is bad_request.
func NormalizeUM(balances []binance.UMBalance, positions []binance.UMPosition) (Rows, error) {
	out := Rows{Balances: []Balance{}, Positions: []Position{}}
	assets := map[string]bool{}
	for i, b := range balances {
		if b.Asset == "" {
			return Rows{}, badRow("balance", i, "asset is empty")
		}
		if assets[b.Asset] {
			return Rows{}, badRow("balance", i, "asset "+b.Asset+" repeats")
		}
		assets[b.Asset] = true
		amounts := []json.Number{b.Balance, b.AvailableBalance, b.CrossWalletBalance, b.MaxWithdrawAmount}
		zero, err := allZero(amounts)
		if err != nil {
			return Rows{}, badRow("balance", i, b.Asset+": "+err.Error())
		}
		if zero {
			continue
		}
		out.Balances = append(out.Balances, Balance{
			Kind: snapshotkind.BinanceUM, Wallet: WalletUM, Asset: b.Asset,
			Balance: b.Balance.String(), AvailableBalance: b.AvailableBalance.String(),
			CrossWalletBalance: b.CrossWalletBalance.String(), MaxWithdraw: b.MaxWithdrawAmount.String(),
		})
	}
	type positionKey struct{ symbol, side string }
	seen := map[positionKey]bool{}
	for i, p := range positions {
		if p.Symbol == "" {
			return Rows{}, badRow("position", i, "symbol is empty")
		}
		switch p.PositionSide {
		case "BOTH", "LONG", "SHORT":
		default:
			return Rows{}, badRow("position", i, fmt.Sprintf("%s: positionSide %q", p.Symbol, p.PositionSide))
		}
		key := positionKey{p.Symbol, p.PositionSide}
		if seen[key] {
			return Rows{}, badRow("position", i, p.Symbol+" "+p.PositionSide+" repeats")
		}
		seen[key] = true
		if _, err := allZero([]json.Number{p.EntryPrice, p.MarkPrice, p.UnRealizedProfit,
			p.IsolatedMargin, p.InitialMargin, p.PositionInitialMargin, p.IsolatedWallet}); err != nil {
			return Rows{}, badRow("position", i, p.Symbol+": "+err.Error())
		}
		zero, err := allZero([]json.Number{p.PositionAmt})
		if err != nil {
			return Rows{}, badRow("position", i, p.Symbol+": "+err.Error())
		}
		if zero {
			continue
		}
		out.Positions = append(out.Positions, Position{
			Kind: snapshotkind.BinanceUM, Venue: VenueBinanceUM, ExchTicker: p.Symbol, PositionSide: p.PositionSide,
			PositionAmt: p.PositionAmt.String(), EntryPrice: p.EntryPrice.String(), MarkPrice: p.MarkPrice.String(),
			UnrealizedProfit: p.UnRealizedProfit.String(), IsolatedMargin: p.IsolatedMargin.String(),
			InitialMargin: p.InitialMargin.String(), PositionInitialMargin: p.PositionInitialMargin.String(),
			IsolatedWallet: p.IsolatedWallet.String(),
		})
	}
	return out, nil
}

// allZero reports whether every amount is zero. An empty or non-decimal amount is an error.
func allZero(amounts []json.Number) (bool, error) {
	zero := true
	for _, a := range amounts {
		if a == "" {
			return false, fmt.Errorf("an amount is missing")
		}
		r, ok := new(big.Rat).SetString(a.String())
		if !ok {
			return false, fmt.Errorf("amount %q is not a decimal", a)
		}
		if r.Sign() != 0 {
			zero = false
		}
	}
	return zero, nil
}

func badRow(what string, i int, detail string) error {
	return &classify.ClassifiedError{Class: classify.BadRequest, Detail: fmt.Sprintf("fapi %s row %d: %s", what, i, detail)}
}
