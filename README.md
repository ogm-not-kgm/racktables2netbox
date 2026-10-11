# racktables2netbox

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Powered by Gemini](https://img.shields.io/badge/Powered%20by-Gemini-8E75B2?style=flat-square&logo=googlegemini&logoColor=white)
![Built with Antigravity](https://img.shields.io/badge/Built%20with-Antigravity-4285F4?style=flat-square)

日本語: [README.ja.md](README.ja.md)

`racktables2netbox.py` is a script that migrates data center assets managed in [RackTables](https://github.com/racktables/racktables) to [NetBox](https://github.com/netbox-community/netbox).
It automates the migration of physical infrastructure, IP address management (IPAM), network connections, virtualization clusters, and server configurations (verified on NetBox 4.x).

---

## Main Features

- **DCIM (Data Center Infrastructure Management) Migration**:
  - Migrates Sites, Locations, Racks, Manufacturers, Device Types, and Devices.
  - Determines Front and Rear mounting orientations from rack mounting positions.
  - Handles the placement of 0U PDUs and console servers.
  - Integrates with definitions from the NetBox Device Type Library.
- **Interface and Cable Wiring Migration**:
  - Migrates connection records for Ports, Interfaces, and Cables.
  - Supports the standalone `MACAddress` object model introduced in NetBox 4.2 and later.
  - Converts DAC, optical fiber, and Ethernet connector types into NetBox port types.
- **IPAM (IP Address Management) Migration**:
  - Migrates VLANs and VLAN Groups.
  - Calculates CIDRs dynamically using Longest Prefix Match (LPM) and creates Prefixes.
  - Assigns IP Addresses to interfaces and configures Primary IPs.
- **Virtualization and Hierarchical Modeling**:
  - Maps Clusters and Cluster Types automatically.
  - Associates Virtual Machines with their parent physical devices.
  - Supports hierarchical virtualization modeling for Solaris LDOMs and Zones (see [README_specific_solaris.md](README_specific_solaris.md) for design details).
- **Pre-verification and Reset**:
  - Provides a Dry-run mode (`--dry-run`) that verifies migration targets without modifying the database.
  - Includes a reset script (`clean_netbox.py`) that deletes registered data in NetBox.
  - Provides a containerized execution environment that runs with `podman compose`.

---

## Quick Start

Step-by-step instructions for environment setup and migration execution are documented in [INSTALL.md](INSTALL.md).

```bash
# 1. Clone the repository
git clone https://github.com/ogm-not-kgm/racktables2netbox.git
cd racktables2netbox

# 2. Configure credentials and mappings
cp conf.sample conf
vim conf

# 3. Start the containers
podman compose up -d

# 4. Run pre-verification in dry-run mode
podman exec ol10-runner python /app/racktables2netbox.py --dry-run

# 5. Execute the migration
podman exec ol10-runner python /app/racktables2netbox.py
```

---

## Documentation

- **[INSTALL.md](INSTALL.md)**: Guide for building the environment, editing configuration files, and executing the migration.
- **[README_specific_solaris.md](README_specific_solaris.md)**: Modeling design document for representing hierarchical virtualization (LDOMs and Zones) in Solaris environments on NetBox.
- **[CHANGELOG.md](CHANGELOG.md)**: Version history of changes.

---

## Contributing

Bug reports and feature proposals are accepted via [Issues](https://github.com/ogm-not-kgm/racktables2netbox/issues).
Follow the steps below when submitting a Pull Request.

1. Fork this repository.
2. Create a Feature Branch (`git checkout -b feature/AmazingFeature`).
3. Commit the changes (`git commit -m 'Add some AmazingFeature'`).
4. Push to the branch (`git push origin feature/AmazingFeature`).
5. Open a Pull Request.

---

## Credits and License

- This script was developed starting from [goebelmeier/racktables2netbox](https://github.com/goebelmeier/racktables2netbox) and [Device42 Migration Script](https://github.com/device42/Racktables-to-Device42-Migration).
- Copyright (c) 2018 Timo Reimann
- Copyright (c) 2026 Hironori OGUMA
- Licensed under the [MIT License](LICENSE.md).
