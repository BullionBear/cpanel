// Applying migrations. cpanel-migrate is the only caller that writes the schema. cpanel-api and
// cpanel-worker call RequireCurrent and refuse to start when the database is not at this version.
//
// Up reads the embedded *.up.sql files, in numeric order, and skips ones already recorded in
// meta.schema_migrations. Each file and its row commit together. A session advisory lock keeps two
// runs from applying at once. Down files are not applied here; tests execute them.
package migrations

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io/fs"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

// LockName is the advisory-lock key. Up takes pg_advisory_lock(hashtext(LockName), 1) for the whole
// run and unlocks when it returns. A second Up waits.
const LockName = "cpanel-migrate"

const (
	lockStatement   = "SELECT pg_advisory_lock(hashtext('" + LockName + "'), 1)"
	unlockStatement = "SELECT pg_advisory_unlock(hashtext('" + LockName + "'), 1)"
)

// versionTable is the record of applied files. Up creates it. It is not itself a numbered migration,
// so a changed checksum cannot be hidden by editing 0001.
const versionTable = `
CREATE SCHEMA IF NOT EXISTS meta;
CREATE TABLE IF NOT EXISTS meta.schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    sha256     TEXT NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL
)`

// State is how one migration stands relative to meta.schema_migrations.
type State string

const (
	StateApplied State = "applied"
	StatePending State = "pending"
	StateChanged State = "changed"
	StateMissing State = "missing"
)

// Row is one migration, embedded or only recorded.
type Row struct {
	Version      int
	Name         string
	RecordedName string
	State        State
	AppliedAt    time.Time
	RecordedSHA  string
	FileSHA      string
}

// String is one status line.
func (r Row) String() string {
	label := fmt.Sprintf("%04d %s", r.Version, r.Name)
	switch r.State {
	case StateApplied:
		return fmt.Sprintf("%s applied %s %s", label, r.AppliedAt.UTC().Format(time.RFC3339Nano), r.RecordedSHA)
	case StatePending:
		return label + " pending"
	case StateChanged:
		return fmt.Sprintf("%s changed recorded %s file %s", label, r.RecordedSHA, r.FileSHA)
	case StateMissing:
		return label + " missing"
	default:
		return label + " " + string(r.State)
	}
}

// Applied is one migration Up committed on this call.
type Applied struct {
	Version int
	Name    string
}

// String is the file stem, "0001_schema".
func (a Applied) String() string {
	return fmt.Sprintf("%04d_%s", a.Version, a.Name)
}

// Result is what Up applied. Applied is empty when the database was already current.
type Result struct {
	Applied []Applied
}

// DriftError means a recorded migration does not match the embedded file, or the file is gone.
// Up stops before applying anything new.
type DriftError struct {
	Version     int
	Name        string
	State       State
	RecordedSHA string
	FileSHA     string
}

func (e *DriftError) Error() string {
	switch e.State {
	case StateMissing:
		return fmt.Sprintf("meta.schema_migrations version %d (%s) has no migration file", e.Version, e.Name)
	default:
		return fmt.Sprintf("migration %04d_%s changed: recorded sha256 %s, file sha256 %s", e.Version, e.Name, e.RecordedSHA, e.FileSHA)
	}
}

type file struct {
	version int
	name    string
	body    []byte
	sha256  string
}

func (f file) stem() string { return fmt.Sprintf("%04d_%s", f.version, f.name) }

var fileRE = regexp.MustCompile(`^([0-9]{4})_([a-z0-9_]+)\.(up|down)\.sql$`)

