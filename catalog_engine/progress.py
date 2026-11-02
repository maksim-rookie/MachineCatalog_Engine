from __future__ import annotations
from . import PROJECT_NAME
import json
import threading
import time
from datetime import datetime
from pathlib import Path

class RunProgress:
    """Monotone work-unit progress; never advertised as a time prediction."""
    def __init__(self,directory:Path,files_total:int):
        self.directory=directory
        self.files_total=files_total
        self.started_at=datetime.now().astimezone().isoformat()
        self.start=time.perf_counter()
        self.last_percent=0.0
        self.last_print_percent=-100.0
        self.last_print=time.perf_counter()
        self.last_stage=''
        self.file_timings=[]
        self._stop=threading.Event()
        self._lock=threading.Lock()
        self.update(0,'Start',force=True)
        self._thread=threading.Thread(target=self._heartbeat,daemon=True)
        self._thread.start()

    @staticmethod
    def format_duration(seconds):
        seconds=max(0,int(seconds));h,rem=divmod(seconds,3600);m,s=divmod(rem,60)
        return f'{h:02d}:{m:02d}:{s:02d}'

    def _heartbeat(self):
        while not self._stop.wait(10):
            if time.perf_counter()-self.last_print>=9:
                print(f'  ... {self.last_percent:.1f}% | {self.last_stage} | elapsed {self.format_duration(time.perf_counter()-self.start)}',flush=True)

    def update(self,percent,stage,filename='',detail='',force=False):
        with self._lock:
            self.last_percent=min(100.0,max(self.last_percent,float(percent)))
            elapsed=time.perf_counter()-self.start
            row={'timestamp':datetime.now().astimezone().isoformat(),'progress_percent':round(self.last_percent,2),
                 'elapsed_seconds':round(elapsed,3),'stage':stage,'source_file':filename,'detail':detail}
            with (self.directory/'RUN_PROGRESS.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            if force or stage!=self.last_stage or self.last_percent-self.last_print_percent>=2 or time.perf_counter()-self.last_print>=5:
                fill=int(self.last_percent//5)
                print(f'[{"#"*fill}{"-"*(20-fill)}] {self.last_percent:5.1f}% | {stage} | {filename} | {detail} | elapsed {self.format_duration(elapsed)}',flush=True)
                self.last_print_percent=self.last_percent;self.last_print=time.perf_counter()
            self.last_stage=stage

    def file_work(self,index,fraction,stage,filename='',detail=''):
        fraction=max(0,min(1,fraction))
        self.update(88*(index+fraction)/max(1,self.files_total),stage,filename,f'file {100*fraction:.0f}%: '+detail)

    def finish(self,errors=False,quality_passed=False):
        self._stop.set();self._thread.join(timeout=1)
        stage='Completed with processing errors' if errors else 'Completed; quality review required' if not quality_passed else 'Completed'
        self.update(100,stage,force=True)
        elapsed=time.perf_counter()-self.start
        return {'project_name':PROJECT_NAME,'project_version':'8.4.5.1','run_id':self.directory.name,'started_at':self.started_at,
                'finished_at':datetime.now().astimezone().isoformat(),'elapsed_seconds':round(elapsed,3),
                'duration_hms':self.format_duration(elapsed),'progress_percent':100,
                'progress_kind':'weighted_work_units_not_elapsed_time','completed_with_errors':errors,
                'quality_passed':quality_passed,'files_total':self.files_total,'file_timings':self.file_timings,
                'cache_hits':sum(r.get('cache_hit',False) for r in self.file_timings)}

    def stop(self):
        self._stop.set();self._thread.join(timeout=1)
