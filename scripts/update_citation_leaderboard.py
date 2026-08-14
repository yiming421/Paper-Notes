#!/usr/bin/env python3
"""Build the Paper Notes citation leaderboard from Semantic Scholar.

The generator has no third-party dependencies. It scans paper notes, resolves
them to Semantic Scholar papers, refreshes citation counts, and writes the JSON
consumed by the MkDocs leaderboard page.

Matching is deliberately conservative:

1. DOI and arXiv identifiers are resolved with the 500-paper batch endpoint.
2. Unidentified papers may use the single-result title-match endpoint.
3. Every returned title is checked locally before a match is accepted.

The public API works without a key. A key is optional and avoids contention in
the shared anonymous request pool. Successful results are checkpointed so a
rate limit or interrupted bootstrap never discards completed work.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import difflib
import html
import json
import os
import re
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence


API_ROOT = "https://api.semanticscholar.org/graph/v1"
CACHE_VERSION = 1
DATA_VERSION = 2
BATCH_SIZE = 500
PAPER_FIELDS = (
    "paperId,title,url,year,citationCount,influentialCitationCount,externalIds,"
    "publicationTypes,publicationDate,venue"
)
CONFERENCE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*?(?P<year>20\d{2})$")
PAPER_ID_RE = re.compile(r"\b[0-9a-f]{40}\b", re.IGNORECASE)
HAN_RE = re.compile(r"[\u3400-\u9fff]")
ARXIV_RE = re.compile(
    r"(?:arxiv\.org/(?:abs|pdf)/|arxiv\s*:\s*)"
    r"(?P<id>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7}))"
    r"(?:v\d+)?",
    re.IGNORECASE,
)
DOI_URL_RE = re.compile(
    r"https?://(?:dx\.)?doi\.org/(?P<doi>10\.\d{4,9}/[^\s<>\])}]+)",
    re.IGNORECASE,
)
DOI_TEXT_RE = re.compile(
    r"(?:^|[\s\[(])(?P<doi>10\.\d{4,9}/[-._;()/:A-Z0-9]+)",
    re.IGNORECASE,
)
ACL_ANTHOLOGY_RE = re.compile(
    r"https?://aclanthology\.org/(?P<id>\d{4}\.[a-z0-9-]+\.\d+)/?",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(r"\[([^]]+)]\([^)]*\)")
MARKDOWN_TAG_RE = re.compile(r"<[^>]+>")
LATEX_COMMAND_RE = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname)\s*\{([^{}]*)}")


@dataclasses.dataclass(frozen=True, slots=True)
class Note:
    """Metadata extracted from one paper-note Markdown file."""

    path: str
    title: str
    title_key: str
    conference: str
    area: str
    conference_year: int
    dois: tuple[str, ...]

    @property
    def url(self) -> str:
        return str(Path(self.path).with_suffix("")) + "/"


class SemanticScholarError(RuntimeError):
    """An unrecoverable Semantic Scholar response."""


class SemanticScholarRateLimited(SemanticScholarError):
    """The API stayed rate-limited after all retries."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_date(value: str | None) -> dt.date | None:
    if not value:
        return None
    try:
        return dt.date.fromisoformat(value[:10])
    except ValueError:
        return None


def strip_markdown(value: str) -> str:
    value = MARKDOWN_LINK_RE.sub(r"\1", value)
    value = MARKDOWN_TAG_RE.sub(" ", value)
    value = LATEX_COMMAND_RE.sub(r"\1", value)
    value = value.replace("$", "").replace("`", "").replace("**", "")
    value = re.sub(r"\s+\{#[^}]+}\s*$", "", value)
    return html.unescape(value).strip()


def normalize_title(value: str) -> str:
    """Return a punctuation-insensitive key suitable for strict matching."""

    value = strip_markdown(value)
    value = unicodedata.normalize("NFKC", value).casefold()
    value = value.replace("&", " and ")
    value = value.replace("^", "").replace("_", "")
    value = "".join(
        character if character.isalnum() else " " for character in value
    )
    return " ".join(value.split())


def normalize_doi(value: str) -> str:
    value = html.unescape(value).strip().lower()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value)
    return value.rstrip(".,;:)]}")


def semantic_scholar_paper_id(value: str | None) -> str | None:
    if not value:
        return None
    match = PAPER_ID_RE.search(value)
    return match.group(0).lower() if match else None


def semantic_scholar_identifier(doi: str) -> str:
    prefix = "10.48550/arxiv."
    if doi.casefold().startswith(prefix):
        return "ARXIV:" + doi[len(prefix) :]
    return "DOI:" + doi


