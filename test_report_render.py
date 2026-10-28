"""Tests for the monthly Xpertis report layout."""
import json
import unittest

import commit_tracker as ct


def _ai(en_topic, pl_topic="temat"):
    return json.dumps({
        "en": {
            "topic": en_topic,
            "created": "Named result for a real problem.",
            "relates_to": "module.pipeline",
            "contribution": "Wrote the schema and job.",
            "creative_element": "Chose one canonical schema.",
        },
        "pl": {
            "topic": pl_topic,
            "created": "Nazwany wynik konkretnego problemu.",
            "relates_to": "module.pipeline",
            "contribution": "Napisał schemat i job.",
            "creative_element": "Wybrał jeden kanoniczny schemat.",
        },
    })


def _row(**overrides):
    data = {
        "work_key": "k1",
        "sha": "aaaaaaaaaa1111111111",
        "subject": "Add pipeline (#10)",
        "authored_at": "2026-09-20T12:00:00+00:00",
        "pr_number": 10,
        "is_creative": 1,
        "creative_reason": "",
        "coauthors": None,
        "files_changed": 2,
        "insertions": 12,
        "deletions": 3,
        "files_json": json.dumps([
            {"path": "jobs/pipeline.yml", "ins": 10, "del": 1},
            {"path": "schema.py", "ins": 2, "del": 2},
        ]),
        "patch_path": str(ct.PATCHES_DIR / "0002-later.patch"),
        "ai_json": _ai("new pipeline", "nowy pipeline"),
        "body": "Implements the pipeline.",
    }
    data.update(overrides)
    return data


def _sample_rows():
    later = _row()
    review = _row(
        work_key="k-rev",
        sha="cccccccccc3333333333",
        subject="Bump timeout (#11)",
        authored_at="2026-09-15T12:00:00+00:00",
        pr_number=11,
        is_creative=0,
        creative_reason="config/parameter-only change",
        files_changed=1,
        insertions=1,
        deletions=1,
        files_json=json.dumps([{"path": "config.yml", "ins": 1, "del": 1}]),
        patch_path=str(ct.PATCHES_DIR / "0003-timeout.patch"),
        ai_json=_ai("should not appear", "nie"),
        body="Just a timeout.",
    )
    earlier = _row(
        work_key="k2",
        sha="bbbbbbbbbb2222222222",
        subject="Add helper (#9)",
        authored_at="2026-09-10T12:00:00+00:00",
        pr_number=9,
        files_json=json.dumps([{"path": "helper.py", "ins": 5, "del": 0}]),
        patch_path=str(ct.PATCHES_DIR / "0001-earlier.patch"),
        ai_json=_ai("old helper", "stary helper"),
        files_changed=1,
        insertions=5,
        deletions=0,
    )
    return [later, review, earlier]


class CreativeItemOrderTests(unittest.TestCase):
    def test_follows_table_date_order_not_patch_filename(self):
        items = ct._creative_items(_sample_rows())
        self.assertEqual([r["work_key"] for r in items], ["k1", "k2"])


