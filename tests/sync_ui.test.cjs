const {test} = require('node:test');
const assert = require('node:assert/strict');
const {mount} = require('../app/static/sync.js');
global.FormData = class {constructor(form) { this.csrf = form.csrf; }};

function setup(fetch) {
  const button = {disabled:false, textContent:''}, message = {textContent:''}, tasks = {}, reconnect = {};
  let submit;
  const form = {action:'/gmail/sync', csrf:'session-token', querySelector:()=>button, addEventListener:(_,fn)=>{submit=fn;}};
  const panel = {dataset:{active:'false'}, setAttribute(){}, querySelector:s=>({'[data-sync-form]':form,'[data-sync-message]':message,'[data-sync-tasks]':tasks,'[data-sync-reconnect]':reconnect}[s])};
  const scheduled=[];
  const controller=mount(panel,{fetch,later:fn=>scheduled.push(fn),refresh:()=>{}});
  return {button,message,tasks,reconnect,panel,controller,scheduled,submit:()=>submit({preventDefault(){}})};
}
const response = job => ({ok:true,json:async()=>job});

test('submit stays on page, disables immediately, carries CSRF, suppresses double click',async()=>{
  let release, calls=0;
  const ui=setup(async(url,opts)=>{calls++; assert.equal(opts.body.csrf,'session-token'); return await new Promise(r=>release=r);});
  const pending=ui.submit();
  assert.equal(ui.button.disabled,true); assert.equal(ui.message.textContent,'Checking new emails…');
  await ui.submit(); assert.equal(calls,1);
  release(response({active:true,message:'Checking new emails…',status_url:'/gmail/sync/1'})); await pending;
  assert.equal(ui.scheduled.length,1);
});
test('completion shows concise receipt and tasks link',async()=>{
  const ui=setup(async()=>response({active:false,message:'Checked 12 emails · found 2 new emails · created 1 task.',tasks_created:1,imported:2}));
  ui.panel.dataset.statusUrl='/gmail/sync/1';
  ui.controller.render({active:true,message:'Checking new emails…'});
  await ui.controller.poll();
  assert.match(ui.message.textContent,/created 1 task/); assert.equal(ui.button.disabled,false); assert.equal(ui.tasks.hidden,false);
});
test('no new mail, failure and malformed API payload never expose raw JSON',async()=>{
  const ui=setup(async()=>({ok:false,json:async()=>({stack:'SECRET'})}));
  await ui.submit(); assert.equal(ui.button.textContent,'Try again'); assert.doesNotMatch(ui.message.textContent,/SECRET|stack/);
  ui.controller.render({active:false,message:'You’re up to date. No new emails found.'});
  assert.match(ui.message.textContent,/No new emails/);
});
test('poll failures are bounded and provide a retry button',async()=>{
  const ui=setup(async()=>{throw new Error('private payload');});
  ui.panel.dataset.statusUrl='/gmail/sync/1'; ui.controller.render({active:true,message:'Checking'});
  await ui.controller.poll(); await ui.controller.poll(); await ui.controller.poll();
  assert.equal(ui.button.disabled,false); assert.equal(ui.button.textContent,'Try again');
  assert.doesNotMatch(ui.message.textContent,/private payload/);
});
