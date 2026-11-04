# apol1-boltz — prebuilt Boltz-2 co-folding image for the ApoL1 bispecific programme

`ghcr.io/mjcipriano/apol1-boltz:2.2.1` is a self-contained, GPU-ready container holding **Boltz-2 (`boltz==2.2.1`)**,
its **weights and CCD cache baked in at `/root/.boltz`**, and the programme's batch scripts under `/opt/apol1/`.
No environment build, no weight download and no network access are needed at run time (the MSA step is the one
exception, see below). Pulling the image replaces the former Modal image build + weights volume.

| item | value |
|---|---|
| base | `nvidia/cuda:12.4.1-base-ubuntu22.04` |
| Python | 3.11 (micromamba, `/opt/py`) |
| torch | 2.5.1+cu124 (pip wheel; bundles CUDA 12.4 runtime libs — host needs an NVIDIA driver >= 525 only) |
| boltz | 2.2.1 (PyPI), kernels **not** installed (pipeline runs `--no_kernels`) |
| weights | `/root/.boltz/boltz2_conf.ckpt` (2.29 GB), `boltz2_aff.ckpt` (2.06 GB), `mols/` (45,227 CCD pickles) — sha256-verified against the official release ([weights.sha256](weights.sha256)) |
| scripts | `/opt/apol1/boltz_batch.py`, `/opt/apol1/make_yaml.py`, `/opt/apol1/msa_fetch_par.py` |
| workdir | `/work` (mount your run directory here) |

Note on `mols.tar`: boltz 2.2.1 only checks that `/root/.boltz/mols.tar` exists once `mols/` has been extracted, so the
image ships a 0-byte `mols.tar` sentinel instead of a second 1.8 GB copy of the CCD. `boltz predict --cache /root/.boltz`
never downloads anything. If you ever point `--cache` elsewhere, Boltz will download a fresh cache there.

## Pull

```bash
docker pull ghcr.io/mjcipriano/apol1-boltz:2.2.1     # or :latest
```

## Run on the WSL / local GPU host

```bash
cd /path/to/run_dir            # contains yaml/ and msa/ (or inputs.tar.gz, see layout below)
docker run --rm --gpus all --user "$(id -u):$(id -g)" -e HOME=/tmp -v "$PWD":/work ghcr.io/mjcipriano/apol1-boltz:2.2.1 \
    python /opt/apol1/boltz_batch.py
```

`--user "$(id -u):$(id -g)"` keeps the files written to the bind mount owned by you instead of root (the weights under
`/root/.boltz` are world-readable, `BOLTZ_CACHE` points at them). Omit it on Modal or when running as root is fine.

`boltz_batch.py` is the exact programme pipeline: batches of `BOLTZ_BATCH` (default 8) YAMLs, `boltz predict … --cache /root/.boltz
--diffusion_samples 3 --seed 42 --recycling_steps 3 --sampling_steps 200 --output_format mmcif --override --write_full_pae --no_kernels`.
It is resumable: pairs already present in `out/metrics.jsonl` are skipped. On a 6 GB card (GTX 1660 Ti) set `-e BOLTZ_BATCH=1`.

## Run on Modal

```python
import os, modal
os.environ["MODAL_IMAGE_BUILDER_VERSION"] = "2025.06"   # this workspace needs the 2025.06 builder for from_registry()

image = modal.Image.from_registry("ghcr.io/mjcipriano/apol1-boltz:2.2.1")
app = modal.App("apol1-boltz", image=image)

@app.function(gpu="A10G", timeout=6 * 3600)
def run_batch(inputs_tar: bytes) -> dict:
    import subprocess, pathlib, json
    pathlib.Path("/work/inputs.tar.gz").write_bytes(inputs_tar)
    subprocess.run(["python", "/opt/apol1/boltz_batch.py"], cwd="/work", check=True)
    out = {}
    for p in pathlib.Path("/work/out").rglob("*"):
        if p.is_file():
            out[str(p.relative_to("/work/out"))] = p.read_bytes()
    return out

@app.local_entrypoint()
def main(inputs: str = "inputs.tar.gz", dest: str = "out"):
    import pathlib
    files = run_batch.remote(pathlib.Path(inputs).read_bytes())
    for rel, data in files.items():
        p = pathlib.Path(dest, rel); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(data)
    print(f"{len(files)} files -> {dest}/")
```

