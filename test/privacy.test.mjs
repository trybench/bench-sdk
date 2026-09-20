import test from 'node:test';
import assert from 'node:assert/strict';
import {Bench} from '../dist/index.js';
import {readFileSync} from 'node:fs';

test('adjacent identifiers and nested fields are filtered before transmission',async()=>{
 let body;
 const bench=new Bench({apiKey:'bench_sk_synthetic_test_key',repository:'fixture/node',branch:'main',captureContent:true,fetch:async(_url,options)=>{body=JSON.parse(options.body);return new Response('{}',{status:201})}});
 await bench.trace({name:'request',input:{card_number:4242424242424242,nested:'{"full_name":"Private Person"}',note:'person@example.test +49 151 12345678 192.168.1.2 4242-4242-4242-4242',count:3}},()=> 'Bearer abcdefghijklmnop');
 await bench.shutdown();
 const content=body.traces[0].spans[0];
 for(const value of ['4242','Private Person','person@example.test','12345678','192.168.1.2','abcdefghijklmnop'])assert.ok(!JSON.stringify(content).includes(value),value);
 assert.equal(JSON.parse(content.input_value).count,3);
});

test('shared privacy contract holds in the outgoing payload',async()=>{
 const fixture=JSON.parse(readFileSync(new URL('./fixtures/privacy.json',import.meta.url),'utf8'));
 let row;
 const bench=new Bench({apiKey:'bench_sk_synthetic_test_key',repository:'fixture/node',branch:'main',captureContent:true,fetch:async(_url,options)=>{row=JSON.parse(options.body).traces[0].spans[0];return new Response('{}',{status:201})}});
 await bench.trace({name:'privacy-contract',input:fixture.input},()=>({ok:true}));await bench.shutdown();
 for(const value of fixture.forbidden)assert.ok(!row.input_value.includes(value),value);
 for(const value of fixture.preserved)assert.ok(row.input_value.includes(value),value);
 assert.equal(JSON.parse(row.input_value).count,42);
});

// Package-local copies make published source tests self-contained. This check
// keeps their contract identical to the canonical cross-language fixture.
test('distributed privacy fixtures match the common contract',()=>{
 const canonical=readFileSync(new URL('./fixtures/privacy.json',import.meta.url),'utf8');
 for(const path of ['../python/tests/fixtures/privacy.json','../go/testdata/privacy.json','../rust/tests/fixtures/privacy.json']){
  assert.equal(readFileSync(new URL(path,import.meta.url),'utf8'),canonical,path);
 }
});
