package bench

import (
	"bytes"
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"mime/multipart"
	"net/http"
	"net/url"
	"strings"
	"time"
	"unicode"
)

//go:embed operations.json
var platformCatalog []byte

type PlatformOptions struct {
	Credential string
	// Token supplies a fresh Bench OAuth/session token for each protected call.
	Token     func(context.Context) (string, error)
	Endpoint  string
	Timeout   time.Duration
	Transport http.RoundTripper
}
type PlatformFile struct {
	Name    string
	Content []byte
}
type PlatformRequest struct {
	Path  map[string]string
	Query url.Values
	Body  any
	Form  map[string]any
	Files []PlatformFile
}
type PlatformError struct {
	Status                   int
	Code, Message, Reference string
	Details                  map[string]any
}

func (e *PlatformError) Error() string   { return e.Message }
func (e *PlatformError) Retryable() bool { return e.Status == 429 || e.Status >= 500 }

type platformOperation struct {
	ID, Method, Path, Auth string
	PathParameters         map[string]json.RawMessage `json:"path_parameters"`
	Multipart              bool
	FileField              string `json:"file_field"`
	MaxFileBytes           int    `json:"max_file_bytes"`
}
type Platform struct {
	options    PlatformOptions
	client     *http.Client
	operations map[string]platformOperation
}

// NewPlatform manages Bench headlessly, independently of application tracing.
// Calls never automatically retry mutations or start an evaluation implicitly.
func NewPlatform(options PlatformOptions) (*Platform, error) {
	if options.Endpoint == "" {
		options.Endpoint = "https://api.usebench.ai"
	}
	u, err := url.Parse(options.Endpoint)
	if err != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" || (u.Path != "" && u.Path != "/") || !(u.Scheme == "https" || (u.Scheme == "http" && (u.Hostname() == "localhost" || u.Hostname() == "127.0.0.1" || u.Hostname() == "::1"))) {
		return nil, errors.New("use an HTTPS API origin or loopback HTTP origin")
	}
	options.Endpoint = strings.TrimRight(options.Endpoint, "/")
	if options.Timeout == 0 {
		options.Timeout = 120 * time.Second
	}
	if options.Timeout < 100*time.Millisecond || options.Timeout > 600*time.Second {
		return nil, errors.New("timeout must be between 100ms and 600s")
	}
	var catalog struct{ Operations []platformOperation }
	if err = json.Unmarshal(platformCatalog, &catalog); err != nil {
		return nil, err
	}
	p := &Platform{options: options, operations: map[string]platformOperation{}, client: &http.Client{Transport: options.Transport, Timeout: options.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}}
	for _, op := range catalog.Operations {
		p.operations[op.ID] = op
	}
	return p, nil
}
func (p *Platform) Operations() json.RawMessage {
	return append(json.RawMessage(nil), platformCatalog...)
}

