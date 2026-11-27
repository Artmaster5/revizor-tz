import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("robustness", ROOT / "tools" / "robustness.py")
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)


class RobustnessShowcase(unittest.TestCase):
    def test_broken_inputs_do_not_crash(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "in"
            tmp.mkdir()
            cases = {n: p for n, _, p in rb.build_cases(tmp)}
            env = rb.clean_env()
            for name, expect_report in (("empty", False), ("docx_cut", False), ("docx_ok", True)):
                r = rb.run_case(name, cases[name], Path(td) / "w", env)
                self.assertEqual(r["code"], 0, name)
                self.assertFalse(r["traceback"], name)
                if expect_report:
                    self.assertGreater(r["requirements"], 0)
                else:
                    self.assertEqual(r["verdict"], "NOT_CHECKED")


if __name__ == "__main__":
    unittest.main()
