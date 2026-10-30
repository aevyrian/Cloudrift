"""Read originals; publish complete browser copies only inside the private cache."""
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import time
from pathlib import Path


def needs_compatibility(path, info):
    video=next((s for s in info.get('streams',[]) if s.get('codec_type')=='video'),None)
    if not video:
        raise RuntimeError('文件没有可读取的视频画面，原文件已保留')
    audio=[s.get('codec_name') for s in info['streams'] if s.get('codec_type')=='audio']
    return (path.suffix.lower() not in {'.mp4','.m4v','.mov'} or video.get('codec_name')!='h264'
            or video.get('pix_fmt') not in {'yuv420p','yuvj420p'} or any(c not in {'aac','mp3'} for c in audio))


def duration(info):
    value=info.get('format') or {}
    value=value.get('duration') if isinstance(value,dict) else None
    try:value=float(value) if not isinstance(value,bool) else 0
    except (TypeError,ValueError,OverflowError):return 0
    return value if math.isfinite(value) and value>=0 else 0


class PlaybackCache:
    def __init__(self, root, limit=20*1024**3):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700);self.root.chmod(0o700)
        self.limit=limit;self.records={};self.jobs={};self.encode_lock=asyncio.Lock();self.probe_lock=asyncio.Lock()
        self.ffmpeg=os.environ.get('FFMPEG_PATH','ffmpeg');self.ffprobe=os.environ.get('FFPROBE_PATH','ffprobe')
        self.hardware=Path('/dev/dri/renderD128').is_char_device()
        for p in self.root.glob('*.part.mp4'):
            if re.fullmatch(r'[0-9a-f]{64}\.part\.mp4',p.name) and not p.is_symlink():p.unlink()

    def target(self, path):
        stat=path.stat()
        key=hashlib.sha256(f'compat-v1:{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()
        return self.root/(key+'.mp4')

    def ready(self, path):
        output=self.target(path)
        return output if output.is_file() and not output.is_symlink() and output.stat().st_size else None

    def streaming(self, path):
        output=self.target(path);part=output.with_suffix('.part.mp4')
        return part if output.stem in self.jobs and part.is_file() and not part.is_symlink() and part.stat().st_size>4096 else None

    async def stream(self, path):
        output=self.target(path);record=self.records.get(output.stem,{})
        record['clients']=record.get('clients',0)+1
        try:
            source=self.ready(path) or self.streaming(path)
            if source is None:return
            try:handle=source.open('rb')
            except FileNotFoundError:handle=output.open('rb')
            with handle:
                while True:
                    chunk=await asyncio.to_thread(handle.read,256*1024)
                    if chunk:yield chunk
                    elif output.stem in self.jobs:await asyncio.sleep(.1)
                    elif self.ready(path):break
                    else:raise RuntimeError('播放准备未完成，请重试；原文件已保留')
        finally:record['clients']-=1

    async def probe(self, path):
        proc=None
        try:
            proc=await asyncio.create_subprocess_exec(self.ffprobe,'-v','error','-protocol_whitelist','file,pipe',
                '-show_entries','stream=codec_type,codec_name,pix_fmt,width,height:format=duration','-of','json',str(path),
                stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            stdout,_=await asyncio.wait_for(proc.communicate(),30)
            if proc.returncode:raise RuntimeError('文件暂时无法读取，原文件已保留；请稍后重试播放')
            return json.loads(stdout)
        except FileNotFoundError as exc:
            raise RuntimeError('播放兼容组件尚未就绪，请更新本项目镜像后重试') from exc
        except asyncio.TimeoutError as exc:
            raise RuntimeError('读取这个视频超时，请稍后重试播放；原文件已保留') from exc
        finally:
            if proc and proc.returncode is None:
                proc.kill();await proc.wait()

    async def prepare(self, path, force=False, retry=False, warm=False):
        output=self.target(path);key=output.stem
        async with self.probe_lock:
            if not warm:
                cancelled=[(old,job) for old,job in self.jobs.items() if old!=key
                           and self.records[old].get('warm') and not self.records[old].get('clients')]
                for _,job in cancelled:job.cancel()
                await asyncio.gather(*(job for _,job in cancelled),return_exceptions=True)
                for old,job in cancelled:
                    if self.jobs.get(old) is job:self.jobs.pop(old)
            if self.ready(path):
                os.utime(output,None)
                return {'status':'ready','mode':'compatible'}
            if key not in self.records:
                info=await self.probe(path)
                self.records[key]={'compatible':needs_compatibility(path,info),'duration':duration(info),'progress':0}
                # shortcut: Keep only recent probes; scan the library only on explicit user request.
                if len(self.records)>512:
                    for old in list(self.records):
                        if old!=key and old not in self.jobs:
                            del self.records[old];break
            record=self.records[key]
            record['requested']=time.monotonic()
            if key not in self.jobs or not warm:record['warm']=warm
            if not force and not record['compatible']:return {'status':'ready','mode':'original'}
            if retry:record.pop('error',None)
            if record.get('error'):return {'status':'failed','message':record['error']}
            if self.streaming(path):return {'status':'ready','mode':'compatible','streaming':True}
            if key not in self.jobs:
                if len(self.jobs)>=3:return {'status':'waiting','progress':None}
                record['progress']=0;self.jobs[key]=asyncio.create_task(self.convert(path,output,record))
            return {'status':'preparing','progress':record['progress']}

    async def convert(self, path, output, record):
        temporary=output.with_suffix('.part.mp4')
        try:
            async with self.encode_lock:
                temporary.unlink(missing_ok=True)
                required=min(self.limit,2*1024**3,max(path.stat().st_size*4,64*1024**2))+2*1024**3
                if shutil.disk_usage(self.root).free<required:raise RuntimeError('播放缓存空间不足，请释放应用数据盘空间后重试；原文件已保留')
                await self.encode(path,temporary,record)
                if self.target(path)!=output:raise RuntimeError('原文件在准备期间发生变化，请重新打开作品')
                if not temporary.is_file() or not temporary.stat().st_size:raise RuntimeError('播放副本未完整生成，请重试')
                temporary.chmod(0o600)
                with temporary.open('rb+') as stream:os.fsync(stream.fileno())
                os.replace(temporary,output);self.prune(output)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            record['error']=str(exc) if isinstance(exc,RuntimeError) else '播放准备失败，请稍后重试；原文件已保留'
        finally:
            temporary.unlink(missing_ok=True);self.jobs.pop(output.stem,None)

    async def encode(self, path, temporary, record):
        hardware=self.hardware and not record.get('software')
        try:await self._encode(path,temporary,record,hardware)
        except (RuntimeError, asyncio.TimeoutError):
            if not hardware:raise
            record['software']=True
            if record.get('clients'):raise RuntimeError('核显转换暂时失败，请点重试播放；将改用软件兼容，原文件已保留')
            temporary.unlink(missing_ok=True);record['progress']=0
            await self._encode(path,temporary,record,False)

    async def _encode(self, path, temporary, record, hardware=False):
        proc=None
        try:
            device=['-hwaccel','vaapi','-hwaccel_device','/dev/dri/renderD128','-hwaccel_output_format','vaapi'] if hardware else []
            scale='w=min(1920\\,iw):h=min(1920\\,ih):force_original_aspect_ratio=decrease:force_divisible_by=2'
            video=['-vf','scale_vaapi='+scale+':format=nv12','-c:v','h264_vaapi','-qp','23'] if hardware else [
                '-vf','scale='+scale,'-c:v','libx264','-preset','ultrafast','-crf','23','-pix_fmt','yuv420p','-threads','2','-filter_threads','1']
            proc=await asyncio.create_subprocess_exec(self.ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-xerror',
                '-protocol_whitelist','file,pipe','-threads','2',*device,'-i',str(path),'-map','0:v:0','-map','0:a:0?',*video,
                '-g','30','-bf','0',
                '-c:a','aac','-b:a','128k','-map_metadata','-1','-movflags','+frag_keyframe+empty_moov+default_base_moof',
                '-frag_duration','500000','-flush_packets','1','-fs',str(min(self.limit,2*1024**3)),
                '-progress','pipe:1','-nostats','-y',str(temporary),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            async def progress():
                while line:=await proc.stdout.readline():
                    if not record.get('clients') and time.monotonic()-record['requested']>10:raise asyncio.CancelledError
                    if line.startswith(b'out_time_us=') and record['duration']:
                        value=line.strip().split(b'=',1)[1]
                        if value.isdigit():record['progress']=min(99,int(value)/(record['duration']*10000))
                await proc.wait()
            await asyncio.wait_for(progress(),max(60,min(1800,record['duration']*5)) if record['duration'] else 1800)
            if proc.returncode:raise RuntimeError('这个视频暂时无法转换，请重试播放；原文件已保留')
            info=await self.probe(temporary);output_duration=duration(info)
            if needs_compatibility(temporary,info) or output_duration<=0 or (record['duration'] and abs(output_duration-record['duration'])>max(.25,record['duration']*.01)):
                raise RuntimeError('播放副本完整性检查未通过，未替换原文件，请重试')
        except FileNotFoundError as exc:
            raise RuntimeError('播放兼容组件尚未就绪，请更新本项目镜像后重试') from exc
        finally:
            if proc and proc.returncode is None:
                proc.kill();await proc.wait()

    def prune(self, keep):
        copies=sorted((p for p in self.root.glob('*.mp4') if re.fullmatch(r'[0-9a-f]{64}\.mp4',p.name) and not p.is_symlink()),key=lambda p:p.stat().st_mtime)
        size=sum(p.stat().st_size for p in copies)
        for p in copies:
            if size<=self.limit:break
            if p!=keep:
                size-=p.stat().st_size;p.unlink()

    async def close(self):
        jobs=list(self.jobs.values())
        for job in jobs:job.cancel()
        await asyncio.gather(*jobs,return_exceptions=True)
