# LoRA Maker

LoRA制作で繰り返し発生するファイル操作や外部ツールの実行をまとめ、人が素材の選別や品質確認に集中できるようにするローカル工程管理アプリです。

ComfyUI、BandiView、ffmpeg、sd-scriptsを置き換えるものではありません。各ツールとプロジェクトデータをつなぎ、動画生成からフレーム抽出、画像選別・拡大、タグ付け、LoRA学習までの流れを管理する「司令塔」を目指しています。

> [!IMPORTANT]
> 現在はフェーズ9（LoRA学習・models配置）まで完了し、学習素材の検証からsd-scripts実行、成果物配置まで行えます。

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
- フェーズ3：完了
  - 7工程のファイル走査、集計、不一致警告と読み取り専用一覧を実装
  - 再読込後のプロジェクト復元と非同期走査を実装
- フェーズ4：完了
  - SQLiteへ永続化する同時実行数1のジョブキューを実装
  - ジョブ状態・ログAPI、SSE通知、再読込後の状態復元を実装
  - 待機中ジョブのキャンセル、異常終了ジョブの失敗判定、稼働中のプロジェクト切替禁止を実装
- フェーズ5：完了（最初の実用MVP）
  - `06_LoRA学習素材`の既存画像を対象としたComfyUI Taggerジョブを実装
  - 未加工キャプションの保存、タグ出現数・出現率の集計、画像との不一致表示を実装
  - 削除対象タグの選択とプロジェクト設定への保存、識別タグの削除防止を実装
  - 複数の識別タグ追加と選択タグ削除を行い、加工済みキャプションを学習素材へ配置可能
  - 再配置時も未加工キャプションを正本として再生成し、元画像と未加工キャプションを保持
- フェーズ6：完了
  - `02_動画/<dataset.key>`の動画別抽出状態とフレーム数を表示
  - ffmpegによる全フレーム抽出を個別実行または未処理動画の一括実行としてジョブ化
- フェーズ7：完了
  - キャプチャフォルダ単位でBandiViewを起動し、共用保存先と選別対象を対応付け
  - ComfyUIで選別画像を拡大し、正本と学習素材へ照合コピー
  - 既存出力の上書き防止と、失敗時の選別画像保全を実装
  - 一時フォルダで抽出を完了してから`03_動画キャプチャ/<dataset.key>/<動画名>`へ確定し、既存出力を保護
  - 空フォルダの再処理、抽出済み動画のスキップ、動画ごとの失敗・再実行に対応
- フェーズ8：完了
  - 素材画像とプリセットからComfyUI動画生成ジョブを登録し、直列実行して`02_動画`へ保全
- フェーズ9：完了
  - `external_configs/training_configs/`直下のTOMLを画面から選択してsd-scriptsを実行
  - 学習素材の整合性検証、`07_LoRA`への成果物保存、modelsフォルダへの安全な配置・削除を実装

全体の進捗は[実装ロードマップ](documents/企画書/06_実装ロードマップ.md)、各フェーズの実測結果は`documents/実装ログ/`を参照してください。

## 想定構成

```text
React / TypeScript
        ↓ HTTP / SSE
FastAPI
  ├─ プロジェクト・設定管理
  ├─ バックグラウンドジョブ管理
  ├─ タグ集計・キャプション配置
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
| `app_master.json` | 起動時に検証・キャッシュするアプリ共通マスタ |
| `external_configs/comfyui_workflows/` | 実機検証済みのComfyUI APIワークフロー |
| `external_configs/training_configs/` | 画面から選択するsd-scripts学習設定TOML |
| `scripts/` | 環境確認用スクリプト |
| `backend/` | FastAPIバックエンド |
| `frontend/` | Vite + React/TypeScriptフロントエンド |
| `tests/` | バックエンド自動テスト |

`training_configs/examples/`は検証用サンプル置き場であり、画面の選択肢には表示されません。実運用するTOMLは`training_configs/`直下へ配置します。
| `phase0/` | フェーズ0の検証用設定 |

`phase0-output/`と`workspace/`はローカルでの検証・作業用であり、Git管理の対象外です。

`app_master.json`には工程フォルダ、対応拡張子、ComfyUIワークフローの参照先とノード、動画生成プリセットなど、全プロジェクト共通の値を保存します。起動時に一度だけ読み込んで検証し、稼働中はメモリ上のキャッシュを参照します。ファイルを変更した場合はアプリを再起動してください。PC固有のパスや接続先は引き続き`%LOCALAPPDATA%\LoRAMaker\settings.json`へ保存します。

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

ブラウザで`http://127.0.0.1:5173`を開くと、プロジェクト管理、工程の進捗、ジョブ状態およびタグ付け画面を利用できます。

## タグ付けを行う

フェーズ5は前工程が未実装でも利用できます。プロジェクトを作成または開き、対象画像を次のフォルダへ配置してください。

```text
06_LoRA学習素材/<repeats>_<dataset.key>/
```

Stability MatrixからComfyUIを起動した後、`05_タグ付け`タブで次の順に操作します。

1. 対象データセットを選択して「自動タグ付け」を実行する
2. ジョブ完了後、タグの出現数・出現率を確認する
3. 不要なタグを削除対象として選択する
4. 「学習素材へ配置」を実行する

未加工キャプションは`05_タグ付け/<dataset.key>`へ保存されます。加工済みキャプションは画像と同じ`06_LoRA学習素材/<repeats>_<dataset.key>`へ配置されます。再配置では未加工キャプションから作り直すため、加工済みファイルへ変更が累積しません。

識別タグはデータセット設定の順序でキャプション先頭へ追加され、削除対象には指定できません。画像と未加工キャプションに不足または余剰がある場合は画面に表示され、キャプション不足がある状態では配置できません。

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
