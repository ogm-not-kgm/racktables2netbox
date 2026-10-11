# RackTables to NetBox 移行および運用手順書

English: [INSTALL.md](INSTALL.md)

本ドキュメントでは、RackTables から NetBox へのデータ移行（Migration）手順、およびコンテナスタックの構築手順を説明する。

---

## 1. 前提条件と環境構成

- **コンテナランタイム（Container Runtime）**：
  - 本プロジェクトでは **`podman`** および **`podman compose`** を使用する。
- **コンテナスタックの構成**：
  - **NetBox（4.x）**：移行先のDCIMおよびIPAMアプリケーション。
  - **PostgreSQL 16**：NetBox用のデータベース。
  - **Redis 7**：NetBox用のタスクキューおよびキャッシュストア。
  - **RackTables DB（MySQL 8.0）**：移行元のRackTablesデータベース。
  - **Migration Runner（`ol10-runner`）**：Oracle Linux 10 と Python 3.14 で構成される移行スクリプト実行用コンテナ。

---

## 2. 環境構築とコンテナの起動

### 2.1. 設定ファイルの準備
設定サンプルファイル `conf.sample` をコピーして `conf` を作成し、実行環境に合わせて編集する。

```bash
cp conf.sample conf
```

`conf` ファイル内の以下のセクションを設定する。
- **`[MySQL]`**：RackTablesデータベースの接続情報（デフォルトは `DB_IP = racktables-db`、`DB_PORT = 3306`、`DB_NAME = racktables_db`）。
- **`[NetBox]`**：NetBox APIの接続先URLおよびAPIトークン（API Token）。
- **`[SiteSlugs]`、`[TagSlugs]`、`[DeviceRoleMapping]`**：日本語名称から英語スラッグ（Slug）やデバイスロール（Device Role）へ変換するためのカスタムマッピング設定。

### 2.2. コンテナスタックの起動
`podman compose` を実行し、コンテナスタックをバックグラウンドで起動する。

```bash
podman compose up -d
```

### 2.3. LDAPおよびActive Directory認証の設定（任意）
環境変数ファイルを用意することで、NetBoxのログイン認証をLDAPまたはActive Directoryと連携できる。

1. サンプルファイル `ldap.env.sample` をコピーして `ldap.env` を作成し、接続先ディレクトリサーバーの情報に合わせて編集する。
   ```bash
   cp ldap.env.sample ldap.env
   ```
2. `docker-compose.yml` の `netbox-app` サービス定義内にある `env_file`（`./ldap.env`）のコメントアウトを解除する。
3. コンテナスタックを再起動して設定を反映する。
   ```bash
   podman compose down
   podman compose up -d
   ```

---

## 3. データ移行の手順

移行スクリプトは `ol10-runner` コンテナ内で実行する。

### 3.1. ドライランによる事前検証
`--dry-run` オプションを指定して実行し、NetBoxのデータベースを変更せずに移行対象データの検証ログを出力する。

```bash
podman exec ol10-runner python /app/racktables2netbox.py --dry-run
```

### 3.2. 本番移行の実行
事前検証でエラーがないことを確認したうえで、本番の移行処理を実行する。

```bash
podman exec ol10-runner python /app/racktables2netbox.py
```

> [!TIP]
> **データの初期化（リセット）**
> 移行途中でエラーが発生した場合や最初からやり直す場合は、`clean_netbox.py` を実行してNetBoxのデータを一括削除（初期化）できる。
> ```bash
> podman exec ol10-runner python /app/clean_netbox.py
> ```

---

## 4. ログファイルの確認

移行スクリプトの実行後、プロジェクトルートに以下のログファイルが出力される。
- **`migration.log`**：移行処理の進行状況と作成されたオブジェクトの記録。
- **`error.log`**：未定義のポート種別（OIF）やスキップされた項目などのエラーおよび警告の記録。
