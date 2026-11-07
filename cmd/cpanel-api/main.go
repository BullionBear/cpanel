// Command cpanel-api is cpanel's public HTTP server (ARCHITECTURE §4.7). It serves the OpenAPI routes
// (/healthz, /readyz, /metrics) and the embedded SPA. It must not link the code that opens sealed secrets
// (I6, internal/archtest).
//
// Settings are flags whose defaults come from environment variables; a flag wins over its variable:
//
//	-listen            $CPANEL_API_LISTEN         listen address, default :8080
//	-database          $CPANEL_DATABASE_URL       PostgreSQL URL; required, logged as ***
//	-log-level         $CPANEL_LOG_LEVEL          trace, debug, info, warn or error, default info
//	-shutdown-timeout  $CPANEL_SHUTDOWN_TIMEOUT   how long to wait for in-flight requests after SIGTERM, default 10s
//	-request-timeout   $CPANEL_REQUEST_TIMEOUT    per-request limit, default 30s; 0 disables it
//
// The effective settings are logged at startup, as JSON on stderr. Startup does not migrate: the database
// must already be at the embedded version (cpanel-migrate up), or the process refuses to start and says why.
// /healthz is 200 while the process is up and does not touch the database. /readyz is 200 only when the
// database is reachable and the schema is current. SIGTERM and SIGINT stop accepting, wait for in-flight
// requests up to -shutdown-timeout, and exit 0.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/BullionBear/cpanel/internal/api"
	"github.com/BullionBear/cpanel/internal/platform/config"
	"github.com/BullionBear/cpanel/internal/platform/httpserver"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/migrations"
)

const name = "cpanel-api"

type options struct {
	listen          string
	database        string
	logLevel        logger.Level
	shutdownTimeout time.Duration
	requestTimeout  time.Duration
}

func parseArgs(args []string, getenv func(string) string) (options, *config.Set, error) {
	opt := options{logLevel: logger.DefaultLevel}
	set := config.New(name, getenv)
	set.String(&opt.listen, "listen", "CPANEL_API_LISTEN", ":8080", "listen address")
	set.Secret(&opt.database, "database", "CPANEL_DATABASE_URL", "PostgreSQL URL")
	set.Var(&opt.logLevel, "log-level", "CPANEL_LOG_LEVEL", "trace, debug, info, warn or error")
	set.Duration(&opt.shutdownTimeout, "shutdown-timeout", "CPANEL_SHUTDOWN_TIMEOUT", 10*time.Second,
		"how long to wait for in-flight requests after SIGTERM")
	set.Duration(&opt.requestTimeout, "request-timeout", "CPANEL_REQUEST_TIMEOUT", 30*time.Second,
		"per-request limit; 0 disables it")
	if err := set.Parse(args); err != nil {
		return options{}, nil, err
	}
	return opt, set, nil
}

func main() {
	opt, set, err := parseArgs(os.Args[1:], os.Getenv)
	if errors.Is(err, flag.ErrHelp) {
		return
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
		os.Exit(2)
	}
	log := logger.New(os.Stderr, name, opt.logLevel)
	log.Info().Dict("config", set.Dict()).Msg("starting")
	if err := requireSchema(opt.database); err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
		if !errors.Is(err, migrations.ErrDatabaseURLEmpty) {
			log.Error().Err(err).Msg("schema")
		}
		if errors.Is(err, migrations.ErrDatabaseURLEmpty) {
			os.Exit(2)
		}
		os.Exit(1)
	}

	pool, err := pgxpool.New(context.Background(), opt.database)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
		log.Error().Err(err).Msg("database")
		os.Exit(1)
	}
	defer pool.Close()

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	h := api.Handler(api.Config{Log: log, Pool: pool, Database: opt.database, Timeout: opt.requestTimeout})
	if err := httpserver.ListenAndServe(ctx, log, opt.listen, h, opt.shutdownTimeout); err != nil {
		log.Error().Err(err).Msg("exit")
		os.Exit(1)
	}
	log.Info().Msg("stopped")
}

// requireSchema checks the database version. It does not migrate. An empty URL is a configuration
// error; a database that is behind, drifted, or unreachable is a failed startup.
func requireSchema(database string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return migrations.RequireCurrent(ctx, database)
}
