#!/usr/bin/env python3
"""Import a consumer-genome file and match it against data/known_variants.json.

Accepts a 23andMe / AncestryDNA raw text export OR a VCF. Parses each rsID's
genotype, matches it to the curated catalogue, and writes interpreted rows into
the `variants` table (which the viewer renders per category).

    python3 import_dna.py inputs/genome.txt     # detect format, match, write
    python3 import_dna.py --self-check          # run the built-in asserts

Orientation-safe: consumer chips and dbSNP don't always report on the same DNA
strand, so every match is tried directly AND reverse-complemented before giving
up — except for palindromic (A/T, C/G) SNPs, where the flip of one homozygote IS
the opposite homozygote, so a flip is undetectable and those are taken as
reported (both file kinds report the genomic plus strand). VCF sites are pulled with bcftools when it's installed (fast random access on
a bgzipped, tabix-indexed VCF) and by a pure-Python scan otherwise — so it runs
with or without bcftools; no snpEff or reference genome is needed.

VCFs are matched by rsID (the ID column). When that's blank ('.', typical of a
raw whole-genome VCF), matching falls back to chrom:pos. Positions are build-
specific, so the genome build is auto-detected from the VCF header — override
with --build 37|38. 23andMe/AncestryDNA raw files are always GRCh37. Run
enrich_variants.py once to add coordinates for any new catalogue entries.

    python3 import_dna.py inputs/genome.vcf --build 38

Extend coverage by adding entries to data/known_variants.json (see its _README).
"""
import gzip
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
DB = HERE / "labs.db"
CATALOGUE = HERE / "data" / "known_variants.json"


def open_text(path):
    """Open a genome file for text reading, transparently decompressing .gz."""
    path = Path(path)
    return gzip.open(path, "rt") if path.suffix == ".gz" else open(path, "r")


def read_genome_text(path):
    """Whole file as text (gzip-aware). Fine for 23andMe/array files; VCFs go
    through read_header + query_vcf instead so a multi-GB WGS VCF isn't slurped."""
    with open_text(path) as f:
        return f.read()


def read_header(path, max_lines=2000):
    """First lines of a genome file (streaming) — enough for format/build
    detection without loading a multi-GB VCF."""
    head = []
    with open_text(path) as f:
        for line in f:
            head.append(line)
            if (not line.startswith("#") and line.strip()) or len(head) >= max_lines:
                break
    return "".join(head)

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


def a23_call(cols):
    """One 23andMe/AncestryDNA line (split on tab) -> (rsid, 'AG') or None."""
    if len(cols) < 4:
        return None
    rsid = cols[0]
    gt = (cols[3] if len(cols) == 4 else cols[3] + cols[4]).upper()
    if rsid.startswith("rs") and len(gt) in (1, 2) and set(gt) <= set("ACGT"):   # 1 letter = hemizygous (male X/Y/MT)
        return rsid, gt
    return None


def parse_23andme(text):
    """23andMe (rsid, chrom, pos, genotype) or AncestryDNA (rsid, chrom, pos,
    allele1, allele2). Returns {rsid: 'AG'} for clean ACGT calls ('A' when hemizygous)."""
    out = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        call = a23_call(line.split("\t"))
        if call:
            out[call[0]] = call[1]
    return out


STATS = {"filtered": 0, "unclear": set()}   # ponytail: module counters (non-PASS sites; wanted rsIDs whose record
                                            # held no confident call); thread a stats object through if this grows


def vcf_rsid(cols, pos_index=None):
    """The rsID of a VCF record: its ID column, else its (chrom, pos) in pos_index."""
    chrom, pos, vid = cols[0], cols[1], cols[2]
    if vid.startswith("rs"):
        return vid
    if pos_index is not None and pos.isdigit():
        return pos_index.get((norm_chrom(chrom), int(pos)))
    return None


