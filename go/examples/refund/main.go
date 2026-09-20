// Run from go/: go run ./examples/refund. No upload or model call.
package main

import (
	"context"
	"fmt"
	bench "github.com/trybench/bench-sdk/go"
	"strings"
)

type session struct {
	client  *bench.Client
	refunds int
	fixed   bool
}

func (s *session) Turn(ctx context.Context, _ any) (any, error) {
	return bench.Trace(ctx, s.client, bench.SpanInput{Name: "refund", Kind: "TOOL"}, func(context.Context) (any, error) {
		if !s.fixed || s.refunds == 0 {
			s.refunds++
		}
		return "Refunded", nil
	})
}
func (s *session) Observe(context.Context) (any, error) {
	return map[string]any{"refunds": s.refunds}, nil
}
func (s *session) Close(context.Context) error { s.refunds = 0; return nil }
func main() {
	client, err := bench.New(bench.Options{APIKey: "bench_sk_local_example", Repository: "example/refunds", Branch: "test"})
	if err != nil {
		panic(err)
	}
	options := bench.SimulationOptions{SourceRevision: strings.Repeat("a", 40), ContextRevision: "refund-policy-v1", Cases: []bench.SystemCase{{ID: "refund-retry", Input: bench.SimulationInput{InitialState: map[string]any{"refunds": 0}, Turns: []any{"Refund order A", "Retry that refund"}}, ExpectedOutput: bench.Expect("Refunded"), ExpectedState: bench.Expect(map[string]any{"refunds": 1})}}}
	for _, fixed := range []bool{false, true} {
		options.CreateSession = func(context.Context, any) (bench.SimulationSession, error) {
			return &session{client: client, fixed: fixed}, nil
		}
		report, err := client.SimulateSystem(context.Background(), options)
		if err != nil {
			panic(err)
		}
		if report.Passed() != fixed {
			panic("unexpected simulation result")
		}
		fmt.Printf("fixed=%t passed=%t\n", fixed, report.Passed())
	}
	client.Shutdown(context.Background())
}
