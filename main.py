#!/usr/bin/env python3
"""insta-to-paprika: Convert Instagram recipe posts to Paprika 3 format."""

import argparse
import csv
import gzip
import json
import os
import re
import sys
import uuid
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    import anthropic
except ImportError:
    sys.exit("Missing dependency: pip install anthropic")

try:
    import instaloader
except ImportError:
    sys.exit("Missing dependency: pip install instaloader")

try:
    from instagrapi import Client as InstagrapiClient
except ImportError:
    InstagrapiClient = None


# ---------------------------------------------------------------------------
# Instagram fetching (single public post via instaloader)
# ---------------------------------------------------------------------------

INSTAGRAM_BASE = "https://www.instagram.com/p/"


def normalize_url(value: str) -> str:
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return INSTAGRAM_BASE + value + "/"
    if not value.startswith("http"):
        value = "https://" + value
    return value


def fetch_instagram_post(url: str) -> dict:
    match = re.search(r"instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)", url)
    if not match:
        raise ValueError(f"Could not find a post shortcode in URL: {url}")

    shortcode = match.group(1)
    L = instaloader.Instaloader(download_pictures=False, download_videos=False,
                                 download_video_thumbnails=False, quiet=True)
    try:
        post = instaloader.Post.from_shortcode(L.context, shortcode)
    except instaloader.exceptions.InstaloaderException as exc:
        raise RuntimeError(
            f"Could not load Instagram post (it may be private or Instagram is "
            f"rate-limiting): {exc}"
        ) from exc

    return {
        "caption": post.caption or "",
        "url": url,
        "author": post.owner_username,
        "thumbnail_url": post.url,
    }


# ---------------------------------------------------------------------------
# Instagram collection access via instagrapi
# ---------------------------------------------------------------------------

SESSION_PATH = Path(".instagram_session.json")


def get_instagrapi_client():
    if InstagrapiClient is None:
        sys.exit("Missing dependency: pip install instagrapi")

    username = os.getenv("INSTAGRAM_USERNAME")
    password = os.getenv("INSTAGRAM_PASSWORD")
    if not username or not password:
        sys.exit(
            "ERROR: INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD must be set in your .env file."
        )

    cl = InstagrapiClient()
    if SESSION_PATH.exists():
        cl.load_settings(SESSION_PATH)

    try:
        cl.login(username, password)
    except Exception as exc:
        sys.exit(f"ERROR: Instagram login failed: {exc}")

    cl.dump_settings(SESSION_PATH)
    return cl


def find_collection(cl, name: str):
    """Return (collection_id, collection_name) matching name (case-insensitive)."""
    collections = cl.collections()
    target = name.strip().lower()
    for c in collections:
        if c.name.strip().lower() == target:
            return c.id, c.name
    available = ", ".join(f'"{c.name}"' for c in collections)
    sys.exit(
        f'ERROR: Collection "{name}" not found.\n'
        f"Available collections: {available or '(none)'}"
    )


# ---------------------------------------------------------------------------
# AI extraction
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
You are a culinary assistant that extracts structured recipe data from \
social media posts. When given an Instagram caption, you always return \
a single JSON object and nothing else — no markdown fences, no commentary.\
"""

EXTRACTION_PROMPT = """\
Extract the recipe from the Instagram post caption below.

Return a JSON object with exactly these keys:
  name        – recipe name (string)
  servings    – serving size, e.g. "4 servings" (string, may be empty)
  prep_time   – e.g. "15 min" (string, may be empty)
  cook_time   – e.g. "30 min" (string, may be empty)
  total_time  – e.g. "45 min" (string, may be empty)
  description – one or two sentence summary (string)
  ingredients – each ingredient on its own line, no JSON array (string)
  directions  – numbered steps, one per line (string)
  notes       – tips, substitutions, storage info (string, may be empty)
  categories  – list of relevant tags, e.g. ["Dinner","Italian"] (array)

