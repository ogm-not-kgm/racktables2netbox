#!/usr/bin/env python3
# -*- coding: utf-8 -*-
__version__ = "1.1.0"

import argparse
import configparser
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
from collections import defaultdict

try:
    import slugify
except ImportError:
    slugify = None


logger = logging.getLogger('racktables2netbox')


def _require_module(module_name, package_name=None):
    """
    Import and return a required third-party module, or fail with an actionable error message.

    Raises:
        ImportError: If the module is not installed, preserving the original cause.
    """
    pkg = package_name or module_name
    try:
        return __import__(module_name)
    except ImportError as e:
        raise ImportError(
            f"Required module '{module_name}' is not installed. "
            f"Install dependencies with: pip install {pkg} (or pip install -r requirements.txt)."
        ) from e


def clean_rt_text(text):
    if not text:
        return ""
    text = re.sub(r'\[\[|\]\]', '', text)
    if '|' in text:
        text = text.split('|')[0]
    return text.strip()


def determine_face(atoms):
    """
    RackSpace.atom の集合 (atoms) から NetBox の face ('front' or 'rear') を判定する。
    - 'rear' が含まれ、かつ 'front' が含まれない場合 ('rear'のみ、または 'rear' & 'interior') -> 'rear'
    - それ以外 -> 'front'
    """
    atoms_set = {a for a in atoms if a}
    if 'rear' in atoms_set and 'front' not in atoms_set:
        return 'rear'
    return 'front'


class DBExtractor:
    """
    Extracts all data from RackTables MySQL database into pure Python dict/list structures
    ready for JSON serialization.
    """
    def __init__(self, config):
        self.config = config
        self.con = None

    def connect(self):
        pymysql = _require_module("pymysql", "PyMySQL")
        if not self.con or not self.con.open:
            self.con = pymysql.connect(
                host=self.config['MySQL']['DB_IP'],
                port=int(self.config['MySQL']['DB_PORT']),
                database=self.config['MySQL']['DB_NAME'],
                user=self.config['MySQL']['DB_USER'],
                password=self.config['MySQL']['DB_PWD']
            )

    @staticmethod
    def convert_ip(ip_raw):
        return socket.inet_ntoa(struct.pack('!I', ip_raw))

    @staticmethod
    def convert_ipv6(ip_raw):
        if isinstance(ip_raw, (bytes, bytearray)):
            return str(ipaddress.IPv6Address(bytes(ip_raw)))
        return str(ip_raw)

    def export_all(self):
        global slugify
        if slugify is None:
            slugify = _require_module("slugify", "python-slugify")
        logger.info("Starting data extraction from RackTables MySQL...")
        self.connect()

        data = {
            "version": __version__,
            "vlan_groups": self.export_vlan_groups(),
            "vlans": self.export_vlans(),
            "prefixes": self.export_prefixes(),
            "infrastructure": self.export_infrastructure(),
            "hardware": self.export_hardware(),
            "pdus": self.export_pdus(),
            "patch_panels": self.export_patch_panels(),
            "devices": self.export_devices(),
            "vm_clusters": self.export_vm_clusters(),
            "virtual_machines": self.export_virtual_machines(),
            "device_ips": self.export_device_ips(),
            "ports": self.export_ports(),
            "cables": self.export_cables(),
            "tag_tree": self.export_tag_tree(),
            "tag_storage": self.export_tag_storage()
        }

        if self.con and self.con.open:
            self.con.close()

        logger.info("Data extraction completed successfully.")
        return data

    def export_tag_tree(self):
        logger.info("Extracting TagTree from RackTables...")
        with self.con.cursor() as cur:
            cur.execute("SELECT id, parent_id, is_assignable, tag FROM TagTree")
            rows = cur.fetchall()
        tags = []
        for tag_id, parent_id, is_assignable, tag_name in rows:
            tags.append({
                "id": tag_id,
                "parent_id": parent_id,
                "is_assignable": is_assignable,
                "name": tag_name
            })
        return tags


    def export_tag_storage(self):
        logger.info("Extracting TagStorage from RackTables...")
        with self.con.cursor() as cur:
            cur.execute("SELECT entity_realm, entity_id, tag_id, tag_is_assignable FROM TagStorage")
            rows = cur.fetchall()
        storage = []
        for entity_realm, entity_id, tag_id, tag_is_assignable in rows:
            storage.append({
                "entity_realm": entity_realm,
                "entity_id": entity_id,
                "tag_id": tag_id,
                "tag_is_assignable": tag_is_assignable
            })
        return storage


    def export_vlan_groups(self):
        logger.info("Extracting VLAN domains from RackTables...")
        with self.con.cursor() as cur:
            cur.execute("SELECT id, description FROM VLANDomain")
            domains = cur.fetchall()
        groups = []
        for domain_id, desc in domains:
            group_name = desc.strip() if desc and desc.strip() else f"VLAN-Domain-{domain_id}"
            group_slug = slugify.slugify(group_name) or f"vlan-domain-{domain_id}"
            groups.append({
                "domain_id": domain_id,
                "name": group_name,
                "slug": group_slug,
                "description": f"Migrated from RackTables VLANDomain (ID: {domain_id})"
            })
        return groups

    def export_vlans(self):
        logger.info("Extracting VLANs from RackTables...")
        with self.con.cursor() as cur:
            cur.execute("SELECT domain_id, vlan_id, vlan_type, vlan_descr FROM VLANDescription")
            vlan_rows = cur.fetchall()
        
        seen_names = defaultdict(set)
        vlans = []
        for domain_id, vlan_id, vlan_type, vlan_descr in vlan_rows:
            base_name = clean_rt_text(vlan_descr) if vlan_descr else f"VLAN_{vlan_id}"
            if not base_name:
                base_name = f"VLAN_{vlan_id}"

            clean_name = base_name
            if clean_name in seen_names[domain_id]:
                clean_name = f"{base_name} (VID {vlan_id})"
            seen_names[domain_id].add(clean_name)

            vlan_slug = slugify.slugify(clean_name) or f"vlan-{domain_id}-{vlan_id}"
            vlan_desc = f"Type: {vlan_type}"
            if vlan_descr:
                vlan_desc += f" | {vlan_descr}"

            vlans.append({
                "domain_id": domain_id,
                "vlan_id": vlan_id,
                "vid": int(vlan_id),
                "base_name": base_name,
                "clean_name": clean_name,
                "slug": vlan_slug,
                "description": vlan_desc[:500]
            })
        return vlans

    def export_prefixes(self):
        logger.info("Extracting IPv4/IPv6 Prefixes from RackTables...")
        v4_vlan_map = {}
        with self.con.cursor() as cur:
            cur.execute("SELECT domain_id, vlan_id, ipv4net_id FROM VLANIPv4")
            for d_id, v_id, net_id in cur.fetchall():
                v4_vlan_map[net_id] = [d_id, v_id]

        with self.con.cursor() as cur:
            cur.execute("SELECT id, ip, mask, name, comment FROM IPv4Network")
            v4_nets = cur.fetchall()

        v4_prefixes = []
        for sid, raw_ip, mask, name, comment in v4_nets:
            ip_str = self.convert_ip(raw_ip)
            cidr = f"{ip_str}/{mask}"
            desc_parts = [p for p in [name, comment] if p and str(p).strip()]
            full_desc = clean_rt_text(" - ".join(desc_parts)) if desc_parts else ""
            vlan_ref = v4_vlan_map.get(sid)

            v4_prefixes.append({
                "id": sid,
                "cidr": cidr,
                "version": 4,
                "description": full_desc,
                "vlan_ref": vlan_ref
            })

        v6_vlan_map = {}
        with self.con.cursor() as cur:
            try:
                cur.execute("SELECT domain_id, vlan_id, ipv6net_id FROM VLANIPv6")
                for d_id, v_id, net_id in cur.fetchall():
                    v6_vlan_map[net_id] = [d_id, v_id]
            except Exception:
                pass

        with self.con.cursor() as cur:
            try:
                cur.execute("SELECT id, ip, mask, name, comment FROM IPv6Network")
                v6_nets = cur.fetchall()
            except Exception:
                v6_nets = []

        v6_prefixes = []
        for sid, raw_ip, mask, name, comment in v6_nets:
            try:
                ip_str = self.convert_ipv6(raw_ip)
                cidr = f"{ip_str}/{mask}"
                desc_parts = [p for p in [name, comment] if p and str(p).strip()]
                full_desc = clean_rt_text(" - ".join(desc_parts)) if desc_parts else ""
                vlan_ref = v6_vlan_map.get(sid)

                v6_prefixes.append({
                    "id": sid,
                    "cidr": cidr,
                    "version": 6,
                    "description": full_desc,
                    "vlan_ref": vlan_ref
                })
            except Exception as e:
                logger.error(f"Error exporting IPv6 Prefix: {e}")

        return {"v4": v4_prefixes, "v6": v6_prefixes}

    def export_infrastructure(self):
        logger.info("Extracting Sites, Locations, Racks from RackTables...")
        sites_map = {}
        rooms_map = {}

        with self.con.cursor() as cur:
            cur.execute("SELECT id, name, parent_id, parent_name FROM Location")
            for location_id, location_name, parent_id, parent_name in cur.fetchall():
                if not parent_name:
                    sites_map[location_id] = location_name
                else:
                    rooms_map[location_name] = parent_name

        with self.con.cursor() as cur:
            cur.execute("SELECT id, name, height, row_id, row_name, location_id, location_name FROM Rack;")
            raw_racks = cur.fetchall()

        racks = []
        for rec in raw_racks:
            rack_id, rack_name, height, row_id, row_name, location_id, location_name = rec
            racks.append({
                "id": rack_id,
                "name": rack_name,
                "height": height or 42,
                "location_name": location_name
            })

        return {
            "sites_map": sites_map,
            "rooms_map": rooms_map,
            "racks": racks
        }

    def export_hardware(self):
        logger.info("Extracting Hardware definitions and computing u_height from RackSpace...")
        # 1. Map all object_ids to their mounted unit height in RackSpace
        with self.con.cursor() as cur:
            cur.execute("SELECT object_id, unit_no FROM RackSpace")
            raw_space = cur.fetchall()

        hwsize_map = defaultdict(list)
        for obj_id, unit_no in raw_space:
            if unit_no is not None and str(unit_no).isdigit():
                hwsize_map[obj_id].append(int(unit_no))

        obj_height_map = {}
        for obj_id, units in hwsize_map.items():
            if units:
                obj_height_map[obj_id] = max(units) - min(units) + 1

        # 2. Collect hardware names from AttributeValue (attr_id 2, 22)
        hw_map = {}
        with self.con.cursor() as cur:
            cur.execute("SELECT object_id, dict_key, dict_value FROM AttributeValue LEFT JOIN Dictionary ON AttributeValue.uint_value = Dictionary.dict_key WHERE attr_id IN (2, 22);")
            for obj_id, dict_key, dict_value in cur.fetchall():
                if dict_value:
                    hw_map[obj_id] = dict_value.replace('%GSKIP%', ' ').replace('%GPASS%', ' ').replace('\t', ' ').strip()

        model_info = {}

        def record_model(obj_id, raw_hw_name, default_h=1, force_h=None):
            hardware_clean = clean_rt_text(raw_hw_name)
            parts = hardware_clean.split()
            vendor = clean_rt_text(parts[0]) if parts else 'Generic'
            model = clean_rt_text(' '.join(parts[1:])) if len(parts) > 1 else hardware_clean

            # If vendor or model is 'Generic' or contains 'generic', fix height to 1U
            if vendor.lower() == 'generic' or model.lower() == 'generic' or 'generic' in model.lower():
                force_h = 1

            h = force_h if force_h is not None else obj_height_map.get(obj_id, default_h)
            if model not in model_info:
                model_info[model] = {
                    "hardware_clean": hardware_clean,
                    "vendor": vendor,
                    "model": model,
                    "u_height": max(1, h)
                }
            else:
                if force_h is None:
                    model_info[model]["u_height"] = max(model_info[model]["u_height"], max(1, h))
                else:
                    model_info[model]["u_height"] = max(1, force_h)

        # A. Process explicit Hardware attributes
        for obj_id, hw_name in hw_map.items():
            record_model(obj_id, hw_name)

        # B. Process Devices (Server Hardware / HW type attributes or default)
        with self.con.cursor() as cur:
            q = """SELECT
                    Object.id,
                    Attribute.name,
                    Dictionary.dict_value
                FROM Object
                LEFT JOIN AttributeValue ON Object.id = AttributeValue.object_id
                LEFT JOIN Attribute ON AttributeValue.attr_id = Attribute.id
                LEFT JOIN Dictionary ON Dictionary.dict_key = AttributeValue.uint_value
                WHERE Object.objtype_id NOT IN (2,9,1504,1505,1560,1561,1562,50015,50275);"""
            cur.execute(q)
            dev_attr_rows = cur.fetchall()

        dev_hw_map = {}
        for obj_id, attr_name, attr_val in dev_attr_rows:
            if attr_name in ('Server Hardware', 'HW type') and attr_val:
                dev_hw_map[obj_id] = attr_val.replace('%GSKIP%', ' ').replace('%GPASS%', ' ').replace('\t', ' ').strip()

        for obj_id, hw_name in dev_hw_map.items():
            record_model(obj_id, hw_name)

        # C. Process PDUs (Fixed at 1U)
        with self.con.cursor() as cur:
            cur.execute("SELECT Object.id, Dictionary.dict_value FROM Object LEFT JOIN AttributeValue ON Object.id = AttributeValue.object_id AND AttributeValue.attr_id = 24 LEFT JOIN Dictionary ON AttributeValue.uint_value = Dictionary.dict_key WHERE Object.objtype_id = 2;")
            for pdu_id, hw_val in cur.fetchall():
                record_model(pdu_id, hw_val or "Generic PDU", force_h=1)

        # D. Process Patch Panels (Fixed at 1U)
        with self.con.cursor() as cur:
            cur.execute("SELECT Object.id, AttributeValue.string_value FROM Object LEFT JOIN AttributeValue ON Object.id = AttributeValue.object_id AND AttributeValue.attr_id = 26 WHERE Object.objtype_id = 9;")
            for pp_id, port_count in cur.fetchall():
                ports = int(port_count) if port_count and str(port_count).isdigit() else 24
                record_model(pp_id, f"Generic Patch Panel {ports}-Port", force_h=1)

        return list(model_info.values())

    def export_pdus(self):
        logger.info("Extracting PDUs from RackTables...")
        with self.con.cursor() as cur:
            q = """SELECT
                    Object.id as pdu_id,
                    Object.name as Name,
                    AttributeValue.string_value as Ports,
                    Dictionary.dict_value as Hardware,
                    Object.comment as Comment,
                    COALESCE(RackSpace.rack_id, EntityLink.parent_entity_id) AS rack_id,
                    RackSpace.unit_no,
                    RackSpace.atom
                FROM Object
                LEFT JOIN AttributeValue ON Object.id = AttributeValue.object_id AND AttributeValue.attr_id = 24
                LEFT JOIN Dictionary ON AttributeValue.uint_value = Dictionary.dict_key
                LEFT JOIN RackSpace ON Object.id = RackSpace.object_id
                LEFT JOIN EntityLink ON Object.id = EntityLink.child_entity_id
                    AND EntityLink.child_entity_type = 'object'
                    AND EntityLink.parent_entity_type = 'rack'
                WHERE Object.objtype_id = 2;"""
            cur.execute(q)

            rows = cur.fetchall()

        pdu_rows_map = defaultdict(list)
        for row in rows:
            pdu_rows_map[row[0]].append(row)

        pdus = []
        for pdu_id, p_rows in pdu_rows_map.items():
            first = p_rows[0]
            name, ports, hardware, comment, rack_id, unit_no = first[1], first[2], first[3], first[4], first[5], first[6]
            atoms = {r[7] for r in p_rows if r[7]}
            face = determine_face(atoms)

            hw_clean = clean_rt_text(hardware or "Generic PDU")
            parts = hw_clean.split()
            vendor = clean_rt_text(parts[0]) if parts else 'Generic'
            model = clean_rt_text(' '.join(parts[1:])) if len(parts) > 1 else hw_clean

            pdus.append({
                "pdu_id": pdu_id,
                "name": name or f"PDU-{pdu_id}",
                "ports": ports,
                "vendor": vendor,
                "model": model,
                "hardware": hw_clean,
                "comment": comment or "",
                "rack_id": rack_id,
                "unit_no": unit_no,
                "face": face
            })
        return pdus

    def export_patch_panels(self):
        logger.info("Extracting Patch Panels from RackTables...")
        with self.con.cursor() as cur:
            q = """SELECT
                    Object.id as id,
                    Object.name as Name,
                    AttributeValue.string_value as Ports,
                    Object.comment as Comment,
                    COALESCE(RackSpace.rack_id, EntityLink.parent_entity_id) AS rack_id,
                    RackSpace.unit_no,
                    RackSpace.atom
                FROM Object
                LEFT JOIN AttributeValue ON Object.id = AttributeValue.object_id AND AttributeValue.attr_id = 26
                LEFT JOIN RackSpace ON Object.id = RackSpace.object_id
                LEFT JOIN EntityLink ON Object.id = EntityLink.child_entity_id
                    AND EntityLink.child_entity_type = 'object'
                    AND EntityLink.parent_entity_type = 'rack'
                WHERE Object.objtype_id = 9;"""
            cur.execute(q)

            rows = cur.fetchall()

        panel_rows_map = defaultdict(list)
        for row in rows:
            panel_rows_map[row[0]].append(row)

        panels = []
        for id, p_rows in panel_rows_map.items():
            first = p_rows[0]
            name, ports, comment, rack_id, unit_no = first[1], first[2], first[3], first[4], first[5]
            atoms = {r[6] for r in p_rows if r[6]}
            face = determine_face(atoms)

            panels.append({
                "id": id,
                "name": name or f"Patch Panel {id}",
                "port_count": ports,
                "comment": comment or "",
                "rack_id": rack_id,
                "unit_no": unit_no,
                "face": face
            })
        return panels

    def get_vm_objtype_ids(self):
        """
        conf の [VirtualMachineObjectTypes] 設定および DB Dictionary テーブルから
        VirtualMachine として扱う objtype_id の集合 (set) を動的に取得・判定する。
        """
        default_ids = {1504, 50015, 50034}
        if not self.config.has_section('VirtualMachineObjectTypes'):
            return default_ids

        configured_keys = set()
        for key, val in self.config['VirtualMachineObjectTypes'].items():
            if str(val).strip().lower() in ('true', '1', 'yes', 'on'):
                configured_keys.add(key.strip().lower())

        if not configured_keys:
            return default_ids

        vm_ids = set()
        for k in configured_keys:
            if k.isdigit():
                vm_ids.add(int(k))

        try:
            with self.con.cursor() as cur:
                cur.execute("SELECT dict_key, dict_value FROM Dictionary WHERE chapter_id = 1;")
                for dict_key, dict_value in cur.fetchall():
                    val_clean = clean_rt_text(dict_value or "").strip().lower()
                    if val_clean in configured_keys:
                        vm_ids.add(dict_key)
        except Exception as e:
            logger.warning(f"Could not fetch ObjectType Dictionary: {e}")

        return vm_ids or default_ids

    def export_devices(self):
        logger.info("Extracting Devices from RackTables...")
        vm_objtype_ids = self.get_vm_objtype_ids()
        base_exclude_ids = {2, 9, 1505, 1560, 1561, 1562, 50275}
        exclude_ids = base_exclude_ids.union(vm_objtype_ids)
        exclude_str = ", ".join(map(str, exclude_ids))
        with self.con.cursor() as cur:
            q = f"""SELECT
                    Object.id as dev_id,
                    Object.objtype_id,
                    ObjTypeDict.dict_value as objtype_name,
                    Object.name as Description,
                    Object.label as Name,
                    Object.asset_no as Asset,
                    Attribute.name as AttrName,
                    Dictionary.dict_value as AttrValue,
                    Object.comment as Comment,
                    COALESCE(RackSpace.rack_id, EntityLinkRack.parent_entity_id) as RackID,
                    RackSpace.unit_no as RackPosition,
                    Rack.name as rack_name,
                    Rack.location_name,
                    EntityLinkCluster.parent_entity_id as cluster_id,
                    RackSpace.atom
                FROM Object
                LEFT JOIN Dictionary ObjTypeDict ON Object.objtype_id = ObjTypeDict.dict_key
                LEFT JOIN AttributeValue ON Object.id = AttributeValue.object_id
                LEFT JOIN Attribute ON AttributeValue.attr_id = Attribute.id
                LEFT JOIN Dictionary ON Dictionary.dict_key = AttributeValue.uint_value
                LEFT JOIN RackSpace ON Object.id = RackSpace.object_id
                LEFT JOIN EntityLink EntityLinkRack ON Object.id = EntityLinkRack.child_entity_id
                    AND EntityLinkRack.child_entity_type = 'object'
                    AND EntityLinkRack.parent_entity_type = 'rack'
                LEFT JOIN EntityLink EntityLinkCluster ON Object.id = EntityLinkCluster.child_entity_id
                    AND EntityLinkCluster.child_entity_type = 'object'
                    AND EntityLinkCluster.parent_entity_type = 'object'
                    AND EntityLinkCluster.parent_entity_id IN (SELECT id FROM Object WHERE objtype_id = 1505)
                LEFT JOIN Rack ON COALESCE(RackSpace.rack_id, EntityLinkRack.parent_entity_id) = Rack.id
                WHERE Object.objtype_id NOT IN ({exclude_str});"""
            cur.execute(q)
            raw_rows = cur.fetchall()

        device_rows_map = defaultdict(list)
        for row in raw_rows:
            device_rows_map[row[0]].append(row)

        devices = []
        for dev_id, rows in device_rows_map.items():
            first = rows[0]
            dev_type_id = first[1]
            objtype_name = first[2] or ""
            description = first[3] or ""
            label_name = first[4] or ""
            asset = first[5] or ""
            comment = first[8] or ""
            rack_id = first[9]
            rack_position = first[10]
            cluster_id = first[13]
            atoms = {r[14] for r in rows if r[14]}
            face = determine_face(atoms)

            dev_name = description or label_name or f"Device-{dev_id}"
            hardware = "Generic Device"
            for r in rows:
                attr_name = r[6]
                attr_val = r[7]
                if attr_name in ('Server Hardware', 'HW type') and attr_val:
                    hardware = attr_val.replace('%GSKIP%', ' ').replace('%GPASS%', ' ').replace('\t', ' ').strip()
                    break

            hardware_clean = clean_rt_text(hardware)
            parts = hardware_clean.split()
            vendor = clean_rt_text(parts[0]) if parts else 'Generic'
            model = clean_rt_text(' '.join(parts[1:])) if len(parts) > 1 else hardware_clean

            devices.append({
                "dev_id": dev_id,
                "dev_type_id": dev_type_id,
                "objtype_name": clean_rt_text(objtype_name),
                "dev_name": dev_name,
                "asset": asset,
                "hardware": hardware_clean,
                "vendor": vendor,
                "model": model,
                "comment": comment,
                "rack_id": rack_id,
                "rack_position": rack_position,
                "cluster_id": cluster_id,
                "face": face
            })

        return devices

    def export_vm_clusters(self):
        logger.info("Extracting VM Clusters from RackTables...")
        with self.con.cursor() as cur:
            q = """SELECT id, name, comment FROM Object WHERE objtype_id = 1505;"""
            cur.execute(q)
            rows = cur.fetchall()

        clusters = []
        self.cluster_name_map = {}
        for cluster_id, name, comment in rows:
            c_name = name or f"Cluster-{cluster_id}"
            self.cluster_name_map[cluster_id] = c_name
            clusters.append({
                "cluster_id": cluster_id,
                "name": c_name,
                "comment": comment or ""
            })
        return clusters

    def export_virtual_machines(self):
        logger.info("Extracting Virtual Machines, Zones & LDOMs from RackTables...")
        vm_objtype_ids = self.get_vm_objtype_ids()
        ids_str = ", ".join(map(str, vm_objtype_ids))
        with self.con.cursor() as cur:
            q = f"""SELECT
                    o.id as vm_id,
                    o.objtype_id,
                    o.name as vm_name,
                    o.comment as comment,
                    el_cluster.parent_entity_id as cluster_id,
                    el_device.parent_entity_id as parent_device_id,
                    rs.rack_id
                FROM Object o
                LEFT JOIN EntityLink el_cluster ON o.id = el_cluster.child_entity_id
                    AND el_cluster.child_entity_type = 'object'
                    AND el_cluster.parent_entity_type = 'object'
                    AND el_cluster.parent_entity_id IN (SELECT id FROM Object WHERE objtype_id = 1505)
                LEFT JOIN EntityLink el_device ON o.id = el_device.child_entity_id
                    AND el_device.child_entity_type = 'object'
                    AND el_device.parent_entity_type = 'object'
                    AND el_device.parent_entity_id NOT IN (SELECT id FROM Object WHERE objtype_id = 1505)
                LEFT JOIN RackSpace rs ON el_device.parent_entity_id = rs.object_id
                WHERE o.objtype_id IN ({ids_str});"""
            cur.execute(q)
            rows = cur.fetchall()

        vms_raw = defaultdict(list)
        for row in rows:
            vm_id = row[0]
            vms_raw[vm_id].append(row)

        vms = []
        cluster_name_map = getattr(self, 'cluster_name_map', {})

        for vm_id, row_list in vms_raw.items():
            first_row = row_list[0]
            objtype_id = first_row[1]
            vm_name = first_row[2]
            comment = first_row[3]
            parent_device_id = first_row[5]
            rack_id = first_row[6]

            # Extract distinct non-null cluster_ids
            cluster_ids = list(dict.fromkeys([r[4] for r in row_list if r[4] is not None]))

            selected_cluster_id = None
            if len(cluster_ids) > 1:
                cluster_names = [cluster_name_map.get(cid, f"Cluster-{cid}") for cid in cluster_ids]
                logger.error(
                    f"ERROR: Virtual Machine '{vm_name}' (ID: {vm_id}) is linked to MULTIPLE clusters in RackTables: {cluster_names}"
                )

                # Naming rule selection: e.g. xe -> xen/xenpool
                vm_name_lower = (vm_name or "").lower()
                for cid in cluster_ids:
                    c_name_lower = (cluster_name_map.get(cid, "") or "").lower()
                    if ("xe." in vm_name_lower or "xen" in vm_name_lower or ".xe" in vm_name_lower) and ("xen" in c_name_lower or "xenpool" in c_name_lower):
                        selected_cluster_id = cid
                        break
                    elif ("px" in vm_name_lower or "pve" in vm_name_lower or "proxmox" in vm_name_lower) and ("pve" in c_name_lower or "proxmox" in c_name_lower):
                        selected_cluster_id = cid
                        break

                if not selected_cluster_id:
                    selected_cluster_id = cluster_ids[0]

                sel_cname = cluster_name_map.get(selected_cluster_id, f"Cluster-{selected_cluster_id}")
                logger.info(f"Selected Cluster '{sel_cname}' (ID: {selected_cluster_id}) for VM '{vm_name}' based on naming rules.")
            elif len(cluster_ids) == 1:
                selected_cluster_id = cluster_ids[0]

            vms.append({
                "vm_id": vm_id,
                "objtype_id": objtype_id,
                "vm_name": vm_name or f"VM-{vm_id}",
                "comment": comment or "",
                "cluster_id": selected_cluster_id,
                "parent_device_id": parent_device_id,
                "rack_id": rack_id
            })
        return vms

    def export_device_ips(self):
        logger.info("Extracting Device IP Assignments from RackTables...")
        device_ips = []
        with self.con.cursor() as cur:
            q = """
                SELECT a.object_id, a.ip, a.name AS alloc_name, ad.name AS addr_name, a.type
                FROM IPv4Allocation a
                LEFT JOIN IPv4Address ad ON a.ip = ad.ip;
            """
            cur.execute(q)
            for object_id, raw_ip, alloc_name, addr_name, alloc_type in cur.fetchall():
                ip_str = self.convert_ip(raw_ip)
                device_ips.append({
                    "object_id": object_id,
                    "ip_str": ip_str,
                    "is_ipv6": False,
                    "name": clean_rt_text(alloc_name or ""),
                    "addr_name": clean_rt_text(addr_name or ""),
                    "alloc_type": alloc_type or "regular"
                })

        with self.con.cursor() as cur:
            try:
                q = """
                    SELECT a.object_id, a.ip, a.name AS alloc_name, ad.name AS addr_name, a.type
                    FROM IPv6Allocation a
                    LEFT JOIN IPv6Address ad ON a.ip = ad.ip;
                """
                cur.execute(q)
                for object_id, raw_ip, alloc_name, addr_name, alloc_type in cur.fetchall():
                    ip_str = self.convert_ipv6(raw_ip)
                    device_ips.append({
                        "object_id": object_id,
                        "ip_str": ip_str,
                        "is_ipv6": True,
                        "name": clean_rt_text(alloc_name or ""),
                        "addr_name": clean_rt_text(addr_name or ""),
                        "alloc_type": alloc_type or "regular"
                    })
            except Exception as e:
                logger.warning(f"Could not fetch IPv6Allocation: {e}")

        return device_ips

    def export_ports(self):
        """RackTables Port テーブルからポート情報を抽出する。"""
        logger.info("Extracting Ports from RackTables...")
        ports = []
        with self.con.cursor() as cur:
            q = """
                SELECT
                    p.id, p.object_id, p.name, p.iif_id, p.type AS oif_id,
                    p.l2address, p.label, p.reservation_comment,
                    pii.iif_name,
                    poi.oif_name,
                    o.name AS obj_name
                FROM Port p
                LEFT JOIN Object o ON p.object_id = o.id
                LEFT JOIN PortInnerInterface pii ON p.iif_id = pii.id
                LEFT JOIN PortOuterInterface poi ON p.type = poi.id
            """
            cur.execute(q)
            for row in cur.fetchall():
                rt_port_id, object_id, name, iif_id, oif_id, l2address, label, reservation_comment, iif_name, oif_name, obj_name = row
                # l2address を AA:BB:CC:DD:EE:FF 形式に変換
                mac_str = ""
                if l2address:
                    try:
                        if isinstance(l2address, (bytes, bytearray)):
                            mac_hex = l2address.hex()
                        else:
                            mac_hex = str(l2address).replace(":", "").replace("-", "")
                        if len(mac_hex) == 12:
                            mac_str = ":".join(mac_hex[i:i+2] for i in range(0, 12, 2)).upper()
                    except Exception:
                        mac_str = ""
                ports.append({
                    "rt_port_id": rt_port_id,
                    "object_id": object_id,
                    "obj_name": clean_rt_text(obj_name or ""),
                    "name": clean_rt_text(name or ""),
                    "iif_name": iif_name or "",
                    "oif_name": oif_name or "",
                    "oif_id": oif_id,
                    "mac_address": mac_str,
                    "label": clean_rt_text(label or ""),
                    "reservation_comment": clean_rt_text(reservation_comment or "")
                })
        logger.info(f"Extracted {len(ports)} ports from RackTables.")
        return ports

    def export_cables(self):
        """RackTables Link テーブルからケーブル接続情報を抽出する。"""
        logger.info("Extracting Cable Links from RackTables...")
        cables = []
        with self.con.cursor() as cur:
            q = """
                SELECT
                    L.porta, L.portb, L.cable,
                    pa.object_id AS obj_a, pa.name AS port_a_name,
                    pb.object_id AS obj_b, pb.name AS port_b_name
                FROM Link L
                JOIN Port pa ON L.porta = pa.id
                JOIN Port pb ON L.portb = pb.id
            """
            cur.execute(q)
            for row in cur.fetchall():
                porta_id, portb_id, cable_label, obj_a, port_a_name, obj_b, port_b_name = row
                cables.append({
                    "porta_id": porta_id,
                    "portb_id": portb_id,
                    "cable_label": clean_rt_text(cable_label or ""),
                    "obj_a": obj_a,
                    "port_a_name": clean_rt_text(port_a_name or ""),
                    "obj_b": obj_b,
                    "port_b_name": clean_rt_text(port_b_name or "")
                })
        logger.info(f"Extracted {len(cables)} cable links from RackTables.")
        return cables


