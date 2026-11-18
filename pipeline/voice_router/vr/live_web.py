"""Live voice test in the browser (Charlie 11-17: "stress test it with my voice now").

Same engine as vr.live (fill-in-the-blanks intake, keyword tree choice until the router is wired
in), shown as a small local web page that looks like the device screen:

  * PC mic: hold the TALK button (or the spacebar) and speak; the browser records, the server
    converts to 16 kHz with ffmpeg and decodes with the speech model.
  * Board mic: with --clips pointing at devrec's FREE folder, every clip the board records
    (hold TALK on the recorder firmware, `python -m fasr devrec` running) is decoded and shown.
  * Typing: a text box, same engine without speech.

Cards open with their cited text from the personal extracts. That text stays on this PC: the
server listens on 127.0.0.1 only. Every turn is logged (jsonl), PC-mic clips are kept as wav.

    python -m vr.live_web --trees TREES_DIR --slots SLOTS_DIR --fasr SPEECH_ROOT [--clips FREE_DIR]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .form import answer, first_sentence, open_fields, ranked_cards, split_tabs
from .live import Speech, _title, keep_trees, load_all

EXTRACTS = [Path.home() / "first-aid-trees/pipeline/plb/build/extract_personal",
            Path.home() / "first-aid-trees/pipeline/plb/build/extract"]
_SECTIONS: dict[str, dict] = {}


def _section(ref: str) -> str:
    """Text of one cited paragraph ("AERIE-WM15/109.3"), from the extract's sections.json."""
    manual = ref.split("/", 1)[0]
    if manual not in _SECTIONS:
        _SECTIONS[manual] = {}
        for root in EXTRACTS:
            f = root / manual / "sections.json"
            if f.exists():
                for s in json.loads(f.read_text()):
                    _SECTIONS[manual][s["id"]] = s.get("text", "")
                break
    return _SECTIONS[manual].get(ref, "")


def card_text(cites) -> list[dict]:
    out = []
    for c in cites or []:
        ref = c["ref"] if isinstance(c, dict) else str(c)
        txt = _section(ref)
        if isinstance(c, dict) and txt:
            a = txt.find(c.get("from", "")) if c.get("from") else 0
            b = txt.find(c.get("to", ""), max(a, 0)) if c.get("to") else -1
            if a >= 0:
                txt = txt[a:(b + len(c["to"])) if b >= 0 else None]
        out.append({"ref": ref, "text": txt or "(text not found in the extracts)"})
    return out


