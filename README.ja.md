# racktables2netbox

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
![Powered by Gemini](https://img.shields.io/badge/Powered%20by-Gemini-8E75B2?style=flat-square&logo=googlegemini&logoColor=white)
![Built with Antigravity](https://img.shields.io/badge/Built%20with-Antigravity-4285F4?style=flat-square)

English: [README.md](README.md)

`racktables2netbox.py` は、[RackTables](https://github.com/racktables/racktables) で管理されているデータセンター資産を [NetBox](https://github.com/netbox-community/netbox) へ移行するスクリプトである。
物理インフラストラクチャ、IPアドレス管理（IPAM）、ネットワーク接続、仮想化クラスタ、およびサーバー構成の移行を自動化する（NetBox 4.x で動作確認済み）。

---

## 主な機能

- **DCIM（Data Center Infrastructure Management）の移行**：
  - サイト（Site）、ロケーション（Location）、ラック（Rack）、製造元（Manufacturer）、デバイスタイプ（Device Type）、およびデバイス（Device）を移行する。
  - ラック内のマウント位置から、前面（Front）と背面（Rear）の装着向きを自動判定する。
  - 0UサイズのPDUおよびコンソールサーバーの配置を処理する。
  - NetBox Device Type Library の定義データと連携する。
- **インターフェースとケーブル配線の移行**：
  - ポート（Port）、インターフェース（Interface）、およびケーブル（Cable）の接続情報を移行する。
  - NetBox 4.2以降で導入された独立オブジェクトとしてのMACアドレス（`MACAddress`）モデルに対応する。
  - DAC、光ファイバー、およびEthernetのコネクタ種別をNetBoxの型へ変換する。
- **IPAM（IP Address Management）の移行**：
  - VLANおよびVLANグループ（VLAN Group）を移行する。
  - 最長一致検索（Longest Prefix Match）を用いてCIDRを動的に算出し、プレフィックス（Prefix）を作成する。
  - IPアドレス（IP Address）をインターフェースへ割り当て、プライマリIP（Primary IP）を設定する。
- **仮想化と階層構造のモデリング**：
  - クラスタ（Cluster）およびクラスタタイプ（Cluster Type）を自動で対応づける。
  - 仮想マシン（Virtual Machine）と親の物理デバイスを紐付ける。
  - Solaris LDOMやゾーンのような階層型仮想化のモデリングに対応する（設計の詳細は [README_specific_solaris.ja.md](README_specific_solaris.ja.md) を参照）。
- **事前検証と初期化**：
  - データベースを変更せずに移行内容を検証するドライラン（Dry-run）モード（`--dry-run`）を備える。
  - NetBoxの登録データを一括削除する初期化スクリプト（`clean_netbox.py`）を同梱する。
  - `podman compose` で動作するコンテナ実行環境を提供する。

---

## クイックスタート

環境構築および移行の具体的な手順は [INSTALL.ja.md](INSTALL.ja.md) で解説する。

```bash
# 1. リポジトリのクローン
git clone https://github.com/ogm-not-kgm/racktables2netbox.git
cd racktables2netbox

# 2. 認証情報とマッピングの設定
cp conf.sample conf
vim conf

# 3. コンテナの起動
podman compose up -d

# 4. ドライランによる事前検証
podman exec ol10-runner python /app/racktables2netbox.py --dry-run

# 5. 本番移行の実行
podman exec ol10-runner python /app/racktables2netbox.py
```

---

## ドキュメント

- **[INSTALL.ja.md](INSTALL.ja.md)**：環境構築、設定ファイルの編集、および移行実行の手順書。
- **[README_specific_solaris.ja.md](README_specific_solaris.ja.md)**：Solaris環境における階層型仮想化（LDOMおよびゾーン）をNetBox上で表現するためのモデリング設計書。
- **[CHANGELOG.ja.md](CHANGELOG.ja.md)**：バージョンごとの変更履歴。

---

## コントリビューション

不具合の報告や機能の提案は [Issues](https://github.com/ogm-not-kgm/racktables2netbox/issues) で受け付けている。
プルリクエスト（Pull Request）を送る際は、以下の手順に従う。

1. 本リポジトリをフォーク（Fork）する。
2. フィーチャーブランチ（Feature Branch）を作成する（`git checkout -b feature/AmazingFeature`）。
3. 変更をコミットする（`git commit -m 'Add some AmazingFeature'`）。
4. ブランチへプッシュする（`git push origin feature/AmazingFeature`）。
5. プルリクエストを作成する。

---

## 謝辞とライセンス

- 本スクリプトは [goebelmeier/racktables2netbox](https://github.com/goebelmeier/racktables2netbox) および [Device42 Migration Script](https://github.com/device42/Racktables-to-Device42-Migration) を起点として開発された。
- Copyright (c) 2018 Timo Reimann
- Copyright (c) 2026 Hironori OGUMA
- ライセンスは [MIT License](LICENSE.md) とする。