def vcf_call(cols, pos_index=None):
    """One VCF data line (split on tab) -> (rsid, 'CT') or None. The rsID comes
    from the ID column; if that's blank ('.') and pos_index is given, it's
    recovered from (chrom, pos). Only clean ACGT calls that passed the caller's
    filters (FILTER is PASS or '.') are returned: two letters for a diploid call,
    one for a hemizygous call (GT '1' on a male X) — a half-missing '0/.' is no call."""
    if len(cols) < 10:
        return None
    ref, alt = cols[3], cols[4].split(",")
    rsid = vcf_rsid(cols, pos_index)
    if not rsid:
        return None
    if cols[6] not in (".", "PASS", ""):      # a filtered call is not a confident genotype
        STATS["filtered"] += 1
        return None
    alleles = [ref] + alt
    idx = cols[9].split(":")[0].replace("|", "/").split("/")
    called = [alleles[int(i)] for i in idx
              if i not in (".", "") and i.isdigit() and int(i) < len(alleles)]
    gt = "".join(called)
    if len(called) == len(idx) and len(gt) in (1, 2) and set(gt) <= set("ACGT"):
        return rsid, gt
    return None


def parse_vcf(text, pos_index=None):
    """Return {rsid: 'CT'} from a single-sample VCF (whole text in memory).
    The chrom:pos fallback (via pos_index) is how un-annotated VCFs get matched."""
    out = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        call = vcf_call(line.split("\t"), pos_index)
        if call:
            out[call[0]] = call[1]
    return out


# ── VCF querying (bcftools fast path, stdlib fallback) ───────────────────────
def _bcftools_regions(path, positions):
    """`bcftools query` restricted to `positions` (iterable of (chrom, pos)),
    emitting VCF-shaped columns so vcf_call can parse them. Returns a list of
    split-column records, or None when bcftools can't serve the query (missing
    binary, un-indexed / not-bgzipped file, or any error) so the caller falls back.
    Random access on an indexed bgzipped VCF — the reason bcftools is worth using."""
    if not positions or shutil.which("bcftools") is None:
        return None
    p = str(path)
    if p.endswith(".gz") and not (os.path.exists(p + ".tbi") or os.path.exists(p + ".csi")):
        try:                                          # best-effort: index a bgzipped VCF once
            subprocess.run(["bcftools", "index", "-f", "-t", p], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1200)
        except Exception:
            pass
    regions = ",".join(f"{ch}:{pos}-{pos}" for ch, pos in positions)
    fmt = "%CHROM\t%POS\t%ID\t%REF\t%ALT\t.\t%FILTER\t.\tGT\t[%GT]\n"   # 10 cols → the vcf_call layout
    try:
        out = subprocess.run(["bcftools", "query", "-r", regions, "-f", fmt, p],
                             check=True, capture_output=True, text=True, timeout=1200).stdout
    except Exception:
        return None
    return [ln.split("\t") for ln in out.splitlines() if ln.strip()]


def query_vcf(path, pos_index, wanted):
    """{rsid: gt} for `wanted` rsIDs from a VCF. Fast path: bcftools region query
    over pos_index's positions (used only when every wanted rsID has coordinates,
    so nothing that needs ID-column matching is missed). Fallback: stream the file
    (which also catches rsIDs matched by their ID column when coords are unknown)."""
    if pos_index and set(wanted) <= set(pos_index.values()):
        recs = _bcftools_regions(path, list(pos_index.keys()))
        if recs is not None:
            out = {}
            for cols in recs:
                call = vcf_call(cols, pos_index)
                if call and call[0] in wanted:
                    out[call[0]] = call[1]
                elif not call and vcf_rsid(cols, pos_index) in wanted:
                    STATS["unclear"].add(vcf_rsid(cols, pos_index))
            return out
    out = {}
    with open_text(path) as f:
        for line in f:
            if line.startswith("#") or not line.strip():
                continue
            cols = line.rstrip("\n").split("\t")
            call = vcf_call(cols, pos_index or None)
            if call and call[0] in wanted:
                out[call[0]] = call[1]
                if len(out) == len(wanted):
                    break
            elif not call and len(cols) >= 10 and vcf_rsid(cols, pos_index or None) in wanted:
                STATS["unclear"].add(vcf_rsid(cols, pos_index or None))
    return out


# ── Matching ─────────────────────────────────────────────────────────────────
def palindromic(genotypes):
    """True when a SNP's alleles are A/T or C/G. Reverse-complementing one homozygote of
    such a SNP yields the OTHER homozygote, so a strand flip is undetectable and an rc()
    retry would silently return the inverted interpretation. Unknown alleles → False."""
    alleles = set("".join(genotypes).upper())
    return bool(alleles) and (alleles <= {"A", "T"} or alleles <= {"C", "G"})


