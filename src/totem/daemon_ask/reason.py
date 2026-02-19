from __future__ import annotations

from .models import Citation, PackedExcerpt


def build_answer(
    *,
    query: str,
    packed: list[PackedExcerpt],
    include_why: bool,
    why_these_sources: list[str],
    sources_mode: str = "auto",  # off|auto|always
) -> tuple[str, list[Citation], list[str]]:
    citations: list[Citation] = [p.citation for p in packed]

    lines: list[str] = []
    lines.append(f"Q: {query.strip()}")
    lines.append("")

    if not packed:
        lines.append("No matches found in the daemon vault index for this query.")
        return ("\n".join(lines).rstrip() + "\n", [], (why_these_sources if include_why else []))

    show_sources = sources_mode in {"auto", "always"}
    source_cap = len(citations) if sources_mode == "always" else min(len(citations), 3)

    lines.append("Evidence (excerpts):")
    for i, p in enumerate(packed, start=1):
        meta_bits = []
        if p.effective_date:
            meta_bits.append(p.effective_date)
        if p.title:
            meta_bits.append(p.title)
        if p.heading_path:
            meta_bits.append(p.heading_path)
        meta = " · ".join(meta_bits)
        cite = p.citation.to_compact_str()
        lines.append(f"{i}. {meta}".rstrip())
        lines.append(f"   {p.excerpt}".rstrip())
        if show_sources:
            lines.append(f"   [{cite}]")

    if show_sources:
        lines.append("")
        lines.append("Citations:")
        for c in citations[:source_cap]:
            lines.append(f"- {c.to_compact_str()}")

    if include_why and why_these_sources:
        lines.append("")
        lines.append("Why these sources:")
        for b in why_these_sources[:4]:
            lines.append(f"- {b}")

    return ("\n".join(lines).rstrip() + "\n", citations, (why_these_sources if include_why else []))