class Session:
    def __init__(self, trees, speech, log: Path, reject: int):
        self.trees, self.speech, self.log, self.reject = trees, speech, log, reject
        self.cands, self.multi, self.turn, self.lock = None, False, 0, threading.Lock()
        self.events: list[dict] = []

    def reset(self):
        self.cands, self.turn, self.multi = None, 0, False

    def _card(self, name, card):
        title, cites = _title(self.trees, name, card)
        return {"title": title, "id": f"{name}/{card}", "cites": card_text(cites)}

    def _form(self, cands):
        heard = {k: v for c in cands for k, v in c.filled.items()}
        fields = [{"question": f.question, "key": f.key,
                   "choices": [str(c.get("label", c.get("value"))) for c in f.choices],
                   "when": list(f.when) if f.when else None} for f in open_fields(cands)]
        return {"heard": heard, "fields": fields,
                "top3": [_title(self.trees, n, c)[0] for n, c in ranked_cards(cands, 3)]}

    def turn_text(self, hyps: list[str], reject: int, source: str, clip: str | None = None) -> dict:
        with self.lock:
            res = {"t": time.time(), "source": source, "hyps": hyps, "reject": reject, "clip": clip}
            words = (hyps[0] if hyps else "").strip().lower()
            if self.speech and source != "typed" and reject >= self.reject:
                res["screen"] = "again"
            elif words in ("new", "home", "back") or not words:
                self.reset(); res["screen"] = "new"
            elif words == "multi":
                self.multi = True; res["screen"] = "multi"
            else:
                self.turn += 1
                res["turn"] = self.turn
                if self.cands is None:
                    self.cands = keep_trees(hyps, self.trees)
                    res["kept"] = [[c.name, round(c.p, 2)] for c in self.cands]
                    if not self.cands:
                        self.reset(); res["screen"] = "start"
                        return self._done(res)
                    kind, name, card = first_sentence(hyps, self.cands, multi=self.multi)
                    self.multi = False
                else:
                    kind, name, card = answer(hyps[0], self.cands)
                res["screen"] = kind
                if kind == "open":
                    res["card"] = self._card(name, card); self.cands = None; self.turn = 0
                elif kind == "skip":
                    res["skip"] = [_title(self.trees, n, c)[0] for n, c in card[:6]]
                elif kind == "tabs":
                    res["tabs"] = [{"trees": [c.name for c in tab], **self._form(tab)} for tab in split_tabs(self.cands)[:5]]
                else:
                    res["form"] = self._form(self.cands)
            return self._done(res)

    def _done(self, res):
        self.events.append(res)
        with open(self.log, "a") as f:
            f.write(json.dumps({k: v for k, v in res.items() if k != "card"} | (
                {"card": res["card"]["id"]} if "card" in res else {})) + "\n")
        return res

    def turn_wav(self, wav: Path, source: str) -> dict:
        if not self.speech:
            return self._done({"t": time.time(), "source": source, "screen": "error",
                               "error": "no speech model loaded (start with --fasr)"})
        hyps, reject = self.speech(wav)
        return self.turn_text(hyps, reject, source, str(wav))


