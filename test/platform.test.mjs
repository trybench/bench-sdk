import assert from 'node:assert/strict';
import { test } from 'node:test';
import { BenchPlatform, PlatformError, operationCatalog } from '../dist/index.js';

test('platform routes the complete catalog and refreshes credentials per protected call', async () => {
 let current; let protectedCalls=0; let resolutions=0;
 const platform=new BenchPlatform({credential:()=>{resolutions++;return 'bench_sk_fixture';},fetch:async(url,init)=>{
  assert.equal(new URL(url).pathname,current.path.replace(/\{[^}]+\}/g,'42'));
  assert.equal(init.method,current.method);assert.equal(init.redirect,'error');
  if(current.auth!=='public'){protectedCalls++;assert.equal(init.headers.Authorization,'Bearer bench_sk_fixture');}
  else assert.equal(init.headers.Authorization,undefined);
  return new Response('{"ok":true}',{headers:{'Content-Type':'application/json'}});
 }});
 for(const operation of operationCatalog.operations){
  current=operation;
  const path=Object.fromEntries(Object.keys(operation.path_parameters).map(key=>[key,42]));
  assert.deepEqual(await platform.call(operation.id,{path}),{ok:true});
 }
 assert.ok(protectedCalls>100);assert.equal(resolutions,protectedCalls);
});
test('platform preserves files, mappings, booleans and structured results',async()=>{
 const bytes=Buffer.from([0,255,128,42]);
 const platform=new BenchPlatform({credential:'fixture',fetch:async(url,init)=>{
  const form=await new Request(url,init).formData();
  assert.equal(form.get('content_consent'),'true');
  assert.deepEqual(JSON.parse(form.get('mapping')),{input:'input',expected:'expected'});
  assert.deepEqual(Buffer.from(await form.get('file').arrayBuffer()),bytes);
  return new Response('{"dataset":{"id":"dataset-1"}}');
 }});
 assert.deepEqual(await platform.call('upload_dataset',{path:{id:1},form:{content_consent:true,mapping:{input:'input',expected:'expected'}},files:[{name:'cases.xlsx',content:bytes}]}),{dataset:{id:'dataset-1'}});
});
test('platform surfaces server limits and references without retrying',async()=>{
 let calls=0;
 const platform=new BenchPlatform({credential:'fixture',fetch:async()=>{calls++;return new Response('{"error":{"code":"upgrade_required","message":"Growth required","reference":"BENCH-TEST","pricing_url":"https://stg.usebench.ai/plans","upgrade_url":"https://stg.usebench.ai/plans","payment_confirmation_required":true}}',{status:403});}});
 await assert.rejects(platform.call('set_model_providers',{body:{providers:['openai']}}),error=>error instanceof PlatformError&&error.code==='upgrade_required'&&error.reference==='BENCH-TEST'&&error.details.upgrade_url==='https://stg.usebench.ai/plans'&&error.details.payment_confirmation_required===true&&!error.retryable);
 assert.equal(calls,1);
});
test('platform handles empty, streaming and failed streaming responses',async()=>{
 for(const [response,expected] of [[new Response(null,{status:204}),undefined],[new Response('{"type":"progress"}\n{"type":"done","result":3}\n',{headers:{'Content-Type':'application/x-ndjson'}}),[{type:'progress'},{type:'done',result:3}]]]){
  const platform=new BenchPlatform({credential:'fixture',fetch:async()=>response});
  assert.deepEqual(await platform.call('run_baseline',{path:{owner:'test',repo:'repo'}}),expected);
 }
 const platform=new BenchPlatform({credential:'fixture',fetch:async()=>new Response('{"type":"error","message":"worker failed"}\n',{headers:{'Content-Type':'application/x-ndjson'}})});
 await assert.rejects(platform.call('run_baseline',{path:{owner:'test',repo:'repo'}}),error=>error.code==='stream_error');
});
test('platform rejects credential exfiltration and invalid paths before sending',async()=>{
 for(const endpoint of ['http://external.invalid','https://user:secret@example.com','https://api.example.com/?key=secret','https://api.example.com/prefix'])assert.throws(()=>new BenchPlatform({endpoint}));
 let calls=0;const platform=new BenchPlatform({credential:'fixture',fetch:async()=>{calls++;return new Response('{}');}});
 for(const id of ['..','%2Fadmin','x/y','x\\y','a\n'])await assert.rejects(platform.call('get_system',{path:{id}}));
 await assert.rejects(platform.call('constructor'));
 await assert.rejects(platform.call('get_system'));
 assert.equal(calls,0);
});
test('platform forwards caller cancellation and uses a bounded timeout',async()=>{
 const controller=new AbortController();controller.abort();
 const platform=new BenchPlatform({credential:'fixture',fetch:async(_url,init)=>{assert.equal(init.signal.aborted,true);throw init.signal.reason;}});
 await assert.rejects(platform.call('whoami',{signal:controller.signal}));
});

test('all independently packaged language clients use the same catalog', async()=>{
 const {readFile}=await import('node:fs/promises');
 for(const path of ['python/src/bench_sdk/operations.json','go/operations.json','rust/src/operations.json'])assert.deepEqual(JSON.parse(await readFile(path,'utf8')),operationCatalog);
});