Instagram caption:
{caption}
"""


def extract_recipe(post: dict) -> dict:
    client = anthropic.Anthropic()

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": EXTRACTION_PROMPT.format(caption=post["caption"]),
            }
        ],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Claude returned invalid JSON. Raw response:\n{raw}"
        ) from exc


# ---------------------------------------------------------------------------
# Paprika format
# ---------------------------------------------------------------------------

def to_paprika(recipe: dict, source_url: str, categories: list | None = None) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "uid": str(uuid.uuid4()).upper(),
        "hash": str(uuid.uuid4()).upper(),
        "name": recipe.get("name") or "Untitled Recipe",
        "servings": recipe.get("servings", ""),
        "source": "Instagram",
        "source_url": source_url,
        "prep_time": recipe.get("prep_time", ""),
        "cook_time": recipe.get("cook_time", ""),
        "total_time": recipe.get("total_time", ""),
        "description": recipe.get("description", ""),
        "categories": categories if categories is not None else recipe.get("categories", []),
        "rating": 0,
        "difficulty": "",
        "nutritional_info": "",
        "notes": recipe.get("notes", ""),
        "ingredients": recipe.get("ingredients", ""),
        "directions": recipe.get("directions", ""),
        "photo_data": None,
        "photo_hash": None,
        "created": now,
        "modified": now,
    }


def save_paprika_recipe(data: dict, output_dir: Path, shortcode: str | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    safe = re.sub(r"[^\w\s-]", "", data["name"]).strip()
    safe = re.sub(r"\s+", "_", safe)

    if shortcode:
        filename = f"{shortcode}_{safe}.paprikarecipe"
    else:
        filename = f"{safe}.paprikarecipe"

    path = output_dir / filename
    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    with gzip.open(path, "wb") as fh:
        fh.write(payload)

    return path


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FIELDS = ["timestamp", "shortcode", "post_url", "recipe_name", "paprika_file", "status", "failure_reason"]


def log_result(log_path: Path, row: dict) -> None:
    write_header = not log_path.exists()
    with log_path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=LOG_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({k: row.get(k, "") for k in LOG_FIELDS})


# ---------------------------------------------------------------------------
# Collection import flow
# ---------------------------------------------------------------------------

def run_collection(args: argparse.Namespace) -> None:
    print("Logging into Instagram…")
    cl = get_instagrapi_client()

    print(f'Finding collection "{args.name}"…')
    collection_id, collection_name = find_collection(cl, args.name)
    print(f"  Found: {collection_name}")

    print(f"Fetching up to {args.limit} media items…")
    medias = cl.collection_medias(collection_id, args.limit)
    print(f"  Retrieved {len(medias)} item(s).")

    output_dir: Path = args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "import_log.csv"

    success = skipped = failed = 0

    for i, media in enumerate(medias, 1):
        shortcode = media.code
        post_url = INSTAGRAM_BASE + shortcode + "/"
        ts = datetime.now().isoformat(timespec="seconds")
        print(f"\n[{i}/{len(medias)}] {shortcode}")

        # Duplicate check
        if list(output_dir.glob(f"{shortcode}_*.paprikarecipe")):
            print("  Already exists — skipping.")
            log_result(log_path, {
                "timestamp": ts, "shortcode": shortcode, "post_url": post_url,
                "status": "SKIPPED",
            })
            skipped += 1
            continue

        # Caption check
        caption = (media.caption_text or "").strip()
        if not caption:
            reason = "no caption text"
            print(f"  FAILED: {reason}")
            log_result(log_path, {
                "timestamp": ts, "shortcode": shortcode, "post_url": post_url,
                "status": "FAILED", "failure_reason": reason,
            })
            failed += 1
            continue

        # Extract recipe
        try:
            recipe = extract_recipe({"caption": caption, "url": post_url})
        except Exception as exc:
            reason = str(exc)
            print(f"  FAILED (extraction): {reason[:120]}")
            log_result(log_path, {
                "timestamp": ts, "shortcode": shortcode, "post_url": post_url,
                "status": "FAILED", "failure_reason": reason,
            })
            failed += 1
            continue

        # Save file
        try:
            paprika_data = to_paprika(recipe, post_url)
            out_path = save_paprika_recipe(paprika_data, output_dir, shortcode=shortcode)
        except Exception as exc:
            reason = str(exc)
            print(f"  FAILED (save): {reason}")
            log_result(log_path, {
                "timestamp": ts, "shortcode": shortcode, "post_url": post_url,
                "recipe_name": recipe.get("name", ""),
                "status": "FAILED", "failure_reason": reason,
            })
            failed += 1
            continue

        print(f"  Saved → {out_path.name}")

        # Unsave from collection
        if args.unsave:
            try:
                cl.media_unsave(media.id)
                print("  Unsaved from collection.")
            except Exception as exc:
                print(f"  WARNING: could not unsave: {exc}")

        log_result(log_path, {
            "timestamp": ts, "shortcode": shortcode, "post_url": post_url,
            "recipe_name": recipe.get("name", ""),
            "paprika_file": str(out_path.resolve()),
            "status": "SUCCESS",
        })
        success += 1

    print(f"\nDone. {success} saved, {skipped} skipped, {failed} failed.")
    print(f"Log → {log_path}")


# ---------------------------------------------------------------------------
# Single-post flow
# ---------------------------------------------------------------------------

def run_post(args: argparse.Namespace) -> None:
    args.url = normalize_url(args.url)

    if args.caption:
        print("Using provided caption text.")
        post = {"caption": args.caption, "url": args.url, "author": ""}
    else:
        print("Fetching Instagram post…")
        try:
            post = fetch_instagram_post(args.url)
        except Exception as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            print(
                "\nTip: if the post is private or Instagram is blocking the request, "
                "copy the caption text and pass it with --caption \"...\"",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"  Author : @{post['author']}")
        cap_preview = post["caption"][:80].replace("\n", " ")
        print(f"  Caption: {cap_preview}{'…' if len(post['caption']) > 80 else ''}")

    if not post["caption"].strip():
        sys.exit("ERROR: The post has no caption text to extract a recipe from.")

    print("Extracting recipe with Claude…")
    try:
        recipe = extract_recipe(post)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  Name       : {recipe.get('name', '(unknown)')}")
    print(f"  Servings   : {recipe.get('servings', '—')}")
    print(f"  Prep/Cook  : {recipe.get('prep_time', '—')} / {recipe.get('cook_time', '—')}")
    print(f"  Ingredients: {len(recipe.get('ingredients', '').splitlines())} lines")
    print(f"  Directions : {len(recipe.get('directions', '').splitlines())} steps")

    paprika_data = to_paprika(recipe, args.url, categories=args.categories)
    output_path = save_paprika_recipe(paprika_data, args.output)
    print(f"\nSaved → {output_path}")

    if args.open:
        print("Opening in Paprika…")
        os.startfile(str(output_path))
    else:
        print("Run with --open to import directly into Paprika.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert Instagram recipe posts to Paprika 3 .paprikarecipe files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # -- post subcommand --
    p_post = sub.add_parser(
        "post",
        help="Convert a single public Instagram post.",
        description="Convert a single public Instagram post URL to a Paprika recipe file.",
        epilog="Example:\n  python main.py post https://www.instagram.com/p/SHORTCODE/ --open",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_post.add_argument("url", help="Public Instagram post URL or shortcode")
    p_post.add_argument(
        "--output", "-o", metavar="DIR", type=Path,
        default=Path.home() / "Documents/PaprikaImports",
        help="Directory to save the file (default: ~/Documents/Paprika Imports)",
    )
    p_post.add_argument(
        "--open", "-O", action="store_true",
        help="Open the file in Paprika immediately after saving",
    )
    p_post.add_argument(
        "--caption", "-c", metavar="TEXT",
        help="Use this caption instead of fetching from Instagram",
    )
    p_post.add_argument(
        "--category", "-C", metavar="NAME", action="append", dest="categories",
        help="Set a Paprika category (repeatable). Overrides AI-suggested categories.",
    )

    # -- collection subcommand --
    p_col = sub.add_parser(
        "collection",
        help="Bulk-import all recipes from a saved Instagram collection.",
        description="Bulk-import recipes from a saved Instagram collection.",
        epilog='Example:\n  python main.py collection "Recipes" --limit 10 --unsave',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_col.add_argument("name", help="Name of the Instagram saved collection")
    p_col.add_argument(
        "--output", "-o", metavar="DIR", type=Path,
        default=Path.home() / "Documents/PaprikaImports",
        help="Directory to save files (default: ~/Documents/Paprika Imports)",
    )

    def validate_limit(value: str) -> int:
        n = int(value)
        if not 1 <= n <= 20:
            raise argparse.ArgumentTypeError("--limit must be between 1 and 20")
        return n

    p_col.add_argument(
        "--limit", "-l", metavar="N", type=validate_limit, default=5,
        help="Max items to fetch (1–20, default: 5)",
    )
    p_col.add_argument(
        "--unsave", "-u", action="store_true",
        help="Unsave from the collection after successfully exporting each recipe",
    )

    args = parser.parse_args()

    if args.command == "post":
        run_post(args)
    elif args.command == "collection":
        run_collection(args)


if __name__ == "__main__":
    main()
