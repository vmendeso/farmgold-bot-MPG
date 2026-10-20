"""Compare local OCR with optional Groq calls on annotated, cropped fixtures."""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from farmbot.config import load_config
from farmbot.groq_ocr import GroqOCR
from farmbot.map_navigation import MapNavigator
from farmbot.results import parse_log_entry
from farmbot.safety import StopController
from farmbot.vision import Vision, Match, _roi_box


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default=str(ROOT/'config.json'))
    parser.add_argument('--execute',action='store_true',help='Envia quatro recortes para Groq; usa créditos da conta')
    parser.add_argument('--output',default=str(ROOT/'artifacts/groq-benchmark.json'))
    args=parser.parse_args()
    cfg=load_config(args.config)
    local=Vision({**cfg['vision'],'groq':{'enabled':False}},cfg['_base_dir'])
    remote=GroqOCR({**cfg['vision'].get('groq',{}),'enabled':True})
    if args.execute and not remote.available:
        parser.error('Chave indisponível. Execute: python scripts/configure_groq.py --check (diagnóstico sem mostrar a chave); use python scripts/configure_groq.py para cadastrar')
    import cv2
    fixtures=ROOT/'tests/fixtures'
    battle=cv2.imread(str(fixtures/'scale100_battle.png'))
    menu=local.locate(battle,cfg['navigation']['menu'])
    if menu is None:
        raise RuntimeError('Âncora da captura de teste não reconhecida')
    anchor=cfg['measurement']['logs_anchor']; factor=menu.width/anchor['reference_width']
    dx,dy,w,h=anchor['offset']; x,y=round(menu.x+dx*factor),round(menu.y+dy*factor)
    native_logs=battle[y:y+round(h*factor),x:x+round(w*factor)]
    with StopController() as stop:
        nav=MapNavigator(None,local,None,stop,cfg['navigation'])
        x,y,w,h=nav._box(battle,menu,cfg['navigation']['current_stage_offset'])
        cases=[
            ('logs_small','logs',cv2.imread(str(fixtures/'logs_bad_digit_a.png')),
             [('4-7',s,1362,2965,'victory') for s in [43.09,43.33,43.69,42.46,43.48]]),
            ('logs_native','logs',native_logs,
             [('2-8',s,2282,5800,'victory') for s in [70.75,74.27,71.82,71.86,72.79]]),
            ('stage_after_258','stage',cv2.imread(str(fixtures/'stage_2_8_after_false_258.png')),'2-8|Inferno'),
            ('stage_native','stage',battle[y:y+h,x:x+w],'2-8|Inferno')]
        report={'remote_executed':args.execute,'model':remote.config['model'],'cases':[]}
        for name,kind,crop,expected in cases:
            stop.check(); started=time.monotonic()
            if kind=='logs':
                lines=local.read_lines(crop,mode='logs')
                rows=[parse_log_entry(line.text) for line in lines]
                actual=[r.fingerprint if r else None for r in rows]
            else:
                config=copy.deepcopy(cfg['navigation']);config['current_stage_offset']=[0,0,crop.shape[1],crop.shape[0]]
                reader=MapNavigator(None,local,None,stop,config)
                stage=reader._read_stage_from_frame(SimpleNamespace(image=crop),Match(0,0,52,52,1,'menu'))
                actual=stage.key if stage else None
            item={'name':name,'kind':kind,'expected':expected,'local':actual,'local_correct':actual==expected,
                  'local_seconds':round(time.monotonic()-started,3)}
            if args.execute:
                stop.wait(remote.config['min_interval_seconds'])
                started=time.monotonic()
                text=remote.transcribe(crop,kind,expected_rows=len(expected) if kind=='logs' else None,stop=stop)
                value=None
                if text:
                    if kind=='logs':
                        rows=[parse_log_entry(t) for t in text]
                        value=[r.fingerprint if r else None for r in rows]
                    else:
                        stage,ambiguous=nav._parse_stage_lines([SimpleNamespace(text=text[0],confidence=1)])
                        value=stage.key if stage and not ambiguous else None
                item.update(remote=value,remote_correct=value==expected,
                            remote_seconds=round(time.monotonic()-started,3),usage=remote.last_usage,
                            remote_status=remote.last_status)
            report['cases'].append(item)
            destination=Path(args.output);destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"{name}: local={'OK' if item['local_correct'] else 'FALHOU'}; "
                  f"Groq={('OK' if item.get('remote_correct') else 'FALHOU/INDISPONÍVEL') if args.execute else 'não executado'}")
            if args.execute and not item.get('remote_correct'):
                print('  Diagnóstico:', item['remote_status'])
        print('Relatório:',args.output)
        return 0 if all(i['local_correct'] and (not args.execute or i['remote_correct']) for i in report['cases']) else 1


if __name__=='__main__':
    raise SystemExit(main())