class NetBoxImporter:
    """
    Imports data from JSON structure into NetBox using pynetbox REST API,
    or validates migration data and mappings in dry-run mode without modifying NetBox.
    """

    BUILTIN_OIF_MAP = {
        "10base-t":               "10base-t",
        "100base-tx":             "100base-tx",
        "1000base-t":             "1000base-t",
        "2.5gbase-t":             "2.5gbase-t",
        "2.5gbase-x":             "2.5gbase-x",
        "5gbase-t":               "5gbase-t",
        "5gbase-x":               "5gbase-x",
        "10gbase-t":              "10gbase-t",
        "25gbase-t":              "25gbase-t",
        "1000base-sx":            "1000base-x-sfp",
        "1000base-lx":            "1000base-x-sfp",
        "1000base-zx":            "1000base-x-sfp",
        "empty sfp":              "1000base-x-sfp",
        "empty sfp-1000":         "1000base-x-sfp",
        "10gbase-sr":             "10gbase-x-sfpp",
        "10gbase-lr":             "10gbase-x-sfpp",
        "10gbase-er":             "10gbase-x-sfpp",
        "10gbase-cx4":            "10gbase-cx4",
        "empty sfp+":             "10gbase-x-sfpp",
        "twinax 10g sfp+ dac":    "10gbase-x-sfpp",
        "10g sfp+ dac":           "10gbase-x-sfpp",
        "25gbase-sr":             "25gbase-x-sfp28",
        "25gbase-lr":             "25gbase-x-sfp28",
        "empty sfp28":            "25gbase-x-sfp28",
        "twinax 25g sfp28 dac":   "25gbase-x-sfp28",
        "25g sfp28 dac":          "25gbase-x-sfp28",
        "40gbase-sr4":            "40gbase-x-qsfpp",
        "40gbase-lr4":            "40gbase-x-qsfpp",
        "empty qsfp+":            "40gbase-x-qsfpp",
        "twinax 40g qsfp+ dac":   "40gbase-x-qsfpp",
        "40g qsfp+ dac":          "40gbase-x-qsfpp",
        "100gbase-sr4":           "100gbase-x-qsfp28",
        "100gbase-lr4":           "100gbase-x-qsfp28",
        "100gbase-cwdm4":         "100gbase-x-qsfp28",
        "empty qsfp28":           "100gbase-x-qsfp28",
        "virtual port":           "virtual",
        "kvm":                    "virtual",
        "kvm (host)":             "virtual",
        "kvm (console)":          "virtual",
        "usb":                    "other",
        "usb2.0":                 "other",
        "usb3.0":                 "other",
        "rs-232 (rj-45)":         "console-port:rj-45",
        "rs-232 (db-9)":          "console-port:de-9",
        "rs-232 (rj-11)":         "console-port:rj-11",
        "rs-232":                 "console-port:rj-45",
        "console":                "console-port:rj-45",
        "jj lc-lc":               "other",
        "jj rj-45":               "8p8c",
        "jj sc-sc":               "other",
        "minisas sff-8088(6gb)":  "other",
        "minisas sff-8644(12gb)": "other",
        "mini-sas sff-8088(6gb)": "other",
        "mini-sas sff-8644(12gb)": "other",
    }

    def __init__(self, config, netbox_api, dry_run=False):
        self.config = config
        self.netbox = netbox_api
        self.dry_run = dry_run
        self.vlan_groups_map = {}
        self.vlans_map = {}
        self.rack_id_map = {}
        self.device_netbox_map = {}
        self.vm_netbox_map = {}
        self.cluster_netbox_map = {}
        self.device_type_cache = {}
        self.hardware_u_height_map = {}
        self.ipv4_prefixes = []
        self.ipv6_prefixes = []
        # {rt_port_id: {"obj": nb_interface_obj, "target_type": "device"|"vm"}}
        self.port_netbox_map = {}
        # {("device"|"vm", nb_obj_id, ifname): nb_interface_obj} — import_device_ips で素早検索用
        self.interface_name_map = {}
        self.prefix_netbox_map = {}

        # NetBox バージョン検出 — MACAddress モデルは 4.2+ で必要
        if self.dry_run or self.netbox is None:
            self.netbox_version = "0.0.0"
            self.use_mac_address_model = False
        else:
            from packaging.version import Version
            try:
                status = self.netbox.status()
                self.netbox_version = status.get("netbox-version", "0.0.0")
            except Exception as e:
                logger.warning(f"Could not detect NetBox version: {e}. Assuming legacy mac_address field.")
                self.netbox_version = "0.0.0"
            self.use_mac_address_model = Version(self.netbox_version) >= Version("4.2.0")
            logger.info(
                f"NetBox {self.netbox_version}: "
                + ("Using MACAddress model (dcim.mac_addresses) for MAC migration."
                   if self.use_mac_address_model
                   else "Using legacy Interface.mac_address field for MAC migration.")
            )

    def _load_port_config(self):
        """Load port type mappings and port exclusion rules from configuration."""
        custom_map = {}
        if self.config.has_section('PortTypeMapping'):
            for oif_key, nb_type in self.config['PortTypeMapping'].items():
                custom_map[oif_key.strip().lower()] = nb_type.strip()

        power_port_custom = {}
        if self.config.has_section('PowerPortTypeMapping'):
            for oif_key, nb_type in self.config['PowerPortTypeMapping'].items():
                power_port_custom[oif_key.strip().lower()] = nb_type.strip()

        power_outlet_custom = {}
        if self.config.has_section('PowerOutletTypeMapping'):
            for oif_key, nb_type in self.config['PowerOutletTypeMapping'].items():
                power_outlet_custom[oif_key.strip().lower()] = nb_type.strip()

        ignore_port_prefixes = []
        ignore_oif_patterns = []
        if self.config.has_section('ExcludePorts'):
            prefixes_str = self.config['ExcludePorts'].get('IGNORE_PORT_PREFIXES', '').strip()
            if prefixes_str:
                ignore_port_prefixes = [p.strip().lower() for p in prefixes_str.split(',') if p.strip()]
            patterns_str = self.config['ExcludePorts'].get('IGNORE_OIF_PATTERNS', '').strip()
            if patterns_str:
                ignore_oif_patterns = [p.strip().lower() for p in patterns_str.split(',') if p.strip()]

        return custom_map, power_port_custom, power_outlet_custom, ignore_port_prefixes, ignore_oif_patterns

    @staticmethod
    def _is_port_excluded(port_name, oif_name, ignore_port_prefixes, ignore_oif_patterns):
        """Return True if the port matches configured prefix or OIF exclusion rules."""
        port_name_lower = (port_name or "").strip().lower()
        oif_name_lower = (oif_name or "").strip().lower()
        for prefix in ignore_port_prefixes:
            if port_name_lower.startswith(prefix):
                return True
        for pattern in ignore_oif_patterns:
            if pattern in oif_name_lower:
                return True
        return False

    def _resolve_port_type(self, oif_name, object_id, port_name, custom_map, power_port_custom, power_outlet_custom, log_unknown=True):
        """Resolve RackTables OIF name to NetBox interface type, logging unknown OIFs to error.log."""
        key = (oif_name or "").strip().lower()
        if key in custom_map:
            return custom_map[key]
        if key in self.BUILTIN_OIF_MAP:
            return self.BUILTIN_OIF_MAP[key]
        if key in power_port_custom or key in power_outlet_custom or key in ("ac-in", "ac-out", "dc"):
            return "other"
        if log_unknown:
            logger.error(
                f"Unknown OIF type '{oif_name}' for object_id={object_id} port='{port_name}': "
                f"falling back to 'other'. Add '[PortTypeMapping]' entry in conf to suppress this error."
            )
        return "other"

    def get_default_site(self):
        try:
            na_site = self.netbox.dcim.sites.get(slug='na')
            if not na_site:
                na_site = self.netbox.dcim.sites.get(name='N/A')
            if na_site:
                return na_site
        except Exception as e:
            logger.warning(f"Error querying N/A site: {e}")

        try:
            all_sites = list(self.netbox.dcim.sites.all())
            return all_sites[0] if all_sites else None
        except Exception:
            return None

    def get_best_cidr(self, ip_str):
        if not ip_str:
            return ip_str
        if "/" in ip_str:
            return ip_str

        try:
            ip_obj = ipaddress.ip_address(ip_str.strip())
        except Exception:
            return f"{ip_str}/32"

        if ip_obj.version == 4:
            matched_prefixes = [p for p in getattr(self, 'ipv4_prefixes', []) if ip_obj in p]
            if matched_prefixes:
                best_prefix = max(matched_prefixes, key=lambda p: p.prefixlen)
                return f"{ip_str}/{best_prefix.prefixlen}"
            return f"{ip_str}/32"
        else:
            matched_prefixes = [p for p in getattr(self, 'ipv6_prefixes', []) if ip_obj in p]
            if matched_prefixes:
                best_prefix = max(matched_prefixes, key=lambda p: p.prefixlen)
                return f"{ip_str}/{best_prefix.prefixlen}"
            return f"{ip_str}/128"

    def run_dry_run(self, data):
        """
        Validate extracted RackTables data, CIDR prefix resolution, port OIF mappings,
        and cable endpoints without writing any changes to NetBox.
        """
        logger.info("[DRY-RUN] Validating extracted data and NetBox mappings (no changes will be written)...")

        prefixes_data = data.get("prefixes", {})
        self.ipv4_prefixes = []
        for p in prefixes_data.get("v4", []):
            try:
                self.ipv4_prefixes.append(ipaddress.ip_network(p["cidr"], strict=False))
            except ValueError as e:
                logger.error(f"[DRY-RUN] Invalid IPv4 prefix '{p.get('cidr')}': {e}")

        self.ipv6_prefixes = []
        for p in prefixes_data.get("v6", []):
            try:
                self.ipv6_prefixes.append(ipaddress.ip_network(p["cidr"], strict=False))
            except ValueError as e:
                logger.error(f"[DRY-RUN] Invalid IPv6 prefix '{p.get('cidr')}': {e}")

        device_ids = {d["dev_id"] for d in data.get("devices", [])}
        device_ids.update(p["pdu_id"] for p in data.get("pdus", []))
        device_ids.update(pp["id"] for pp in data.get("patch_panels", []))
        vm_ids = {v["vm_id"] for v in data.get("virtual_machines", [])}

        custom_map, power_port_custom, power_outlet_custom, ignore_prefixes, ignore_patterns = self._load_port_config()
        valid_ports = {}
        ports_checked = ports_skipped = 0

        for port in data.get("ports", []):
            rt_port_id = port["rt_port_id"]
            object_id = port["object_id"]
            name = port["name"] or f"port-{rt_port_id}"
            oif_name = port["oif_name"]

            if self._is_port_excluded(name, oif_name, ignore_prefixes, ignore_patterns):
                ports_skipped += 1
                continue

            if object_id in device_ids:
                target_type = "device"
            elif object_id in vm_ids:
                target_type = "vm"
            else:
                logger.warning(
                    f"[DRY-RUN] Port '{name}' (rt_port_id={rt_port_id}): "
                    f"parent object_id={object_id} not found in extracted devices/VMs. Skipping."
                )
                ports_skipped += 1
                continue

            self._resolve_port_type(oif_name, object_id, name, custom_map, power_port_custom, power_outlet_custom, log_unknown=True)
            valid_ports[rt_port_id] = target_type
            ports_checked += 1

        cables_checked = cables_skipped = 0
        for cable in data.get("cables", []):
            porta_id = cable["porta_id"]
            portb_id = cable["portb_id"]
            cable_label = cable.get("cable_label", "") or ""
            a_type = valid_ports.get(porta_id)
            b_type = valid_ports.get(portb_id)

            if not a_type or not b_type:
                logger.warning(
                    f"[DRY-RUN] Cable '{cable_label}': one or both ports not found "
                    f"(porta_id={porta_id}, portb_id={portb_id}). Skipping."
                )
                cables_skipped += 1
                continue

            if a_type != "device" or b_type != "device":
                logger.error(
                    f"Cable '{cable_label}': cables involving VM interfaces are not supported "
                    f"(porta={cable['port_a_name']} obj={cable['obj_a']}, "
                    f"portb={cable['port_b_name']} obj={cable['obj_b']}). Skipping."
                )
                cables_skipped += 1
                continue
            cables_checked += 1

        ips_checked = 0
        for dip in data.get("device_ips", []):
            if dip["object_id"] in device_ids or dip["object_id"] in vm_ids:
                self.get_best_cidr(dip["ip_str"])
                ips_checked += 1

        infra = data.get("infrastructure", {})
        logger.info(
            f"[DRY-RUN] Validation summary: "
            f"sites={len(infra.get('sites_map', {}))}, "
            f"locations={len(infra.get('rooms_map', {}))}, "
            f"racks={len(infra.get('racks', []))}, "
            f"vlan_groups={len(data.get('vlan_groups', []))}, "
            f"vlans={len(data.get('vlans', []))}, "
            f"prefixes_v4={len(self.ipv4_prefixes)}, "
            f"prefixes_v6={len(self.ipv6_prefixes)}, "
            f"hardware_models={len(data.get('hardware', []))}, "
            f"devices={len(data.get('devices', []))}, "
            f"pdus={len(data.get('pdus', []))}, "
            f"patch_panels={len(data.get('patch_panels', []))}, "
            f"clusters={len(data.get('vm_clusters', []))}, "
            f"vms={len(vm_ids)}, "
            f"ports_valid={ports_checked} (skipped={ports_skipped}), "
            f"cables_valid={cables_checked} (skipped={cables_skipped}), "
            f"device_ips={ips_checked}, "
            f"tags={len(data.get('tag_tree', []))}."
        )

    def import_all(self, data):
        if self.dry_run:
            self.run_dry_run(data)
            return

        global slugify
        if slugify is None:
            slugify = _require_module("slugify", "python-slugify")

        logger.info("Starting NetBox import from JSON data...")
        self.import_vlan_groups(data.get("vlan_groups", []))
        self.import_vlans(data.get("vlans", []))
        self.import_prefixes(data.get("prefixes", {}))
        self.import_infrastructure(data.get("infrastructure", {}))
        self.import_hardware(data.get("hardware", []))
        self.import_pdus(data.get("pdus", []))
        self.import_patch_panels(data.get("patch_panels", []))
        self.import_vm_clusters(data.get("vm_clusters", []))
        self.import_devices(data.get("devices", []))
        self.import_virtual_machines(data.get("virtual_machines", []))
        self.import_ports(data.get("ports", []))
        self.import_cables(data.get("cables", []))
        self.import_device_ips(data.get("device_ips", []))
        self.import_tags(data.get("tag_tree", []), data.get("tag_storage", []))

        # TSVマッピングファイルの書き出し
        try:
            mapping_file = 'id_mappings.tsv'
            logger.info(f"Writing ID mappings to {mapping_file}...")
            with open(mapping_file, 'w', encoding='utf-8') as f:
                f.write("rt_id\tname\tkind\tnb_id\n")
                # Devices
                for rt_id, dev_obj in self.device_netbox_map.items():
                    f.write(f"{rt_id}\t{dev_obj.name}\tdevice\t{dev_obj.id}\n")
                # VMs
                for rt_id, vm_obj in self.vm_netbox_map.items():
                    f.write(f"{rt_id}\t{vm_obj.name}\tvm\t{vm_obj.id}\n")
            logger.info("ID mappings TSV written successfully.")
        except Exception as e:
            logger.error(f"Failed to write ID mappings TSV: {e}")

        logger.info("NetBox import completed successfully.")



    def import_vlan_groups(self, groups):
        logger.info(f"Importing {len(groups)} VLAN Groups into NetBox...")
        for g in groups:
            domain_id = g["domain_id"]
            group_name = g["name"]
            group_slug = g["slug"]
            try:
                vg = self.netbox.ipam.vlan_groups.get(slug=group_slug)
                if not vg:
                    vg = self.netbox.ipam.vlan_groups.get(name=group_name)
                if not vg:
                    logger.info(f"Creating VLANGroup in NetBox: '{group_name}' (slug: '{group_slug}')")
                    vg = self.netbox.ipam.vlan_groups.create(
                        name=group_name,
                        slug=group_slug,
                        description=g["description"]
                    )
                self.vlan_groups_map[domain_id] = vg
            except Exception as e:
                logger.error(f"Error syncing VLANGroup '{group_name}' (domain_id: {domain_id}): {e}")

    def import_vlans(self, vlans):
        logger.info(f"Importing {len(vlans)} VLANs into NetBox...")
        for v in vlans:
            domain_id = v["domain_id"]
            vlan_id = v["vlan_id"]
            clean_name = v["clean_name"]
            base_name = v["base_name"]
            vlan_slug = v["slug"]
            vlan_desc = v["description"]

            group_obj = self.vlan_groups_map.get(domain_id)
            group_id = group_obj.id if group_obj else None

            try:
                vlan_obj = None
                if group_id:
                    vlan_obj = self.netbox.ipam.vlans.get(group_id=group_id, vid=vlan_id)
                else:
                    vlan_obj = self.netbox.ipam.vlans.get(vid=vlan_id)

                if not vlan_obj:
                    vlan_data = {
                        'vid': int(vlan_id),
                        'name': clean_name,
                        'slug': vlan_slug,
                        'status': 'active',
                        'description': vlan_desc[:500]
                    }
                    if group_id:
                        vlan_data['group'] = group_id

                    logger.info(f"Creating VLAN in NetBox: VID {vlan_id} '{clean_name}'")
                    try:
                        vlan_obj = self.netbox.ipam.vlans.create(**vlan_data)
                    except Exception as e_create:
                        if "must make a unique set" in str(e_create) or "already exists" in str(e_create):
                            fallback_name = f"{base_name} (VID {vlan_id})" if f"VID {vlan_id}" not in base_name else base_name
                            fallback_slug = slugify.slugify(fallback_name) or f"vlan-{domain_id}-{vlan_id}"
                            vlan_data['name'] = fallback_name
                            vlan_data['slug'] = fallback_slug
                            logger.warning(f"VLAN name collision, retrying with fallback '{fallback_name}'...")
                            vlan_obj = self.netbox.ipam.vlans.create(**vlan_data)
                        else:
                            raise e_create
                self.vlans_map[(domain_id, vlan_id)] = vlan_obj
            except Exception as e:
                logger.error(f"Error syncing VLAN VID {vlan_id}: {e}")

    def import_prefixes(self, prefixes_data):
        default_site = self.get_default_site()
        site_id = default_site.id if default_site else None

        v4_list = prefixes_data.get("v4", [])
        logger.info(f"Importing {len(v4_list)} IPv4 Prefixes into NetBox...")
        self.ipv4_prefixes = []

        for p in v4_list:
            cidr = p["cidr"]
            full_desc = p["description"]
            vlan_ref = p["vlan_ref"]

            try:
                self.ipv4_prefixes.append(ipaddress.ip_network(cidr, strict=False))
            except Exception:
                pass

            vlan_obj = None
            if vlan_ref:
                d_id, v_id = vlan_ref
                vlan_obj = self.vlans_map.get((d_id, v_id))

            prefix_data = {'prefix': cidr, 'status': 'active'}
            if len(full_desc) > 200:
                prefix_data['description'] = full_desc[:190] + "…"
                prefix_data['comments'] = full_desc
            else:
                prefix_data['description'] = full_desc

            if vlan_obj:
                prefix_data['vlan'] = vlan_obj.id
            if site_id:
                prefix_data['site'] = site_id

            try:
                p_obj = self.netbox.ipam.prefixes.get(prefix=cidr)
                if not p_obj:
                    logger.info(f"Creating IPv4 Prefix in NetBox: {cidr}")
                    p_obj = self.netbox.ipam.prefixes.create(**prefix_data)
                elif vlan_obj and not p_obj.vlan:
                    p_obj.update({'vlan': vlan_obj.id})
                if p_obj:
                    self.prefix_netbox_map[p["id"]] = p_obj
            except Exception as e:
                logger.error(f"Error syncing IPv4 Prefix {cidr}: {e}")

        v6_list = prefixes_data.get("v6", [])
        logger.info(f"Importing {len(v6_list)} IPv6 Prefixes into NetBox...")
        self.ipv6_prefixes = []

        for p in v6_list:
            cidr = p["cidr"]
            full_desc = p["description"]
            vlan_ref = p["vlan_ref"]

            try:
                self.ipv6_prefixes.append(ipaddress.ip_network(cidr, strict=False))
            except Exception:
                pass

            vlan_obj = None
            if vlan_ref:
                d_id, v_id = vlan_ref
                vlan_obj = self.vlans_map.get((d_id, v_id))

            prefix_data = {'prefix': cidr, 'status': 'active'}
            if len(full_desc) > 200:
                prefix_data['description'] = full_desc[:190] + "…"
                prefix_data['comments'] = full_desc
            else:
                prefix_data['description'] = full_desc

            if vlan_obj:
                prefix_data['vlan'] = vlan_obj.id
            if site_id:
                prefix_data['site'] = site_id

            try:
                p_obj = self.netbox.ipam.prefixes.get(prefix=cidr)
                if not p_obj:
                    logger.info(f"Creating IPv6 Prefix in NetBox: {cidr}")
                    p_obj = self.netbox.ipam.prefixes.create(**prefix_data)
                elif vlan_obj and not p_obj.vlan:
                    p_obj.update({'vlan': vlan_obj.id})
                if p_obj:
                    self.prefix_netbox_map[p["id"]] = p_obj
            except Exception as e:
                logger.error(f"Error syncing IPv6 Prefix {cidr}: {e}")


    def import_infrastructure(self, infra):
        sites_map = infra.get("sites_map", {})
        rooms_map = infra.get("rooms_map", {})
        racks = infra.get("racks", [])

        logger.info("Importing Sites, Locations, Racks into NetBox...")
        site_objects = {}
        siteslugs_map = {}

        if self.config.has_section('SiteSlugs'):
            for s_name, s_slug in self.config['SiteSlugs'].items():
                s_name_str = s_name.strip()
                s_slug_str = s_slug.strip()
                siteslugs_map[s_name_str.lower()] = s_slug_str
                try:
                    site = self.netbox.dcim.sites.get(slug=s_slug_str)
                    if not site:
                        site = self.netbox.dcim.sites.get(name=s_name_str)
                    if not site:
                        site = self.netbox.dcim.sites.create(name=s_name_str, slug=s_slug_str, status='active')
                        logger.info(f"Created Site from SiteSlugs: {s_name_str}")
                    site_objects[s_name_str] = site
                    site_objects[s_name_str.lower()] = site
                    site_objects[s_slug_str] = site
                except Exception as e:
                    logger.error(f"Failed to sync SiteSlugs site '{s_name_str}': {e}")

        for site_id, site_name in sites_map.items():
            if site_name in site_objects or site_name.lower() in site_objects:
                if site_name not in site_objects:
                    site_objects[site_name] = site_objects[site_name.lower()]
                continue

            slug = siteslugs_map.get(site_name.lower()) or slugify.slugify(site_name) or f"site-{site_id}"
            try:
                site = self.netbox.dcim.sites.get(slug=slug)
                if not site:
                    site = self.netbox.dcim.sites.get(name=site_name)
                if not site:
                    site = self.netbox.dcim.sites.create(name=site_name, slug=slug, status='active')
                    logger.info(f"Created Site in NetBox: {site_name}")
                site_objects[site_name] = site
                site_objects[site_name.lower()] = site
                site_objects[slug] = site
            except Exception as e:
                logger.error(f"Failed to sync Site '{site_name}': {e}")

        location_objects = {}
        for room, parent in rooms_map.items():
            site_name = parent if parent in sites_map.values() else rooms_map.get(parent, parent)
            site_obj = site_objects.get(site_name)
            site_id = site_obj.id if site_obj else None

            if site_id:
                slug = slugify.slugify(room) or f"loc-{site_id}-{hashlib.md5(room.encode('utf-8')).hexdigest()[:6]}"
                try:
                    loc = self.netbox.dcim.locations.get(name=room, site_id=site_id)
                    if not loc:
                        loc = self.netbox.dcim.locations.create(name=room, slug=slug, site=site_id, status='active')
                        logger.info(f"Created Location in NetBox: {room}")
                    location_objects[room] = loc
                except Exception as e:
                    logger.error(f"Failed to sync Location '{room}': {e}")

        for r in racks:
            rack_id = r["id"]
            rack_name = r["name"]
            height = r["height"]
            location_name = r["location_name"]

            site_name = location_name if location_name in sites_map.values() else rooms_map.get(location_name, location_name)
            site_obj = site_objects.get(site_name)
            site_id = site_obj.id if site_obj else None
            loc_obj = location_objects.get(location_name)
            loc_id = loc_obj.id if loc_obj else None

            if site_id:
                rack_slug = slugify.slugify(rack_name) or f"rack-{rack_id}"
                try:
                    rack_obj = self.netbox.dcim.racks.get(name=rack_name, site_id=site_id)
                    if not rack_obj:
                        rack_data = {
                            'name': rack_name,
                            'slug': rack_slug,
                            'site': site_id,
                            'u_height': height,
                            'status': 'active'
                        }
                        if loc_id:
                            rack_data['location'] = loc_id
                        rack_obj = self.netbox.dcim.racks.create(**rack_data)
                        logger.info(f"Created Rack in NetBox: {rack_name}")
                    if rack_obj:
                        self.rack_id_map[rack_id] = rack_obj.id
                except Exception as e:
                    logger.error(f"Failed to sync Rack '{rack_name}': {e}")

    def import_hardware(self, hardware_list):
        logger.info(f"Importing {len(hardware_list)} Hardware DeviceTypes into NetBox...")
        manufacturer_cache = {}

        for hw in hardware_list:
            vendor = hw["vendor"]
            model = hw["model"]
            u_height = hw["u_height"]
            self.hardware_u_height_map[model] = max(1, u_height)

            if vendor not in manufacturer_cache:
                m_slug = slugify.slugify(vendor) or f"mfr-{hashlib.md5(vendor.encode('utf-8')).hexdigest()[:6]}"
                try:
                    mfr = self.netbox.dcim.manufacturers.get(slug=m_slug[:50])
                    if not mfr:
                        mfr = self.netbox.dcim.manufacturers.get(name=vendor)
                    if not mfr:
                        mfr = self.netbox.dcim.manufacturers.create(name=vendor, slug=m_slug[:50])
                    manufacturer_cache[vendor] = mfr
                except Exception as e:
                    logger.error(f"Failed to sync Manufacturer '{vendor}': {e}")

            mfr_obj = manufacturer_cache.get(vendor)
            if mfr_obj and model not in self.device_type_cache:
                dt_slug = slugify.slugify(f"{vendor}-{model}") or f"dt-{hashlib.md5(model.encode('utf-8')).hexdigest()[:6]}"
                try:
                    dt = self.netbox.dcim.device_types.get(slug=dt_slug[:50])
                    if not dt:
                        dt = self.netbox.dcim.device_types.get(model=model, manufacturer_id=mfr_obj.id)
                    if not dt:
                        dt = self.netbox.dcim.device_types.create(
                            manufacturer=mfr_obj.id,
                            model=model[:100],
                            slug=dt_slug[:50],
                            u_height=max(1, u_height),
                            is_full_depth=False
                        )
                        logger.info(f"Created DeviceType in NetBox: {model} (u_height: {u_height}) ({vendor})")
                    self.device_type_cache[model] = dt
                except Exception as e:
                    logger.error(f"Failed to sync DeviceType '{model}': {e}")

    def import_pdus(self, pdus):
        logger.info(f"Importing {len(pdus)} PDUs into NetBox...")
        try:
            pdu_role = self.netbox.dcim.device_roles.get(slug='pdu')
            if not pdu_role:
                pdu_role = self.netbox.dcim.device_roles.create(name='PDU', slug='pdu', color='9e9e9e')
        except Exception as e:
            logger.error(f"Failed to get/create PDU Device Role: {e}")
            return

        default_site = self.get_default_site()
        manufacturer_cache = {}
        device_type_cache = {}

        for p in pdus:
            pdu_id = p["pdu_id"]
            name = p["name"]
            vendor = p["vendor"]
            model = p["model"]
            rack_id = p["rack_id"]
            unit_no = p["unit_no"]

            if vendor not in manufacturer_cache:
                m_slug = slugify.slugify(vendor) or f"pdu-mfr-{hashlib.md5(vendor.encode('utf-8')).hexdigest()[:6]}"
                try:
                    mfr = self.netbox.dcim.manufacturers.get(slug=m_slug[:50])
                    if not mfr:
                        mfr = self.netbox.dcim.manufacturers.get(name=vendor)
                    if not mfr:
                        mfr = self.netbox.dcim.manufacturers.create(name=vendor, slug=m_slug[:50])
                    manufacturer_cache[vendor] = mfr
                except Exception as e:
                    logger.error(f"Failed to sync Manufacturer '{vendor}': {e}")

            mfr_obj = manufacturer_cache.get(vendor)
            if mfr_obj and model not in device_type_cache:
                dt_slug = slugify.slugify(f"{vendor}-{model}") or f"pdu-dt-{hashlib.md5(model.encode('utf-8')).hexdigest()[:6]}"
                try:
                    dt = self.netbox.dcim.device_types.get(slug=dt_slug[:50])
                    if not dt:
                        dt = self.netbox.dcim.device_types.get(model=model, manufacturer_id=mfr_obj.id)
                    if not dt:
                        dt = self.netbox.dcim.device_types.create(
                            manufacturer=mfr_obj.id,
                            model=model[:100],
                            slug=dt_slug[:50],
                            u_height=1,
                            is_full_depth=False
                        )
                        logger.info(f"Created PDU DeviceType in NetBox: {model}")
                    device_type_cache[model] = dt
                except Exception as e:
                    logger.error(f"Failed to sync PDU DeviceType '{model}': {e}")

            dt_obj = device_type_cache.get(model)
            if dt_obj and name:
                d_slug = slugify.slugify(name) or f"pdu-{pdu_id}"
                site_id = default_site.id if default_site else None
                rack_netbox_id = self.rack_id_map.get(rack_id) if rack_id else None

                if rack_netbox_id:
                    try:
                        r_obj = self.netbox.dcim.racks.get(id=rack_netbox_id)
                        if r_obj:
                            site_id = r_obj.site.id
                    except Exception:
                        pass

                if site_id:
                    try:
                        dev = self.netbox.dcim.devices.get(name=name, site_id=site_id)
                        if not dev:
                            dev_data = {
                                'name': name,
                                'slug': d_slug[:50],
                                'device_type': dt_obj.id,
                                'role': pdu_role.id,
                                'site': site_id,
                                'status': 'active'
                            }
                            if rack_netbox_id:
                                dev_data['rack'] = rack_netbox_id
                                if unit_no and str(unit_no).isdigit():
                                    dev_data['position'] = int(unit_no)
                                    dev_data['face'] = p.get('face', 'front')


                            try:
                                dev = self.netbox.dcim.devices.create(**dev_data)
                                logger.info(f"Created PDU Device in NetBox: {name}")
                            except Exception as e_dev_create:
                                err_str = str(e_dev_create)
                                if rack_netbox_id and ("is already occupied" in err_str or "does not have sufficient space" in err_str or "must make a unique set" in err_str):
                                    logger.error(f"PDU rack position occupied for '{name}' at U{unit_no}: {e_dev_create}")
                                    dev_data.pop('position', None)
                                    dev_data.pop('face', None)
                                    logger.warning(f"Retrying PDU '{name}' as unpositioned device in rack...")
                                    dev = self.netbox.dcim.devices.create(**dev_data)
                                else:
                                    raise e_dev_create

                        if dev:
                            self.device_netbox_map[pdu_id] = dev
                    except Exception as e:
                        logger.error(f"Failed to sync PDU Device '{name}': {e}")

    def import_patch_panels(self, panels):
        logger.info(f"Importing {len(panels)} Patch Panels into NetBox...")
        try:
            pp_role = self.netbox.dcim.device_roles.get(slug='patch-panel')
            if not pp_role:
                pp_role = self.netbox.dcim.device_roles.create(name='Patch Panel', slug='patch-panel', color='607d8b')
        except Exception as e:
            logger.error(f"Failed to get/create Patch Panel Device Role: {e}")
            return

        try:
            mfr = self.netbox.dcim.manufacturers.get(slug='generic')
            if not mfr:
                mfr = self.netbox.dcim.manufacturers.get(name='Generic')
            if not mfr:
                mfr = self.netbox.dcim.manufacturers.create(name='Generic', slug='generic')
        except Exception as e:
            logger.error(f"Failed to get/create Generic Manufacturer: {e}")
            return

        default_site = self.get_default_site()
        device_type_cache = {}

        for p in panels:
            id = p["id"]
            name = p["name"]
            port_count = p["port_count"]
            rack_id = p["rack_id"]
            unit_no = p["unit_no"]

            ports = int(port_count) if port_count and str(port_count).isdigit() else 24
            model_name = f"Patch Panel {ports}-Port"
            dt_slug = slugify.slugify(model_name) or f"pp-dt-{ports}"

            if model_name not in device_type_cache:
                try:
                    dt = self.netbox.dcim.device_types.get(model=model_name, manufacturer_id=mfr.id)
                    if not dt:
                        dt = self.netbox.dcim.device_types.create(
                            manufacturer=mfr.id,
                            model=model_name,
                            slug=dt_slug[:50],
                            u_height=1,
                            is_full_depth=False
                        )
                        logger.info(f"Created Patch Panel DeviceType: {model_name}")
                    device_type_cache[model_name] = dt
                except Exception as e:
                    logger.error(f"Failed to sync Patch Panel DeviceType '{model_name}': {e}")

            dt_obj = device_type_cache.get(model_name)
            if dt_obj:
                site_id = default_site.id if default_site else None
                rack_netbox_id = self.rack_id_map.get(rack_id) if rack_id else None

                if rack_netbox_id:
                    try:
                        r_obj = self.netbox.dcim.racks.get(id=rack_netbox_id)
                        if r_obj:
                            site_id = r_obj.site.id
                    except Exception:
                        pass

                if site_id:
                    d_slug = slugify.slugify(name) or f"patch-panel-{id}"
                    try:
                        dev = self.netbox.dcim.devices.get(name=name, site_id=site_id)
                        if not dev:
                            dev_data = {
                                'name': name,
                                'slug': d_slug[:50],
                                'device_type': dt_obj.id,
                                'role': pp_role.id,
                                'site': site_id,
                                'status': 'active'
                            }
                            if rack_netbox_id:
                                dev_data['rack'] = rack_netbox_id
                                if unit_no and str(unit_no).isdigit():
                                    dev_data['position'] = int(unit_no)
                                    dev_data['face'] = p.get('face', 'front')


                            try:
                                dev = self.netbox.dcim.devices.create(**dev_data)
                                logger.info(f"Created Patch Panel Device in NetBox: {name}")
                            except Exception as e_dev_create:
                                err_str = str(e_dev_create)
                                if rack_netbox_id and ("is already occupied" in err_str or "does not have sufficient space" in err_str or "must make a unique set" in err_str):
                                    logger.error(f"Patch Panel rack position occupied for '{name}' at U{unit_no}: {e_dev_create}")
                                    dev_data.pop('position', None)
                                    dev_data.pop('face', None)
                                    logger.warning(f"Retrying Patch Panel '{name}' as unpositioned device in rack...")
                                    dev = self.netbox.dcim.devices.create(**dev_data)
                                else:
                                    raise e_dev_create

                        if dev:
                            self.device_netbox_map[id] = dev
                    except Exception as e:
                        logger.error(f"Failed to sync Patch Panel Device '{name}': {e}")

    def import_devices(self, devices):
        logger.info(f"Importing {len(devices)} Devices into NetBox...")
        try:
            default_role = self.netbox.dcim.device_roles.get(slug='server')
            if not default_role:
                default_role = self.netbox.dcim.device_roles.create(name='Server', slug='server', color='009688')
        except Exception as e:
            logger.error(f"Failed to get/create Server Device Role: {e}")
            return

        # Device Role マッピングの組み込み辞書＋conf設定
        BUILTIN_ROLE_MAP = {
            "serial console server": "Console Server",
            "network switch": "Switch",
            "router": "Router",
            "network security": "Firewall",
            "server": "Server",
            "pdu": "PDU",
            "patch panel": "Patch Panel",
        }
        role_map = dict(BUILTIN_ROLE_MAP)
        if self.config.has_section('DeviceRoleMapping'):
            for objtype_key, role_name in self.config['DeviceRoleMapping'].items():
                role_map[objtype_key.strip().lower()] = role_name.strip()

        device_role_cache = {"server": default_role}

        def get_device_role(objtype_name):
            key = (objtype_name or "").strip().lower()
            role_name = role_map.get(key, "Server")
            role_key = role_name.lower()
            if role_key in device_role_cache:
                return device_role_cache[role_key]

            role_slug = slugify.slugify(role_name) or f"role-{hashlib.md5(role_name.encode('utf-8')).hexdigest()[:6]}"
            try:
                role_obj = self.netbox.dcim.device_roles.get(slug=role_slug[:50])
                if not role_obj:
                    role_obj = self.netbox.dcim.device_roles.get(name=role_name)
                if not role_obj:
                    colors = {
                        "console server": "607d8b",
                        "switch": "2196f3",
                        "router": "9c27b0",
                        "firewall": "f44336",
                        "server": "009688",
                        "pdu": "ff9800",
                    }
                    c = colors.get(role_name.lower(), "009688")
                    role_obj = self.netbox.dcim.device_roles.create(name=role_name, slug=role_slug[:50], color=c)
                    logger.info(f"Created Device Role in NetBox: {role_name} (slug: {role_slug[:50]})")
                device_role_cache[role_key] = role_obj
                return role_obj
            except Exception as e:
                logger.error(f"Failed to get/create Device Role '{role_name}': {e}")
                return default_role

        default_site = self.get_default_site()
        manufacturer_cache = {}
        device_type_cache = {}

        for d in devices:
            dev_id = d["dev_id"]
            dev_name = d["dev_name"]
            asset = d["asset"]
            vendor = d["vendor"]
            model = d["model"]
            rack_id = d["rack_id"]
            rack_position = d["rack_position"]
            dev_cluster_id = d.get("cluster_id")
            objtype_name = d.get("objtype_name", "")
            role_obj = get_device_role(objtype_name)

            if vendor not in manufacturer_cache:
                m_slug = slugify.slugify(vendor) or f"mfr-{hashlib.md5(vendor.encode('utf-8')).hexdigest()[:6]}"
                try:
                    mfr = self.netbox.dcim.manufacturers.get(slug=m_slug[:50])
                    if not mfr:
                        mfr = self.netbox.dcim.manufacturers.get(name=vendor)
                    if not mfr:
                        mfr = self.netbox.dcim.manufacturers.create(name=vendor, slug=m_slug[:50])
                    manufacturer_cache[vendor] = mfr
                except Exception as e:
                    logger.error(f"Failed to sync Manufacturer '{vendor}': {e}")

            mfr_obj = manufacturer_cache.get(vendor)
            if mfr_obj and model not in device_type_cache:
                dt_slug = slugify.slugify(f"{vendor}-{model}") or f"dev-dt-{hashlib.md5(model.encode('utf-8')).hexdigest()[:6]}"
                try:
                    dt = self.netbox.dcim.device_types.get(slug=dt_slug[:50])
                    if not dt:
                        dt = self.netbox.dcim.device_types.get(model=model, manufacturer_id=mfr_obj.id)
                    if not dt:
                        dt = self.netbox.dcim.device_types.create(
                            manufacturer=mfr_obj.id,
                            model=model[:100],
                            slug=dt_slug[:50],
                            u_height=1,
                            is_full_depth=False
                        )
                        logger.info(f"Created DeviceType in NetBox: {model} ({vendor})")
                    device_type_cache[model] = dt
                except Exception as e:
                    logger.error(f"Failed to sync DeviceType '{model}': {e}")

            dt_obj = device_type_cache.get(model)
            if dt_obj and default_role:
                d_slug = slugify.slugify(dev_name) or f"dev-{dev_id}"
                site_id = default_site.id if default_site else None
                rack_netbox_id = self.rack_id_map.get(rack_id) if rack_id else None

                if rack_netbox_id:
                    try:
                        r_obj = self.netbox.dcim.racks.get(id=rack_netbox_id)
                        if r_obj:
                            site_id = r_obj.site.id
                    except Exception:
                        pass

                # Cluster to assign to this Device (hypervisor ↔ cluster membership)
                cluster_netbox_obj = self.cluster_netbox_map.get(dev_cluster_id) if dev_cluster_id else None

                if site_id:
                    try:
                        dev = self.netbox.dcim.devices.get(name=dev_name, site_id=site_id)
                        if not dev:
                            dev_data = {
                                'name': dev_name,
                                'slug': d_slug[:50],
                                'device_type': dt_obj.id,
                                'role': role_obj.id,

                                'site': site_id,
                                'status': 'active'
                            }
                            if rack_netbox_id:
                                dev_data['rack'] = rack_netbox_id
                                if rack_position and str(rack_position).isdigit():
                                    dev_data['position'] = int(rack_position)
                                    dev_data['face'] = d.get('face', 'front')

                            if asset:
                                dev_data['asset_tag'] = asset
                            if cluster_netbox_obj:
                                dev_data['cluster'] = cluster_netbox_obj.id

                            try:
                                dev = self.netbox.dcim.devices.create(**dev_data)
                                logger.info(f"Created Device in NetBox: {dev_name}" + (f" (Cluster: {cluster_netbox_obj.name})" if cluster_netbox_obj else ""))
                            except Exception as e_dev_create:
                                err_str = str(e_dev_create)
                                if rack_netbox_id and ("is already occupied" in err_str or "does not have sufficient space" in err_str or "must make a unique set" in err_str):
                                    logger.error(f"Device rack position occupied for '{dev_name}' at U{rack_position}: {e_dev_create}")
                                    dev_data.pop('position', None)
                                    dev_data.pop('face', None)
                                    logger.warning(f"Retrying Device '{dev_name}' as unpositioned device in rack...")
                                    dev = self.netbox.dcim.devices.create(**dev_data)
                                else:
                                    raise e_dev_create
                        else:
                            # Existing device: update cluster if not yet set
                            if cluster_netbox_obj and not getattr(dev, 'cluster', None):
                                try:
                                    dev.update({'cluster': cluster_netbox_obj.id})
                                    logger.info(f"Updated Device '{dev_name}' cluster -> {cluster_netbox_obj.name}")
                                except Exception as e_cl:
                                    logger.warning(f"Failed to update cluster for Device '{dev_name}': {e_cl}")

                        if dev:
                            self.device_netbox_map[dev_id] = dev
                    except Exception as e:
                        logger.error(f"Failed to sync Device '{dev_name}': {e}")

    def import_vm_clusters(self, clusters):
        logger.info(f"Importing {len(clusters)} VM Clusters into NetBox...")
        cluster_type_map = {}
        if self.config.has_section('ClusterTypes'):
            for c_name, c_type in self.config['ClusterTypes'].items():
                cluster_type_map[c_name.strip().lower()] = c_type.strip()

        cluster_prefix_types = []
        if self.config.has_section('ClusterTypePrefixes'):
            for prefix, c_type in self.config['ClusterTypePrefixes'].items():
                cluster_prefix_types.append((prefix.strip().lower(), c_type.strip()))

        default_type_name = "VMware vSphere"
        cluster_types_cache = {}

        def get_or_create_cluster_type(type_name):
            if type_name in cluster_types_cache:
                return cluster_types_cache[type_name]
            ct_slug = slugify.slugify(type_name) or f"ct-{hashlib.md5(type_name.encode('utf-8')).hexdigest()[:6]}"
            try:
                ct = self.netbox.virtualization.cluster_types.get(slug=ct_slug[:50])
                if not ct:
                    ct = self.netbox.virtualization.cluster_types.get(name=type_name)
                if not ct:
                    ct = self.netbox.virtualization.cluster_types.create(name=type_name, slug=ct_slug[:50])
                    logger.info(f"Created ClusterType in NetBox: {type_name}")
                cluster_types_cache[type_name] = ct
                return ct
            except Exception as e:
                logger.error(f"Failed to get/create ClusterType '{type_name}': {e}")
                return None

        get_or_create_cluster_type(default_type_name)
        cluster_netbox_map = {}

        for c in clusters:
            cluster_id = c["cluster_id"]
            name = c["name"]
            comment = c["comment"]

            matched_type_name = None
            if name.lower() in cluster_type_map:
                matched_type_name = cluster_type_map[name.lower()]
            else:
                for prefix, p_type in cluster_prefix_types:
                    if name.lower().startswith(prefix):
                        matched_type_name = p_type
                        break

            target_type_name = matched_type_name or default_type_name
            cluster_type_obj = get_or_create_cluster_type(target_type_name)

            if cluster_type_obj and name:
                c_slug = slugify.slugify(name) or f"cluster-{cluster_id}"
                try:
                    cluster_obj = self.netbox.virtualization.clusters.get(name=name)
                    if not cluster_obj:
                        cluster_data = {
                            'name': name,
                            'slug': c_slug[:50],
                            'type': cluster_type_obj.id,
                            'comments': comment or ""
                        }
                        cluster_obj = self.netbox.virtualization.clusters.create(**cluster_data)
                        logger.info(f"Created Cluster in NetBox: {name} (Type: {target_type_name})")
                    cluster_netbox_map[cluster_id] = cluster_obj
                except Exception as e:
                    logger.error(f"Failed to sync Cluster '{name}': {e}")

        self.cluster_netbox_map = cluster_netbox_map

    def import_virtual_machines(self, vms):
        logger.info(f"Importing {len(vms)} Virtual Machines & Zones into NetBox...")
        default_site = self.get_default_site()

        for v in vms:
            vm_id = v["vm_id"]
            objtype_id = v["objtype_id"]
            vm_name = v["vm_name"]
            comment = v["comment"]
            cluster_id = v["cluster_id"]
            parent_device_id = v["parent_device_id"]

            cluster_obj = self.cluster_netbox_map.get(cluster_id) if cluster_id else None
            parent_dev_obj = self.device_netbox_map.get(parent_device_id) if parent_device_id else None

            if not cluster_obj and parent_dev_obj and getattr(parent_dev_obj, 'cluster', None):
                cluster_obj = parent_dev_obj.cluster

            site_id = None
            if parent_dev_obj and parent_dev_obj.site:
                site_id = parent_dev_obj.site.id
            elif cluster_obj and getattr(cluster_obj, 'site', None):
                site_id = cluster_obj.site.id
            elif default_site:
                site_id = default_site.id

            v_slug = slugify.slugify(vm_name) or f"vm-{vm_id}"
            vm_data = {
                'name': vm_name,
                'slug': v_slug[:50],
                'status': 'active',
                'comments': comment or ""
            }

            if cluster_obj:
                vm_data['cluster'] = cluster_obj.id
            elif site_id:
                vm_data['site'] = site_id

            if parent_dev_obj:
                vm_data['device'] = parent_dev_obj.id

            try:
                vm_obj = None
                # Check by name globally first to prevent duplicate VM objects with identical names across different clusters
                vm_obj = self.netbox.virtualization.virtual_machines.get(name=vm_name)

                if not vm_obj:
                    if cluster_obj:
                        vm_obj = self.netbox.virtualization.virtual_machines.get(name=vm_name, cluster_id=cluster_obj.id)
                    elif site_id:
                        vm_obj = self.netbox.virtualization.virtual_machines.get(name=vm_name, site_id=site_id)

                if not vm_obj:
                    vm_obj = self.netbox.virtualization.virtual_machines.create(**vm_data)
                    logger.info(f"Created VirtualMachine in NetBox: '{vm_name}'")

                self.vm_netbox_map[vm_id] = vm_obj
            except Exception as e:
                logger.error(f"Failed to sync VirtualMachine '{vm_name}' (ID: {vm_id}): {e}")

    def import_device_ips(self, device_ips):
        logger.info(f"Importing {len(device_ips)} Device IP Assignments into NetBox...")

        # 高速化および重複誤判定防止用の全既存 IP 一括プリフェッチキャッシュ
        logger.info("Pre-fetching existing IP addresses from NetBox for fast lookup...")
        existing_ip_cache = defaultdict(list)
        try:
            for ip in self.netbox.ipam.ip_addresses.all():
                addr_str = str(ip.address)
                base = addr_str.split('/')[0].strip()
                existing_ip_cache[addr_str].append(ip)
                if base not in existing_ip_cache[addr_str]:
                    existing_ip_cache[base].append(ip)
        except Exception as e:
            logger.warning(f"Error pre-fetching IP addresses: {e}")

        for dip in device_ips:
            object_id = dip["object_id"]
            ip_str = dip["ip_str"]
            name = dip.get("name", "")
            addr_name = dip.get("addr_name", "")
            alloc_type = dip.get("alloc_type", "regular")

            target_obj = self.device_netbox_map.get(object_id)
            target_type = 'device'
            if not target_obj:
                target_obj = self.vm_netbox_map.get(object_id)
                target_type = 'vm'

            if not target_obj:
                continue

            ip_cidr = self.get_best_cidr(ip_str)
            base_ip = ip_str.split('/')[0].strip()

            # Ensure interface object exists (dcim.interface or virtualization.vminterface)
            ifname = name.strip() if name and name.strip() else "eth0"
            ifname_lower = ifname.lower()
            assigned_if = None

            if target_type == 'device':
                # 1. メモリマップから大文字小文字無視で検索
                assigned_if = self.interface_name_map.get(("device", target_obj.id, ifname_lower)) or self.interface_name_map.get(("device", target_obj.id, ifname))
                if not assigned_if:
                    try:
                        all_ifs = list(self.netbox.dcim.interfaces.filter(device_id=target_obj.id))
                        for intf in all_ifs:
                            if intf.name.strip().lower() == ifname_lower:
                                assigned_if = intf
                                break
                        if not assigned_if:
                            # 一致するポートが存在しない場合は仮想ポート (type='virtual') を新規作成してバインド
                            assigned_if = self.netbox.dcim.interfaces.create(
                                device=target_obj.id,
                                name=ifname,
                                type='virtual',
                                description='Virtual interface created for IP binding'
                            )
                            self.interface_name_map[("device", target_obj.id, ifname)] = assigned_if
                            self.interface_name_map[("device", target_obj.id, ifname_lower)] = assigned_if
                            logger.info(f"Created virtual interface '{ifname}' for device '{target_obj.name}' to bind IP {ip_cidr}.")
                    except Exception as if_err:
                        logger.warning(f"Could not get/create virtual interface '{ifname}' for device '{target_obj.name}': {if_err}")
            else:
                # VM の場合
                assigned_if = self.interface_name_map.get(("vm", target_obj.id, ifname_lower)) or self.interface_name_map.get(("vm", target_obj.id, ifname))
                if not assigned_if:
                    try:
                        all_vm_ifs = list(self.netbox.virtualization.interfaces.filter(virtual_machine_id=target_obj.id))
                        for vmintf in all_vm_ifs:
                            if vmintf.name.strip().lower() == ifname_lower:
                                assigned_if = vmintf
                                break
                        if not assigned_if:
                            assigned_if = self.netbox.virtualization.interfaces.create(
                                virtual_machine=target_obj.id,
                                name=ifname,
                                description='Virtual interface created for IP binding'
                            )
                            self.interface_name_map[("vm", target_obj.id, ifname)] = assigned_if
                            self.interface_name_map[("vm", target_obj.id, ifname_lower)] = assigned_if
                            logger.info(f"Created virtual interface '{ifname}' for VM '{target_obj.name}' to bind IP {ip_cidr}.")
                    except Exception as if_err:
                        logger.warning(f"Could not get/create virtual interface '{ifname}' for VM '{target_obj.name}': {if_err}")


            assigned_obj_type = 'dcim.interface' if target_type == 'device' else 'virtualization.vminterface'
            assigned_obj_id = assigned_if.id if assigned_if else None

            try:
                # 重複 IP (FGCP HA, VRRP, Multi-device assignment) 対応ロジック
                ip_obj = None
                existing_ips = existing_ip_cache.get(ip_cidr, []) or existing_ip_cache.get(base_ip, [])

                # 1. 既に対象の assigned_if にバインドされている IP アドレスがあればそれを利用
                for existing in existing_ips:
                    ex_type = getattr(existing, 'assigned_object_type', None)
                    ex_id = getattr(existing, 'assigned_object_id', None)
                    if ex_type == assigned_obj_type and ex_id == assigned_obj_id:
                        ip_obj = existing
                        break

                # 2. インターフェース未バインドの既存 IP があれば対象ポートにバインドして利用
                if not ip_obj and assigned_obj_id:
                    for existing in existing_ips:
                        if not getattr(existing, 'assigned_object_id', None):
                            try:
                                existing.update({
                                    'assigned_object_type': assigned_obj_type,
                                    'assigned_object_id': assigned_obj_id
                                })
                                ip_obj = existing
                                break
                            except Exception:
                                pass

                # 3. 既存の IP がすべて別機器/別ポートにバインドされている（＝真の重複 IP）場合、新しく重複 IP を作成してバインド
                desc = addr_name if addr_name else ""
                if not ip_obj:
                    is_duplicate = False
                    if existing_ips:
                        is_duplicate = any(getattr(ex, 'assigned_object_id', None) is not None for ex in existing_ips)

                    ip_data = {
                        'address': ip_cidr,
                        'status': 'active',
                    }
                    if assigned_obj_id:
                        ip_data['assigned_object_type'] = assigned_obj_type
                        ip_data['assigned_object_id'] = assigned_obj_id

                    if desc:
                        ip_data['description'] = desc[:200]

                    try:
                        ip_obj = self.netbox.ipam.ip_addresses.create(**ip_data)
                        existing_ip_cache[ip_cidr].append(ip_obj)
                        existing_ip_cache[base_ip].append(ip_obj)
                    except Exception as create_err:
                        if existing_ips:
                            ip_obj = existing_ips[0]
                            logger.warning(f"NetBox rejected duplicate IP creation for {ip_cidr}: {create_err}. Falling back to existing IP (ID: {ip_obj.id}).")
                        else:
                            raise create_err

                    if is_duplicate:
                        logger.info(f"Assigned (Duplicate) IP {ip_cidr} to {target_type} '{target_obj.name}' (Interface: {assigned_if.name if assigned_if else 'None'})")
                    else:
                        logger.info(f"Assigned IP {ip_cidr} to {target_type} '{target_obj.name}' (Interface: {assigned_if.name if assigned_if else 'None'})")


                if target_obj and ip_obj:
                    is_v6 = dip.get("is_ipv6", False)
                    primary_attr = 'primary_ip6' if is_v6 else 'primary_ip4'
                    if not getattr(target_obj, primary_attr, None):
                        ip_assigned_type = str(getattr(ip_obj, 'assigned_object_type', ''))
                        ip_assigned_id = getattr(ip_obj, 'assigned_object_id', None)

                        # 未割当かつ assigned_if が存在する場合はインターフェースへ割り当て
                        if assigned_if and (not ip_assigned_id or ip_assigned_type != assigned_obj_type or ip_assigned_id != assigned_if.id):
                            try:
                                ip_obj.update({
                                    'assigned_object_type': assigned_obj_type,
                                    'assigned_object_id': assigned_if.id
                                })
                                ip_assigned_type = assigned_obj_type
                                ip_assigned_id = assigned_if.id
                            except Exception as e_rebind:
                                logger.warning(f"Could not rebind IP {ip_cidr} to interface '{assigned_if.name}': {e_rebind}")

                        # バインドが確実に確認できた場合のみ primary_ip に割り当て
                        if ip_assigned_type == assigned_obj_type and ip_assigned_id and (assigned_if and ip_assigned_id == assigned_if.id):
                            try:
                                target_obj.update({primary_attr: ip_obj.id})
                            except Exception as e_prim:
                                logger.warning(f"Could not set {primary_attr} for {target_type} '{target_obj.name}': {e_prim}")
                        else:
                            logger.warning(f"Skipped setting {primary_attr} for {target_type} '{target_obj.name}' because IP {ip_cidr} is not assigned to its interface.")
            except Exception as e:
                logger.error(f"Error assigning IP {ip_cidr} to {target_type} ID {object_id}: {e}")



    def import_ports(self, ports):
        """RackTables ポートを NetBox dcim.interfaces / console_ports / power_ports / power_outlets として登録する。"""
        logger.info(f"Importing {len(ports)} Ports into NetBox...")

        custom_map, power_port_custom, power_outlet_custom, ignore_port_prefixes, ignore_oif_patterns = self._load_port_config()

        # 高速化のための既存ポートデータ一括プリフェッチ（Pre-fetch Cache）
        logger.info("Pre-fetching existing ports from NetBox for fast in-memory lookup...")
        existing_power_ports = {}          # {(device_id, name): obj}
        existing_power_outlets = {}        # {(device_id, name): obj}
        existing_console_ports = {}        # {(device_id, name): obj}
        existing_console_server_ports = {} # {(device_id, name): obj}
        existing_interfaces = {}           # {(device_id, name): obj}
        existing_vm_interfaces = {}        # {(vm_id, name): obj}

        try:
            for obj in self.netbox.dcim.power_ports.all():
                if getattr(obj, 'device', None):
                    existing_power_ports[(obj.device.id, obj.name)] = obj
        except Exception as e:
            logger.warning(f"Error pre-fetching power_ports: {e}")

        try:
            for obj in self.netbox.dcim.power_outlets.all():
                if getattr(obj, 'device', None):
                    existing_power_outlets[(obj.device.id, obj.name)] = obj
        except Exception as e:
            logger.warning(f"Error pre-fetching power_outlets: {e}")

        try:
            for obj in self.netbox.dcim.console_ports.all():
                if getattr(obj, 'device', None):
                    existing_console_ports[(obj.device.id, obj.name)] = obj
        except Exception as e:
            logger.warning(f"Error pre-fetching console_ports: {e}")

        try:
            for obj in self.netbox.dcim.console_server_ports.all():
                if getattr(obj, 'device', None):
                    existing_console_server_ports[(obj.device.id, obj.name)] = obj
        except Exception as e:
            logger.warning(f"Error pre-fetching console_server_ports: {e}")

        try:
            for obj in self.netbox.dcim.interfaces.all():
                if getattr(obj, 'device', None):
                    existing_interfaces[(obj.device.id, obj.name)] = obj
        except Exception as e:
            logger.warning(f"Error pre-fetching interfaces: {e}")

        try:
            for obj in self.netbox.virtualization.interfaces.all():
                if getattr(obj, 'virtual_machine', None):
                    existing_vm_interfaces[(obj.virtual_machine.id, obj.name)] = obj
        except Exception as e:
            logger.warning(f"Error pre-fetching vm_interfaces: {e}")

        logger.info(
            f"Pre-fetch complete. Cached: {len(existing_interfaces)} interfaces, "
            f"{len(existing_console_ports)} console_ports, {len(existing_console_server_ports)} console_server_ports, "
            f"{len(existing_power_ports)} power_ports, {len(existing_power_outlets)} power_outlets, "
            f"{len(existing_vm_interfaces)} vm_interfaces."
        )

        # 機器ごとの Console Server 判定（Roleが Console Server、またはコンソールポートが 8 個以上所持する機器）
        console_port_count_per_object = defaultdict(int)
        console_server_object_ids = set()

        for p in ports:
            oif_name = p.get("oif_name") or ""
            name = p.get("name") or ""
            object_id = p.get("object_id")
            key = oif_name.strip().lower()
            if not (key in power_port_custom or key in power_outlet_custom or key in ("ac-in", "ac-out", "dc")):
                nb_type = self._resolve_port_type(
                    oif_name, object_id, name, custom_map, power_port_custom, power_outlet_custom, log_unknown=False
                )
                if nb_type.startswith("console-port") or nb_type.startswith("console_port"):
                    console_port_count_per_object[object_id] += 1

            target_dev = self.device_netbox_map.get(object_id)
            if target_dev:
                dev_role = getattr(target_dev, 'role', None) or getattr(target_dev, 'device_role', None)
                role_name = str(getattr(dev_role, 'name', '') or '').lower()
                role_slug = str(getattr(dev_role, 'slug', '') or '').lower()
                if 'console server' in role_name or 'console-server' in role_slug:
                    console_server_object_ids.add(object_id)

        for obj_id, count in console_port_count_per_object.items():
            if count >= 8:
                console_server_object_ids.add(obj_id)

        created = updated = skipped = 0
        mac_created = mac_skipped = 0  # NetBox 4.2+ MACAddress モデル方式のカウンタ
        for port in ports:
            rt_port_id  = port["rt_port_id"]
            object_id   = port["object_id"]
            name        = port["name"] or f"port-{rt_port_id}"
            oif_name    = port["oif_name"]
            mac_addr    = port["mac_address"] or None
            label       = port.get("label", "") or ""
            description = port.get("reservation_comment", "") or ""
            obj_name    = port.get("obj_name", "") or ""
            key         = (oif_name or "").strip().lower()

            # 除外チェック (conf の [ExcludePorts] 設定)
            if self._is_port_excluded(name, oif_name, ignore_port_prefixes, ignore_oif_patterns):
                logger.info(f"Skipping excluded port '{name}' (OIF: '{oif_name}') for object_id={object_id}.")
                skipped += 1
                continue

            target_obj  = self.device_netbox_map.get(object_id)
            target_type = "device"
            if not target_obj:
                target_obj  = self.vm_netbox_map.get(object_id)
                target_type = "vm"

            if not target_obj:
                logger.warning(
                    f"Port '{name}' (rt_port_id={rt_port_id}): "
                    f"parent object_id={object_id} not found in NetBox. Skipping."
                )
                skipped += 1
                continue

            # 電源ポート判定 (PowerPort: AC-in, DC 等 / PowerOutlet: AC-out 等)
            is_power_port   = key in power_port_custom or key in ("ac-in", "dc")
            is_power_outlet = key in power_outlet_custom or key == "ac-out"

            try:
                if is_power_port:
                    port_kind = "power-port"
                    if key in power_port_custom:
                        pp_type = power_port_custom[key]
                    elif key == "ac-in":
                        pp_type = "iec-60320-c14"
                    elif key == "dc":
                        pp_type = "dc-terminal"
                    else:
                        pp_type = "other"

                    if target_type == "device":
                        nb_if = existing_power_ports.get((target_obj.id, name))
                        if nb_if:
                            if getattr(nb_if, 'type', None) != pp_type or getattr(nb_if, 'label', None) != label or getattr(nb_if, 'description', None) != description[:200]:
                                nb_if.update({"type": pp_type, "label": label, "description": description[:200]})
                                updated += 1
                        else:
                            cdata = {
                                "device": target_obj.id, "name": name,
                                "type": pp_type, "label": label,
                                "description": description[:200],
                            }
                            nb_if = self.netbox.dcim.power_ports.create(**cdata)
                            existing_power_ports[(target_obj.id, name)] = nb_if
                            created += 1
                    else:
                        logger.warning(f"PowerPort '{name}' cannot be assigned to VM object_id={object_id}. Skipping.")
                        skipped += 1
                        continue

                elif is_power_outlet:
                    port_kind = "power-outlet"
                    if key in power_outlet_custom:
                        po_type = power_outlet_custom[key]
                    elif key == "ac-out":
                        # 200V 判定（ポート名, label, description, obj_name のいずれかに '200v' を含む場合）
                        check_text = f"{name} {label} {description} {obj_name}".lower()
                        if "200v" in check_text:
                            po_type = "iec-60320-c13"
                        else:
                            po_type = "nema-5-15r"
                    else:
                        po_type = "other"

                    if target_type == "device":
                        nb_if = existing_power_outlets.get((target_obj.id, name))
                        if nb_if:
                            if getattr(nb_if, 'type', None) != po_type or getattr(nb_if, 'label', None) != label or getattr(nb_if, 'description', None) != description[:200]:
                                nb_if.update({"type": po_type, "label": label, "description": description[:200]})
                                updated += 1
                        else:
                            cdata = {
                                "device": target_obj.id, "name": name,
                                "type": po_type, "label": label,
                                "description": description[:200],
                            }
                            nb_if = self.netbox.dcim.power_outlets.create(**cdata)
                            existing_power_outlets[(target_obj.id, name)] = nb_if
                            created += 1
                    else:
                        logger.warning(f"PowerOutlet '{name}' cannot be assigned to VM object_id={object_id}. Skipping.")
                        skipped += 1
                        continue

                else:
                    nb_type = self._resolve_port_type(
                        oif_name, object_id, name, custom_map, power_port_custom, power_outlet_custom, log_unknown=True
                    )
                    port_kind = "interface"
                    if nb_type.startswith("console-port") or nb_type.startswith("console_port"):
                        cp_type = nb_type.split(":", 1)[1] if ":" in nb_type else "rj-45"
                        # Console Server 機器（Roleが Console Server または ポート数が 8 個以上）のポートは「Console Server Port」として登録
                        if object_id in console_server_object_ids:
                            port_kind = "console-server-port"
                            if target_type == "device":
                                # 過去に誤って ConsolePort として登録されていた場合は事前に削除して再生成を可能にする
                                old_cp = existing_console_ports.get((target_obj.id, name))
                                if old_cp:
                                    try:
                                        logger.info(f"Deleting obsolete ConsolePort '{name}' on Console Server '{target_obj.name}' to recreate as ConsoleServerPort.")
                                        old_cp.delete()
                                        del existing_console_ports[(target_obj.id, name)]
                                    except Exception as e:
                                        logger.warning(f"Failed to delete obsolete ConsolePort '{name}': {e}")

                                nb_if = existing_console_server_ports.get((target_obj.id, name))
                                if nb_if:
                                    if getattr(nb_if, 'type', None) != cp_type or getattr(nb_if, 'label', None) != label or getattr(nb_if, 'description', None) != description[:200]:
                                        nb_if.update({"type": cp_type, "label": label, "description": description[:200]})
                                        updated += 1
                                else:
                                    cdata = {
                                        "device": target_obj.id, "name": name,
                                        "type": cp_type, "label": label,
                                        "description": description[:200],
                                    }
                                    nb_if = self.netbox.dcim.console_server_ports.create(**cdata)
                                    existing_console_server_ports[(target_obj.id, name)] = nb_if
                                    created += 1
                            else:
                                logger.warning(f"ConsoleServerPort '{name}' cannot be assigned to VM object_id={object_id}. Skipping.")
                                skipped += 1
                                continue
                        else:
                            port_kind = "console-port"
                            if target_type == "device":
                                nb_if = existing_console_ports.get((target_obj.id, name))
                                if nb_if:
                                    if getattr(nb_if, 'type', None) != cp_type or getattr(nb_if, 'label', None) != label or getattr(nb_if, 'description', None) != description[:200]:
                                        nb_if.update({"type": cp_type, "label": label, "description": description[:200]})
                                        updated += 1
                                else:
                                    cdata = {
                                        "device": target_obj.id, "name": name,
                                        "type": cp_type, "label": label,
                                        "description": description[:200],
                                    }
                                    nb_if = self.netbox.dcim.console_ports.create(**cdata)
                                    existing_console_ports[(target_obj.id, name)] = nb_if
                                    created += 1
                            else:
                                logger.warning(f"ConsolePort '{name}' cannot be assigned to VM object_id={object_id}. Skipping.")
                                skipped += 1
                                continue
                    elif target_type == "device":
                        nb_if = existing_interfaces.get((target_obj.id, name))
                        if nb_if:
                            upd = {}
                            current_type = getattr(nb_if.type, 'value', str(getattr(nb_if, 'type', '')))
                            if current_type != nb_type:
                                upd["type"] = nb_type
                            if getattr(nb_if, 'label', '') != label:
                                upd["label"] = label
                            if getattr(nb_if, 'description', '') != description[:200]:
                                upd["description"] = description[:200]
                            # 4.2未満のみ直接フィールドで更新（4.2+はループ末尾の後処理で対応）
                            if not self.use_mac_address_model:
                                if mac_addr and getattr(nb_if, 'mac_address', '') != mac_addr:
                                    upd["mac_address"] = mac_addr

                            if upd:
                                nb_if.update(upd)
                                updated += 1
                        else:
                            cdata = {
                                "device": target_obj.id, "name": name,
                                "type": nb_type, "label": label,
                                "description": description[:200],
                            }
                            # 4.2未満のみ直接フィールドで設定（4.2+はループ末尾の後処理で対応）
                            if mac_addr and not self.use_mac_address_model:
                                cdata["mac_address"] = mac_addr
                            nb_if = self.netbox.dcim.interfaces.create(**cdata)
                            existing_interfaces[(target_obj.id, name)] = nb_if
                            created += 1
                    else:  # vm
                        nb_if = existing_vm_interfaces.get((target_obj.id, name))
                        if nb_if:
                            upd = {}
                            if getattr(nb_if, 'description', '') != description[:200]:
                                upd["description"] = description[:200]
                            # 4.2未満のみ直接フィールドで更新（4.2+はループ末尾の後処理で対応）
                            if not self.use_mac_address_model:
                                if mac_addr and getattr(nb_if, 'mac_address', '') != mac_addr:
                                    upd["mac_address"] = mac_addr
                            if upd:
                                nb_if.update(upd)
                                updated += 1
                        else:
                            cdata = {
                                "virtual_machine": target_obj.id, "name": name,
                                "description": description[:200],
                            }
                            # 4.2未満のみ直接フィールドで設定（4.2+はループ末尾の後処理で対応）
                            if mac_addr and not self.use_mac_address_model:
                                cdata["mac_address"] = mac_addr
                            nb_if = self.netbox.virtualization.interfaces.create(**cdata)
                            existing_vm_interfaces[(target_obj.id, name)] = nb_if
                            created += 1

                self.port_netbox_map[rt_port_id] = {"obj": nb_if, "target_type": target_type, "port_kind": port_kind}
                if port_kind == "interface":
                    self.interface_name_map[(target_type, target_obj.id, name)] = nb_if
                    self.interface_name_map[(target_type, target_obj.id, name.lower())] = nb_if

                # ── NetBox 4.2+ MACAddress モデル方式 ──────────────────────────────
                # Interface 作成/更新後に独立した MACAddress オブジェクトを生成して紐付ける。
                # べき等性: 同一インターフェースに同一MACが既に紐付け済みの場合はスキップ。
                if self.use_mac_address_model and mac_addr and port_kind == "interface" and nb_if:
                    try:
                        obj_type = (
                            "dcim.interface" if target_type == "device"
                            else "virtualization.vminterface"
                        )
                        existing_macs = list(
                            self.netbox.dcim.mac_addresses.filter(mac_address=mac_addr)
                        )
                        already_assigned = any(
                            str(getattr(m, 'assigned_object_type', '')) == obj_type
                            and getattr(m, 'assigned_object_id', None) == nb_if.id
                            for m in existing_macs
                        )
                        if not already_assigned:
                            mac_obj = self.netbox.dcim.mac_addresses.create({
                                "mac_address": mac_addr,
                                "assigned_object_type": obj_type,
                                "assigned_object_id": nb_if.id,
                            })
                            nb_if.update({"primary_mac_address": mac_obj.id})
                            mac_created += 1
                        else:
                            mac_skipped += 1
                    except Exception as e_mac:
                        logger.warning(
                            f"Failed to assign MAC '{mac_addr}' to interface '{name}' "
                            f"(object_id={object_id}): {e_mac}"
                        )
                # ──────────────────────────────────────────────────────────────────

            except Exception as e:
                logger.error(
                    f"Error creating/updating port '{name}' (kind={port_kind}) "
                    f"for object_id={object_id}: {e}"
                )


        if self.use_mac_address_model:
            logger.info(
                f"Ports import complete: created={created}, updated={updated}, skipped={skipped}. "
                f"MAC (MACAddress model): assigned={mac_created}, already_exists={mac_skipped}."
            )
        else:
            logger.info(f"Ports import complete: created={created}, updated={updated}, skipped={skipped}.")

    def import_cables(self, cables):
        """RackTables Link を NetBox dcim.cables として登録する。"""
        logger.info(f"Importing {len(cables)} Cables into NetBox...")
        created = skipped = 0
        for cable in cables:
            porta_id    = cable["porta_id"]
            portb_id    = cable["portb_id"]
            cable_label = cable.get("cable_label", "") or ""

            a_info = self.port_netbox_map.get(porta_id)
            b_info = self.port_netbox_map.get(portb_id)

            if not a_info or not b_info:
                logger.warning(
                    f"Cable '{cable_label}': one or both ports not found in NetBox "
                    f"(porta_id={porta_id}, portb_id={portb_id}). Skipping."
                )
                skipped += 1
                continue

            a_if   = a_info["obj"]
            b_if   = b_info["obj"]
            a_type = a_info["target_type"]
            b_type = b_info["target_type"]
            a_kind = a_info.get("port_kind", "interface")
            b_kind = b_info.get("port_kind", "interface")

            # NetBox の Cable は Device コンポーネント同士の接続をサポート
            if a_type != "device" or b_type != "device":
                logger.error(
                    f"Cable '{cable_label}': cables involving VM interfaces are not supported "
                    f"(porta={cable['port_a_name']} obj={cable['obj_a']}, "
                    f"portb={cable['port_b_name']} obj={cable['obj_b']}). Skipping."
                )
                skipped += 1
                continue

            def get_cable_obj_type(kind):
                if kind == "console-port":
                    return "dcim.consoleport"
                elif kind == "console-server-port":
                    return "dcim.consoleserverport"
                elif kind == "power-port":
                    return "dcim.powerport"
                elif kind == "power-outlet":
                    return "dcim.poweroutlet"
                return "dcim.interface"


            a_obj_type = get_cable_obj_type(a_kind)
            b_obj_type = get_cable_obj_type(b_kind)

            cdata = {
                "a_terminations": [{"object_type": a_obj_type, "object_id": a_if.id}],
                "b_terminations": [{"object_type": b_obj_type, "object_id": b_if.id}],
                "label": cable_label[:100],
                "status": "connected"
            }
            if a_kind in ("power-port", "power-outlet") or b_kind in ("power-port", "power-outlet"):
                cdata["type"] = "power"

            try:
                self.netbox.dcim.cables.create(**cdata)
                created += 1
            except Exception as e:
                logger.error(
                    f"Error creating cable '{cable_label}' between "
                    f"'{cable['port_a_name']}' (obj={cable['obj_a']}) and "
                    f"'{cable['port_b_name']}' (obj={cable['obj_b']}): {e}"
                )

        logger.info(f"Cables import complete: created={created}, skipped={skipped}.")

    def import_tags(self, tag_tree, tag_storage):
        if not tag_tree:
            logger.info("No TagTree data found.")
            return

        logger.info(f"Processing {len(tag_tree)} TagTree entries and {len(tag_storage)} TagStorage entries...")

        # 1. 除外タグ設定および TagSlugs カスタムマッピングの読み込み
        exclude_names = set()
        if self.config.has_section('ExcludeTags'):
            for name, _ in self.config['ExcludeTags'].items():
                exclude_names.add(name.strip().lower())

        tag_slug_custom = {}
        if self.config.has_section('TagSlugs'):
            for tag_name, custom_slug in self.config['TagSlugs'].items():
                tag_slug_custom[tag_name.strip().lower()] = custom_slug.strip()

        # 2. ツリー構造の構築
        nodes = {}
        children = defaultdict(list)
        for t in tag_tree:
            t_id = t["id"]
            p_id = t["parent_id"]
            nodes[t_id] = t
            if p_id is not None:
                children[p_id].append(t_id)

        # 3. 除外タグ ID 集合 (excluded_ids) の計算 (指定ノードおよび配下サブツリー全体)
        excluded_ids = set()
        queue = []
        for t_id, t in nodes.items():
            if t["name"].strip().lower() in exclude_names:
                queue.append(t_id)

        while queue:
            curr_id = queue.pop(0)
            if curr_id in excluded_ids:
                continue
            excluded_ids.add(curr_id)
            for child_id in children[curr_id]:
                queue.append(child_id)

        if excluded_ids:
            logger.info(f"Excluded {len(excluded_ids)} tags (matching ExcludeTags & subtrees).")

        # 4. 移行対象タグの抽出 (Explicit tags ＝ TagStorage に記録されているタグ、および有効なタグ)
        used_tag_ids = {s["tag_id"] for s in tag_storage}
        target_tags = {}
        for t_id, t in nodes.items():
            if t_id in excluded_ids:
                continue
            # TagStorage に明示的(Explicit)に割り当てられているタグ、または子ノードを持たないリーフタグを対象とする
            if t_id in used_tag_ids or len(children[t_id]) == 0:
                target_tags[t_id] = t

        logger.info(f"Found {len(target_tags)} valid/explicit tags to migrate.")
        if not target_tags:
            return

        # 5. NetBox extras.tags の確保 / 作成
        nb_tags_map = {} # tag_id -> {"name": name, "slug": slug}
        for t_id, t in target_tags.items():

            raw_name = clean_rt_text(t["name"])
            if not raw_name:
                continue

            raw_name_lower = raw_name.strip().lower()
            if raw_name_lower in tag_slug_custom:
                slug = tag_slug_custom[raw_name_lower]
            else:
                slug = slugify.slugify(raw_name) or f"tag-{t_id}"

            try:
                tag_obj = self.netbox.extras.tags.get(slug=slug)
                if not tag_obj:
                    tag_obj = self.netbox.extras.tags.get(name=raw_name)
                if not tag_obj:
                    logger.info(f"Creating NetBox Tag: {raw_name} (slug: {slug})")
                    tag_obj = self.netbox.extras.tags.create(name=raw_name, slug=slug)
                nb_tags_map[t_id] = {"name": tag_obj.name, "slug": tag_obj.slug}
            except Exception as e:
                logger.error(f"Error syncing NetBox Tag '{raw_name}': {e}")


        if not nb_tags_map:
            return

        # 6. TagStorage から各エンティティへのタグ割り当てデータの集計
        entity_tags = defaultdict(list)
        for s in tag_storage:
            realm = s["entity_realm"]
            e_id = s["entity_id"]
            t_id = s["tag_id"]
            if t_id in nb_tags_map:
                tag_data = nb_tags_map[t_id]
                if tag_data not in entity_tags[(realm, e_id)]:
                    entity_tags[(realm, e_id)].append(tag_data)

        logger.info(f"Assigning tags to NetBox entities ({len(entity_tags)} entity assignments)...")

        # 7. 各エンティティへのタグ付与・更新
        for (realm, e_id), tags_to_add in entity_tags.items():
            try:
                if realm == 'object':
                    if e_id in self.device_netbox_map:
                        dev = self.device_netbox_map[e_id]
                        existing_tags = [t if isinstance(t, dict) else {"name": t.name, "slug": t.slug} for t in (dev.tags or [])]
                        updated = False
                        for tag_data in tags_to_add:
                            if not any(t["slug"] == tag_data["slug"] for t in existing_tags):
                                existing_tags.append(tag_data)
                                updated = True
                        if updated:
                            try:
                                dev.update({'tags': existing_tags})
                            except Exception as dev_err:
                                if 'primary_ip' in str(dev_err):
                                    logger.warning(f"Clearing invalid primary_ip on device '{dev.name}' (ID: {e_id}) before applying tags: {dev_err}")
                                    dev.update({'primary_ip4': None, 'primary_ip6': None, 'tags': existing_tags})
                                else:
                                    raise dev_err
                    elif e_id in self.vm_netbox_map:
                        vm = self.vm_netbox_map[e_id]
                        existing_tags = [t if isinstance(t, dict) else {"name": t.name, "slug": t.slug} for t in (vm.tags or [])]
                        updated = False
                        for tag_data in tags_to_add:
                            if not any(t["slug"] == tag_data["slug"] for t in existing_tags):
                                existing_tags.append(tag_data)
                                updated = True
                        if updated:
                            try:
                                vm.update({'tags': existing_tags})
                            except Exception as vm_err:
                                if 'primary_ip' in str(vm_err):
                                    logger.warning(f"Clearing invalid primary_ip on VM '{vm.name}' (ID: {e_id}) before applying tags: {vm_err}")
                                    vm.update({'primary_ip4': None, 'primary_ip6': None, 'tags': existing_tags})
                                else:
                                    raise vm_err

                elif realm == 'rack':
                    if e_id in self.rack_id_map:
                        rack_ref = self.rack_id_map[e_id]
                        rack_obj = rack_ref if not isinstance(rack_ref, int) else self.netbox.dcim.racks.get(rack_ref)
                        if rack_obj:
                            existing_tags = [t if isinstance(t, dict) else {"name": t.name, "slug": t.slug} for t in (rack_obj.tags or [])]
                            updated = False
                            for tag_data in tags_to_add:
                                if not any(t["slug"] == tag_data["slug"] for t in existing_tags):
                                    existing_tags.append(tag_data)
                                    updated = True
                            if updated:
                                rack_obj.update({'tags': existing_tags})

                elif realm in ('ipv4net', 'ipv6net'):
                    if hasattr(self, 'prefix_netbox_map') and e_id in self.prefix_netbox_map:
                        prefix_obj = self.prefix_netbox_map[e_id]
                        if prefix_obj:
                            existing_tags = [t if isinstance(t, dict) else {"name": t.name, "slug": t.slug} for t in (prefix_obj.tags or [])]
                            updated = False
                            for tag_data in tags_to_add:
                                if not any(t["slug"] == tag_data["slug"] for t in existing_tags):
                                    existing_tags.append(tag_data)
                                    updated = True
                            if updated:
                                prefix_obj.update({'tags': existing_tags})
            except Exception as e:
                logger.error(f"Error applying tags to {realm} {e_id}: {e}")


