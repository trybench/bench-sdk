package bench_test

import (
	"context"
	"encoding/json"
	"errors"
	bench "github.com/trybench/bench-sdk/go"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
)

func TestSharedPrivacyContractInOutgoingPayload(t *testing.T) {
	data, err := os.ReadFile("testdata/privacy.json")
	if err != nil {
		t.Fatal(err)
	}
	var fixture struct {
		Input                json.RawMessage
		Forbidden, Preserved []string
	}
	if err = json.Unmarshal(data, &fixture); err != nil {
		t.Fatal(err)
	}
	var raw []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { raw, _ = io.ReadAll(r.Body); w.WriteHeader(201) }))
	defer server.Close()
	client, err := bench.New(bench.Options{APIKey: "bench_sk_synthetic_test_key", Repository: "fixture/go", Branch: "main", Endpoint: server.URL, CaptureContent: true})
	if err != nil {
		t.Fatal(err)
	}
	_, _ = bench.Trace(context.Background(), client, bench.SpanInput{Name: "privacy-contract", Input: fixture.Input}, func(context.Context) (bool, error) { return true, nil })
	client.Shutdown(context.Background())
	var body struct {
		Traces []struct {
			Spans []struct {
				Input string `json:"input_value"`
			} `json:"spans"`
		} `json:"traces"`
	}
	if err = json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	input := body.Traces[0].Spans[0].Input
	for _, value := range fixture.Forbidden {
		if strings.Contains(input, value) {
			t.Errorf("leaked %s", value)
		}
	}
	for _, value := range fixture.Preserved {
		if !strings.Contains(input, value) {
			t.Errorf("lost %s", value)
		}
	}
	var parsed map[string]any
	if err = json.Unmarshal([]byte(input), &parsed); err != nil || parsed["count"] != float64(42) {
		t.Fatal("ordinary count changed", err)
	}
}

func TestHTTPParentagePrivacyAndApplicationErrors(t *testing.T) {
	var mu sync.Mutex
	var bodies [][]byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		mu.Lock()
		bodies = append(bodies, raw)
		mu.Unlock()
		if r.URL.Path != "/api/traces" || r.Header.Get("Authorization") != "Bearer bench_sk_synthetic_test_key" {
			t.Error("wrong endpoint/auth")
		}
		w.WriteHeader(201)
	}))
	defer server.Close()
	client, err := bench.New(bench.Options{APIKey: "bench_sk_synthetic_test_key", Repository: "fixture/go", Branch: "main", Environment: "staging", Endpoint: server.URL, CaptureContent: true})
	if err != nil {
		t.Fatal(err)
	}
	output, err := bench.Trace(context.Background(), client, bench.SpanInput{Name: "agent", Kind: "AGENT", Input: map[string]any{"card_number": 4242424242424242, "note": "person@example.test 192.168.1.2 4242 4242 4242 4242", "count": 3}}, func(ctx context.Context) (string, error) {
		var wg sync.WaitGroup
		for i := 0; i < 5; i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				_, e := bench.Trace(ctx, client, bench.SpanInput{Name: "tool", Kind: "TOOL"}, func(context.Context) (int, error) { return 42, nil })
				if e != nil {
					t.Error(e)
				}
			}()
		}
		wg.Wait()
		return "done", nil
	})
	if err != nil || output != "done" {
		t.Fatal("application changed", output, err)
	}
	original := errors.New("application error")
	_, err = bench.Trace(context.Background(), client, bench.SpanInput{Name: "failed"}, func(context.Context) (string, error) { return "", original })
	if err != original {
		t.Fatal("error changed")
	}
	client.Shutdown(context.Background())
	if len(bodies) != 1 {
		t.Fatal("missing batch")
	}
	for _, s := range []string{"person@example.test", "192.168.1.2", "4242", "application error"} {
		if strings.Contains(string(bodies[0]), s) {
			t.Fatal("sensitive value sent", s)
		}
	}
	// Decode explicit wire fields to check the transmitted contract.
	var raw map[string]any
	if err = json.Unmarshal(bodies[0], &raw); err != nil {
		t.Fatal(err)
	}
	traces := raw["traces"].([]any)
	root := ""
	traceID := ""
	tools := []map[string]any{}
	for _, item := range traces {
		tr := item.(map[string]any)
		row := tr["spans"].([]any)[0].(map[string]any)
		if row["name"] == "agent" {
			root = row["span_id"].(string)
			traceID = tr["trace_id"].(string)
		}
		if row["name"] == "tool" {
			row["trace_id"] = tr["trace_id"]
			tools = append(tools, row)
		}
		if row["name"] == "failed" && row["status"] != "error" {
			t.Fatal("failure not recorded")
		}
	}
	if root == "" || len(tools) != 5 {
		t.Fatal("missing spans")
	}
	for _, tool := range tools {
		if tool["parent_span_id"] != root || tool["trace_id"] != traceID {
			t.Fatal("wrong parent")
		}
	}
}
func TestRetryIdentityRedirectQueueAndMetadataDefaults(t *testing.T) {
	var bodies []string
	status := 503
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, _ := io.ReadAll(r.Body)
		bodies = append(bodies, string(raw))
		w.Header().Set("Location", "/never-follow")
		w.WriteHeader(status)
	}))
	defer server.Close()
	client, err := bench.New(bench.Options{APIKey: "bench_sk_synthetic_test_key", Repository: "fixture/go", Branch: "main", Endpoint: server.URL, MaxQueueSize: 1})
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 2; i++ {
		_, s := client.StartSpan(context.Background(), bench.SpanInput{Name: "request", Input: "do-not-capture", Attributes: map[string]any{"private": "no", "gen_ai.usage.input_tokens": 42}})
		s.SetOutput("no-output")
		s.End()
	}
	if client.Stats().Dropped != 1 {
		t.Fatal("unbounded queue")
	}
	client.Flush(context.Background())
	if len(bodies) != 2 || bodies[0] != bodies[1] {
		t.Fatal("retry changed identity")
	}
	if strings.Contains(bodies[0], "do-not-capture") || strings.Contains(bodies[0], "private") || !strings.Contains(bodies[0], `"gen_ai.usage.input_tokens":42`) {
		t.Fatal("metadata contract")
	}
	status = 302
	_, s := client.StartSpan(context.Background(), bench.SpanInput{Name: "redirect"})
	s.SetOutput(nil)
	s.End()
	client.Flush(context.Background())
	if len(bodies) != 3 {
		t.Fatal("redirect followed")
	}
	zero := 0.0
	off, err := bench.New(bench.Options{APIKey: "bench_sk_test", Repository: "fixture/go", Branch: "main", SampleRate: &zero})
	if err != nil {
		t.Fatal(err)
	}
	_, span := off.StartSpan(context.Background(), bench.SpanInput{Name: "off"})
	span.End()
	if off.Stats().Queued != 0 {
		t.Fatal("sampling ignored")
	}
}
func TestRedactorPanicDoesNotChangeAppResult(t *testing.T) {
	c, e := bench.New(bench.Options{APIKey: "bench_sk_test", Repository: "fixture/go", Branch: "main", Redact: func(any) any { panic("redactor failed") }})
	if e != nil {
		t.Fatal(e)
	}
	result, e := bench.Trace(context.Background(), c, bench.SpanInput{Name: "safe"}, func(context.Context) (int, error) { return 42, nil })
	if e != nil || result != 42 || c.Stats().Dropped != 1 {
		t.Fatal("telemetry interrupted app")
	}
}
