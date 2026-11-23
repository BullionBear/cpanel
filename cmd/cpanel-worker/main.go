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
//	-sealbox-private-key $CPANEL_SEALBOX_PRIVATE_KEY  X25519 private key, standard base64; required when -database is set
//	-sealbox-previous-private-key $CPANEL_SEALBOX_PREVIOUS_PRIVATE_KEY  previous X25519 private key; set only while credentials are resealed
//
// Every secret may instead come from a file named by $NAME_FILE, which is how compose mounts them (C2-D008).
// A private key file must be mode 0600. The key is not logged. Opening tries the current key, then the previous
// one. cpanel-api seals with the current public key only.
//
//	cpanel-worker [flags] doctor
//	cpanel-worker keygen <path>
//	cpanel-worker [flags] rebuild -account <id> -venue <v> -data trade|order|funding
//
// doctor calls mftiko GET /accounts, SYM's grpc.health.v1 Check and SYM Revisions, prints one line per check and
// exits 0 when all pass, 1 otherwise. It needs no database and starts no server. It does not open secrets, so it
// does not require the sealbox private key.
//
// keygen writes a new private key to path, mode 0600, and prints only the public key. It refuses a path that
// already exists. The private key is not printed.
//
// rebuild deletes one account's ledger rows for a venue and a data kind, and writes them again from raw with the
// registered normalizer (C7-01). It does not call an exchange and does not move a stream cursor. It needs the
// database and no sealbox key. No venue normalizer is registered until C7-03.
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
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/collectors"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/BullionBear/cpanel/internal/credential"
	"github.com/BullionBear/cpanel/internal/doctor"
	"github.com/BullionBear/cpanel/internal/history"
	"github.com/BullionBear/cpanel/internal/mftiko"
	"github.com/BullionBear/cpanel/internal/mirror"
	"github.com/BullionBear/cpanel/internal/platform/config"
	"github.com/BullionBear/cpanel/internal/platform/httpserver"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/internal/platform/sealbox/open"
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
	sealboxPrivate    string
	sealboxPrevious   string
	doctor            bool
	keygenPath        string
	rebuild           bool
	rebuildAccount    int64
	rebuildVenue      string
	rebuildData       string
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
	set.Secret(&opt.sealboxPrivate, "sealbox-private-key", "CPANEL_SEALBOX_PRIVATE_KEY", "X25519 private key, standard base64")
	set.Secret(&opt.sealboxPrevious, "sealbox-previous-private-key", "CPANEL_SEALBOX_PREVIOUS_PRIVATE_KEY", "previous X25519 private key, standard base64; set while credentials are resealed")
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
	case len(rest) == 2 && rest[0] == "keygen":
		opt.keygenPath = rest[1]
	case len(rest) == 1 && rest[0] == "keygen":
		return options{}, nil, errors.New("keygen needs the path of a new private key file")
	case len(rest) >= 1 && rest[0] == "rebuild":
		account, venue, data, err := parseRebuild(rest[1:])
		if err != nil {
			return options{}, nil, err
		}
		opt.rebuild = true
		opt.rebuildAccount = account
		opt.rebuildVenue = venue
		opt.rebuildData = data
	default:
		return options{}, nil, fmt.Errorf("unexpected argument %q; commands are doctor, keygen and rebuild", rest[0])
	}
	return opt, set, nil
}