def extract_title(markdown: str) -> str | None:
    in_frontmatter = markdown.startswith("---\n") or markdown.startswith("---\r\n")
    frontmatter_closed = not in_frontmatter
    for index, line in enumerate(markdown.splitlines()):
        if in_frontmatter and not frontmatter_closed:
            if line.strip() == "---" and index > 0:
                frontmatter_closed = True
            continue
        if line.startswith("# "):
            title = strip_markdown(line[2:].strip())
            title = re.sub(r"^\[论文解读]\s*", "", title)
            return title or None
    return None


def extract_dois(markdown: str) -> tuple[str, ...]:
    """Extract identifiers in preference order (published DOI before arXiv)."""

    found: list[str] = []
    for match in DOI_URL_RE.finditer(markdown):
        found.append(normalize_doi(match.group("doi")))

    for line in markdown.splitlines()[:60]:
        if "doi" not in line.casefold():
            continue
        match = DOI_TEXT_RE.search(line)
        if match:
            found.append(normalize_doi(match.group("doi")))

    for match in ACL_ANTHOLOGY_RE.finditer(markdown):
        found.append("10.18653/v1/" + match.group("id").lower())

    for match in ARXIV_RE.finditer(markdown):
        found.append("10.48550/arxiv." + match.group("id").lower())

    unique = list(dict.fromkeys(doi for doi in found if doi.startswith("10.")))
    unique.sort(key=lambda doi: (doi.startswith("10.48550/arxiv."), doi))
    return tuple(unique)


def scan_notes(docs_dir: Path) -> list[Note]:
    notes: list[Note] = []
    for path in sorted(docs_dir.glob("*/*/*.md")):
        if path.name == "index.md":
            continue
        relative = path.relative_to(docs_dir)
        conference, area, _ = relative.parts
        conference_match = CONFERENCE_RE.match(conference)
        if not conference_match:
            continue
        markdown = path.read_text(encoding="utf-8")
        title = extract_title(markdown)
        if not title:
            print(f"warning: no H1 title in {relative}", file=sys.stderr)
            continue
        notes.append(
            Note(
                path=relative.as_posix(),
                title=title,
                title_key=normalize_title(title),
                conference=conference,
                area=area,
                conference_year=int(conference_match.group("year")),
                dois=extract_dois(markdown),
            )
        )
    return notes


def empty_cache() -> dict[str, Any]:
    return {
        "version": CACHE_VERSION,
        "source": "Semantic Scholar",
        "updated_at": None,
        "matches": {},
        "papers": {},
    }


def load_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return empty_cache()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read cache {path}: {error}") from error
    if value.get("version") != CACHE_VERSION:
        raise SystemExit(
            f"unsupported cache version in {path}; expected {CACHE_VERSION}"
        )
    if value.get("source") not in {None, "Semantic Scholar"}:
        raise SystemExit(f"cache {path} belongs to a different citation source")
    value["source"] = "Semantic Scholar"
    value.setdefault("matches", {})
    value.setdefault("papers", {})
    return value


def safe_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def compact_paper(
    raw: dict[str, Any], *, retrieved_at: str | None = None
) -> dict[str, Any] | None:
    paper_id = semantic_scholar_paper_id(raw.get("paperId"))
    title = raw.get("title")
    if not paper_id or not isinstance(title, str) or not title.strip():
        return None

    external_ids = {
        str(key): value
        for key, value in (raw.get("externalIds") or {}).items()
        if isinstance(value, (str, int))
    }
    doi_value = external_ids.get("DOI")
    doi = normalize_doi(str(doi_value)) if doi_value else None
    try:
        year = int(raw["year"]) if raw.get("year") is not None else None
    except (TypeError, ValueError):
        year = None
    publication_types = raw.get("publicationTypes")
    if not isinstance(publication_types, list):
        publication_types = []
    url = raw.get("url")
    if not isinstance(url, str) or not url.startswith("https://"):
        url = f"https://www.semanticscholar.org/paper/{paper_id}"

    return {
        "id": paper_id,
        "title": title.strip(),
        "citation_count": safe_nonnegative_int(raw.get("citationCount")),
        "influential_citation_count": safe_nonnegative_int(
            raw.get("influentialCitationCount")
        ),
        "year": year,
        "doi": doi,
        "external_ids": external_ids,
        "publication_types": [str(value) for value in publication_types],
        "publication_date": raw.get("publicationDate"),
        "venue": raw.get("venue"),
        "url": url,
        "retrieved_at": retrieved_at,
    }


