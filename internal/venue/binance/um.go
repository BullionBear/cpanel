package binance

import (
	"context"
	"encoding/json"

	"github.com/BullionBear/cpanel/internal/classify"
)

// Snapshot endpoints of the USD-M futures wallet (docs/venues/binance-um.md §2). Neither sends
// symbol, so one call returns every row of the account. Each is weight 5.
const (
	UMBalancePath      = "/fapi/v3/balance"
	UMPositionRiskPath = "/fapi/v3/positionRisk"
)

// UMBalance is one asset of GET /fapi/v3/balance. Amounts are the decimal strings Binance sent.
type UMBalance struct {
	Asset              string      `json:"asset"`
	Balance            json.Number `json:"balance"`
	AvailableBalance   json.Number `json:"availableBalance"`
	CrossWalletBalance json.Number `json:"crossWalletBalance"`
	MaxWithdrawAmount  json.Number `json:"maxWithdrawAmount"`
}

// UMPosition is one symbol and side of GET /fapi/v3/positionRisk. A zero PositionAmt is a
// symbol with open orders and no position.
type UMPosition struct {
	Symbol                string      `json:"symbol"`
	PositionSide          string      `json:"positionSide"`
	PositionAmt           json.Number `json:"positionAmt"`
	EntryPrice            json.Number `json:"entryPrice"`
	MarkPrice             json.Number `json:"markPrice"`
	UnRealizedProfit      json.Number `json:"unRealizedProfit"`
	IsolatedMargin        json.Number `json:"isolatedMargin"`
	InitialMargin         json.Number `json:"initialMargin"`
	PositionInitialMargin json.Number `json:"positionInitialMargin"`
	IsolatedWallet        json.Number `json:"isolatedWallet"`
}

// UMBalance reads GET /fapi/v3/balance. It returns the parsed rows and the body as received,
// which the caller keeps as raw.
func (c *Client) UMBalance(ctx context.Context) ([]UMBalance, []byte, error) {
	body, _, err := c.get(ctx, c.futures(), UMBalancePath, "")
	if err != nil {
		return nil, nil, err
	}
	rows, err := ParseUMBalance(body)
	if err != nil {
		return nil, nil, err
	}
	return rows, body, nil
}

// UMPositionRisk reads GET /fapi/v3/positionRisk. It returns the parsed rows and the body as
// received, which the caller keeps as raw.
func (c *Client) UMPositionRisk(ctx context.Context) ([]UMPosition, []byte, error) {
	body, _, err := c.get(ctx, c.futures(), UMPositionRiskPath, "")
	if err != nil {
		return nil, nil, err
	}
	rows, err := ParseUMPositions(body)
	if err != nil {
		return nil, nil, err
	}
	return rows, body, nil
}

// ParseUMBalance parses a GET /fapi/v3/balance body. A body that is not an array of balance
// objects, or an amount that is not a number, is bad_request.
func ParseUMBalance(body []byte) ([]UMBalance, error) {
	rows := []UMBalance{}
	if err := json.Unmarshal(body, &rows); err != nil {
		return nil, &classify.ClassifiedError{Class: classify.BadRequest, Detail: "fapi balance response: " + err.Error()}
	}
	return rows, nil
}

// ParseUMPositions parses a GET /fapi/v3/positionRisk body. A body that is not an array of
// position objects, or an amount that is not a number, is bad_request.
func ParseUMPositions(body []byte) ([]UMPosition, error) {
	rows := []UMPosition{}
	if err := json.Unmarshal(body, &rows); err != nil {
		return nil, &classify.ClassifiedError{Class: classify.BadRequest, Detail: "fapi positionRisk response: " + err.Error()}
	}
	return rows, nil
}
