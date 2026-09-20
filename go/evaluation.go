package bench

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"
)

// Expect distinguishes an explicit JSON null assertion from an omitted assertion.
func Expect(value any) *any { return &value }

type SystemCase struct {
	ID              string   `json:"id"`
	Input           any      `json:"input"`
	Split           string   `json:"split,omitempty"`
	ExpectedOutput  *any     `json:"expectedOutput,omitempty"`
	ExpectedState   *any     `json:"expectedState,omitempty"`
	BusinessOutcome string   `json:"businessOutcome,omitempty"`
	RequiredTools   []string `json:"requiredTools,omitempty"`
	ForbiddenTools  []string `json:"forbiddenTools,omitempty"`
	MaxToolCalls    *int     `json:"maxToolCalls,omitempty"`
	MaxModelCalls   *int     `json:"maxModelCalls,omitempty"`
}

// UnmarshalJSON preserves an explicit null expected outcome while pinning cases.
func (c *SystemCase) UnmarshalJSON(data []byte) error {
	type plain SystemCase
	var value plain
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&value); err != nil {
		return err
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(data, &fields); err != nil {
		return err
	}
	for key, target := range map[string]**any{"expectedOutput": &value.ExpectedOutput, "expectedState": &value.ExpectedState} {
		if raw, ok := fields[key]; ok {
			var decoded any
			d := json.NewDecoder(bytes.NewReader(raw))
			d.UseNumber()
			if err := d.Decode(&decoded); err != nil {
				return err
			}
			*target = Expect(decoded)
		}
	}
	*c = SystemCase(value)
	return nil
}

type SystemEvaluationOptions struct {
	SourceRevision, ContextRevision string
	Cases                           []SystemCase
	Run                             func(context.Context, any) (any, error)
	Observe                         func(context.Context, string) (any, error)
	Timeout                         time.Duration
}
type SystemCheck struct {
	ID     string `json:"id"`
	Passed bool   `json:"passed"`
	Reason string `json:"reason"`
}
type RuntimeFinding struct {
	Category        string   `json:"category"`
	Title           string   `json:"title"`
	EvidenceSpanIDs []string `json:"evidence_span_ids"`
	Confidence      string   `json:"confidence"`
	FixBrief        string   `json:"fix_brief"`
}
type SystemCaseResult struct {
	ID             string           `json:"id"`
	Split          string           `json:"split"`
	Status         string           `json:"status"`
	CaseDefinition any              `json:"case_definition,omitempty"`
	Output         any              `json:"output"`
	ObservedState  *any             `json:"observedState,omitempty"`
	Spans          []wireSpan       `json:"spans"`
	Checks         []SystemCheck    `json:"checks"`
	Findings       []RuntimeFinding `json:"findings"`
	Error          string           `json:"error,omitempty"`
}
type SystemSummary struct {
	Status   string   `json:"status"`
	Score    *float64 `json:"score"`
	Passed   int      `json:"passed"`
	Failed   int      `json:"failed"`
	Errors   int      `json:"errors"`
	Unscored int      `json:"unscored"`
}
type SystemEvaluationReport struct {
	SchemaVersion    int                `json:"schema_version"`
	ExecutionMode    string             `json:"execution_mode"`
	EvidenceOrigin   string             `json:"evidence_origin"`
	Environment      string             `json:"environment"`
	SourceRevision   string             `json:"source_revision"`
	ContextRevision  string             `json:"context_revision"`
	SuiteHash        string             `json:"suite_hash"`
	PlannedCaseCount int                `json:"planned_case_count"`
	Coverage         map[string]any     `json:"coverage"`
	Cases            []SystemCaseResult `json:"cases"`
	Summary          SystemSummary      `json:"summary"`
}

// Passed is a strict CI gate: complete coverage, no failed/error/unscored cases.
func (r SystemEvaluationReport) Passed() bool {
	if r.Summary.Status != "completed" || r.PlannedCaseCount == 0 || len(r.Cases) != r.PlannedCaseCount {
		return false
	}
	for _, c := range r.Cases {
		if c.Status != "passed" {
			return false
		}
	}
	return true
}

type evaluationCapture struct {
	mu              sync.Mutex
	active, limited bool
	pending         int
	spans           []wireSpan
}

