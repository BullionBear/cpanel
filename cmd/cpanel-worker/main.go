// Command cpanel-worker runs cpanel's jobs: every call to mftiko, SYM and the exchanges (ARCHITECTURE §4.4). It
// runs the ReconcileLoop (internal/reconcile, §4.3), which inserts the jobs that are due, and the job runner
// (internal/worker), which takes them by lease and runs them; every replica runs both. It serves /healthz and
// /metrics and stays on the private network.
//
// Settings are flags whose defaults come from environment variables; a flag wins over its variable:
//
//	-listen            $CPANEL_WORKER_LISTEN      listen address, default :8081
//	-database          $CPANEL_DATABASE_URL       PostgreSQL URL; required, logged as ***
//	-log-level         $CPANEL_LOG_LEVEL          trace, debug, info, warn or error, default info
//	-shutdown-timeout  $CPANEL_SHUTDOWN_TIMEOUT   how long to wait for in-flight requests and job pages after SIGTERM, default 10s
//	-reconcile-interval $CPANEL_RECONCILE_INTERVAL  how often the ReconcileLoop starts a round, default 5s
//	-job-slots         $CPANEL_JOB_SLOTS          how many lease keys run jobs at once, default 4
//	-egress            $CPANEL_EGRESS             name shared by every replica behind one egress IP; an HTTP 418 pauses it
//	-mftiko-url        $CPANEL_MFTIKO_URL         mftiko-api base URL, e.g. http://100.65.26.119:8080/api/v1
//	-mftiko-token      $CPANEL_MFTIKO_TOKEN       mftiko-api bearer token; logged as ***
//	-sym-url           $CPANEL_SYM_URL            SYM gRPC listener, e.g. http://100.65.26.119:7450
//	-sym-token         $CPANEL_SYM_TOKEN          SYM gRPC bearer token; logged as ***
//
// Every secret may instead come from a file named by $NAME_FILE, which is how compose mounts them (C2-D008).
//
//	cpanel-worker [flags] doctor
//
// doctor calls mftiko GET /accounts, SYM's grpc.health.v1 Check and SYM Revisions, prints one line per check and
// exits 0 when all pass, 1 otherwise. It needs no database and starts no server.
//
// The effective settings are logged at startup, as JSON on stderr. Startup does not migrate: the database
// must already be at the embedded version (cpanel-migrate up), or the process refuses to start and says why.
// /healthz is 200 while the process is up. SIGTERM and SIGINT stop the ReconcileLoop, whose unfinished round rolls
// back, and stop taking jobs. Up to -shutdown-timeout, the job pages in progress finish and in-flight requests are
// served; then unfinished jobs go back to pending, the leases are released so another replica carries on at once,
// and the process exits 0.
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
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/BullionBear/cpanel/internal/doctor"
	"github.com/BullionBear/cpanel/internal/mftiko"
	"github.com/BullionBear/cpanel/internal/mirror"
	"github.com/BullionBear/cpanel/internal/platform/config"
	"github.com/BullionBear/cpanel/internal/platform/httpserver"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/internal/reconcile"
	"github.com/BullionBear/cpanel/internal/sym"
	"github.com/BullionBear/cpanel/internal/worker"
	"github.com/BullionBear/cpanel/migrations"
)

const name = "cpanel-worker"

type options struct {
	listen            string
	database          string
	logLevel          logger.Level
	shutdownTimeout   time.Duration
	reconcileInterval time.Duration
	jobSlots          int
	egress            string
	mftikoURL         string
	mftikoToken       string
	symURL            string
	symToken          string
	doctor            bool
}

