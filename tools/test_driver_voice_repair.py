"""Config and microphone lease regressions; no live account, device or network."""
import json,os,sys,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from launcher import _prepare_qt
_prepare_qt()
from core.model_settings import dsh_driver,resolve_config,save_settings
from core.realtime_voice import ManualInputCapture


class DriverVoiceRepairTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows selection provider fixture')
    def test_optional_uia_operations_do_not_discard_selected_text(self):
        import subprocess
        root=Path(__file__).resolve().parents[1]
        source=(root/'tools/windows_selection.ps1').read_text(encoding='utf-8-sig')
        function=source[source.index('function Read-Selection('):source.index('if(-not (Read-Selection')]
        script='''Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$result=@{text='';context=''}
$script:rangeStub=[pscustomobject]@{}
$script:rangeStub | Add-Member ScriptMethod GetText {param($limit) 'fixture selected text'}
$script:rangeStub | Add-Member ScriptMethod Clone {throw 'paragraph unsupported'}
$script:rangeStub | Add-Member ScriptMethod GetBoundingRectangles {throw 'rectangles unsupported'}
$script:patternStub=[pscustomobject]@{}
$script:patternStub | Add-Member ScriptMethod GetSelection { ,$script:rangeStub }
$element=[pscustomobject]@{Current=[pscustomobject]@{IsPassword=$false;IsOffscreen=$false;BoundingRectangle=[pscustomobject]@{X=10;Y=10;Width=300;Height=80}}}
$element | Add-Member ScriptMethod GetCurrentPattern {param($pattern) $script:patternStub}
'''+function+'''\n$null=Read-Selection @($element)
if($result.text -ne 'fixture selected text' -or -not $result.selection_verified){exit 2}
Write-Output 'selected text retained'
'''
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],capture_output=True,timeout=8,creationflags=0x08000000)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))

    def test_dsh_credential_reference_is_resolved_without_copying_secret(self):
        with tempfile.TemporaryDirectory() as folder:
            home=Path(folder);p=home/'.dsh/profiles/web';p.mkdir(parents=True)
            (p/'cordis.patch.yml').write_text(json.dumps([
                dict(id='llm-pi-ai',config=dict(providers={'test-provider':dict(baseURL='https://example.test/v1',apiKeyEnv='FIXTURE_API_KEY')})),
                dict(id='agent-default-model',config=dict(provider='test-provider',model='fixture-model'))]),encoding='utf-8')
            (home/'.dsh/.credentials.yaml').write_text('refs:\n  FIXTURE_API_KEY: fixture-only-secret\n',encoding='utf-8')
            raw=dict(driver_source='dsh',api_key='')
            resolved=resolve_config(raw,home)
            self.assertEqual(resolved['api_key'],'fixture-only-secret')
            self.assertEqual(resolved['model'],'fixture-model');self.assertEqual(raw['api_key'],'')
            self.assertNotIn('api_key',dsh_driver(home))

    def test_save_keeps_unrelated_settings_and_edge_is_default(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json';path.write_text(json.dumps(dict(run_as_admin=False,pet_scale=.8)),encoding='utf-8')
            save_settings(dict(driver_source='custom',base_url='https://example.test/v1',model='fixture',api_key='fixture'),path)
            saved=json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(saved['pet_scale'],.8);self.assertFalse(saved['run_as_admin'])
            self.assertEqual(saved.get('tts_provider') or 'edge','edge')

    def test_microphone_released_and_reacquired_without_dropping_connection(self):
        live=[];events=[]
        class Stream:
            def __enter__(self):live.append(self);events.append('opened');return self
            def __exit__(self,*args):live.remove(self);events.append('closed')
        owner=SimpleNamespace(_manual_capture_hold=threading.Event(),_manual_capture_released=threading.Event(),
            _cfg={},_capture=lambda *args:None,connected=True)
        with ManualInputCapture(owner,SimpleNamespace(RawInputStream=lambda **kwargs:Stream())) as capture:
            self.assertEqual(len(live),1)
            owner._manual_capture_hold.set();capture.sync()
            self.assertEqual(live,[]);self.assertTrue(owner._manual_capture_released.is_set());self.assertTrue(owner.connected)
            owner._manual_capture_hold.clear();capture.sync()
            self.assertEqual(len(live),1);self.assertTrue(owner.connected)
        self.assertEqual(live,[]);self.assertEqual(events,['opened','closed','opened','closed'])


if __name__=='__main__':unittest.main()
