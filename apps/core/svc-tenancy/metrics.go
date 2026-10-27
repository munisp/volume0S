package main

import (
	"fmt"
	"net/http"
	"sort"
	"strconv"
	"strings"
	"sync"
)

type requestMetricKey struct {
	method string
	route  string
	code   int
}

// RequestMetrics is a small, dependency-free Prometheus counter registry. It
// intentionally excludes tenant IDs, raw URLs, and health/scrape traffic.
type RequestMetrics struct {
	mu     sync.RWMutex
	counts map[requestMetricKey]uint64
}

func NewRequestMetrics() *RequestMetrics {
	return &RequestMetrics{counts: make(map[requestMetricKey]uint64)}
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(status int) {
	if w.status != 0 {
		return
	}
	w.status = status
	w.ResponseWriter.WriteHeader(status)
}

func (w *statusWriter) Write(body []byte) (int, error) {
	if w.status == 0 {
		w.WriteHeader(http.StatusOK)
	}
	return w.ResponseWriter.Write(body)
}

func (w *statusWriter) Unwrap() http.ResponseWriter { return w.ResponseWriter }

func (m *RequestMetrics) instrument(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.URL.Path, "/v1/") {
			next.ServeHTTP(w, r)
			return
		}
		writer := &statusWriter{ResponseWriter: w}
		next.ServeHTTP(writer, r)
		status := writer.status
		if status == 0 {
			status = http.StatusOK
		}
		route := strings.TrimPrefix(r.Pattern, r.Method+" ")
		if route == "" {
			route = "unmatched"
		}
		m.mu.Lock()
		m.counts[requestMetricKey{method: r.Method, route: route, code: status}]++
		m.mu.Unlock()
	})
}

func (m *RequestMetrics) ServeHTTP(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	_, _ = fmt.Fprintln(w, "# HELP volumeos_http_requests_total Completed VolumeOS Core API requests.")
	_, _ = fmt.Fprintln(w, "# TYPE volumeos_http_requests_total counter")
	m.mu.RLock()
	keys := make([]requestMetricKey, 0, len(m.counts))
	for key := range m.counts {
		keys = append(keys, key)
	}
	sort.Slice(keys, func(i, j int) bool {
		if keys[i].route != keys[j].route {
			return keys[i].route < keys[j].route
		}
		if keys[i].method != keys[j].method {
			return keys[i].method < keys[j].method
		}
		return keys[i].code < keys[j].code
	})
	for _, key := range keys {
		_, _ = fmt.Fprintf(w, "volumeos_http_requests_total{service=\"svc-tenancy\",method=%s,route=%s,code=%s} %d\n",
			strconv.Quote(key.method), strconv.Quote(key.route), strconv.Quote(strconv.Itoa(key.code)), m.counts[key])
	}
	m.mu.RUnlock()
}
