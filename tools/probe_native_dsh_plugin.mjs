// Real Cordis services + native plugin + MCP, with synthetic session events only.
import {createRequire} from 'node:module';
import {pathToFileURL} from 'node:url';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import * as guga from '../plugins/dsh-guga-desktop/index.mjs';
const cfg=JSON.parse(await readFile(process.argv[2],'utf8'));
const require=createRequire(cfg.runtimeAnchor);
const load=async name=>import(pathToFileURL(require.resolve(name)).href);
const {Context}=await load('@deepseek-ai/cordis');
const {SystemPrompt}=await load('@deepseek-ai/dsh-system-prompt');
const {ToolRuntime}=await load('@deepseek-ai/dsh-tools');
const {readPluginMeta}=await load('@deepseek-ai/dsh-app-boot');
const metadata=readPluginMeta('dsh-guga-desktop',pathToFileURL(cfg.profileManifest).href);
assert.ok(JSON.stringify(metadata).includes('咕嘎'));
const ctx=new Context();
new SystemPrompt(ctx,{});new ToolRuntime(ctx,{});
const plugin=ctx.plugin(guga,cfg);
try{
  await plugin.await();
  assert.ok(ctx.tools.get('mcp__guga__daily_plan'));
  assert.ok(ctx.tools.get('mcp__guga__computer'));
  assert.equal(ctx.tools.get('mcp__guga__dsh_host_event'),undefined);
  const value=await ctx.tools.get('mcp__guga__daily_plan').execute({action:'list'},{signal:new AbortController().signal});
  assert.ok(value.content.some(x=>x.type==='text'));
  const session={id:'isolated-native-session'};
  for(const event of [
    {type:'user/message',data:{content:[{type:'text',text:'隔离验收：打开测试浏览器'}]}},
    {type:'turn/start',data:{turn:1}},
    {type:'tool/call',data:{name:'mcp__guga__applications'}},
    {type:'tool/result',data:{message:{isError:false}}},
    {type:'assistant/message',data:{message:{content:[{type:'reasoning',text:'DO_NOT_FORWARD'},{type:'text',text:'合成结果已返回，未操作真实桌面'}]}}},
    {type:'turn/end',data:{turn:1,reason:'stop'}}
  ])await ctx.parallel('session/event',session,event);
  await new Promise(resolve=>setTimeout(resolve,1500));
  console.log(JSON.stringify({ok:true,real_cordis:true,real_mcp:true,plugin_list_metadata_verified:true,synthetic_events:true,desktop_input:false,model_called:false}));
}finally{await plugin.dispose();}
