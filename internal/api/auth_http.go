package api

import (
	"context"
	"crypto/subtle"
	"errors"
	"net/http"
	"strings"
	"time"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
	"github.com/BullionBear/cpanel/internal/auth"
)

type reqKey struct{}

type principal struct {
	kind       string // session or token
	user       auth.User
	tokenID    string
	rawSession string // the cookie value; never log it or put it in audit
}

func (p principal) auditID() string {
	if p.kind == "token" {
		return "token:" + p.tokenID
	}
	return "discord:" + p.user.ID
}

func (p principal) me() cpanelv1.Me {
	return cpanelv1.Me{
		Auth:          cpanelv1.MeAuth(p.kind),
		DiscordUserId: p.user.ID,
		Username:      p.user.Username,
		DisplayName:   p.user.DisplayName,
	}
}

func (s *server) withRequest(next cpanelv1.StrictHandlerFunc, _ string) cpanelv1.StrictHandlerFunc {
	return func(ctx context.Context, w http.ResponseWriter, r *http.Request, request any) (any, error) {
		ctx = context.WithValue(ctx, reqKey{}, r)
		return next(ctx, w, r, request)
	}
}

func requestFrom(ctx context.Context) *http.Request {
	r, _ := ctx.Value(reqKey{}).(*http.Request)
	return r
}

func (s *server) LoginDiscord(ctx context.Context, _ cpanelv1.LoginDiscordRequestObject) (cpanelv1.LoginDiscordResponseObject, error) {
	if s.discord == nil || !s.settings.Configured() {
		p := problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "discord is not configured")
		return cpanelv1.LoginDiscord503ApplicationProblemPlusJSONResponse(p), nil
	}
	state, err := auth.NewSecret(32)
	if err != nil {
		return nil, err
	}
	addCookie(ctx, &http.Cookie{
		Name: auth.StateCookie, Value: state, Path: "/auth/discord/callback",
		MaxAge: 600, HttpOnly: true, Secure: true, SameSite: http.SameSiteLaxMode,
	})
	return cpanelv1.LoginDiscord302Response{Headers: cpanelv1.LoginDiscord302ResponseHeaders{
		Location: s.discord.AuthorizeURL(state),
	}}, nil
}

