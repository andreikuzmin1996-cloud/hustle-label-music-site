import os, re, json, time, uuid, shutil, zipfile, pathlib, threading, subprocess, textwrap, logging, math
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from groq import Groq
from PIL import Image, ImageDraw, ImageFont
from yt_dlp import YoutubeDL

ROOT = pathlib.Path(os.environ.get("WORK_ROOT", "/tmp/kinomoment-jobs"))
ROOT.mkdir(parents=True, exist_ok=True)
STATIC = pathlib.Path(__file__).parent / "static"
MAX_BYTES = int(os.environ.get("MAX_UPLOAD_MB", "1200")) * 1024 * 1024
MAX_DURATION = int(os.environ.get("MAX_VIDEO_MINUTES", "150")) * 60
TTL = 6 * 3600
STUDIO_PASSWORD = os.environ.get("STUDIO_PASSWORD", "").strip()
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "").strip()
GROQ_MODEL = os.environ.get("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
executor = ThreadPoolExecutor(max_workers=1)
jobs = {}
lock = threading.Lock()
app = FastAPI(title="KINOMOMENT STUDIO")
logging.basicConfig(level=logging.INFO)

def auth(key: str | None):
    if not STUDIO_PASSWORD:
        raise HTTPException(503, "Администратор ещё не настроил STUDIO_PASSWORD на сервере")
    import hmac
    if not key or not hmac.compare_digest(key, STUDIO_PASSWORD):
        raise HTTPException(401, "Неверный пароль студии")

def save_status(j, stage, progress=None):
    with lock:
        j["stage"] = stage
        if progress is not None:
            j["progress"] = progress
        j["updated"] = time.time()

def run(cmd, timeout=1800):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", timeout=timeout)
    if p.returncode:
        raise RuntimeError("Ошибка FFmpeg: " + p.stderr[-1200:])
    return p.stdout

def duration_of(file):
    info = json.loads(run(["ffprobe","-v","error","-show_entries","format=duration","-of","json",str(file)], 90))
    return float(info["format"]["duration"])

def cleanup():
    now = time.time()
    with lock:
        obsolete = [k for k,v in jobs.items() if v["stage"] in ("ready", "error") and now - v["updated"] > TTL]
        for k in obsolete:
            v = jobs.pop(k)
            shutil.rmtree(v["folder"], ignore_errors=True)

def new_job(opts):
    cleanup()
    with lock:
        if len([v for v in jobs.values() if v["stage"] not in ("ready","error")]) >= 2:
            raise HTTPException(429, "Слишком много задач. Дождитесь завершения обработки")
        jid = uuid.uuid4().hex
        folder = ROOT / jid
        folder.mkdir()
        job = dict(id=jid, stage="queued", progress=0, error=None, files=[], clips=[], folder=str(folder), created=time.time(),updated=time.time(),options=opts)
        jobs[jid] = job
    return job

def expose(j):
    return {k:v for k,v in j.items() if k not in ("folder","options")}

@app.get("/", response_class=HTMLResponse)
def homepage():
    return (STATIC / "index.html").read_text("utf-8")

@app.get("/health")
def health():
    return {"ok":True,"configured":bool(STUDIO_PASSWORD),"ai":bool(GROQ_API_KEY)}

@app.post("/api/upload")
async def upload(request: Request, video: UploadFile = File(...), count:int=Form(5), mode:str=Form("blur"), captions:bool=Form(True), rights:bool=Form(False), x_studio_key:str|None=Header(None)):
    auth(x_studio_key)
    if not rights: raise HTTPException(400,"Подтвердите права на использование видеоматериала")
    if not video.filename or not re.search(r"\.(mp4|mov|mkv|webm|avi)$",video.filename,re.I):
        raise HTTPException(400,"Поддерживаются MP4, MOV, MKV, WEBM, AVI")
    opts={"count":max(1,min(int(count),12)),"mode":mode if mode in ("blur","crop") else "blur","captions":captions}
    j=new_job(opts)
    target=pathlib.Path(j["folder"])/("source"+pathlib.Path(video.filename).suffix.lower())
    total=0
    try:
        with open(target,"wb") as out:
            while True:
                chunk=await video.read(1024*1024)
                if not chunk: break
                total+=len(chunk)
                if total>MAX_BYTES: raise ValueError("Слишком большой файл: лимит "+str(MAX_BYTES//1024//1024)+" МБ")
                out.write(chunk)
        executor.submit(process,j,target)
        return {"id":j["id"]}
    except Exception as e:
        shutil.rmtree(j["folder"],ignore_errors=True)
        with lock: jobs.pop(j["id"],None)
        raise HTTPException(413,str(e))

@app.post("/api/link")
async def by_link(request:Request,x_studio_key:str|None=Header(None)):
    auth(x_studio_key)
    data=await request.json()
    if not data.get("rights"): raise HTTPException(400,"Подтвердите права на использование видео")
    url=str(data.get("url","")).strip()
    u=urlparse(url)
    if u.scheme!="https" or (u.hostname or "").lower() not in ("youtube.com","www.youtube.com","m.youtube.com","youtu.be","www.youtu.be"):
        raise HTTPException(400,"Для импорта по ссылке сейчас поддерживается только YouTube (при наличии прав)")
    opts={"count":max(1,min(int(data.get("count",5)),12)),"mode":data.get("mode") if data.get("mode") in ("blur","crop") else "blur","captions":bool(data.get("captions",True))}
    j=new_job(opts)
    executor.submit(process_link,j,url)
    return {"id":j["id"]}

@app.get("/api/jobs/{jid}")
def get_job(jid:str,x_studio_key:str|None=Header(None)):
    auth(x_studio_key)
    with lock:
        j=jobs.get(jid)
        if not j: raise HTTPException(404,"Задача не найдена или файлы очищены")
        return expose(j)

@app.get("/api/jobs/{jid}/download/{filename}")
def download(jid:str,filename:str,x_studio_key:str|None=Header(None),key:str|None=None):
    auth(x_studio_key or key)
    with lock:
        j=jobs.get(jid)
    if not j or j["stage"]!="ready": raise HTTPException(404,"Файл пока недоступен")
    if filename not in j["files"]: raise HTTPException(404,"Файл не найден")
    return FileResponse(pathlib.Path(j["folder"])/filename,filename=filename)

def process_link(j,url):
    try:
        save_status(j,"downloading",4)
        folder=pathlib.Path(j["folder"])
        config={
            "outtmpl":str(folder/"source.%(ext)s"),
            "format":"bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
            "merge_output_format":"mp4",
            "noplaylist":True,
            "no_warnings":True,
            "quiet":True,
            "max_filesize":MAX_BYTES,
            "socket_timeout":20,
            "retries":2,
        }
        with YoutubeDL(config) as ydl:
            info=ydl.extract_info(url,download=False)
            if (info.get("duration") or 0)>MAX_DURATION: raise ValueError("Видео длиннее лимита "+str(MAX_DURATION//60)+" минут")
            if info.get("is_live"): raise ValueError("Прямые эфиры не поддерживаются")
            ydl.download([url])
        candidates=[p for p in folder.glob("source.*") if p.is_file()]
        if not candidates: raise ValueError("Не удалось получить видео. YouTube может ограничивать загрузки")
        source=max(candidates,key=lambda p:p.stat().st_size)
        if source.stat().st_size>MAX_BYTES: raise ValueError("Видео превышает лимит размера")
        process(j,source)
    except Exception as e:
        logging.exception("Download failed")
        j["error"]=str(e)[:900]
        save_status(j,"error",0)

def transcribe(source,folder,duration):
    key=GROQ_API_KEY
    if not key: return []
    client=Groq(api_key=key,timeout=180)
    chunks=[]
    parts=int(math.ceil(duration/420))
    for i in range(parts):
        start=i*420
        secs=min(420,duration-start)
        audio=folder/f"audio_{i}.mp3"
        run(["ffmpeg","-y","-hide_banner","-loglevel","error","-ss",str(start),"-t",str(secs),"-i",str(source),"-vn","-ac","1","-ar","16000","-b:a","48k",str(audio)],180)
        try:
            with open(audio,"rb") as stream:
                t=client.audio.transcriptions.create(file=stream,model="whisper-large-v3-turbo",response_format="verbose_json",timestamp_granularities=["segment"])
            for s in t.segments or []:
                seg = s if isinstance(s,dict) else s.model_dump()
                text=str(seg.get("text","")).strip()
                if text:
                    chunks.append({"start":float(seg["start"])+start,"end":float(seg["end"])+start,"text":text})
        finally:
            audio.unlink(missing_ok=True)
    return chunks

def highlights(segments,duration,count):
    if not segments:
        step=max(30,min(180,duration/max(1,count)))
        return [{"start":round(min(duration-3,i*step),1),"end":round(min(duration,i*step+42),1),"title":"Интересный момент","description":"Сцена из видео — смотри до конца.","hashtags":["#кино","#shorts"]} for i in range(count) if i*step<duration-3]
    candidates=[]
    if GROQ_API_KEY:
        try:
            client=Groq(api_key=GROQ_API_KEY,timeout=120)
            # Divide long videos to avoid oversized prompts and missing latter portions.
            windows=max(1,math.ceil(duration/1200))
            for w in range(windows):
                selected=[s for s in segments if w*1200<=s["start"]<(w+1)*1200]
                compact="\n".join(f'{int(s["start"])}|{s["text"][:170]}' for s in selected)
                if not compact:continue
                prompt=f"""Ниже расшифровка видео (время в секундах). Найди до {min(count+2,8)} действительно интересных САМОСТОЯТЕЛЬНЫХ фрагментов для коротких видео 25-55 секунд: напряжение, юмор, конфликт, неожиданный вывод. Не выбирай случайные куски. Верни ТОЛЬКО JSON объект вида {{"clips":[{{"start":123,"end":165,"title":"сильный заголовок до 65 символов","description":"интересное описание для Shorts","hashtags":["#кино","#shorts"]}}]}}. Времена должны быть в пределах стенограммы, описание и заголовок на русском, без выдумывания событий. Содержание:\n{compact[:42000]}"""
                res=client.chat.completions.create(model=GROQ_MODEL,messages=[{"role":"system","content":"Ты профессиональный редактор Shorts. Отвечай строго валидным JSON, основывайся только на тексте."},{"role":"user","content":prompt}],temperature=0.3,response_format={"type":"json_object"})
                raw=json.loads(res.choices[0].message.content)
                candidates.extend(raw.get("clips",[]))
        except Exception:
            logging.exception("AI highlights failed, falling back")
    if not candidates:
        # Rank dialogue chunks by salient expression and punctuation instead of arbitrary fixed windows.
        for s in segments:
            snippet=s["text"]
            score=(4 if any(x in snippet.lower() for x in ("но ","вдруг","почему","никогда","зачем","сейчас","правда","серьёзно")) else 0) + 2*snippet.count("?")+2*snippet.count("!")+min(len(snippet)/40,3)
            candidates.append({"start":max(0,s["start"]-5),"end":min(duration,s["start"]+40),"title":snippet[:64].strip() or "Момент из видео","description":"Интересная сцена из видео.","hashtags":["#кино","#shorts"],"_score":score})
        candidates.sort(key=lambda c:c.get("_score",0),reverse=True)
    final=[]
    for c in candidates:
        try:
            a=max(0,float(c["start"]));b=min(duration,float(c["end"]))
        except Exception:continue
        if b-a<12: b=min(duration,a+35)
        if b-a>59:b=a+59
        if b-a<12 or any(max(0,min(b,d["end"])-max(a,d["start"]))>8 for d in final):continue
        title=str(c.get("title") or "Момент из видео").strip()[:100]
        final.append({"start":round(a,2),"end":round(b,2),"title":title,"description":str(c.get("description") or title)[:260],"hashtags":[str(x)[:40] for x in (c.get("hashtags") or ["#shorts","#кино"])[:8]]})
        if len(final)>=count:break
    return sorted(final,key=lambda c:c["start"])

def ftime(t):
    ms=max(0,round(t*1000))
    return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"

def write_srt(segments,clip,path):
    srt=[];i=1
    for s in segments:
        a=max(float(s["start"]),clip["start"]);b=min(float(s["end"]),clip["end"])
        if b-a<0.15:continue
        rel_a=a-clip["start"]; rel_b=b-clip["start"]
        line=textwrap.wrap(s["text"],36,break_long_words=False)
        srt.append(f'{i}\n{ftime(rel_a)} --> {ftime(rel_b)}\n'+ "\n".join(line[:3])+"\n")
        i+=1
    path.write_text("\n".join(srt),encoding="utf-8")
    return bool(srt)

def title_png(title,path):
    w,h=900,240
    im=Image.new("RGBA",(w,h),(0,0,0,0))
    d=ImageDraw.Draw(im)
    fontpath="/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    f=ImageFont.truetype(fontpath,43)
    words=title.upper().split()
    lines=[];line=""
    for word in words:
        guess=(line+" "+word).strip()
        if d.textbbox((0,0),guess,font=f)[2]>w-85 and line:
            lines.append(line);line=word
        else:line=guess
    if line:lines.append(line)
    lines=lines[:3]
    for i,line in enumerate(lines):
        y=20+i*67
        d.text((w/2,y),line,anchor="mt",font=f,fill="white",stroke_width=6,stroke_fill=(6,7,14,240))
    im.save(path)

def process(j,source):
    folder=pathlib.Path(j["folder"])
    try:
        save_status(j,"probing",9)
        dur=duration_of(source)
        if not 15<dur<=MAX_DURATION: raise ValueError("Длительность должна быть от 15 секунд до "+str(MAX_DURATION//60)+" минут")
        save_status(j,"transcribing",15)
        segments=transcribe(source,folder,dur)
        save_status(j,"selecting",45)
        clips=highlights(segments,dur,j["options"]["count"])
        if not clips:raise ValueError("Не удалось выбрать подходящие фрагменты")
        j["clips"]=clips
        for i,clip in enumerate(clips):
            save_status(j,"rendering",50+round(i/len(clips)*45))
            name=f"kinomoment_{i+1:02}.mp4"
            out=folder/name
            tpng=folder/f"heading_{i}.png"
            title_png(clip["title"],tpng)
            srt=folder/f"subtitle_{i}.srt"
            has_captions=bool(j["options"]["captions"] and segments and write_srt(segments,clip,srt))
            # Video stream: vertical 720x1280; blur sidefill or centered crop.
            if j["options"]["mode"]=="crop":
                vf="[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1[base]"
            else:
                vf="[0:v]split=2[bg][fg];[bg]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,boxblur=20:5[blur];[fg]scale=720:1280:force_original_aspect_ratio=decrease[front];[blur][front]overlay=(W-w)/2:(H-h)/2,setsar=1[base]"
            if has_captions:
                # subtitles filter reads filesystem path and uses fontconfig from Docker image
                vf += f";[base]subtitles={srt.as_posix()}:force_style='FontName=DejaVu Sans,FontSize=19,Outline=2,Alignment=2,MarginV=170'[sub]"
                base="[sub]"
            else:base="[base]"
            vf += f";{base}[1:v]overlay=(W-w)/2:75:format=auto[vout]"
            cmd=["ffmpeg","-y","-hide_banner","-loglevel","error","-ss",str(clip["start"]),"-t",str(clip["end"]-clip["start"]),"-i",str(source),"-loop","1","-i",str(tpng),"-filter_complex",vf,"-map","[vout]","-map","0:a?","-c:v","libx264","-preset","veryfast","-crf","25","-pix_fmt","yuv420p","-c:a","aac","-b:a","128k","-movflags","+faststart","-shortest",str(out)]
            run(cmd,900)
            j["files"].append(name)
            clip["file"]=name
            clip["srt"]=srt.name if has_captions else None
            if has_captions:j["files"].append(srt.name)
        manifest=folder/"descriptions.json"
        manifest.write_text(json.dumps(clips,ensure_ascii=False,indent=2),encoding="utf-8")
        j["files"].append(manifest.name)
        zipname="kinomoment_shorts.zip"
        with zipfile.ZipFile(folder/zipname,"w",compression=zipfile.ZIP_STORED) as z:
            for p in j["files"]:
                z.write(folder/p,p)
        j["files"].insert(0,zipname)
        save_status(j,"ready",100)
    except Exception as e:
        logging.exception("Job failed")
        j["error"]=str(e)[:900]
        save_status(j,"error",0)
