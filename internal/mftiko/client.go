package mftiko

import (
	"context"
	"errors"
	"fmt"
	"net/http"
	"time"

	apiv1 "github.com/BullionBear/mftiko/contract/gen/api/v1"

	"github.com/BullionBear/cpanel/internal/classify"
)

// pageSize is the largest page mftiko accepts. Sync walks next_cursor until it is absent.
const pageSize = 1000

// maxAPIID is 2^53−1, the range mftiko assigns (E10-D008).
const maxAPIID int64 = 9007199254740991

// Account is one mftiko account as cpanel keeps it. Exchange and ExchAccountUID are empty when
// mftiko omitted them. DeletedAt is set only after a soft delete.
type Account struct {
	APIID          int64
	Name           string
	Kind           string
	Exchange       string
	ExchAccountUID string
	Revision       int64
	DeletedAt      *time.Time
}

// Page is one page of ListAccounts. Next is empty on the last page.
type Page struct {
	Accounts []Account
	Next     string
}

// Client calls mftiko-api. It exports only reads. Its transport refuses every method but GET and
// HEAD before the request is sent: the PMS token can write, and mftiko has no read-only role (I2).
type Client struct {
	api *apiv1.ClientWithResponses
	hc  *http.Client
}

// New builds a client for baseURL, which is the mftiko-api base including /api/v1. token is the
// bearer already read from -mftiko-token. hc nil means a client with a 10s timeout.
func New(baseURL, token string, hc *http.Client) (*Client, error) {
	if baseURL == "" || token == "" {
		return nil, errors.New("mftiko URL or token is not set")
	}
	if hc == nil {
		hc = &http.Client{Timeout: 10 * time.Second}
	}
	base := hc.Transport
	if base == nil {
		base = http.DefaultTransport
	}
	wrapped := *hc
	wrapped.Transport = getOnly{base: base}
	api, err := apiv1.NewClientWithResponses(baseURL,
		apiv1.WithHTTPClient(&wrapped),
		apiv1.WithRequestEditorFn(func(_ context.Context, req *http.Request) error {
			req.Header.Set("Authorization", "Bearer "+token)
			return nil
		}))
	if err != nil {
		return nil, err
	}
	return &Client{api: api, hc: &wrapped}, nil
}

// ListAccounts reads one page, with include_deleted and the maximum limit. cursor empty starts
// at the first page. The caller follows Next until it is empty; a sync does not keep the cursor
// across jobs.
func (c *Client) ListAccounts(ctx context.Context, cursor string) (Page, error) {
	limit := apiv1.Limit(pageSize)
	include := true
	params := &apiv1.ListAccountsParams{Limit: &limit, IncludeDeleted: &include}
	if cursor != "" {
		params.Cursor = &cursor
	}
	resp, err := c.api.ListAccountsWithResponse(ctx, params)
	if err != nil {
		if ctx.Err() != nil {
			return Page{}, ctx.Err()
		}
		return Page{}, &classify.ClassifiedError{Class: classify.Unavailable, Detail: err.Error()}
	}
	if resp.StatusCode() < 200 || resp.StatusCode() >= 300 {
		retry := ""
		if resp.HTTPResponse != nil {
			retry = resp.HTTPResponse.Header.Get("Retry-After")
		}
		return Page{}, classifyStatus(resp.StatusCode(), retry, resp.Body)
	}
	if resp.JSON200 == nil {
		return Page{}, &classify.ClassifiedError{Class: classify.Internal, Status: resp.StatusCode(), Detail: "account list had no JSON body"}
	}
	out := Page{Accounts: make([]Account, 0, len(resp.JSON200.Items))}
	for _, item := range resp.JSON200.Items {
		if item.ApiId < 1 || item.ApiId > maxAPIID {
			return Page{}, &classify.ClassifiedError{Class: classify.Internal, Detail: fmt.Sprintf("api_id %d is outside 1..2^53-1", item.ApiId)}
		}
		account := Account{APIID: item.ApiId, Name: item.Name, Kind: item.Kind, Revision: item.Revision, DeletedAt: item.DeletedAt}
		if item.Exchange != nil {
			account.Exchange = *item.Exchange
		}
		if item.ExchAccountUid != nil {
			account.ExchAccountUID = *item.ExchAccountUid
		}
		out.Accounts = append(out.Accounts, account)
	}
	if resp.JSON200.NextCursor != nil {
		out.Next = *resp.JSON200.NextCursor
	}
	return out, nil
}

// classifyStatus is C4-D008. 418 is a 4xx here, not an exchange IP ban: recording ip_banned would
// pause the exchange egress for a call that never went there.
func classifyStatus(status int, retryAfter string, body []byte) *classify.ClassifiedError {
	ce := classify.FromHTTP(status, retryAfter, body, time.Now())
	switch ce.Class {
	case classify.IPBanned, classify.NotFound:
		ce.Class = classify.BadRequest
	}
	return ce
}

// getOnly refuses every method but GET and HEAD before the request leaves the process.
type getOnly struct{ base http.RoundTripper }

func (g getOnly) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.Method != http.MethodGet && req.Method != http.MethodHead {
		return nil, fmt.Errorf("mftiko: %s is not allowed; only GET and HEAD are sent", req.Method)
	}
	if g.base == nil {
		return nil, errors.New("mftiko: nil transport")
	}
	return g.base.RoundTrip(req)
}
