package bench_test

import (
	"context"
	"encoding/json"
	bench "github.com/trybench/bench-sdk/go"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestInferSpanKindFollowsGenAIConventions(t *testing.T) {
	cases := map[string]map[string]any{
		"AGENT":     {"gen_ai.operation.name": "invoke_agent"},
		"TOOL":      {"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup"},
		"LLM":       {"gen_ai.request.model": "gpt-4.1"},
		"EMBEDDING": {"gen_ai.operation.name": "embeddings"},
		"UNKNOWN":   {"http.method": "GET"},
	}
	for want, attrs := range cases {
		if got := bench.InferSpanKind(attrs); got != want {
			t.Fatalf("%v: got %s want %s", attrs, got, want)
		}
	}
	// Pydantic AI stamps the agent name on model and tool spans too.
	if bench.InferSpanKind(map[string]any{"gen_ai.operation.name": "chat", "gen_ai.agent.name": "a", "gen_ai.request.model": "claude"}) != "LLM" || bench.InferSpanKind(map[string]any{"gen_ai.operation.name": "execute_tool", "gen_ai.agent.name": "a", "gen_ai.tool.name": "t"}) != "TOOL" {
		t.Fatal("operation name must take precedence over agent identity")
	}
	if bench.InferSpanKind(map[string]any{"gen_ai.agent.name": "Triage"}) != "AGENT" {
		t.Fatal("agent name must imply an agent span")
	}
}

func TestExternalSpansArriveAsOneMetadataOnlyTree(t *testing.T) {
	var raw []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { raw, _ = io.ReadAll(r.Body); w.WriteHeader(201) }))
	defer server.Close()
	client, err := bench.New(bench.Options{APIKey: "bench_sk_synthetic_test_key", Repository: "acme/support", Branch: "main", SystemName: "Customer support", Endpoint: server.URL})
	if err != nil {
		t.Fatal(err)
	}
	traceID := "0000000000000000000000000000ab01"
	started := time.Date(2025, 9, 16, 5, 20, 0, 0, time.UTC)
	client.RecordExternalSpan(bench.ExternalSpan{TraceID: traceID, SpanID: "0000000000000001", Name: "invoke_agent Triage", StartedAt: started, EndedAt: started.Add(time.Second), Attributes: map[string]any{"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": "Triage agent", "customer.email": "x@example.com"}})
	client.RecordExternalSpan(bench.ExternalSpan{TraceID: traceID, SpanID: "0000000000000002", ParentSpanID: "0000000000000001", Name: "chat", Model: "gpt-4.1-mini", StartedAt: started, EndedAt: started, Attributes: map[string]any{"gen_ai.operation.name": "chat", "gen_ai.provider.name": "openai", "gen_ai.usage.input_tokens": 12, "prompt": "secret"}})
	client.RecordExternalSpan(bench.ExternalSpan{TraceID: traceID, SpanID: "0000000000000003", ParentSpanID: "0000000000000001", Name: "execute_tool lookup_order", Status: "error", StartedAt: started, EndedAt: started, Attributes: map[string]any{"gen_ai.operation.name": "execute_tool", "gen_ai.tool.name": "lookup_order"}})
	client.RecordExternalSpan(bench.ExternalSpan{TraceID: "not-hex", SpanID: "0000000000000009", Name: "bad"})
	if stats := client.Stats(); stats.Queued != 3 || stats.Dropped != 1 {
		t.Fatalf("stats %+v", stats)
	}
	client.Shutdown(context.Background())
	var body struct {
		SystemName     string `json:"system_name"`
		CaptureContent bool   `json:"capture_content"`
		Traces         []struct {
			TraceID string `json:"trace_id"`
			Spans   []struct {
				ID         string         `json:"span_id"`
				Parent     string         `json:"parent_span_id"`
				Kind       string         `json:"kind"`
				Status     string         `json:"status"`
				Model      string         `json:"model_name"`
				Started    string         `json:"started_at"`
				Attributes map[string]any `json:"attributes"`
				Input      *string        `json:"input_value"`
			} `json:"spans"`
		} `json:"traces"`
	}
	if err = json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body.SystemName != "Customer support" || body.CaptureContent || len(body.Traces) != 3 {
		t.Fatalf("payload %s", raw)
	}
	spans := map[string]any{}
	for _, tr := range body.Traces {
		if tr.TraceID != traceID {
			t.Fatalf("trace id %s", tr.TraceID)
		}
		for _, s := range tr.Spans {
			spans[s.ID] = s
		}
	}
	agent := spans["0000000000000001"].(struct {
		ID         string         `json:"span_id"`
		Parent     string         `json:"parent_span_id"`
		Kind       string         `json:"kind"`
		Status     string         `json:"status"`
		Model      string         `json:"model_name"`
		Started    string         `json:"started_at"`
		Attributes map[string]any `json:"attributes"`
		Input      *string        `json:"input_value"`
	})
	if agent.Kind != "AGENT" || agent.Parent != "" || agent.Attributes["gen_ai.agent.name"] != "Triage agent" || agent.Attributes["customer.email"] != nil || agent.Started != "2025-09-16T05:20:00Z" {
		t.Fatalf("agent span %+v", agent)
	}
	for _, id := range []string{"0000000000000002", "0000000000000003"} {
		s := spans[id].(struct {
			ID         string         `json:"span_id"`
			Parent     string         `json:"parent_span_id"`
			Kind       string         `json:"kind"`
			Status     string         `json:"status"`
			Model      string         `json:"model_name"`
			Started    string         `json:"started_at"`
			Attributes map[string]any `json:"attributes"`
			Input      *string        `json:"input_value"`
		})
		if s.Parent != "0000000000000001" || s.Input != nil {
			t.Fatalf("child span %+v", s)
		}
		if id == "0000000000000002" && (s.Kind != "LLM" || s.Model != "gpt-4.1-mini" || s.Attributes["prompt"] != nil || s.Attributes["gen_ai.usage.input_tokens"] != float64(12)) {
			t.Fatalf("model span %+v", s)
		}
		if id == "0000000000000003" && (s.Kind != "TOOL" || s.Status != "error") {
			t.Fatalf("tool span %+v", s)
		}
	}
}
