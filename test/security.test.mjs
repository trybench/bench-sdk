import test from 'node:test';
import assert from 'node:assert/strict';
import {Bench} from '../dist/index.js';

test('bounded filtering covers envelope names and refuses unsafe routing identity', async()=>{
 let body;
 const options={apiKey:'bench_sk_synthetic_test_key',repository:'fixture/node',branch:'main',systemName:'person@example.test bench_sk_NOT_A_REAL_SECRET',captureContent:true,fetch:async(_,init)=>{body=JSON.parse(init.body);return new Response('{}',{status:201});}};
 const bench=new Bench(options);
 const start=performance.now();
 await bench.trace({name:'privacy',input:'x'.repeat(1000000)},()=> 'x'.repeat(16000));
 await bench.shutdown();
 assert.ok(performance.now()-start<1000, 'privacy processing must stay bounded');
 assert.ok(!JSON.stringify(body).includes('person@example.test'));
 assert.ok(!JSON.stringify(body).includes('NOT_A_REAL_SECRET'));
 assert.ok(body.traces[0].spans[0].input_value.includes('[CONTENT_LIMIT]'));
 for(const branch of ['person@example.test','bench_sk_NOT_A_REAL_SECRET','bad\nbranch'])assert.throws(()=>new Bench({...options,branch}));
});
