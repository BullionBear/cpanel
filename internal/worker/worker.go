package worker

import (
	"cmp"
	"context"
	crand "crypto/rand"
	"encoding/hex"
	"fmt"
	"math/rand/v2"
	"os"
	"slices"
	"sync"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/rs/zerolog"
)

// Defaults for the Worker fields left zero.
const (
	DefaultSlots          = 4
	DefaultLeaseTTL       = 60 * time.Second
	DefaultRenewEvery     = 20 * time.Second
	DefaultBudgetRequests = 20
	DefaultBudgetTime     = 30 * time.Second
	DefaultPoll           = time.Second
	DefaultGrace          = 10 * time.Second
)

// cleanupTimeout bounds handing a job back and releasing a lease, which run after the worker has been told to stop.
const cleanupTimeout = 5 * time.Second

// Handlers maps a job kind to the code that runs it. A worker takes only the jobs whose kind it has.
type Handlers map[string]Handler

// Worker runs jobs. A zero count or duration means its default.
type Worker struct {
	DB             *pgxpool.Pool
	Name           string // owner of its leases and worker of its jobs; DefaultName() when empty
	Handlers       Handlers
	Slots          int                                              // lease keys run at once
	LeaseTTL       time.Duration                                    // how long an acquired or renewed lease lasts
	RenewEvery     time.Duration                                    // how often a held lease is renewed; must be under half of LeaseTTL
	BudgetRequests int                                              // outbound requests one job may make
	BudgetTime     time.Duration                                    // how long one job may keep starting pages; it is cancelled at twice that
	Poll           time.Duration                                    // how long an idle slot waits before it looks for work again
	Grace          time.Duration                                    // how long the page in progress may take once the worker is told to stop
	Egress         string                                           // this process's egress; every replica behind it shares one IP-ban pause
	Local          []string                                         // kinds that do not call out, so an egress pause does not hold them; maint.partitions always counts
	Rand           func() float64                                   // jitter position in [0, 1]; nil is math/rand
	CredentialHook func(context.Context, pgx.Tx, AuthFailure) error // nil records an auth failure and does nothing else
	AlertHook      func(context.Context, Alert)                     // nil only logs a bad request
	Log            zerolog.Logger
}

// DefaultName is host-pid-random: unique per process start, even for pid 1 in a restarted container.
func DefaultName() string {
	host, err := os.Hostname()
	if err != nil {
		host = "worker"
	}
	b := make([]byte, 4)
	_, _ = crand.Read(b)
	return fmt.Sprintf("%s-%d-%s", host, os.Getpid(), hex.EncodeToString(b))
}

// runner is a Worker with its defaults filled in.
type runner struct {
	db             *pgxpool.Pool
	name           string
	handlers       Handlers
	kinds          []string
	slots          int
	ttl, renew     time.Duration
	budgetRequests int
	budgetTime     time.Duration
	poll, grace    time.Duration
	egress         string
	local          []string
	rand           func() float64
	auth           func(context.Context, pgx.Tx, AuthFailure) error
	alert          func(context.Context, Alert)
	log            zerolog.Logger
}

func (w *Worker) runner() *runner {
	r := &runner{
		db:             w.DB,
		name:           cmp.Or(w.Name, DefaultName()),
		handlers:       w.Handlers,
		slots:          cmp.Or(w.Slots, DefaultSlots),
		ttl:            cmp.Or(w.LeaseTTL, DefaultLeaseTTL),
		renew:          cmp.Or(w.RenewEvery, DefaultRenewEvery),
		budgetRequests: cmp.Or(w.BudgetRequests, DefaultBudgetRequests),
		budgetTime:     cmp.Or(w.BudgetTime, DefaultBudgetTime),
		poll:           cmp.Or(w.Poll, DefaultPoll),
		grace:          cmp.Or(w.Grace, DefaultGrace),
		egress:         w.Egress,
		local:          append([]string{}, w.Local...),
		rand:           w.Rand,
		auth:           w.CredentialHook,
		alert:          w.AlertHook,
		log:            w.Log,
	}
	if !slices.Contains(r.local, maintPartitionsKind) {
		r.local = append(r.local, maintPartitionsKind)
	}
	for k := range w.Handlers {
		r.kinds = append(r.kinds, k)
	}
	slices.Sort(r.kinds)
	slices.Sort(r.local)
	return r
}