def netbox_purge_data(config, netbox):
    logger.info("Executing NetBox data purge...")

    # Strategy 1: Direct PostgreSQL TRUNCATE via psycopg2
    try:
        import psycopg2
        pg_host = config['NetBox'].get('PG_HOST', 'postgres') if config.has_section('NetBox') else 'postgres'
        pg_user = config['NetBox'].get('PG_USER', 'netbox') if config.has_section('NetBox') else 'netbox'
        pg_pass = config['NetBox'].get('PG_PASSWORD', 'netboxpassword') if config.has_section('NetBox') else 'netboxpassword'
        pg_db = config['NetBox'].get('PG_DB', 'netbox') if config.has_section('NetBox') else 'netbox'
        pg_port = int(config['NetBox'].get('PG_PORT', '5432')) if config.has_section('NetBox') else 5432

        if config.has_section('PostgreSQL'):
            pg_host = config['PostgreSQL'].get('PG_HOST', pg_host)
            pg_port = int(config['PostgreSQL'].get('PG_PORT', pg_port))
            pg_db = config['PostgreSQL'].get('PG_NAME', pg_db)
            pg_user = config['PostgreSQL'].get('PG_USER', pg_user)
            pg_pass = config['PostgreSQL'].get('PG_PWD', pg_pass)

        pg_conn = psycopg2.connect(
            host=pg_host,
            port=pg_port,
            dbname=pg_db,
            user=pg_user,
            password=pg_pass,
            connect_timeout=3
        )
        pg_conn.autocommit = True
        with pg_conn.cursor() as cur:
            # NetBox バージョンを確認して TRUNCATE 対象テーブルを決定
            try:
                from packaging.version import Version
                _nb_ver = netbox.status().get("netbox-version", "0.0.0")
                _use_mac_model = Version(_nb_ver) >= Version("4.2.0")
            except Exception:
                _use_mac_model = False

            tables = [
                "dcim_cable",
                "ipam_ipaddress", "ipam_prefix", "ipam_vlan", "ipam_vlangroup",
                "dcim_interface",
                "virtualization_vminterface", "virtualization_virtualmachine",
                "virtualization_cluster", "virtualization_clustergroup", "virtualization_clustertype",
                "dcim_device", "dcim_devicetype", "dcim_devicerole", "dcim_rack",
                "dcim_location", "dcim_site", "dcim_manufacturer", "extras_tag"
            ]
            # NetBox 4.2+ は dcim_macaddress テーブルも存在する
            if _use_mac_model:
                tables.insert(tables.index("dcim_interface"), "dcim_macaddress")
                logger.info(f"NetBox {_nb_ver}: Adding dcim_macaddress to TRUNCATE list.")
            truncate_sql = f"TRUNCATE TABLE {', '.join(tables)} RESTART IDENTITY CASCADE;"
            cur.execute(truncate_sql)
            logger.info("Successfully truncated NetBox PostgreSQL tables.")
        pg_conn.close()

        # Flush Redis Cache
        try:
            redis_host = config['Redis'].get('REDIS_HOST', 'netbox-redis') if config.has_section('Redis') else 'netbox-redis'
            redis_port = int(config['Redis'].get('REDIS_PORT', '6379')) if config.has_section('Redis') else 6379
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((redis_host, redis_port))
            s.sendall(b"FLUSHALL\r\n")
            s.close()
            logger.info("Successfully flushed NetBox Redis cache.")
        except Exception as e_redis:
            logger.warning(f"Could not flush Redis cache directly: {e_redis}")

        logger.info("NetBox data purge completed via PostgreSQL TRUNCATE.")
        return
    except Exception as e_pg:
        logger.warning(f"PostgreSQL TRUNCATE failed ({e_pg}), falling back to API purging...")

    # Strategy 2: Fallback to pynetbox API purging
    endpoints = [
        ("IP Addresses", netbox.ipam.ip_addresses),
        ("Prefixes", netbox.ipam.prefixes),
        ("VLANs", netbox.ipam.vlans),
        ("VLAN Groups", netbox.ipam.vlan_groups),
        ("Cables", netbox.dcim.cables),
        ("Interfaces", netbox.dcim.interfaces),
        ("VM Interfaces", netbox.virtualization.interfaces),
        ("Virtual Machines", netbox.virtualization.virtual_machines),
        ("Clusters", netbox.virtualization.clusters),
        ("Cluster Groups", netbox.virtualization.cluster_groups),
        ("Cluster Types", netbox.virtualization.cluster_types),
        ("Devices", netbox.dcim.devices),
        ("Device Types", netbox.dcim.device_types),
        ("Device Roles", netbox.dcim.device_roles),
        ("Racks", netbox.dcim.racks),
        ("Locations", netbox.dcim.locations),
        ("Sites", netbox.dcim.sites),
        ("Manufacturers", netbox.dcim.manufacturers),
        ("Tags", netbox.extras.tags),
    ]


    for name, endpoint in endpoints:
        try:
            items = list(endpoint.all())
            if items:
                logger.info(f"Purging {len(items)} {name} from NetBox via API...")
                for item in items:
                    try:
                        item.delete()
                    except Exception as e:
                        logger.warning(f"Failed to delete {name} '{item}': {e}")
            else:
                logger.info(f"No {name} to purge.")
        except Exception as e:
            logger.error(f"Error while querying {name} for purge: {e}")

    logger.info("NetBox data purge completed via API.")


