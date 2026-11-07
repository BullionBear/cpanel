package api

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/rs/zerolog"

	"github.com/BullionBear/cpanel/internal/auth"
	"github.com/BullionBear/cpanel/internal/platform/pgtest"
	"github.com/BullionBear/cpanel/migrations"
)

func TestDiscordLogin(t *testing.T) {
	db := pgtest.Start(t)
	ctx := context.Background()
	if _, err := migrations.Up(ctx, db.DSN()); err != nil {
		t.Fatal(err)
	}
	pool, err := pgxpool.New(ctx, db.DSN())
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(pool.Close)

	var logs strings.Builder
	fake := &fakeDiscord{code: "discord-code-0123456789abcdef", user: auth.User{ID: "42", Username: "ada", DisplayName: "Ada"}}
	h := Handler(Config{
		Log:     zerolog.New(&logs),
		Pool:    pool,
		Auth:    testAuthSettings(),
		Discord: fake,
	})

	t.Run("not configured", func(t *testing.T) {
		bare := Handler(Config{Log: zerolog.Nop(), Pool: pool})
		rec := call(bare, http.MethodGet, "/auth/discord/login", "", nil)
		problemOf(t, rec, http.StatusServiceUnavailable, "unavailable")
	})

	t.Run("not in guild", func(t *testing.T) {
		resetAuth(t, ctx, pool, fake)
		fake.status = http.StatusNotFound
		rec := callback(t, h, loginState(t, h))
		problemOf(t, rec, http.StatusForbidden, "forbidden")
		if n := sessions(t, ctx, pool); n != 0 {
			t.Fatalf("sessions %d", n)
		}
	})

	t.Run("missing role", func(t *testing.T) {
		resetAuth(t, ctx, pool, fake)
		fake.roles = nil
		rec := callback(t, h, loginState(t, h))
		p := problemOf(t, rec, http.StatusForbidden, "forbidden")
		if !strings.Contains(p.Detail, "role") {
			t.Fatalf("%+v", p)
		}
		if n := sessions(t, ctx, pool); n != 0 {
			t.Fatalf("sessions %d", n)
		}
	})

	t.Run("bad state", func(t *testing.T) {
		resetAuth(t, ctx, pool, fake)
		loginState(t, h)
		rec := call(h, http.MethodGet, "/auth/discord/callback?code="+fake.code+"&state=nope", "", nil)
		problemOf(t, rec, http.StatusBadRequest, "bad_request")
	})

	var session string
	t.Run("login", func(t *testing.T) {
		resetAuth(t, ctx, pool, fake)
		state := loginState(t, h)
		rec := callback(t, h, state)
		if rec.Code != http.StatusFound || rec.Header().Get("Location") != "/" {
			t.Fatalf("callback %d location %q", rec.Code, rec.Header().Get("Location"))
		}
		c := setCookie(t, rec, auth.CookieName)
		if !c.HttpOnly || !c.Secure || c.SameSite != http.SameSiteStrictMode || c.Path != "/" {
			t.Fatalf("session cookie %+v", c)
		}
		session = c.Value
		var n int
		if err := pool.QueryRow(ctx, `SELECT count(*) FROM meta.session WHERE id_hash = $1`, auth.Hash(session)).Scan(&n); err != nil {
			t.Fatal(err)
		}
		if n != 1 {
			t.Fatalf("stored hashes %d", n)
		}
		me := call(h, http.MethodGet, "/me", "", cookieHeader(session))
		if me.Code != http.StatusOK || !strings.Contains(me.Body.String(), `"discord_user_id":"42"`) {
			t.Fatalf("me %d %s", me.Code, me.Body)
		}
		if fake.calls != 1 {
			t.Fatalf("member calls %d, want 1", fake.calls)
		}
	})

	t.Run("role lost", func(t *testing.T) {
		if _, err := pool.Exec(ctx, `UPDATE meta.session SET role_checked_at = now() - interval '2 hours'`); err != nil {
			t.Fatal(err)
		}
		fake.roles = nil
		rec := call(h, http.MethodGet, "/me", "", cookieHeader(session))
		problemOf(t, rec, http.StatusUnauthorized, "unauthenticated")
		if n := sessions(t, ctx, pool); n != 0 {
			t.Fatalf("sessions %d", n)
		}
	})

	t.Run("logout rejects the next request", func(t *testing.T) {
		resetAuth(t, ctx, pool, fake)
		c := setCookie(t, callback(t, h, loginState(t, h)), auth.CookieName)
		session = c.Value
		rec := call(h, http.MethodPost, "/auth/logout", "", mergeHeaders(cookieHeader(session), originHeader()))
		if rec.Code != http.StatusNoContent {
			t.Fatalf("logout %d %s", rec.Code, rec.Body)
		}
		again := call(h, http.MethodGet, "/me", "", cookieHeader(session))
		problemOf(t, again, http.StatusUnauthorized, "unauthenticated")
	})

	var token string
	var tokenID string
	t.Run("token is shown once and can be revoked", func(t *testing.T) {
		resetAuth(t, ctx, pool, fake)
		logs.Reset()
		c := setCookie(t, callback(t, h, loginState(t, h)), auth.CookieName)
		session = c.Value
		bad := call(h, http.MethodPost, "/tokens", `{"name":"ci"}`, mergeHeaders(cookieHeader(session), http.Header{"Origin": {"https://evil.example"}}))
		problemOf(t, bad, http.StatusForbidden, "forbidden")
		if n := tokens(t, ctx, pool); n != 0 {
			t.Fatalf("tokens after csrf %d", n)
		}
		missing := call(h, http.MethodPost, "/tokens", `{"name":"ci"}`, cookieHeader(session))
		problemOf(t, missing, http.StatusForbidden, "forbidden")

		created := call(h, http.MethodPost, "/tokens", `{"name":"ci"}`, mergeHeaders(cookieHeader(session), originHeader()))
		if created.Code != http.StatusCreated {
			t.Fatalf("create %d %s", created.Code, created.Body)
		}
		var body struct {
			ID    string `json:"id"`
			Token string `json:"token"`
		}
		if err := json.Unmarshal(created.Body.Bytes(), &body); err != nil {
			t.Fatal(err)
		}
		if !strings.HasPrefix(body.Token, auth.TokenPrefix) || body.ID == "" {
			t.Fatalf("%+v", body)
		}
		token, tokenID = body.Token, body.ID
		listed := call(h, http.MethodGet, "/tokens", "", cookieHeader(session))
		if listed.Code != http.StatusOK || strings.Contains(listed.Body.String(), token) {
			t.Fatalf("list %d %s", listed.Code, listed.Body)
		}
		authed := call(h, http.MethodGet, "/me", "", bearerHeader(token))
		if authed.Code != http.StatusOK || !strings.Contains(authed.Body.String(), `"auth":"token"`) {
			t.Fatalf("bearer me %d %s", authed.Code, authed.Body)
		}
		// A bearer request does not use the cookie, and Origin is not required.
		again := call(h, http.MethodPost, "/tokens", `{"name":"other"}`, mergeHeaders(bearerHeader(token), http.Header{"Origin": {"https://evil.example"}}))
		if again.Code != http.StatusCreated {
			t.Fatalf("bearer create %d %s", again.Code, again.Body)
		}
		revoked := call(h, http.MethodDelete, "/tokens/"+tokenID, "", mergeHeaders(cookieHeader(session), originHeader()))
		if revoked.Code != http.StatusNoContent {
			t.Fatalf("delete %d %s", revoked.Code, revoked.Body)
		}
		after := call(h, http.MethodGet, "/me", "", mergeHeaders(bearerHeader(token), cookieHeader(session)))
		problemOf(t, after, http.StatusUnauthorized, "unauthenticated")
		assertNoSecret(t, ctx, pool, logs.String(), session, token, fake.code)
	})
}

