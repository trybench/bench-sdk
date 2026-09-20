package bench

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

type refundSession struct {
	client *Client
	cents  int
	fixed  bool
	closed *int
}

func TestObserverBelongsToApplicationTree(t *testing.T) {
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/refunds", Branch: "dev"})
	report, err := c.EvaluateSystem(context.Background(), SystemEvaluationOptions{
		SourceRevision: strings.Repeat("a", 40), ContextRevision: "v1",
		Cases: []SystemCase{{ID: "state", Input: 1, ExpectedState: Expect(1), RequiredTools: []string{"read-ledger"}}},
		Run:   func(context.Context, any) (any, error) { return "done", nil },
		Observe: func(ctx context.Context, _ string) (any, error) {
			return Trace(ctx, c, SpanInput{Name: "read-ledger", Kind: "TOOL"}, func(context.Context) (any, error) { return 1, nil })
		},
	})
	if err != nil || !report.Passed() {
		t.Fatalf("evaluation: %v %+v", err, report)
	}
	roots := 0
	for _, span := range report.Cases[0].Spans {
		if span.Parent == "" {
			roots++
		}
	}
	if roots != 1 || len(report.Cases[0].Spans) != 2 || c.Stats().Queued != 0 {
		t.Fatalf("disconnected observer: %+v", report.Cases[0])
	}
}

func TestPublishApplicationReportIsExplicitAndRedacted(t *testing.T) {
	count := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		count++
		body, _ := io.ReadAll(r.Body)
		if r.URL.Path != "/api/ai-systems/3/runtime-evaluations" || strings.Contains(string(body), "person@example.test") {
			t.Error("incorrect or unsafe report")
		}
		w.WriteHeader(201)
	}))
	defer server.Close()
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/refunds", Branch: "dev", Endpoint: server.URL})
	report, err := c.EvaluateSystem(context.Background(), SystemEvaluationOptions{SourceRevision: strings.Repeat("a", 40), ContextRevision: "v1", Cases: []SystemCase{{ID: "privacy", Input: 1, ExpectedOutput: Expect("person@example.test")}}, Run: func(context.Context, any) (any, error) { return "person@example.test", nil }})
	if err != nil || !report.Passed() || count != 0 {
		t.Fatalf("evaluation: %v %+v", err, report)
	}
	if err = c.PublishSystemEvaluation(context.Background(), 3, report); err != nil {
		t.Fatal(err)
	}
	if count != 1 {
		t.Fatal("explicit publication missing")
	}
}

func (s *refundSession) Turn(ctx context.Context, message any) (any, error) {
	return Trace(ctx, s.client, SpanInput{Name: "refund", Kind: "TOOL"}, func(context.Context) (any, error) {
		if !s.fixed || s.cents == 0 {
			s.cents += 12000
		}
		return "Refunded", nil
	})
}
func (s *refundSession) Observe(context.Context) (any, error) {
	return map[string]any{"cents": s.cents}, nil
}
func (s *refundSession) Close(context.Context) error { *s.closed++; s.cents = 0; return nil }

func TestSimulationChecksStateBeforeReset(t *testing.T) {
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/refunds", Branch: "dev"})
	closed := 0
	opts := SimulationOptions{SourceRevision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ContextRevision: "v1", Cases: []SystemCase{{ID: "retry", Input: SimulationInput{InitialState: map[string]any{"cents": 0}, Turns: []any{"Refund", "Try again"}}, ExpectedOutput: Expect("Refunded"), ExpectedState: Expect(map[string]any{"cents": 12000})}}}
	opts.CreateSession = func(context.Context, any) (SimulationSession, error) {
		return &refundSession{client: c, closed: &closed}, nil
	}
	bad, err := c.SimulateSystem(context.Background(), opts)
	if err != nil {
		t.Fatal(err)
	}
	opts.CreateSession = func(context.Context, any) (SimulationSession, error) {
		return &refundSession{client: c, fixed: true, closed: &closed}, nil
	}
	good, err := c.SimulateSystem(context.Background(), opts)
	if err != nil {
		t.Fatal(err)
	}
	if bad.Passed() || !good.Passed() || closed != 2 || bad.SuiteHash != good.SuiteHash {
		t.Fatalf("bad=%+v good=%+v closed=%d", bad.Summary, good.Summary, closed)
	}
	if !jsonEqual(*bad.Cases[0].ObservedState, map[string]any{"cents": 24000}) {
		t.Fatal("state reset before observation")
	}
}

