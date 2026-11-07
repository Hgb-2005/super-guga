"""Local WebRTC AEC3: microphone speech stays live during the agent's playback.

Only numeric diagnostics are retained. Render callbacks enqueue the exact PCM
sent to the speaker; one capture callback owns the native DSP instance.
"""
from collections import deque
import threading
import time


class VoiceEcho:
    def __init__(self):
        import numpy as np
        from pywebrtc_audio import AudioProcessor
        self.np=np
        self.processor=AudioProcessor(sample_rate=16000,echo_cancellation=True,
            noise_suppression=False,auto_gain_control=False,stream_delay_ms=0)
        self.lock=threading.Lock();self.render_queue=deque(maxlen=8)
        self.pending=np.empty(0,dtype=np.int16);self.closed=False
        self.frames=0;self.reference_blocks=0;self.dropped_references=0;self.max_process_ms=0.
        self.input_rms=0.;self.clean_rms=0.;self.probability=0.

    def render(self,raw):
        with self.lock:
            if self.closed:return
            if len(self.render_queue)==self.render_queue.maxlen:self.dropped_references+=1
            self.render_queue.append(raw);self.reference_blocks+=1

    def process(self,raw):
        np=self.np;started=time.perf_counter();near=np.frombuffer(raw,dtype='<i2')
        with self.lock:
            if self.closed:return bytes(len(raw))
            blocks=list(self.render_queue);self.render_queue.clear()
        if blocks:
            rendered=np.frombuffer(b''.join(blocks),dtype='<i2')
            # The production stream is 24 kHz; AEC capture is 16 kHz.
            far=np.interp(np.arange(0,len(rendered),1.5),np.arange(len(rendered)),rendered).astype(np.int16)
            self.pending=np.concatenate((self.pending,far))
        size=len(near)
        if len(self.pending)>size*8:self.pending=self.pending[-size*8:]
        reference=np.zeros(size,dtype=np.int16);take=min(size,len(self.pending))
        reference[:take]=self.pending[:take];self.pending=self.pending[take:]
        clean=self.processor.process(near,reference)
        self.frames+=1;self.probability=float(self.processor.speech_probability)
        self.input_rms=float(np.sqrt(np.mean(near.astype(np.float32)**2)))/32768 if size else 0.
        self.clean_rms=float(np.sqrt(np.mean(clean.astype(np.float32)**2)))/32768 if size else 0.
        self.max_process_ms=max(self.max_process_ms,(time.perf_counter()-started)*1000)
        return clean.tobytes()

    def status(self):
        return dict(engine='webrtc_aec3',frames=self.frames,reference_blocks=self.reference_blocks,
            dropped_references=self.dropped_references,max_process_ms=round(self.max_process_ms,3),
            input_rms=round(self.input_rms,5),clean_rms=round(self.clean_rms,5),
            speech_probability=round(self.probability,3),local_only=True,audio_saved=False)

    def close(self):
        with self.lock:self.closed=True;self.render_queue.clear()