def watch_clips(sess: Session, folder: Path):
    seen = {p.name for p in folder.glob("*.wav")}
    while True:
        for p in sorted(q for q in folder.glob("*.wav") if q.name not in seen):
            time.sleep(0.3)
            seen.add(p.name)
            sess.turn_wav(p, "board")
        time.sleep(0.5)


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Voice intake test</title><style>
:root{--ink:#111;--paper:#f4f1ea;--dim:#666;--line:#bbb;--alert:#8a1c1c}
body{margin:0;background:#2b2b2b;color:var(--ink);font:16px/1.4 system-ui,sans-serif;display:flex;justify-content:center}
main{width:min(560px,100% - 32px);margin:16px 0}
.screen{background:var(--paper);border:3px solid #000;border-radius:6px;padding:16px;min-height:320px}
h2{margin:0 0 8px;font-size:18px} .dim{color:var(--dim);font-size:13px}
.field{border-top:1px solid var(--line);padding:8px 0} .chips span{display:inline-block;border:1px solid #000;border-radius:4px;padding:2px 8px;margin:3px 4px 0 0;font-size:14px}
.chips span b{margin-right:4px}.card h2{border-bottom:2px solid #000;padding-bottom:6px}.cite{margin:10px 0;white-space:pre-wrap}.cite .dim{display:block}
.alert{color:var(--alert);font-weight:600}
.controls{display:flex;gap:8px;margin:12px 0;flex-wrap:wrap}
button{font:inherit;padding:10px 14px;border-radius:6px;border:2px solid #000;background:#ddd;cursor:pointer}
#talk{flex:1;min-width:180px;background:#ffd54a;font-weight:700}#talk.rec{background:#e53935;color:#fff}
input[type=text]{flex:1;min-width:200px;font:inherit;padding:10px;border-radius:6px;border:2px solid #000}
.log{background:#1d1d1d;color:#ddd;border-radius:6px;padding:10px;font:13px/1.35 ui-monospace,monospace;max-height:220px;overflow:auto;white-space:pre-wrap}
</style></head><body><main>
<div class="screen" id="screen"><h2>Ready</h2><div class="dim">Hold TALK (or the spacebar) and describe the problem. Board clips show up here too.</div></div>
<div class="controls"><button id="talk">Hold to TALK</button><button id="multi" title="TALK + WIN_R: several problems">Multi (next)</button><button id="new">New problem</button></div>
<div class="controls"><input id="txt" type="text" placeholder="or type a sentence / answer, Enter to send"><button id="send">Send</button></div>
<div class="log" id="log"></div></main><script>
const S=document.getElementById('screen'),L=document.getElementById('log');let seen=0,rec=null,chunks=[],busy=false;
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function form(f){let h='';if(Object.keys(f.heard).length)h+=`<div class="dim">heard: ${esc(Object.entries(f.heard).map(([k,v])=>k+'='+v).join(', '))}</div>`;
 for(const x of f.fields)h+=`<div class="field"><b>${esc(x.question)}</b>${x.when?` <span class="dim">(after ${esc(x.when[0])}=${esc(x.when[1])})</span>`:''}<div class="chips">${x.choices.map((c,i)=>`<span><b>${i+1}</b>${esc(c)}</span>`).join('')}</div></div>`;
 if(f.top3&&f.top3.length)h+=`<div class="dim" style="margin-top:8px">SKIP would offer: ${esc(f.top3.join(' · '))}</div>`;return h}
function show(r){const heard=r.hyps?`"${esc(r.hyps[0])}"`:'';let h='';
 if(r.screen==='again')h=`<h2>Say again?</h2><div class="dim">heard ${heard} (reject ${r.reject})</div>`;
 else if(r.screen==='new')h=`<h2>New problem</h2>`;else if(r.screen==='multi')h=`<h2>Several problems</h2><div class="dim">next sentence opens one tab per problem</div>`;
 else if(r.screen==='start')h=`<h2>Injury | Survival</h2><div class="dim">no blanks heard in any tree for ${heard}</div>`;
 else if(r.screen==='open'){h=`<div class="card"><h2>${esc(r.card.title)}</h2>${r.card.cites.map(c=>`<div class="cite"><span class="dim">${esc(c.ref)}</span>${esc(c.text)}</div>`).join('')}<div class="dim">${esc(r.card.id)}</div></div>`}
 else if(r.screen==='skip')h=`<h2>Likely cards</h2>${r.skip.map((s,i)=>`<div class="field"><b>${i+1}</b> ${esc(s)}</div>`).join('')}`;
 else if(r.screen==='tabs')h=r.tabs.map((t,i)=>`<h2>Tab ${i+1}: ${esc(t.trees.join(', '))}</h2>${form(t)}`).join('');
 else if(r.screen==='form')h=`<h2>Fill in what's missing</h2>${form(r.form)}`;else h=`<h2>${esc(r.screen)}</h2><div class="alert">${esc(r.error||'')}</div>`;
 S.innerHTML=h;const k=r.kept?'  kept: '+r.kept.map(x=>x[0]+' '+Math.round(x[1]*100)+'%').join(', '):'';
 L.textContent=`[${r.source}] ${r.hyps?r.hyps[0]:''}  (reject ${r.reject??'-'})${k}  -> ${r.screen}${r.card?' '+r.card.id:''}\n`+L.textContent}
async function post(u,body,type){const res=await fetch(u,{method:'POST',body,headers:type?{'Content-Type':type}:{}});return res.json()}
async function poll(){try{const r=await (await fetch('/events?since='+seen)).json();for(const e of r.events){seen++;show(e)}}catch(e){}setTimeout(poll,800)}poll();
async function start(){if(rec||busy)return;try{const st=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:false,noiseSuppression:false,autoGainControl:true}});
 chunks=[];rec=new MediaRecorder(st);rec.ondataavailable=e=>chunks.push(e.data);rec.onstop=async()=>{st.getTracks().forEach(t=>t.stop());busy=true;
 S.innerHTML='<h2>Listening done…</h2><div class="dim">decoding</div>';await post('/audio',new Blob(chunks,{type:rec.mimeType}),rec.mimeType);rec=null;busy=false};
 rec.start();document.getElementById('talk').classList.add('rec')}catch(e){S.innerHTML='<h2>No mic</h2><div class="alert">'+esc(e)+'</div>'}}
function stop(){if(rec&&rec.state==='recording'){rec.stop();document.getElementById('talk').classList.remove('rec')}}
const T=document.getElementById('talk');T.onmousedown=start;T.onmouseup=stop;T.onmouseleave=stop;T.ontouchstart=e=>{e.preventDefault();start()};T.ontouchend=stop;
document.addEventListener('keydown',e=>{if(e.code==='Space'&&document.activeElement.id!=='txt'){e.preventDefault();if(!e.repeat)start()}});
document.addEventListener('keyup',e=>{if(e.code==='Space'&&document.activeElement.id!=='txt'){e.preventDefault();stop()}});
const sendTxt=()=>{const v=document.getElementById('txt').value.trim();if(v){post('/text',JSON.stringify({text:v}),'application/json');document.getElementById('txt').value=''}};
document.getElementById('send').onclick=sendTxt;document.getElementById('txt').onkeydown=e=>{if(e.key==='Enter')sendTxt()};
document.getElementById('new').onclick=()=>post('/text',JSON.stringify({text:'new'}),'application/json');
document.getElementById('multi').onclick=()=>post('/text',JSON.stringify({text:'multi'}),'application/json');
</script></body></html>"""


def make_handler(sess: Session, rec_dir: Path):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, obj, code=200):
            b = json.dumps(obj).encode()
            self.send_response(code); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def do_GET(self):
            if self.path.startswith("/events"):
                since = int(self.path.split("since=")[-1]) if "since=" in self.path else 0
                return self._json({"events": sess.events[since:]})
            b = PAGE.encode()
            self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            try:
                if self.path == "/text":
                    t = json.loads(body or b"{}").get("text", "")
                    return self._json(sess.turn_text([t], 0, "typed"))
                if self.path == "/audio":
                    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
                    raw, wav = rec_dir / f"pc-{stamp}.webm", rec_dir / f"pc-{stamp}.wav"
                    raw.write_bytes(body)
                    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(raw), "-ac", "1", "-ar", "16000",
                                    "-sample_fmt", "s16", str(wav)], check=True)
                    raw.unlink(missing_ok=True)
                    return self._json(sess.turn_wav(wav, "pc-mic"))
            except Exception as e:                       # show the error on the page, keep serving
                return self._json(sess._done({"t": time.time(), "source": "server", "screen": "error", "error": repr(e)}))
            self._json({"error": "unknown path"}, 404)
    return H


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vr.live_web")
    ap.add_argument("--trees", type=Path, nargs="+", required=True)
    ap.add_argument("--slots", type=Path, required=True)
    ap.add_argument("--fasr", type=Path, help="speech package root (holds fasr/); omit = typing only")
    ap.add_argument("--run", default="firstaid")
    ap.add_argument("--clips", type=Path, help="devrec FREE folder to watch for board-mic clips")
    ap.add_argument("--out", type=Path, default=Path("/mnt/data/first-aid-gpu/speech/me_dev/live"),
                    help="where PC-mic clips and the turn log go (outside any repo)")
    ap.add_argument("--reject", type=int, default=70)
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    trees = load_all(a.trees, a.slots)
    speech = Speech(a.fasr, a.run) if a.fasr else None
    rec_dir = a.out / dt.date.today().isoformat()
    rec_dir.mkdir(parents=True, exist_ok=True)
    sess = Session(trees, speech, rec_dir / "turns.jsonl", a.reject)
    if a.clips:
        threading.Thread(target=watch_clips, args=(sess, a.clips), daemon=True).start()
    print(f"{len(trees)} trees loaded; speech: {'model ' + a.run if speech else 'off (typing only)'}; "
          f"board clips: {a.clips or 'off'}; log + PC clips: {rec_dir}")
    print(f"open http://127.0.0.1:{a.port}/  (Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(sess, rec_dir)).serve_forever()


if __name__ == "__main__":
    main()