class MarkdownReportTests(unittest.TestCase):
    def setUp(self):
        self.md = ct._render_month("2026-09", _sample_rows())

    def test_works_section_lists_creative_rows_only(self):
        works = self.md.split("## Not Works")[0]
        self.assertIn("## Works to copy into Xpertis", works)
        self.assertIn("new pipeline", works)
        self.assertIn("old helper", works)
        self.assertNotIn("Bump timeout", works)
        self.assertNotIn("should not appear", works)

    def test_skip_comes_after_works_and_has_no_paste(self):
        self.assertIn("## Not Works", self.md)
        self.assertLess(
            self.md.find("## Works to copy into Xpertis"),
            self.md.find("## Not Works"))
        skip = self.md.split("## Not Works", 1)[1]
        self.assertIn("Bump timeout", skip)
        self.assertNotIn("**English — paste into Xpertis", skip)

    def test_xpertis_cards_follow_table_order(self):
        section = self.md.split("## Works to copy into Xpertis", 1)[1]
        self.assertLess(section.find("new pipeline"), section.find("old helper"))

    def test_work_card_omits_file_list(self):
        # The uploaded attachment carries the per-file scope and the diff.
        card = self.md.split("### Work 1 of", 1)[1].split("### Work 2 of", 1)[0]
        self.assertNotIn("jobs/pipeline.yml", card)

    def test_description_uses_short_labels(self):
        card = self.md.split("### Work 1 of", 1)[1].split("### Work 2 of", 1)[0]
        self.assertIn("**3. Description — paste into Xpertis", card)
        self.assertIn("Problem: Named result for a real problem.", card)
        self.assertIn("Component: module.pipeline", card)
        self.assertIn("Mine: Wrote the schema and job.", card)
        self.assertIn("Creative: Chose one canonical schema.", card)
        self.assertIn("2 file(s), +12/-3 lines.", card)
        # Numbered boilerplate belongs in the attachment, not the 255 field.
        self.assertNotIn("2. What it relates to:", card)

    def test_card_links_the_patch_as_the_attachment(self):
        card = self.md.split("### Work 1 of", 1)[1].split("### Work 2 of", 1)[0]
        self.assertIn("**1. Attachment to upload:** [0002-later.patch]", card)
        self.assertIn("**2. Title:**", card)

    def test_shows_character_budget(self):
        self.assertRegex(self.md, r"\d+/255")

    def test_omits_duplicate_numbered_commit_dump(self):
        self.assertNotIn("## 1. ", self.md)


class HtmlReportTests(unittest.TestCase):
    def setUp(self):
        self.html = ct._render_month_html("2026-09", _sample_rows())

    def test_nav_and_works_exclude_review(self):
        self.assertIn('href="#works"', self.html)
        self.assertIn('href="#skip"', self.html)
        works = self.html.split('id="skip"')[0]
        self.assertIn("new pipeline", works)
        self.assertNotIn("Bump timeout", works)
        self.assertNotIn("should not appear", works)

    def test_english_textarea_is_copy_ready(self):
        self.assertIn("<textarea", self.html)
        self.assertIn("Problem: Named result for a real problem.", self.html)
        self.assertIn("Component: module.pipeline", self.html)
        self.assertNotIn("2. What it relates to:", self.html)

    def test_links_the_patch_as_the_attachment(self):
        card = self.html.split('id="cw-k1"', 1)[1].split('id="cw-k2"', 1)[0]
        self.assertIn("1. Attachment to upload", card)
        self.assertIn("0002-later.patch", card)

    def test_card_omits_files(self):
        card = self.html.split('id="cw-k1"', 1)[1].split('id="cw-k2"', 1)[0]
        self.assertNotIn("jobs/pipeline.yml", card)

    def test_review_is_collapsed_at_end(self):
        self.assertIn("<details", self.html)
        self.assertIn("Bump timeout", self.html)
        self.assertLess(
            self.html.find('id="works"'),
            self.html.find('id="skip"'))

    def test_omits_duplicate_numbered_headings(self):
        self.assertNotIn("<h2 id=\"i1\">", self.html)


def _long_row():
    return _row(ai_json=json.dumps({
        "en": {
            "topic": "pipeline",
            "created": "word " * 40,
            "relates_to": "word " * 40,
            "contribution": "word " * 40,
            "creative_element": "word " * 40,
        },
        "pl": {
            "topic": "x",
            "created": "słowo " * 40,
            "relates_to": "słowo " * 40,
            "contribution": "słowo " * 40,
            "creative_element": "słowo " * 40,
        },
    }))


class DescriptionFieldTests(unittest.TestCase):
    def test_never_exceeds_255(self):
        row = _long_row()
        for lang in ("en", "pl"):
            text = ct._xpertis_desc(row, lang)
            self.assertLessEqual(len(text), 255)
            self.assertTrue(text)

    def test_keeps_every_element_marker_when_squeezed(self):
        text = ct._xpertis_desc(_long_row(), "en")
        for marker in ("Problem:", "Component:", "Mine:", "Creative:"):
            self.assertIn(marker, text)
        self.assertIn("file(s)", text)

    def test_single_line_with_short_labels(self):
        text = ct._xpertis_desc(_row(), "en")
        self.assertNotIn("\n", text)
        self.assertTrue(text.startswith("Problem: "))
        self.assertIn("Component: module.pipeline", text)
        self.assertIn("2 file(s), +12/-3 lines.", text)
        self.assertLessEqual(len(text), 255)

    def test_short_labels_beat_numbered_format_on_room(self):
        row = _row()
        self.assertLess(
            len(ct._xpertis_desc(row, "en")),
            len(ct._work_narrative(row, "en")))