func (e *evaluationCapture) start() { e.mu.Lock(); defer e.mu.Unlock(); e.pending++ }
func (e *evaluationCapture) finish(row *wireSpan) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.pending--
	if e.active {
		if row == nil || len(e.spans) >= 100 {
			e.limited = true
		} else {
			e.spans = append(e.spans, *row)
		}
	}
}
func (e *evaluationCapture) freeze() ([]wireSpan, bool, int) {
	e.mu.Lock()
	defer e.mu.Unlock()
	e.active = false
	return append([]wireSpan{}, e.spans...), e.limited, e.pending
}

func snapshot(value any) (any, error) {
	raw, err := json.Marshal(value)
	if err != nil {
		return nil, err
	}
	var result any
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	err = d.Decode(&result)
	return result, err
}
func jsonEqual(a, b any) bool {
	x, e := json.Marshal(a)
	y, f := json.Marshal(b)
	return e == nil && f == nil && bytes.Equal(x, y)
}
func (c *Client) safeEvidence(value any) (result any, err error) {
	defer func() {
		if recover() != nil {
			err = errors.New("application evidence could not be redacted")
		}
	}()
	return c.safe(value)
}

func validateEvaluation(options SystemEvaluationOptions) ([]SystemCase, error) {
	if !regexp.MustCompile(`^[a-f0-9]{40}$`).MatchString(options.SourceRevision) || len(options.ContextRevision) < 1 || len(options.ContextRevision) > 200 || options.Run == nil {
		return nil, errors.New("pin source and context revisions and provide a real application entry point")
	}
	if len(options.Cases) < 1 || len(options.Cases) > 100 || options.Timeout < 0 || options.Timeout > 300*time.Second {
		return nil, errors.New("use 1 to 100 cases and a timeout up to 300 seconds")
	}
	raw, err := json.Marshal(options.Cases)
	if err != nil {
		return nil, err
	}
	if len(raw) > 500000 {
		return nil, errors.New("case suite exceeds 500 KB")
	}
	var cases []SystemCase
	if err = json.Unmarshal(raw, &cases); err != nil {
		return nil, err
	}
	ids := map[string]bool{}
	for _, item := range cases {
		if item.ID == "" || len(item.ID) > 100 || ids[item.ID] {
			return nil, errors.New("case IDs must be unique and nonempty")
		}
		ids[item.ID] = true
		switch item.Split {
		case "", "capability", "incident", "regression", "holdout":
		default:
			return nil, errors.New("invalid case split")
		}
		if len(item.BusinessOutcome) > 2000 || len(item.RequiredTools)+len(item.ForbiddenTools) > 90 {
			return nil, errors.New("case assertions exceed limits")
		}
		for _, names := range [][]string{item.RequiredTools, item.ForbiddenTools} {
			for _, name := range names {
				if len(name) < 1 || len(name) > 200 {
					return nil, errors.New("invalid tool name")
				}
			}
		}
		for _, limit := range []*int{item.MaxToolCalls, item.MaxModelCalls} {
			if limit != nil && (*limit < 0 || *limit > 10000) {
				return nil, errors.New("call limits must be nonnegative integers up to 10000")
			}
		}
	}
	return cases, nil
}

