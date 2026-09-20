// Package bench records bounded server traces for Bench without running inference.
package bench

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"strings"
	"sync"
	"time"
)

type Options struct {
	APIKey, Repository, Branch, SystemName, Environment, Endpoint string
	CaptureContent                                                bool
	// Nil means 1. Set a pointer to 0 to disable sampling.
	SampleRate   *float64
	MaxQueueSize int
	Timeout      time.Duration
	Redact       func(any) any
	OnError      func(string)
	// Transport may be injected for tests. Redirects remain disabled.
	Transport http.RoundTripper
}
type SpanInput struct {
	Name, Kind, Model string
	ComponentID       int64
	Input             any
	Attributes        map[string]any
}
type wireSpan struct {
	ID         string    `json:"span_id"`
	Parent     string    `json:"parent_span_id,omitempty"`
	Name       string    `json:"name"`
	Kind       string    `json:"kind"`
	Model      string    `json:"model_name,omitempty"`
	Started    time.Time `json:"started_at"`
	Ended      time.Time `json:"ended_at"`
	Status     string    `json:"status"`
	Attributes any       `json:"attributes"`
	Input      *string   `json:"input_value,omitempty"`
	Output     *string   `json:"output_value,omitempty"`
}
type trace struct {
	ID     string     `json:"trace_id"`
	Source string     `json:"source"`
	Spans  []wireSpan `json:"spans"`
}
type Stats struct{ Queued, Dropped int }
type Client struct {
	options     Options
	endpoint    string
	http        *http.Client
	sample      float64
	mu, sending sync.Mutex
	queue       []trace
	dropped     int
	closed      bool
}
type contextKey struct{ client *Client }
type traceContext struct {
	traceID, spanID string
	sampled         bool
}
type Span struct {
	client  *Client
	input   SpanInput
	context traceContext
	parent  string
	start   time.Time
	mu      sync.Mutex
	output  any
	status  string
	ended   bool
}

