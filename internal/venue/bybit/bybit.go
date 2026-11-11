// Package bybit is the read-only Bybit v5 client (C5-03). Signing follows the v5 guide:
// HMAC-SHA256 of timestamp + api key + recv window + query string, hex encoded.
// Only GET is sent.
package bybit

import (
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/BullionBear/cpanel/internal/classify"
	"github.com/BullionBear/cpanel/internal/venue"
)

const (
	defaultHost = "https://api.bybit.com"
	recvWindow  = "5000"
)

// Client calls Bybit with one key.
type Client struct {
	Host     string
	Key      string
	Secret   string
	Now      func() time.Time
	HTTP     *venue.Transport
	requests int
}

// CheckAccount reads GET /v5/user/query-api. readOnly must be 1. userID is the account uid.
func (c *Client) CheckAccount(ctx context.Context) (venue.Check, error) {
	body, err := c.get(ctx, "/v5/user/query-api", "")
	if err != nil {
		return venue.Check{}, err
	}
	var payload struct {
		Result struct {
			ReadOnly int         `json:"readOnly"`
			UserID   json.Number `json:"userID"`
		} `json:"result"`
	}
	if err := unmarshal(body, &payload); err != nil || payload.Result.UserID.String() == "" {
		return venue.Check{}, &classify.ClassifiedError{Class: classify.BadRequest, Detail: "query-api response has no userID"}
	}
	summary := "readOnly=" + strconv.Itoa(payload.Result.ReadOnly)
	if payload.Result.ReadOnly != 1 {
		return venue.Check{UID: payload.Result.UserID.String(), Summary: summary, Reason: "api key is not read-only"}, nil
	}
	return venue.Check{UID: payload.Result.UserID.String(), ReadOnly: true, Summary: summary}, nil
}

// ReadFamily checks GET /v5/account/wallet-balance for the v5 family. Other families are not called.
func (c *Client) ReadFamily(ctx context.Context, family string) error {
	if family != "v5" {
		return &classify.ClassifiedError{Class: classify.BadRequest, Detail: "api family is not called"}
	}
	_, err := c.get(ctx, "/v5/account/wallet-balance", "accountType=UNIFIED")
	return err
}

// Calls is how many GET requests this client has sent.
func (c *Client) Calls() int { return c.requests }

func (c *Client) get(ctx context.Context, path, query string) ([]byte, error) {
	ts := strconv.FormatInt(c.now().UnixMilli(), 10)
	target := c.host() + path
	if query != "" {
		target += "?" + query
	}
	req, err := http.NewRequest(http.MethodGet, target, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("X-BAPI-API-KEY", c.Key)
	req.Header.Set("X-BAPI-TIMESTAMP", ts)
	req.Header.Set("X-BAPI-RECV-WINDOW", recvWindow)
	req.Header.Set("X-BAPI-SIGN", sign(c.Secret, ts+c.Key+recvWindow+query))
	status, header, body, err := c.http().Get(ctx, req)
	c.requests++
	c.noteLimit(header)
	if refined := refine(status, body, err); refined != nil {
		return body, refined
	}
	return body, nil
}

func (c *Client) host() string {
	if c.Host != "" {
		return strings.TrimRight(c.Host, "/")
	}
	return defaultHost
}

func (c *Client) now() time.Time {
	if c.Now != nil {
		return c.Now()
	}
	if c.HTTP != nil && c.HTTP.Now != nil {
		return c.HTTP.Now()
	}
	return time.Now()
}

func (c *Client) http() *venue.Transport {
	if c.HTTP == nil {
		c.HTTP = &venue.Transport{}
	}
	return c.HTTP
}

// noteLimit waits out the window when less than a fifth of the account budget remains.
func (c *Client) noteLimit(h http.Header) {
	if h == nil {
		return
	}
	limit, err1 := strconv.Atoi(h.Get("X-Bapi-Limit"))
	remaining, err2 := strconv.Atoi(h.Get("X-Bapi-Limit-Status"))
	reset, err3 := strconv.ParseInt(h.Get("X-Bapi-Limit-Reset-Timestamp"), 10, 64)
	if err1 != nil || err2 != nil || err3 != nil || limit <= 0 {
		return
	}
	if remaining*5 > limit {
		return
	}
	c.http().Hold(time.UnixMilli(reset))
}

func sign(secret, payload string) string {
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(payload))
	return hex.EncodeToString(mac.Sum(nil))
}

// Sign is the documented HMAC of timestamp + api key + recv window + query.
func Sign(secret, payload string) string { return sign(secret, payload) }

func refine(status int, body []byte, err error) error {
	if err != nil && (status == 0 || status == http.StatusTeapot) {
		return err
	}
	var payload struct {
		RetCode int    `json:"retCode"`
		RetMsg  string `json:"retMsg"`
	}
	_ = json.Unmarshal(body, &payload)
	if err == nil && payload.RetCode == 0 {
		return nil
	}
	if err == nil && payload.RetCode != 0 {
		return &classify.ClassifiedError{Class: retClass(payload.RetCode), Status: status, Detail: payload.RetMsg}
	}
	ce, ok := err.(*classify.ClassifiedError)
	if !ok || payload.RetCode == 0 {
		return err
	}
	ce.Class = retClass(payload.RetCode)
	return ce
}

func retClass(code int) classify.Class {
	switch code {
	case 10006, 10018:
		return classify.RateLimited
	case 10003, 10004, 10005, 33004:
		return classify.Auth
	default:
		return classify.BadRequest
	}
}

func unmarshal(body []byte, dest any) error {
	dec := json.NewDecoder(strings.NewReader(string(body)))
	dec.UseNumber()
	return dec.Decode(dest)
}
