# insta-to-paprika

Convert an Instagram recipe post into a [Paprika 3](https://www.paprikaapp.com/) `.paprikarecipe` file using Claude AI.

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

Copy `.env.example` to `.env` and fill in your Anthropic API key:

```
ANTHROPIC_API_KEY=sk-ant-...
```

---

## Usage

```
python main.py <URL|SHORTCODE> [options]
```

| Argument / Option | Description |
|---|---|
| `<URL\|SHORTCODE>` | Full Instagram URL or bare shortcode (e.g. `ABC123def`) |
| `--output DIR`, `-o` | Directory to save the file (default: `~/Documents/Paprika Imports`) |
| `--open`, `-O` | Open the `.paprikarecipe` file in Paprika immediately after saving |
| `--caption TEXT`, `-c` | Use this text instead of fetching from Instagram (useful for private/blocked posts) |
| `--category NAME`, `-C` | Set a Paprika category — repeatable: `-C Dinner -C Italian` (overrides AI suggestions) |

**Examples**

```powershell
# From a full URL
python main.py https://www.instagram.com/p/ABC123def/ --open

# From a bare shortcode
python main.py ABC123def --open

# With explicit categories
python main.py ABC123def -C Dinner -C "Quick Meals"

# Private post — paste the caption manually
python main.py ABC123def --caption "Chocolate chip cookies: 2 cups flour..."
```

---

## Output

A `.paprikarecipe` file (gzipped JSON) is written to the output directory. Double-click it to import into Paprika, or use `--open` to have the tool do it automatically.

If Paprika is not yet the registered handler, right-click the file → **Open With** → **Paprika Recipe Manager**.

---

## Notes

- Works with any public Instagram post. Private posts require the `--caption` fallback.
- The `--caption` flag is also handy for testing without hitting Instagram at all.
- If Claude doesn't find a recipe in the caption the tool exits with a clear message.
