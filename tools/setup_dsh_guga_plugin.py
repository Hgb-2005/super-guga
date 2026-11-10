"""Connect Guga to DSH's official MCP plugin; no packages or credentials copied."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def plugin_entry(python=None):
    return dict(id='guga-desktop-mcp',name='@deepseek-ai/dsh-mcp-client',config=dict(
        transport='stdio',serverName='guga',command=str(python or sys.executable),
        args=[str(ROOT/'tools/guga_mcp_server.py')],cwd=str(ROOT),
        toolCallTimeoutMs=50000,failOnStartupError=True,reconnect=dict(enabled=False)))


def install(profile='web', home=None):
    base=Path(home or Path.home()/'.dsh')
    folder=base/'profiles'/profile
    if not (folder/'cordis.yml').is_file():raise ValueError('DSH profile不存在：'+profile)
    entry=plugin_entry()
    out=ROOT/'runtime/dsh-plugin/guga.patch.yml'
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps([dict(insert=[entry])],ensure_ascii=False,indent=2),encoding='utf-8')
    path=folder/'cordis.patch.yml'
    old=path.read_text(encoding='utf-8') if path.exists() else ''
    begin='# BEGIN GUGA DESKTOP PLUGIN'
    end='# END GUGA DESKTOP PLUGIN'
    if begin in old:
        start=old.index(begin);stop=old.index(end,start)+len(end)
        old=old[:start]+old[stop:]
    content=old.rstrip()+'\n\n'+begin+'\n- insert: '+json.dumps([entry],ensure_ascii=False)+'\n'+end+'\n'
    if path.exists():
        backup=ROOT/'runtime/upgrades'/('dsh-'+profile+'-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.patch.yml.bak')
        backup.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,backup)
    path.write_text(content,encoding='utf-8')
    return dict(profile=profile,config=str(path),overlay=str(out),tools=['computer','wechat','pet_say','daily_plan'])


def prepare_native(profile='web',home=None):
    base=Path(home or Path.home()/'.dsh')
    folder=base/'profiles'/profile
    if not (folder/'cordis.yml').is_file():raise ValueError('DSH profile不存在：'+profile)
    backup=ROOT/'runtime/upgrades'/('native-dsh-profile-'+datetime.now().strftime('%Y%m%d-%H%M%S'))
    backup.mkdir(parents=True,exist_ok=True)
    for name in ('package.json','cordis.patch.yml','pnpm-lock.yaml'):
        if (folder/name).exists():shutil.copy2(folder/name,backup/name)
    patch=folder/'cordis.patch.yml';old=patch.read_text(encoding='utf-8')
    for label in ('GUGA DESKTOP PLUGIN','GUGA NATIVE PLUGIN'):
        begin='# BEGIN '+label;end='# END '+label
        if begin in old:
            start=old.index(begin);stop=old.index(end,start)+len(end);old=old[:start]+old[stop:]
    config=dict(projectRoot=str(ROOT),python=sys.executable,
                runtimeAnchor=str(Path.home()/'.local/bin/node_modules/@deepseek-ai/dsh/package.json'))
    content=old.rstrip()+'\n\n# BEGIN GUGA NATIVE PLUGIN\n- '+json.dumps(dict(id='guga-desktop',config=config),ensure_ascii=False)+'\n# END GUGA NATIVE PLUGIN\n'
    patch.write_text(content,encoding='utf-8')
    launcher=Path('G:/Doubao/智能体/dsh/launcher/launcher.py')
    if launcher.is_file():
        output=ROOT/'runtime/dsh-plugin/native-launcher.json';output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(dict(launcher=str(launcher)),ensure_ascii=False),encoding='utf-8')
    return dict(profile=profile,package=str(ROOT/'plugins/dsh-guga-desktop'),backup=str(backup))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',default='web')
    parser.add_argument('--native',action='store_true')
    parser.add_argument('--legacy-mcp',action='store_true',help='仅给没有原生插件的旧配置使用')
    args=parser.parse_args()
    if args.legacy_mcp:
        manifest=Path.home()/'.dsh/profiles'/args.profile/'package.json'
        bundles=json.loads(manifest.read_text(encoding='utf-8')).get('dsh',{}).get('profile',{}).get('bundles',[])
        if 'dsh-guga-desktop' in bundles:raise RuntimeError('已启用咕嘎原生插件，不再安装重复的旧MCP连接')
        result=install(args.profile)
    else:
        result=prepare_native(args.profile)
        manifest=json.loads((Path.home()/'.dsh/profiles'/args.profile/'package.json').read_text(encoding='utf-8'))
        if 'dsh-guga-desktop' not in manifest.get('dependencies',{}):
            profile="'"+args.profile.replace("'","''")+"'"
            package="'"+result['package'].replace("'","''")+"'"
            subprocess.run(['powershell.exe','-NoProfile','-Command','dsh plugin --profile '+profile+' link '+package],check=True)
    print(json.dumps(result,ensure_ascii=True))
