# RackTables to NetBox Migration and Operations Guide

日本語: [INSTALL.ja.md](INSTALL.ja.md)

This document explains the procedure for migrating data from RackTables to NetBox and setting up the container stack.

---

## 1. Prerequisites and Stack Architecture

- **Container Runtime**:
  - This project uses **`podman`** and **`podman compose`**.
- **Container Stack Components**:
  - **NetBox (4.x)**: Target DCIM and IPAM application.
  - **PostgreSQL 16**: Database for NetBox.
  - **Redis 7**: Task queue and cache store for NetBox.
  - **RackTables DB (MySQL 8.0)**: Source RackTables database.
  - **Migration Runner (`ol10-runner`)**: Container for running migration scripts, built on Oracle Linux 10 and Python 3.14.

---

## 2. Environment Setup and Container Startup

### 2.1. Preparing the Configuration File
Copy the sample configuration file `conf.sample` to `conf` and edit it to match the target environment.

```bash
cp conf.sample conf
```

Configure the following sections in the `conf` file.
- **`[MySQL]`**: Connection settings for the RackTables database (defaults are `DB_IP = racktables-db`, `DB_PORT = 3306`, and `DB_NAME = racktables_db`).
- **`[NetBox]`**: NetBox API endpoint URL and API Token.
- **`[SiteSlugs]`, `[TagSlugs]`, `[DeviceRoleMapping]`**: Custom mapping rules for converting source names into NetBox Slugs and Device Roles.

### 2.2. Starting the Container Stack
Run `podman compose` to start the container stack in the background.

```bash
podman compose up -d
```

### 2.3. Configuring LDAP and Active Directory Authentication (Optional)
NetBox login authentication can be integrated with LDAP or Active Directory by providing an environment variable file.

1. Copy the sample file `ldap.env.sample` to `ldap.env` and edit it to match the directory server settings.
   ```bash
   cp ldap.env.sample ldap.env
   ```
2. Uncomment the `env_file` (`./ldap.env`) entry under the `netbox-app` service definition in `docker-compose.yml`.
3. Restart the container stack to apply the configuration.
   ```bash
   podman compose down
   podman compose up -d
   ```

---

## 3. Data Migration Procedure

Execute the migration script inside the `ol10-runner` container.

### 3.1. Pre-verification with Dry-run
Run the script with the `--dry-run` option to output verification logs for the migration targets without modifying the NetBox database.

```bash
podman exec ol10-runner python /app/racktables2netbox.py --dry-run
```

### 3.2. Executing the Production Migration
After confirming that no errors appear during pre-verification, execute the migration.

```bash
podman exec ol10-runner python /app/racktables2netbox.py
```

> [!TIP]
> **Resetting NetBox Data**
> If an error occurs during migration or if a clean state is needed to rerun the migration from scratch, run `clean_netbox.py` to purge the registered data in NetBox.
> ```bash
> podman exec ol10-runner python /app/clean_netbox.py
> ```

---

## 4. Checking Log Files

After the migration script runs, the following log files are generated in the project root directory.
- **`migration.log`**: Execution progress and records of created objects.
- **`error.log`**: Error and warning records, including unmapped outer interface types (OIF) and skipped items.