func TestApplicationEvaluationDetectsHarnessDefect(t *testing.T) {
	zero := 0.0
	c, err := New(Options{APIKey: "bench_sk_fixture", Repository: "test/refunds", Branch: "dev", SampleRate: &zero})
	if err != nil {
		t.Fatal(err)
	}
	options := SystemEvaluationOptions{SourceRevision: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", ContextRevision: "v1",
		Cases: []SystemCase{{ID: "late-refund", Input: 45, ExpectedOutput: Expect(false), ForbiddenTools: []string{"issue-refund"}}},
		Run: func(ctx context.Context, input any) (any, error) {
			_, err := Trace(ctx, c, SpanInput{Name: "issue-refund", Kind: "TOOL"}, func(ctx context.Context) (bool, error) { return true, nil })
			return false, err
		},
	}
	bad, err := c.EvaluateSystem(context.Background(), options)
	if err != nil {
		t.Fatal(err)
	}
	options.Run = func(context.Context, any) (any, error) { return false, nil }
	good, err := c.EvaluateSystem(context.Background(), options)
	if err != nil {
		t.Fatal(err)
	}
	if bad.Summary.Score == nil || *bad.Summary.Score != 0 || good.Summary.Score == nil || *good.Summary.Score != 100 {
		t.Fatalf("bad=%+v good=%+v", bad.Summary, good.Summary)
	}
	if bad.SuiteHash != good.SuiteHash || len(bad.Cases[0].Spans) != 2 || bad.Cases[0].Findings[0].Category != "harness" || c.Stats().Queued != 0 {
		t.Fatal("missing isolated application evidence")
	}
}

func TestIncompleteApplicationsNeverPass(t *testing.T) {
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/app", Branch: "dev"})
	for _, item := range []SystemCase{{ID: "none", Input: 1}, {ID: "state", Input: 1, ExpectedState: Expect(nil)}} {
		report, err := c.EvaluateSystem(context.Background(), SystemEvaluationOptions{SourceRevision: strings.Repeat("a", 40), ContextRevision: "v1", Cases: []SystemCase{item}, Run: func(context.Context, any) (any, error) { return 1, nil }})
		if err != nil || report.Passed() || report.Summary.Score != nil {
			t.Fatalf("incomplete report passed: %v %+v", err, report)
		}
	}
	report, err := c.EvaluateSystem(context.Background(), SystemEvaluationOptions{SourceRevision: strings.Repeat("a", 40), ContextRevision: "v1", Cases: []SystemCase{{ID: "null", Input: nil, ExpectedOutput: Expect(nil)}}, Run: func(context.Context, any) (any, error) { return nil, nil }})
	if err != nil || !report.Passed() {
		t.Fatalf("explicit null assertion failed: %v %+v", err, report)
	}
}

func TestTimeoutAndCancellationStopNextCase(t *testing.T) {
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/app", Branch: "dev"})
	for _, external := range []bool{false, true} {
		ctx, cancel := context.WithCancel(context.Background())
		stopped := make(chan struct{})
		calls := 0
		options := SystemEvaluationOptions{SourceRevision: strings.Repeat("a", 40), ContextRevision: "v1", Timeout: 20 * time.Millisecond, Cases: []SystemCase{{ID: "one", Input: 1, ExpectedOutput: Expect(1)}, {ID: "two", Input: 2, ExpectedOutput: Expect(2)}}, Run: func(ctx context.Context, input any) (any, error) {
			calls++
			if external {
				cancel()
			}
			<-ctx.Done()
			close(stopped)
			return nil, ctx.Err()
		}}
		report, err := c.EvaluateSystem(ctx, options)
		<-stopped
		cancel()
		if err != nil || report.Passed() || report.Summary.Score != nil || len(report.Cases) != 1 || calls != 1 {
			t.Fatalf("timeout did not stop suite: %v %+v", err, report)
		}
	}
}