`modal run modal_run.py --inputs inputs.tar.gz`. Because the weights are in the image there is no volume to hydrate; Modal
caches the pulled image after the first run. The image is public, so no registry secret is needed.

## Input layout

```
run_dir/
├── yaml/<pair_id>.yaml      # one Boltz YAML per scFv x antigen pair (chain A = scFv, chain B = antigen)
├── msa/<S+sha1[:12]>.a3m    # one unpaired a3m per unique sequence, referenced from the YAMLs as msa: msa/<key>.a3m
└── inputs.tar.gz            # optional: a tarball containing yaml/ and msa/ — boltz_batch.py extracts it first
```

Build the YAMLs from a pairs CSV (columns `scfv`, `ag_seq`, optional `pair_id`; extra columns are carried through):

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work ghcr.io/mjcipriano/apol1-boltz:2.2.1 python /opt/apol1/make_yaml.py pairs.csv
# -> yaml/<pair_id>.yaml, unique_seqs.fasta, pairs_manifest.csv
```

Conventions (identical to the programme's existing 500+ pairs): `pair_id = "P" + sha1(scfv + "|" + ag_seq)[:10]`,
MSA key `"S" + sha1(sequence)[:12]`, YAML `version: 1`, chain ids `A` (scFv) and `B` (antigen).

## MSA step (ColabFold MMseqs2 API, CPU, needs internet)

```bash
# inside the container (CPU only, no --gpus needed) or with any Python >= 3.8 outside it (stdlib only)
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/work ghcr.io/mjcipriano/apol1-boltz:2.2.1 python /opt/apol1/msa_fetch_par.py
mv out/msa msa    # msa_fetch_par.py writes out/msa/<key>.a3m + out/msa_status.json; boltz_batch.py expects msa/
```

`msa_fetch_par.py` reads `unique_seqs.fasta`, queries `https://api.colabfold.com` in batches (`MSA_BATCH`, default 6) with
`MSA_WORKERS` (default 4) threads in `env` mode (UniRef30 + environmental), writes one a3m per key and is resumable.
Respect the public server's rate limits (it is a shared academic resource); run this on CPU outside the GPU job.

## Output layout

```
out/
├── metrics.jsonl                     # one JSON line per pair: pair_id, status, best_model, iptm, ptm, confidence_score,
│                                     #   complex_plddt, complex_iplddt, complex_pde, complex_ipde, chains_ptm,
│                                     #   pair_chains_iptm, iptm_all (ipTM of samples 0..2, in model order)
└── <pair_id>/
    ├── best_model_<k>.cif            # the top-ranked sample (Boltz confidence_score = 0.8 complex_pLDDT + 0.2 ipTM)
    └── confidence_<pair_id>_model_{0,1,2}.json
```

`iptm` in `metrics.jsonl` is the ipTM of the **top-ranked** sample (programme column `iptm_top_ranked_model`);
`max(iptm_all)` gives `iptm_max`. Join to the pairs via `pairs_manifest.csv` / `pair_id`.

## Smoke tests

```bash
docker run --rm ghcr.io/mjcipriano/apol1-boltz:2.2.1 bash -c "boltz predict --help | head -3 && ls -la /root/.boltz"
docker run --rm --gpus all ghcr.io/mjcipriano/apol1-boltz:2.2.1 python -c "import torch; print(torch.cuda.get_device_name(0))"
```

## Rebuilding

* Locally (re-using an existing cache): hard-link or copy `boltz2_conf.ckpt`, `boltz2_aff.ckpt`, `mols.tar` from `~/.boltz` into `weights/`, then
  `docker build --build-arg WEIGHTS_SOURCE=local -t ghcr.io/mjcipriano/apol1-boltz:2.2.1 .`
* Locally (download): `docker build -t ghcr.io/mjcipriano/apol1-boltz:2.2.1 .` (fetches ~6.2 GB from Hugging Face).
* GitHub Actions: `Actions -> build-and-push-image -> Run workflow` ([.github/workflows/build.yml](.github/workflows/build.yml));
  manual dispatch only, pushes `:<tag>` and `:latest` with the repository `GITHUB_TOKEN`.

The weights are redistributed under the Boltz-2 licence (MIT, boltz-community). Cite Passaro et al. 2025 (Boltz-2) and
Mirdita et al. 2022 (ColabFold MSA server) when reporting results.