func (s *server) CallbackDiscord(ctx context.Context, req cpanelv1.CallbackDiscordRequestObject) (cpanelv1.CallbackDiscordResponseObject, error) {
	r := requestFrom(ctx)
	addCookie(ctx, &http.Cookie{
		Name: auth.StateCookie, Value: "", Path: "/auth/discord/callback",
		MaxAge: -1, HttpOnly: true, Secure: true, SameSite: http.SameSiteLaxMode,
	})
	state, err := r.Cookie(auth.StateCookie)
	if err != nil || subtle.ConstantTimeCompare([]byte(state.Value), []byte(req.Params.State)) != 1 {
		s.audit(ctx, "anonymous", "login", "session", nil, map[string]any{"status": http.StatusBadRequest})
		p := problemValue(http.StatusBadRequest, "bad_request", "Bad Request", "state does not match")
		return cpanelv1.CallbackDiscord400ApplicationProblemPlusJSONResponse(p), nil
	}
	if s.discord == nil {
		p := problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "discord is not configured")
		return cpanelv1.CallbackDiscorddefaultApplicationProblemPlusJSONResponse{Body: p, StatusCode: http.StatusServiceUnavailable}, nil
	}
	access, err := s.discord.Exchange(ctx, req.Params.Code)
	if err != nil {
		s.audit(ctx, "anonymous", "login", "session", nil, map[string]any{"status": http.StatusUnauthorized})
		p := problemValue(http.StatusUnauthorized, "unauthenticated", "Unauthenticated", "discord login failed")
		return cpanelv1.CallbackDiscorddefaultApplicationProblemPlusJSONResponse{Body: p, StatusCode: http.StatusUnauthorized}, nil
	}
	user, err := s.discord.User(ctx, access)
	if err != nil {
		s.audit(ctx, "anonymous", "login", "session", nil, map[string]any{"status": http.StatusUnauthorized})
		p := problemValue(http.StatusUnauthorized, "unauthenticated", "Unauthenticated", "discord login failed")
		return cpanelv1.CallbackDiscorddefaultApplicationProblemPlusJSONResponse{Body: p, StatusCode: http.StatusUnauthorized}, nil
	}
	if err := s.discord.Member(ctx, user.ID); err != nil {
		detail := "discord login failed"
		if errors.Is(err, auth.ErrNotMember) {
			detail = "not a member of the required Discord server"
		}
		if errors.Is(err, auth.ErrNoRole) {
			detail = "the required Discord role is missing"
		}
		if errors.Is(err, auth.ErrNotMember) || errors.Is(err, auth.ErrNoRole) {
			s.audit(ctx, "discord:"+user.ID, "login", "session", nil, map[string]any{"status": http.StatusForbidden})
			p := problemValue(http.StatusForbidden, "forbidden", "Forbidden", detail)
			return cpanelv1.CallbackDiscord403ApplicationProblemPlusJSONResponse(p), nil
		}
		s.audit(ctx, "discord:"+user.ID, "login", "session", nil, map[string]any{"status": http.StatusServiceUnavailable})
		p := problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "discord is not reachable")
		return cpanelv1.CallbackDiscorddefaultApplicationProblemPlusJSONResponse{Body: p, StatusCode: http.StatusServiceUnavailable}, nil
	}
	raw, err := auth.NewSecret(32)
	if err != nil {
		return nil, err
	}
	if err := s.store.CreateSession(ctx, raw, user, s.sessionTTL); err != nil {
		return nil, err
	}
	addCookie(ctx, &http.Cookie{
		Name: auth.CookieName, Value: raw, Path: "/",
		MaxAge: int(s.sessionTTL.Seconds()), HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode,
	})
	s.audit(ctx, "discord:"+user.ID, "login", "session", nil, map[string]any{"status": http.StatusFound})
	return cpanelv1.CallbackDiscord302Response{Headers: cpanelv1.CallbackDiscord302ResponseHeaders{Location: "/"}}, nil
}

func (s *server) Logout(ctx context.Context, _ cpanelv1.LogoutRequestObject) (cpanelv1.LogoutResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.Logout401ApplicationProblemPlusJSONResponse{
			UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem(""),
		}, nil
	case gateCSRF:
		return cpanelv1.Logout403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		body := problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "discord is not reachable")
		return cpanelv1.LogoutdefaultApplicationProblemPlusJSONResponse{Body: body, StatusCode: http.StatusServiceUnavailable}, nil
	}
	if p.kind == "session" {
		if err := s.store.DeleteSession(ctx, p.rawSession); err != nil {
			return nil, err
		}
	}
	addCookie(ctx, &http.Cookie{
		Name: auth.CookieName, Value: "", Path: "/",
		MaxAge: -1, HttpOnly: true, Secure: true, SameSite: http.SameSiteStrictMode,
	})
	s.audit(ctx, p.auditID(), "logout", "session", nil, map[string]any{"status": http.StatusNoContent})
	return cpanelv1.Logout204Response{}, nil
}

func (s *server) GetMe(ctx context.Context, _ cpanelv1.GetMeRequestObject) (cpanelv1.GetMeResponseObject, error) {
	p, kind := s.gate(ctx, false)
	switch kind {
	case gateUnauth:
		return cpanelv1.GetMe401ApplicationProblemPlusJSONResponse{
			UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem(""),
		}, nil
	case gateUnavailable:
		body := problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "discord is not reachable")
		return cpanelv1.GetMedefaultApplicationProblemPlusJSONResponse{Body: body, StatusCode: http.StatusServiceUnavailable}, nil
	}
	return cpanelv1.GetMe200JSONResponse(p.me()), nil
}