func parseArgs(args []string, getenv func(string) string) (options, *config.Set, error) {
	opt := options{logLevel: logger.DefaultLevel}
	set := config.New(name, getenv)
	set.String(&opt.listen, "listen", "CPANEL_WORKER_LISTEN", ":8081", "listen address")
	set.Secret(&opt.database, "database", "CPANEL_DATABASE_URL", "PostgreSQL URL")
	set.Var(&opt.logLevel, "log-level", "CPANEL_LOG_LEVEL", "trace, debug, info, warn or error")
	set.Duration(&opt.shutdownTimeout, "shutdown-timeout", "CPANEL_SHUTDOWN_TIMEOUT", 10*time.Second,
		"how long to wait for in-flight requests and job pages after SIGTERM")
	set.Duration(&opt.reconcileInterval, "reconcile-interval", "CPANEL_RECONCILE_INTERVAL", reconcile.DefaultInterval,
		"how often the ReconcileLoop starts a round")
	set.Int(&opt.jobSlots, "job-slots", "CPANEL_JOB_SLOTS", worker.DefaultSlots, "how many lease keys run jobs at once")
	set.String(&opt.egress, "egress", "CPANEL_EGRESS", "", "name shared by every replica behind one egress IP; an HTTP 418 pauses it")
	set.String(&opt.mftikoURL, "mftiko-url", "CPANEL_MFTIKO_URL", "", "mftiko-api base URL")
	set.Secret(&opt.mftikoToken, "mftiko-token", "CPANEL_MFTIKO_TOKEN", "mftiko-api bearer token")
	set.String(&opt.symURL, "sym-url", "CPANEL_SYM_URL", "", "SYM gRPC listener URL")
	set.Secret(&opt.symToken, "sym-token", "CPANEL_SYM_TOKEN", "SYM gRPC bearer token")
	rest, err := set.ParseRemaining(args)
	if err != nil {
		return options{}, nil, err
	}
	if opt.reconcileInterval <= 0 {
		return options{}, nil, fmt.Errorf("-reconcile-interval ($CPANEL_RECONCILE_INTERVAL) must be positive, got %s", opt.reconcileInterval)
	}
	if opt.jobSlots < 1 {
		return options{}, nil, fmt.Errorf("-job-slots ($CPANEL_JOB_SLOTS) must be at least 1, got %d", opt.jobSlots)
	}
	switch {
	case len(rest) == 0:
	case len(rest) == 1 && rest[0] == "doctor":
		opt.doctor = true
	default:
		return options{}, nil, fmt.Errorf("unexpected argument %q; the only command is doctor", rest[0])
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
	if opt.doctor {
		ctx, cancel := context.WithTimeout(context.Background(), time.Minute)
		defer cancel()
		results := doctor.Run(ctx, doctor.Config{
			MftikoURL: opt.mftikoURL, MftikoToken: opt.mftikoToken, SymURL: opt.symURL, SymToken: opt.symToken,
		})
		if !doctor.Write(os.Stdout, results) {
			os.Exit(1)
		}
		return
	}
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
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	reg.MustRegister(newOpsCollector(pool, log))
	rules := reconcile.Rules()
	loop := &reconcile.Loop{
		DB: pool, Rules: rules, Interval: opt.reconcileInterval, Log: log,
		Observe: newReconcileMetrics(reg, rules).observe,
	}
	looping := make(chan struct{})
	go func() {
		defer close(looping)
		loop.Run(ctx)
	}()
	var accounts mirror.Lister
	if client, err := mftiko.New(opt.mftikoURL, opt.mftikoToken, nil); err != nil {
		log.Warn().Err(err).Msg("mftiko client")
	} else {
		accounts = client
	}
	var symbols mirror.Catalog
	if client, err := sym.New(opt.symURL, opt.symToken); err != nil {
		log.Warn().Err(err).Msg("sym client")
	} else {
		symbols = client
	}
	jobs := &worker.Worker{
		DB: pool, Slots: opt.jobSlots, Grace: opt.shutdownTimeout, Egress: opt.egress, Log: log,
		Local: []string{reconcile.MftikoSync.Name, reconcile.SymSync.Name},
		Handlers: worker.Handlers{
			reconcile.MaintPartitions.Name: worker.MaintPartitions,
			reconcile.MftikoSync.Name:      mirror.MftikoSync(accounts),
			reconcile.SymSync.Name:         mirror.SymSync(symbols),
		},
	}
	working := make(chan struct{})
	go func() {
		defer close(working)
		jobs.Run(ctx)
	}()

	mux := httpserver.NewMux()
	mux.Handle("GET /metrics", promhttp.HandlerFor(reg, promhttp.HandlerOpts{}))
	err = httpserver.ListenAndServe(ctx, log, opt.listen, mux, opt.shutdownTimeout)
	stop()
	<-looping
	<-working
	if err != nil {
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
