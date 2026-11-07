// Command cpanel-worker runs cpanel's jobs: every call to mftiko, SYM and the exchanges (ARCHITECTURE §4.4). It
// serves /healthz only; the job system comes with C3. It stays on the private network.
//
// Settings are flags whose defaults come from environment variables; a flag wins over its variable:
//
//	-listen            $CPANEL_WORKER_LISTEN      listen address, default :8081
//	-database          $CPANEL_DATABASE_URL       PostgreSQL URL; required, logged as ***
//	-log-level         $CPANEL_LOG_LEVEL          trace, debug, info, warn or error, default info
//	-shutdown-timeout  $CPANEL_SHUTDOWN_TIMEOUT   how long to wait for in-flight requests after SIGTERM, default 10s
//
// The effective settings are logged at startup, as JSON on stderr. Startup does not migrate: the database
// must already be at the embedded version (cpanel-migrate up), or the process refuses to start and says why.
// /healthz is 200 while the process is up. SIGTERM and SIGINT stop accepting, wait for in-flight requests
// up to -shutdown-timeout, and exit 0.
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

	"github.com/BullionBear/cpanel/internal/platform/config"
	"github.com/BullionBear/cpanel/internal/platform/httpserver"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/migrations"
)

const name = "cpanel-worker"

type options struct {
	listen          string
	database        string
	logLevel        logger.Level
	shutdownTimeout time.Duration
}

func parseArgs(args []string, getenv func(string) string) (options, *config.Set, error) {
	opt := options{logLevel: logger.DefaultLevel}
	set := config.New(name, getenv)
	set.String(&opt.listen, "listen", "CPANEL_WORKER_LISTEN", ":8081", "listen address")
	set.Secret(&opt.database, "database", "CPANEL_DATABASE_URL", "PostgreSQL URL")
	set.Var(&opt.logLevel, "log-level", "CPANEL_LOG_LEVEL", "trace, debug, info, warn or error")
	set.Duration(&opt.shutdownTimeout, "shutdown-timeout", "CPANEL_SHUTDOWN_TIMEOUT", 10*time.Second,
		"how long to wait for in-flight requests after SIGTERM")
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

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := httpserver.ListenAndServe(ctx, log, opt.listen, httpserver.NewMux(), opt.shutdownTimeout); err != nil {
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