def chunks(values: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for index in range(0, len(values), size):
        yield values[index : index + size]


class SemanticScholarClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 45.0,
        max_retries: int = 5,
        request_interval: float = 1.05,
    ) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.request_interval = max(0.0, request_interval)
        self.calls = 0
        self.attempts = 0
        self.rate_limit_retries = 0
        self._last_request_started = 0.0

    def make_url(self, endpoint: str, params: dict[str, Any] | None = None) -> str:
        query = urllib.parse.urlencode(params or {})
        suffix = f"?{query}" if query else ""
        return f"{API_ROOT}/{endpoint.lstrip('/')}{suffix}"

    def _wait_for_slot(self) -> None:
        elapsed = time.monotonic() - self._last_request_started
        remaining = self.request_interval - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_started = time.monotonic()

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> Any:
        url = self.make_url(endpoint, params)
        headers = {
            "Accept": "application/json",
            "User-Agent": "Paper-Notes-Citation-Leaderboard/2.0",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":")).encode("utf-8")
        if self.api_key:
            headers["x-api-key"] = self.api_key

        for attempt in range(self.max_retries + 1):
            self._wait_for_slot()
            self.attempts += 1
            request = urllib.request.Request(
                url, data=data, headers=headers, method=method.upper()
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                    self.calls += 1
                    return payload
            except urllib.error.HTTPError as error:
                body_text = error.read().decode("utf-8", errors="replace")[:1_000]
                if error.code == 404 and allow_not_found:
                    self.calls += 1
                    return None
                if error.code == 429:
                    if attempt >= self.max_retries:
                        raise SemanticScholarRateLimited(
                            "Semantic Scholar stayed rate-limited after retries"
                        ) from error
                    retry_after = error.headers.get("Retry-After")
                    try:
                        wait_seconds = float(retry_after) if retry_after else 0.0
                    except ValueError:
                        wait_seconds = 0.0
                    wait_seconds = max(wait_seconds, min(2**attempt, 30))
                    self.rate_limit_retries += 1
                    print(
                        f"warning: Semantic Scholar returned 429; retrying in "
                        f"{wait_seconds:g}s",
                        file=sys.stderr,
                    )
                    time.sleep(wait_seconds)
                    continue
                if error.code in {500, 502, 503, 504} and attempt < self.max_retries:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise SemanticScholarError(
                    f"Semantic Scholar HTTP {error.code} for {endpoint}: {body_text}"
                ) from error
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 30))
                    continue
                raise SemanticScholarError(
                    f"Semantic Scholar request failed: {error}"
                ) from error
        raise AssertionError("retry loop unexpectedly exhausted")

    def batch_papers(self, paper_ids: Sequence[str]) -> list[dict[str, Any] | None]:
        if len(paper_ids) > BATCH_SIZE:
            raise ValueError(f"paper batch exceeds {BATCH_SIZE} identifiers")
        try:
            payload = self.request(
                "POST",
                "paper/batch",
                params={"fields": PAPER_FIELDS},
                body={"ids": list(paper_ids)},
            )
        except SemanticScholarError as error:
            # The API returns HTTP 400, rather than a same-length list of
            # nulls, when every external identifier in a batch is unknown.
            if "HTTP 400" in str(error) and "No valid paper ids given" in str(error):
                return [None] * len(paper_ids)
            raise
        if not isinstance(payload, list) or len(payload) != len(paper_ids):
            raise SemanticScholarError(
                "Semantic Scholar returned an unexpected paper batch response"
            )
        return [value if isinstance(value, dict) else None for value in payload]

    def match_title(self, title: str, year: int) -> dict[str, Any] | None:
        payload = self.request(
            "GET",
            "paper/search/match",
            params={
                "query": title,
                "year": f"{year - 1}-{year + 1}",
                "fields": PAPER_FIELDS,
            },
            allow_not_found=True,
        )
        if payload is None:
            return None
        values = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(values, list) and values and isinstance(values[0], dict):
            return values[0]
        return None


def cache_paper(
    cache: dict[str, Any], raw: dict[str, Any], *, retrieved_at: str
) -> str | None:
    paper = compact_paper(raw, retrieved_at=retrieved_at)
    if not paper:
        return None
    cache["papers"][paper["id"]] = paper
    return paper["id"]


def title_similarity(left: str, right: str) -> float:
    return difflib.SequenceMatcher(None, left, right, autojunk=False).ratio()


def is_published_paper(raw: dict[str, Any]) -> bool:
    external_ids = raw.get("externalIds") or {}
    doi = str(external_ids.get("DOI") or "")
    return bool(doi and "10.48550/arxiv." not in doi.casefold())


