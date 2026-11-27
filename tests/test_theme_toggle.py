"""Переключатель темы в HTML-отчёте: кнопка, скрипт, CSS, печать, офлайн."""
from __future__ import annotations

import base64
import hashlib
import re
import unittest

from revizor.report_html import render_html

MD = "# Отчёт\n\n## Раздел\n\nтекст\n\n## Ещё\n\n[ссылка](https://example.com/x)\n"


class ThemeToggle(unittest.TestCase):
    def setUp(self):
        self.h = render_html(MD)

    def test_button_and_script(self):
        self.assertRegex(self.h, r'<button type="button" id="theme-btn"[^>]*aria-label="[^"]+"')
        self.assertEqual(self.h.count("<script>"), 1)
        for word in ("localStorage", "авто", "светлая", "тёмная", "data-theme"):
            self.assertIn(word, self.h)
        self.assertEqual(self.h.count("localStorage"), 2)
        self.assertEqual(self.h.count("try{"), 2)           # каждый доступ к localStorage в try/catch

    def test_css(self):
        self.assertIn(':root[data-theme="dark"]{', self.h)
        self.assertIn(':root[data-theme="light"]', self.h)
        self.assertIn('@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){', self.h)
        self.assertNotIn("@media not all", self.h)

    def test_print_is_light_and_hides_button(self):
        printed = self.h[self.h.index("@media print{"):self.h.index("</style>")]
        self.assertRegex(printed, r"\.theme-btn\{display:none!important\}|footer,\.theme-btn\{display:none!important\}")
        self.assertIn(":root:root:root{color-scheme:light;--bg:#fff", printed)

    def test_csp_hash_matches_script(self):
        js = re.search(r"<script>(.*?)</script>", self.h, re.S).group(1)
        sha = base64.b64encode(hashlib.sha256(js.encode("utf-8")).digest()).decode()
        self.assertIn(f"script-src 'sha256-{sha}'", self.h)

    def test_no_external_resources(self):
        refs = re.findall(r'(?:src|href)="(https?:[^"]*)"', self.h)
        self.assertEqual([r for r in refs if "example.com" not in r], [])     # единственная — ссылка из текста отчёта
        self.assertNotRegex(self.h, r"<(?:script|img|link|iframe)[^>]+(?:src|href)=\"?https?:")
        self.assertNotRegex(self.h, r"url\(\s*['\"]?https?:|@import")
        head = self.h[:self.h.index("<body>")]
        self.assertNotIn("http://", head)
        self.assertNotIn("https://", head)

    def test_light_theme_starts_light(self):
        h = render_html(MD, theme="light")
        self.assertIn('<html lang="ru" data-theme="light">', h)
        self.assertIn('m="light"', h)
        self.assertIn('<meta name="color-scheme" content="light">', h)
        self.assertNotIn('data-theme="light">', self.h.split("<head>")[0])
        self.assertIn('m="auto"', self.h)


if __name__ == "__main__":
    unittest.main()
