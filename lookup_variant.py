#!/usr/bin/env python3
"""Query the genotype of ANY variant from your genome file, on demand.

Your genome file (23andMe/AncestryDNA .txt or a VCF, optionally .gz) is the
source of truth for your genotypes — this reads it directly, so you are never
limited to the curated catalogue. If a looked-up rsID happens to be in
data/known_variants.json, its gene + interpretation are shown too.

    python3 lookup_variant.py rs1801133 rs429358        # auto-find the file in inputs/
    python3 lookup_variant.py rs4988235 --genome path/to/genome.vcf.gz
    python3 lookup_variant.py rs1801133 --build 37       # force build for an un-annotated VCF
    python3 lookup_variant.py --self-check

Read-only: it prints results and never writes the database. To make a finding
stick in the viewer, promote it into the `variants` table — see CLAUDE.md
("DNA analysis phase"). The file is streamed line by line, so a multi-GB WGS VCF
is fine (it stops once every requested rsID is found).
"""
import gzip
import json
import sys
from pathlib import Path

import import_dna as I   # reuse parsing / build / match helpers — one source of truth

HERE = Path(__file__).parent
CATALOGUE = HERE / "data" / "known_variants.json"
NOT_FOUND_NOTE = ("not found in file (VCF: absent ≈ homozygous reference; "
                  "array: this SNP isn't on the chip)")


def open_text(path):
    path = Path(path)
    return gzip.open(path, "rt") if path.suffix == ".gz" else open(path, "r")


def find_genome():
    """Auto-detect a single genome file in inputs/."""
    cands = [p for p in sorted((HERE / "inputs").glob("*"))
             if p.suffix in (".txt", ".vcf", ".gz") and p.name != ".gitkeep"]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        sys.exit("No genome file in inputs/. Pass one with --genome PATH.")
    sys.exit("Multiple candidate files in inputs/ (" + ", ".join(p.name for p in cands)
             + "). Choose one with --genome PATH.")


def lookup(path, rsids, build_override=None):
    """Stream `path`; return (found {rsid: gt}, fmt, build) for the requested rsids."""
    wanted = set(rsids)
    header = []
    with open_text(path) as f:                       # peek the header for format + build
        for line in f:
            header.append(line)
            if (not line.startswith("#") and line.strip()) or len(header) > 1000:
                break
    fmt = I.detect_format("".join(header))
    found = {}

    if fmt == "vcf":
        build = build_override or I.detect_build("".join(header)) or I.DEFAULT_BUILD
        catalogue = json.loads(CATALOGUE.read_text())
        # position fallback only helps catalogued rsIDs (only they have coords)
        want_pos = {cp: rs for cp, rs in I.build_pos_index(catalogue, build).items()
                    if rs in wanted}
        with open_text(path) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                call = I.vcf_call(line.rstrip("\n").split("\t"), want_pos or None)
                if call and call[0] in wanted:
                    found[call[0]] = call[1]
                    if len(found) == len(wanted):
                        break
        return found, fmt, build

    build = "37"                                     # 23andMe/AncestryDNA are GRCh37
    with open_text(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            call = I.a23_call(line.rstrip("\n").split("\t"))
            if call and call[0] in wanted:
                found[call[0]] = call[1]
                if len(found) == len(wanted):
                    break
    return found, fmt, build


def describe(rsid, gt, catalogue):
    """One report line for a requested rsID."""
    if gt is None:
        return f"{rsid:12} —      {NOT_FOUND_NOTE}"
    rec = catalogue.get(rsid)
    if not rec:
        return f"{rsid:12} {gt:6} (not in catalogue — raw genotype only)"
    interp = I.match(gt, rec["genotypes"]) or "genotype not catalogued for this rsID"
    zyg = I.zygosity(gt, rec)
    gene = rec.get("gene", "")
    return f"{rsid:12} {gt:6} {gene:9} {zyg:8} {interp}  [{rec.get('category', '')}]"


def main():
    if "--self-check" in sys.argv:
        _self_check()
        return
    argv = sys.argv[1:]
    genome = build = None
    for flag, setter in (("--genome", "genome"), ("--build", "build")):
        if flag in argv:
            i = argv.index(flag)
            val = argv[i + 1] if i + 1 < len(argv) else None
            argv = argv[:i] + argv[i + 2:]
            if setter == "genome":
                genome = val
            else:
                build = val
    rsids = [a for a in argv if not a.startswith("-")]
    if not rsids:
        sys.exit("usage: lookup_variant.py <rsID ...> [--genome PATH] [--build 37|38]  |  --self-check")

    path = Path(genome) if genome else find_genome()
    found, fmt, bld = lookup(path, rsids, build)
    catalogue = json.loads(CATALOGUE.read_text())
    print(f"# {path.name} · {fmt} · GRCh{bld}")
    for rs in rsids:
        print(describe(rs, found.get(rs), catalogue))
    # APOE ε genotype if both defining SNPs were requested and found
    if {"rs429358", "rs7412"} <= set(found):
        apoe = I.apoe_call(found)
        if apoe:
            print(f"{'APOE':12} {apoe['genotype']:6} {'APOE':9} {apoe['zygosity']:8} {apoe['relevance']}")


def _self_check():
    cat = json.loads(CATALOGUE.read_text())
    tmp = HERE / "inputs" / "_lookup_selfcheck.txt"
    tmp.write_text("# rsid\tchrom\tpos\tgenotype\n"
                   "rs4988235\t2\t136608646\tGG\n"      # catalogued (strand-flipped CC)
                   "rs76543210\t1\t1000\tAG\n")          # present but not catalogued
    try:
        found, fmt, build = lookup(tmp, ["rs4988235", "rs76543210", "rs999999"])
        assert fmt == "23andme" and build == "37", (fmt, build)
        assert found == {"rs4988235": "GG", "rs76543210": "AG"}, found
        assert "Lactase non-persistent" in describe("rs4988235", "GG", cat)   # strand-flip + interp
        assert "not in catalogue" in describe("rs76543210", "AG", cat)
        assert "not found" in describe("rs999999", None, cat)
        # VCF streaming, incl. un-annotated (ID '.') resolved by catalogue position
        vtmp = HERE / "inputs" / "_lookup_selfcheck.vcf"
        p38 = cat["rs1801133"]["pos"]["38"]
        vtmp.write_text("##fileformat=VCFv4.2\n##reference=GRCh38\n"
                        f"1\t{p38}\t.\tG\tA\t.\t.\t.\tGT\t0/1\n")
        vf, vfmt, vbuild = lookup(vtmp, ["rs1801133"])
        assert vfmt == "vcf" and vbuild == "38" and vf == {"rs1801133": "GA"}, (vfmt, vbuild, vf)
        vtmp.unlink()
    finally:
        tmp.unlink(missing_ok=True)
    print("self-check OK")


if __name__ == "__main__":
    main()
