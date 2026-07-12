#!/usr/bin/env python3
"""Backfill chrom + dual-build positions (GRCh37 & GRCh38) into
data/known_variants.json from Ensembl, so import_dna.py can match un-annotated
VCFs (empty ID column) by chrom:pos on either build.

    python3 enrich_variants.py            # fill entries missing coords
    python3 enrich_variants.py --refresh  # refetch coords for every entry

Needs network (Ensembl REST) — run it once after adding rsIDs to the catalogue.
import_dna.py itself stays offline / stdlib-only.
"""
import json
import sys
import urllib.request
from pathlib import Path

CAT = Path(__file__).parent / "data" / "known_variants.json"
ENDPOINTS = {"38": "https://rest.ensembl.org", "37": "https://grch37.rest.ensembl.org"}
CANON = {str(i) for i in range(1, 23)} | {"X", "Y", "MT"}   # drop patch/haplotype contigs


def fetch(base, ids):
    req = urllib.request.Request(
        base + "/variation/human",
        data=json.dumps({"ids": ids}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def primary_mapping(v):
    for m in (v or {}).get("mappings", []):
        if m.get("seq_region_name") in CANON:
            return m
    return None


def main():
    refresh = "--refresh" in sys.argv
    cat = json.loads(CAT.read_text())
    ids = [rs for rs in cat if not rs.startswith("_")
           and (refresh or not cat[rs].get("pos") or not cat[rs].get("chrom"))]
    if not ids:
        print("Nothing to fetch — all entries have coords. Use --refresh to refetch.")
        return
    print(f"Fetching coords for {len(ids)} rsIDs from Ensembl (GRCh37 + GRCh38)...")
    got = {b: fetch(base, ids) for b, base in ENDPOINTS.items()}

    filled = missing = 0
    for rs in ids:
        m37, m38 = primary_mapping(got["37"].get(rs)), primary_mapping(got["38"].get(rs))
        pos = {}
        if m37:
            pos["37"] = m37["start"]
        if m38:
            pos["38"] = m38["start"]
        chrom = (m38 or m37 or {}).get("seq_region_name")
        if pos and chrom:
            cat[rs]["chrom"], cat[rs]["pos"] = chrom, pos
            filled += 1
        else:
            missing += 1
            print(f"  ! no canonical mapping for {rs}")

    CAT.write_text(json.dumps(cat, ensure_ascii=False, indent=1))
    print(f"Filled {filled} entries ({missing} unmapped). "
          "Un-annotated VCFs can now match by position — re-run import_dna.py.")


if __name__ == "__main__":
    main()
