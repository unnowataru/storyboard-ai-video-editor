# CHANGELOG

## 2026-11-14

### 公開
- 構成表 (JSON) から画面録画の動画を作るツールとして公開した。簡易版 (FFmpeg だけ) と完成版 (DaVinci Resolve Studio のタイムライン) の 2 通りで動画にする
- ライセンスは MIT (`LICENSE`)
- CI (GitHub Actions) は、push のたびと毎週、その時点の最新の Python・パッケージ・FFmpeg で、Resolve が要らないテスト (`tests/offline`) を流す