// Run runs jobs until ctx ends. Then it stops taking work, lets each slot finish its page in progress for up to
// Grace, sets unfinished jobs back to pending, releases its leases and returns.
func (w *Worker) Run(ctx context.Context) {
	r := w.runner()
	if r.renew*2 >= r.ttl {
		panic(fmt.Sprintf("worker: RenewEvery %s must be under half of LeaseTTL %s", r.renew, r.ttl))
	}
	// hard outlives ctx by Grace: the page in progress and the cleanup after it run under it.
	hard, kill := context.WithCancel(context.WithoutCancel(ctx))
	defer kill()
	stopAfter := context.AfterFunc(ctx, func() {
		t := time.NewTimer(r.grace)
		defer t.Stop()
		select {
		case <-t.C:
			kill()
		case <-hard.Done():
		}
	})
	defer stopAfter()

	if r.egress == "" {
		r.log.Warn().Str("worker", r.name).Msg("no egress name; an IP ban is not paused for other workers")
	}
	r.log.Info().Str("worker", r.name).Str("egress", r.egress).Int("slots", r.slots).Strs("kinds", r.kinds).Msg("worker started")
	var wg sync.WaitGroup
	for range r.slots {
		wg.Go(func() { r.slot(ctx, hard) })
	}
	wg.Wait()
	r.log.Info().Str("worker", r.name).Msg("worker stopped")
}

// slot holds one lease at a time until ctx ends.
func (r *runner) slot(ctx, hard context.Context) {
	for ctx.Err() == nil {
		l, err := r.acquire(hard)
		switch {
		case err != nil:
			r.log.Error().Err(err).Msg("acquire lease")
		case l != nil:
			r.hold(ctx, hard, l)
			continue
		}
		// Jitter keeps idle slots of many workers from polling in step.
		wait := r.poll/2 + rand.N(r.poll)
		select {
		case <-ctx.Done():
		case <-time.After(wait):
		}
	}
}

// hold runs the due jobs of l's key, one at a time, until there are none, the lease is lost or ctx ends.
func (r *runner) hold(ctx, hard context.Context, l *lease) {
	keepCtx, stopKeeping := context.WithCancel(hard)
	var keeping sync.WaitGroup
	keeping.Go(func() { r.keep(keepCtx, l) })
	defer func() {
		stopKeeping()
		keeping.Wait()
		cctx, cancel := cleanup(ctx)
		defer cancel()
		if err := r.release(cctx, l); err != nil {
			r.log.Error().Err(err).Str("lease_key", l.key).Msg("release lease")
		}
	}()
	for ctx.Err() == nil && !l.lost(r.renew) {
		j, err := r.claim(hard, l)
		if err != nil {
			r.log.Error().Err(err).Str("lease_key", l.key).Msg("claim job")
			return
		}
		if j == nil {
			return
		}
		r.run(ctx, hard, l, j)
	}
}

// run runs one claimed job and records how it ended.
func (r *runner) run(ctx, hard context.Context, l *lease, j *Job) {
	start := time.Now()
	j.Budget = Budget{Requests: r.budgetRequests, Deadline: start.Add(r.budgetTime)}
	j.stopping = ctx
	j.lease = l
	j.renew = r.renew
	hctx, cancel := context.WithDeadline(hard, start.Add(2*r.budgetTime))
	res, err := call(hctx, r.handlers[j.Kind], j)
	cancel()

	cctx, cancelCleanup := cleanup(ctx)
	defer cancelCleanup()
	log := r.log.With().Int64("job", j.ID).Str("kind", j.Kind).Str("lease_key", l.key).Logger()
	// Stopped before the job was done: it goes back to pending for the next holder of the key. A page cancelled
	// after Grace rolled back, so the same holds.
	if hard.Err() != nil || (ctx.Err() != nil && err == nil && res.More) {
		if err := r.handBack(cctx, l, j); err != nil {
			log.Error().Err(err).Msg("hand job back")
		}
		return
	}
	ours, cerr := r.complete(cctx, l, j, res, err)
	switch {
	case cerr != nil:
		log.Error().Err(cerr).Msg("complete job")
	case !ours:
		log.Warn().Msg("job was taken over by another worker; its result is dropped")
	case err != nil:
		log.Warn().Err(err).Int("requests", res.Requests).Int("items", res.Items).Msg("job failed")
	default:
		log.Debug().Int("requests", res.Requests).Int("items", res.Items).Bool("more", res.More).Msg("job done")
	}
}

// call runs h, turning a panic into an error so one bad job does not stop the worker.
func call(ctx context.Context, h Handler, j *Job) (res Result, err error) {
	defer func() {
		if p := recover(); p != nil {
			err = fmt.Errorf("handler panicked: %v", p)
		}
	}()
	return h(ctx, j)
}

// cleanup is a context for the work done after ctx ended: handing a job back, releasing a lease.
func cleanup(ctx context.Context) (context.Context, context.CancelFunc) {
	return context.WithTimeout(context.WithoutCancel(ctx), cleanupTimeout)
}
