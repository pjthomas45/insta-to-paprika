#!/usr/bin/env python3
"""insta-to-paprika: Convert Instagram recipe posts to Paprika 3 format."""

import argparse
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
    pass  # dotenv is optional; key can also come from the environment

try:
    import anthropic
except ImportError:
    sys.exit("Missing dependency: pip install anthropic")

try:
    import instaloader
except ImportError:
    sys.exit("Missing dependency: pip install instaloader")


# ---------------------------------------------------------------------------
# Instagram fetching
# ---------------------------------------------------------------------------

INSTAGRAM_BASE = "https://www.instagram.com/p/"


def normalize_url(value: str) -> str:
    """Accept a shortcode or any instagram.com URL and return a canonical URL."""
    value = value.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return INSTAGRAM_BASE + value + "/"
    if not value.startswith("http"):
        value = "https://" + value
    return value


def fetch_instagram_post(url: str) -> dict:
    """Fetch caption and metadata from a public Instagram post URL."""
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
    """Use Claude to parse a recipe out of the Instagram caption."""
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
    # Strip accidental markdown fences just in case
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
    """Build the Paprika 3 JSON payload from extracted recipe fields."""
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


def save_paprika_recipe(data: dict, output_dir: Path) -> Path:
    """Write a .paprikarecipe file (gzipped JSON) to output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)

    safe = re.sub(r"[^\w\s-]", "", data["name"]).strip()
    safe = re.sub(r"\s+", "_", safe)
    path = output_dir / f"{safe}.paprikarecipe"

    payload = json.dumps(data, ensure_ascii=False).encode("utf-8")
    with gzip.open(path, "wb") as fh:
        fh.write(payload)

    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert an Instagram recipe post to a Paprika 3 .paprikarecipe file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Example:\n  python main.py https://www.instagram.com/p/SHORTCODE/ --open",
    )
    parser.add_argument("url", help="Public Instagram post URL")
    parser.add_argument(
        "--output", "-o",
        metavar="DIR",
        type=Path,
        default=Path.home() / "Documents/Paprika Imports",
        help="Directory to save the .paprikarecipe file (default: ~/Documents/Paprika Imports)",
    )
    parser.add_argument(
        "--open", "-O",
        action="store_true",
        help="Open the file in Paprika immediately after saving",
    )
    parser.add_argument(
        "--caption", "-c",
        metavar="TEXT",
        help="Use this caption text instead of fetching from Instagram "
             "(useful when the post is private or instaloader is blocked)",
    )
    parser.add_argument(
        "--category", "-C",
        metavar="NAME",
        action="append",
        dest="categories",
        help="Set a Paprika category (can be repeated: -C Dinner -C Italian). "
             "Overrides the AI-suggested categories.",
    )
    args = parser.parse_args()

    args.url = normalize_url(args.url)

    # Step 1 – get post content
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

    # Step 2 – extract recipe with AI
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

    # Step 3 – convert and save
    paprika_data = to_paprika(recipe, args.url, categories=args.categories)
    output_path = save_paprika_recipe(paprika_data, args.output)
    print(f"\nSaved → {output_path}")

    # Step 4 – optionally open in Paprika
    if args.open:
        print("Opening in Paprika…")
        os.startfile(str(output_path))
    else:
        print("Run with --open to import directly into Paprika.")


if __name__ == "__main__":
    main()