func (s *server) ListTokens(ctx context.Context, _ cpanelv1.ListTokensRequestObject) (cpanelv1.ListTokensResponseObject, error) {
	p, kind := s.gate(ctx, false)
	switch kind {
	case gateUnauth:
		return cpanelv1.ListTokens401ApplicationProblemPlusJSONResponse{
			UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem(""),
		}, nil
	case gateUnavailable:
		body := problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "discord is not reachable")
		return cpanelv1.ListTokensdefaultApplicationProblemPlusJSONResponse{Body: body, StatusCode: http.StatusServiceUnavailable}, nil
	}
	rows, err := s.store.Tokens(ctx, p.user.ID)
	if err != nil {
		return nil, err
	}
	out := make(cpanelv1.ListTokens200JSONResponse, 0, len(rows))
	for _, row := range rows {
		out = append(out, cpanelv1.Token{Id: row.ID, Name: row.Name, CreatedAt: row.CreatedAt, LastUsedAt: row.LastUsedAt})
	}
	return out, nil
}

func (s *server) CreateToken(ctx context.Context, req cpanelv1.CreateTokenRequestObject) (cpanelv1.CreateTokenResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.CreateToken401ApplicationProblemPlusJSONResponse{
			UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem(""),
		}, nil
	case gateCSRF:
		return cpanelv1.CreateToken403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		body := problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "discord is not reachable")
		return cpanelv1.CreateTokendefaultApplicationProblemPlusJSONResponse{Body: body, StatusCode: http.StatusServiceUnavailable}, nil
	}
	name := ""
	if req.Body != nil {
		name = strings.TrimSpace(req.Body.Name)
	}
	if name == "" || len(name) > 80 {
		body := problemValue(http.StatusBadRequest, "bad_request", "Bad Request", "name is required")
		return cpanelv1.CreateToken400ApplicationProblemPlusJSONResponse(body), nil
	}
	raw, err := auth.NewToken()
	if err != nil {
		return nil, err
	}
	row, err := s.store.CreateToken(ctx, p.user.ID, p.user.Username, name, raw)
	if err != nil {
		return nil, err
	}
	s.audit(ctx, p.auditID(), "create_token", "token:"+row.ID, map[string]any{"name": name}, map[string]any{"status": http.StatusCreated})
	return cpanelv1.CreateToken201JSONResponse{Id: row.ID, Name: row.Name, CreatedAt: row.CreatedAt, Token: raw}, nil
}

func (s *server) DeleteToken(ctx context.Context, req cpanelv1.DeleteTokenRequestObject) (cpanelv1.DeleteTokenResponseObject, error) {
	p, kind := s.gate(ctx, true)
	switch kind {
	case gateUnauth:
		return cpanelv1.DeleteToken401ApplicationProblemPlusJSONResponse{
			UnauthenticatedApplicationProblemPlusJSONResponse: unauthProblem(""),
		}, nil
	case gateCSRF:
		return cpanelv1.DeleteToken403ApplicationProblemPlusJSONResponse(forbidden("origin is not allowed")), nil
	case gateUnavailable:
		body := problemValue(http.StatusServiceUnavailable, "unavailable", "Unavailable", "discord is not reachable")
		return cpanelv1.DeleteTokendefaultApplicationProblemPlusJSONResponse{Body: body, StatusCode: http.StatusServiceUnavailable}, nil
	}
	if err := s.store.RevokeToken(ctx, p.user.ID, req.Id); err != nil {
		if errors.Is(err, auth.ErrNotFound) {
			body := problemValue(http.StatusNotFound, "not_found", "Not Found", "token not found")
			return cpanelv1.DeleteToken404ApplicationProblemPlusJSONResponse(body), nil
		}
		return nil, err
	}
	s.audit(ctx, p.auditID(), "revoke_token", "token:"+req.Id, nil, map[string]any{"status": http.StatusNoContent})
	return cpanelv1.DeleteToken204Response{}, nil
}

