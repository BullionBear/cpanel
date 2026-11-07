package auth

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const (
	defaultAPIBase       = "https://discord.com/api/v10"
	defaultAuthorizeBase = "https://discord.com/oauth2/authorize"
)

// Directory talks to Discord. Tests substitute a fake.
type Directory interface {
	AuthorizeURL(state string) string
	Exchange(ctx context.Context, code string) (string, error)
	User(ctx context.Context, accessToken string) (User, error)
	Member(ctx context.Context, userID string) error
}

// Client is Directory over HTTP. Errors do not include the response body, the
// authorization code, or the bot token.
type Client struct {
	Settings Settings
	HTTP     *http.Client
}

// NewClient returns a client. HTTP is http.DefaultClient when nil, with a timeout set here.
func NewClient(s Settings) *Client {
	return &Client{Settings: s, HTTP: &http.Client{Timeout: 15 * time.Second}}
}

func (c *Client) api() string {
	if c.Settings.APIBase != "" {
		return strings.TrimRight(c.Settings.APIBase, "/")
	}
	return defaultAPIBase
}

// AuthorizeURL is the Discord consent page for state.
func (c *Client) AuthorizeURL(state string) string {
	base := defaultAuthorizeBase
	if c.Settings.AuthorizeBase != "" {
		base = c.Settings.AuthorizeBase
	}
	q := url.Values{}
	q.Set("client_id", c.Settings.ClientID)
	q.Set("redirect_uri", c.Settings.Redirect())
	q.Set("response_type", "code")
	q.Set("scope", "identify")
	q.Set("state", state)
	q.Set("prompt", "consent")
	return base + "?" + q.Encode()
}

// Exchange trades an authorization code for an access token.
func (c *Client) Exchange(ctx context.Context, code string) (string, error) {
	form := url.Values{}
	form.Set("client_id", c.Settings.ClientID)
	form.Set("client_secret", c.Settings.ClientSecret)
	form.Set("grant_type", "authorization_code")
	form.Set("code", code)
	form.Set("redirect_uri", c.Settings.Redirect())
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.api()+"/oauth2/token", strings.NewReader(form.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	var body struct {
		AccessToken string `json:"access_token"`
	}
	if err := c.do(req, &body); err != nil {
		return "", err
	}
	if body.AccessToken == "" {
		return "", ErrUnavailable
	}
	return body.AccessToken, nil
}

// User loads the authorized account. The access token is not returned.
func (c *Client) User(ctx context.Context, accessToken string) (User, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.api()+"/users/@me", nil)
	if err != nil {
		return User{}, err
	}
	req.Header.Set("Authorization", "Bearer "+accessToken)
	var body struct {
		ID         string `json:"id"`
		Username   string `json:"username"`
		GlobalName string `json:"global_name"`
	}
	if err := c.do(req, &body); err != nil {
		return User{}, err
	}
	if body.ID == "" {
		return User{}, ErrUnavailable
	}
	name := body.GlobalName
	if name == "" {
		name = body.Username
	}
	return User{ID: body.ID, Username: body.Username, DisplayName: name}, nil
}

// Member checks guild membership and the required role.
func (c *Client) Member(ctx context.Context, userID string) error {
	u := fmt.Sprintf("%s/guilds/%s/members/%s", c.api(), url.PathEscape(c.Settings.GuildID), url.PathEscape(userID))
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bot "+c.Settings.BotToken)
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("%w", ErrUnavailable)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return ErrNotMember
	}
	if resp.StatusCode != http.StatusOK {
		return ErrUnavailable
	}
	var body struct {
		Roles []string `json:"roles"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		return ErrUnavailable
	}
	for _, role := range body.Roles {
		if role == c.Settings.RoleID {
			return nil
		}
	}
	return ErrNoRole
}

func (c *Client) do(req *http.Request, dest any) error {
	resp, err := c.HTTP.Do(req)
	if err != nil {
		return fmt.Errorf("%w", ErrUnavailable)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return ErrUnavailable
	}
	if err := json.NewDecoder(resp.Body).Decode(dest); err != nil {
		return ErrUnavailable
	}
	return nil
}
