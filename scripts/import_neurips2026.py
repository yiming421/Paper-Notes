"""Import NeurIPS 2026 paper metadata from the official program and OpenReview/arXiv crosswalk.

Inputs: neurips-2026-orals-posters.json (neurips.cc) and the default Parquet
split of ai-conferences/NeurIPS2026. Only regeneration requires pyarrow.
"""
import argparse
import csv
import html
import json
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from update_citation_leaderboard import load_neurips_versions, normalize_title

GROUP = "https://openreview.net/group?id=NeurIPS.cc/2026/"
TRACKS = {
    GROUP + "Conference": "main",
    GROUP + "Evaluations_and_Datasets_Track": "evaluations_and_datasets",
    GROUP + "Position_Paper_Track": "position",
}
FIELDS = ("forum", "poster", "track", "area", "title", "arxiv", "arxiv_source")


def build_catalog(events, metadata):
    crosswalk = {row["paper_id"]: row for row in metadata}
    papers = {}
    for event in events:
        track = TRACKS.get(event["sourceurl"])
        if not track or event["eventtype"] != "Poster":
            continue
        link = urlsplit(event["paper_url"] or "")
        forum = parse_qs(link.query).get("id", [""])[0]
        if link.hostname != "openreview.net" or not re.fullmatch(r"[\w-]{10}", forum):
            raise ValueError(f"Missing OpenReview forum for poster {event['id']}")
        title = " ".join(html.unescape(event["name"]).split())
        cross = crosswalk.get(forum, {})
        if cross and normalize_title(cross["title"]) != normalize_title(title):
            cross = {}
        arxiv = cross.get("arxiv_id") or ""
        arxiv_source = "openreview_crosswalk" if arxiv else ""
        if not arxiv:
            match = re.fullmatch(r"https?://arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:\.pdf)?/?", event.get("url") or "")
            if match:
                arxiv = match.group(1)
                arxiv_source = "conference"
        if arxiv and not re.fullmatch(r"\d{4}\.\d{4,5}", arxiv):
            raise ValueError(f"Invalid arXiv ID for forum {forum}")
        topic = (event.get("topic") or "").split("->")[0]
        area = cross.get("primary_area") or re.sub(r"[^a-z0-9]+", "_", topic.casefold()).strip("_") or "others"
        row = {
            "forum": forum,
            "poster": int(event["id"]),
            "track": track,
            "area": area,
            "title": title,
            "arxiv": arxiv,
            "arxiv_source": arxiv_source,
        }
        if forum in papers and papers[forum] != row:
            raise ValueError(f"Conflicting posters for {forum}")
        papers[forum] = row
    if set(TRACKS.values()) != {row["track"] for row in papers.values()}:
        raise ValueError("Program is missing an expected paper track")
    return sorted(papers.values(), key=lambda row: row["forum"])


def enrich_from_arxiv(rows, events, parquet):
    """Fill missing identifiers on a unique exact title and a named author."""
    wanted = {}
    authors = {event["id"]: event["authors"] for event in events if event["eventtype"] == "Poster"}
    for row in rows:
        if not row["arxiv"]:
            wanted.setdefault(normalize_title(row["title"]), []).append(row)
    candidates = {}
    date_col = parquet.schema.names.index("first_version_date")
    for group in range(parquet.num_row_groups):
        dates = parquet.metadata.row_group(group).column(date_col).statistics
        if dates and dates.has_min_max and dates.max.year < 2024:
            continue
        table = parquet.read_row_group(group, columns=["paper_id", "title", "authors"], use_threads=False)
        for arxiv, title, names in zip(*(table[key].to_pylist() for key in ("paper_id", "title", "authors"))):
            if not re.fullmatch(r"\d{4}\.\d{4,5}", arxiv):
                continue
            key = normalize_title(title)
            if key not in wanted:
                continue
            author_text = " " + normalize_title(names or "") + " "
            for row in wanted[key]:
                official = [normalize_title(author["fullname"]) for author in authors[row["poster"]]]
                if any(len(name.split()) >= 2 and " " + name + " " in author_text for name in official):
                    candidates.setdefault(row["forum"], set()).add(arxiv)
    for row in rows:
        matches = candidates.get(row["forum"], set())
        if len(matches) == 1:
            row["arxiv"] = matches.pop()
            row["arxiv_source"] = "arxiv_title_author"


def apply_verified_arxiv(rows, versions):
    prefix = "10.48550/arxiv."
    for row in rows:
        if row["arxiv"]:
            continue
        dois = versions.get((row["forum"], normalize_title(row["title"])), ())
        ids = {doi[len(prefix):] for doi in dois if doi.startswith(prefix)}
        if len(ids) == 1:
            row["arxiv"] = ids.pop()
            row["arxiv_source"] = "version_verified"


if __name__ == "__main__":
    import pyarrow.parquet as pq

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", type=Path)
    parser.add_argument("crosswalk", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--arxiv-metadata", type=Path, help="optional arXiv title and author registry Parquet")
    args = parser.parse_args()
    program = json.loads(args.program.read_bytes())
    if program.get("next") or program["count"] != len(program["results"]):
        raise ValueError("Incomplete conference program")
    metadata = pq.read_table(args.crosswalk, columns=["paper_id", "title", "primary_area", "arxiv_id"]).to_pylist()
    rows = build_catalog(program["results"], metadata)
    if args.arxiv_metadata:
        enrich_from_arxiv(rows, program["results"], pq.ParquetFile(args.arxiv_metadata))
    apply_verified_arxiv(rows, load_neurips_versions(args.output.parent))
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows):,} conference papers, {sum(bool(row['arxiv']) for row in rows):,} arXiv identifiers")