// Up applies every embedded up migration that meta.schema_migrations does not yet record.
func Up(ctx context.Context, dsn string) (Result, error) {
	ups, _, err := load()
	if err != nil {
		return Result{}, err
	}
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		return Result{}, err
	}
	defer conn.Close(context.Background())

	if _, err := conn.Exec(ctx, lockStatement); err != nil {
		return Result{}, fmt.Errorf("advisory lock: %w", err)
	}
	defer func() { _, _ = conn.Exec(context.Background(), unlockStatement) }()

	if _, err := conn.Exec(ctx, versionTable); err != nil {
		return Result{}, fmt.Errorf("meta.schema_migrations: %w", err)
	}
	recorded, err := readRecorded(ctx, conn)
	if err != nil {
		return Result{}, err
	}
	if _, drift := compare(ups, recorded); drift != nil {
		return Result{}, drift
	}
	var applied []Applied
	for _, f := range ups {
		if _, ok := recorded[f.version]; ok {
			continue
		}
		if err := applyOne(ctx, conn, f); err != nil {
			return Result{Applied: applied}, err
		}
		applied = append(applied, Applied{Version: f.version, Name: f.name})
	}
	return Result{Applied: applied}, nil
}

// Status reports every embedded up migration and any recorded version that has no file.
// It does not take the advisory lock. A non-nil DriftError means the lines are complete and at
// least one of them is changed or missing.
func Status(ctx context.Context, dsn string) ([]Row, error) {
	ups, _, err := load()
	if err != nil {
		return nil, err
	}
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		return nil, err
	}
	defer conn.Close(ctx)
	recorded, err := readRecorded(ctx, conn)
	if err != nil {
		return nil, err
	}
	rows, drift := compare(ups, recorded)
	return rows, drift
}

// ErrDatabaseURLEmpty means the command was given no PostgreSQL URL.
var ErrDatabaseURLEmpty = errors.New("database URL is empty (set -database or $CPANEL_DATABASE_URL)")

// RequireCurrent reports whether the database matches the embedded migrations. It does not migrate
// and does not take the lock. The error names the pending or drifted migration.
func RequireCurrent(ctx context.Context, dsn string) error {
	if strings.TrimSpace(dsn) == "" {
		return ErrDatabaseURLEmpty
	}
	rows, err := Status(ctx, dsn)
	if err != nil {
		return fmt.Errorf("database schema: %w", err)
	}
	var pending []string
	for _, r := range rows {
		if r.State == StatePending {
			pending = append(pending, fmt.Sprintf("%04d_%s", r.Version, r.Name))
		}
	}
	if len(pending) > 0 {
		return fmt.Errorf("database schema is behind (%s); run cpanel-migrate up", strings.Join(pending, ", "))
	}
	return nil
}

// ExecSQL runs sql with the simple query protocol, so a migration file may contain more than one
// statement. Tests use it to run down files.
func ExecSQL(ctx context.Context, conn *pgx.Conn, sql string) error {
	return conn.PgConn().Exec(ctx, sql).Close()
}

func applyOne(ctx context.Context, conn *pgx.Conn, f file) error {
	var b strings.Builder
	b.WriteString("BEGIN;\n")
	b.Write(f.body)
	if len(f.body) == 0 || f.body[len(f.body)-1] != '\n' {
		b.WriteByte('\n')
	}
	fmt.Fprintf(&b, "INSERT INTO meta.schema_migrations (version, name, sha256, applied_at) VALUES (%d, '%s', '%s', now());\nCOMMIT;\n",
		f.version, f.name, f.sha256)
	// The simple query protocol accepts several statements; the extended protocol does not.
	// The whole script is one transaction, so a later statement's failure rolls the file back
	// with its row.
	if err := conn.PgConn().Exec(ctx, b.String()).Close(); err != nil {
		_ = conn.PgConn().Exec(context.Background(), "ROLLBACK").Close()
		return fmt.Errorf("migration %s: %w", f.stem(), err)
	}
	return nil
}

type record struct {
	version   int
	name      string
	sha       string
	appliedAt time.Time
}

func readRecorded(ctx context.Context, conn *pgx.Conn) (map[int]record, error) {
	var rel *string
	if err := conn.QueryRow(ctx, `SELECT to_regclass('meta.schema_migrations')::text`).Scan(&rel); err != nil {
		return nil, err
	}
	out := map[int]record{}
	if rel == nil {
		return out, nil
	}
	rows, err := conn.Query(ctx, `SELECT version, name, sha256, applied_at FROM meta.schema_migrations ORDER BY version`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var rec record
		if err := rows.Scan(&rec.version, &rec.name, &rec.sha, &rec.appliedAt); err != nil {
			return nil, err
		}
		out[rec.version] = rec
	}
	return out, rows.Err()
}

