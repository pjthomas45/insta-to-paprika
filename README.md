# insta-to-paprika

Convert Instagram recipe posts into [Paprika 3](https://www.paprikaapp.com/) `.paprikarecipe` files using Claude AI.

Two modes:
- **`post`** — convert a single public post by URL or shortcode
- **`collection`** — bulk-import all recipes from a saved Instagram collection

---

## Setup

**1. Create and activate a virtual environment**

```powershell
python -m venv .venv
.venv\Scripts\activate
```

**2. Install dependencies**

```powershell
pip install -r requirements.txt
```

**3. Configure credentials**

Copy `.env.example` to `.env` and fill in your keys:

```
ANTHROPIC_API_KEY=sk-ant-...
INSTAGRAM_USERNAME=your-username
INSTAGRAM_PASSWORD=your-password
```

> `INSTAGRAM_USERNAME` / `INSTAGRAM_PASSWORD` are only required for the `collection` command.

---

## Usage

### Single post

```
python main.py post <URL|SHORTCODE> [options]
```

| Option | Description |
|---|---|
| `--output DIR`, `-o` | Save directory (default: `~/Documents/PaprikaImports`) |
| `--open`, `-O` | Open the file in Paprika immediately after saving |
| `--caption TEXT`, `-c` | Use this text instead of fetching from Instagram (useful for private/blocked posts) |
| `--category NAME`, `-C` | Set a Paprika category — repeatable: `-C Dinner -C Italian` (overrides AI suggestions) |

```powershell
# From a full URL
python main.py post https://www.instagram.com/p/ABC123def/ --open

# From a bare shortcode
python main.py post ABC123def --open

# With explicit categories
python main.py post ABC123def -C Dinner -C "Quick Meals"

# Private post — paste the caption manually
python main.py post ABC123def --caption "Chocolate chip cookies: 2 cups flour..."
```

---

### Collection bulk import

```
python main.py collection <NAME> [options]
```

| Option | Description |
|---|---|
| `--output DIR`, `-o` | Save directory (default: `~/Documents/PaprikaImports`) |
| `--limit N`, `-l` | Number of posts to fetch, 1–20 (default: 5) |
| `--unsave`, `-u` | Remove each post from the collection after it is successfully exported |

```powershell
# Import 10 recipes from your "Recipes" collection
python main.py collection "Recipes" --limit 10

# Import and remove from collection as you go
python main.py collection "Recipes" --limit 20 --unsave

# Save to a custom folder
python main.py collection "Weeknight Dinners" --limit 5 --output C:\Users\me\Desktop\Recipes
```

**Duplicate protection** — if a `.paprikarecipe` file for a shortcode already exists in the output folder, that item is skipped automatically.

---

## Output

Each file is named `{shortcode}_{RecipeName}.paprikarecipe` — a gzipped JSON file that Paprika imports when double-clicked (or via **File → Import**).

An `import_log.csv` is written alongside the recipe files after every collection run, recording:

| Column | Description |
|---|---|
| `timestamp` | When the item was processed |
| `shortcode` | Instagram media shortcode |
| `post_url` | Full Instagram URL |
| `recipe_name` | Name extracted by Claude |
| `paprika_file` | Absolute path of the saved file |
| `status` | `SUCCESS`, `SKIPPED`, or `FAILED` |
| `failure_reason` | Error detail when status is `FAILED` |

---

## Notes

- The first `collection` run saves a login session to `.instagram_session.json` so subsequent runs don't re-prompt Instagram's security checks. This file contains session credentials — it is gitignored.
- `post` mode works with any public post. Private posts require the `--caption` fallback.
- If Claude doesn't find a recipe in the caption the item is logged as `FAILED` and the next item continues.