def match(user_gt, genotypes):
    """genotypes: {catalogue_gt: interpretation}. Try the user genotype directly, then
    reverse-complemented — except for palindromic SNPs (see palindromic()), which are
    taken as reported. Returns interpretation or None."""
    table = {canon(k): v for k, v in genotypes.items()}
    tries = (user_gt,) if palindromic(genotypes) else (user_gt, rc(user_gt))
    for g in tries:
        hit = table.get(canon(g))
        if hit is not None:
            return hit
    return None


def zygosity(user_gt, rec):
    """Relative to the catalogue's `risk` list, NOT to the reference genome (where the
    reference allele is often the minor or the effect one). 'hemi' for a one-copy call;
    'het' if the two alleles differ. A homozygote is 'hom-alt' when it is the flagged
    genotype and 'hom-ref' when it is another genotype the entry lists; plain 'hom' when
    the entry has no `risk` list or doesn't list this genotype — the effect is then
    unknown, and 'hom-ref' would paint it green in the viewer."""
    if len(user_gt) == 1:
        return "hemi"
    if user_gt[0] != user_gt[1]:
        return "het"
    risk_list = rec.get("risk") or []
    if not risk_list:
        return "hom"
    risk = {canon(r) for r in risk_list}
    if not palindromic(rec.get("genotypes", {})):
        risk |= {canon(rc(r)) for r in risk_list}   # strand-agnostic; unsafe for palindromic SNPs
    if canon(user_gt) in risk:
        return "hom-alt"
    return "hom" if match(user_gt, rec.get("genotypes", {})) is None else "hom-ref"


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

    src = args[0]
    header = read_header(src)
    catalogue = json.loads(CATALOGUE.read_text())
    fmt = detect_format(header)
    if fmt != "vcf":
        genome, build = parse_23andme(read_genome_text(src)), "37"   # 23andMe/AncestryDNA are GRCh37
    else:
        build = build_override or detect_build(header)
        if build is None:
            build = DEFAULT_BUILD
            print(f"warning: could not detect genome build from the VCF header; "
                  f"assuming GRCh{build}. If matches look low, retry with --build 37.")
        pos_index = build_pos_index(catalogue, build)
        genome = query_vcf(src, pos_index, set(pos_index.values()))   # bcftools if available, else scan

    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
    cat_ids = dict(conn.execute("SELECT name_en, id FROM categories").fetchall())
    rows = build_rows(genome, catalogue, cat_ids)
    cat_rsids = [k for k in catalogue if not k.startswith("_")]
    # replace, don't accumulate: a catalogue row from an earlier file or category must go;
    # hand-promoted (non-catalogue) rows are left alone
    conn.execute(f"DELETE FROM variants WHERE rsid IN ({','.join('?' * len(cat_rsids))})", cat_rsids)
    conn.executemany(
        "INSERT OR REPLACE INTO variants "
        "(category_id, rsid, gene, relevance, genotype, zygosity) VALUES (?,?,?,?,?,?)",
        rows)
    conn.commit()
    conn.close()
    print(f"Format {fmt}, build GRCh{build}. Matched {len(rows)} catalogued variants.")
    absent = [k for k in cat_rsids if k not in genome and k not in ("rs429358", "rs7412")]
    if absent:
        print(f"  {len(absent)} catalogue site(s) absent from the file — not on the array, not called, "
              "filtered, or (in a variant-only VCF) homozygous for the reference allele. Absence is "
              "not a genotype: no row is written for them.")
    n_pal = sum(1 for r in rows if palindromic(catalogue.get(r[1], {}).get("genotypes", {})))
    if n_pal:
        print(f"  {n_pal} palindromic (A/T or C/G) SNP(s) taken as reported — strand can't be "
              "verified for these; consumer files and VCFs report the genomic plus strand.")
    if STATS["filtered"]:
        print(f"  {STATS['filtered']} catalogue site(s) skipped: VCF FILTER not PASS.")
    print("Next: python3 viewer.py")
    print("Tip: the whole genome stays queryable — `python3 lookup_variant.py <rsID ...>` "
          "for any variant, catalogued or not.")


