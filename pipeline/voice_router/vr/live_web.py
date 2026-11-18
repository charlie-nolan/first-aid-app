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
        [--scenarios me_dev/scenarios/batch1.yaml]

Scenario mode (--scenarios): the page shows one imagined situation at a time (gold hidden); you say
what you would say, answer the form as usual, and each turn is logged with the scenario id and its
gold card. When a card opens it is scored (right card? which turn? life card on the first
sentence?) and only then is the gold shown. Next / Skip / Unclear move on; progress is kept.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .form import answer, first_sentence, open_fields, ranked_cards, split_tabs
from .form import _life_cards, Candidate
from .live import Speech, _title, keep_trees, load_all, rescue

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
        self.scen, self.order, self.pos, self.prog, self.done_scen = [], [], 0, None, {}
        self.life = {f"{n}/{g}" if "/" not in g else g for n, (t, sm) in trees.items()
                     for g in _life_cards(Candidate(n, t, sm))}

    def load_scenarios(self, path: Path, prog: Path):
        import yaml
        self.scen = (yaml.safe_load(path.read_text()) or {}).get("scenarios", [])
        self.prog, self.scen_file = prog, path
        st = json.loads(prog.read_text()) if prog.exists() else {}
        if st.get("n") == len(self.scen) and st.get("file") == str(path):
            self.order, self.pos, self.done_scen = st["order"], st["pos"], st.get("done", {})
        else:
            self.order = list(range(len(self.scen)))
            random.Random(11).shuffle(self.order)
            self.pos, self.done_scen = 0, {}
        self._save()

    def _save(self):
        if self.prog:
            self.prog.write_text(json.dumps({"file": str(self.scen_file),
                                             "n": len(self.scen), "order": self.order, "pos": self.pos,
                                             "done": self.done_scen}))

    def scenario(self) -> dict | None:
        if not self.scen:
            return None
        if self.pos >= len(self.order):
            return {"finished": True, "n": len(self.order), "tally": self.tally()}
        s = self.scen[self.order[self.pos]]
        return {"i": self.pos + 1, "n": len(self.order), "id": s["id"], "scene": s["scene"],
                "if_asked": s.get("if_asked"), "multi": bool(s.get("multi")), "tally": self.tally()}

    def tally(self) -> dict:
        v = list(self.done_scen.values())
        scored = [x for x in v if x.get("result") in ("right", "wrong")]
        return {"done": len(v), "right": sum(x["result"] == "right" for x in v), "scored": len(scored),
                "turn1": sum(x["result"] == "right" and x.get("turn") == 1 for x in v),
                "turn2": sum(x["result"] == "right" and (x.get("turn") or 9) <= 2 for x in v),
                "skipped": sum(x["result"] in ("skip", "unclear") for x in v)}

    def _gold(self):
        if not self.scen or self.pos >= len(self.order):
            return None
        s = self.scen[self.order[self.pos]]
        g = f"{s['gold_tree']}/{s['gold_card']}" if s.get("gold_tree") else None
        return s, g, {g, *s.get("also", [])} - {None}

    def scen_action(self, action: str) -> dict:
        with self.lock:
            cur = self._gold()
            if cur and action in ("skip", "unclear") and cur[0]["id"] not in self.done_scen:
                self.done_scen[cur[0]["id"]] = {"result": action}
            if cur:
                rec = {"t": time.time(), "source": "scenario", "screen": "scenario-" + action, "scenario": cur[0]["id"],
                       "gold": cur[1]}
                with open(self.log, "a") as f:
                    f.write(json.dumps(rec) + "\n")
            self.pos += 1
            self.reset()
            self._save()
            return {"scenario": self.scenario()}

    def _score(self, res):
        cur = self._gold()
        if not cur:
            return
        s, g, golds = cur
        res["scenario"], res["gold"] = s["id"], g
        if s["id"] in self.done_scen and self.done_scen[s["id"]].get("result") in ("right", "wrong"):
            return
        verdict = None
        if res["screen"] == "open":
            verdict = "right" if res["card"]["id"] in golds else "wrong"
        elif res["screen"] == "start" and g is None:
            verdict = "right"
        elif res["screen"] in ("form", "skip", "tabs") and g is None:
            verdict = "wrong"
        if verdict:
            first = res.get("turn") == 1
            self.done_scen[s["id"]] = {"result": verdict, "turn": res.get("turn"), "card": res.get("card", {}).get("id"),
                                       "gold": g, "life_first": bool(first and res.get("card", {}).get("id") in self.life)}
            res["verdict"] = {**self.done_scen[s["id"]], "gold_title": _title(self.trees, *g.split("/", 1))[0] if g else "no card (off topic)"}
            self._save()

    def reset(self):
        self.cands, self.turn, self.multi = None, 0, False

    def _card(self, name, card):
        title, cites = _title(self.trees, name, card)
        cid = card if (card not in self.trees[name][0]["nodes"] and "/" in card) else f"{name}/{card}"
        return {"title": title, "id": cid, "cites": card_text(cites)}

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
            if self.speech and source != "typed" and reject > self.reject:
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
                        self._score(res)
                        return self._done(res)
                    kind, name, card = first_sentence(hyps, self.cands, multi=self.multi)
                    self.multi = False
                else:
                    r = rescue(hyps, self.trees, self.cands)   # life threat / new problem mid-form
                    kind, name, card = r if r else answer(hyps[0], self.cands)
                res["screen"] = kind
                if kind == "open":
                    res["card"] = self._card(name, card); self.cands = None
                elif kind == "skip":
                    res["skip"] = [_title(self.trees, n, c)[0] for n, c in card[:6]]
                elif kind == "tabs":
                    res["tabs"] = [{"trees": [c.name for c in tab], **self._form(tab)} for tab in split_tabs(self.cands)[:5]]
                else:
                    res["form"] = self._form(self.cands)
            if res.get("screen") not in ("again", "new", "multi"):
                self._score(res)
            if res.get("screen") == "open":
                self.turn = 0
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
.scen{background:#fffbe6;border:3px solid #c9a400;border-radius:6px;padding:14px;margin-bottom:12px}#stext{font-size:20px;line-height:1.45;margin:6px 0}
.verdict{margin-top:10px;padding:8px;border-radius:4px;font-weight:600}.ok{background:#d9f2dc}.bad{background:#f8d7d7}
.log{background:#1d1d1d;color:#ddd;border-radius:6px;padding:10px;font:13px/1.35 ui-monospace,monospace;max-height:220px;overflow:auto;white-space:pre-wrap}
</style></head><body><main>
<div class="scen" id="scen" hidden><div class="dim" id="scount"></div><div id="stext"></div>
<details id="sask" hidden><summary class="dim">facts, if the form asks</summary><div id="sasktext"></div></details>
<div class="controls"><button id="snext">Next scenario</button><button id="sskip">Skip</button><button id="sunclear">Unclear</button><span class="dim" id="stally"></span></div></div>
<div class="screen" id="screen"><h2>Ready</h2><div class="dim">Hold TALK (or the spacebar) and describe the problem. Board clips show up here too.</div></div>
<div class="controls"><button id="talk">Hold to TALK</button><button id="multi" title="TALK + WIN_R: several problems">Multi (next)</button><button id="new">New problem</button></div>
<div class="controls"><input id="txt" type="text" placeholder="or type a sentence / answer, Enter to send"><button id="send">Send</button></div>
<div class="log" id="log"></div></main><script>
const S=document.getElementById('screen'),L=document.getElementById('log');let seen=0,rec=null,chunks=[],busy=false;
const esc=s=>String(s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function form(f){let h='';if(Object.keys(f.heard).length)h+=`<div class="dim">heard: ${esc(Object.entries(f.heard).map(([k,v])=>k+'='+v).join(', '))}</div>`;
 for(const x of f.fields)h+=`<div class="field"><b>${esc(x.question)}</b>${x.when?` <span class="dim">(after ${esc(x.when[0])}=${esc(x.when[1])})</span>`:''}<div class="chips">${x.choices.map((c,i)=>`<span><b>${i+1}</b>${esc(c)}</span>`).join('')}</div></div>`;
 if(f.top3&&f.top3.length)h+=`<div class="dim" style="margin-top:8px">SKIP would offer: ${esc(f.top3.join(' · '))}</div>`;return h}
function verdict(r){if(!r.verdict)return '';const v=r.verdict;return `<div class="verdict ${v.result==='right'?'ok':'bad'}">${v.result==='right'?'Right card':'Not the expected card'}${v.turn?' on turn '+v.turn:''}${v.life_first?' (life card on first sentence)':''}<br><span class="dim">expected: ${esc(v.gold_title)}</span></div>`}
async function scen(){const r=await (await fetch('/scenario')).json();const s=r.scenario,P=document.getElementById('scen');if(!s){P.hidden=true;return}P.hidden=false;
 if(s.finished){document.getElementById('scount').textContent='All '+s.n+' scenarios done';document.getElementById('stext').textContent='Thanks! That was the whole batch.';}
 else{document.getElementById('scount').textContent=`Scenario ${s.i} / ${s.n}`+(s.multi?'  (several problems: use Multi first)':'');document.getElementById('stext').textContent=s.scene;
 const a=document.getElementById('sask');a.hidden=!s.if_asked;a.open=false;document.getElementById('sasktext').textContent=s.if_asked||''}
 const t=s.tally;document.getElementById('stally').textContent=t?` right ${t.right}/${t.scored} · turn1 ${t.turn1} · by turn2 ${t.turn2} · skipped ${t.skipped}`:''}
async function sact(a){await post('/scenario',JSON.stringify({action:a}),'application/json');S.innerHTML='<h2>Ready</h2><div class="dim">Hold TALK and say what you would say.</div>';scen()}
function show(r){const heard=r.hyps?`"${esc(r.hyps[0])}"`:'';let h='';
 if(r.screen==='again')h=`<h2>Say again?</h2><div class="dim">heard ${heard} (reject ${r.reject})</div>`;
 else if(r.screen==='new')h=`<h2>New problem</h2>`;else if(r.screen==='multi')h=`<h2>Several problems</h2><div class="dim">next sentence opens one tab per problem</div>`;
 else if(r.screen==='start')h=`<h2>Injury | Survival</h2><div class="dim">no blanks heard in any tree for ${heard}</div>${verdict(r)}`;
 else if(r.screen==='open'){h=`<div class="card"><h2>${esc(r.card.title)}</h2>${r.card.cites.map(c=>`<div class="cite"><span class="dim">${esc(c.ref)}</span>${esc(c.text)}</div>`).join('')}<div class="dim">${esc(r.card.id)}</div></div>${verdict(r)}`}
 else if(r.screen==='skip')h=`<h2>Likely cards</h2>${r.skip.map((s,i)=>`<div class="field"><b>${i+1}</b> ${esc(s)}</div>`).join('')}`;
 else if(r.screen==='tabs')h=r.tabs.map((t,i)=>`<h2>Tab ${i+1}: ${esc(t.trees.join(', '))}</h2>${form(t)}`).join('');
 else if(r.screen==='form')h=`<h2>Fill in what's missing</h2>${form(r.form)}${verdict(r)}`;else h=`<h2>${esc(r.screen)}</h2><div class="alert">${esc(r.error||'')}</div>`;
 S.innerHTML=h;const k=r.kept?'  kept: '+r.kept.map(x=>x[0]+' '+Math.round(x[1]*100)+'%').join(', '):'';
 L.textContent=`[${r.source}] ${r.hyps?r.hyps[0]:''}  (reject ${r.reject??'-'})${k}  -> ${r.screen}${r.card?' '+r.card.id:''}\n`+L.textContent}
async function post(u,body,type){const res=await fetch(u,{method:'POST',body,headers:type?{'Content-Type':type}:{}});return res.json()}
document.getElementById('snext').onclick=()=>sact('next');document.getElementById('sskip').onclick=()=>sact('skip');document.getElementById('sunclear').onclick=()=>sact('unclear');scen();
async function poll(){try{const r=await (await fetch('/events?since='+seen)).json();for(const e of r.events){seen++;show(e);if(e.verdict)scen()}}catch(e){}setTimeout(poll,800)}poll();
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
            if self.path.startswith("/scenario"):
                return self._json({"scenario": sess.scenario()})
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
                if self.path == "/scenario":
                    return self._json(sess.scen_action(json.loads(body or b"{}").get("action", "next")))
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
    ap.add_argument("--reject", type=int, default=50, help="calibrated reject above which to ask again")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--scenarios", type=Path, help="scenario YAML for Scenario mode (gold hidden on the page)")
    a = ap.parse_args(argv)
    trees = load_all(a.trees, a.slots)
    speech = Speech(a.fasr, a.run) if a.fasr else None
    rec_dir = a.out / dt.date.today().isoformat()
    rec_dir.mkdir(parents=True, exist_ok=True)
    sess = Session(trees, speech, rec_dir / "turns.jsonl", a.reject)
    if a.scenarios:
        sess.load_scenarios(a.scenarios, a.out / f"scenarios-progress-{a.scenarios.stem}.json")
        print(f"scenario mode: {len(sess.scen)} scenarios from {a.scenarios}")
    if a.clips:
        threading.Thread(target=watch_clips, args=(sess, a.clips), daemon=True).start()
    print(f"{len(trees)} trees loaded; speech: {'model ' + a.run if speech else 'off (typing only)'}; "
          f"board clips: {a.clips or 'off'}; log + PC clips: {rec_dir}")
    print(f"open http://127.0.0.1:{a.port}/  (Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(sess, rec_dir)).serve_forever()


if __name__ == "__main__":
    main()