// Call maps an operation name from Operations to one HTTP request. For NDJSON
// endpoints it returns the full event array and surfaces in-stream errors.
func (p *Platform) Call(ctx context.Context, operation string, input PlatformRequest) (json.RawMessage, error) {
	op, ok := p.operations[operation]
	if !ok {
		return nil, errors.New("unknown Bench operation")
	}
	route := op.Path
	for key := range op.PathParameters {
		value := input.Path[key]
		if value == "" || value == "." || value == ".." || strings.ContainsAny(value, "/?#\\%") || strings.IndexFunc(value, unicode.IsSpace) >= 0 {
			return nil, fmt.Errorf("invalid or missing path parameter: %s", key)
		}
		route = strings.ReplaceAll(route, "{"+key+"}", url.PathEscape(value))
	}
	endpoint := p.options.Endpoint + route
	if len(input.Query) > 0 {
		endpoint += "?" + input.Query.Encode()
	}
	var payload bytes.Buffer
	contentType := ""
	if op.Multipart {
		if input.Body != nil {
			return nil, errors.New("use form and files for multipart operations")
		}
		writer := multipart.NewWriter(&payload)
		for key, value := range input.Form {
			var text string
			if s, ok := value.(string); ok {
				text = s
			} else {
				raw, err := json.Marshal(value)
				if err != nil {
					return nil, err
				}
				text = string(raw)
			}
			if err := writer.WriteField(key, text); err != nil {
				return nil, err
			}
		}
		total := 0
		limit := op.MaxFileBytes
		if limit == 0 {
			limit = 4 << 20
		}
		field := op.FileField
		if field == "" {
			field = "file"
		}
		for _, file := range input.Files {
			if file.Name == "" || strings.ContainsAny(file.Name, "\r\n/\\") {
				return nil, errors.New("use a file name without directories or line breaks")
			}
			total += len(file.Content)
			if total > limit {
				return nil, errors.New("upload exceeds operation file-size limit")
			}
			part, err := writer.CreateFormFile(field, file.Name)
			if err != nil {
				return nil, err
			}
			if _, err = part.Write(file.Content); err != nil {
				return nil, err
			}
		}
		if err := writer.Close(); err != nil {
			return nil, err
		}
		contentType = writer.FormDataContentType()
	} else {
		if input.Files != nil || input.Form != nil {
			return nil, errors.New("operation does not accept multipart uploads")
		}
		if input.Body != nil {
			raw, err := json.Marshal(input.Body)
			if err != nil {
				return nil, err
			}
			payload.Write(raw)
			contentType = "application/json"
		}
	}
	req, err := http.NewRequestWithContext(ctx, op.Method, endpoint, &payload)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Accept", "application/json, application/x-ndjson")
	if contentType != "" {
		req.Header.Set("Content-Type", contentType)
	}
	if op.Auth != "public" {
		credential := p.options.Credential
		if p.options.Token != nil {
			credential, err = p.options.Token(ctx)
			if err != nil {
				return nil, err
			}
		}
		if strings.TrimSpace(credential) == "" {
			return nil, &PlatformError{Status: 401, Code: "unauthorized", Message: "Provide a Bench credential or authenticate through MCP OAuth."}
		}
		req.Header.Set("Authorization", "Bearer "+credential)
	}
	response, err := p.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(response.Body, (16<<20)+1))
	if err != nil {
		return nil, err
	}
	if len(raw) > 16<<20 {
		return nil, &PlatformError{Status: response.StatusCode, Code: "response_too_large", Message: "Response exceeds 16 MB; use pagination."}
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		var body struct {
			Error struct{ Code, Message, Reference string }
		}
		_ = json.Unmarshal(raw, &body)
		var envelope struct{ Error map[string]any }
		_ = json.Unmarshal(raw, &envelope)
		if body.Error.Code == "" {
			body.Error.Code = fmt.Sprintf("http_%d", response.StatusCode)
		}
		if body.Error.Message == "" {
			body.Error.Message = "Bench request failed."
		}
		return nil, &PlatformError{Status: response.StatusCode, Code: body.Error.Code, Message: body.Error.Message, Reference: body.Error.Reference, Details: envelope.Error}
	}
	if response.StatusCode == 204 || len(raw) == 0 {
		return json.RawMessage("null"), nil
	}
	if strings.Contains(response.Header.Get("Content-Type"), "ndjson") {
		events := []json.RawMessage{}
		for _, line := range bytes.Split(raw, []byte{'\n'}) {
			if len(bytes.TrimSpace(line)) == 0 {
				continue
			}
			if !json.Valid(line) {
				return nil, errors.New("invalid Bench stream event")
			}
			var event struct {
				Type, Code, Message string
				Error               any
			}
			_ = json.Unmarshal(line, &event)
			if event.Type == "error" {
				if event.Code == "" {
					event.Code = "stream_error"
				}
				if event.Message == "" {
					event.Message = fmt.Sprint(event.Error)
				}
				return nil, &PlatformError{Status: 200, Code: event.Code, Message: event.Message}
			}
			events = append(events, append(json.RawMessage(nil), line...))
		}
		return json.Marshal(events)
	}
	if !json.Valid(raw) {
		return nil, errors.New("invalid Bench JSON response")
	}
	return raw, nil
}