def _self_check():
    assert rc("AG") == "TC" and canon("GA") == "AG"
    # 23andMe (4-col) + AncestryDNA (5-col) + no-call skipped
    g = parse_23andme("# header\nrs4988235\t2\t136608646\tTT\n"
                      "rs1801133\t1\t11856378\tC\tT\nrsX\t1\t1\t--\n")
    assert g == {"rs4988235": "TT", "rs1801133": "CT"}, g
    assert parse_vcf("#CHROM\tPOS\nx\t1\trs1\tC\tT\t.\t.\t.\tGT\t0/1\n") == {"rs1": "CT"}
    assert parse_vcf("x\t1\trs1\tC\tT\t.\tPASS\t.\tGT\t0/1\n") == {"rs1": "CT"}
    assert parse_vcf("x\t1\trs1\tC\tT\t.\tLowQual\t.\tGT\t0/1\n") == {}     # filtered call skipped
    assert parse_vcf("X\t1\trs1\tC\tT\t.\tPASS\t.\tGT\t1\n") == {"rs1": "T"}  # hemizygous, not dropped
    assert parse_vcf("x\t1\trs1\tC\tT\t.\tPASS\t.\tGT\t0/.\n") == {}        # half-missing = no call
    assert parse_23andme("rs5\tX\t9\tA\n") == {"rs5": "A"}                       # male X on a chip
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
    # direct match, and strand-flip match (user 'AG' vs catalogue listing 'CT')
    genos = {"CC": "non-persistent", "CT": "persistent", "TT": "persistent"}
    assert match("TT", genos) == "persistent"
    assert match("AG", {"CT": "flip-hit", "TT": "x"}) == "flip-hit"
    assert match("GG", {"AA": "x"}) is None
    # palindromic (A/T, C/G) SNPs: no strand-flip retry — the flip of one homozygote is the other
    pal = {"AA": "two risk alleles", "AT": "one", "TT": "lower-risk"}
    assert palindromic(pal) and palindromic({"CC": 1, "GG": 1}) and not palindromic(genos) and not palindromic({})
    assert match("TT", pal) == "lower-risk"
    assert match("TT", {"AA": "x", "AT": "y"}) is None          # used to flip to AA and return "x"
    # zygosity: het; hom flagged as risk -> hom-alt (strand-agnostic for non-palindromic SNPs);
    # hom not flagged -> hom-ref; no risk list -> plain 'hom' (effect allele unknown)
    assert zygosity("CT", {}) == "het"
    assert zygosity("CC", {"risk": ["CC"]}) == "hom-alt"
    assert zygosity("TT", {"risk": ["CC"], "genotypes": genos}) == "hom-ref"
    assert zygosity("GG", {"risk": ["CC"], "genotypes": genos}) == "hom-alt"   # flipped risk genotype
    assert zygosity("TT", {}) == "hom"
    assert zygosity("TT", {"risk": ["AA"], "genotypes": pal}) == "hom-ref"     # palindromic: TT is not flipped AA
    assert zygosity("AA", {"risk": ["CC"], "genotypes": {"CC": 1, "CG": 1, "GG": 1}}) == "hom"  # not a listed genotype
    assert zygosity("T", {"risk": ["CC"]}) == "hemi"
    # APOE haplotypes
    assert apoe_call({"rs429358": "TT", "rs7412": "CC"})["genotype"] == "ε3/ε3"
    assert apoe_call({"rs429358": "CT", "rs7412": "CC"})["genotype"] == "ε3/ε4"
    assert apoe_call({"rs429358": "CC", "rs7412": "CC"})["zygosity"] == "hom-alt"
    assert apoe_call({"rs429358": "TT"}) is None       # needs both SNPs
    # query_vcf: same result whether bcftools serves it or the stdlib fallback does
    import tempfile
    idx = build_pos_index({"rs9": {"chrom": "1", "pos": {"38": 11796321}}}, "38")
    with tempfile.NamedTemporaryFile("w", suffix=".vcf", delete=False) as tf:
        tf.write("##fileformat=VCFv4.2\n#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\tS\n")
        tf.write("1\t11796321\t.\tG\tA\t.\t.\t.\tGT\t0/1\n")   # un-annotated (ID '.')
        vpath = tf.name
    try:
        assert query_vcf(vpath, idx, {"rs9"}) == {"rs9": "GA"}, "query_vcf"
        assert query_vcf(vpath, {}, {"rsX"}) == {}, "query_vcf empty"
    finally:
        os.unlink(vpath)
    print("self-check OK")


if __name__ == "__main__":
    main()