func parseRebuild(args []string) (account int64, venue, data string, err error) {
	fs := flag.NewFlagSet("rebuild", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	fs.Int64Var(&account, "account", 0, "exchange account id")
	fs.StringVar(&venue, "venue", "", "venue name, as SYM spells it")
	fs.StringVar(&data, "data", "", "trade, order or funding")
	if err = fs.Parse(args); err != nil {
		return 0, "", "", err
	}
	if fs.NArg() != 0 {
		return 0, "", "", fmt.Errorf("unexpected argument %q", fs.Arg(0))
	}
	if account < 1 {
		return 0, "", "", errors.New("rebuild -account is required")
	}
	if venue == "" {
		return 0, "", "", errors.New("rebuild -venue is required")
	}
	switch data {
	case "trade", "order", "funding":
	default:
		return 0, "", "", fmt.Errorf("rebuild -data must be trade, order or funding, got %q", data)
	}
	return account, venue, data, nil
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
	if opt.keygenPath != "" {
		if err := keygen(opt.keygenPath); err != nil {
			fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
			os.Exit(1)
		}
		return
	}
	if opt.rebuild {
		if err := runRebuild(context.Background(), opt); err != nil {
			fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
			if errors.Is(err, migrations.ErrDatabaseURLEmpty) {
				os.Exit(2)
			}
			os.Exit(1)
		}
		return
	}
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
	key, prev, err := loadSealboxPrivate(opt)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
		os.Exit(2)
	}
	ev := log.Info().Dict("config", set.Dict())
	if key != nil {
		fp := key.Public().Fingerprint()
		ev = ev.Str("sealbox_fingerprint", hex.EncodeToString(fp[:]))
	}
	if prev != nil {
		fp := prev.Public().Fingerprint()
		ev = ev.Str("sealbox_previous_fingerprint", hex.EncodeToString(fp[:]))
	}
	ev.Msg("starting")
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
	if err := credential.CheckRecipients(ctx, pool, key, prev); err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
		log.Error().Err(err).Msg("sealbox")
		os.Exit(1)
	}
	reg := prometheus.NewRegistry()
	reg.MustRegister(collectors.NewGoCollector(), collectors.NewProcessCollector(collectors.ProcessCollectorOpts{}))
	// C6-02 dispatches only a kind this worker can snapshot. C6-03 adds the first
	// handler and its kind here. Empty inserts no snapshot jobs.
	var snapshotKinds []string
	reg.MustRegister(newOpsCollector(pool, log, snapshotKinds))
	rules := reconcile.Rules(snapshotKinds)
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
	handlers := worker.Handlers{
		reconcile.MaintPartitions.Name: worker.MaintPartitions,
		reconcile.MftikoSync.Name:      mirror.MftikoSync(accounts),
		reconcile.SymSync.Name:         mirror.SymSync(symbols),
	}
	if key != nil {
		handlers[reconcile.CredentialVerifyKind] = credential.Verify{DB: pool, Key: key, Previous: prev}.Handle
		handlers[reconcile.CredentialProbeKind] = credential.Probe{DB: pool, Key: key, Previous: prev}.Handle
		handlers[reconcile.CredentialRekey.Name] = credential.Rekey{DB: pool, Current: key, Previous: prev}.Handle
	}
	jobs := &worker.Worker{
		DB: pool, Slots: opt.jobSlots, Grace: opt.shutdownTimeout, Egress: opt.egress, Log: log,
		Local:          []string{reconcile.MftikoSync.Name, reconcile.SymSync.Name, reconcile.CredentialRekey.Name},
		Handlers:       handlers,
		CredentialHook: credential.ApplyAuth,
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

// keygen writes a new private key and prints the public key. The private key stays in the file.
func keygen(path string) error {
	k, err := open.GenerateKey()
	if err != nil {
		return err
	}
	if err := open.WriteKeyFile(path, k); err != nil {
		return err
	}
	fmt.Println(k.Public().String())
	return nil
}

// loadSealboxPrivate parses the worker private key when a database URL is set, and the previous
// private key when one is configured. doctor and keygen do not call it. A file named by
// $CPANEL_SEALBOX_PRIVATE_KEY_FILE or $CPANEL_SEALBOX_PREVIOUS_PRIVATE_KEY_FILE must be mode 0600.
func loadSealboxPrivate(opt options) (current, previous *open.PrivateKey, err error) {
	if opt.database == "" {
		return nil, nil, nil
	}
	if strings.TrimSpace(opt.sealboxPrivate) == "" {
		return nil, nil, errors.New("sealbox private key is required ($CPANEL_SEALBOX_PRIVATE_KEY or $CPANEL_SEALBOX_PRIVATE_KEY_FILE)")
	}
	if err := checkKeyFileEnv("CPANEL_SEALBOX_PRIVATE_KEY_FILE"); err != nil {
		return nil, nil, err
	}
	current, err = open.ParsePrivateKey(opt.sealboxPrivate)
	if err != nil {
		return nil, nil, err
	}
	if strings.TrimSpace(opt.sealboxPrevious) == "" {
		return current, nil, nil
	}
	if err := checkKeyFileEnv("CPANEL_SEALBOX_PREVIOUS_PRIVATE_KEY_FILE"); err != nil {
		return nil, nil, err
	}
	previous, err = open.ParsePrivateKey(opt.sealboxPrevious)
	if err != nil {
		return nil, nil, err
	}
	if previous.Public() == current.Public() {
		return nil, nil, errors.New("sealbox: CPANEL_SEALBOX_PREVIOUS_PRIVATE_KEY is the current private key")
	}
	return current, previous, nil
}

func checkKeyFileEnv(name string) error {
	path := os.Getenv(name)
	if path == "" {
		return nil
	}
	return open.CheckKeyFile(path)
}

// requireSchema checks the database version. It does not migrate. An empty URL is a configuration
// error; a database that is behind, drifted, or unreachable is a failed startup.
func runRebuild(ctx context.Context, opt options) error {
	if err := requireSchema(opt.database); err != nil {
		return err
	}
	pool, err := pgxpool.New(ctx, opt.database)
	if err != nil {
		return err
	}
	defer pool.Close()
	kind := history.Kind(opt.rebuildData)
	n, ok := history.Lookup(opt.rebuildVenue, kind)
	if !ok {
		return fmt.Errorf("no %s normalizer for %s", opt.rebuildData, opt.rebuildVenue)
	}
	return history.Rebuild(ctx, pool, n, opt.rebuildAccount)
}

func requireSchema(database string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return migrations.RequireCurrent(ctx, database)
}
