# 変更履歴（Changelog）

English: [CHANGELOG.md](CHANGELOG.md)

本プロジェクトにおける主な変更点をこのファイルに記録する。

形式は [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) に準拠し、バージョニングは [セマンティックバージョニング（Semantic Versioning）](https://semver.org/spec/v2.0.0.html) に従う。

## [1.1.1] - 2026-10-12

### 変更（Changed）
- **動的テーブル指定によるPostgreSQLデータ初期化（`netbox_purge_data`）**：
  - 静的なテーブルリスト指定から、PostgreSQLの `pg_tables`（`schemaname = 'public'`）を参照した動的テーブル抽出方式へ変更し、`users_*`、`auth_*`、`django_*` を除く全テーブルを一括で `TRUNCATE TABLE ... RESTART IDENTITY CASCADE` するよう更新した。
- **移行実行コンテナ（`Dockerfile.runner`）**：
  - Oracle Linux 10 の移行実行コンテナイメージに `tzdata` パッケージを追加した。

## [1.1.0] - 2026-10-11

### 追加（Added）
- **NetBox 4.x への対応**：
  - NetBox 4.x のAPI仕様およびデータモデルに対応した。
  - `nb.status()` によるバージョン自動判定を実装し、NetBox 4.2以降のMACアドレス（`MACAddress`）モデルに対応した。
- **DCIMおよび仮想化の移行機能**：
  - 最長一致検索（Longest Prefix Match）による動的なCIDR算出とプレフィックス（Prefix）の自動作成を実装した。
  - `RackSpace.atom` の値に基づくラック背面（Rear）マウントの判定を追加した。
  - 0Uデバイスのラック配置フォールバック処理を追加した。
  - 仮想マシン（Virtual Machine）に対する親デバイスの所属クラスタ自動継承を実装した。
  - 10Gおよび25GのDACケーブルならびにコンソールポート（Console Port）の型マッピングを追加した。
  - データベースを変更せずに移行データおよびMySQLラック本数・NetBox API疎通（OK/NG）を検証するドライラン（Dry-run）モード（`--dry-run`）を追加した。
- **コンテナ実行環境（Enterprise Linux 10 および Python 3.14）**：
  - NetBox、PostgreSQL 16、Redis 7、MySQL 8.0（RackTables DB）、および移行実行コンテナで構成される `docker-compose.yml` を追加した（NetBoxコンテナの標準エントリポイント（Entrypoint）を使用）。
  - Oracle Linux 10 と公式AppStreamリポジトリの Python 3.14 を用いた移行実行コンテナ（`Dockerfile.runner`）を追加した。
- **認証連携**：
  - LDAPおよびActive Directoryとコンテナを連携させるための環境変数サンプルファイル（`ldap.env.sample`）を追加した（標準の `netbox:latest` イメージでそのまま起動できるよう、`docker-compose.yml` 内の `REMOTE_AUTH_BACKEND` はデフォルトでコメントアウト）。
- **ドキュメント**：
  - 日本語および英語の環境構築および移行手順書（[INSTALL.ja.md](INSTALL.ja.md)、[INSTALL.md](INSTALL.md)）を追加した。
  - 日本語および英語のSolaris階層型仮想化に関するモデリング設計書（[README_specific_solaris.ja.md](README_specific_solaris.ja.md)、[README_specific_solaris.md](README_specific_solaris.md)）を追加した。

### 変更（Changed）
- **NetBox 4.x API仕様に合わせたフィールドとクエリの更新**：
  - デバイスロール（Device Role）の指定フィールドを旧仕様の `device.device_role` から `device.role` へ変更した。
  - タグ（Tag）の指定形式を従来のスラッグ（Slug）文字列配列から数値ID配列（`[tag.id, ...]`）へ置き換えた。
  - クラスタ（Cluster）の検索条件を従来の `slug` から `name` フィルタへ変更した。
- **設定ファイルと初期化スクリプトの更新**：
  - `conf.sample` のデフォルトデータベース名（`DB_NAME`）を `docker-compose.yml` の設定に合わせて `racktables_db` に変更し、スラッグおよびロールのマッピング設定セクションを追加した。
  - 検証時のデータ削除を高速化するため、`clean_netbox.py` にPostgreSQLの `TRUNCATE TABLE ... RESTART IDENTITY CASCADE` による直接削除とRedisのキャッシュ破棄を実装し、設定読み込みを `configparser` へ移行した。

## [1.0.0]

- オリジナルの [goebelmeier/racktables2netbox](https://github.com/goebelmeier/racktables2netbox) からフォークした初期バージョン（`__version__ = 1.00`）。
