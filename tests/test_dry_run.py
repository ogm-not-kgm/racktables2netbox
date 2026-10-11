import configparser
import json
import logging
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import racktables2netbox as rt2nb


class TestDryRunAndConfigValidation(unittest.TestCase):
    """Deterministic tests for --dry-run validation and configuration checks (success and failure paths)."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.conf_path = os.path.join(self.temp_dir.name, "conf")
        self.log_path = os.path.join(self.temp_dir.name, "migration.log")
        self.err_path = os.path.join(self.temp_dir.name, "error.log")
        self.json_path = os.path.join(self.temp_dir.name, "dump.json")

    def _write_conf(self, content):
        with open(self.conf_path, "w", encoding="utf-8") as f:
            f.write(content)
        cfg = configparser.ConfigParser()
        cfg.read(self.conf_path, encoding="utf-8")
        return cfg

    def test_validate_config_success_dry_run_import(self):
        """Success path: import mode with dry_run=True only requires [Log] section."""
        cfg = self._write_conf(
            f"[Log]\nLOGFILE = {self.log_path}\nERROR_LOG = {self.err_path}\n"
        )
        rt2nb.validate_config(cfg, self.conf_path, mode="import", dry_run=True)

    def test_validate_config_missing_file_raises(self):
        """Failure path: non-existent config file raises FileNotFoundError with actionable message."""
        cfg = configparser.ConfigParser()
        missing_path = os.path.join(self.temp_dir.name, "nonexistent.conf")
        with self.assertRaises(FileNotFoundError) as ctx:
            rt2nb.validate_config(cfg, missing_path, mode="import", dry_run=True)
        self.assertIn("conf.example", str(ctx.exception))

    def test_validate_config_missing_netbox_section_when_not_dry_run(self):
        """Failure path: import mode without dry_run fails when [NetBox] is missing."""
        cfg = self._write_conf(f"[Log]\nLOGFILE = {self.log_path}\n")
        with self.assertRaises(ValueError) as ctx:
            rt2nb.validate_config(cfg, self.conf_path, mode="import", dry_run=False)
        self.assertIn("[NetBox]", str(ctx.exception))

    def test_validate_config_missing_mysql_key_on_export(self):
        """Failure path: export mode without dry_run fails when required MySQL keys are missing."""
        cfg = self._write_conf(
            f"[Log]\nLOGFILE = {self.log_path}\n[MySQL]\nDB_IP = localhost\n"
        )
        with self.assertRaises(ValueError) as ctx:
            rt2nb.validate_config(cfg, self.conf_path, mode="export", dry_run=False)
        self.assertIn("DB_PORT", str(ctx.exception))

    def test_require_module_missing_dependency_raises(self):
        """Failure path (IMP-2): missing required module raises ImportError with install hint and cause."""
        with self.assertRaises(ImportError) as ctx:
            rt2nb._require_module("nonexistent_pkg_xyz_12345", "nonexistent-pkg")
        self.assertIn("pip install nonexistent-pkg", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.__cause__)

    def test_check_mysql_racks_ok_and_ng(self):
        """Success & failure paths: check_mysql_racks returns OK with rack count or NG when 0/unconfigured."""
        cfg = self._write_conf(f"[Log]\nLOGFILE = {self.log_path}\n")

        ok, count, msg = rt2nb.check_mysql_racks(
            cfg,
            exported_data={"infrastructure": {"racks": [{"id": 1}, {"id": 2}, {"id": 3}]}},
        )
        self.assertTrue(ok)
        self.assertEqual(count, 3)
        self.assertEqual(msg, "OK (Racks: 3)")

        ok_empty, count_empty, msg_empty = rt2nb.check_mysql_racks(
            cfg,
            exported_data={"infrastructure": {"racks": []}},
        )
        self.assertFalse(ok_empty)
        self.assertEqual(count_empty, 0)
        self.assertIn("NG (Racks: 0", msg_empty)

        ok_no_cfg, _, msg_no_cfg = rt2nb.check_mysql_racks(cfg, exported_data=None)
        self.assertFalse(ok_no_cfg)
        self.assertIn("NG (Missing or incomplete [MySQL]", msg_no_cfg)

    def test_check_netbox_api_ok_and_ng(self):
        """Success & failure paths: check_netbox_api returns OK with NetBox version or NG on failure."""
        cfg_ok = self._write_conf(
            f"[Log]\nLOGFILE = {self.log_path}\n"
            "[NetBox]\nNETBOX_HOST = http://netbox-app:8080\nNETBOX_TOKEN = dummy-token\n"
        )

        class _FakeRacksEndpoint:
            @staticmethod
            def count():
                return 10

        class _FakeDcim:
            racks = _FakeRacksEndpoint()

        class _FakeNetBoxClient:
            dcim = _FakeDcim()

            @staticmethod
            def status():
                return {"netbox-version": "4.2.9"}

        ok, msg = rt2nb.check_netbox_api(cfg_ok, netbox_api=_FakeNetBoxClient())
        self.assertTrue(ok)
        self.assertEqual(msg, "OK (http://netbox-app:8080, NetBox v4.2.9)")

        cfg_missing = self._write_conf(f"[Log]\nLOGFILE = {self.log_path}\n")
        ok_ng, msg_ng = rt2nb.check_netbox_api(cfg_missing, netbox_api=None)
        self.assertFalse(ok_ng)
        self.assertIn("NG (Missing or incomplete [NetBox]", msg_ng)

    def test_log_dry_run_check_summary_outputs_ok_and_ng(self):
        """Verify final dry-run summary logs MySQL rack count and NetBox API status."""
        with self.assertLogs(rt2nb.logger, level="INFO") as log_ctx:
            rt2nb.log_dry_run_check_summary(
                True, "OK (Racks: 12)", True, "OK (http://netbox-app:8080, NetBox v4.2.9)"
            )
        joined = "\n".join(log_ctx.output)
        self.assertIn("[DRY-RUN] Final Check Result:", joined)
        self.assertIn("MySQL Data (Racks) : OK (Racks: 12)", joined)
        self.assertIn("NetBox API Access  : OK (http://netbox-app:8080, NetBox v4.2.9)", joined)

    def test_dry_run_success_and_port_exclusion(self):
        """Success path: dry-run validates prefixes, ports, exclusions, cables, and IPs without NetBox API."""
        cfg = self._write_conf(
            f"[Log]\nLOGFILE = {self.log_path}\nERROR_LOG = {self.err_path}\n"
            "[PortTypeMapping]\ncustom-10g = 10gbase-x-sfpp\n"
            "[ExcludePorts]\nIGNORE_PORT_PREFIXES = mgmt-ignore\n"
        )
        sample_data = {
            "infrastructure": {"sites_map": {"1": "Tokyo"}, "rooms_map": {}, "racks": []},
            "vlan_groups": [{"id": 1, "name": "Default"}],
            "vlans": [],
            "prefixes": {
                "v4": [{"cidr": "192.168.10.0/24"}],
                "v6": [{"cidr": "2001:db8::/64"}],
            },
            "hardware": [],
            "devices": [{"dev_id": 10, "name": "sw-01"}, {"dev_id": 11, "name": "srv-01"}],
            "pdus": [],
            "patch_panels": [],
            "vm_clusters": [],
            "virtual_machines": [{"vm_id": 20, "name": "vm-01"}],
            "ports": [
                {"rt_port_id": 100, "object_id": 10, "name": "eth0", "oif_name": "1000BASE-T", "mac_address": None},
                {"rt_port_id": 101, "object_id": 11, "name": "eth0", "oif_name": "custom-10g", "mac_address": None},
                {"rt_port_id": 102, "object_id": 11, "name": "mgmt-ignore0", "oif_name": "1000BASE-T", "mac_address": None},
            ],
            "cables": [
                {
                    "porta_id": 100,
                    "portb_id": 101,
                    "port_a_name": "eth0",
                    "port_b_name": "eth0",
                    "obj_a": "sw-01",
                    "obj_b": "srv-01",
                    "cable_label": "CBL-01",
                }
            ],
            "device_ips": [{"object_id": 10, "ip_str": "192.168.10.5", "os_if_name": "eth0"}],
            "tag_tree": [],
            "tag_storage": [],
        }

        importer = rt2nb.NetBoxImporter(cfg, netbox_api=None, dry_run=True)
        with self.assertLogs(rt2nb.logger, level="INFO") as log_ctx:
            importer.import_all(sample_data)

        joined_logs = "\n".join(log_ctx.output)
        self.assertIn("[DRY-RUN] Validation summary:", joined_logs)
        self.assertIn("ports_valid=2 (skipped=1)", joined_logs)
        self.assertIn("cables_valid=1 (skipped=0)", joined_logs)
        self.assertEqual(importer.get_best_cidr("192.168.10.5"), "192.168.10.5/24")

    def test_dry_run_failure_paths_logged_as_errors(self):
        """Failure path: invalid prefix, unknown OIF type, and VM cable log actionable ERROR entries."""
        cfg = self._write_conf(f"[Log]\nLOGFILE = {self.log_path}\nERROR_LOG = {self.err_path}\n")
        bad_data = {
            "prefixes": {"v4": [{"cidr": "not-a-cidr"}], "v6": []},
            "devices": [{"dev_id": 10, "name": "sw-01"}],
            "virtual_machines": [{"vm_id": 20, "name": "vm-01"}],
            "ports": [
                {"rt_port_id": 100, "object_id": 10, "name": "p1", "oif_name": "unmapped-proprietary-oif", "mac_address": None},
                {"rt_port_id": 200, "object_id": 20, "name": "vnet0", "oif_name": "virtual port", "mac_address": None},
            ],
            "cables": [
                {
                    "porta_id": 100,
                    "portb_id": 200,
                    "port_a_name": "p1",
                    "port_b_name": "vnet0",
                    "obj_a": "sw-01",
                    "obj_b": "vm-01",
                    "cable_label": "BAD-VM-CABLE",
                }
            ],
        }

        importer = rt2nb.NetBoxImporter(cfg, netbox_api=None, dry_run=True)
        with self.assertLogs(rt2nb.logger, level="ERROR") as err_ctx:
            importer.import_all(bad_data)

        joined_errors = "\n".join(err_ctx.output)
        self.assertIn("Invalid IPv4 prefix 'not-a-cidr'", joined_errors)
        self.assertIn("Unknown OIF type 'unmapped-proprietary-oif'", joined_errors)
        self.assertIn("cables involving VM interfaces are not supported", joined_errors)

    def test_cli_dry_run_end_to_end_with_temp_files(self):
        """E2E CLI test using real temporary files for --dry-run --mode import (reports NG when MySQL/NetBox not configured)."""
        self._write_conf(
            f"[Log]\nLOGFILE = {self.log_path}\nERROR_LOG = {self.err_path}\n"
        )
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump({"devices": [{"dev_id": 1, "name": "d1"}], "ports": []}, f)

        script_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "racktables2netbox.py",
        )
        result = subprocess.run(
            [
                sys.executable,
                script_path,
                "--mode",
                "import",
                "--dry-run",
                "--config",
                self.conf_path,
                "--json-file",
                self.json_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1)
        self.assertTrue(os.path.isfile(self.log_path))
        with open(self.log_path, "r", encoding="utf-8") as lf:
            log_content = lf.read()
        self.assertIn("[DRY-RUN] Skipping NetBox data purge and DeviceType Library import.", log_content)
        self.assertIn("[DRY-RUN] Validation summary:", log_content)
        self.assertIn("[DRY-RUN] Final Check Result:", log_content)
        self.assertIn("MySQL Data (Racks) : NG", log_content)
        self.assertIn("NetBox API Access  : NG", log_content)


if __name__ == "__main__":
    unittest.main()
