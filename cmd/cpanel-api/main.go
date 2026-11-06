// Command cpanel-api is cpanel's public HTTP server (ARCHITECTURE §4.7). It serves /healthz only; the OpenAPI
// routes, /readyz, /metrics and the SPA come with C2-03. It must not link the code that opens sealed secrets
// (I6, internal/archtest).
//
// Settings are flags whose defaults come from environment variables; a flag wins over its variable:
//
//	-listen            $CPANEL_API_LISTEN         listen address, default :8080
//	-log-level         $CPANEL_LOG_LEVEL          trace, debug, info, warn or error, default info
//	-shutdown-timeout  $CPANEL_SHUTDOWN_TIMEOUT   how long to wait for in-flight requests after SIGTERM, default 10s
//
// The effective settings are logged at startup, as JSON on stderr. /healthz is 200 while the process is up.
// SIGTERM and SIGINT stop accepting, wait for in-flight requests up to -shutdown-timeout, and exit 0.
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
)

const name = "cpanel-api"

type options struct {
	listen          string
	logLevel        logger.Level
	shutdownTimeout time.Duration
}

func parseArgs(args []string, getenv func(string) string) (options, *config.Set, error) {
	opt := options{logLevel: logger.DefaultLevel}
	set := config.New(name, getenv)
	set.String(&opt.listen, "listen", "CPANEL_API_LISTEN", ":8080", "listen address")
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

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	if err := httpserver.ListenAndServe(ctx, log, opt.listen, httpserver.NewMux(), opt.shutdownTimeout); err != nil {
		log.Error().Err(err).Msg("exit")
		os.Exit(1)
	}
	log.Info().Msg("stopped")
}