func New(options Options) (*Client, error) {
	if !strings.HasPrefix(options.APIKey, "bench_sk_") || options.Repository == "" || options.Branch == "" {
		return nil, errors.New("a Bench key, repository and branch are required")
	}
	if options.Endpoint == "" {
		options.Endpoint = "https://api.trybench.ai"
	}
	u, err := url.Parse(options.Endpoint)
	if err != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || !(u.Scheme == "https" || (u.Scheme == "http" && (u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1" || u.Hostname() == "::1"))) {
		return nil, errors.New("use HTTPS or a loopback HTTP endpoint")
	}
	if options.Environment != "" && !regexp.MustCompile(`^[A-Za-z0-9_.-]{1,64}$`).MatchString(options.Environment) {
		return nil, errors.New("invalid environment")
	}
	sample := 1.0
	if options.SampleRate != nil {
		sample = *options.SampleRate
	}
	if options.MaxQueueSize == 0 {
		options.MaxQueueSize = 200
	}
	if options.Timeout == 0 {
		options.Timeout = 5 * time.Second
	}
	if math.IsNaN(sample) || sample < 0 || sample > 1 || options.MaxQueueSize < 1 || options.MaxQueueSize > 2000 || options.Timeout < 100*time.Millisecond || options.Timeout > 30*time.Second {
		return nil, errors.New("sampling, queue size or timeout is out of range")
	}
	if options.SystemName == "" {
		parts := strings.Split(options.Repository, "/")
		options.SystemName = parts[len(parts)-1]
	}
	return &Client{options: options, endpoint: strings.TrimRight(options.Endpoint, "/") + "/api/traces", sample: sample, http: &http.Client{Transport: options.Transport, Timeout: options.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}, nil
}
func id(n int) string {
	b := make([]byte, n)
	if _, err := rand.Read(b); err != nil {
		return ""
	}
	return hex.EncodeToString(b)
}
func (c *Client) report(message string) {
	defer func() { _ = recover() }()
	if c.options.OnError != nil {
		c.options.OnError(message)
	}
}

// StartSpan propagates parentage through context.Context, including goroutines.
// Call End after SetOutput/SetError. A span that never receives either is an error.
func (c *Client) StartSpan(ctx context.Context, input SpanInput) (context.Context, *Span) {
	parent, _ := ctx.Value(contextKey{c}).(traceContext)
	own := traceContext{traceID: parent.traceID, spanID: id(8), sampled: parent.sampled}
	if own.traceID == "" {
		own.traceID = id(16)
		b := make([]byte, 8)
		_, e := rand.Read(b)
		var n uint64
		for _, v := range b {
			n = n<<8 | uint64(v)
		}
		own.sampled = e == nil && (c.sample == 1 || float64(n)/float64(^uint64(0)) < c.sample)
	}
	if input.Kind == "" {
		input.Kind = "LLM"
	}
	s := &Span{client: c, input: input, context: own, parent: parent.spanID, start: time.Now().UTC(), status: "error"}
	return context.WithValue(ctx, contextKey{c}, own), s
}
func (s *Span) SetOutput(value any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.output = value
	s.status = "ok"
}
func (s *Span) SetError() { s.mu.Lock(); defer s.mu.Unlock(); s.status = "error" }
func (s *Span) End() {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.ended {
		return
	}
	s.ended = true
	if s.context.sampled {
		s.client.capture(s)
	}
}

// Trace preserves the result, error and panic behavior of the original function.
func Trace[T any](ctx context.Context, c *Client, input SpanInput, fn func(context.Context) (T, error)) (output T, err error) {
	ctx, span := c.StartSpan(ctx, input)
	defer span.End()
	output, err = fn(ctx)
	if err != nil {
		span.SetError()
	} else {
		span.SetOutput(output)
	}
	return
}
func (c *Client) safe(value any) (any, error) {
	if c.options.Redact != nil {
		value = c.options.Redact(value)
	}
	data, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var decoded any
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err = decoder.Decode(&decoded); err != nil {
		return nil, err
	}
	return scrub(decoded, 0), nil
}
func (c *Client) capture(s *Span) {
	// A customer redactor or serialization failure must not mask an app error.
	defer func() {
		if recover() != nil {
			c.drop(1)
			c.report("Trace could not be captured; dropped.")
		}
	}()
	row, err := c.buildSpan(s)
	if err != nil {
		c.drop(1)
		c.report("Trace could not be captured; dropped.")
		return
	}
	t := trace{ID: s.context.traceID, Source: "bench_sdk", Spans: []wireSpan{row}}
	data, err := json.Marshal(t)
	if err != nil || len(data) > 200000 {
		c.drop(1)
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.closed || len(c.queue) >= c.options.MaxQueueSize {
		c.dropped++
		return
	}
	c.queue = append(c.queue, t)
}
func (c *Client) buildSpan(s *Span) (wireSpan, error) {
	row := wireSpan{ID: s.context.spanID, Parent: s.parent, Kind: s.input.Kind, Started: s.start, Ended: time.Now().UTC(), Status: s.status}
	switch row.Kind {
	case "LLM", "TOOL", "CHAIN", "AGENT", "RETRIEVER", "EMBEDDING":
	default:
		return row, errors.New("invalid kind")
	}
	attrs := map[string]any{}
	for k, v := range s.input.Attributes {
		if c.options.CaptureContent || metadata.MatchString(k) {
			attrs[k] = v
		}
	}
	if c.options.Environment != "" {
		attrs["bench.environment"] = c.options.Environment
	}
	if s.input.ComponentID > 0 {
		attrs["bench.component_id"] = s.input.ComponentID
	}
	var err error
	row.Attributes, err = c.safe(attrs)
	if err != nil {
		return row, err
	}
	name, err := c.safe(s.input.Name)
	if err != nil {
		return row, err
	}
	text, ok := name.(string)
	if !ok || text == "" {
		return row, errors.New("invalid name")
	}
	row.Name = limitText(text, 200)
	if s.input.Model != "" {
		model, e := c.safe(s.input.Model)
		if e != nil {
			return row, e
		}
		if text, ok := model.(string); ok {
			row.Model = limitText(text, 200)
		}
	}
	if c.options.CaptureContent {
		for value, target := range map[*any]**string{&s.input.Input: &row.Input, &s.output: &row.Output} {
			safe, e := c.safe(*value)
			if e != nil {
				return row, e
			}
			raw, e := json.Marshal(safe)
			if e != nil {
				return row, e
			}
			text := string(raw)
			*target = &text
		}
	}
	return row, nil
}
func (c *Client) drop(n int) { c.mu.Lock(); c.dropped += n; c.mu.Unlock() }
func (c *Client) Stats() Stats {
	c.mu.Lock()
	defer c.mu.Unlock()
	return Stats{len(c.queue), c.dropped}
}

// Flush sends the currently queued batch. New events wait for the next flush.
// Network failures are counted and reported, without changing app return values.
func (c *Client) Flush(ctx context.Context) {
	c.sending.Lock()
	defer c.sending.Unlock()
	c.mu.Lock()
	pending := c.queue
	c.queue = nil
	c.mu.Unlock()
	for len(pending) > 0 {
		batch, size := []trace{}, 0
		for len(pending) > 0 && len(batch) < 20 {
			raw, _ := json.Marshal(pending[0])
			if size+len(raw) > 800000 {
				break
			}
			size += len(raw)
			batch = append(batch, pending[0])
			pending = pending[1:]
		}
		body, _ := json.Marshal(map[string]any{"repo_full_name": c.options.Repository, "branch": c.options.Branch, "system_name": c.options.SystemName, "capture_content": c.options.CaptureContent, "traces": batch})
		delivered := false
		for attempt := 0; attempt < 2; attempt++ {
			req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.endpoint, bytes.NewReader(body))
			if err != nil {
				break
			}
			req.Header.Set("Content-Type", "application/json")
			req.Header.Set("Authorization", "Bearer "+c.options.APIKey)
			response, err := c.http.Do(req)
			if err == nil {
				status := response.StatusCode
				_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
				response.Body.Close()
				if status >= 200 && status < 300 {
					delivered = true
					break
				}
				if status != 429 && status < 500 {
					break
				}
			}
			if ctx.Err() != nil {
				break
			}
			if attempt == 0 {
				timer := time.NewTimer(250 * time.Millisecond)
				select {
				case <-timer.C:
				case <-ctx.Done():
				}
				timer.Stop()
			}
		}
		if !delivered {
			c.drop(len(batch))
			c.report("Bench trace delivery failed; batch dropped.")
		}
	}
}
func (c *Client) Shutdown(ctx context.Context) {
	c.mu.Lock()
	c.closed = true
	c.mu.Unlock()
	c.Flush(ctx)
}
