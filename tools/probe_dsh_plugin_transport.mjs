// Exercise the same official MCP client/transport used by DSH, without an LLM.
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { readFile, writeFile } from 'node:fs/promises';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(join(homedir(),'.local/bin/node_modules/@deepseek-ai/dsh/node_modules/@deepseek-ai/dsh-mcp-client/package.json'));
const {Client}=await import(pathToFileURL(require.resolve('@modelcontextprotocol/client')).href);
const {StdioClientTransport}=await import(pathToFileURL(require.resolve('@modelcontextprotocol/client/stdio')).href);
const entry=JSON.parse(await readFile(join(root,'runtime/dsh-plugin/guga.patch.yml'),'utf8'))[0].insert[0];
const cfg=entry.config;
const client=new Client({name:'guga-dsh-plugin-acceptance',version:'1.0.0'});
const transport=new StdioClientTransport({command:cfg.command,args:cfg.args,cwd:cfg.cwd});
const report={mode:'official_dsh_mcp_transport_without_model',model_verified:false};
const timer=setTimeout(()=>{console.error('bounded plugin probe timed out');process.exit(2);},45000);
try {
  await client.connect(transport);
  report.tools=(await client.listTools()).tools.map(t=>t.name);
  report.schema_ready=['computer','wechat','daily_plan','pet_say'].every(n=>report.tools.includes(n));
  const call=async(name,args)=>{
    const result=await client.callTool({name,arguments:args});
    if(result.isError)throw new Error(JSON.stringify(result.content));
    const text=result.content.find(x=>x.type==='text')?.text;
    return {result:JSON.parse(text).result,content:result.content};
  };
  const plan=await call('daily_plan',{action:'list'});
  report.daily_plan_read=plan.result.ok;
  const wins=await call('computer',{action:'windows',query:'每日计划'});
  report.window_verified=(wins.result.windows??[]).length>0;
  if(report.window_verified){
    const obs=await call('computer',{action:'observe',hwnd:wins.result.windows[0].hwnd});
    report.observation_image=obs.content.some(x=>x.type==='image');
    report.observation_size=obs.result.image_size;
  }
  report.bubble_shown=(await call('pet_say',{text:'咕嘎插件接口已接通；每日清单已经准备好，等你来填写。'})).result.shown;
  const failure=await client.callTool({name:'daily_plan',arguments:{action:'check',title:'__nonexistent_acceptance_task__'}});
  report.failure_is_error=failure.isError===true;
  report.ok=report.schema_ready&&report.daily_plan_read&&report.window_verified&&report.bubble_shown&&report.failure_is_error&&report.observation_image;
} finally {
  await client.close();clearTimeout(timer);
  await writeFile(join(root,'runtime/daily-dsh-qa/transport.json'),JSON.stringify(report,null,2),'utf8');
}
console.log(JSON.stringify(report));
process.exitCode=report.ok?0:1;
