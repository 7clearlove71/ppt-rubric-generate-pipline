"""Exact, addressable source fragments for model-selected citations.

The catalog does not summarize, normalize, merge rows, or discard source text.
Offsets are Python string indices (Unicode code points), with an exclusive end;
every fragment is exactly ``source['text'][start:end]``. CR/LF separators and
whitespace-only lines need no citation IDs. The full ordered catalog is supplied
to a model so that headings, table headers, and neighbouring rows remain visible.
"""

from __future__ import annotations

import hashlib
import copy
import json
import re


# A soft target, not a truncation limit: never cut a token, equation, or URL just
# to fit it. A line without a natural sentence boundary remains a single span.
_TARGET_FRAGMENT_LENGTH = 1200
_LINE = re.compile(r"[^\r\n]+")
_BOUNDARY = re.compile(r'''[。！？；!?;]+[”’」』】）》"'）)]*|\.(?=\s|$)''')


def _fragment_ranges(text, start, end):
    """Split a long source line only at actual, contiguous punctuation cuts."""
    boundaries = [start + match.end() for match in _BOUNDARY.finditer(text[start:end])]
    while end - start > _TARGET_FRAGMENT_LENGTH:
        limit = start + _TARGET_FRAGMENT_LENGTH
        before = [position for position in boundaries if start < position <= limit]
        if before:
            cut = before[-1]
        else:
            after = [position for position in boundaries if position > limit]
            if not after:
                break
            cut = after[0]
        # Do not leave a standalone whitespace fragment after a final sentence.
        if not text[cut:end].strip():
            break
        yield start, cut
        start = cut
    if text[start:end].strip():
        yield start, end


def _fragment_id(source_id, text, start, end):
    payload = json.dumps([source_id, start, end, text], ensure_ascii=False,
                         separators=(",", ":")).encode("utf-8")
    return "src_" + hashlib.sha256(payload).hexdigest()[:24]


def source_catalog(sources):
    """Build stable exact spans from ``[{'id': ..., 'text': ...}, ...]``.

    Nonempty original lines form the normal citation units. Only long lines are
    split at natural sentence punctuation; pieces are never joined across a
    newline or a gap. Original whitespace, punctuation, tables, and Unicode are
    retained verbatim. IDs include the source identity, range, and exact text,
    so equal text on different rows or in different sources remains distinct.
    """
    if not isinstance(sources, list):
        raise ValueError("Sources must be a list")
    catalog = []
    seen_sources = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("Each source must contain an id and text")
        source_id, text = source.get("id"), source.get("text")
        if not isinstance(source_id, str) or not source_id.strip():
            raise ValueError("Source id must be a nonempty string")
        if source_id in seen_sources:
            raise ValueError(f"Duplicate source id: {source_id}")
        if not isinstance(text, str):
            raise ValueError(f"Source text must be a string: {source_id}")
        seen_sources.add(source_id)
        if 'page_image' in source:
            # A whole page remains addressable when scanned or entirely visual.
            catalog.append({**copy.deepcopy({k: v for k, v in source.items() if k not in {'id', 'text'}}),
                            'id': 'src_' + hashlib.sha256(json.dumps(
                                [source_id, source['source_sha256'], source['page_image']['sha256'], text],
                                ensure_ascii=False).encode()).hexdigest()[:24],
                            'source_id': source_id, 'text': text, 'start': 0, 'end': len(text)})
            continue
        for line in _LINE.finditer(text):
            if not line.group().strip():
                continue
            for start, end in _fragment_ranges(text, line.start(), line.end()):
                fragment = text[start:end]
                catalog.append({"id": _fragment_id(source_id, fragment, start, end),
                                "source_id": source_id, "text": fragment,
                                "start": start, "end": end})
    return catalog


def resolve_source_ids(catalog, ids):
    """Return exact catalog records selected by IDs, in requested order.

    Unknown IDs and duplicate selections are errors. Empty selections are
    permitted for quality-only conditions; whether a criterion needs source
    support belongs to its caller. Copies prevent downstream output decoration
    from modifying the authoritative catalog.
    """
    if not isinstance(catalog, list):
        raise ValueError("Source catalog must be a list")
    if not isinstance(ids, list) or any(not isinstance(value, str) or not value for value in ids):
        raise ValueError("Source ids must be a list of nonempty strings")
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate source reference ids")
    by_id = {}
    for fragment in catalog:
        if not isinstance(fragment, dict):
            raise ValueError("Invalid source catalog fragment")
        fragment_id = fragment.get("id")
        if not isinstance(fragment_id, str) or not fragment_id:
            raise ValueError("Source catalog fragment needs an id")
        if fragment_id in by_id:
            raise ValueError(f"Duplicate source catalog id: {fragment_id}")
        by_id[fragment_id] = fragment
    unknown = [value for value in ids if value not in by_id]
    if unknown:
        raise ValueError(f"Unknown source reference ids: {unknown}")
    return [dict(by_id[value]) for value in ids]
