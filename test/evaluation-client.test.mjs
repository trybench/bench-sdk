import test from 'node:test';
import assert from 'node:assert/strict';
import { EvaluationClient, RuntimeGateway } from '../dist/index.js';
import { mkdtemp, readFile, writeFile, rm } from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';

test('runtime client keeps authorization and idempotency on the same API contracts',async()=>{
 const calls=[];const client=new EvaluationClient('http://127.0.0.1:8080','scoped-test-key',42,async(url,opts)=>{calls.push({url,opts});return new Response(JSON.stringify({id:'run',result:{decision:'insufficient_evidence'}}),{status:200})});
 await client.run('plan','start-once');await client.resume('run','resume-once');await client.cancel('run');await client.events('run',9);await client.review('plan','case','bad','wrong outcome');
 assert.equal(calls[0].url,'http://127.0.0.1:8080/api/ai-systems/42/evaluation-runs');
 assert.equal(calls[0].opts.headers['Idempotency-Key'],'start-once');assert.equal(calls[1].opts.headers['Idempotency-Key'],'resume-once');
 assert.equal(calls[0].opts.redirect,'error');assert.equal(calls[0].opts.headers.Authorization,'Bearer scoped-test-key');
 assert.ok(calls[3].url.endsWith('/events?after=9'));assert.equal(JSON.parse(calls[4].opts.body).verdict,'bad');
 assert.throws(()=>new EvaluationClient('http://remote.example','secret',1),/HTTPS/);
});

test('runtime client never invents a successful response on service failure',async()=>{
 const client=new EvaluationClient('https://api.example','secret',1,async()=>new Response('unavailable',{status:503}));
 await assert.rejects(client.status('run'),/503/);
});

test('guest model gateway returns host reply without provider credentials',async()=>{
 const dir=await mkdtemp(path.join(os.tmpdir(),'bench-gateway-test-'));
 try{
  const gateway=new RuntimeGateway(dir);const pending=gateway.model([{role:'user',content:'fixture'}]);
  let request;
  for(let i=0;i<100;i++){try{request=JSON.parse(await readFile(path.join(dir,'request-0.json'),'utf8'));break}catch{await new Promise(r=>setTimeout(r,5))}}
  assert.equal(request.operation,'model');assert.deepEqual(Object.keys(request).sort(),['messages','operation']);
  await writeFile(path.join(dir,'response-0.json'),JSON.stringify({message:{content:'answer'}}));
  assert.deepEqual(await pending,{content:'answer'});
 }finally{await rm(dir,{recursive:true,force:true})}
});
