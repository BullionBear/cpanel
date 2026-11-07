package main

import (
	"github.com/prometheus/client_golang/prometheus"

	"github.com/BullionBear/cpanel/internal/reconcile"
)

// reconcileMetrics turns the ReconcileLoop's rounds into series on the worker's /metrics. internal/reconcile cannot
// export them itself: the Prometheus client links net/http (I5).
type reconcileMetrics struct {
	rounds   *prometheus.CounterVec
	inserted *prometheus.CounterVec
	seconds  *prometheus.HistogramVec
}

func newReconcileMetrics(reg prometheus.Registerer, rules []reconcile.Rule) *reconcileMetrics {
	m := &reconcileMetrics{
		rounds: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "cpanel_reconcile_rounds_total",
			Help: "ReconcileLoop rounds: result ok committed, error rolled back.",
		}, []string{"result"}),
		inserted: prometheus.NewCounterVec(prometheus.CounterOpts{
			Name: "cpanel_reconcile_inserted_total",
			Help: "Rows each ReconcileLoop rule committed.",
		}, []string{"rule"}),
		seconds: prometheus.NewHistogramVec(prometheus.HistogramOpts{
			Name:    "cpanel_reconcile_rule_duration_seconds",
			Help:    "Time one ReconcileLoop rule took in one round.",
			Buckets: prometheus.ExponentialBuckets(0.001, 4, 7), // 1ms to 4s
		}, []string{"rule"}),
	}
	reg.MustRegister(m.rounds, m.inserted, m.seconds)
	m.rounds.WithLabelValues("ok")
	m.rounds.WithLabelValues("error")
	for _, r := range rules {
		m.inserted.WithLabelValues(r.Name)
		m.seconds.WithLabelValues(r.Name)
	}
	return m
}

func (m *reconcileMetrics) observe(res reconcile.Result) {
	result := "ok"
	if res.Err != nil {
		result = "error"
	}
	m.rounds.WithLabelValues(result).Inc()
	for _, r := range res.Rules {
		m.inserted.WithLabelValues(r.Name).Add(float64(r.Inserted))
		m.seconds.WithLabelValues(r.Name).Observe(r.Elapsed.Seconds())
	}
}
