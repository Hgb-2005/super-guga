"""Summarize only real provider-reported numeric usage. No estimated billing."""
from datetime import date
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
FIELDS=('total_tokens','input_tokens','output_tokens','input_text_tokens','input_audio_tokens',
        'output_text_tokens','output_audio_tokens','input_cached_tokens','output_cached_tokens')


def report(day=None):
    day=day or date.today().strftime('%Y%m%d')
    records=[]
    for path in sorted((ROOT/'runtime/voice').glob('usage-'+day+'-*.jsonl')):
        for line in path.read_text(encoding='utf-8').splitlines():
            try:
                item=json.loads(line)
                if item.get('source')=='server_response' and item.get('metering_version')==2:records.append(item)
            except (ValueError,TypeError):pass
    known=[x for x in records if isinstance(x.get('usage'),dict)]
    totals={key:sum(x['usage'][key] for x in known if type(x['usage'].get(key)) is int)
            if any(type(x['usage'].get(key)) is int for x in known) else None for key in FIELDS}
    return dict(day=day,real_rounds=len(records),reported_rounds=len(known),unreported_rounds=len(records)-len(known),
        tokens=totals,billing_estimated=False,
        note='未上报的用量为未知，不按零计算；只统计新版真实服务日志，单元测试与旧日志不混入。',
        recent=records[-10:])


if __name__=='__main__':
    result=report();folder=ROOT/'runtime/native-dsh-voice-qa';folder.mkdir(parents=True,exist_ok=True)
    path=folder/'voice-usage-summary.json';path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print('真实语音回复轮数：'+str(result['real_rounds']))
    print('已上报用量的轮数：'+str(result['reported_rounds'])+'；未上报：'+str(result['unreported_rounds']))
    print('Token 累计：'+('未知' if result['tokens']['total_tokens'] is None else str(result['tokens']['total_tokens'])))
    print('报告：'+str(path))
