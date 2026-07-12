#!/usr/bin/env python3
"""Import a consumer-genome file and match it against data/known_variants.json.

Accepts a 23andMe / AncestryDNA raw text export OR a VCF. Parses each rsID's
genotype, matches it to the curated catalogue, and writes interpreted rows into
the `variants` table (which the viewer renders per category).

    python3 import_dna.py inputs/genome.txt     # detect format, match, write
    python3 import_dna.py --self-check          # run the built-in asserts

Orientation-safe: consumer chips and dbSNP don't always report on the same DNA
strand, so every match is tried directly AND reverse-complemented before giving
up. No external dependencies (pure stdlib) — no bcftools, snpEff or reference
genome needed; a curated catalogue of well-characterized SNPs is enough.

VCFs are matched by rsID (the ID column). When that's blank ('.', typical of a
raw whole-genome VCF), matching falls back to chrom:pos. Positions are build-
specific, so the genome build is auto-detected from the VCF header — override
with --build 37|38. 23andMe/AncestryDNA raw files are always GRCh37. Run
enrich_variants.py once to add coordinates for any new catalogue entries.

    python3 import_dna.py inputs/genome.vcf --build 38

Extend coverage by adding entries to data/known_variants.json (see its _README).
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "labs.db"
CATALOGUE = HERE / "data" / "known_variants.json"

COMP = {"A": "T", "T": "A", "G": "C", "C": "G"}


def rc(gt):
    """Reverse-complement a genotype string ('AG' -> 'CT')."""
    return "".join(COMP.get(b, b) for b in gt)


def canon(gt):
    """Order-independent key for a 2-allele genotype: 'GA' -> 'AG'."""
    return "".join(sorted(gt.upper()))


# ── Genome build (for the chrom:pos fallback) ────────────────────────────────
DEFAULT_BUILD = "38"


def norm_chrom(c):
    """Normalize a chromosome name: 'chr1'->'1', 'chrX'->'X', 'M'/'MT'->'MT'."""
    c = str(c).strip()
    if c[:3].lower() == "chr":
        c = c[3:]
    u = c.upper()
    return "MT" if u in ("M", "MT") else u


# A few canonical chromosome lengths per build — a reliable build fingerprint.
_CONTIG_LEN = {
    "1": {"249250621": "37", "248956422": "38"},
    "2": {"243199373": "37", "242193529": "38"},
    "X": {"155270560": "37", "156040895": "38"},
}


def detect_build(text):
    """Infer the genome build from a VCF header: '37' | '38' | None.
    Prefers ##contig length fingerprints; falls back to reference/assembly strings."""
    hint = None
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        if line.startswith("##contig"):
            m_id = re.search(r"ID=([^,>]+)", line)
            m_len = re.search(r"length=(\d+)", line)
            if m_id and m_len:
                fam = _CONTIG_LEN.get(norm_chrom(m_id.group(1)))
                if fam and m_len.group(1) in fam:
                    return fam[m_len.group(1)]
        low = line.lower()
        if hint is None:
            if "grch38" in low or "hg38" in low:
                hint = "38"
            elif "grch37" in low or "hg19" in low or "b37" in low:
                hint = "37"
    return hint


def build_pos_index(catalogue, build):
    """{(chrom, pos): rsid} for the given build, from catalogue coordinates."""
    idx = {}
    for rsid, rec in catalogue.items():
        if rsid.startswith("_"):
            continue
        pos = (rec.get("pos") or {}).get(build)
        chrom = rec.get("chrom")
        if pos and chrom:
            idx[(norm_chrom(str(chrom)), int(pos))] = rsid
    return idx


# ── Parsing ──────────────────────────────────────────────────────────────────
def detect_format(text):
    for line in text.splitlines():
        if line.startswith("##fileformat=VCF") or line.startswith("#CHROM"):
            return "vcf"
        if line.startswith("#") or not line.strip():
            continue
        return "23andme"          # first data line settles it
    return "23andme"


def parse_23andme(text):
    """23andMe (rsid, chrom, pos, genotype) or AncestryDNA (rsid, chrom, pos,
    allele1, allele2). Returns {rsid: 'AG'} for clean 2-base ACGT calls."""
    out = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("\t")
        if len(f) < 4:
            continue
        rsid = f[0]
        gt = f[3] if len(f) == 4 else f[3] + f[4]
        gt = gt.upper()
        if rsid.startswith("rs") and len(gt) == 2 and set(gt) <= set("ACGT"):
            out[rsid] = gt
    return out


