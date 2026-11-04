
"""Run Boltz-2 on every YAML in yaml/ with precomputed MSAs in msa/, writing compact results to out/.
Checkpoints as it goes: out/metrics.jsonl (one line per pair) + out/<pair>/model_0.cif (best sample) so a deadline loses at most one pair."""
import os, sys, glob, json, subprocess, shutil, time, tarfile
os.makedirs("out", exist_ok=True)
if os.path.exists("inputs.tar.gz"):
    with tarfile.open("inputs.tar.gz") as tf: tf.extractall(".")
MSA_ABS = os.path.abspath("msa")
for y in glob.glob("yaml/*.yaml"):
    t = open(y).read().replace("msa: msa/", f"msa: {MSA_ABS}/"); open(y, "w").write(t)
done = set()
if os.path.exists("out/metrics.jsonl"):
    for line in open("out/metrics.jsonl"): done.add(json.loads(line)["pair_id"])
yamls = sorted(glob.glob("yaml/*.yaml")); todo = [y for y in yamls if os.path.basename(y)[:-5] not in done]
print(f"{len(yamls)} pairs, {len(todo)} to run", flush=True)
BATCH = int(os.environ.get("BOLTZ_BATCH", "8")); t0 = time.time()
for b in range(0, len(todo), BATCH):
    batch = todo[b:b+BATCH]; bdir = f"batch_{b//BATCH:03d}"; os.makedirs(bdir, exist_ok=True)
    for y in batch: shutil.copy(y, bdir)
    cmd = ["boltz", "predict", bdir, "--out_dir", f"pred_{bdir}", "--cache", "/root/.boltz", "--diffusion_samples", "3", "--seed", "42",
           "--recycling_steps", "3", "--sampling_steps", "200", "--output_format", "mmcif", "--override", "--write_full_pae", "--no_kernels"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"batch {bdir} failed rc={r.returncode}\n{r.stderr[-3000:]}", flush=True)
        # retry per-pair without kernels and smaller settings
    for y in batch:
        pid = os.path.basename(y)[:-5]; pd_ = f"pred_{bdir}/boltz_results_{bdir}/predictions/{pid}"
        rec = {"pair_id": pid, "status": "failed"}
        if os.path.isdir(pd_):
            confs = sorted(glob.glob(f"{pd_}/confidence_{pid}_model_*.json"))
            samples = []
            for c in confs:
                d = json.load(open(c)); d["model"] = int(c.rsplit("_", 1)[1].split(".")[0]); samples.append(d)
            if samples:
                best = max(samples, key=lambda d: d.get("confidence_score", 0))
                rec = {"pair_id": pid, "status": "ok", "best_model": best["model"], "iptm": best.get("iptm"), "ptm": best.get("ptm"),
                       "confidence_score": best.get("confidence_score"), "complex_plddt": best.get("complex_plddt"), "complex_iplddt": best.get("complex_iplddt"),
                       "complex_pde": best.get("complex_pde"), "complex_ipde": best.get("complex_ipde"),
                       "chains_ptm": best.get("chains_ptm"), "pair_chains_iptm": best.get("pair_chains_iptm"),
                       "iptm_all": [s.get("iptm") for s in sorted(samples, key=lambda d: d["model"])]}
                os.makedirs(f"out/{pid}", exist_ok=True)
                src = f"{pd_}/{pid}_model_{best['model']}.cif"
                if os.path.exists(src): shutil.copy(src, f"out/{pid}/best_model_{best['model']}.cif")
                for c in confs: shutil.copy(c, f"out/{pid}/")
        with open("out/metrics.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
    shutil.rmtree(bdir, ignore_errors=True); shutil.rmtree(f"pred_{bdir}", ignore_errors=True)
    n = min(b+BATCH, len(todo)); el = time.time()-t0
    print(f"{n}/{len(todo)} done, {el/60:.1f} min elapsed, {el/n*(len(todo)-n)/60:.1f} min remaining", flush=True)
print("ALL DONE", flush=True)
