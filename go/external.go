package bench

import (
	"encoding/json"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// ExternalSpan is a finished span produced by another tracer: an OpenTelemetry
// exporter or a framework callback. IDs, timing and parent links come from
// that tracer, so Bench rebuilds the agent/model/tool tree the framework
// actually executed. This is the framework-agnostic path to SDK-first
// discovery: any framework that emits OpenTelemetry GenAI spans is covered
// without a Bench adapter for it.
type ExternalSpan struct {
	// 32 lowercase hex characters.
	TraceID string
	// 16 lowercase hex characters; ParentSpanID may be empty for a root span.
	SpanID, ParentSpanID string
	Name                 string
	// LLM | TOOL | CHAIN | RETRIEVER | AGENT | EMBEDDING; inferred from GenAI
	// attributes when empty.
	Kind               string
	Model              string
	Status             string // "ok" (default) or "error"
	StartedAt, EndedAt time.Time
	Attributes         map[string]any
	Input, Output      any
}

var (
	hexTrace = regexp.MustCompile(`^[0-9a-f]{32}$`)
	hexSpan  = regexp.MustCompile(`^[0-9a-f]{16}$`)
)

func attributeText(attributes map[string]any, keys ...string) string {
	for _, key := range keys {
		if value, ok := attributes[key].(string); ok && strings.TrimSpace(value) != "" {
			return strings.ToLower(strings.TrimSpace(value))
		}
	}
	return ""
}

// InferSpanKind maps OpenTelemetry GenAI semantic conventions to a Bench span
// kind. Every supported framework that emits GenAI spans uses these
// attributes, so discovery does not depend on the framework or language.
func InferSpanKind(attributes map[string]any) string {
	// The operation name is authoritative: frameworks such as Pydantic AI stamp
	// gen_ai.agent.name on every span of a run, including model calls and tools.
	switch attributeText(attributes, "gen_ai.operation.name") {
	case "chat", "text_completion", "generate_content":
		return "LLM"
	case "execute_tool":
		return "TOOL"
	case "embeddings":
		return "EMBEDDING"
	case "invoke_agent", "create_agent":
		return "AGENT"
	}
	switch {
	case attributeText(attributes, "gen_ai.tool.name") != "":
		return "TOOL"
	case attributeText(attributes, "gen_ai.request.model") != "":
		return "LLM"
	case attributeText(attributes, "gen_ai.agent.name") != "":
		return "AGENT"
	}
	return "UNKNOWN"
}

// RecordExternalSpan queues a finished span from another tracer. Metadata-only
// capture and redaction apply exactly as for Trace. Invalid input is dropped
// and reported, never returned as an application error.
func (c *Client) RecordExternalSpan(s ExternalSpan) {
	defer func() {
		if recover() != nil {
			c.drop(1)
			c.report("External span could not be captured; dropped.")
		}
	}()
	traceID, spanID, parent := strings.ToLower(s.TraceID), strings.ToLower(s.SpanID), strings.ToLower(s.ParentSpanID)
	if !hexTrace.MatchString(traceID) || !hexSpan.MatchString(spanID) || (parent != "" && !hexSpan.MatchString(parent)) {
		c.drop(1)
		c.report("External spans need 32-hex trace and 16-hex span identifiers; dropped.")
		return
	}
	if c.sample < 1 {
		prefix, _ := strconv.ParseUint(traceID[:8], 16, 64)
		if float64(prefix)/float64(0xFFFFFFFF) >= c.sample {
			return
		}
	}
	kind := s.Kind
	switch kind {
	case "LLM", "TOOL", "CHAIN", "AGENT", "RETRIEVER", "EMBEDDING":
	default:
		kind = InferSpanKind(s.Attributes)
	}
	attrs := map[string]any{}
	for k, v := range s.Attributes {
		if c.options.CaptureContent || metadata.MatchString(k) {
			attrs[k] = v
		}
	}
	if c.options.Environment != "" {
		attrs["bench.environment"] = c.options.Environment
	}
	safeAttrs, err := c.safe(attrs)
	if err != nil {
		panic(err)
	}
	name, err := c.safe(s.Name)
	if err != nil {
		panic(err)
	}
	text, ok := name.(string)
	if !ok || text == "" {
		panic("invalid name")
	}
	status := "ok"
	if s.Status == "error" {
		status = "error"
	}
	started, ended := s.StartedAt, s.EndedAt
	if started.IsZero() {
		started = time.Now()
	}
	if ended.IsZero() {
		ended = time.Now()
	}
	row := wireSpan{ID: spanID, Parent: parent, Name: limitText(text, 200), Kind: kind, Started: started.UTC(), Ended: ended.UTC(), Status: status, Attributes: safeAttrs}
	if s.Model != "" {
		model, err := c.safe(s.Model)
		if err != nil {
			panic(err)
		}
		if text, ok := model.(string); ok {
			row.Model = limitText(text, 200)
		}
	}
	if c.options.CaptureContent {
		for value, target := range map[*any]**string{&s.Input: &row.Input, &s.Output: &row.Output} {
			safe, err := c.safe(*value)
			if err != nil {
				panic(err)
			}
			raw, err := json.Marshal(safe)
			if err != nil {
				panic(err)
			}
			encoded := string(raw)
			*target = &encoded
		}
	}
	t := trace{ID: traceID, Source: "bench_sdk", Spans: []wireSpan{row}}
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