def import_devicetype_library(config, netbox, skip_devicetype_lib=False):
    if skip_devicetype_lib:
        logger.info("Skipping devicetype-library import (--skip-devicetype-library specified).")
        return

    enable_import = True
    repo_url = "https://github.com/netbox-community/devicetype-library.git"
    local_path = "./devicetype-library"
    auto_clone = False
    use_official_script = False
    vendors_filter = []

    if config.has_section('DeviceTypeLibrary'):
        enable_import = config['DeviceTypeLibrary'].getboolean('ENABLE_IMPORT', fallback=True)
        repo_url = config['DeviceTypeLibrary'].get('REPO_URL', repo_url)
        local_path = config['DeviceTypeLibrary'].get('LOCAL_PATH', local_path)
        auto_clone = config['DeviceTypeLibrary'].getboolean('AUTO_CLONE', fallback=False)
        use_official_script = config['DeviceTypeLibrary'].getboolean('USE_OFFICIAL_SCRIPT', fallback=False)
        vendors_str = config['DeviceTypeLibrary'].get('VENDORS', '').strip()
        if vendors_str:
            vendors_filter = [v.strip().lower() for v in vendors_str.split(',') if v.strip()]

    if not enable_import:
        logger.info("Skipping devicetype-library import (ENABLE_IMPORT = False in configuration).")
        return


    logger.info("Starting netbox-community/devicetype-library import process...")

    local_path_abs = os.path.abspath(local_path)
    if not os.path.exists(local_path_abs) or not os.listdir(local_path_abs):
        if auto_clone:
            logger.info(f"Local devicetype-library not found at {local_path_abs}. Auto-cloning from {repo_url}...")
            try:
                subprocess.run(["git", "clone", "--depth", "1", repo_url, local_path_abs], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                logger.info("Git clone completed successfully.")
            except Exception as e:
                logger.error(f"Failed to clone devicetype-library: {e}")
        else:
            logger.warning(f"Local devicetype-library not found at {local_path_abs}. Please clone it on the host side first: git clone {repo_url} {local_path}")
            return
    else:
        logger.info(f"Using local devicetype-library repository at {local_path_abs}.")

    # Only run official script if explicitly configured
    if use_official_script:
        dt_import_script = os.path.abspath("./Device-Type-Library-Import/nb-dt-import.py")
        nb_dt_import_bin = shutil.which("nb-dt-import")

        if os.path.exists(dt_import_script):
            logger.info(f"[Importer Engine] Using official script: '{dt_import_script}' for devicetype-library import.")
            env = os.environ.copy()
            env["REPO_URL"] = repo_url
            env["NETBOX_URL"] = config['NetBox']['NETBOX_HOST']
            env["NETBOX_TOKEN"] = config['NetBox']['NETBOX_TOKEN']
            env["IGNORE_SSL_ERRORS"] = "True"
            env["GIT_PYTHON_REFRESH"] = "quiet"
            if vendors_filter:
                env["VENDORS"] = ",".join(vendors_filter)

            dt_import_dir = os.path.dirname(dt_import_script)
            dt_repo_target = os.path.join(dt_import_dir, "repo")

            if os.name != 'nt' and os.path.exists(local_path_abs) and not os.path.exists(dt_repo_target):
                try:
                    os.symlink(local_path_abs, dt_repo_target)
                except Exception:
                    pass

            cmd = [sys.executable, dt_import_script]
            if vendors_filter:
                cmd.extend(["--vendors", ",".join(vendors_filter)])

            try:
                res = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=dt_import_dir)
                if res.returncode == 0:
                    logger.info(f"Official nb-dt-import.py completed successfully:\n{res.stdout}")
                    return
                else:
                    logger.warning(f"nb-dt-import.py exited with code {res.returncode}: {res.stderr}\n{res.stdout}. Falling back to built-in PyYAML importer...")
            except Exception as e:
                logger.warning(f"Error running nb-dt-import.py ({e}), falling back to built-in PyYAML importer...")

        elif nb_dt_import_bin:
            logger.info(f"[Importer Engine] Using external CLI tool: '{nb_dt_import_bin}' for devicetype-library import.")
            cmd = [
                nb_dt_import_bin,
                "--netbox-url", config['NetBox']['NETBOX_HOST'],
                "--netbox-token", config['NetBox']['NETBOX_TOKEN'],
                "--repo-path", local_path_abs
            ]
            if vendors_filter:
                for v in vendors_filter:
                    cmd.extend(["--vendor", v])
            try:
                res = subprocess.run(cmd, capture_output=True, text=True)
                if res.returncode == 0:
                    logger.info("External CLI nb-dt-import completed successfully.")
                    return
                else:
                    logger.warning(f"nb-dt-import exited with code {res.returncode}: {res.stderr}. Falling back to built-in PyYAML importer...")
            except Exception as e:
                logger.warning(f"Error running nb-dt-import ({e}), falling back to built-in PyYAML importer...")

    logger.info("[Importer Engine] Using built-in PyYAML + pynetbox importer engine for devicetype-library import.")



    try:
        import yaml

    except ImportError:
        logger.error("PyYAML is not installed. Please install PyYAML (pip install PyYAML) to use fallback YAML importer.")
        return

    device_types_dir = os.path.join(local_path_abs, "device-types")
    if not os.path.exists(device_types_dir):
        device_types_dir = local_path_abs

    if not os.path.exists(device_types_dir):
        logger.error(f"Device types directory not found at {device_types_dir}.")
        return

    logger.info(f"Scanning YAML files in {device_types_dir}...")
    yaml_files = []
    for root, dirs, files in os.walk(device_types_dir):
        for file in files:
            if file.endswith(('.yaml', '.yml')):
                yaml_files.append(os.path.join(root, file))

    logger.info(f"Found {len(yaml_files)} YAML definitions in devicetype-library.")

    mfr_cache = {m.name.lower(): m for m in netbox.dcim.manufacturers.all()}
    dt_cache = {d.slug.lower(): d for d in netbox.dcim.device_types.all()}

    imported_count = 0
    for yfile in yaml_files:
        try:
            with open(yfile, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f)
            if not isinstance(data, dict) or 'manufacturer' not in data or 'model' not in data:
                continue

            manufacturer_name = str(data['manufacturer']).strip()
            if vendors_filter and manufacturer_name.lower() not in vendors_filter:
                continue

            mfr_lower = manufacturer_name.lower()
            if mfr_lower not in mfr_cache:
                mfr_slug = slugify.slugify(manufacturer_name)
                mfr_obj = netbox.dcim.manufacturers.get(slug=mfr_slug) or netbox.dcim.manufacturers.get(name=manufacturer_name)
                if not mfr_obj:
                    try:
                        mfr_obj = netbox.dcim.manufacturers.create(name=manufacturer_name, slug=mfr_slug)
                        logger.info(f"Created Manufacturer: {manufacturer_name}")
                    except Exception as e:
                        logger.warning(f"Could not create manufacturer '{manufacturer_name}': {e}")
                        continue
                mfr_cache[mfr_lower] = mfr_obj

            mfr_obj = mfr_cache[mfr_lower]

            model = str(data['model']).strip()
            slug = str(data.get('slug', slugify.slugify(f"{manufacturer_name}-{model}"))).strip()[:50]
            slug_lower = slug.lower()

            if slug_lower in dt_cache:
                continue

            dt_obj = netbox.dcim.device_types.get(slug=slug) or netbox.dcim.device_types.get(model=model, manufacturer_id=mfr_obj.id)
            if not dt_obj:
                dt_kwargs = {
                    'manufacturer': mfr_obj.id,
                    'model': model,
                    'slug': slug,
                    'u_height': int(data.get('u_height', 1)),
                    'is_full_depth': bool(data.get('is_full_depth', False)),
                }
                if 'part_number' in data and data['part_number']:
                    dt_kwargs['part_number'] = str(data['part_number'])[:50]

                try:
                    dt_obj = netbox.dcim.device_types.create(**dt_kwargs)
                    dt_cache[slug_lower] = dt_obj
                    imported_count += 1

                    if 'interfaces' in data and isinstance(data['interfaces'], list):
                        for iface in data['interfaces']:
                            if isinstance(iface, dict) and 'name' in iface:
                                try:
                                    netbox.dcim.interface_templates.create(
                                        device_type=dt_obj.id,
                                        name=str(iface['name']),
                                        type=str(iface.get('type', 'other'))
                                    )
                                except Exception:
                                    pass
                    if 'power-ports' in data and isinstance(data['power-ports'], list):
                        for pport in data['power-ports']:
                            if isinstance(pport, dict) and 'name' in pport:
                                try:
                                    netbox.dcim.power_port_templates.create(
                                        device_type=dt_obj.id,
                                        name=str(pport['name']),
                                        type=str(pport.get('type', 'iec-60320-c14'))
                                    )
                                except Exception:
                                    pass
                    if 'console-ports' in data and isinstance(data['console-ports'], list):
                        for cport in data['console-ports']:
                            if isinstance(cport, dict) and 'name' in cport:
                                try:
                                    netbox.dcim.console_port_templates.create(
                                        device_type=dt_obj.id,
                                        name=str(cport['name']),
                                        type=str(cport.get('type', 'de-9'))
                                    )
                                except Exception:
                                    pass
                except Exception as e_dt:
                    logger.warning(f"Could not create DeviceType '{model}' for '{manufacturer_name}': {e_dt}")

        except Exception as e_file:
            logger.warning(f"Error processing YAML file {yfile}: {e_file}")

    logger.info(f"DeviceType Library import completed. {imported_count} new DeviceTypes imported.")