func compare(ups []file, recorded map[int]record) ([]Row, error) {
	byVersion := map[int]file{}
	var versions []int
	for _, f := range ups {
		byVersion[f.version] = f
		versions = append(versions, f.version)
	}
	for v := range recorded {
		if _, ok := byVersion[v]; !ok {
			versions = append(versions, v)
		}
	}
	slices.Sort(versions)

	var lines []Row
	var drift error
	for _, v := range versions {
		f, hasFile := byVersion[v]
		rec, hasRec := recorded[v]
		switch {
		case hasFile && hasRec && f.name == rec.name && strings.EqualFold(f.sha256, rec.sha):
			lines = append(lines, Row{
				Version: v, Name: f.name, RecordedName: rec.name, State: StateApplied,
				AppliedAt: rec.appliedAt, RecordedSHA: rec.sha, FileSHA: f.sha256,
			})
		case hasFile && hasRec:
			lines = append(lines, Row{
				Version: v, Name: f.name, RecordedName: rec.name, State: StateChanged,
				AppliedAt: rec.appliedAt, RecordedSHA: rec.sha, FileSHA: f.sha256,
			})
			if drift == nil {
				drift = &DriftError{Version: v, Name: f.name, State: StateChanged, RecordedSHA: rec.sha, FileSHA: f.sha256}
			}
		case hasFile:
			lines = append(lines, Row{Version: v, Name: f.name, State: StatePending, FileSHA: f.sha256})
		default:
			lines = append(lines, Row{
				Version: v, Name: rec.name, RecordedName: rec.name, State: StateMissing,
				AppliedAt: rec.appliedAt, RecordedSHA: rec.sha,
			})
			if drift == nil {
				drift = &DriftError{Version: v, Name: rec.name, State: StateMissing, RecordedSHA: rec.sha}
			}
		}
	}
	return lines, drift
}

// load reads the embedded pairs. Every up file has a down file and the reverse, versions are unique,
// and names are lowercase.
func load() (ups []file, downs map[int]string, err error) {
	ents, err := fs.ReadDir(FS, ".")
	if err != nil {
		return nil, nil, err
	}
	downs = map[int]string{}
	seen := map[int]string{}
	for _, e := range ents {
		if e.IsDir() {
			return nil, nil, fmt.Errorf("migrations: unexpected directory %s", e.Name())
		}
		m := fileRE.FindStringSubmatch(e.Name())
		if m == nil {
			return nil, nil, fmt.Errorf("migrations: unexpected file %s", e.Name())
		}
		version, err := strconv.Atoi(m[1])
		if err != nil || version < 1 {
			return nil, nil, fmt.Errorf("migrations: bad version in %s", e.Name())
		}
		body, err := FS.ReadFile(e.Name())
		if err != nil {
			return nil, nil, err
		}
		switch m[3] {
		case "up":
			if prev, ok := seen[version]; ok {
				return nil, nil, fmt.Errorf("migrations: duplicate version %d (%s and %s)", version, prev, e.Name())
			}
			seen[version] = e.Name()
			sum := sha256.Sum256(body)
			ups = append(ups, file{version: version, name: m[2], body: body, sha256: hex.EncodeToString(sum[:])})
		case "down":
			if _, ok := downs[version]; ok {
				return nil, nil, fmt.Errorf("migrations: duplicate down file for version %d", version)
			}
			downs[version] = string(body)
		}
	}
	slices.SortFunc(ups, func(a, b file) int { return a.version - b.version })
	for _, f := range ups {
		if _, ok := downs[f.version]; !ok {
			return nil, nil, fmt.Errorf("migrations: %s has no down file", f.stem())
		}
	}
	for v := range downs {
		if _, ok := seen[v]; !ok {
			return nil, nil, fmt.Errorf("migrations: version %d has a down file and no up file", v)
		}
	}
	if len(ups) == 0 {
		return nil, nil, errors.New("migrations: no migration files")
	}
	return ups, downs, nil
}