func scoreCase(item SystemCase, output, state any, observed bool, spans []wireSpan, failure string) SystemCaseResult {
	r := SystemCaseResult{ID: item.ID, Split: item.Split, Spans: spans, Checks: []SystemCheck{}, Findings: []RuntimeFinding{}, Output: output, Error: failure}
	if r.Split == "" {
		r.Split = "regression"
	}
	root := false
	tools, models := 0, 0
	ids := []string{}
	for _, s := range spans {
		ids = append(ids, s.ID)
		if s.Parent == "" && s.Kind == "AGENT" && s.Status == "ok" {
			root = true
		}
		if s.Kind == "TOOL" {
			tools++
		}
		if s.Kind == "LLM" {
			models++
		}
	}
	if !root && r.Error == "" {
		r.Error = "Application root was not recorded. No complete score."
	}
	if item.ExpectedState != nil && !observed && r.Error == "" {
		r.Error = "Application state was not observed. No complete score."
	}
	check := func(id string, passed bool, reason string) {
		r.Checks = append(r.Checks, SystemCheck{id, passed, reason})
	}
	if item.ExpectedOutput != nil {
		check("expected-output", jsonEqual(output, *item.ExpectedOutput), "Compare actual output with the expected outcome.")
	}
	if item.ExpectedState != nil && observed {
		check("expected-state", jsonEqual(state, *item.ExpectedState), "Compare observed tool effects with expected business state.")
	}
	for _, name := range item.RequiredTools {
		found := false
		for _, s := range spans {
			found = found || (s.Kind == "TOOL" && s.Name == name && s.Status == "ok")
		}
		check("required-tool:"+name, found, "A successful recorded tool call is required.")
	}
	for _, name := range item.ForbiddenTools {
		found := false
		for _, s := range spans {
			found = found || (s.Kind == "TOOL" && s.Name == name)
		}
		check("forbidden-tool:"+name, !found, "This tool must not be invoked.")
	}
	if item.MaxToolCalls != nil {
		check("tool-call-limit", tools <= *item.MaxToolCalls, fmt.Sprintf("%d recorded tool calls", tools))
	}
	if item.MaxModelCalls != nil {
		check("model-call-limit", models <= *item.MaxModelCalls, fmt.Sprintf("%d recorded model calls", models))
	}
	for _, s := range spans {
		if s.Kind == "TOOL" && s.Status == "error" {
			r.Findings = append(r.Findings, RuntimeFinding{"tool", s.Name + " failed", []string{s.ID}, "observed_failure", "Inspect this tool's contract and dependencies. Reproduce the failure and rerun incident and regression cases."})
		}
	}
	passed := true
	for _, check := range r.Checks {
		if !check.Passed {
			passed = false
			category := "harness"
			if check.ID == "expected-output" || check.ID == "expected-state" {
				category = "quality"
			}
			r.Findings = append(r.Findings, RuntimeFinding{category, check.ID, ids, "hypothesis", "Inspect the real application path, tools, state, routing and retries. Rerun unchanged incident, regression and holdout cases."})
		}
	}
	switch {
	case r.Error != "":
		r.Status = "error"
	case len(r.Checks) == 0:
		r.Status = "unscored"
	case passed:
		r.Status = "passed"
	default:
		r.Status = "failed"
	}
	return r
}