func TestUnfinishedToolsAndCaptureOverflowStayIncomplete(t *testing.T) {
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/app", Branch: "dev"})
	var pending *Span
	options := SystemEvaluationOptions{SourceRevision: strings.Repeat("a", 40), ContextRevision: "v1", Cases: []SystemCase{{ID: "one", Input: 1, ExpectedOutput: Expect(1)}}, Run: func(ctx context.Context, input any) (any, error) {
		_, pending = c.StartSpan(ctx, SpanInput{Name: "unfinished", Kind: "TOOL"})
		return 1, nil
	}}
	report, err := c.EvaluateSystem(context.Background(), options)
	pending.SetOutput(1)
	pending.End()
	if err != nil || report.Passed() || report.Summary.Score != nil {
		t.Fatalf("unfinished tool passed: %v %+v", err, report)
	}
	options.Run = func(ctx context.Context, input any) (any, error) {
		for i := 0; i < 100; i++ {
			_, _ = Trace(ctx, c, SpanInput{Name: "tool", Kind: "TOOL"}, func(context.Context) (int, error) { return i, nil })
		}
		return 1, nil
	}
	report, err = c.EvaluateSystem(context.Background(), options)
	if err != nil || report.Passed() || report.Summary.Score != nil || c.Stats().Queued != 0 {
		t.Fatalf("overflow passed: %v %+v", err, report)
	}
}

func TestLargeIntegerInputIsNotChanged(t *testing.T) {
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/app", Branch: "dev"})
	id := int64(9007199254740993)
	report, err := c.EvaluateSystem(context.Background(), SystemEvaluationOptions{SourceRevision: strings.Repeat("a", 40), ContextRevision: "v1", Cases: []SystemCase{{ID: "large-id", Input: id, ExpectedOutput: Expect(id)}}, Run: func(ctx context.Context, input any) (any, error) { return input, nil }})
	if err != nil || !report.Passed() {
		t.Fatalf("input changed: %v %+v", err, report)
	}
}

type echoSession struct{ state, turn any }

func (s *echoSession) Turn(_ context.Context, value any) (any, error) {
	s.turn = value
	return value, nil
}
func (s *echoSession) Observe(context.Context) (any, error) { return s.state, nil }
func (s *echoSession) Close(context.Context) error          { return nil }
func TestSimulationPreservesLargeIdentifiers(t *testing.T) {
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/app", Branch: "dev"})
	id := int64(9007199254740993)
	report, err := c.SimulateSystem(context.Background(), SimulationOptions{SourceRevision: strings.Repeat("a", 40), ContextRevision: "v1", Cases: []SystemCase{{ID: "large-id", Input: SimulationInput{InitialState: id, Turns: []any{id}}, ExpectedOutput: Expect(id), ExpectedState: Expect(id)}}, CreateSession: func(_ context.Context, state any) (SimulationSession, error) { return &echoSession{state: state}, nil }})
	if err != nil || !report.Passed() {
		t.Fatalf("simulation changed identifiers: %v %+v", err, report)
	}
}

func TestToolMeasurementsSurviveMetadataOnlyCapture(t *testing.T) {
	var body map[string]any
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewDecoder(r.Body).Decode(&body)
		w.WriteHeader(201)
	}))
	defer server.Close()
	c, _ := New(Options{APIKey: "bench_sk_fixture", Repository: "test/app", Branch: "dev", Endpoint: server.URL})
	_, _ = Trace(context.Background(), c, SpanInput{Name: "search", Kind: "TOOL", Input: "private query", Attributes: map[string]any{"gen_ai.tool.name": "search", "bench.cost.usd": 0.002, "bench.cost.source": "reported", "bench.duration_ms": -1, "private": "customer input"}}, func(context.Context) (string, error) { time.Sleep(5 * time.Millisecond); return "private output", nil })
	c.Flush(context.Background())
	span := body["traces"].([]any)[0].(map[string]any)["spans"].([]any)[0].(map[string]any)
	attrs := span["attributes"].(map[string]any)
	if attrs["bench.cost.usd"] != 0.002 || attrs["gen_ai.tool.name"] != "search" || attrs["bench.duration_ms"].(float64) < 1 || attrs["private"] != nil || span["input_value"] != nil || span["output_value"] != nil {
		t.Fatalf("incorrect metadata: %+v", span)
	}
}