def choose_paper(
    target_title_key: str,
    conference_year: int,
    raw_candidates: Iterable[dict[str, Any]],
    *,
    identifier_match: bool = False,
) -> tuple[dict[str, Any], str, float] | None:
    """Choose a locally verified paper from upstream candidates."""

    fuzzy_threshold = 0.68 if identifier_match else 0.96
    overlap_threshold = 0.50 if identifier_match else 0.90
    year_tolerance = 3 if identifier_match else 1
    candidates: list[tuple[dict[str, Any], float, bool]] = []
    for raw in raw_candidates:
        title = raw.get("title")
        if not isinstance(title, str):
            continue
        candidate_key = normalize_title(title)
        if not candidate_key:
            continue
        exact = candidate_key == target_title_key
        similarity = 1.0 if exact else title_similarity(target_title_key, candidate_key)
        try:
            publication_year = int(raw.get("year"))
        except (TypeError, ValueError):
            publication_year = 0
        if not exact:
            target_tokens = set(target_title_key.split())
            candidate_tokens = set(candidate_key.split())
            shared_tokens = len(target_tokens & candidate_tokens)
            if identifier_match:
                # An exact DOI/arXiv lookup is strong evidence even when a
                # conference version renames or extends the preprint title.
                # Use containment so added subtitles do not look unrelated,
                # while still rejecting a genuinely wrong linked identifier.
                token_overlap = shared_tokens / max(
                    1, min(len(target_tokens), len(candidate_tokens))
                )
            else:
                token_overlap = shared_tokens / max(
                    1, len(target_tokens | candidate_tokens)
                )
            if (
                similarity < fuzzy_threshold
                or token_overlap < overlap_threshold
                or (
                    publication_year
                    and abs(publication_year - conference_year) > year_tolerance
                )
            ):
                continue
        candidates.append((raw, similarity, exact))

    if not candidates:
        return None

    def preference(item: tuple[dict[str, Any], float, bool]) -> tuple[Any, ...]:
        raw, similarity, exact = item
        try:
            distance = abs(int(raw.get("year")) - conference_year)
        except (TypeError, ValueError):
            distance = 99
        return (
            int(exact),
            int(is_published_paper(raw)),
            -distance,
            similarity,
            safe_nonnegative_int(raw.get("citationCount")),
        )

    raw, similarity, exact = max(candidates, key=preference)
    method = "title_exact" if exact else "title_fuzzy"
    return raw, method, round(similarity, 4)


def choose_identifier_paper(
    note: Note, raw_candidates: Sequence[dict[str, Any]]
) -> tuple[dict[str, Any], str, float] | None:
    """Validate an exact ID against the H1 or its English filename slug.

    Some 2026 notes translate the displayed H1 into Chinese while retaining
    the original English paper title in the filename. The slug provides a
    useful independent check without blindly trusting a potentially wrong ID.
    """

    selection = choose_paper(
        note.title_key,
        note.conference_year,
        raw_candidates,
        identifier_match=True,
    )
    if selection:
        return selection
    slug = Path(note.path).stem.replace("_", " ")
    slug_key = normalize_title(slug)
    if not slug_key or slug_key == note.title_key:
        return None
    return choose_paper(
        slug_key,
        note.conference_year,
        raw_candidates,
        identifier_match=True,
    )


def note_title_query(note: Note) -> str:
    """Use the original English filename when the visible H1 is translated."""

    if HAN_RE.search(note.title):
        return Path(note.path).stem.replace("_", " ")
    return note.title


def choose_title_paper(
    note: Note, raw_candidates: Sequence[dict[str, Any]]
) -> tuple[dict[str, Any], str, float] | None:
    selection = choose_paper(note.title_key, note.conference_year, raw_candidates)
    if selection or not HAN_RE.search(note.title):
        return selection
    # For translated notes, the search itself used the original English slug.
    # Validate with the same conservative containment rule used for exact IDs.
    slug_key = normalize_title(Path(note.path).stem.replace("_", " "))
    return choose_paper(
        slug_key,
        note.conference_year,
        raw_candidates,
        identifier_match=True,
    )


def refresh_known_papers(
    client: SemanticScholarClient,
    cache: dict[str, Any],
    *,
    retrieved_at: str,
    paper_ids: Iterable[str] | None = None,
) -> tuple[int, set[str]]:
    if paper_ids is None:
        requested = sorted(
            {
                match.get("paper_id")
                for match in cache["matches"].values()
                if match.get("paper_id")
            }
        )
    else:
        requested = sorted(set(paper_ids))
    refreshed = 0
    missing: set[str] = set()
    for batch in chunks(requested, BATCH_SIZE):
        results = client.batch_papers(batch)
        for requested_id, raw in zip(batch, results):
            if raw and cache_paper(cache, raw, retrieved_at=retrieved_at):
                refreshed += 1
            else:
                missing.add(requested_id)
    return refreshed, missing