def validate_config(config, config_path, mode, dry_run=False):
    """
    Validate required configuration sections and keys before running migration.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If required sections or keys are missing for the selected mode.
    """
    if not os.path.isfile(config_path):
        raise FileNotFoundError(
            f"Configuration file '{config_path}' not found. "
            f"Copy 'conf.example' to '{config_path}' and set the required parameters."
        )

    required = {"Log": ["LOGFILE"]}
    if not dry_run:
        if mode in ("all", "export"):
            required["MySQL"] = ["DB_IP", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD"]
        if mode in ("all", "import"):
            required["NetBox"] = ["NETBOX_HOST", "NETBOX_TOKEN"]

    for section, keys in required.items():
        if not config.has_section(section):
            raise ValueError(
                f"Missing required section '[{section}]' in '{config_path}'. "
                f"Check 'conf' for required configuration keys."
            )
        for key in keys:
            if not config[section].get(key, "").strip():
                raise ValueError(
                    f"Missing or empty required key '{key}' in section '[{section}]' of '{config_path}'."
                )


def check_mysql_racks(config, exported_data=None, db_extractor_cls=DBExtractor):
    """
    Verify that RackTables MySQL is accessible and contains rack data.

    Returns:
        tuple[bool, int, str]: (is_ok, rack_count, status_message)
    """
    if exported_data is not None:
        racks = exported_data.get("infrastructure", {}).get("racks", [])
        rack_count = len(racks)
        if rack_count > 0:
            return True, rack_count, f"OK (Racks: {rack_count})"
        return False, 0, "NG (Racks: 0 - no rack data found in MySQL)"

    mysql_keys = ("DB_IP", "DB_PORT", "DB_NAME", "DB_USER", "DB_PWD")
    if not config.has_section("MySQL") or any(not config["MySQL"].get(k, "").strip() for k in mysql_keys):
        return False, 0, "NG (Missing or incomplete [MySQL] configuration in conf)"

    try:
        extractor = db_extractor_cls(config)
        extractor.connect()
        try:
            with extractor.con.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM Rack;")
                row = cur.fetchone()
                rack_count = int(row[0]) if row else 0
        finally:
            if extractor.con and extractor.con.open:
                extractor.con.close()
    except Exception as e:
        return False, 0, f"NG ({e})"

    if rack_count > 0:
        return True, rack_count, f"OK (Racks: {rack_count})"
    return False, 0, "NG (Racks: 0 - no rack data found in MySQL)"


def check_netbox_api(config, netbox_api=None):
    """
    Verify read-only connectivity and token authentication against the NetBox REST API.

    Returns:
        tuple[bool, str]: (is_ok, status_message)
    """
    host = config["NetBox"].get("NETBOX_HOST", "").strip() if config.has_section("NetBox") else ""
    token = config["NetBox"].get("NETBOX_TOKEN", "").strip() if config.has_section("NetBox") else ""

    if netbox_api is None:
        if not host or not token:
            return False, "NG (Missing or incomplete [NetBox] configuration in conf)"
        try:
            pynetbox = _require_module("pynetbox", "pynetbox")
            netbox_api = pynetbox.api(host, token=token)
        except Exception as e:
            return False, f"NG ({e})"

    try:
        status = netbox_api.status()
        netbox_api.dcim.racks.count()
        nb_ver = status.get("netbox-version", "unknown") if isinstance(status, dict) else "unknown"
        return True, f"OK ({host or 'NetBox'}, NetBox v{nb_ver})"
    except Exception as e:
        return False, f"NG ({e})"


def log_dry_run_check_summary(mysql_ok, mysql_msg, netbox_ok, netbox_msg):
    """Log the final dry-run OK/NG check results for MySQL rack data and NetBox API access."""
    logger.info("=" * 60)
    logger.info("[DRY-RUN] Final Check Result:")
    if mysql_ok:
        logger.info(f"  MySQL Data (Racks) : {mysql_msg}")
    else:
        logger.error(f"  MySQL Data (Racks) : {mysql_msg}")
    if netbox_ok:
        logger.info(f"  NetBox API Access  : {netbox_msg}")
    else:
        logger.error(f"  NetBox API Access  : {netbox_msg}")
    logger.info("=" * 60)


def main():
    """CLI entry point for RackTables to NetBox migration tool."""
    parser = argparse.ArgumentParser(description="RackTables to NetBox Migration Tool")
    parser.add_argument(
        "--mode", choices=["all", "export", "import"], default="all",
        help="Operation mode: 'export' (MySQL->JSON), 'import' (JSON->NetBox), or 'all' (Export->JSON->Import)"
    )
    parser.add_argument(
        "--json-file", default="racktables_dump.json",
        help="Path to intermediate JSON dump file (default: racktables_dump.json)"
    )
    parser.add_argument(
        "--no-purge", action="store_true",
        help="Skip purging existing NetBox data before import"
    )
    parser.add_argument(
        "--skip-devicetype-library", action="store_true",
        help="Skip importing netbox-community/devicetype-library before NetBox data import"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Validate extracted data and NetBox mappings without modifying NetBox"
    )
    parser.add_argument(
        "--config", default="conf",
        help="Path to configuration file (default: conf)"
    )
    args = parser.parse_args()

    config = configparser.ConfigParser()
    config.read(args.config, encoding="utf-8")
    validate_config(config, args.config, args.mode, dry_run=args.dry_run)

    logger.setLevel(logging.DEBUG)

    fh = logging.FileHandler(config['Log']['LOGFILE'], encoding='utf-8')
    fh.setLevel(logging.DEBUG)

    error_logfile = config['Log'].get('ERROR_LOG', 'error.log')
    error_fh = logging.FileHandler(error_logfile, mode='w', encoding='utf-8')
    error_fh.setLevel(logging.ERROR)

    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)

    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    fh.setFormatter(formatter)
    error_fh.setFormatter(formatter)
    ch.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(error_fh)
    logger.addHandler(ch)

    logger.info(f"RackTables to NetBox Migration Tool v{__version__}" + (" [DRY-RUN]" if args.dry_run else ""))

    netbox = None
    if args.mode in ("all", "import") and not args.dry_run:
        pynetbox = _require_module("pynetbox", "pynetbox")
        netbox = pynetbox.api(config['NetBox']['NETBOX_HOST'], token=config['NetBox']['NETBOX_TOKEN'])

    exported_data = None
    mysql_export_error = None
    if args.mode in ("all", "export"):
        logger.info(f"[Mode: {args.mode}] Extracting data from RackTables DB...")
        try:
            extractor = DBExtractor(config)
            exported_data = extractor.export_all()

            logger.info(f"Saving extracted data to JSON file: {args.json_file}")
            with open(args.json_file, "w", encoding="utf-8") as f:
                json.dump(exported_data, f, indent=2, ensure_ascii=False)
            logger.info(f"JSON export completed: {args.json_file}")
        except Exception as e:
            if not args.dry_run:
                raise
            mysql_export_error = e
            logger.error(f"[DRY-RUN] Failed to extract data from RackTables DB: {e}")

    if args.mode in ("all", "import"):
        data_to_import = exported_data
        if data_to_import is None:
            if not os.path.isfile(args.json_file):
                if not args.dry_run:
                    raise FileNotFoundError(
                        f"JSON dump file '{args.json_file}' not found. "
                        f"Run with '--mode export' first or specify a valid '--json-file' path."
                    )
                logger.error(f"[DRY-RUN] JSON dump file '{args.json_file}' not found; skipping JSON data validation.")
            else:
                logger.info(f"[Mode: {args.mode}] Reading JSON data from file: {args.json_file}")
                with open(args.json_file, "r", encoding="utf-8") as f:
                    data_to_import = json.load(f)

        if data_to_import is not None:
            if args.dry_run:
                logger.info("[DRY-RUN] Skipping NetBox data purge and DeviceType Library import.")
            else:
                # Step 1: Purge NetBox data if requested
                if not args.no_purge:
                    netbox_purge_data(config, netbox)

                # Step 2: Import netbox-community/devicetype-library right after purge
                import_devicetype_library(config, netbox, skip_devicetype_lib=args.skip_devicetype_library)

            # Step 3: Import (or dry-run validate) RackTables data into NetBox
            importer = NetBoxImporter(config, netbox, dry_run=args.dry_run)
            importer.import_all(data_to_import)

    if args.dry_run:
        if mysql_export_error is not None:
            mysql_ok, mysql_msg = False, f"NG ({mysql_export_error})"
        else:
            mysql_ok, _, mysql_msg = check_mysql_racks(config, exported_data=exported_data)
        netbox_ok, netbox_msg = check_netbox_api(config, netbox_api=netbox)
        log_dry_run_check_summary(mysql_ok, mysql_msg, netbox_ok, netbox_msg)
        if not (mysql_ok and netbox_ok):
            sys.exit(1)

    logger.info('[!] All tasks completed successfully!')


if __name__ == '__main__':
    main()

