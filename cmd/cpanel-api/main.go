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
//	-public-origin     $CPANEL_PUBLIC_ORIGIN      site origin, used for the OAuth redirect and CSRF
//	-discord-client-id $CPANEL_DISCORD_CLIENT_ID  Discord application id
//	-discord-client-secret $CPANEL_DISCORD_CLIENT_SECRET  Discord application secret; logged as ***
//	-discord-bot-token $CPANEL_DISCORD_BOT_TOKEN  bot token used to read guild roles; logged as ***
//	-discord-guild-id  $CPANEL_DISCORD_GUILD_ID   guild the user must belong to
//	-discord-role-id   $CPANEL_DISCORD_ROLE_ID    role the user must have
//	-discord-redirect-uri $CPANEL_DISCORD_REDIRECT_URI  defaults to {public-origin}/auth/discord/callback
//	-session-ttl       $CPANEL_SESSION_TTL        session lifetime, default 12h
//	-role-recheck      $CPANEL_ROLE_RECHECK       how often to recheck the Discord role, default 1h
//	-sealbox-public-key $CPANEL_SEALBOX_PUBLIC_KEY  worker public key, standard base64; required when -database is set
//
// Every secret may instead come from a file named by $NAME_FILE, which is how compose mounts them (C2-D008).
// The sealbox public key is only the sealing key. This process does not link the code that opens a box (I6).
//
// The effective settings are logged at startup, as JSON on stderr. Startup does not migrate: the database
// must already be at the embedded version (cpanel-migrate up), or the process refuses to start and says why.
// Discord settings may be empty: the process still serves /healthz, and login answers 503 until they are set.
// /healthz is 200 while the process is up and does not touch the database. /readyz is 200 only when the
// database is reachable and the schema is current. SIGTERM and SIGINT stop accepting, wait for in-flight
// requests up to -shutdown-timeout, and exit 0.
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

	"github.com/BullionBear/cpanel/internal/api"
	"github.com/BullionBear/cpanel/internal/auth"
	"github.com/BullionBear/cpanel/internal/platform/config"
	"github.com/BullionBear/cpanel/internal/platform/httpserver"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/internal/platform/sealbox"
	"github.com/BullionBear/cpanel/migrations"
)

const name = "cpanel-api"

type options struct {
	listen              string
	database            string
	logLevel            logger.Level
	shutdownTimeout     time.Duration
	requestTimeout      time.Duration
	publicOrigin        string
	discordClientID     string
	discordClientSecret string
	discordBotToken     string
	discordGuildID      string
	discordRoleID       string
	discordRedirectURI  string
	sessionTTL          time.Duration
	roleRecheck         time.Duration
	sealboxPublic       string
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
	set.String(&opt.publicOrigin, "public-origin", "CPANEL_PUBLIC_ORIGIN", "", "site origin, used for the OAuth redirect and CSRF")
	set.String(&opt.discordClientID, "discord-client-id", "CPANEL_DISCORD_CLIENT_ID", "", "Discord application id")
	set.Secret(&opt.discordClientSecret, "discord-client-secret", "CPANEL_DISCORD_CLIENT_SECRET", "Discord application secret")
	set.Secret(&opt.discordBotToken, "discord-bot-token", "CPANEL_DISCORD_BOT_TOKEN", "bot token used to read guild roles")
	set.String(&opt.discordGuildID, "discord-guild-id", "CPANEL_DISCORD_GUILD_ID", "", "guild the user must belong to")
	set.String(&opt.discordRoleID, "discord-role-id", "CPANEL_DISCORD_ROLE_ID", "", "role the user must have")
	set.String(&opt.discordRedirectURI, "discord-redirect-uri", "CPANEL_DISCORD_REDIRECT_URI", "", "OAuth redirect URI; default is {public-origin}/auth/discord/callback")
	set.Duration(&opt.sessionTTL, "session-ttl", "CPANEL_SESSION_TTL", auth.DefaultSessionTTL, "session lifetime")
	set.Duration(&opt.roleRecheck, "role-recheck", "CPANEL_ROLE_RECHECK", auth.DefaultRoleRecheck, "how often to recheck the Discord role")
	set.Secret(&opt.sealboxPublic, "sealbox-public-key", "CPANEL_SEALBOX_PUBLIC_KEY", "worker public key, standard base64")
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
	pub, ok, err := loadSealboxPublic(opt)
	if err != nil {
		fmt.Fprintf(os.Stderr, "%s: %v\n", name, err)
		os.Exit(2)
	}
	ev := log.Info().Dict("config", set.Dict())
	if ok {
		fp := pub.Fingerprint()
		ev = ev.Str("sealbox_fingerprint", hex.EncodeToString(fp[:]))
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
	h := api.Handler(api.Config{
		Log: log, Pool: pool, Database: opt.database, Timeout: opt.requestTimeout,
		SessionTTL: opt.sessionTTL, RoleRecheck: opt.roleRecheck,
		Auth: auth.Settings{
			PublicOrigin: opt.publicOrigin, ClientID: opt.discordClientID, ClientSecret: opt.discordClientSecret,
			BotToken: opt.discordBotToken, GuildID: opt.discordGuildID, RoleID: opt.discordRoleID,
			RedirectURI: opt.discordRedirectURI,
		},
	})
	if err := httpserver.ListenAndServe(ctx, log, opt.listen, h, opt.shutdownTimeout); err != nil {
		log.Error().Err(err).Msg("exit")
		os.Exit(1)
	}
	log.Info().Msg("stopped")
}

// loadSealboxPublic parses the worker public key when a database URL is set. Without a database the
// process is already going to refuse to start, and the existing error names that setting.
func loadSealboxPublic(opt options) (sealbox.PublicKey, bool, error) {
	if opt.database == "" {
		return sealbox.PublicKey{}, false, nil
	}
	if strings.TrimSpace(opt.sealboxPublic) == "" {
		return sealbox.PublicKey{}, false, errors.New("sealbox public key is required ($CPANEL_SEALBOX_PUBLIC_KEY or $CPANEL_SEALBOX_PUBLIC_KEY_FILE)")
	}
	pub, err := sealbox.ParsePublicKey(opt.sealboxPublic)
	if err != nil {
		return sealbox.PublicKey{}, false, err
	}
	return pub, true, nil
}

// requireSchema checks the database version. It does not migrate. An empty URL is a configuration
// error; a database that is behind, drifted, or unreachable is a failed startup.
func requireSchema(database string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	return migrations.RequireCurrent(ctx, database)
}
