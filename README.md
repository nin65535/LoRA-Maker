# LoRA Maker

LoRA制作に伴うファイル操作と外部ツールの実行をまとめた、Windows向けのローカル工程管理アプリです。

ComfyUI、BandiView、ffmpeg、sd-scriptsを連携し、動画生成、フレーム抽出、画像選別・拡大、タグ付け、LoRA学習、成果物配置までを管理します。入力ファイルは直接変更せず、素材の選別や品質確認は人が行う設計です。

> [!IMPORTANT]
> フェーズ0〜10の実装と総合試験が完了し、日常運用を開始できる状態です。

## 必要な環境

- Python 3.14.7
- Node.js 26.9.0 / npm 11.19.1
- Stability Matrixで管理されたComfyUIおよびkohya_ss / sd-scripts
- ffmpeg
- BandiView

外部ツールの配置と接続は、読み取り専用の事前確認スクリプトで検証できます。

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\scripts\phase0-preflight.ps1
```

標準と異なる場所に導入している場合は、スクリプトの引数でパスやURLを指定してください。詳しくは[システム構成・外部連携](documents/企画書/05_システム構成・外部連携.md)を参照してください。

## 日常運用

初回導入時とフロントエンド変更後に、配布用画面をビルドします。

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Set-Location frontend
npm ci
npm run build
Set-Location ..
```

以後は次のスクリプトで起動できます。

```powershell
.\scripts\start.ps1
```

Chromeで `http://127.0.0.1:8000/` が開きます。Chromeを閉じると、実行中のジョブを完了した後にアプリも終了します。

バックアップ対象は、各プロジェクトのルートフォルダと `%LOCALAPPDATA%\LoRAMaker` です。

## 開発

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Set-Location frontend
npm ci
Set-Location ..
```

バックエンドとフロントエンドを別々のPowerShellで起動します。

```powershell
.\scripts\dev-backend.ps1
```

```powershell
.\scripts\dev-frontend.ps1
```

開発画面は `http://127.0.0.1:5173` です。

検証コマンド：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
Set-Location frontend
npm run test
npm run build
```

## 主な構成

| パス | 内容 |
| --- | --- |
| `backend/` | FastAPIバックエンド |
| `frontend/` | React / TypeScriptフロントエンド |
| `external_configs/` | ComfyUIワークフローと学習設定 |
| `scripts/` | 起動・環境確認スクリプト |
| `tests/` | バックエンド自動テスト |
| `documents/` | 仕様、ロードマップ、実装ログ |

## ドキュメント

- [ドキュメント案内](documents/README.md)
- [仕様書一覧](documents/企画書/00_仕様書一覧.md)
- [実装ロードマップ](documents/企画書/06_実装ロードマップ.md)
- [実装工程詳細](documents/企画書/07_実装工程詳細.md)

機能の仕様、保存規則、外部連携、各フェーズの実測結果は `documents/` を正本として参照してください。
