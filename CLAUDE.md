# このリポジトリで作業する Claude へ

## 言語
- ユーザーへの返答・報告・質問は、**常に日本語**で書く（途中で英語に切り替えない）。
- PR のタイトル・本文も日本語で書く。コミットメッセージは英語でもよい。

## プロジェクト
- 概要は `PROJECT_BRIEF.md`、現行ロジックの説明は `docs/CURRENT_LOGIC.md` を参照。
- 既存の買いスコア・4つのシグナルの定義は、明示的な依頼がない限り変えない。新しい判定は長期（暴落点）タブ側に追加する。
- しきい値は `config/longterm.yaml` にまとめる。
- テスト: `python -m pytest`（実データのテストは `RUN_NETWORK_TESTS=1` のときだけ）、`node tests/js/holdings_logic.test.js`。
