"""Render a ``TailoredResume`` to ATS-safe DOCX and PDF.

"ATS-safe" is a real constraint, not a style preference. Applicant tracking
systems parse documents by walking the text stream, and they reliably mangle:
multi-column layouts, tables, text boxes, headers/footers, images, and unusual
fonts. So everything here is a single column of plain paragraphs in a standard
font, with right-aligned dates done via a tab stop rather than a table.

Both renderers read the same model, so the DOCX and the PDF always say the same
thing.
"""

from __future__ import annotations

from pathlib import Path

from ..models import MasterProfile, TailoredResume

# Kept in one place so the two renderers can't drift apart.
FONT_NAME = "Calibri"
PDF_FONT = "Helvetica"
PDF_FONT_BOLD = "Helvetica-Bold"


def _contact_line(profile: MasterProfile) -> str:
    parts = [profile.location, profile.phone, profile.email, *profile.links]
    return "  |  ".join(p for p in parts if p)


def _sections(resume: TailoredResume) -> list[tuple[str, str]]:
    """Section order. Skills sit high because screeners look there first."""
    return [
        ("PROFESSIONAL SUMMARY", "summary"),
        ("TECHNICAL SKILLS", "skills"),
        ("EXPERIENCE", "experience"),
        ("PROJECTS", "projects"),
        ("EDUCATION", "education"),
        ("CERTIFICATIONS", "certifications"),
        ("ACTIVITIES & LEADERSHIP", "activities"),
    ]


# --------------------------------------------------------------------------- #
#  DOCX                                                                        #
# --------------------------------------------------------------------------- #


def render_docx(resume: TailoredResume, profile: MasterProfile, path: Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
    from docx.shared import Inches, Pt, RGBColor

    doc = Document()

    for section in doc.sections:
        section.top_margin = Inches(0.5)
        section.bottom_margin = Inches(0.5)
        section.left_margin = Inches(0.6)
        section.right_margin = Inches(0.6)

    normal = doc.styles["Normal"]
    normal.font.name = FONT_NAME
    normal.font.size = Pt(10)
    normal.paragraph_format.space_after = Pt(0)
    normal.paragraph_format.line_spacing = 1.05

    right_tab = Inches(7.3)

    def para(space_before: int = 0, space_after: int = 0):
        p = doc.add_paragraph()
        p.paragraph_format.space_before = Pt(space_before)
        p.paragraph_format.space_after = Pt(space_after)
        return p

    def heading(text: str) -> None:
        p = para(space_before=9, space_after=3)
        run = p.add_run(text)
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0x1F, 0x3B, 0x57)
        # A bottom rule reads as a section break to humans and is invisible to
        # the text extractor an ATS uses.
        p_borders = p.paragraph_format.element.get_or_add_pPr()
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        borders = OxmlElement("w:pBdr")
        bottom = OxmlElement("w:bottom")
        bottom.set(qn("w:val"), "single")
        bottom.set(qn("w:sz"), "6")
        bottom.set(qn("w:color"), "1F3B57")
        borders.append(bottom)
        p_borders.append(borders)

    def bullet(text: str) -> None:
        p = para(space_after=2)
        p.paragraph_format.left_indent = Inches(0.18)
        p.paragraph_format.first_line_indent = Inches(-0.18)
        p.add_run(f"• {text}")

    def titled_row(left_bold: str, left_rest: str, right: str) -> None:
        p = para(space_before=5, space_after=1)
        p.paragraph_format.tab_stops.add_tab_stop(right_tab, WD_TAB_ALIGNMENT.RIGHT)
        run = p.add_run(left_bold)
        run.bold = True
        if left_rest:
            p.add_run(left_rest)
        if right:
            p.add_run(f"\t{right}").italic = True

    # -- header ------------------------------------------------------------- #
    name_p = para(space_after=1)
    name_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    name_run = name_p.add_run(profile.name.upper())
    name_run.bold = True
    name_run.font.size = Pt(19)

    if resume.headline:
        h = para(space_after=1)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        run = h.add_run(resume.headline)
        run.font.size = Pt(11)

    contact = para(space_after=2)
    contact.alignment = WD_ALIGN_PARAGRAPH.CENTER
    contact.add_run(_contact_line(profile)).font.size = Pt(9)

    # -- body --------------------------------------------------------------- #
    for label, key in _sections(resume):
        if key == "summary" and resume.summary:
            heading(label)
            para(space_after=2).add_run(resume.summary)

        elif key == "skills" and resume.skill_groups:
            heading(label)
            for group in resume.skill_groups:
                if not group.items:
                    continue
                p = para(space_after=2)
                p.add_run(f"{group.label}: ").bold = True
                p.add_run(", ".join(group.items))

        elif key == "experience" and resume.experience:
            heading(label)
            for item in resume.experience:
                titled_row(item.title, f" — {item.organization}", item.dates)
                for b in item.bullets:
                    bullet(b)

        elif key == "projects" and resume.projects:
            heading(label)
            for item in resume.projects:
                titled_row(item.name, "", item.dates)
                if item.tech:
                    p = para(space_after=1)
                    p.add_run("Tech: ").bold = True
                    p.add_run(item.tech).italic = True
                for b in item.bullets:
                    bullet(b)

        elif key == "education" and resume.education_lines:
            heading(label)
            for line in resume.education_lines:
                para(space_after=2).add_run(line)

        elif key == "certifications" and resume.certifications:
            heading(label)
            for cert in resume.certifications:
                bullet(cert)

        elif key == "activities" and resume.activities:
            heading(label)
            for activity in resume.activities:
                bullet(activity)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
    return path