func TestDiscordClientHidesSecrets(t *testing.T) {
	const secret = "client-secret-should-not-leak"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.RawQuery != "" || strings.Contains(r.URL.Path, secret) {
			t.Errorf("secret or query in URL %s", r.URL)
		}
		w.WriteHeader(http.StatusBadRequest)
		_, _ = io.WriteString(w, secret)
	}))
	t.Cleanup(srv.Close)
	c := auth.NewClient(auth.Settings{APIBase: srv.URL, ClientID: "cid", ClientSecret: secret, RedirectURI: "https://cpanel.example/auth/discord/callback"})
	_, err := c.Exchange(context.Background(), "code-value")
	if err == nil || strings.Contains(err.Error(), secret) || strings.Contains(err.Error(), "code-value") {
		t.Fatalf("err %v", err)
	}
}

func testAuthSettings() auth.Settings {
	return auth.Settings{
		PublicOrigin: "https://cpanel.example",
		ClientID:     "cid",
		ClientSecret: "csecret",
		BotToken:     "bottok",
		GuildID:      "guild",
		RoleID:       "role-1",
	}
}

type fakeDiscord struct {
	mu     sync.Mutex
	code   string
	user   auth.User
	roles  []string
	status int
	calls  int
}

func (f *fakeDiscord) AuthorizeURL(state string) string {
	return "https://discord.example/oauth?state=" + state
}

func (f *fakeDiscord) Exchange(_ context.Context, code string) (string, error) {
	if code != f.code {
		return "", auth.ErrUnavailable
	}
	return "access-token", nil
}

