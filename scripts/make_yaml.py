#!/usr/bin/env python
"""Build Boltz-2 YAML inputs from a pairs CSV exactly as the ApoL1 bispecific programme does (stdlib only).

Usage:
    python make_yaml.py pairs.csv [--yaml-dir yaml] [--fasta unique_seqs.fasta] [--manifest pairs_manifest.csv]

Input CSV columns: scfv (binder arm sequence, chain A), ag_seq (antigen/ectodomain sequence, chain B),
optional pair_id (default: 'P' + sha1(scfv + '|' + ag_seq)[:10], the programme convention). Any other
columns (slug, target, binder, job_key, ...) are carried through to the manifest.

Per pair writes yaml/<pair_id>.yaml:
    version: 1
    sequences:
      - protein:
          id: A
          sequence: <scfv>
          msa: msa/S<sha1(scfv)[:12]>.a3m
      - protein:
          id: B
          sequence: <ag_seq>
          msa: msa/S<sha1(ag_seq)[:12]>.a3m

Also writes unique_seqs.fasta (one record per distinct sequence, header = MSA key) which is the input of
msa_fetch_par.py, and pairs_manifest.csv (pair_id, scfv_key, ag_key + input columns) for joining metrics.
boltz_batch.py rewrites the relative 'msa: msa/' prefix to an absolute path at run time, so keep it as is.
"""
import argparse, csv, hashlib, os, sys


def seq_key(seq: str) -> str:
    return "S" + hashlib.sha1(seq.encode()).hexdigest()[:12]


def pair_key(scfv: str, ag: str) -> str:
    return "P" + hashlib.sha1((scfv + "|" + ag).encode()).hexdigest()[:10]


def clean(seq: str) -> str:
    seq = "".join(seq.split()).upper()
    bad = set(seq) - set("ACDEFGHIKLMNPQRSTVWYX")
    if bad:
        raise ValueError(f"non-amino-acid characters in sequence: {sorted(bad)}")
    return seq


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pairs_csv")
    ap.add_argument("--yaml-dir", default="yaml")
    ap.add_argument("--fasta", default="unique_seqs.fasta")
    ap.add_argument("--manifest", default="pairs_manifest.csv")
    a = ap.parse_args(argv)

    with open(a.pairs_csv, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("empty pairs CSV")
    for col in ("scfv", "ag_seq"):
        if col not in rows[0]:
            sys.exit(f"pairs CSV must have a '{col}' column (has: {list(rows[0])})")

    os.makedirs(a.yaml_dir, exist_ok=True)
    seqs, manifest, seen = {}, [], set()
    for r in rows:
        scfv, ag = clean(r["scfv"]), clean(r["ag_seq"])
        pid = (r.get("pair_id") or "").strip() or pair_key(scfv, ag)
        if pid in seen:
            print(f"duplicate pair {pid}, skipping", file=sys.stderr); continue
        seen.add(pid)
        ka, kb = seq_key(scfv), seq_key(ag)
        seqs[ka], seqs[kb] = scfv, ag
        with open(os.path.join(a.yaml_dir, f"{pid}.yaml"), "w") as f:
            f.write("version: 1\nsequences:\n")
            for cid, seq, k in (("A", scfv, ka), ("B", ag, kb)):
                f.write(f"  - protein:\n      id: {cid}\n      sequence: {seq}\n      msa: msa/{k}.a3m\n")
        manifest.append({**r, "pair_id": pid, "scfv_key": ka, "ag_key": kb, "tokens": len(scfv) + len(ag)})

    with open(a.fasta, "w") as f:
        for k, s in seqs.items():
            f.write(f">{k}\n{s}\n")
    cols = list(manifest[0].keys())
    with open(a.manifest, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(manifest)
    print(f"{len(manifest)} YAMLs in {a.yaml_dir}/, {len(seqs)} unique sequences in {a.fasta}, manifest {a.manifest}")


if __name__ == "__main__":
    main()
