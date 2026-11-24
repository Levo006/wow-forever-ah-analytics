'use strict';
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const s=fs.readFileSync(require('path').join(__dirname,'../static/static-api.js'),'utf8');
const ctx=vm.createContext({Date});vm.runInContext(s.slice(s.indexOf('function focusEligible'),s.indexOf('  async function movers')),ctx);
const good={class_id:7,last_min:100,last_qty:5,points:24,market_complete:true,last_ts:Date.now()/1000,liq_events:5,liq_presence:.5,liq_score:30};ctx.r=good;assert.equal(vm.runInContext('focusEligible(r)',ctx),true);
for(const patch of [{class_id:9},{class_id:0,subclass_id:8},{last_min:99},{last_qty:4},{points:23},{market_complete:false},{last_ts:1},{liq_events:4},{liq_presence:.49},{liq_score:29}]){ctx.r={...good,...patch};assert.equal(vm.runInContext('focusEligible(r)',ctx),false,JSON.stringify(patch));}
console.log('Conservative homepage eligibility boundaries OK');