def parse_vcf(text, pos_index=None):
    """Return {rsid: 'CT'} from a single-sample VCF. The rsID comes from the ID
    column; if that's blank ('.') and pos_index is given, it's recovered from
    (chrom, pos) — that's how un-annotated VCFs get matched."""
    out = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        c = line.split("\t")
        if len(c) < 10:
            continue
        chrom, pos, vid, ref, alt = c[0], c[1], c[2], c[3], c[4].split(",")
        rsid = vid if vid.startswith("rs") else None
        if rsid is None and pos_index is not None and pos.isdigit():
            rsid = pos_index.get((norm_chrom(chrom), int(pos)))
        if not rsid:
            continue
        alleles = [ref] + alt
        idx = c[9].split(":")[0].replace("|", "/").split("/")
        called = [alleles[int(i)] for i in idx
                  if i not in (".", "") and i.isdigit() and int(i) < len(alleles)]
        gt = "".join(called)
        if len(gt) == 2 and set(gt) <= set("ACGT"):
            out[rsid] = gt
    return out


# ── Matching ─────────────────────────────────────────────────────────────────
def match(user_gt, genotypes):
    """genotypes: {catalogue_gt: interpretation}. Try the user genotype directly,
    then reverse-complemented. Returns interpretation or None."""
    table = {canon(k): v for k, v in genotypes.items()}
    for g in (user_gt, rc(user_gt)):
        hit = table.get(canon(g))
        if hit is not None:
            return hit
    return None


def zygosity(user_gt, rec):
    """het if the two alleles differ; else hom-alt when the (canonical) genotype
    is flagged in the record's `risk` list, otherwise hom-ref."""
    if user_gt[0] != user_gt[1]:
        return "het"
    risk = {canon(r) for r in rec.get("risk", [])}
    risk |= {canon(rc(r)) for r in rec.get("risk", [])}   # strand-agnostic
    return "hom-alt" if canon(user_gt) in risk else "hom-ref"


# ── APOE ε genotype (two-SNP haplotype; can't use the single-SNP map) ─────────
_APOE = {   # (sorted rs429358 gt, sorted rs7412 gt) -> ε genotype
    ("CC", "CC"): "ε4/ε4", ("CT", "CC"): "ε3/ε4", ("TT", "CC"): "ε3/ε3",
    ("TT", "CT"): "ε2/ε3", ("TT", "TT"): "ε2/ε2", ("CT", "CT"): "ε2/ε4",
}
_APOE_REL = {
    "ε2/ε2": "ε2/ε2 — lowest LDL; longevity-associated (rare); small type-III hyperlipidemia risk",
    "ε2/ε3": "ε2/ε3 — favorable lipid profile, below-average cardiovascular risk",
    "ε3/ε3": "ε3/ε3 — neutral, most common genotype",
    "ε3/ε4": "ε3/ε4 — one ε4 allele: higher LDL, cardiovascular and Alzheimer's risk",
    "ε4/ε4": "ε4/ε4 — two ε4 alleles: markedly higher Alzheimer's and cardiovascular risk",
    "ε2/ε4": "ε2/ε4 — mixed effect (one protective, one risk allele)",
}


def apoe_call(genome):
    g1, g2 = genome.get("rs429358"), genome.get("rs7412")
    if not g1 or not g2:
        return None

    def orient(gt, alleles):
        if set(gt) <= alleles:
            return gt
        return rc(gt) if set(rc(gt)) <= alleles else gt

    key = ("".join(sorted(orient(g1, {"T", "C"}))),
           "".join(sorted(orient(g2, {"C", "T"}))))
    e = _APOE.get(key)
    if not e:
        return None
    zyg = "hom-alt" if e == "ε4/ε4" else ("het" if "ε4" in e else "hom-ref")
    return {"rsid": "rs429358", "gene": "APOE", "genotype": e,
            "zygosity": zyg, "relevance": _APOE_REL.get(e, e),
            "category": "Biological Age & Longevity"}


# ── Build + write ────────────────────────────────────────────────────────────
def build_rows(genome, catalogue, cat_ids):
    """Return [(category_id, rsid, gene, relevance, genotype, zygosity), ...]."""
    rows = []
    for rsid, rec in catalogue.items():
        if rsid.startswith("_") or rsid in ("rs429358", "rs7412"):  # APOE handled below
            continue
        gt = genome.get(rsid)
        if not gt:
            continue
        cid = cat_ids.get(rec["category"])
        if cid is None:
            continue
        rel = match(gt, rec["genotypes"]) or f"genotype {gt} (not in catalogue for this rsID)"
        rows.append((cid, rsid, rec.get("gene", ""), rel, gt, zygosity(gt, rec)))

    apoe = apoe_call(genome)
    if apoe and apoe["category"] in cat_ids:
        rows.append((cat_ids[apoe["category"]], apoe["rsid"], apoe["gene"],
                     apoe["relevance"], apoe["genotype"], apoe["zygosity"]))
    return rows


