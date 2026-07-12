#!/usr/bin/env python3
"""Query the genotype of ANY variant from your genome file, on demand.

Your genome file (23andMe/AncestryDNA .txt or a VCF, optionally .gz) is the
source of truth for your genotypes — this reads it directly, so you are never
limited to the curated catalogue. If a looked-up rsID happens to be in
data/known_variants.json, its gene + interpretation are shown too.

    python3 lookup_variant.py rs1801133 rs429358        # auto-find the file in inputs/
    python3 lookup_variant.py rs4988235 --genome path/to/genome.vcf.gz
    python3 lookup_variant.py rs1801133 --build 37       # force build for an un-annotated VCF
    python3 lookup_variant.py rs12913832 --offline       # skip the Ensembl position lookup
    python3 lookup_variant.py --self-check

For an un-annotated VCF (blank ID column), any requested rsID's position is
resolved on demand from Ensembl (build-matched) and then looked up — so you are
not limited to catalogued rsIDs. If a position resolves but the VCF has no record
there, that's reported as homozygous reference. Use --offline to skip Ensembl
(catalogue coordinates only). 23andMe/array files carry rsIDs directly and never
need the network.

Read-only: it prints results and never writes the database. To make a finding
stick in the viewer, promote it into the `variants` table — see CLAUDE.md
("DNA analysis phase"). VCF sites are fetched with bcftools when it's installed
(fast random access on a bgzipped, tabix-indexed VCF), otherwise the file is
scanned in pure Python — either way a multi-GB WGS VCF works.
"""
import json
import sys
from pathlib import Path

import import_dna as I        # reuse parsing / build / match / VCF-query helpers — one source of truth
import enrich_variants as E   # reuse the Ensembl position lookup (network; opt out with --offline)

HERE = Path(__file__).parent
CATALOGUE = HERE / "data" / "known_variants.json"


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


def resolve_positions(rsids, build):
    """{rsid: (chrom, pos)} from Ensembl for `build`. Network; returns {} (with a
    warning) on failure, and simply omits any rsID Ensembl can't map."""
    if not rsids:
        return {}
    try:
        resp = E.fetch(E.ENDPOINTS[build], list(rsids))
    except Exception as exc:                         # network / HTTP / JSON — degrade gracefully
        print(f"# note: could not reach Ensembl to resolve positions ({exc}); "
              f"use --offline to skip.", file=sys.stderr)
        return {}
    out = {}
    for rs in rsids:
        m = E.primary_mapping(resp.get(rs))
        if m:
            out[rs] = (m["seq_region_name"], int(m["start"]))
    return out


def lookup(path, rsids, build_override=None, resolver=resolve_positions, offline=False):
    """Stream `path`; return (found {rsid: gt}, fmt, build, resolved_set).
    `resolved_set` = rsIDs whose genomic position was known (from the catalogue or
    Ensembl) — lets callers tell homozygous-reference (position known, no record)
    from a position we couldn't resolve. `resolver` is injectable for testing."""
    wanted = set(rsids)
    header = I.read_header(path)                      # streamed — no whole-file read
    fmt = I.detect_format(header)

    if fmt != "vcf":                                 # 23andMe/AncestryDNA carry rsIDs directly
        found = {}
        with I.open_text(path) as f:
            for line in f:
                if line.startswith("#") or not line.strip():
                    continue
                call = I.a23_call(line.rstrip("\n").split("\t"))
                if call and call[0] in wanted:
                    found[call[0]] = call[1]
                    if len(found) == len(wanted):
                        break
        return found, fmt, "37", set()

    # VCF: coordinates for the requested rsIDs — catalogue first, Ensembl for the rest.
    build = build_override or I.detect_build(header) or I.DEFAULT_BUILD
    catalogue = json.loads(CATALOGUE.read_text())
    pos_of = {}                                      # rsid -> (chrom, pos)
    for rs in wanted:
        rec = catalogue.get(rs)
        pos = (rec.get("pos") or {}).get(build) if rec else None
        if rec and pos and rec.get("chrom"):
            pos_of[rs] = (I.norm_chrom(str(rec["chrom"])), int(pos))
    missing = [rs for rs in wanted if rs not in pos_of]
    if missing and not offline:
        pos_of.update(resolver(missing, build))
    resolved = set(pos_of)
    pos_index = {(I.norm_chrom(ch), p): rs for rs, (ch, p) in pos_of.items()}
    found = I.query_vcf(path, pos_index, wanted)     # bcftools fast path if available, else scan
    return found, fmt, build, resolved


