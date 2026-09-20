import assert from 'node:assert/strict';
import { Bench } from '@benchai/sdk';

// This example runs locally. It does not upload data or call a model.
const bench = new Bench({apiKey:'bench_sk_local_example',repository:'example/refunds',branch:'test'});
const cases = [{id:'refund-retry',businessOutcome:'Refund an order exactly once',input:{initialState:{refunds:0},turns:['Refund order A','Retry that refund']},expectedOutput:'Refunded',expectedState:{refunds:1}}];
const createSession = fixed => state => ({
  turn: () => bench.trace({name:'refund',kind:'TOOL'}, () => {
    if (!fixed || state.refunds === 0) state.refunds++;
    return 'Refunded';
  }),
  observe: () => state,
  close: () => { state.refunds = 0; },
});
const options = {sourceRevision:'a'.repeat(40),contextRevision:'refund-policy-v1',cases};
const before = await bench.simulateSystem({...options,createSession:createSession(false)});
const after = await bench.simulateSystem({...options,sourceRevision:'b'.repeat(40),createSession:createSession(true)});
assert.equal(before.summary.score,0);
assert.equal(after.summary.score,100);
assert.equal(before.suite_hash,after.suite_hash);
console.log({before:before.summary,after:after.summary});
await bench.shutdown();
