"""Prepare a first-paint snapshot for the two standalone ranking pages."""
import csv
import json
import subprocess
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def snapshot(data_dir, data_stamp, catalog_stamp):
    with (data_dir / "citations.json").open(encoding="utf-8") as handle:
        data = json.load(handle)
    with (data_dir / "neurips2026.csv").open(encoding="utf-8", newline="") as handle:
        total = sum(1 for _ in csv.DictReader(handle))
    counts = [0] * 6
    citations = matched = 0
    leaders = []
    for paper in data["papers"]:
        notes = [note for note in paper["notes"] if note["conference"] == "NeurIPS2026"]
        if not notes:
            continue
        number = paper["citations"]
        bucket = sum(number >= threshold for threshold in (1, 5, 10, 25, 100))
        counts[bucket] += 1
        matched += len(notes)
        citations += number
        if len(leaders) < 5:
            note = notes[0]
            leaders.append(dict(title=note.get("title") or paper["title"], url=note["path"], citations=number))
    return dict(total=total, matched=matched, citations=citations, counts=counts, leaders=leaders,
                date=data["generated_at"][:10], works=data["matched_works"], all_citations=data["total_citations"])


def on_page_context(context, page, config, **kwargs):
    if page.meta.get("template") == "citation-atlas.html":
        directory = Path(config.docs_dir) / "assets" / "data"
        context["atlas"] = snapshot(directory, (directory / "citations.json").stat().st_mtime_ns,
                                    (directory / "neurips2026.csv").stat().st_mtime_ns)
    return context


def on_post_build(config, **kwargs):
    script = Path(__file__).resolve().parents[1] / "scripts" / "build_ranking_pages.cjs"
    subprocess.run(["node", str(script), str(Path(config.site_dir) / "assets" / "data")], check=True)