const (
	gateOK = iota
	gateUnauth
	gateCSRF
	gateUnavailable
)

// gate authenticates the request. Mutating cookie requests must send the configured Origin.
// A bearer token is never combined with the cookie: a bad bearer is rejected even if a cookie is present.
func (s *server) gate(ctx context.Context, mutating bool) (principal, int) {
	r := requestFrom(ctx)
	p, err := s.authenticate(ctx, r)
	if errors.Is(err, errUnauth) {
		return principal{}, gateUnauth
	}
	if err != nil {
		return principal{}, gateUnavailable
	}
	if mutating && p.kind == "session" && !s.originOK(r) {
		s.audit(ctx, p.auditID(), "csrf_rejected", r.URL.Path, map[string]any{"origin": r.Header.Get("Origin")}, map[string]any{"status": http.StatusForbidden})
		return principal{}, gateCSRF
	}
	return p, gateOK
}

var errUnauth = errors.New("unauthenticated")

func (s *server) authenticate(ctx context.Context, r *http.Request) (principal, error) {
	if h := r.Header.Get("Authorization"); h != "" {
		token, ok := strings.CutPrefix(h, "Bearer ")
		if !ok || token == "" || strings.Contains(token, " ") {
			return principal{}, errUnauth
		}
		row, err := s.store.UseToken(ctx, token)
		if errors.Is(err, auth.ErrNotFound) {
			return principal{}, errUnauth
		}
		if err != nil {
			return principal{}, err
		}
		return principal{
			kind: "token", tokenID: row.ID,
			user: auth.User{ID: row.DiscordUserID, Username: row.Username, DisplayName: row.Username},
		}, nil
	}
	c, err := r.Cookie(auth.CookieName)
	if err != nil || c.Value == "" {
		return principal{}, errUnauth
	}
	sess, err := s.store.Session(ctx, c.Value)
	if errors.Is(err, auth.ErrNotFound) {
		return principal{}, errUnauth
	}
	if err != nil {
		return principal{}, err
	}
	if s.roleRecheck > 0 && time.Since(sess.RoleCheckedAt) >= s.roleRecheck {
		if s.discord == nil {
			return principal{}, auth.ErrUnavailable
		}
		if err := s.discord.Member(ctx, sess.DiscordUserID); err != nil {
			if errors.Is(err, auth.ErrNotMember) || errors.Is(err, auth.ErrNoRole) {
				_ = s.store.DeleteSession(ctx, c.Value)
				return principal{}, errUnauth
			}
			return principal{}, err
		}
		if err := s.store.TouchRole(ctx, c.Value); err != nil {
			return principal{}, err
		}
	}
	return principal{
		kind: "session", rawSession: c.Value,
		user: auth.User{ID: sess.DiscordUserID, Username: sess.Username, DisplayName: sess.DisplayName},
	}, nil
}

func (s *server) originOK(r *http.Request) bool {
	origin := s.settings.Origin()
	return origin != "" && r.Header.Get("Origin") == origin
}

func (s *server) audit(ctx context.Context, actor, action, target string, params, result any) {
	if s.store == nil || s.store.Pool == nil {
		return
	}
	if err := s.store.Audit(ctx, actor, action, target, params, result, requestIDFrom(ctx)); err != nil {
		s.log.Error().Err(err).Str("action", action).Msg("audit")
	}
}

func unauthProblem(detail string) cpanelv1.UnauthenticatedApplicationProblemPlusJSONResponse {
	return cpanelv1.UnauthenticatedApplicationProblemPlusJSONResponse(problemValue(http.StatusUnauthorized, "unauthenticated", "Unauthenticated", detail))
}

func forbidden(detail string) cpanelv1.Problem {
	return problemValue(http.StatusForbidden, "forbidden", "Forbidden", detail)
}
