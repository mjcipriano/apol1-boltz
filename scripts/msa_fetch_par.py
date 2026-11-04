
"""Fetch unpaired MSAs from the ColabFold MMseqs2 API for every sequence in unique_seqs.fasta (stdlib only).
Writes out/msa/<key>.a3m (uniref + environmental hits, colabfold 'env' mode) and out/msa_status.json.
Protocol follows colabfold.colabfold.run_mmseqs2 (Mirdita et al. 2022)."""
import os, sys, time, json, tarfile, io, urllib.request, urllib.parse, random
HOST = "https://api.colabfold.com"; UA = "apol1-bsab-boltz-msa/1.0 (python-urllib)"
os.makedirs("out/msa", exist_ok=True)
seqs = {}; k = None
for line in open("unique_seqs.fasta"):
    line = line.strip()
    if line.startswith(">"): k = line[1:]
    elif line: seqs[k] = seqs.get(k, "") + line
status = json.load(open("out/msa_status.json")) if os.path.exists("out/msa_status.json") else {}
todo = [k for k in seqs if not os.path.exists(f"out/msa/{k}.a3m")]
print(f"{len(seqs)} sequences, {len(todo)} to fetch", flush=True)
def req(url, data=None):
    r = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    with urllib.request.urlopen(r, timeout=120) as resp: return resp.read()
def submit(batch):
    q = "".join(f">{101+i}\n{seqs[k]}\n" for i, k in enumerate(batch))
    data = urllib.parse.urlencode({"q": q, "mode": "env"}).encode()
    while True:
        try:
            out = json.loads(req(f"{HOST}/ticket/msa", data))
        except Exception as e:
            print("submit error", e, flush=True); time.sleep(30); continue
        st = out.get("status")
        if st == "RATELIMIT": print("ratelimit; sleeping 60", flush=True); time.sleep(60); continue
        if st in ("ERROR", "MAINTENANCE"): raise RuntimeError(f"server {st}: {out}")
        return out["id"]
def wait(tid):
    while True:
        out = json.loads(req(f"{HOST}/ticket/{tid}")); st = out.get("status")
        if st == "COMPLETE": return
        if st == "ERROR": raise RuntimeError(f"ticket error {out}")
        time.sleep(5 + random.random()*3)
BATCH = int(os.environ.get("MSA_BATCH", "6")); WORKERS = int(os.environ.get("MSA_WORKERS", "4"))
import threading; from concurrent.futures import ThreadPoolExecutor
lock = threading.Lock()
if os.environ.get("MSA_REVERSE"): todo = todo[::-1]
def do_batch(b):
    batch = todo[b:b+BATCH]
    for attempt in range(4):
        try:
            tid = submit(batch); wait(tid)
            blob = req(f"{HOST}/result/download/{tid}")
            tf = tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz")
            a3m_lines = {}
            for name in ["uniref.a3m", "bfd.mgnify30.metaeuk30.smag30.a3m"]:
                try: f = tf.extractfile(name)
                except KeyError: f = None
                if f is None: continue
                update_M, M = True, None
                for raw in f.read().decode().split("\n"):
                    line = raw
                    if len(line) > 0:
                        if "\x00" in line: line = line.replace("\x00", ""); update_M = True
                        if line.startswith(">") and update_M:
                            M = int(line[1:].rstrip()); update_M = False
                            a3m_lines.setdefault(M, [])
                        if M is not None: a3m_lines[M].append(line)
            for i, k in enumerate(batch):
                lines = a3m_lines.get(101+i, [])
                if not lines: lines = [f">{101+i}", seqs[k]]
                open(f"out/msa/{k}.a3m", "w").write("\n".join(lines) + "\n")
                status[k] = {"n_lines": len(lines), "ticket": tid}
            with lock: json.dump(status, open("out/msa_status.json", "w"))
            print(f"batch {b//BATCH+1}/{(len(todo)+BATCH-1)//BATCH} ok ticket {tid} depths {[status[k]['n_lines']//2 for k in batch]}", flush=True)
            return
        except Exception as e:
            print(f"batch {b//BATCH+1} attempt {attempt+1} failed: {e}", flush=True); time.sleep(30*(attempt+1))
with ThreadPoolExecutor(WORKERS) as ex:
    list(ex.map(do_batch, range(0, len(todo), BATCH)))
print("done", len([k for k in seqs if os.path.exists(f'out/msa/{k}.a3m')]), "of", len(seqs), flush=True)
