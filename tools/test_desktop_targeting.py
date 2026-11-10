"""Visual candidate validation must reject stale or malformed observations."""
import json
import time
import unittest
from unittest.mock import Mock,patch
from PIL import Image,ImageDraw
from core import desktop_targeting as targeting


class VisualTargetTests(unittest.TestCase):
    def setUp(self):
        self.fingerprint=dict(hwnd=7,pid=10,process='fixture.exe',rect=[100,200,1100,1000],title='fixture')
        self.image=Image.new('RGB',(1000,800),'white')

    def reply(self,value):
        response=Mock();response.status_code=200
        response.json.return_value={'choices':[{'message':{'content':json.dumps(value)}}]}
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        return response

    def resolve(self,value,target='测试按钮'):
        with patch.object(targeting,'_frame',return_value=(self.fingerprint,self.image)), \
                patch('core.quick_look._config',return_value={}), \
                patch('core.quick_look._cloud_credentials',return_value=('test-key','https://fixture.invalid','fixture')), \
                patch('requests.post',return_value=self.reply(value)) as post:
            result=targeting.resolve_target(7,target)
        self.assertEqual(post.call_count,1)
        return result

    def test_box_is_mapped_to_the_observed_window(self):
        result=self.resolve(dict(found=True,confidence=.98,name='测试按钮',bbox=[100,200,300,400]))
        self.assertEqual(result['rect'],dict(left=200,top=360,right=400,bottom=520))

    def test_low_confidence_does_not_become_a_click_target(self):
        with self.assertRaisesRegex(RuntimeError,'没有看准'):
            self.resolve(dict(found=True,confidence=.5,bbox=[100,200,300,400]))

    def test_ambiguous_target_does_not_become_a_click_target(self):
        with self.assertRaisesRegex(RuntimeError,'没有看准'):self.resolve(dict(found=False))

    def test_bad_boxes_are_refused(self):
        for box in ([0,0,1001,3],[5,5,4,8],[0,0,float('nan'),10],[True,0,4,9],None):
            with self.subTest(box=box),self.assertRaises(RuntimeError):
                targeting._box(box,self.fingerprint['rect'])

    def test_old_picture_is_refused_before_capture(self):
        with patch.object(targeting,'_frame') as frame,self.assertRaisesRegex(RuntimeError,'过期'):
            targeting.validate_target(7,dict(observed_at=time.monotonic()-13))
        frame.assert_not_called()

    def test_switched_window_is_refused(self):
        target=dict(observed_at=time.monotonic(),window_fingerprint=dict(self.fingerprint,pid=99),
            frame_signature=targeting._signature(self.image))
        with patch.object(targeting,'_frame',return_value=(self.fingerprint,self.image)), \
                self.assertRaisesRegex(RuntimeError,'窗口位置或页面'):
            targeting.validate_target(7,target)

    def test_changed_picture_is_refused(self):
        target=dict(observed_at=time.monotonic(),window_fingerprint=self.fingerprint,
            frame_signature=targeting._signature(self.image))
        with patch.object(targeting,'_frame',return_value=(self.fingerprint,Image.new('RGB',(1000,800),'black'))), \
                self.assertRaisesRegex(RuntimeError,'画面已经变'):
            targeting.validate_target(7,target)

    def test_visual_list_returns_only_confident_targets(self):
        result=self.resolve(dict(elements=[dict(name='按钮甲',confidence=.96,bbox=[100,200,300,400]),
            dict(name='不确定',confidence=.3,bbox=[0,0,1,1])]),target='')
        self.assertEqual([item['name'] for item in result['elements']],['按钮甲'])

    def test_non_object_response_is_refused(self):
        with self.assertRaisesRegex(RuntimeError,'不够明确'):self.resolve([])

    def test_small_target_movement_cannot_hide_in_whole_window_average(self):
        before=Image.new('RGB',(1000,800),'white');after=before.copy()
        ImageDraw.Draw(before).rectangle((100,100,180,140),fill='blue')
        ImageDraw.Draw(after).rectangle((220,100,300,140),fill='blue')
        rect=dict(left=200,top=300,right=280,bottom=340)
        target=dict(observed_at=time.monotonic(),window_fingerprint=self.fingerprint,rect=rect,
            frame_signature=targeting._signature(before),
            target_signature=targeting._target_signature(before,rect,self.fingerprint['rect']))
        with patch.object(targeting,'_frame',return_value=(self.fingerprint,after)), \
                self.assertRaisesRegex(RuntimeError,'按钮附近'):
            targeting.validate_target(7,target)


if __name__=='__main__':unittest.main()
