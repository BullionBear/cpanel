// Package sym is the SYM client (C4-02). It exports only Revisions, List and GetByUid, and it
// returns cpanel's own types. The generated connect client stays in this package (C4-D006).
//
// The transport is unencrypted HTTP/2 and the protocol is gRPC. The contract promises gRPC;
// Connect is only for debugging (C4-D007, E22-D005).
package sym

import (
	"context"
	"crypto/tls"
	"errors"
	"net"
	"net/http"
	"strings"
	"time"

	"connectrpc.com/connect"
	"golang.org/x/net/http2"

	symv1 "github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1"
	"github.com/BullionBear/mftiko/contract/gen/mftiko/sym/v1/symv1connect"
	"github.com/BullionBear/mftiko/contract/symbol"

	"github.com/BullionBear/cpanel/internal/classify"
)

// pageSize is the largest List page SYM accepts. Zero would mean the same 500, and anything
// above 500 is InvalidArgument.
const pageSize = 500

// Table is one (venue, category) revision. Venue and category are SYM's spelling.
type Table struct {
	Venue    string
	Category string
	Revision int64
}

// Instrument is one SYM instrument as cpanel keeps it. Venue and category are not on
// SymbolInfo: List copies them from the request, and GetByUid parses them from the
// universal ticker. Empty Venue means the ticker did not parse.
type Instrument struct {
	UID              uint32
	Venue            string
	Category         string
	ExchTicker       string
	UniversalTicker  string
	Base             string
	Quote            string
	Settle           string
	ContractSize     *float64
	ContractValueCcy string
	PriceCcy         string
	Expiry           *time.Time
	Strike           *float64
	OptionType       string
	Status           string
	Active           bool
	Filters          map[string]*float64
	UpdatedAt        time.Time
}

// Page is one page of List. Total is the table's row count, not this page's.
type Page struct {
	Instruments []Instrument
	Total       uint32
}

// Client calls SYM. token is the bearer already read from -sym-token.
type Client struct {
	rpc   symv1connect.SymServiceClient
	token string
}

// New builds a gRPC-over-h2c client for baseURL, SYM's listener (for example
// http://100.65.26.119:7450). An empty URL or token fails before any connection.
func New(baseURL, token string) (*Client, error) {
	if strings.TrimSpace(baseURL) == "" || token == "" {
		return nil, errors.New("SYM URL or token is not set")
	}
	hc := &http.Client{
		Timeout: 10 * time.Second,
		Transport: &http2.Transport{
			AllowHTTP: true,
			DialTLSContext: func(ctx context.Context, network, addr string, _ *tls.Config) (net.Conn, error) {
				var d net.Dialer
				return d.DialContext(ctx, network, addr)
			},
		},
	}
	return &Client{
		rpc:   symv1connect.NewSymServiceClient(hc, baseURL, connect.WithGRPC()),
		token: token,
	}, nil
}

// Revisions reads every (venue, category) revision.
func (c *Client) Revisions(ctx context.Context) ([]Table, error) {
	req := connect.NewRequest(&symv1.RevisionsRequest{})
	req.Header().Set("Authorization", "Bearer "+c.token)
	resp, err := c.rpc.Revisions(ctx, req)
	if err != nil {
		return nil, callErr(ctx, err)
	}
	out := make([]Table, 0)
	if resp.Msg.GetRevisions() == nil {
		return out, nil
	}
	for _, table := range resp.Msg.GetRevisions().GetTables() {
		out = append(out, Table{Venue: table.GetVenue(), Category: table.GetCategory(), Revision: table.GetRevision()})
	}
	return out, nil
}

// List reads one page of the active (venue, category) table. offset is the number of rows
// already read. The caller walks pages; a sync does not keep the offset across jobs.
func (c *Client) List(ctx context.Context, venue, category string, offset uint32) (Page, error) {
	req := connect.NewRequest(&symv1.ListRequest{
		Venue: venue, Category: category, Limit: pageSize, Offset: offset,
	})
	req.Header().Set("Authorization", "Bearer "+c.token)
	resp, err := c.rpc.List(ctx, req)
	if err != nil {
		return Page{}, callErr(ctx, err)
	}
	list := resp.Msg.GetList()
	page := Page{}
	if list == nil {
		return page, nil
	}
	page.Total = list.GetTotal()
	page.Instruments = make([]Instrument, 0, len(list.GetSymbols()))
	for _, info := range list.GetSymbols() {
		inst, err := instrumentFrom(info, venue, category)
		if err != nil {
			return Page{}, err
		}
		page.Instruments = append(page.Instruments, inst)
	}
	return page, nil
}

