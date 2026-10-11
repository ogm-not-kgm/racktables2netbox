# Changelog

日本語: [CHANGELOG.ja.md](CHANGELOG.ja.md)

Notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.1] - 2026-10-12

### Changed
- **Dynamic PostgreSQL Table Truncation (`netbox_purge_data`)**:
  - Replaced the static table list with a dynamic query against `pg_tables` (`schemaname = 'public'`) to truncate all NetBox data tables (`TRUNCATE TABLE ... RESTART IDENTITY CASCADE`) while preserving `users_*`, `auth_*`, and `django_*` tables.
- **Migration Runner Container (`Dockerfile.runner`)**:
  - Added the `tzdata` package to the Oracle Linux 10 runner image.

## [1.1.0] - 2026-10-11

### Added
- **NetBox 4.x Support**:
  - Added support for NetBox 4.x API specifications and data models.
  - Implemented automatic version detection via `nb.status()` to support the `MACAddress` model in NetBox 4.2 and later.
- **DCIM and Virtualization Migration**:
  - Implemented dynamic CIDR calculation and automatic Prefix creation using Longest Prefix Match (LPM).
  - Added rear rack face mounting detection based on `RackSpace.atom` values.
  - Added fallback rack placement handling for 0U devices.
  - Implemented automatic parent device cluster inheritance for Virtual Machines.
  - Added port type mappings for 10G and 25G DAC cables and Console Ports.
  - Added a Dry-run mode (`--dry-run`) to validate migration data, MySQL rack counts, and NetBox API connectivity (OK/NG) without modifying the database.
- **Containerized Execution Environment (Enterprise Linux 10 and Python 3.14)**:
  - Added `docker-compose.yml` consisting of NetBox, PostgreSQL 16, Redis 7, MySQL 8.0 (RackTables DB), and the migration runner container (using the standard NetBox container entrypoint).
  - Added the migration runner container (`Dockerfile.runner`) using Oracle Linux 10 and Python 3.14 from the official AppStream repository.
- **Authentication Integration**:
  - Added a sample environment variable configuration file (`ldap.env.sample`) for integrating the container with LDAP and Active Directory (`REMOTE_AUTH_BACKEND` in `docker-compose.yml` is commented out by default so the standard `netbox:latest` image starts without external dependencies).
- **Documentation**:
  - Added setup and migration guides in Japanese and English ([INSTALL.ja.md](INSTALL.ja.md), [INSTALL.md](INSTALL.md)).
  - Added modeling design documents for Solaris hierarchical virtualization in Japanese and English ([README_specific_solaris.ja.md](README_specific_solaris.ja.md), [README_specific_solaris.md](README_specific_solaris.md)).

### Changed
- **Field and Query Updates for NetBox 4.x API**:
  - Changed the Device Role assignment field from the legacy `device.device_role` to `device.role`.
  - Replaced legacy Tag slug string arrays with numeric ID arrays (`[tag.id, ...]`).
  - Changed Cluster lookup queries from `slug` to `name` filtering.
- **Configuration File and Reset Script Updates**:
  - Changed the default database name (`DB_NAME`) in `conf.sample` to `racktables_db` to match `docker-compose.yml`, and added mapping configuration sections for slugs and roles.
  - Implemented direct deletion via PostgreSQL `TRUNCATE TABLE ... RESTART IDENTITY CASCADE` and Redis cache invalidation in `clean_netbox.py` to speed up data resets during testing, and migrated configuration loading to `configparser`.

## [1.0.0]

- Initial version forked from [goebelmeier/racktables2netbox](https://github.com/goebelmeier/racktables2netbox) (`__version__ = 1.00`).
