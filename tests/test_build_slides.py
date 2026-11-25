"""tools/build_slides.py: placeholders, example guard, no Chrome needed."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import build_slides as bs  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
EXAMPLE = ROOT / "docs" / "slides" / "numbers.example.json"


class BuildSlidesTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / "report_top.png").write_bytes(PNG)
        self.template = bs.TEMPLATE.read_text(encoding="utf-8")
        self.data = json.loads(EXAMPLE.read_text(encoding="utf-8"))

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, data, *extra):
        nums = self.dir / "n.json"
        nums.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        out = self.dir / "out.html"
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = bs.main(["--numbers", str(nums), "--html-only", "--out", str(out), "--shots-dir", str(self.dir), *extra])
        return rc, err.getvalue(), out

    def test_template_keys_equal_example_keys(self):
        self.assertEqual(bs.placeholders(self.template), {k for k in self.data if not k.startswith("_")})

    def test_template_has_seven_slides(self):
        self.assertEqual(self.template.count('<section class="slide'), 7)

    def test_missing_key_fails(self):
        data = dict(self.data)
        del data["acc_org"]
        rc, err, out = self.run_cli(data, "--allow-example")
        self.assertEqual(rc, 2)
        self.assertIn("acc_org", err)
        self.assertFalse(out.exists())

    def test_unused_key_fails(self):
        data = dict(self.data, лишний="1", extra_key="2")
        rc, err, _ = self.run_cli(data, "--allow-example")
        self.assertEqual(rc, 2)
        self.assertIn("extra_key", err)

    def test_example_marker_blocks_final_build(self):
        rc, err, out = self.run_cli(self.data)
        self.assertEqual(rc, 2)
        self.assertIn("_example", err)
        self.assertFalse(out.exists())
        data = {k: v for k, v in self.data.items() if k != "_example"}      # marker word in values still blocks
        rc, err, _ = self.run_cli(data)
        self.assertEqual(rc, 2)
        self.assertIn("ПРИМЕР", err)

    def test_real_numbers_build(self):
        data = {k: ("report_top.png" if k.startswith("img_") else "42") for k in self.data if not k.startswith("_")}
        rc, err, out = self.run_cli(data)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("{{", out.read_text(encoding="utf-8"))

    def test_html_only_has_no_placeholders_and_embeds_image(self):
        rc, err, out = self.run_cli(self.data, "--allow-example")
        self.assertEqual(rc, 0, err)
        page = out.read_text(encoding="utf-8")
        self.assertNotIn("{{", page)
        self.assertIn("data:image/png;base64,", page)
        self.assertNotIn("<script", page.lower())

    def test_missing_image_is_clear_error(self):
        data = dict(self.data, img_report_top="нет_такой.png")
        rc, err, _ = self.run_cli(data, "--allow-example")
        self.assertEqual(rc, 2)
        self.assertIn("нет_такой.png", err)

    def test_values_are_escaped(self):
        data = dict(self.data, author="<script>x</script>")
        rc, _, out = self.run_cli(data, "--allow-example")
        self.assertEqual(rc, 0)
        self.assertNotIn("<script>x", out.read_text(encoding="utf-8"))

    def test_no_hardcoded_figures_in_slide_text(self):
        import re
        body = re.sub(r"<style>.*?</style>|<svg.*?</svg>|<!--.*?-->|\{\{\w+\}\}|<[^>]+>", " ", self.template, flags=re.S)
        self.assertEqual(re.findall(r"\d+", body), [])


if __name__ == "__main__":
    unittest.main()