def resolve_identifiers(
    client: SemanticScholarClient,
    cache: dict[str, Any],
    notes: Sequence[Note],
    *,
    attempted_on: str,
    retrieved_at: str,
    checkpoint: Callable[[], None] | None = None,
) -> int:
    """Resolve notes by published DOI first, then arXiv ID."""

    unresolved = {note.path for note in notes}
    notes_by_path = {note.path: note for note in notes}
    resolved = 0
    max_identifiers = max((len(note.dois) for note in notes), default=0)
    for identifier_index in range(max_identifiers):
        by_identifier: dict[str, list[str]] = defaultdict(list)
        for path in sorted(unresolved):
            note = notes_by_path[path]
            if identifier_index < len(note.dois):
                by_identifier[
                    semantic_scholar_identifier(note.dois[identifier_index])
                ].append(path)

        identifiers = sorted(by_identifier)
        for batch in chunks(identifiers, BATCH_SIZE):
            results = client.batch_papers(batch)
            for requested_id, raw in zip(batch, results):
                if not raw:
                    continue
                for path in by_identifier[requested_id]:
                    note = notes_by_path[path]
                    selection = choose_identifier_paper(note, [raw])
                    if not selection:
                        continue
                    selected, _title_method, score = selection
                    paper_id = cache_paper(
                        cache, selected, retrieved_at=retrieved_at
                    )
                    if not paper_id:
                        continue
                    method = "arxiv" if requested_id.startswith("ARXIV:") else "doi"
                    cache["matches"][path] = {
                        "title_key": note.title_key,
                        "paper_id": paper_id,
                        "match_method": method,
                        "match_score": score,
                        "identifier": requested_id,
                        "identifier_checked": attempted_on,
                        "last_attempted": attempted_on,
                        "title_checked": None,
                    }
                    unresolved.discard(path)
                    resolved += 1
            if checkpoint:
                checkpoint()
    for path in sorted(unresolved):
        note = notes_by_path[path]
        if not note.dois:
            continue
        cache["matches"][path] = {
            "title_key": note.title_key,
            "paper_id": None,
            "match_method": "identifier_unmatched",
            "match_score": None,
            "identifier": None,
            "identifier_checked": attempted_on,
            "last_attempted": attempted_on,
            "title_checked": None,
        }
    if checkpoint and unresolved:
        checkpoint()
    return resolved


