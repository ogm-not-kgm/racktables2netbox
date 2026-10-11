# Modeling Policy for Solaris Virtualization Environments in NetBox

日本語: [README_specific_solaris.ja.md](README_specific_solaris.ja.md)

This document defines the design policy for managing the hierarchical nested structure of Physical Servers, LDOMs, and Zones in Solaris LDOM (Logical Domains) environments while maintaining data consistency in NetBox.

---

## 1. Basic Policy

To avoid duplicate registration of Virtual Machines and self-contradictions in Physical Server representations, each Solaris layer is mapped to a NetBox object as follows.

| Solaris Layer | Assigned NetBox Object | Design Policy and Role |
| :--- | :--- | :--- |
| **Physical Server** | **Device** | Represents the physical chassis and holds the rack position and serial number. |
| **Control Domain** | **Device Attributes** | Operates one-to-one with the physical server and controls the system, so it is represented as the Device's Primary IP and Platform without creating a separate Virtual Machine. |
| **Global Zone** | **Device Attributes** | Treated as the host OS environment in the same way as the Control Domain, and is not registered as a Virtual Machine. |
| **I/O Domain** | **VirtualMachine** | Treated as an independent virtual entity that provides physical resources to other domains, and assigned the role `Solaris I/O Domain`. |
| **Guest LDOM** | **VirtualMachine** | Operates as an independent virtual domain, and is assigned the role `Solaris Guest LDOM`. |
| **Kernel Zone** | **VirtualMachine** | Operates as a zone with its own kernel, and is assigned the role `Solaris Kernel Zone`. |
| **Native Zone** | **VirtualMachine** | Operates as a zone sharing the host kernel, and is assigned the role `Solaris Native Zone`. |

---

## 2. Clusters and Cluster Types

A **Cluster** in NetBox is defined as the virtualization environment provided by a single Physical Host.
One Cluster corresponds to each Physical Host, and separate Clusters are not created for individual LDOMs.

### Naming Convention
The Cluster name is formed by prefixing an underscore to the physical host name (for example, `_host01`).
The Physical Server is registered as a Member Device of this Cluster.
All virtual domains and zones running on this Physical Server are assigned to this Cluster.

### Dynamic Determination of Cluster Types
Based on the configuration of the Virtual Machines belonging to the Physical Host, the **Cluster Type** is determined as one of the following two types.

- **Oracle VM Server** (slug: `oracle-vm-server`): Used when the target Cluster contains one or more LDOM objects.
- **Solaris Zones** (slug: `solaris-zones`): Used when the Cluster contains no LDOM objects and runs only Zones.

---

## 3. Tracking Parent and Child Relationships

NetBox's standard data model does not provide a built-in field for a Virtual Machine to host another Virtual Machine directly, nor does it allow pinning a Virtual Machine to a Device outside its assigned Cluster.
The following design resolves these constraints.

### Relationship Between Physical Servers and Domains
The target Physical Host Device is specified in the `device` field of the Virtual Machine.
Because the Physical Host itself is registered as a member of the target Cluster, the Virtual Machine satisfies NetBox's device-cluster consistency constraint.

### Relationship Between LDOMs and Zones
Because NetBox's Virtual Machine model lacks a direct parent-child relationship field, bidirectional navigation is constructed by combining a **Custom Field** named **`parent_vm`** (referencing `VirtualMachine`) and a **Custom Link** button named **`Child Zones`**.

- **Child Zone View**: The `parent_vm` Custom Field displays a hyperlink (`Parent VM / LDOM`) to the parent LDOM.
- **Parent LDOM View**: Clicking the **Child Zones** button in the top-right action area displays a filtered list of all child Zone Virtual Machines (`cf_parent_vm` filter applied).

```text
                     ┌───────────────────────┐
                     │ Device                │
                     │ host1                 │
                     │                       │
                     │ Role: SPARC Solaris   │
                     └───────────┬───────────┘
                                 │
                                 │ Member of Cluster
                                 ▼
                     ┌───────────────────────┐
                     │ _host1                │
                     │ Type: Oracle VM Server│
                     └───────────┬───────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                  │
              ▼                  ▼                  ▼
          ┌───────┐          ┌─────────┐       ┌─────────┐
          │ io01  │          │ ldom01  │       │ ldom02  │
          │ I/O   │          │ Guest   │       │ Guest   │
          │Domain │          │ LDOM    │       │ LDOM    │
          └───────┘          └────┬────┘       └────┬────┘
                                  │                 │
                          ┌───────┼──────┐       ┌──┴───┐
                          ▼       ▼      ▼       ▼      ▼
                        kz01    kz02    nz01    kz03   nz02
                        (KZ)    (KZ)    (NZ)    (KZ)   (NZ)
                         │       │       │       │      │
                         └───────┴───────┴───────┴──────┘
                          (Specifies parent LDOM via Custom Field parent_vm)
```

---

## 4. Definition Procedure in NetBox

The object definitions required to implement this design in NetBox 4.x are shown below.

### 4.1. Creating the Custom Field
Navigate to `Customization` -> `Custom Fields` -> `+ Add` and create the field with the following settings.
- **Model**: `Virtualization > Virtual Machine`
- **Type**: `Object`
- **Related Object Type**: `Virtualization > Virtual Machine`
- **Name**: `parent_vm`
- **Label**: `Parent VM / LDOM`

### 4.2. Creating the Custom Link Button
Navigate to `Customization` -> `Custom Links` -> `+ Add` and create the link with the following settings.
- **Content Type**: `Virtualization > Virtual Machine`
- **Name**: `Child Zones`
- **Link Text**: `Child Zones`
- **Link URL**: `/virtualization/virtual-machines/?cf_parent_vm={{ object.id }}`
- **Button Class**: `Green`
