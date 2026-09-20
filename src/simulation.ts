import type { SystemCase, SystemEvaluationOptions, SystemEvaluationReport } from './system-evaluation.js';

export interface SimulationInput<State, Turn> {
  initialState: State;
  turns: Turn[];
}
export interface SimulationSession<Turn, Reply> {
  /** Call the real application's turn handler, keeping its tool wrappers and retries. */
  turn: (message: Turn, context: { signal: AbortSignal; turnIndex: number }) => Promise<Reply> | Reply;
  /** Read the fixture's authoritative state; never derive it from the agent's reply. */
  observe: () => Promise<unknown> | unknown;
  /** Release the fixture and application session. Called even after failure. */
  close: () => Promise<void> | void;
}
export interface SimulationOptions<State, Turn, Reply> {
  sourceRevision: string;
  contextRevision: string;
  cases: SystemCase<SimulationInput<State, Turn>>[];
  createSession: (initialState: State, context: { caseId: string; signal: AbortSignal }) => Promise<SimulationSession<Turn, Reply>> | SimulationSession<Turn, Reply>;
  timeoutMs?: number;
  signal?: AbortSignal;
}

/** Scripted user simulation. Fixture semantics and reset fidelity belong to the adapter. */
export async function simulateSystem<State, Turn, Reply>(
  options: SimulationOptions<State, Turn, Reply>,
  evaluate: (options: SystemEvaluationOptions<SimulationInput<State, Turn>, Reply>) => Promise<SystemEvaluationReport>,
): Promise<SystemEvaluationReport> {
  for (const item of options.cases) {
    if (!Array.isArray(item.input?.turns) || item.input.turns.length < 1 || item.input.turns.length > 20) throw new Error('A simulation requires 1 to 20 scripted user turns.');
    if (!Object.hasOwn(item, 'expectedState')) throw new Error('A simulation requires the expected business state.');
  }
  const observations = new Map<string, unknown>();
  return evaluate({
    ...options,
    run: async (input, context) => {
      const session = await options.createSession(input.initialState, context);
      try {
        let reply!: Reply;
        for (const [turnIndex, message] of input.turns.entries()) {
          if (context.signal.aborted) throw new Error('Simulation stopped.');
          reply = await session.turn(message, { signal: context.signal, turnIndex });
        }
        if (context.signal.aborted) throw new Error('Simulation stopped.');
        // Snapshot before close/reset can alter the fixture. The answer is kept separate.
        observations.set(context.caseId, structuredClone(await session.observe()));
        return reply;
      } finally {
        await session.close();
      }
    },
    observe: ({ caseId }) => { const state = observations.get(caseId); observations.delete(caseId); return state; },
  });
}
