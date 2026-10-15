# サブロー — あなた専用のAIエージェント

パスワードで守られた、あなただけが使える会話型AIエージェントです。
Anthropic の Claude（Claude Opus 5.5）を頭脳に使っています。

## できること

- **会話**: 雑談・相談・調べもの・文章作成・事業計画づくりなど
- **記憶**: あなたの好み・目標・決めたことを自分で覚え、次の会話でも活かします
  （画面の「🧠 記憶」で一覧・削除できます）
- **Web検索**: 最新情報が必要なときは自分でネットを調べます
- **会話の保存**: 過去の会話は左のリストからいつでも再開できます
- **スマホ対応**: スマホのブラウザからもそのまま使えます

## はじめかた

### 1. API キーを用意する

[Anthropic Console](https://console.anthropic.com/) でアカウントを作り、API キーを発行します（従量課金）。

### 2. インストール

Python 3.10 以上が必要です。

```bash
git clone <このリポジトリ>
cd Saburo
python3 -m venv .venv
source .venv/bin/activate        # Windows は .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 設定

```bash
cp .env.example .env
```

`.env` を開いて、次を書き換えます。

| 項目 | 内容 |
|---|---|
| `ANTHROPIC_API_KEY` | 手順1の API キー |
| `SABURO_PASSWORD` | ログイン用パスワード（必ず変更） |
| `SABURO_OWNER_NAME` | AI があなたを呼ぶ名前 |
| `SABURO_EFFORT` | 考える深さ。`low`（速い・安い）〜 `max`（賢い・遅い）。普段は `medium` |

### 4. 起動

**Web画面で使う**

```bash
python -m saburo.server
```

ブラウザで http://localhost:8000 を開き、パスワードでログインします。

**ターミナルで使う**

```bash
python -m saburo.cli          # 新しい会話
python -m saburo.cli --last   # 前回の続き
```

## 性格を変える

`persona.md` を編集すると、サブローの性格・話し方・役割を自由に変えられます。
`{owner}` はあなたの名前に置き換わります。

## データの保存場所

会話と記憶は `data/saburo.db`（SQLite）に保存されます。
このファイルはあなたのパソコンの中だけにあり、Git には含まれません。

## スマホや外出先から使うには

- 同じWi-Fi内のスマホから使う: `.env` に `SABURO_HOST=0.0.0.0` を追加して起動し、
  スマホで `http://<パソコンのIPアドレス>:8000` を開きます。
- 外出先から使う: クラウド（Render、Railway、Fly.io など）にデプロイし、必ず HTTPS で公開してください。
  パスワードは長く推測されにくいものにしてください。

## ファイル構成

```
saburo/
  agent.py      # 頭脳: Claude API とのやりとり、記憶ツール、Web検索
  store.py      # 会話と記憶の保存（SQLite）
  server.py     # Web サーバー（ログイン・チャット API）
  cli.py        # ターミナル版
  config.py     # 設定の読み込み
  static/index.html  # チャット画面
persona.md      # サブローの性格設定
```

## 次のステップ（AI会社に向けて）

サブローは「1人のAI社員」の土台です。ここから次のように広げられます。

- **役割ごとのエージェント**: `persona.md` を複製して、営業・経理・開発などの専門エージェントを作る
- **ツールの追加**: `agent.py` の `TOOLS` に、カレンダー・メール・社内データなどを操作する道具を足す
- **チーム化**: 社長エージェントが各専門エージェントに仕事を振る仕組みを作る
