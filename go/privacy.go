package bench

import (
	"encoding/json"
	"math"
	"math/big"
	"net"
	"regexp"
	"strconv"
	"strings"
)

var sensitive = regexp.MustCompile(`(?i)authorization|cookie|password|secret|token|api.?key|email|phone|address|user.?id|(?:first|last|full).?name|card.?number`)
var metadata = regexp.MustCompile(`^(code\.(filepath|lineno)|gen_ai\.(system|provider\.name|operation\.name|agent\.name|request\.model|response\.model|tool\.(name|type|call\.id)|usage\.(input_tokens|output_tokens))|bench\.(component_id|environment|prompt_version|duration_ms|cost\.(usd|source|pricing_version)))$`)
var secretPattern = regexp.MustCompile(`(?i)(?:bench_sk_|apikey_|sk-)[a-zA-Z0-9_-]{8,}|Bearer\s+[a-zA-Z0-9._~+/-]+`)
var emailPattern = regexp.MustCompile("(?i)[a-z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-z0-9-]+(?:\\.[a-z0-9-]+)+")
var phonePattern = regexp.MustCompile(`\+\d[\d ()-]{8,}\d`)
var ipPattern = regexp.MustCompile(`\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b`)
var cardPattern = regexp.MustCompile(`\b(?:[0-9]{4}(?:[ -][0-9]{4}){3}[ -][0-9]{3}|[0-9]{4}(?:[ -][0-9]{4}){3}|[0-9]{4}[ -][0-9]{6}[ -][0-9]{5}|[0-9]{13,19})\b`)

func redactText(s string) string {
	if len(s) > 16000 {
		return "[CONTENT_LIMIT]"
	}
	s = secretPattern.ReplaceAllString(s, "[REDACTED_SECRET]")
	s = emailPattern.ReplaceAllString(s, "[REDACTED_EMAIL]")
	s = ipPattern.ReplaceAllStringFunc(s, func(v string) string {
		if net.ParseIP(v) != nil {
			return "[REDACTED_IP]"
		}
		return v
	})
	s = cardPattern.ReplaceAllStringFunc(s, func(v string) string {
		digits := strings.NewReplacer(" ", "", "-", "").Replace(v)
		if cardChecksum(digits) || (len(digits) == 19 && len(v) > 19 && cardChecksum(digits[:16])) {
			return "[REDACTED_PAYMENT_NUMBER]"
		}
		return v
	})
	return limitText(phonePattern.ReplaceAllString(s, "[REDACTED_PHONE]"), 16000)
}

func cardChecksum(digits string) bool {
	sum := 0
	for i := len(digits) - 1; i >= 0; i-- {
		n := int(digits[i] - '0')
		if (len(digits)-1-i)%2 == 1 {
			n *= 2
			if n > 9 {
				n -= 9
			}
		}
		sum += n
	}
	return sum > 0 && sum%10 == 0
}
func limitText(s string, max int) string {
	r := []rune(s)
	if len(r) > max {
		return string(r[:max])
	}
	return s
}
func scrub(value any, depth int) any { return scrubLimit(value, depth, 100) }
func scrubLimit(value any, depth, maxItems int) any {
	if depth > 12 {
		return "[DEPTH_LIMIT]"
	}
	switch v := value.(type) {
	case json.Number:
		if numericCard(v.String()) {
			return "[REDACTED_PAYMENT_NUMBER]"
		}
		return v
	case string:
		if len(v) > 16000 {
			return "[CONTENT_LIMIT]"
		}
		text := strings.TrimSpace(v)
		if strings.HasPrefix(text, "{") || strings.HasPrefix(text, "[") {
			var inner any
			dec := json.NewDecoder(strings.NewReader(v))
			dec.UseNumber()
			if json.Valid([]byte(v)) && dec.Decode(&inner) == nil {
				encoded, _ := json.Marshal(scrubLimit(inner, depth+1, maxItems))
				return limitText(string(encoded), 16000)
			}
		}
		return redactText(v)
	case map[string]any:
		out := map[string]any{}
		n := 0
		for k, item := range v {
			if n >= maxItems {
				break
			}
			n++
			_, number := item.(json.Number)
			if sensitive.MatchString(k) && !(strings.HasPrefix(k, "gen_ai.usage.") && metadata.MatchString(k) && number) {
				out[redactText(k)] = "[REDACTED]"
			} else {
				out[redactText(k)] = scrubLimit(item, depth+1, maxItems)
			}
		}
		return out
	case []any:
		if len(v) > maxItems {
			v = v[:maxItems]
		}
		out := make([]any, len(v))
		for i, item := range v {
			out[i] = scrubLimit(item, depth+1, maxItems)
		}
		return out
	default:
		return v
	}
}

func numericCard(text string) bool {
	value, err := strconv.ParseFloat(text, 64)
	if err != nil || math.IsInf(value, 0) || value < 1e12 || value >= 1e19 || math.Trunc(value) != value {
		return false
	}
	if len(text) > 128 {
		return true
	}
	if number, ok := new(big.Rat).SetString(text); ok && number.IsInt() && number.Sign() >= 0 {
		digits := number.Num().String()
		return len(digits) >= 13 && len(digits) <= 19 && cardChecksum(digits)
	}
	return false
}
