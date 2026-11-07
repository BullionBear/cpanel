// Package api is cpanel-api's HTTP stack: the OpenAPI strict server, middleware, and the embedded SPA.
package api

import (
	"context"
	"fmt"
	"io/fs"
	"net/http"
	"strings"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/rs/zerolog"

	cpanelv1 "github.com/BullionBear/cpanel/contract/gen/cpanel/v1"
	"github.com/BullionBear/cpanel/internal/platform/buildinfo"
	"github.com/BullionBear/cpanel/migrations"
	"github.com/BullionBear/cpanel/web"
)

// Config is what Handler serves with.
type Config struct {
	Log      zerolog.Logger
	Pool     *pgxpool.Pool
	Database string        // PostgreSQL URL used by /readyz; never written to a response
	Timeout  time.Duration // request timeout; zero disables it
}

// Handler is /healthz, /readyz, /metrics, and the SPA for every other path.
func Handler(cfg Config) http.Handler {
	s := &server{
		log:     cfg.Log,
		pool:    cfg.Pool,
		dsn:     cfg.Database,
		metrics: promhttp.Handler(),
	}
	strict := cpanelv1.NewStrictHandlerWithOptions(s, nil, cpanelv1.StrictHTTPServerOptions{
		RequestErrorHandlerFunc: func(w http.ResponseWriter, r *http.Request, err error) {
			cfg.Log.Error().Err(err).Str("request_id", requestIDFrom(r.Context())).Msg("bad request")
			writeProblem(w, http.StatusBadRequest, "bad_request", "Bad Request", "")
		},
		ResponseErrorHandlerFunc: func(w http.ResponseWriter, r *http.Request, err error) {
			cfg.Log.Error().Err(err).Str("request_id", requestIDFrom(r.Context())).Msg("response error")
			writeProblem(w, http.StatusInternalServerError, "internal", "Internal Server Error", "")
		},
	})
	apiHandler := cpanelv1.Handler(strict)
	mux := http.NewServeMux()
	mux.Handle("/healthz", getOnly(apiHandler))
	mux.Handle("/readyz", getOnly(apiHandler))
	mux.Handle("/metrics", getOnly(apiHandler))
	mux.Handle("/", spa(siteFiles()))
	return wrap(cfg.Log, cfg.Timeout, mux)
}

func siteFiles() fs.FS {
	sub, err := fs.Sub(web.Build, "build")
	if err != nil {
		panic(err)
	}
	return sub
}

type server struct {
	log     zerolog.Logger
	pool    *pgxpool.Pool
	dsn     string
	metrics http.Handler
}

var _ cpanelv1.StrictServerInterface = (*server)(nil)

func (s *server) GetHealthz(context.Context, cpanelv1.GetHealthzRequestObject) (cpanelv1.GetHealthzResponseObject, error) {
	return cpanelv1.GetHealthz200JSONResponse{
		Ok:      true,
		Version: buildinfo.Version,
		Commit:  buildinfo.Commit,
	}, nil
}

func (s *server) GetReadyz(ctx context.Context, _ cpanelv1.GetReadyzRequestObject) (cpanelv1.GetReadyzResponseObject, error) {
	if s.pool == nil || strings.TrimSpace(s.dsn) == "" {
		return notReady("database is not configured"), nil
	}
	if err := s.pool.Ping(ctx); err != nil {
		s.log.Error().Err(err).Msg("readyz")
		return notReady("database is not reachable"), nil
	}
	if err := migrations.RequireCurrent(ctx, s.dsn); err != nil {
		s.log.Error().Err(err).Msg("readyz")
		return notReady("database schema is not current"), nil
	}
	return cpanelv1.GetReadyz200JSONResponse{Ok: true}, nil
}

func (s *server) GetMetrics(ctx context.Context, _ cpanelv1.GetMetricsRequestObject) (cpanelv1.GetMetricsResponseObject, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, "/metrics", nil)
	if err != nil {
		return nil, err
	}
	rec := newMemWriter()
	s.metrics.ServeHTTP(rec, req)
	if rec.code != http.StatusOK {
		return nil, fmt.Errorf("metrics: status %d", rec.code)
	}
	return cpanelv1.GetMetrics200TextResponse(string(rec.body)), nil
}

func notReady(detail string) cpanelv1.GetReadyz503ApplicationProblemPlusJSONResponse {
	return cpanelv1.GetReadyz503ApplicationProblemPlusJSONResponse(problemValue(http.StatusServiceUnavailable, "unavailable", "Not Ready", detail))
}
