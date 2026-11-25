#!/usr/bin/env python3
"""Two checks on the `variants` table, both CONSISTENCY (two layers disagree about one fact):

  1. Direction in prose. A variant whose call carries NO copy of its effect allele
     (variants.effect_copies = 0) may not be written up as carried — "MTHFR hom-alt",
     "carries the risk allele", "rsN carrier" — in a sentence that does not also say no.
     The common slip: reading a zygosity word or a gene name as "has the risk".
  2. Promoted rows vs the genome. A row added by hand (an rsID outside the catalogue, which
     import_dna.py re-derives on every import) is re-read from the genome file in inputs/
     and must show the same letters (either strand, either order).

    python3 check_variants.py            # exit 1 on a contradiction
    python3 check_variants.py --self-check

Ceilings (ponytail): prose is judged sentence by sentence and any negation word clears a
sentence; the re-read runs offline, so a promoted rsID whose position only Ensembl knows (in
an un-annotated VCF) is listed as not re-read, never failed.
"""
import json
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DB = ROOT / "labs.db"
CATALOGUE = ROOT / "data" / "known_variants.json"

CARRIED = re.compile(r"hom-alt|risk (?:allele|genotype|variant)|\bcarrier\b|\bcarr(?:y|ies|ying)\b", re.I)
NEGATED = re.compile(r"\b(?:no|not|non|neither|nor|none|zero|0|absent|without|lacks?|doesn't|isn't)\b", re.I)
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n|\||•")


def prose(conn):
    """[(where, text)] — every authored text a variant can be misdescribed in."""
    out = []
    for cid, *cols in conn.execute("SELECT category_id, insight, insight_dna, concordance FROM category_insights"):
        out += [(f"category {cid}", t) for t in cols if t]
    out += [("routine", t) for (t,) in conn.execute("SELECT protocol FROM unified_protocol") if t]
    return out


def carried_claims(conn):
    """→ [(where, rsid, gene, sentence)] naming a 0-copy variant as carried."""
    absent = conn.execute("SELECT DISTINCT rsid, gene, effect_allele FROM variants "
                          "WHERE effect_copies = 0").fetchall()
    hits = []
    for where, text in prose(conn):
        for sentence in SENTENCE.split(text):
            if not CARRIED.search(sentence) or NEGATED.search(sentence):
                continue
            for rsid, gene, allele in absent:
                named = re.search(rf"\b{rsid}\b", sentence) or (
                    gene and re.search(rf"(?<![\w-]){re.escape(gene)}(?![\w-])", sentence)
                    and not set(re.findall(r"rs\d+", sentence)) - {rsid})
                if named:
                    hits.append((where, rsid, f"{gene} (0 copies of {allele})", sentence.strip()[:160]))
    return hits


def genome_file():
    cands = [p for p in sorted((ROOT / "inputs").glob("*")) if p.suffix in (".txt", ".vcf", ".gz")
             and not p.name.startswith(("_", "."))]
    return cands[0] if len(cands) == 1 else None


def _letters(gt):
    return "".join(sorted(re.sub(r"[^ACGT]", "", (gt or "").upper())))


def promoted_vs_genome(conn, path=None, lookup=None):
    """→ (mismatches, not_reread). Rows outside the catalogue, re-read from the genome."""
    import import_dna as I
    cat = {k for k in json.loads(CATALOGUE.read_text()) if not k.startswith("_")}
    rows = [r for r in conn.execute("SELECT rsid, genotype FROM variants") if r[0] not in cat]
    path = path or genome_file()
    if not rows:
        return [], []
    if path is None:
        return [], [f"{len(rows)} promoted row(s): no single genome file in inputs/ to re-read them from"]
    if lookup is None:
        from lookup_variant import lookup
    found, *_ = lookup(path, [r for r, _ in rows], offline=True)
    bad, unread = [], []
    for rsid, gt in rows:
        got = found.get(rsid)
        if got is None:
            unread.append(f"{rsid}: not re-read (absent, no confident call, or position unknown offline)")
        elif _letters(gt) not in (_letters(got), _letters(I.rc(got))):
            bad.append(f"{rsid}: recorded {gt}, genome file says {got}")
    return bad, unread


def self_check():
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE category_insights (category_id INT, insight TEXT, insight_dna TEXT, concordance TEXT);
        CREATE TABLE unified_protocol (protocol TEXT);
        CREATE TABLE variants (rsid TEXT, gene TEXT, genotype TEXT, effect_allele TEXT, effect_copies INT);
        INSERT INTO variants VALUES ('rs900001', 'TESTG', 'GG', 'A', 0), ('rs900002', 'OTHER', 'AG', 'A', 1);
        INSERT INTO category_insights VALUES (1, 'TESTG hom-alt slows the pathway. OTHER carries one risk allele.',
            'rs900001 carries no copy of the risk allele.', NULL);
        INSERT INTO unified_protocol VALUES ('• Item — because of the TESTG risk genotype');""")
    hits = carried_claims(c)
    assert [(w, r) for w, r, *_ in hits] == [("category 1", "rs900001"), ("routine", "rs900001")], hits
    # promoted rows: same letters on the other strand pass; different letters fail; absent is a report
    c.execute("DELETE FROM variants")
    c.execute("INSERT INTO variants VALUES ('rs900003','X','CT',NULL,NULL), ('rs900004','Y','AA',NULL,NULL), "
              "('rs900005','Z','GG',NULL,NULL)")
    fake = lambda path, ids, offline: ({"rs900003": "GA", "rs900004": "AG"}, "vcf", "38", {})
    bad, unread = promoted_vs_genome(c, path=Path("x.vcf"), lookup=fake)
    assert bad == ["rs900004: recorded AA, genome file says AG"] and len(unread) == 1 and "rs900005" in unread[0], (bad, unread)
    print("self-check OK")


def main():
    if "--self-check" in sys.argv:
        return self_check()
    conn = sqlite3.connect(DB)
    hits = carried_claims(conn)
    bad, unread = promoted_vs_genome(conn)
    for where, rsid, what, sentence in hits:
        print(f"✗ {where}: {rsid} {what} written up as carried — \"{sentence}\"")
    for line in bad:
        print(f"✗ {line}")
    for line in unread:
        print(f"  (report) {line}")
    if not (hits or bad):
        print("OK — no 0-copy variant written up as carried; promoted rows match the genome")
    return 1 if hits or bad else 0


if __name__ == "__main__":
    sys.exit(main())