def main():
    if "--self-check" in sys.argv:
        _self_check()
        return
    argv = sys.argv[1:]
    build_override = None
    if "--build" in argv:
        i = argv.index("--build")
        build_override = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    args = [a for a in argv if not a.startswith("-")]
    if not args:
        sys.exit("usage: import_dna.py <genome.txt|.vcf> [--build 37|38]   |   --self-check")

    text = Path(args[0]).read_text()
    catalogue = json.loads(CATALOGUE.read_text())
    fmt = detect_format(text)
    if fmt != "vcf":
        genome, build = parse_23andme(text), "37"        # 23andMe/AncestryDNA are GRCh37
    else:
        build = build_override or detect_build(text)
        if build is None:
            build = DEFAULT_BUILD
            print(f"warning: could not detect genome build from the VCF header; "
                  f"assuming GRCh{build}. If position matches look low, retry with --build 37.")
        genome = parse_vcf(text, build_pos_index(catalogue, build))

    conn = sqlite3.connect(DB)
    cat_ids = dict(conn.execute("SELECT name_en, id FROM categories").fetchall())
    rows = build_rows(genome, catalogue, cat_ids)
    conn.executemany(
        "INSERT OR REPLACE INTO variants "
        "(category_id, rsid, gene, relevance, genotype, zygosity) VALUES (?,?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    print(f"Format {fmt}, build GRCh{build}. Genotyped {len(genome)} rsIDs; "
          f"matched {len(rows)} catalogued variants.")
    print("Next: python3 viewer.py")


def _self_check():
    assert rc("AG") == "TC" and canon("GA") == "AG"
    # 23andMe (4-col) + AncestryDNA (5-col) + no-call skipped
    g = parse_23andme("# header\nrs4988235\t2\t136608646\tTT\n"
                      "rs1801133\t1\t11856378\tC\tT\nrsX\t1\t1\t--\n")
    assert g == {"rs4988235": "TT", "rs1801133": "CT"}, g
    assert parse_vcf("#CHROM\tPOS\nx\t1\trs1\tC\tT\t.\t.\t.\tGT\t0/1\n") == {"rs1": "CT"}
    assert detect_format("##fileformat=VCFv4.2\n") == "vcf"
    assert detect_format("rs1\t1\t2\tAA\n") == "23andme"
    # genome-build detection: contig-length fingerprint, reference string, or unknown
    assert norm_chrom("chr1") == "1" and norm_chrom("chrX") == "X" and norm_chrom("MT") == "MT"
    assert detect_build("##contig=<ID=chr1,length=249250621>\n") == "37"
    assert detect_build("##contig=<ID=1,length=248956422>\n") == "38"
    assert detect_build("##reference=file:///ref/GRCh38.fa\n#CHROM\n") == "38"
    assert detect_build("##fileformat=VCFv4.2\n#CHROM\n") is None
    # chrom:pos fallback for an un-annotated VCF (ID '.'), build-specific
    idx38 = build_pos_index({"rs9": {"chrom": "1", "pos": {"37": 11856378, "38": 11796321}}}, "38")
    assert parse_vcf("1\t11796321\t.\tG\tA\t.\t.\t.\tGT\t0/1\n", idx38) == {"rs9": "GA"}
    assert parse_vcf("chr1\t11796321\t.\tG\tA\t.\t.\t.\tGT\t1|1\n", idx38) == {"rs9": "AA"}
    assert parse_vcf("1\t11856378\t.\tG\tA\t.\t.\t.\tGT\t0/1\n", idx38) == {}   # GRCh37 pos, wrong build
    # direct match, and strand-flip match (user 'AA' vs catalogue listing 'TT')
    genos = {"CC": "non-persistent", "CT": "persistent", "TT": "persistent"}
    assert match("TT", genos) == "persistent"
    assert match("AA", {"TT": "flip-hit"}) == "flip-hit"
    assert match("GG", {"AA": "x"}) is None
    # zygosity: het, hom flagged as risk -> hom-alt, hom not flagged -> hom-ref
    assert zygosity("CT", {}) == "het"
    assert zygosity("CC", {"risk": ["CC"]}) == "hom-alt"
    assert zygosity("TT", {"risk": ["CC"]}) == "hom-ref"
    # APOE haplotypes
    assert apoe_call({"rs429358": "TT", "rs7412": "CC"})["genotype"] == "ε3/ε3"
    assert apoe_call({"rs429358": "CT", "rs7412": "CC"})["genotype"] == "ε3/ε4"
    assert apoe_call({"rs429358": "CC", "rs7412": "CC"})["zygosity"] == "hom-alt"
    assert apoe_call({"rs429358": "TT"}) is None       # needs both SNPs
    print("self-check OK")


if __name__ == "__main__":
    main()
