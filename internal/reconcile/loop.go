package reconcile

import (
	"cmp"
	"context"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/rs/zerolog"
)

const (
	// DefaultInterval is how often a loop starts a round.
	DefaultInterval = 5 * time.Second
	// DefaultTimeout bounds one round.
	DefaultTimeout = 30 * time.Second
)

// Rule inserts the jobs that should exist and do not, inside the round's transaction, and returns how many rows
// it inserted. now is the round's clock: a rule compares against it, not against the database's now().
type Rule struct {
	Name string // the metrics label
	Run  func(ctx context.Context, tx pgx.Tx, now time.Time) (inserted int, err error)
}

// DB begins a round's transaction: a *pgxpool.Pool, or a *pgx.Conn in tests.
type DB interface {
	BeginTx(ctx context.Context, opts pgx.TxOptions) (pgx.Tx, error)
}

// Loop runs Rules against DB every Interval until its context ends.
type Loop struct {
	DB       DB
	Rules    []Rule
	Interval time.Duration    // DefaultInterval when zero
	Timeout  time.Duration    // DefaultTimeout when zero
	Now      func() time.Time // time.Now when nil
	Log      zerolog.Logger
	Observe  func(Result) // when set, gets every round's Result, committed or not
}

// Result is one round.
type Result struct {
	At    time.Time    // the now the rules saw
	Rules []RuleResult // in order, up to and including the rule that failed
	Err   error        // nil when the round committed
}

// RuleResult is one rule in one round. Inserted is 0 for every rule of a round that rolled back.
type RuleResult struct {
	Name     string
	Inserted int
	Elapsed  time.Duration
}

// Run starts a round at once and then every Interval, until ctx ends. A failed round is logged and the next one
// tries again.
func (l *Loop) Run(ctx context.Context) {
	tick := time.NewTicker(cmp.Or(l.Interval, DefaultInterval))
	defer tick.Stop()
	for {
		res := l.Round(ctx)
		switch {
		case ctx.Err() != nil:
			return
		case res.Err != nil:
			l.Log.Error().Err(res.Err).Msg("reconcile")
		default:
			if ev := l.Log.Debug(); ev.Enabled() {
				inserted := zerolog.Dict()
				for _, r := range res.Rules {
					inserted.Int(r.Name, r.Inserted)
				}
				ev.Dict("inserted", inserted).Msg("reconciled")
			}
		}
		select {
		case <-ctx.Done():
			return
		case <-tick.C:
		}
	}
}

// Round runs every rule once, in order, in one transaction, and commits only if all of them succeed.
func (l *Loop) Round(ctx context.Context) Result {
	ctx, cancel := context.WithTimeout(ctx, cmp.Or(l.Timeout, DefaultTimeout))
	defer cancel()
	now := time.Now
	if l.Now != nil {
		now = l.Now
	}
	res := Result{At: now()}
	res.Err = pgx.BeginTxFunc(ctx, l.DB, pgx.TxOptions{}, func(tx pgx.Tx) error {
		for _, rule := range l.Rules {
			start := time.Now()
			n, err := rule.Run(ctx, tx, res.At)
			res.Rules = append(res.Rules, RuleResult{Name: rule.Name, Inserted: n, Elapsed: time.Since(start)})
			if err != nil {
				return fmt.Errorf("rule %s: %w", rule.Name, err)
			}
		}
		return nil
	})
	if res.Err != nil {
		for i := range res.Rules {
			res.Rules[i].Inserted = 0
		}
	}
	if l.Observe != nil {
		l.Observe(res)
	}
	return res
}