def describe(rsid, gt, catalogue, fmt, resolved):
    """One report line for a requested rsID."""
    if gt is None:
        if fmt == "vcf" and rsid in resolved:
            return f"{rsid:12} —      homozygous reference (position resolved, no variant call there)"
        if fmt == "vcf":
            return f"{rsid:12} —      not found (couldn't resolve its position — --offline, or rsID unknown to Ensembl)"
        return f"{rsid:12} —      not found (this SNP isn't on the array)"
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
    offline = "--offline" in argv
    argv = [a for a in argv if a != "--offline"]
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
        sys.exit("usage: lookup_variant.py <rsID ...> [--genome PATH] [--build 37|38] [--offline]"
                 "  |  --self-check")

    path = Path(genome) if genome else find_genome()
    found, fmt, bld, resolved = lookup(path, rsids, build, offline=offline)
    catalogue = json.loads(CATALOGUE.read_text())
    print(f"# {path.name} · {fmt} · GRCh{bld}")
    for rs in rsids:
        print(describe(rs, found.get(rs), catalogue, fmt, resolved))
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
        found, fmt, build, resolved = lookup(tmp, ["rs4988235", "rs76543210", "rs999999"])
        assert fmt == "23andme" and build == "37" and resolved == set(), (fmt, build, resolved)
        assert found == {"rs4988235": "GG", "rs76543210": "AG"}, found
        assert "Lactase non-persistent" in describe("rs4988235", "GG", cat, fmt, resolved)
        assert "not in catalogue" in describe("rs76543210", "AG", cat, fmt, resolved)
        assert "on the array" in describe("rs999999", None, cat, fmt, resolved)

        # VCF, un-annotated (ID '.'): catalogued rsID resolved by its catalogue position
        vtmp = HERE / "inputs" / "_lookup_selfcheck.vcf"
        p38 = cat["rs1801133"]["pos"]["38"]
        vtmp.write_text("##fileformat=VCFv4.2\n##reference=GRCh38\n"
                        f"1\t{p38}\t.\tG\tA\t.\t.\t.\tGT\t0/1\n")
        vf, vfmt, vbuild, vres = lookup(vtmp, ["rs1801133"])
        assert vfmt == "vcf" and vbuild == "38" and vf == {"rs1801133": "GA"}, (vfmt, vbuild, vf)
        assert "rs1801133" in vres
        vtmp.unlink()

        # VCF, ARBITRARY (non-catalogue) rsID resolved by an injected resolver — no network.
        v2 = HERE / "inputs" / "_lookup_selfcheck2.vcf"
        v2.write_text("##fileformat=VCFv4.2\n##reference=GRCh38\n"
                      "7\t1234\t.\tG\tA\t.\t.\t.\tGT\t0/1\n")
        fake = lambda ids, build: {"rs990000": ("7", 1234), "rs990001": ("7", 9999)}
        f2, fmt2, b2, res2 = lookup(v2, ["rs990000", "rs990001"], resolver=fake)
        assert f2 == {"rs990000": "GA"}, f2                      # found at its resolved position
        assert res2 == {"rs990000", "rs990001"}, res2            # both positions resolved
        assert "raw genotype only" in describe("rs990000", "GA", cat, "vcf", res2)
        assert "homozygous reference" in describe("rs990001", None, cat, "vcf", res2)  # resolved, absent
        # --offline skips the resolver → an arbitrary rsID stays unresolved
        f3, _, _, res3 = lookup(v2, ["rs990000"], resolver=fake, offline=True)
        assert f3 == {} and res3 == set(), (f3, res3)
        assert "couldn't resolve" in describe("rs990000", None, cat, "vcf", res3)
        v2.unlink()
    finally:
        tmp.unlink(missing_ok=True)
    print("self-check OK")


if __name__ == "__main__":
    main()
