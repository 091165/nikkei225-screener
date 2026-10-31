[README (2).md](https://github.com/user-attachments/files/25873571/README.2.md)
# 理想乖離ダッシュボード (Ideal Deviation Dashboard)

長期投資の**買い場**を一目で判断するためのPythonツール。  
主要株価指数の25日移動平均線乖離率を統計的に算出し、-2σ / -3σ の買いゾーンを可視化する。

## 出力イメージ

### サマリーカード
各指数の現在のゾーン（BUY ZONE / NEUTRAL等）、乖離率、-2σ到達価格を一覧表示。

### 乖離率チャート（直近2年）
- 青線 = 25日MA乖離率
- オレンジ点線 = -2σライン（95%圏外 → 買い検討）
- 赤点線 = -3σライン（99%圏外 → 強い買いシグナル）
- オレンジ点 / 赤点 = -2σ / -3σ 突入ポイント

### ヒストグラム
全期間の乖離率分布。黒線が現在位置。

## 対象指数

| 指数 | ティッカー | 備考 |
|------|----------|------|
| 日経225 | ^N225 | |
| ダウ平均 | ^DJI | |
| S&P500 | ^GSPC | |
| ナスダック | ^IXIC | |
| SOX半導体 | ^SOX | |

## ゾーン判定ロジック

| ゾーン | 条件 | アクション |
|--------|------|----------|
| **STRONG BUY (-3σ)** | 乖離率 ≤ -3σ | ナンピンしてでも買い（99%圏外） |
| **BUY ZONE (-2σ)** | 乖離率 ≤ -2σ | 仕込み検討（95%圏外） |
| MILD DIP | 乖離率 < 0 | 軽い押し目 |
| NEUTRAL / UP | 乖離率 ≤ +2σ | 平常〜上昇 |
| OVERBOUGHT (+2σ) | 乖離率 ≤ +3σ | 過熱気味 |
| EXTREME HIGH (+3σ) | 乖離率 > +3σ | 極端な高値圏 |

## セットアップ

### ローカル実行

```bash
# 1. リポジトリをクローン
git clone https://github.com/YOUR_USERNAME/ideal-deviation-dashboard.git
cd ideal-deviation-dashboard

# 2. 依存パッケージをインストール
pip install -r requirements.txt

# 3. 実行
python ideal_deviation_dashboard.py
```

PNG画像3枚（summary / charts / histograms）とCSVが出力される。

### GitHub Actions（自動実行）

1. このリポジトリをGitHubにpush
2. `.github/workflows/dashboard.yml` が毎週月曜朝7時(JST)に自動実行
3. `output/` フォルダに最新のダッシュボード画像がコミットされる
4. 手動実行: Actions → Ideal Deviation Dashboard → Run workflow

### 出力先ディレクトリの変更

```bash
OUTPUT_DIR=./my_output python ideal_deviation_dashboard.py
```

## カスタマイズ

### 銘柄の追加・変更

`ideal_deviation_dashboard.py` の `INDICES` 辞書を編集：

```python
INDICES = {
    "Nikkei 225":   {"ticker": "^N225",  "label_jp": "日経225"},
    "Dow Jones":    {"ticker": "^DJI",   "label_jp": "ダウ平均"},
    # 個別株を追加する場合:
    "Toyota":       {"ticker": "7203.T", "label_jp": "トヨタ"},
    "NVIDIA":       {"ticker": "NVDA",   "label_jp": "NVIDIA"},
}
```

日本株は証券コード + `.T`（例: `7203.T`）、米国株はそのままティッカー。

### チャート表示期間の変更

```python
CHART_YEARS = 3   # 直近3年表示に変更
```

## 技術詳細

- **理想乖離**: 全日足データの25日MA乖離率を算出し、平均±2σ（95%信頼区間）を「理想乖離」とする
- **統計期間**: yfinanceで取得可能な最大期間（指数により30〜50年）
- **データソース**: Yahoo Finance (yfinance)

## ライセンス

個人利用限定。投資判断は自己責任で行ってください。

---

## 長期（暴落点）タブ と 保有管理

既存の理想乖離ダッシュボード（日本株・米国株）はそのままに、長期投資用のタブを2つ追加しています。

### 📉 長期（暴落点） — `longterm.html`

「相場全体や個別株が大きく売られた日（暴落点）に、上昇トレンドが続いていて業績も伸びている銘柄を買う」という考え方を、機械的に判定するページです。

- **暴落点**：日経平均（米国株は S&P500・ダウ）の25日乖離率がしきい値に、前回から3か月以上あけて初めて届いた日（指数要因）。または銘柄自身の乖離率が理想乖離の -2σ 相当に、同じ条件で届いた日（個別要因）。
- **判定**：
  - A 暴落点から数日以内か
  - B 6か月以内に上場来高値を更新しているか
  - C 暴落点ごとの安値が切り上がっているか
  - D 売上高・純利益が毎期伸びているか
  - これらから「長期買い（強）／長期買い／監視（暴落待ち）／見送り」を出します。判定の目印をタップすると理由が表示されます。
- **地合いパネル**：指数の乖離率と暴落度、最後の暴落点、次に判定がリセットされる日、下落銘柄の割合、月別アノマリー、過去の暴落点の一覧（メモは `config/crash_notes.yaml` で編集できます）。
- **詳細画面**：銘柄の行をタップすると、チャート（暴落点と安値の推移）、乖離率の分布、条件A〜Dの根拠、業績の推移、月別の騰落率、同じセクターとの比較が出ます。

ルールは機械的な目安です。最終判断はチャートを自分の目で確認してください。

### 💼 保有管理 — `holdings.html`

保有銘柄（コード・買付日・買値・株数）を入力すると、次のような提案が出ます。

- 買付から3か月は様子見
- その後は含み損益に応じた逆指値（上げるだけで下げない）
- 売りを検討するアラート（高値を更新しなくなった、安値を切り下げた、業績が悪化した、+100%、過熱）

入力したデータは**ブラウザの中（localStorage）にだけ**保存され、サイトやリポジトリには送られません。機種変更などに備えて、「JSONを書き出し」でバックアップしてください。

### 設定・検証

- しきい値はすべて `config/longterm.yaml` で変更できます。
- 毎営業日の `Stock Deviation Screener` ワークフローで `scripts/run_longterm.py` が動き、`output/docs/data/longterm/` に JSON を出力します。この JSON は GitHub Pages にだけ公開し、main ブランチにはコミットしません。株価のキャッシュは Actions のキャッシュに置きます。
- バックテスト：`python scripts/backtest_longterm.py`（結果は `docs/backtest/`）。スイング用の別戦略は `python scripts/backtest_swing.py`。
- テスト：`python -m pytest`（実データのテストは `RUN_NETWORK_TESTS=1` のときだけ実行）、`node tests/js/holdings_logic.test.js`。
- 現行ロジックの説明：`docs/CURRENT_LOGIC.md`。