func (f *fakeDiscord) User(context.Context, string) (auth.User, error) { return f.user, nil }

func (f *fakeDiscord) Member(context.Context, string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.calls++
	switch f.status {
	case http.StatusNotFound:
		return auth.ErrNotMember
	case http.StatusServiceUnavailable:
		return auth.ErrUnavailable
	}
	for _, role := range f.roles {
		if role == "role-1" {
			return nil
		}
	}
	return auth.ErrNoRole
}

func resetAuth(t *testing.T, ctx context.Context, pool *pgxpool.Pool, fake *fakeDiscord) {
	t.Helper()
	if _, err := pool.Exec(ctx, `TRUNCATE meta.session, meta.api_token, meta.audit`); err != nil {
		t.Fatal(err)
	}
	fake.mu.Lock()
	fake.calls = 0
	fake.status = 0
	fake.roles = []string{"role-1"}
	fake.mu.Unlock()
}

func loginState(t *testing.T, h http.Handler) string {
	t.Helper()
	rec := call(h, http.MethodGet, "/auth/discord/login", "", nil)
	if rec.Code != http.StatusFound || !strings.Contains(rec.Header().Get("Location"), "discord.example") {
		t.Fatalf("login %d %q", rec.Code, rec.Header().Get("Location"))
	}
	c := setCookie(t, rec, auth.StateCookie)
	if !c.HttpOnly || !c.Secure || c.SameSite != http.SameSiteLaxMode {
		t.Fatalf("state cookie %+v", c)
	}
	return c.Value
}

func callback(t *testing.T, h http.Handler, state string) *httptest.ResponseRecorder {
	t.Helper()
	return call(h, http.MethodGet, "/auth/discord/callback?code=discord-code-0123456789abcdef&state="+state, "", http.Header{"Cookie": {auth.StateCookie + "=" + state}})
}

func call(h http.Handler, method, path, body string, header http.Header) *httptest.ResponseRecorder {
	var rdr io.Reader
	if body != "" {
		rdr = strings.NewReader(body)
	}
	req := httptest.NewRequest(method, path, rdr)
	for k, vs := range header {
		req.Header[k] = append([]string(nil), vs...)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func setCookie(t *testing.T, rec *httptest.ResponseRecorder, name string) *http.Cookie {
	t.Helper()
	for _, line := range rec.Header().Values("Set-Cookie") {
		c, err := http.ParseSetCookie(line)
		if err != nil {
			t.Fatal(err)
		}
		if c.Name == name {
			return c
		}
	}
	t.Fatalf("no cookie %s in %v", name, rec.Header().Values("Set-Cookie"))
	return nil
}

func cookieHeader(value string) http.Header {
	return http.Header{"Cookie": {auth.CookieName + "=" + value}}
}

func bearerHeader(token string) http.Header {
	return http.Header{"Authorization": {"Bearer " + token}}
}

func originHeader() http.Header {
	return http.Header{"Origin": {"https://cpanel.example"}}
}

func mergeHeaders(hs ...http.Header) http.Header {
	out := http.Header{}
	for _, h := range hs {
		for k, vs := range h {
			out[k] = append(out[k], vs...)
		}
	}
	return out
}

func sessions(t *testing.T, ctx context.Context, pool *pgxpool.Pool) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM meta.session`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func tokens(t *testing.T, ctx context.Context, pool *pgxpool.Pool) int {
	t.Helper()
	var n int
	if err := pool.QueryRow(ctx, `SELECT count(*) FROM meta.api_token`).Scan(&n); err != nil {
		t.Fatal(err)
	}
	return n
}

func assertNoSecret(t *testing.T, ctx context.Context, pool *pgxpool.Pool, logs string, secrets ...string) {
	t.Helper()
	var dump string
	err := pool.QueryRow(ctx, `
		SELECT coalesce(string_agg(row, E'\n'), '') FROM (
			SELECT encode(id_hash, 'hex') || ' ' || discord_user_id || ' ' || username || ' ' || display_name AS row FROM meta.session
			UNION ALL
			SELECT id || ' ' || encode(token_hash, 'hex') || ' ' || name || ' ' || discord_user_id || ' ' || username || ' ' || coalesce(encode(token_hash, 'escape'), '') FROM meta.api_token
			UNION ALL
			SELECT actor || ' ' || action || ' ' || target || ' ' || coalesce(params::text, '') || ' ' || coalesce(result::text, '') || ' ' || request_id FROM meta.audit
		) s`).Scan(&dump)
	if err != nil {
		t.Fatal(err)
	}
	for _, secret := range secrets {
		if secret != "" && (strings.Contains(dump, secret) || strings.Contains(logs, secret)) {
			t.Fatalf("secret %q leaked\ndb:\n%s\nlog:\n%s", secret, dump, logs)
		}
	}
}
