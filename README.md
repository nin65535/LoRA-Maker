# LoRA Maker

LoRA制作で繰り返し発生するファイル操作や外部ツールの実行をまとめ、人が素材の選別や品質確認に集中できるようにするローカル工程管理アプリです。

ComfyUI、BandiView、ffmpeg、sd-scriptsを置き換えるものではありません。各ツールとプロジェクトデータをつなぎ、動画生成からフレーム抽出、画像選別・拡大、タグ付け、LoRA学習までの流れを管理する「司令塔」を目指しています。

> [!IMPORTANT]
> 現在はフェーズ2（設定・プロジェクト・データセット管理）まで完了しています。

## 目指すワークフロー

1. 素材画像からComfyUIで動画を生成する
2. ffmpegで動画の全フレームを抽出する
3. BandiViewで使用する画像を人が選別する
4. ComfyUIで選別画像を拡大する
5. Taggerでキャプションを生成し、タグを整理する
6. sd-scriptsでLoRAを学習する
7. 完成したLoRAを保管し、利用先へコピーする

入力ファイルは直接変更せず、品質に関わる判断は人に残すことを基本方針としています。

## 現在の進捗

- フェーズ0：完了
  - ComfyUIの動画生成・画像拡大・タグ付けAPIを実機確認
  - ffmpegによる全フレーム抽出を確認
  - BandiViewによる元画像を保持した選別を確認
  - Stability Matrix管理下のsd-scriptsで最小学習を確認
  - ComfyUIと学習処理のGPU排他方針を確認
- フェーズ1：完了
  - FastAPI + React/TypeScriptの開発基盤を構築
  - ヘルスチェックAPI、疎通画面、共通エラー応答、リクエストログを実装
  - バックエンド自動テスト、TypeScript型検査、Vite本番ビルドを確認
- フェーズ2：完了
  - Pydanticによる設定検証と原子的なJSON保存を実装
  - プロジェクトの作成・ネイティブファイル選択・前回状態の復元を実装
  - データセット管理、標準フォルダ作成、不一致警告を実装
  - React画面からプロジェクトとデータセットを操作可能

全体の進捗は[実装ロードマップ](documents/企画書/06_実装ロードマップ.md)、各フェーズの実測結果は`documents/実装ログ/`を参照してください。

## 想定構成

```text
React / TypeScript
        ↓ HTTP / SSE
FastAPI
  ├─ プロジェクト・設定管理
  ├─ バックグラウンドジョブ管理
  ├─ ComfyUI API連携
  ├─ ffmpeg / BandiView連携
  └─ sd-scripts CLI実行
```

主な固定バージョンは次のとおりです。

| 対象 | バージョン |
| --- | ---: |
| Python | 3.14.7 |
| Node.js | 26.9.0 |
| npm | 11.19.1 |
| FastAPI | 0.141.1 |
| Pydantic | 2.13.5 |
| React | 19.3.0 |
| Vite | 8.3.0 |

## フェーズ0の検証環境を用意する

現時点ではWindows環境を対象としています。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

別途、次の外部ツールが必要です。

- Stability Matrixで管理されたComfyUIおよびkohya_ss / sd-scripts
- ffmpeg
- BandiView
- Node.js（`.node-version`記載のバージョン）とnpm

環境と外部ツールの配置、ComfyUI APIへの疎通を読み取り専用で確認できます。

```powershell
.\scripts\phase0-preflight.ps1
```

標準と異なる場所に導入している場合は、引数でパスやURLを指定します。

```powershell
.\scripts\phase0-preflight.ps1 `
  -StabilityMatrixRoot 'D:\StabilityMatrix' `
  -ComfyUiApiUrl 'http://127.0.0.1:8188' `
  -BandiViewPath 'C:\Program Files\BandiView\BandiView.exe'
```

このスクリプトは外部アプリの起動、ファイル削除、ワークフロー投入、学習開始を行いません。

## リポジトリ構成

| パス | 内容 |
| --- | --- |
| `documents/` | 仕様、設計、ロードマップ、実装ログ |
| `comfyui_workflows/` | 実機検証済みのComfyUI APIワークフロー |
| `scripts/` | 環境確認用スクリプト |
| `backend/` | FastAPIバックエンド |
| `frontend/` | Vite + React/TypeScriptフロントエンド |
| `tests/` | バックエンド自動テスト |
| `phase0/` | フェーズ0の検証用設定 |

`phase0-output/`と`workspace/`はローカルでの検証・作業用であり、Git管理の対象外です。

## 開発サーバーを起動する

初回は固定された依存関係を導入します。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Set-Location frontend
npm ci
Set-Location ..
```

別々のPowerShellでバックエンドとフロントエンドを起動します。

```powershell
.\scripts\dev-backend.ps1
```

```powershell
.\scripts\dev-frontend.ps1
```

ブラウザで`http://127.0.0.1:5173`を開くと、FastAPIへの接続状態が表示されます。

自動テストとフロントエンド検証は次のコマンドで実行できます。

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Set-Location frontend
npm run test
npm run build
```

## ドキュメント

仕様の入口は[ドキュメント案内](documents/README.md)です。特に以下を正本として扱います。

- [LoRA作成工程仕様](documents/企画書/01_LoRA作成工程仕様.md)
- [プロジェクト・データ仕様](documents/企画書/02_プロジェクト・データ仕様.md)
- [設定ファイル仕様](documents/企画書/03_設定ファイル仕様.md)
- [アプリ機能仕様](documents/企画書/04_アプリ機能仕様.md)
- [システム構成・外部連携](documents/企画書/05_システム構成・外部連携.md)
- [実装工程詳細](documents/企画書/07_実装工程詳細.md)

実装時は、対応する仕様書との整合性を確認してから変更します。
