# NetBoxにおけるSolaris仮想化環境のモデリング方針

English: [README_specific_solaris.md](README_specific_solaris.md)

本ドキュメントは、Solaris LDOM（Logical Domains）環境における「物理サーバー、LDOM、ゾーン」という階層的なネスト構造を、NetBox上で整合性を保って管理するための設計方針を定義する。

---

## 1. 基本方針

無駄な仮想マシン（Virtual Machine）の重複登録や物理サーバー（Physical Server）の自己矛盾を避けるため、Solarisの各レイヤーを以下のようにNetBoxのオブジェクトへ割り当てる。

| Solarisの要素 | NetBoxの割り当てオブジェクト | 設計方針と役割 |
| :--- | :--- | :--- |
| **物理サーバー（Physical Server）** | **Device** | 物理筐体を表し、ラック位置やシリアル番号を保持する。 |
| **コントロール・ドメイン（Control Domain）** | **Deviceの属性** | 物理サーバーと1対1で動作し全体を制御するため、別個の仮想マシン（Virtual Machine）は作成せず、DeviceのプライマリIP（Primary IP）やプラットフォーム（Platform）として表現する。 |
| **グローバル・ゾーン（Global Zone）** | **Deviceの属性** | コントロール・ドメインと同様にホストOS環境として扱い、仮想マシンとしての登録は行わない。 |
| **I/Oドメイン（I/O Domain）** | **VirtualMachine** | 物理リソースを他ドメインに提供する独立した仮想実体として扱い、役割には「Solaris I/O Domain」を割り当てる。 |
| **ゲストLDOM（Guest LDOM）** | **VirtualMachine** | 独立して動作するメインの仮想ドメインであり、役割には「Solaris Guest LDOM」を割り当てる。 |
| **カーネル・ゾーン（Kernel Zone）** | **VirtualMachine** | 独自カーネルで動作するゾーンであり、役割には「Solaris Kernel Zone」を割り当てる。 |
| **ネイティブ・ゾーン（Native Zone）** | **VirtualMachine** | ホスト共有カーネルで動作するゾーンであり、役割には「Solaris Native Zone」を割り当てる。 |

---

## 2. クラスタとクラスタタイプ

NetBoxにおけるクラスタ（Cluster）の定義を「この物理ホスト（Physical Host）が提供する仮想化環境」とする。
物理ホストごとに1つのクラスタを対応させ、LDOMごとのクラスタ作成は行わない。

### 命名規則
物理ホスト名に基づき、先頭にアンダースコアを付与した名前とする（例：`_host01`）。
物理サーバーをこのクラスタのメンバーデバイス（Member Device）に登録する。
この物理サーバー上で稼働するすべての仮想ドメインおよびゾーンは、すべてこのクラスタに所属させる。

### クラスタタイプの動的判定
物理ホストに所属する仮想マシンの構成情報から、クラスタタイプ（Cluster Type）を以下の2つのいずれかに決定する。

- **Oracle VM Server**（slug: `oracle-vm-server`）：対象クラスタの配下にLDOMオブジェクトが1つ以上含まれる場合。
- **Solaris Zones**（slug: `solaris-zones`）：クラスタ内にLDOMオブジェクトが含まれず、ゾーンのみが動作している場合。

---

## 3. 親子関係の追跡

NetBoxの標準仕様には、仮想マシンが別の仮想マシンを直接ホストする関係や、所属クラスタ外のデバイスに仮想マシンをピン留めする直接のフィールドがない。
この制約を以下の設計で解決する。

### 物理サーバーとドメインの関係
仮想マシンの `device` フィールドに対象の物理ホストデバイス（Physical Host Device）を指定する。
物理ホスト自体が対象クラスタのメンバーとして登録されているため、デバイス整合性の制約を満たした状態で登録できる。

### LDOMとゾーンの関係
NetBoxの仮想マシンモデルには直接の親子関係フィールドがないため、カスタムフィールド（Custom Field）**`parent_vm`**（参照先: `VirtualMachine`）およびカスタムリンク（Custom Link）**`Child Zones`** ボタンを組み合わせた双方向ナビゲーションを構築する。

- **子ゾーン画面**：`parent_vm` カスタムフィールドにより、親LDOMへのハイパーリンク（`Parent VM / LDOM`）を表示する。
- **親LDOM画面**：画面右上のボタンエリアに配置される **Child Zones** ボタンを押下することで、配下で稼働するすべてのゾーン仮想マシン（`cf_parent_vm` フィルタ適用済み）を一覧表示する。

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
                          (カスタムフィールド parent_vm で親LDOMを指定)
```

---

## 4. NetBoxでの定義手順

NetBox 4.x 上で上記設計を実現するためのオブジェクト定義を以下に示す。

### 4.1. カスタムフィールドの作成
`Customization` → `Custom Fields` → `+ Add` から以下の設定で作成する。
- **Model**：`Virtualization > Virtual Machine`
- **Type**：`Object`
- **Related Object Type**：`Virtualization > Virtual Machine`
- **Name**：`parent_vm`
- **Label**：`Parent VM / LDOM`

### 4.2. カスタムリンクボタンの作成
`Customization` → `Custom Links` → `+ Add` から以下の設定で作成する。
- **Content Type**：`Virtualization > Virtual Machine`
- **Name**：`Child Zones`
- **Link Text**：`Child Zones`
- **Link URL**：`/virtualization/virtual-machines/?cf_parent_vm={{ object.id }}`
- **Button Class**：`Green`
