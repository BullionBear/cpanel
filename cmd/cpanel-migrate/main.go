// Command cpanel-migrate applies the SQL migrations embedded from migrations/ before a new release
// starts (ARCHITECTURE §4.8). cpanel-api and cpanel-worker do not migrate; they refuse to start when
// the database is behind.
//
//	cpanel-migrate [-database URL] [-log-level LEVEL] up
//	cpanel-migrate [-database URL] [-log-level LEVEL] status
//
// -database defaults to $CPANEL_DATABASE_URL and -log-level to $CPANEL_LOG_LEVEL; a flag wins over its
// variable. up applies each pending file in its own transaction, under an advisory lock, and records
// the file's sha256 in meta.schema_migrations. A file that was applied and then edited is refused, and
// nothing new is applied. status lists applied and pending files and does not lock. There is no down
// command: down files are for tests.
//
// The database URL is a secret: the startup log shows it as ***. $CPANEL_DATABASE_URL_FILE may name a file that
// holds it instead (C2-D008).
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"

	"github.com/BullionBear/cpanel/internal/platform/config"
	"github.com/BullionBear/cpanel/internal/platform/logger"
	"github.com/BullionBear/cpanel/migrations"
)

const name = "cpanel-migrate"

const usageText = "usage: cpanel-migrate [-database URL] [-log-level LEVEL] up|status"

func main() {
	os.Exit(run(os.Args[1:], os.Getenv, os.Stdout, os.Stderr))
}

func run(args []string, getenv func(string) string, stdout, stderr io.Writer) int {
	var database string
	level := logger.DefaultLevel
	set := config.New(name, getenv)
	set.Secret(&database, "database", "CPANEL_DATABASE_URL", "PostgreSQL URL")
	set.Var(&level, "log-level", "CPANEL_LOG_LEVEL", "trace, debug, info, warn or error")
	rest, err := set.ParseRemaining(args)
	log := logger.New(stderr, name, level)
	if errors.Is(err, flag.ErrHelp) {
		fmt.Fprintln(stderr, usageText)
		return 0
	}
	if err != nil {
		fmt.Fprintf(stderr, "%s: %v\n", name, err)
		return 2
	}
	log.Info().Dict("config", set.Dict()).Msg("starting")
	if len(rest) != 1 || (rest[0] != "up" && rest[0] != "status") {
		fmt.Fprintln(stderr, usageText)
		return 2
	}
	if strings.TrimSpace(database) == "" {
		fmt.Fprintf(stderr, "%s: database URL is empty (set -database or $CPANEL_DATABASE_URL)\n", name)
		return 2
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	switch rest[0] {
	case "up":
		res, err := migrations.Up(ctx, database)
		for _, a := range res.Applied {
			fmt.Fprintf(stdout, "applied %s\n", a)
		}
		if err != nil {
			log.Error().Err(err).Msg("up")
			fmt.Fprintf(stderr, "%s: %v\n", name, err)
			return 1
		}
		if len(res.Applied) == 0 {
			fmt.Fprintln(stdout, "up to date")
		}
		return 0
	default:
		rows, err := migrations.Status(ctx, database)
		for _, row := range rows {
			fmt.Fprintln(stdout, row)
		}
		if err != nil {
			log.Error().Err(err).Msg("status")
			fmt.Fprintf(stderr, "%s: %v\n", name, err)
			return 1
		}
		return 0
	}
}