PATCH_TEXT = """From aaaaaaaaaa1111111111 Mon Sep 17 00:00:00 2001
From: Me <me@example.com>
Date: Sun, 20 Sep 2026 12:00:00 +0000
Subject: [PATCH] Add pipeline (#10)

Implements the pipeline.
---
 jobs/pipeline.yml | 11 +++++++++++
 1 file changed, 10 insertions(+), 1 deletion(-)

diff --git a/jobs/pipeline.yml b/jobs/pipeline.yml
+job:
"""


class PatchAttachmentTests(unittest.TestCase):
    def test_narrative_is_full_numbered_template(self):
        text = ct._work_narrative(_row(), "en")
        self.assertIn("1. Named result for a real problem.", text)
        self.assertIn("2. What it relates to: module.pipeline", text)
        self.assertIn("3. My contribution. Wrote the schema and job.", text)
        self.assertIn("4. Creative element: Chose one canonical schema.", text)
        self.assertIn(
            "5. Evidence and scope: 2026-09-20-new-pipeline-aaaaaaa.patch",
            text)

    def test_narrative_is_not_truncated(self):
        text = ct._work_narrative(_long_row(), "en")
        self.assertGreater(len(text), 255)
        self.assertIn("word word word", text)

    def test_attachment_is_the_patch_named_after_the_result(self):
        self.assertEqual(
            ct._attachment_name(_row()),
            "2026-09-20-new-pipeline-aaaaaaa.patch")

    def test_annotation_sits_in_the_commit_message_area(self):
        out = ct._annotate_patch(PATCH_TEXT, _row())
        self.assertIn("2. What it relates to: module.pipeline", out)
        self.assertIn("2. Czego dotyczy: module.pipeline", out)
        # Before the diffstat separator, so the file stays a valid patch.
        self.assertLess(out.index("1. Named result"), out.index("\n---\n"))
        self.assertLess(out.index("\n---\n"), out.index("diff --git"))
        self.assertIn("Implements the pipeline.", out)
        self.assertTrue(out.startswith("From aaaaaaaaaa1111111111"))

    def test_annotation_stays_in_the_body_not_the_subject(self):
        subject_wrapped = (
            "From aaaa Mon Sep 17 00:00:00 2001\n"
            "Subject: [PATCH] Add pipeline\n and a wrapped subject line\n"
            "---\n jobs/pipeline.yml | 1 +\n")
        out = ct._annotate_patch(subject_wrapped, _row())
        self.assertIn("wrapped subject line\n\n[Xpertis Work description]", out)

    def test_annotation_is_idempotent(self):
        once = ct._annotate_patch(PATCH_TEXT, _row())
        twice = ct._annotate_patch(once, _row())
        self.assertEqual(once, twice)

    def test_annotation_refreshes_a_stale_description(self):
        stale = ct._annotate_patch(PATCH_TEXT, _row())
        fresh = ct._annotate_patch(stale, _row(ai_json=json.dumps({
            "en": {"topic": "new pipeline", "created": "Rewritten problem.",
                   "relates_to": "r", "contribution": "c",
                   "creative_element": "e"},
            "pl": {"topic": "x", "created": "y", "relates_to": "r",
                   "contribution": "c", "creative_element": "e"},
        })))
        self.assertIn("Rewritten problem.", fresh)
        self.assertNotIn("Named result for a real problem.", fresh)
        self.assertEqual(fresh.count("[Xpertis Work description]"), 1)

    def test_patch_without_description_is_untouched(self):
        row = _row(ai_json=None)
        self.assertEqual(ct._annotate_patch(PATCH_TEXT, row), PATCH_TEXT)

    def test_review_rows_are_not_creative_items(self):
        items = ct._creative_items(_sample_rows())
        self.assertEqual([r["work_key"] for r in items], ["k1", "k2"])
