"""Open the user's installed native DSH, keeping its model and conversation setup.

The model overlay contains no credential. The key is handed to this child process
through an environment variable; existing DSH providers and saved keys remain intact.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def open_native():
    path=ROOT/'runtime/dsh-plugin/native-launcher.json'
    if not path.is_file():raise RuntimeError('请使用电脑原来的 DeepSeek Harness 图标启动本地 UI；未配置另一个网页版入口')
    target=Path(json.loads(path.read_text(encoding='utf-8'))['launcher'])
    if not target.is_file():raise RuntimeError('本地 DSH 启动器路径已失效，请使用原来的 DeepSeek Harness 图标')
    import psutil
    for process in psutil.process_iter(['name','cmdline']):
        try:
            if str(target).casefold() in ' '.join(process.info.get('cmdline') or []).casefold():
                print('本地 DSH UI 已运行，请使用现有窗口。');return 0
        except (psutil.Error,OSError):pass
    pythonw=Path(sys.executable).with_name('pythonw.exe')
    subprocess.Popen([str(pythonw if pythonw.exists() else sys.executable),str(target)],cwd=target.parent)
    return 0


def prepare_model():
    from core.model_settings import load_config
    cfg=load_config()
    if not cfg.get('api_key') or not cfg.get('base_url') or not cfg.get('model'):
        raise ValueError('请先在咕嘎配置中填写可用的聊天模型')
    folder=ROOT/'runtime/dsh-plugin';folder.mkdir(parents=True,exist_ok=True)
    provider=dict(displayName='咕嘎当前聊天模型',apiKeyEnv='GUGA_CHAT_API_KEY',
        api='openai-completions',baseURL=cfg['base_url'],
        compat=dict(supportsDeveloperRole=False,supportsReasoningEffort=False),
        retryPolicy=dict(mode='normal',maxRetries=0),
        models=[dict(id=cfg['model'],name=cfg['model'],contextWindow=131072,maxTokens=4096,
                     input=['text','image'],reasoningEfforts=False)])
    overlay=[dict(id='llm-pi-ai',config=dict(providers={'guga-chat':provider})),
             dict(id='agent-default-model',config=dict(provider='guga-chat',model=cfg['model']))]
    path=folder/'model.patch.yml'
    path.write_text(json.dumps(overlay,ensure_ascii=False,indent=2),encoding='utf-8')
    env=dict(os.environ,GUGA_CHAT_API_KEY=str(cfg['api_key']))
    return path,env


def command(profile='web',json_events=False):
    model,env=prepare_model()
    cli=Path.home()/'.local/bin/node_modules/@deepseek-ai/dsh/lib/bin.js'
    node=shutil.which('node')
    if not node or not cli.is_file():raise RuntimeError('未找到已安装的DSH')
    args=[node,str(cli),'--profile',profile,'--patch',str(model)]
    # web has its persistent plugin entry; other profiles use the standalone overlay.
    if profile!='web':args.extend(['--patch',str(ROOT/'runtime/dsh-plugin/guga.patch.yml')])
    if json_events:args.extend(['--json','-'])
    return args,env


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',default='web')
    parser.add_argument('--use-guga-model',action='store_true',help='显式使用咕嘎模型覆盖层；默认保留本地DSH模型')
    args=parser.parse_args()
    if args.profile=='web' and not args.use_guga_model:sys.exit(open_native())
    launch,env=command(args.profile)
    sys.exit(subprocess.call(launch,env=env,cwd=ROOT))
