"""Synthetic 1080p workload and Windows-only capture of our own animated window."""
import json
import sys
import threading
import time
from pathlib import Path
import av
import cv2
import numpy as np
from recorder import ScreenRecorder
from green_targets import green_score, load_variants, config_for_region


def inspect(path):
    with av.open(str(path)) as video:
        frames=list(video.decode(video=0))
        stamps=[float(f.pts*f.time_base) for f in frames]
        assert len(frames)>1 and all(b>a for a,b in zip(stamps,stamps[1:]))
        duration=float(video.duration/av.time_base)
        # Compare images instead of trusting frame-count metadata alone.
        changed=sum(not np.array_equal(a.to_ndarray(),b.to_ndarray()) for a,b in zip(frames,frames[1:]))
        assert changed>len(frames)//2, 'captured video lacks motion'
        return dict(decoded_frames=len(frames),duration_seconds=duration,
                    decoded_fps=len(frames)/duration,changed_transitions=changed,
                    width=frames[0].width,height=frames[0].height)


class MovingScene:
    def __init__(self):
        rng=np.random.default_rng(10)
        self.base=rng.integers(15,180,(1080,1920,4),dtype=np.uint8)
        self.n=0
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def grab(self,region):
        self.n+=1
        frame=self.base.copy()
        x=(self.n*23)%1600
        frame[200:700,x:x+280,:3]=(20,240,200)
        frame[:40,:,:3]=self.n%200
        return frame


def synthetic(folder):
    region=dict(left=0,top=0,width=1920,height=1080)
    variants=load_variants(region,config_for_region(region)['scales'])
    scene=MovingScene();events=[];stop=threading.Event();detection_times=[]
    sample=np.full((1080,1920,3),60,np.uint8)
    # Include the real crosshair assets so recognition does its normal candidate work.
    for i in range(3):
        mask=cv2.imread(str(Path('assets')/('green-target-%d.png'%(i+1))),0)
        h,w=mask.shape;crop=sample[500:500+h,500+i*w*3:500+i*w*3+w]
        crop[mask>100]=(20,240,20)
    def detect():
        while not stop.is_set():
            began=time.monotonic();green_score(sample,variants,.8)
            detection_times.append(time.monotonic()-began)
            stop.wait(max(0,.1-(time.monotonic()-began)))
    worker=threading.Thread(target=detect);worker.start()
    recorder=ScreenRecorder(lambda *e:events.append(e),folder=folder,capture_factory=lambda:scene)
    try:
        recorder.start(region);time.sleep(3);recorder.stop();recorder.thread.join(20)
        assert not recorder.thread.is_alive(),'recording stop timed out'
    finally:
        stop.set();worker.join()
    errors=[data for kind,data in events if data[0]=='error'];assert not errors,errors
    saved=[data[1] for kind,data in events if data[0]=='saved'];assert saved,events
    path=Path(saved[0]);target=folder/'recording-demo-720p.mp4';path.replace(target)
    report=dict(metrics=recorder.metrics,video=inspect(target),
                detection_checks=len(detection_times),
                mean_detection_ms=1000*sum(detection_times)/max(1,len(detection_times)))
    assert report['video']['decoded_frames']==recorder.metrics['encoded']
    assert recorder.metrics['max_queue']<=2
    assert report['video']['decoded_fps']>=20,report
    assert len(detection_times)>=15,report
    return report


def live_window(folder):
    import tkinter as tk
    from alert import enable_dpi_awareness
    enable_dpi_awareness()
    root=tk.Tk();root.overrideredirect(True);root.geometry('640x360+100+100')
    root.attributes('-topmost',True)
    canvas=tk.Canvas(root,width=640,height=360,highlightthickness=0,bg='#182438');canvas.pack()
    box=canvas.create_rectangle(10,90,110,190,fill='#40e0b0')
    root.update();time.sleep(.2);events=[]
    recorder=ScreenRecorder(lambda *e:events.append(e),folder=folder)
    try:
        recorder.start(dict(left=100,top=100,width=640,height=360))
        began=time.monotonic()
        while time.monotonic()-began<2.5:
            x=int((time.monotonic()-began)*180)%520
            canvas.coords(box,x,90,x+100,190);root.update();time.sleep(1/60)
        recorder.stop();recorder.thread.join(20)
        assert not recorder.thread.is_alive()
        errors=[data for kind,data in events if data[0]=='error'];assert not errors,errors
        paths=[data[1] for kind,data in events if data[0]=='saved'];assert paths,events
        report=dict(metrics=recorder.metrics,video=inspect(paths[0]))
        assert report['video']['decoded_fps']>=20,report
        Path(paths[0]).unlink()
        return report
    finally:
        recorder.stop()
        if recorder.thread:recorder.thread.join(20)
        root.destroy()


if __name__=='__main__':
    folder=Path('dist');folder.mkdir(exist_ok=True)
    report={'workload':'synthetic 1920x1080 motion + 10 Hz recognition; output max 720p/target 30fps',
            'synthetic':synthetic(folder)}
    if sys.platform=='win32':report['windows_mss_own_window']=live_window(folder)
    (folder/'recording-performance.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))
