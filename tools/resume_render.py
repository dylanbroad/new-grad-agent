"""Renders a tailored-bullets result (from optimize_resume_bullets) into
an actual submittable PDF, not just the plain-text preview
render_resume_text produces.

Uses xhtml2pdf (pure Python, no system deps like wkhtmltopdf/Cairo) to
convert a small hand-built HTML template. The template uses table rows
for left/right-aligned header lines (company vs. dates, title vs.
location) because xhtml2pdf's CSS engine is CSS2.1-ish - no flexbox/grid
support - so tables are the reliable way to get that layout, not an
attempt to match the source PDF byte-for-byte.

Dates, locations, and job titles are always pulled from experience_bank
(never from the LLM's tailored_resume output) even though
optimize_resume_bullets' output includes a "title" field - the model was
only ever asked to select/reword bullets, and nothing validates that it
left title text alone, so trusting the bank for anything factual outside
the bullets themselves is the safer default.
"""

import html
import logging
from pathlib import Path

from xhtml2pdf import pisa

# xhtml2pdf logs a "CSS properties it doesn't implement" warning for
# harmless things like border-collapse (used for spacing control here,
# not actual borders) on every render - noise in batch output, not
# something actionable.
logging.getLogger("xhtml2pdf").setLevel(logging.ERROR)

# xhtml2pdf's base fonts (Helvetica etc.) are the classic PDF 14 - they
# don't cover most "smart" typographic punctuation, so a model-written
# bullet using U+2011 (non-breaking hyphen) instead of a plain "-" comes
# out as a black box glyph in the actual PDF. Normalize to ASCII before
# anything gets rendered.
_UNICODE_PUNCTUATION = str.maketrans(
    {
        "‐": "-",  # hyphen
        "‑": "-",  # non-breaking hyphen
        "‒": "-",  # figure dash
        "–": "-",  # en dash
        "—": "-",  # em dash
        "‘": "'",  # left single quote
        "’": "'",  # right single quote / apostrophe
        "“": '"',  # left double quote
        "”": '"',  # right double quote
        "…": "...",  # ellipsis
        " ": " ",  # non-breaking space
    }
)


def _clean(text: str) -> str:
    return html.escape(text.translate(_UNICODE_PUNCTUATION))


_CSS = """
<style>
  @page { size: letter; margin: 0.45in; }
  body { font-family: Helvetica, Arial, sans-serif; font-size: 9.5pt; color: #111; line-height: 1.15; }
  h1 { text-align: center; font-size: 16pt; margin: 0 0 2px 0; }
  .contact { text-align: center; font-size: 8.5pt; margin: 0 0 6px 0; }
  h2 {
    font-size: 10.5pt; margin: 5px 0 2px 0;
    border-bottom: 1px solid #333; padding-bottom: 1px;
  }
  table.row { width: 100%; border-collapse: collapse; margin: 0; }
  table.row td { padding: 0; }
  .bold { font-weight: bold; }
  .italic { font-style: italic; }
  .right { text-align: right; }
  ul { margin: 1px 0 4px 0; padding-left: 14px; }
  li { margin-bottom: 0px; }
  .skills p { margin: 1px 0; }
  .meta { margin: 1px 0; }
</style>
"""


def _header_table(rows: list[tuple[str, str, str, str]]) -> str:
    """One 2-row table (company/dates, then title/location) instead of two
    separate <table> elements - avoids the implicit gap between them that
    two adjacent block-level tables otherwise leave."""
    trs = []
    for left, right, left_class, right_class in rows:
        rc = f"right {right_class}".strip()
        trs.append(f'<tr><td class="{left_class}">{_clean(left)}</td><td class="{rc}">{_clean(right)}</td></tr>')
    return '<table class="row">' + "".join(trs) + "</table>"


def render_resume_html(tailored_resume: dict, experience_bank: dict) -> str:
    """Build a single-page resume HTML document.

    Contact/education/skills come straight from experience_bank (never
    tailored). Job/project bullets come from tailored_resume; everything
    else about them (dates, location, title, tags) is looked up from
    experience_bank by company/project name so the PDF never shows
    anything the model wrote outside the bullets themselves.
    """
    source_jobs = {j["company"]: j for j in experience_bank.get("jobs", [])}
    source_projects = {p["name"]: p for p in experience_bank.get("projects", [])}

    parts = ["<html><head>", _CSS, "</head><body>"]

    contact = experience_bank.get("contact", {})
    parts.append(f"<h1>{_clean(contact.get('name', ''))}</h1>")
    contact_line = " | ".join(
        v for v in (contact.get("email"), contact.get("phone"), contact.get("linkedin")) if v
    )
    parts.append(f'<p class="contact">{_clean(contact_line)}</p>')

    if experience_bank.get("education"):
        parts.append("<h2>Education</h2>")
        for edu in experience_bank["education"]:
            parts.append(
                _header_table(
                    [
                        (edu.get("school", ""), f"{edu.get('start', '')} - {edu.get('end', '')}", "bold", ""),
                        (edu.get("degree", ""), edu.get("location", ""), "italic", "italic"),
                    ]
                )
            )
            if edu.get("details"):
                parts.append(f'<p class="meta">{_clean(edu["details"])}</p>')

    jobs = tailored_resume.get("jobs", [])
    if jobs:
        parts.append("<h2>Experience</h2>")
        for job in jobs:
            source = source_jobs.get(job.get("company", ""), {})
            company = source.get("company") or job.get("company", "")
            parts.append(
                _header_table(
                    [
                        (company, f"{source.get('start', '')} - {source.get('end', '')}", "bold", ""),
                        (source.get("title", ""), source.get("location", ""), "italic", "italic"),
                    ]
                )
            )
            parts.append("<ul>")
            for bullet in job.get("bullets", []):
                parts.append(f"<li>{_clean(bullet)}</li>")
            parts.append("</ul>")

    projects = tailored_resume.get("projects", [])
    if projects:
        parts.append("<h2>Projects</h2>")
        for project in projects:
            source = source_projects.get(project.get("name", ""), {})
            tags = ", ".join(source.get("tags", []))
            header = source.get("name", project.get("name", ""))
            if tags:
                header += f" | {tags}"
            parts.append(f'<p class="bold meta">{_clean(header)}</p>')
            parts.append("<ul>")
            for bullet in project.get("bullets", []):
                parts.append(f"<li>{_clean(bullet)}</li>")
            parts.append("</ul>")

    skills = experience_bank.get("skills")
    if skills:
        parts.append("<h2>Technical Skills</h2>")
        parts.append('<div class="skills">')
        if isinstance(skills, dict):
            for category, items in skills.items():
                if items:
                    parts.append(
                        f'<p><span class="bold">{_clean(category)}:</span> '
                        f'{_clean(", ".join(items))}</p>'
                    )
        else:
            parts.append(f'<p>{_clean(", ".join(skills))}</p>')
        parts.append("</div>")

    parts.append("</body></html>")
    return "".join(parts)


def render_resume_pdf(tailored_resume: dict, experience_bank: dict, output_path: Path) -> Path:
    """Render the tailored resume to a PDF file at output_path."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    html_doc = render_resume_html(tailored_resume, experience_bank)
    with open(output_path, "wb") as f:
        result = pisa.CreatePDF(html_doc, dest=f)
    if result.err:
        raise RuntimeError(f"failed to render resume PDF ({result.err} errors) at {output_path}")
    return output_path
