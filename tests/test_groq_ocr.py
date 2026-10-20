import base64
import io
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError

import cv2
import numpy as np
from farmbot import groq_ocr as groq
from farmbot.vision import Vision, VisionError, OCRLine, Match
from farmbot.runner import read_results
from farmbot.models import Stage
from farmbot.map_navigation import MapNavigator
from farmbot.safety import StopController, StopRequested


LINE = '2-8 concluída - 1:12.79 - +2.282 - +5.800 EXP'


def response(lines, **extra):
    return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(
        {'complete': True, 'lines': lines, **extra})}}], 'usage': {'total_tokens': 2200}}


class GroqTests(unittest.TestCase):
    def setUp(self):
        groq._calls.clear()
        groq._worker = None
        self.key = patch.object(groq, 'api_key', return_value='test-secret')
        self.key.start()
        self.addCleanup(self.key.stop)
        self.client = groq.GroqOCR({'enabled': True})
        self.crop = np.zeros((80, 300, 3), np.uint8)
        self.crop[5:14, 8:180] = (0,200,0)
        self.crop[35:44, 8:180] = (0,200,0)
        self.vision = Vision({'groq': {'enabled': True}, 'logs_linewise': True}, Path('.'))

    def test_transmits_only_crop_json_and_preserves_identical_rows(self):
        full = np.full((200, 600, 3), 123, np.uint8)
        full[20:100, 100:400] = self.crop
        with patch.object(groq, '_post', return_value=response([LINE, LINE])) as post:
            lines = self.vision.remote_lines(full, [100/600, .1, .5, .4], 'logs')
        self.assertEqual([l.text for l in lines], [LINE, LINE])
        payload, key, timeout = post.call_args.args
        self.assertEqual(key, 'test-secret')
        self.assertNotIn('test-secret', json.dumps(payload))
        content = payload['messages'][0]['content']
        encoded = base64.b64decode(content[1]['image_url']['url'].split(',')[1])
        actual = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
        np.testing.assert_array_equal(actual, self.crop)
        self.assertEqual(payload['reasoning_effort'], 'none')

    def test_transport_identifies_application_and_keeps_credentials_in_header(self):
        stream = io.BytesIO(b'{"choices": []}')
        opener = Mock()
        opener.open.return_value = stream
        with patch.object(groq.request, 'build_opener', return_value=opener) as build:
            self.assertEqual(groq._post({'model': 'test-model'}, 'test-secret', 4), {'choices': []})
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_header('User-agent'), 'farmgold-bot-MPG/1.0')
        self.assertEqual(req.get_header('Authorization'), 'Bearer test-secret')
        self.assertNotIn(b'test-secret', req.data)
        self.assertEqual(req.full_url, 'https://api.groq.com/openai/v1/chat/completions')
        self.assertIsInstance(build.call_args.args[0], groq.NoRedirect)

    def test_thin_crops_get_black_padding_without_resizing_or_expanding_roi(self):
        root = Path(__file__).resolve().parents[1]
        fixture = cv2.imread(str(root / 'tests/fixtures/stage_2_8_after_false_258.png'))
        self.assertEqual(fixture.shape[:2], (23, 420))
        # The native label is 31 px high. Include a narrow crop to cover width.
        cases = [fixture, np.full((31, 560, 3), 77, np.uint8),
                 np.full((40, 7, 3), 91, np.uint8),
                 np.full((1, 1, 3), 123, np.uint8)]
        for crop in cases:
            with self.subTest(shape=crop.shape):
                original = crop.copy()
                groq._calls.clear()
                with patch.object(groq, '_post', return_value=response(['2-8 Posto - Inferno'])) as post:
                    self.assertEqual(self.client.transcribe(crop, 'stage'), ['2-8 Posto - Inferno'])
                payload = post.call_args.args[0]
                uri = payload['messages'][0]['content'][1]['image_url']['url']
                encoded = base64.b64decode(uri.split(',', 1)[1])
                sent = cv2.imdecode(np.frombuffer(encoded, np.uint8), cv2.IMREAD_COLOR)
                height, width = crop.shape[:2]
                self.assertEqual(sent.shape[:2], (max(32, height), max(32, width)))
                top, left = (sent.shape[0] - height) // 2, (sent.shape[1] - width) // 2
                np.testing.assert_array_equal(sent[top:top+height, left:left+width], original)
                sent[top:top+height, left:left+width] = 0
                self.assertFalse(sent.any())
                np.testing.assert_array_equal(crop, original)

    def test_missing_key_disabled_and_unbounded_roi_never_send(self):
        with patch.object(groq, '_post') as post, patch.object(groq, 'api_key', return_value=''):
            self.assertIsNone(self.client.transcribe(self.crop, 'logs', expected_rows=2))
        post.assert_not_called()
        with patch.object(groq, '_post') as post:
            self.assertIsNone(groq.GroqOCR().transcribe(self.crop, 'logs', expected_rows=2))
            self.assertIsNone(self.vision.remote_lines(self.crop, None, 'logs'))
            self.assertIsNone(self.client.transcribe(np.zeros((500,1600,3),np.uint8),'stage'))
        post.assert_not_called()

    def test_invalid_json_partial_truncated_and_wrong_count_rejected(self):
        cases = [response([LINE]), response([LINE, LINE], complete=False),
                 response([LINE, LINE], extra=1), response([LINE, None])]
        truncated = response([LINE, LINE]); truncated['choices'][0]['finish_reason'] = 'length'
        cases += [truncated, {'choices': []}, {'choices':[{'finish_reason':'stop','message':{'content':'oops'}}]}]
        for body in cases:
            groq._calls.clear()
            with self.subTest(body=body), patch.object(groq, '_post', return_value=body):
                self.assertIsNone(self.client.transcribe(self.crop,'logs',expected_rows=2))

    def test_conflicting_confirmed_local_number_rejects_entire_remote_snapshot(self):
        known = [OCRLine(LINE,1,(0,0,1,1)), OCRLine('',0,(0,0,1,1))]
        with patch.object(groq,'_post',return_value=response([LINE.replace('2.282','9.999'),LINE])):
            self.assertIsNone(self.vision.remote_lines(self.crop,[0,0,1,1],'logs',local_lines=known))

    def test_local_success_makes_no_request_and_invalid_line_uses_fallback(self):
        cfg = {'logs_roi':[0,0,1,1]}
        good = OCRLine(LINE,1,(0,0,1,1))
        with patch.object(self.vision,'read_lines',return_value=[good,good]), patch.object(groq,'_post') as post:
            self.assertEqual(len(read_results(self.vision,self.crop,cfg)),2)
            post.assert_not_called()
        with patch.object(self.vision,'read_lines',return_value=[good,OCRLine('',0,(0,0,1,1))]), \
                patch.object(groq,'_post',return_value=response([LINE,LINE])):
            diagnostics={}
            rows=read_results(self.vision,self.crop,cfg,diagnostics)
            self.assertEqual([r.gold for r in rows],[2282,2282])
            self.assertEqual(diagnostics['provider'],'groq')

    def test_local_timeout_uses_remote_but_unavailable_preserves_error(self):
        with patch.object(self.vision,'read_lines',side_effect=VisionError('local timeout')), \
                patch.object(groq,'_post',return_value=response([LINE,LINE])):
            self.assertEqual(len(read_results(self.vision,self.crop,{'logs_roi':[0,0,1,1]})),2)
        with patch.object(self.vision,'read_lines',side_effect=VisionError('local timeout')), \
                patch.object(groq,'api_key',return_value=''), self.assertRaisesRegex(VisionError,'local timeout'):
            read_results(self.vision,self.crop,{'logs_roi':[0,0,1,1]})

    def test_budget_is_shared_and_http_errors_have_cooldown_without_secret(self):
        self.client.config['max_requests_per_hour']=1
        with patch.object(groq,'_post',side_effect=HTTPError('url',429,'test-secret',None,None)) as post, \
                self.assertLogs('farmbot.groq_ocr',level='WARNING') as logs:
            self.assertIsNone(self.client.transcribe(self.crop,'logs',expected_rows=2))
            self.assertIsNone(self.client.transcribe(self.crop,'logs',expected_rows=2))
        self.assertEqual(post.call_count,1)
        self.assertNotIn('test-secret',''.join(logs.output))
        other=groq.GroqOCR({'enabled':True,'max_requests_per_hour':1})
        with patch.object(groq,'_post') as post:
            self.assertIsNone(other.transcribe(self.crop,'logs',expected_rows=2))
        post.assert_not_called()

    def test_timeout_discards_late_response_and_prevents_overlapping_requests(self):
        gate=threading.Event()
        self.client.config['timeout_seconds']=.1
        def slow(*args):
            gate.wait(2)
            return response([LINE,LINE])
        try:
            with patch.object(groq,'_post',side_effect=slow) as post:
                self.assertIsNone(self.client.transcribe(self.crop,'logs',expected_rows=2))
                groq._calls.clear()
                self.assertIsNone(groq.GroqOCR({'enabled':True}).transcribe(self.crop,'logs',expected_rows=2))
                self.assertEqual(post.call_count,1)
        finally:
            gate.set(); groq._worker.join(2)
        self.assertIsNone(self.client.last_usage)

    def test_http_diagnostics_allow_only_known_codes_and_never_echo_response(self):
        cases = [
            (403, {'error': {'code': 'model_permission_blocked_org', 'message': 'test-secret'}}, 'organização'),
            (403, {'error': {'code': 'model_permission_blocked_project'}}, 'projeto'),
            (403, {'error': {'code': 'test-secret', 'message': 'test-secret'}}, 'acesso negado'),
            (403, {'error': {'code': ['test-secret']}}, 'causa não identificada'),
            (401, {}, 'autenticação'),
            (429, {}, 'limite'),
        ]
        for status, body, expected in cases:
            with self.subTest(status=status, expected=expected):
                stream = io.BytesIO(json.dumps(body).encode())
                text = groq._http_failure(HTTPError('url', status, 'test-secret', {}, stream))
                self.assertIn(expected, text)
                self.assertNotIn('test-secret', text)
                self.assertTrue(stream.closed)
        for raw in (b'<html>test-secret</html>', b'test-secret' * 1000):
            text = groq._http_failure(HTTPError('url', 403, 'test-secret', {}, io.BytesIO(raw)))
            self.assertNotIn('test-secret', text)
            self.assertIn('HTTP 403', text)

    def test_forbidden_is_reported_once_and_other_cases_are_skipped_during_cooldown(self):
        body = json.dumps({'error': {'code': 'model_permission_blocked_project'}}).encode()
        with patch.object(groq, '_post', side_effect=HTTPError('url', 403, '', {}, io.BytesIO(body))) as post:
            self.assertIsNone(self.client.transcribe(self.crop, 'stage'))
            self.assertIn('modelo bloqueado no projeto', self.client.last_status)
            self.assertIsNone(self.client.transcribe(self.crop, 'stage'))
            self.assertEqual(self.client.last_status, 'não executado: pausa após erro anterior')
            self.assertEqual(post.call_count, 1)

    def test_stop_during_network_wait_propagates_without_using_response(self):
        gate=threading.Event(); stop=StopController(key_check=lambda:False)
        def slow(*args):
            stop.request_stop('test stop'); gate.wait(2)
            return response([LINE,LINE])
        try:
            with patch.object(groq,'_post',side_effect=slow), self.assertRaises(StopRequested):
                self.client.transcribe(self.crop,'logs',expected_rows=2,stop=stop)
        finally:
            gate.set(); groq._worker.join(2)

    def test_stage_fallback_is_still_confirmed_on_two_fresh_frames(self):
        from farmbot.config import load_config
        root=Path(__file__).resolve().parents[1]
        cfg=load_config(root/'config.json')['navigation']
        cfg.update(label_timeout=1, label_poll_interval=.001, label_hover_delay=.001,
                   current_stage_offset=[0,0,300,30], battle_hover_offset=[0,0,24,24])
        window=SimpleNamespace(assert_foreground=Mock()); stop=StopController(key_check=lambda:False)
        nav=MapNavigator(window,self.vision,Mock(),stop,cfg)
        for reader in (nav.label_vision,nav.label_fallback_vision,nav.label_edge_vision,nav.label_edge_confirm_vision):
            reader.read_lines=Mock(return_value=[])
        captures=[]
        def frame():
            fresh=SimpleNamespace(image=self.crop.copy());captures.append(fresh)
            return fresh,Match(0,0,52,52,1,'menu')
        nav._capture_menu=frame
        with patch.object(self.vision.remote_ocr,'transcribe',return_value=['2-8 Posto - Inferno']) as remote:
            self.assertEqual(nav.current_stage(),Stage('2-8','Inferno'))
        self.assertEqual(remote.call_count,2)
        self.assertGreaterEqual(len(captures),3)
        self.assertEqual(window.assert_foreground.call_count,2)

    def test_config_rejects_secrets_and_unbounded_limits(self):
        for bad in ({'api_key':'secret'},{'enabled':1},{'timeout_seconds':float('inf')},
                    {'min_interval_seconds':0},{'max_requests_per_hour':True},
                    {'max_requests_per_hour':1.5},{'model':''}):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                groq.settings(bad)

    def test_remote_snapshots_reach_collector_only_after_temporal_confirmation(self):
        from farmbot.runner import Collector
        from farmbot.storage import Store
        import tempfile
        clock=SimpleNamespace(now=0.0,check=lambda:None)
        def wait(seconds): clock.now+=seconds
        clock.wait=wait
        image=np.zeros((150,400,3),np.uint8)
        for top in (5,35,65,95,125): image[top:top+9,10:300]=(0,200,0)
        def line(seconds): return f'2-8 concluída - 0:{seconds:02d}.00 - +100 - +200 EXP'
        baseline=[line(s) for s in (10,11,12,13,14)]
        updated=[line(s) for s in (12,13,14,15,15)]
        frame=SimpleNamespace(image=image)
        window=SimpleNamespace(capture=Mock(return_value=frame),assert_foreground=Mock())
        nav=SimpleNamespace(current_stage=lambda:Stage('2-8','Inferno'))
        with tempfile.TemporaryDirectory() as tmp, Store(':memory:') as store:
            cfg={'measurement':{'battles_per_stage':2,'poll_interval':20,'battle_timeout':100,
                               'stage_timeout':200,'log_order':'oldest_first','max_log_gap':120,
                               'logs_roi':[0,0,1,1]},'navigation':{},'_base_dir':Path(tmp),
                 'output':{'artifacts':tmp}}
            responses=iter([baseline,baseline,updated,updated])
            def remote(*args,**kwargs):
                self.assertEqual(store.records(),[])
                return next(responses)
            with patch.object(self.vision,'read_lines',return_value=[OCRLine('',0,(0,0,1,1))]*5), \
                    patch.object(self.vision.remote_ocr,'transcribe',side_effect=remote) as request, \
                    patch('farmbot.runner.time',SimpleNamespace(monotonic=lambda:clock.now)):
                count=Collector(window,self.vision,nav,clock,store,cfg).collect(Stage('2-8','Inferno'))
            self.assertEqual(count,2)
            self.assertEqual(request.call_count,4)
            self.assertEqual([r.duration for r in store.records()],[15,15])
            self.assertEqual(len({r.event_id for r in store.records()}),2)

    def test_remote_stage_ambiguity_cannot_be_resolved_by_guessing_expected_id(self):
        from farmbot.config import load_config
        root=Path(__file__).resolve().parents[1]
        cfg=load_config(root/'config.json')['navigation']
        cfg['current_stage_offset']=[0,0,300,30]
        nav=MapNavigator(None,self.vision,None,StopController(key_check=lambda:False),cfg)
        for reader in (nav.label_vision,nav.label_fallback_vision,nav.label_edge_vision,nav.label_edge_confirm_vision):
            reader.read_lines=Mock(return_value=[])
        with patch.object(self.vision.remote_ocr,'transcribe',return_value=['2-8 Inferno 2-9']):
            self.assertIsNone(nav._read_stage_from_frame(SimpleNamespace(image=self.crop),Match(0,0,52,52,1,'menu')))
