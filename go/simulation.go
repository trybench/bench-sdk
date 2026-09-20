package bench

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"sync"
	"time"
)

type SimulationInput struct {
	InitialState any   `json:"initialState"`
	Turns        []any `json:"turns"`
}
type SimulationSession interface {
	Turn(context.Context, any) (any, error)
	Observe(context.Context) (any, error)
	Close(context.Context) error
}
type SimulationOptions struct {
	SourceRevision, ContextRevision string
	Cases                           []SystemCase
	CreateSession                   func(context.Context, any) (SimulationSession, error)
	Timeout                         time.Duration
}
type evaluationCaseKey struct{}

// EvaluationCaseID identifies the current case in application/observer callbacks.
func EvaluationCaseID(ctx context.Context) string {
	id, _ := ctx.Value(evaluationCaseKey{}).(string)
	return id
}

// SimulateSystem keeps application tools/retries real and resets fixture state per case.
func (c *Client) SimulateSystem(ctx context.Context, options SimulationOptions) (SystemEvaluationReport, error) {
	if options.CreateSession == nil {
		return SystemEvaluationReport{}, errors.New("provide an application session factory")
	}
	for _, item := range options.Cases {
		raw, err := json.Marshal(item.Input)
		if err != nil {
			return SystemEvaluationReport{}, err
		}
		var input SimulationInput
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		if err = decoder.Decode(&input); err != nil {
			return SystemEvaluationReport{}, errors.New("invalid simulation input")
		}
		var fields map[string]json.RawMessage
		_ = json.Unmarshal(raw, &fields)
		if _, ok := fields["initialState"]; !ok || len(input.Turns) < 1 || len(input.Turns) > 20 || item.ExpectedState == nil {
			return SystemEvaluationReport{}, errors.New("provide initialState, 1 to 20 turns and an expected state")
		}
	}
	var mu sync.Mutex
	observations := map[string]any{}
	return c.EvaluateSystem(ctx, SystemEvaluationOptions{SourceRevision: options.SourceRevision, ContextRevision: options.ContextRevision, Cases: options.Cases, Timeout: options.Timeout,
		Run: func(ctx context.Context, data any) (reply any, err error) {
			raw, err := json.Marshal(data)
			if err != nil {
				return nil, err
			}
			var input SimulationInput
			decoder := json.NewDecoder(bytes.NewReader(raw))
			decoder.UseNumber()
			if err = decoder.Decode(&input); err != nil {
				return nil, err
			}
			session, err := options.CreateSession(ctx, input.InitialState)
			if err != nil {
				return nil, err
			}
			if session == nil {
				return nil, errors.New("session is required")
			}
			defer func() {
				cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), 5*time.Second)
				defer cancel()
				if closeErr := session.Close(cleanup); err == nil {
					err = closeErr
				}
			}()
			for _, turn := range input.Turns {
				if ctx.Err() != nil {
					return nil, ctx.Err()
				}
				reply, err = session.Turn(ctx, turn)
				if err != nil {
					return nil, err
				}
			}
			if ctx.Err() != nil {
				return nil, ctx.Err()
			}
			state, err := session.Observe(ctx)
			if err != nil {
				return nil, err
			}
			state, err = snapshot(state)
			if err != nil {
				return nil, err
			}
			mu.Lock()
			observations[EvaluationCaseID(ctx)] = state
			mu.Unlock()
			return reply, nil
		},
		Observe: func(ctx context.Context, id string) (any, error) {
			mu.Lock()
			defer mu.Unlock()
			v, ok := observations[id]
			delete(observations, id)
			if !ok {
				return nil, errors.New("missing observed state")
			}
			return v, nil
		},
	})
}