// GetByUid reads one instrument. includeInactive also searches the terminal table.
// Venue and category come from the universal ticker. A ticker that does not parse leaves
// them empty and still returns the instrument: the caller already knows the row.
func (c *Client) GetByUid(ctx context.Context, uid uint32, includeInactive bool) (Instrument, error) {
	req := connect.NewRequest(&symv1.GetByUidRequest{Uid: uid, IncludeInactive: includeInactive})
	req.Header().Set("Authorization", "Bearer "+c.token)
	resp, err := c.rpc.GetByUid(ctx, req)
	if err != nil {
		return Instrument{}, callErr(ctx, err)
	}
	info := resp.Msg.GetInfo()
	venue, category := "", ""
	if info != nil {
		if parsed, err := symbol.Parse(info.GetUniversalTicker()); err == nil {
			venue, category = parsed.Venue, string(parsed.Category)
		}
	}
	return instrumentFrom(info, venue, category)
}

func instrumentFrom(info *symv1.SymbolInfo, venue, category string) (Instrument, error) {
	if info == nil {
		return Instrument{}, &classify.ClassifiedError{Class: classify.NotFound, Detail: "SYM returned no instrument"}
	}
	if info.GetUid() == 0 {
		return Instrument{}, &classify.ClassifiedError{Class: classify.Internal, Detail: "SYM returned uid 0"}
	}
	inst := Instrument{
		UID:              info.GetUid(),
		Venue:            venue,
		Category:         category,
		ExchTicker:       info.GetExchTicker(),
		UniversalTicker:  info.GetUniversalTicker(),
		Base:             info.GetBase(),
		Quote:            info.GetQuote(),
		Settle:           info.GetSettle(),
		ContractValueCcy: info.GetContractValueCcy(),
		PriceCcy:         info.GetPriceCcy(),
		OptionType:       info.GetOptionType(),
		Status:           statusName(info.GetStatus()),
		Filters:          filtersOf(info),
	}
	if info.ContractSize != nil {
		v := *info.ContractSize
		inst.ContractSize = &v
	}
	if info.Strike != nil {
		v := *info.Strike
		inst.Strike = &v
	}
	if ns := info.GetExpiryNs(); ns != 0 {
		t := time.Unix(0, ns).UTC()
		inst.Expiry = &t
	}
	if ns := info.GetUpdatedAtNs(); ns != 0 {
		inst.UpdatedAt = time.Unix(0, ns).UTC()
	}
	return inst, nil
}

func statusName(status symv1.SymbolStatus) string {
	return strings.TrimPrefix(status.String(), "SYMBOL_STATUS_")
}

func filtersOf(info *symv1.SymbolInfo) map[string]*float64 {
	raw := info.GetFilters()
	if len(raw) == 0 {
		return nil
	}
	out := make(map[string]*float64, len(raw))
	for name, bound := range raw {
		if bound == nil || bound.Value == nil {
			out[name] = nil
			continue
		}
		v := bound.GetValue()
		out[name] = &v
	}
	return out
}

func callErr(ctx context.Context, err error) error {
	if err == nil {
		return nil
	}
	if ctx.Err() != nil {
		return ctx.Err()
	}
	var ce *connect.Error
	if !errors.As(err, &ce) {
		return &classify.ClassifiedError{Class: classify.Unavailable, Detail: err.Error()}
	}
	detail := ce.Message()
	if detail == "" {
		detail = ce.Code().String()
	}
	var class classify.Class
	switch ce.Code() {
	case connect.CodeUnauthenticated:
		class = classify.Auth
	case connect.CodeInvalidArgument:
		class = classify.BadRequest
	case connect.CodeNotFound:
		class = classify.NotFound
	default:
		class = classify.Unavailable
	}
	return &classify.ClassifiedError{Class: class, Detail: detail}
}
