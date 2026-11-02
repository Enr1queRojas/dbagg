import re
import unittest
from pathlib import Path

from dbagg.database.catalog import load_catalog

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sql" / "dbagg_readonly.sql"


class ReadOnlyGrantTests(unittest.TestCase):
    def test_script_grants_select_on_exactly_the_catalogued_sources(self):
        text = SCRIPT.read_text(encoding="utf-8")
        granted = {
            m.lower() for m in re.findall(r"GRANT SELECT ON ([\w.]+) TO dbagg_lectura;", text)
        }
        self.assertEqual(granted, set(load_catalog().sources))
        self.assertNotIn("db_datareader", text.replace("Sin db_datareader", ""))
        self.assertIn("DENY EXECUTE TO dbagg_lectura;", text)


if __name__ == "__main__":
    unittest.main()