def match_titles(
    client: SemanticScholarClient,
    cache: dict[str, Any],
    notes: Sequence[Note],
    *,
    attempted_on: str,
    retrieved_at: str,
    max_requests: int | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> tuple[int, int]:
    by_key: dict[tuple[str, int], list[Note]] = defaultdict(list)
    for note in notes:
        by_key[(note.title_key, note.conference_year)].append(note)

    matched = 0
    requests = 0
    for key in sorted(by_key):
        if max_requests is not None and requests >= max_requests:
            break
        grouped_notes = by_key[key]
        target = grouped_notes[0]
        raw = client.match_title(note_title_query(target), target.conference_year)
        requests += 1
        selection = (
            choose_title_paper(target, [raw])
            if raw
            else None
        )
        for note in grouped_notes:
            if selection:
                selected, method, score = selection
                paper_id = cache_paper(cache, selected, retrieved_at=retrieved_at)
                if paper_id:
                    cache["matches"][note.path] = {
                        "title_key": note.title_key,
                        "paper_id": paper_id,
                        "match_method": method,
                        "match_score": score,
                        "identifier": None,
                        "last_attempted": attempted_on,
                        "title_checked": attempted_on,
                    }
                    matched += 1
                    continue
            cache["matches"][note.path] = {
                "title_key": note.title_key,
                "paper_id": None,
                "match_method": "unmatched",
                "match_score": None,
                "identifier": None,
                "last_attempted": attempted_on,
                "title_checked": attempted_on,
            }
        if requests % 50 == 0:
            print(
                f"title progress: {requests:,} requests, {matched:,} notes matched",
                flush=True,
            )
        if checkpoint and requests % 100 == 0:
            checkpoint()
    if checkpoint and requests:
        checkpoint()
    return matched, requests


def apply_overrides(
    cache: dict[str, Any],
    notes_by_path: dict[str, Note],
    overrides: dict[str, Any],
    *,
    attempted_on: str,
) -> set[str]:
    fetch_ids: set[str] = set()
    for path, value in overrides.items():
        note = notes_by_path.get(path)
        if not note:
            print(f"warning: override points to missing note: {path}", file=sys.stderr)
            continue
        if value is None:
            cache["matches"][path] = {
                "title_key": note.title_key,
                "paper_id": None,
                "match_method": "manual_exclude",
                "match_score": None,
                "identifier": None,
                "last_attempted": attempted_on,
                "title_checked": attempted_on,
            }
            continue
        paper_id = semantic_scholar_paper_id(str(value))
        if not paper_id:
            raise SystemExit(
                f"invalid Semantic Scholar paper ID override for {path}: {value!r}"
            )
        cache["matches"][path] = {
            "title_key": note.title_key,
            "paper_id": paper_id,
            "match_method": "manual",
            "match_score": 1.0,
            "identifier": None,
            "last_attempted": attempted_on,
            "title_checked": attempted_on,
        }
        fetch_ids.add(paper_id)
    return fetch_ids


def fetch_paper_ids(
    client: SemanticScholarClient,
    cache: dict[str, Any],
    paper_ids: Iterable[str],
    *,
    retrieved_at: str,
) -> int:
    fetched = 0
    values = sorted(set(paper_ids))
    for batch in chunks(values, BATCH_SIZE):
        for raw in client.batch_papers(batch):
            if raw and cache_paper(cache, raw, retrieved_at=retrieved_at):
                fetched += 1
    return fetched


def should_search_title(
    note: Note,
    match: dict[str, Any] | None,
    *,
    today: dt.date,
    retry_after_days: int,
) -> bool:
    if not match or match.get("title_key") != note.title_key:
        return True
    if match.get("match_method") in {
        "manual",
        "manual_exclude",
        "doi",
        "arxiv",
        "title_exact",
        "title_fuzzy",
    }:
        return False
    checked = parse_iso_date(match.get("title_checked"))
    return checked is None or (today - checked).days >= retry_after_days


def should_resolve_identifier(
    note: Note,
    match: dict[str, Any] | None,
    *,
    today: dt.date,
    retry_after_days: int,
) -> bool:
    if not note.dois:
        return False
    if not match or match.get("title_key") != note.title_key:
        return True
    if match.get("paper_id") or match.get("match_method") == "manual_exclude":
        return False
    checked = parse_iso_date(match.get("identifier_checked"))
    return checked is None or (today - checked).days >= retry_after_days


def reconcile_cache(cache: dict[str, Any], notes: Sequence[Note]) -> None:
    notes_by_path = {note.path: note for note in notes}
    cache["matches"] = {
        path: match
        for path, match in cache["matches"].items()
        if path in notes_by_path
    }
    for path, match in list(cache["matches"].items()):
        if match.get("title_key") != notes_by_path[path].title_key:
            del cache["matches"][path]


def prune_unused_papers(cache: dict[str, Any]) -> None:
    used = {
        match.get("paper_id")
        for match in cache["matches"].values()
        if match.get("paper_id")
    }
    cache["papers"] = {
        paper_id: paper
        for paper_id, paper in cache["papers"].items()
        if paper_id in used
    }


def build_public_data(
    notes: Sequence[Note],
    cache: dict[str, Any],
    *,
    generated_at: dt.datetime,
) -> dict[str, Any]:
    notes_by_path = {note.path: note for note in notes}
    grouped_notes: dict[str, list[Note]] = defaultdict(list)
    matched_note_count = 0
    match_methods: dict[str, int] = defaultdict(int)
    for path, match in cache["matches"].items():
        paper_id = match.get("paper_id")
        if path not in notes_by_path or not paper_id or paper_id not in cache["papers"]:
            continue
        grouped_notes[paper_id].append(notes_by_path[path])
        matched_note_count += 1
        match_methods[str(match.get("match_method") or "unknown")] += 1

    papers: list[dict[str, Any]] = []
    for paper_id, paper_notes in grouped_notes.items():
        paper = cache["papers"][paper_id]
        paper_notes.sort(key=lambda note: (note.conference, note.area, note.path))
        papers.append(
            {
                "id": paper_id,
                "title": paper["title"],
                "citations": safe_nonnegative_int(paper.get("citation_count")),
                "influential_citations": safe_nonnegative_int(
                    paper.get("influential_citation_count")
                ),
                "year": paper.get("year"),
                "doi": paper.get("doi"),
                "semantic_scholar_url": paper.get("url")
                or f"https://www.semanticscholar.org/paper/{paper_id}",
                "notes": [
                    {
                        "path": note.url,
                        "conference": note.conference,
                        "area": note.area,
                    }
                    for note in paper_notes
                ],
            }
        )

    papers.sort(
        key=lambda paper: (
            -paper["citations"],
            -paper["influential_citations"],
            normalize_title(paper["title"]),
            paper["id"],
        )
    )
    return {
        "version": DATA_VERSION,
        "generated_at": iso_z(generated_at),
        "source": "Semantic Scholar",
        "total_notes": len(notes),
        "matched_notes": matched_note_count,
        "matched_works": len(papers),
        "total_citations": sum(paper["citations"] for paper in papers),
        "total_influential_citations": sum(
            paper["influential_citations"] for paper in papers
        ),
        "match_methods": dict(sorted(match_methods.items())),
        "papers": papers,
    }


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=path.name + ".", suffix=".tmp", text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
        os.chmod(temporary_name, 0o644)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def render_cache(cache: dict[str, Any]) -> str:
    lines = ["{", f'  "version": {CACHE_VERSION},']
    lines.append('  "source": "Semantic Scholar",')
    lines.append(
        '  "updated_at": '
        + json.dumps(cache.get("updated_at"), ensure_ascii=False)
        + ","
    )
    lines.append('  "matches": {')
    match_items = sorted(cache["matches"].items())
    for index, (path, match) in enumerate(match_items):
        comma = "," if index + 1 < len(match_items) else ""
        lines.append(
            "    "
            + json.dumps(path, ensure_ascii=False)
            + ": "
            + json.dumps(match, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + comma
        )
    lines.append("  },")
    lines.append('  "papers": {')
    paper_items = sorted(cache["papers"].items())
    for index, (paper_id, paper) in enumerate(paper_items):
        comma = "," if index + 1 < len(paper_items) else ""
        lines.append(
            "    "
            + json.dumps(paper_id, ensure_ascii=False)
            + ": "
            + json.dumps(paper, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + comma
        )
    lines.extend(["  }", "}"])
    return "\n".join(lines) + "\n"


def render_public_data(data: dict[str, Any]) -> str:
    header_keys = [
        "version",
        "generated_at",
        "source",
        "total_notes",
        "matched_notes",
        "matched_works",
        "total_citations",
        "total_influential_citations",
        "match_methods",
    ]
    lines = ["{"]
    for key in header_keys:
        lines.append(
            "  "
            + json.dumps(key)
            + ": "
            + json.dumps(data[key], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + ","
        )
    lines.append('  "papers": [')
    for index, paper in enumerate(data["papers"]):
        comma = "," if index + 1 < len(data["papers"]) else ""
        lines.append(
            "    "
            + json.dumps(paper, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            + comma
        )
    lines.extend(["  ]", "}"])
    return "\n".join(lines) + "\n"


def load_overrides(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SystemExit(f"cannot read overrides {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"overrides must be a JSON object: {path}")
    return value


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docs-dir", type=Path, default=Path("docs"))
    parser.add_argument(
        "--cache", type=Path, default=Path("data/semantic-scholar-cache.json")
    )
    parser.add_argument(
        "--output", type=Path, default=Path("docs/assets/data/citations.json")
    )
    parser.add_argument(
        "--overrides",
        type=Path,
        default=Path("data/semantic-scholar-overrides.json"),
    )
    parser.add_argument(
        "--api-key",
        default=os.environ.get("SEMANTIC_SCHOLAR_API_KEY"),
        help="optional key (defaults to SEMANTIC_SCHOLAR_API_KEY)",
    )
    parser.add_argument(
        "--retry-after-days",
        type=int,
        default=30,
        help="retry unmatched title lookups after this many days",
    )
    parser.add_argument(
        "--max-title-requests",
        "--max-search-batches",
        dest="max_title_requests",
        type=int,
        help="limit title-match requests for an incremental bootstrap",
    )
    parser.add_argument(
        "--skip-title-search",
        action="store_true",
        help="refresh known papers and resolve DOI/arXiv identifiers only",
    )
    parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="skip refreshing cached counts while bootstrapping new matches",
    )
    parser.add_argument(
        "--request-interval",
        type=float,
        default=1.05,
        help="minimum seconds between API requests (default: 1.05)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="do not call Semantic Scholar; rebuild public JSON from cache",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="scan and query but do not write cache or output files",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.retry_after_days < 0:
        raise SystemExit("--retry-after-days must be non-negative")
    if args.max_title_requests is not None and args.max_title_requests < 0:
        raise SystemExit("--max-title-requests must be non-negative")
    if args.request_interval < 0:
        raise SystemExit("--request-interval must be non-negative")

    now = utc_now()
    attempted_on = now.date().isoformat()
    retrieved_at = iso_z(now)
    notes = scan_notes(args.docs_dir)
    if not notes:
        raise SystemExit(f"no paper notes found below {args.docs_dir}")
    print(f"scanned {len(notes):,} paper notes")

    cache = load_cache(args.cache)
    reconcile_cache(cache, notes)
    notes_by_path = {note.path: note for note in notes}
    overrides = load_overrides(args.overrides)
    override_ids = apply_overrides(
        cache, notes_by_path, overrides, attempted_on=attempted_on
    )

    def checkpoint() -> None:
        if args.dry_run:
            return
        cache["updated_at"] = retrieved_at
        prune_unused_papers(cache)
        atomic_write(args.cache, render_cache(cache))

    client: SemanticScholarClient | None = None
    rate_limited = False
    fatal_api_error: SemanticScholarError | None = None
    if not args.offline:
        client = SemanticScholarClient(
            args.api_key, request_interval=args.request_interval
        )
        if not args.api_key:
            print(
                "using Semantic Scholar's public API without a key; the shared "
                "anonymous pool may occasionally throttle requests",
                file=sys.stderr,
            )
        try:
            if override_ids:
                fetched = fetch_paper_ids(
                    client, cache, override_ids, retrieved_at=retrieved_at
                )
                print(f"fetched {fetched:,} manual override papers")

            # Prioritize unresolved identifiers before refreshing old counts.
            # This lets anonymous, rate-limited bootstrap runs make forward
            # progress instead of spending every request on the existing cache.
            known_before = {
                match.get("paper_id")
                for match in cache["matches"].values()
                if match.get("paper_id")
            }
            identifier_notes = [
                note
                for note in notes
                if should_resolve_identifier(
                    note,
                    cache["matches"].get(note.path),
                    today=now.date(),
                    retry_after_days=args.retry_after_days,
                )
            ]
            identifier_resolved = resolve_identifiers(
                client,
                cache,
                identifier_notes,
                attempted_on=attempted_on,
                retrieved_at=retrieved_at,
                checkpoint=checkpoint,
            )
            print(f"resolved {identifier_resolved:,} notes by DOI/arXiv ID")
            checkpoint()

            if identifier_notes:
                print(
                    "deferred refreshing older counts until the identifier bootstrap "
                    "is complete"
                )
            elif args.skip_refresh:
                print("skipped refreshing cached counts for this bootstrap run")
            else:
                refreshed, missing_ids = refresh_known_papers(
                    client,
                    cache,
                    retrieved_at=retrieved_at,
                    paper_ids=known_before,
                )
                print(
                    f"refreshed {refreshed:,} previously cached Semantic Scholar papers"
                )
                if missing_ids:
                    print(
                        f"warning: {len(missing_ids):,} cached paper IDs were not returned; "
                        "retaining their last known values",
                        file=sys.stderr,
                    )

            if not args.skip_title_search:
                search_notes = [
                    note
                    for note in notes
                    if should_search_title(
                        note,
                        cache["matches"].get(note.path),
                        today=now.date(),
                        retry_after_days=args.retry_after_days,
                    )
                ]
                max_requests = args.max_title_requests
                if not args.api_key and max_requests is None:
                    max_requests = 100
                    print(
                        "anonymous title matching is capped at 100 requests per run; "
                        "rerun to continue or pass --max-title-requests",
                        file=sys.stderr,
                    )
                print(f"title matching {len(search_notes):,} notes")
                if search_notes:
                    title_matched, request_count = match_titles(
                        client,
                        cache,
                        search_notes,
                        attempted_on=attempted_on,
                        retrieved_at=retrieved_at,
                        max_requests=max_requests,
                        checkpoint=checkpoint,
                    )
                    print(
                        f"matched {title_matched:,} notes in "
                        f"{request_count:,} title requests"
                    )
        except SemanticScholarRateLimited as error:
            rate_limited = True
            print(f"warning: {error}; keeping the completed partial refresh", file=sys.stderr)
        except SemanticScholarError as error:
            fatal_api_error = error
            print(f"error: {error}; keeping the completed partial refresh", file=sys.stderr)

    prune_unused_papers(cache)
    cache["updated_at"] = retrieved_at
    public_data = build_public_data(notes, cache, generated_at=now)
    if not args.dry_run:
        atomic_write(args.cache, render_cache(cache))
        atomic_write(args.output, render_public_data(public_data))

    coverage = (
        100 * public_data["matched_notes"] / public_data["total_notes"]
        if public_data["total_notes"]
        else 0
    )
    print(
        "leaderboard: "
        f"{public_data['matched_works']:,} unique papers, "
        f"{public_data['matched_notes']:,}/{public_data['total_notes']:,} notes matched "
        f"({coverage:.1f}%), {public_data['total_citations']:,} citations, "
        f"{public_data['total_influential_citations']:,} influential"
    )
    if client:
        print(
            f"Semantic Scholar usage: {client.calls:,} successful calls, "
            f"{client.attempts:,} attempts, {client.rate_limit_retries:,} rate-limit retries"
        )
    if fatal_api_error:
        return 1
    if rate_limited:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
