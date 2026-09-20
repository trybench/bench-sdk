package bench_test

import (
	"context"
	bench "github.com/trybench/bench-sdk/go"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
)

type privacyTransport func(*http.Request) (*http.Response, error)

func (f privacyTransport) RoundTrip(r *http.Request) (*http.Response, error) { return f(r) }

func TestPrivacyEnvelopeAndBoundedText(t *testing.T) {
	var body string
	client, err := bench.New(bench.Options{APIKey: "bench_sk_synthetic_test_key", Repository: "fixture/go", Branch: "main", SystemName: "{\"email\":\"person@example.test\",\"password\":\"SYNTHETIC_PRIVATE_VALUE\",\"secret\":\"bench_sk_NOT_A_REAL_SECRET\"}", CaptureContent: true, Transport: privacyTransport(func(r *http.Request) (*http.Response, error) {
		b, _ := io.ReadAll(r.Body)
		body = string(b)
		return &http.Response{StatusCode: 201, Body: io.NopCloser(strings.NewReader("{}")), Header: make(http.Header)}, nil
	})})
	if err != nil {
		t.Fatal(err)
	}
	_, _ = bench.Trace(context.Background(), client, bench.SpanInput{Name: "privacy", Input: strings.Repeat("x", 1000000)}, func(context.Context) (bool, error) { return true, nil })
	client.Shutdown(context.Background())
	if strings.Contains(body, "person@example.test") || strings.Contains(body, "NOT_A_REAL_SECRET") || !strings.Contains(body, "[CONTENT_LIMIT]") || strings.Contains(body, "SYNTHETIC_PRIVATE_VALUE") {
		t.Fatal("privacy policy was not applied to envelope/content")
	}
	for _, branch := range []string{"person@example.test", "bench_sk_NOT_A_REAL_SECRET", "bad\nbranch"} {
		if _, err := bench.New(bench.Options{APIKey: "bench_sk_synthetic_test_key", Repository: "fixture/go", Branch: branch}); err == nil {
			t.Fatal("unsafe identity accepted")
		}
	}
}

func TestConcurrentFlushAndShutdownHonorCallerDeadline(t *testing.T) {
	entered, release, finished := make(chan struct{}), make(chan struct{}), make(chan struct{})
	client, err := bench.New(bench.Options{APIKey: "bench_sk_synthetic_test_key", Repository: "fixture/go", Branch: "main", Transport: privacyTransport(func(r *http.Request) (*http.Response, error) {
		close(entered)
		<-release
		return &http.Response{StatusCode: 201, Body: io.NopCloser(strings.NewReader("{}")), Header: make(http.Header)}, nil
	})})
	if err != nil {
		t.Fatal(err)
	}
	_, _ = bench.Trace(context.Background(), client, bench.SpanInput{Name: "request"}, func(context.Context) (bool, error) { return true, nil })
	go func() { client.Flush(context.Background()); close(finished) }()
	<-entered
	defer func() { close(release); <-finished }()
	for _, flush := range []func(context.Context){client.Flush, client.Shutdown} {
		ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
		done := make(chan struct{})
		go func() { flush(ctx); close(done) }()
		select {
		case <-done:
		case <-time.After(250 * time.Millisecond):
			cancel()
			t.Fatal("waiting delivery ignored cancellation")
		}
		cancel()
	}
}