// EvaluateSystem runs callbacks locally; it never silently publishes a report or calls a judge.
// Callbacks must honor their context. A deadline cannot forcibly terminate a goroutine.
func (c *Client) EvaluateSystem(ctx context.Context, options SystemEvaluationOptions) (SystemEvaluationReport, error) {
	cases, err := validateEvaluation(options)
	if err != nil {
		return SystemEvaluationReport{}, err
	}
	c.mu.Lock()
	closed := c.closed
	c.mu.Unlock()
	parent, _ := ctx.Value(contextKey{c}).(traceContext)
	if closed || parent.evaluation != nil {
		return SystemEvaluationReport{}, errors.New("use an open client outside another application evaluation")
	}
	timeout := options.Timeout
	if timeout == 0 {
		timeout = 30 * time.Second
	}
	pinned, _ := json.Marshal(map[string]any{"context": options.ContextRevision, "cases": cases})
	digest := sha256.Sum256(pinned)
	env := c.options.Environment
	if env == "" {
		env = "unspecified"
	}
	report := SystemEvaluationReport{SchemaVersion: 1, ExecutionMode: "application_runtime", EvidenceOrigin: "sdk_client_reported", Environment: env, SourceRevision: options.SourceRevision, ContextRevision: options.ContextRevision, SuiteHash: hex.EncodeToString(digest[:]), PlannedCaseCount: len(cases), Cases: []SystemCaseResult{}, Coverage: map[string]any{"instrumentation": "explicit_spans", "tool_dependencies": "application_configured", "hosted_validation": false}}
	for _, item := range cases {
		if ctx.Err() != nil {
			break
		}
		capture := &evaluationCapture{active: true}
		runCtx, cancel := context.WithTimeout(ctx, timeout)
		runCtx = context.WithValue(runCtx, contextKey{c}, traceContext{evaluation: capture})
		runCtx = context.WithValue(runCtx, evaluationCaseKey{}, item.ID)
		type outcome struct {
			output, state any
			observed      bool
			failed        bool
		}
		done := make(chan outcome, 1)
		go func(item SystemCase) {
			result := outcome{}
			defer func() {
				if recover() != nil {
					result.failed = true
				}
				done <- result
			}()
			input, e := snapshot(item.Input)
			if e != nil {
				result.failed = true
				return
			}
			inner, root := c.StartSpan(runCtx, SpanInput{Name: "system-entrypoint", Kind: "AGENT", Input: input})
			defer root.End()
			output, e := options.Run(inner, input)
			if e != nil {
				result.failed = true
				return
			}
			result.output, e = snapshot(output)
			if e != nil {
				result.failed = true
				return
			}
			if options.Observe != nil {
				result.state, e = options.Observe(inner, item.ID)
				if e == nil {
					result.state, e = snapshot(result.state)
				}
				result.observed = e == nil
				result.failed = e != nil
			}
			if !result.failed {
				root.SetOutput(output)
			}
		}(item)
		result := outcome{}
		failure := ""
		stopped := false
		select {
		case result = <-done:
			if result.failed {
				failure = "Application execution failed. Inspect recorded spans."
			}
		case <-runCtx.Done():
			stopped = true
			failure = "Application timed out or was stopped. No complete score."
		}
		if runCtx.Err() != nil {
			stopped = true
			failure = "Application timed out or was stopped. No complete score."
		}
		cancel()
		spans, limited, pending := capture.freeze()
		if limited || pending > 0 {
			failure = "Application evidence is incomplete. Await all tools and stay within capture limits."
			stopped = stopped || pending > 0
		}
		row := scoreCase(item, result.output, result.state, result.observed, spans, failure)
		row.CaseDefinition, err = c.safeEvidence(item)
		if err == nil {
			row.Output, err = c.safeEvidence(result.output)
		}
		if err == nil && result.observed {
			var state any
			state, err = c.safeEvidence(result.state)
			row.ObservedState = Expect(state)
		}
		if err != nil {
			row.Status = "error"
			row.Error = "Application evidence could not be redacted."
			row.Output = nil
		}
		report.Cases = append(report.Cases, row)
		if stopped {
			break
		}
	}
	report.Summary.Status = "incomplete"
	for _, item := range report.Cases {
		switch item.Status {
		case "passed":
			report.Summary.Passed++
		case "failed":
			report.Summary.Failed++
		case "error":
			report.Summary.Errors++
		default:
			report.Summary.Unscored++
		}
	}
	if len(report.Cases) == len(cases) && report.Summary.Errors == 0 && report.Summary.Unscored == 0 {
		report.Summary.Status = "completed"
		score := 100 * float64(report.Summary.Passed) / float64(len(cases))
		report.Summary.Score = &score
	}
	return report, nil
}

// PublishSystemEvaluation explicitly saves evidence without running a paid evaluation.
func (c *Client) PublishSystemEvaluation(ctx context.Context, systemID int64, report SystemEvaluationReport) (err error) {
	defer func() {
		if recover() != nil {
			err = errors.New("application report could not be redacted")
		}
	}()
	if systemID < 1 || systemID > 9007199254740991 {
		return errors.New("select a system")
	}
	var value any = report
	if c.options.Redact != nil {
		value = c.options.Redact(value)
	}
	value, err = snapshot(value)
	if err != nil {
		return errors.New("invalid application report")
	}
	body, err := json.Marshal(scrubLimit(value, 0, 300))
	if err != nil {
		return err
	}
	if len(body) > 500000 {
		return errors.New("runtime report exceeds 500 KB; retain it locally or split the suite")
	}
	url := strings.TrimSuffix(c.endpoint, "/api/traces") + fmt.Sprintf("/api/ai-systems/%d/runtime-evaluations", systemID)
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(body))
	if err != nil {
		return err
	}
	request.Header.Set("Authorization", "Bearer "+c.options.APIKey)
	request.Header.Set("Content-Type", "application/json")
	response, err := c.http.Do(request)
	if err != nil {
		return errors.New("could not save application results; local results remain available")
	}
	defer response.Body.Close()
	_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		return fmt.Errorf("could not save application results (HTTP %d); local results remain available", response.StatusCode)
	}
	return nil
}