# --------------------------------------------------------------------------- #
#  PDF                                                                         #
# --------------------------------------------------------------------------- #


def render_pdf(resume: TailoredResume, profile: MasterProfile, path: Path) -> Path:
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        HRFlowable,
        ListFlowable,
        ListItem,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
    )
    from xml.sax.saxutils import escape

    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.5 * inch,
        bottomMargin=0.5 * inch,
        title=f"{profile.name} — Resume",
        author=profile.name,
    )

    body = ParagraphStyle(
        "body", fontName=PDF_FONT, fontSize=9.5, leading=12.5, spaceAfter=2
    )
    name = ParagraphStyle(
        "name", fontName=PDF_FONT_BOLD, fontSize=18, leading=21, alignment=TA_CENTER
    )
    headline = ParagraphStyle(
        "headline", parent=body, fontSize=11, leading=14, alignment=TA_CENTER
    )
    contact = ParagraphStyle(
        "contact", parent=body, fontSize=8.5, leading=11, alignment=TA_CENTER
    )
    section = ParagraphStyle(
        "section",
        fontName=PDF_FONT_BOLD,
        fontSize=10.5,
        leading=13,
        textColor="#1F3B57",
        spaceBefore=9,
        spaceAfter=1,
    )
    entry = ParagraphStyle("entry", parent=body, spaceBefore=4, spaceAfter=1)

    def e(text: str) -> str:
        return escape(text or "")

    story: list = [
        Paragraph(e(profile.name).upper(), name),
    ]
    if resume.headline:
        story.append(Paragraph(e(resume.headline), headline))
    story += [
        Paragraph(e(_contact_line(profile)), contact),
        Spacer(1, 3),
    ]

    def add_section(label: str) -> None:
        story.append(Paragraph(label, section))
        story.append(
            HRFlowable(width="100%", thickness=0.6, color="#1F3B57", spaceAfter=3)
        )

    def add_bullets(items: list[str]) -> None:
        if not items:
            return
        story.append(
            ListFlowable(
                [ListItem(Paragraph(e(i), body), leftIndent=12) for i in items],
                bulletType="bullet",
                start="•",
                leftIndent=12,
                bulletFontSize=7,
            )
        )

    def dated(left: str, right: str) -> str:
        # Right-align the date with a wide non-breaking gap; no table needed.
        if not right:
            return left
        pad = "&nbsp;" * 4
        return f"{left}{pad}<font size=8.5><i>({e(right)})</i></font>"

    for label, key in _sections(resume):
        if key == "summary" and resume.summary:
            add_section(label)
            story.append(Paragraph(e(resume.summary), body))

        elif key == "skills" and resume.skill_groups:
            add_section(label)
            for group in resume.skill_groups:
                if group.items:
                    story.append(
                        Paragraph(
                            f"<b>{e(group.label)}:</b> {e(', '.join(group.items))}",
                            body,
                        )
                    )

        elif key == "experience" and resume.experience:
            add_section(label)
            for item in resume.experience:
                story.append(
                    Paragraph(
                        dated(
                            f"<b>{e(item.title)}</b> — {e(item.organization)}",
                            item.dates,
                        ),
                        entry,
                    )
                )
                add_bullets(item.bullets)

        elif key == "projects" and resume.projects:
            add_section(label)
            for item in resume.projects:
                story.append(
                    Paragraph(dated(f"<b>{e(item.name)}</b>", item.dates), entry)
                )
                if item.tech:
                    story.append(
                        Paragraph(f"<i>Tech: {e(item.tech)}</i>", body)
                    )
                add_bullets(item.bullets)

        elif key == "education" and resume.education_lines:
            add_section(label)
            for line in resume.education_lines:
                story.append(Paragraph(e(line), body))

        elif key == "certifications" and resume.certifications:
            add_section(label)
            add_bullets(resume.certifications)

        elif key == "activities" and resume.activities:
            add_section(label)
            add_bullets(resume.activities)

    doc.build(story)
    return path


def render(
    resume: TailoredResume,
    profile: MasterProfile,
    directory: Path,
    stem: str,
    formats: list[str],
) -> dict[str, str]:
    """Write every requested format and return {format: path}."""
    written: dict[str, str] = {}
    if "docx" in formats:
        written["docx"] = str(render_docx(resume, profile, directory / f"{stem}.docx"))
    if "pdf" in formats:
        written["pdf"] = str(render_pdf(resume, profile, directory / f"{stem}.pdf"))
    return written
