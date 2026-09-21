package bench

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestPlatformCatalogRouting(t *testing.T) {
	var current platformOperation
	count := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		route := current.Path
		for key := range current.PathParameters {
			route = strings.ReplaceAll(route, "{"+key+"}", "42")
		}
		if r.Method != current.Method || r.URL.Path != route {
			t.Errorf("wrong route %s %s", r.Method, r.URL.Path)
		}
		expected := ""
		if current.Auth != "public" {
			expected = "Bearer fixture"
			count++
		}
		if r.Header.Get("Authorization") != expected {
			t.Error("incorrect credential forwarding")
		}
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()
	resolutions := 0
	p, err := NewPlatform(PlatformOptions{Endpoint: server.URL, Token: func(context.Context) (string, error) { resolutions++; return "fixture", nil }})
	if err != nil {
		t.Fatal(err)
	}
	for _, op := range p.operations {
		current = op
		path := map[string]string{}
		for key := range op.PathParameters {
			path[key] = "42"
		}
		out, err := p.Call(context.Background(), op.ID, PlatformRequest{Path: path})
		if err != nil || string(out) != `{"ok":true}` {
			t.Fatal(op.ID, err, string(out))
		}
	}
	if count < 100 || resolutions != count {
		t.Fatal("credential renewal or catalog incomplete", count, resolutions)
	}
}
func TestPlatformMultipartAndErrors(t *testing.T) {
	calls := 0
	status := 200
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if status != 200 {
			w.WriteHeader(status)
			w.Write([]byte(`{"error":{"code":"upgrade_required","message":"Growth required","reference":"BENCH-TEST","pricing_url":"https://stg.usebench.ai/plans","upgrade_url":"https://stg.usebench.ai/plans","payment_confirmation_required":true}}`))
			return
		}
		if err := r.ParseMultipartForm(10000); err != nil {
			t.Error(err)
			return
		}
		defer r.MultipartForm.RemoveAll()
		if r.FormValue("content_consent") != "true" {
			t.Error("consent lost")
		}
		var mapping map[string]string
		if json.Unmarshal([]byte(r.FormValue("mapping")), &mapping) != nil || mapping["input"] != "question" {
			t.Error("mapping lost")
		}
		f, _, err := r.FormFile("file")
		if err != nil {
			t.Error(err)
			return
		}
		defer f.Close()
		b, _ := io.ReadAll(f)
		if string(b) != string([]byte{0, 255, 128}) {
			t.Error("binary content changed")
		}
		w.Write([]byte(`{"ok":true}`))
	}))
	defer server.Close()
	p, _ := NewPlatform(PlatformOptions{Endpoint: server.URL, Credential: "fixture"})
	_, err := p.Call(context.Background(), "upload_dataset", PlatformRequest{Path: map[string]string{"id": "1"}, Form: map[string]any{"content_consent": true, "mapping": map[string]string{"input": "question"}}, Files: []PlatformFile{{Name: "cases.xlsx", Content: []byte{0, 255, 128}}}})
	if err != nil {
		t.Fatal(err)
	}
	status = 403
	_, err = p.Call(context.Background(), "whoami", PlatformRequest{})
	var apiErr *PlatformError
	if !errors.As(err, &apiErr) || apiErr.Code != "upgrade_required" || apiErr.Reference != "BENCH-TEST" || apiErr.Details["upgrade_url"] != "https://stg.usebench.ai/plans" || apiErr.Details["payment_confirmation_required"] != true || apiErr.Retryable() {
		t.Fatal(err)
	}
	if calls != 2 {
		t.Fatal("unexpected retry")
	}
}
func TestPlatformRejectsTraversalAndRedirects(t *testing.T) {
	calls := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		w.Header().Set("Location", "http://127.0.0.1:1/secret")
		w.WriteHeader(302)
	}))
	defer server.Close()
	p, _ := NewPlatform(PlatformOptions{Endpoint: server.URL, Credential: "fixture"})
	for _, value := range []string{"..", "%2fadmin", "a/b", "a\\b", "a\n"} {
		if _, err := p.Call(context.Background(), "get_system", PlatformRequest{Path: map[string]string{"id": value}}); err == nil {
			t.Fatal("unsafe path accepted")
		}
	}
	if calls != 0 {
		t.Fatal("unsafe request sent")
	}
	if _, err := p.Call(context.Background(), "whoami", PlatformRequest{}); err == nil {
		t.Fatal("redirect accepted")
	}
	if calls != 1 {
		t.Fatal("redirect followed")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if _, err := p.Call(ctx, "whoami", PlatformRequest{}); err == nil {
		t.Fatal("cancellation ignored")
	}
}
func TestPlatformStreamError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/x-ndjson")
		w.Write([]byte("{\"type\":\"progress\"}\n{\"type\":\"error\",\"message\":\"failed\"}\n"))
	}))
	defer server.Close()
	p, _ := NewPlatform(PlatformOptions{Endpoint: server.URL, Credential: "fixture"})
	_, err := p.Call(context.Background(), "whoami", PlatformRequest{})
	var apiErr *PlatformError
	if !errors.As(err, &apiErr) || apiErr.Code != "stream_error" {
		t.Fatal(err)
	}
}
