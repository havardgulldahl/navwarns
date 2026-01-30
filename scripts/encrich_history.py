"""Enrich historic NavWarn Feature JSON files.

For each JSON file at history/<year>/<area>/<id>.json containing either:
  * a single GeoJSON Feature object, or
  * an object with a top-level "feature" key holding the Feature

This script will:
  1. Read the body text (properties.body OR properties.text)
  2. Use classification.prompt.yml to classify and derive a short title
  3. Use geometry.prompt.yml to extract a geometry if missing
  4. Write updated file back in-place (unless --dry-run)

Added / updated properties:
  properties.title
  properties.category
  geometry (if previously absent or empty)
  properties.last_enriched (UTC ISO timestamp)

Environment variables:
  OPENAI_API_KEY  (required for API calls)
  OPENAI_BASE_URL (optional, default https://api.openai.com/v1)

CLI usage:
  python -m scripts.encrich_history --years 2019 2020 --limit 50
  python -m scripts.encrich_history --all-years --dry-run

Exit codes:
  0 success
  2 missing API key (if enrichment was required but not possible)

Notes:
  * Rate limiting / retries: simple exponential backoff implemented.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None

import dotenv
import openai

dotenv.load_dotenv()


REPO_ROOT = Path(__file__).resolve().parent.parent
HISTORY_DIR = REPO_ROOT / "history"
CLASS_PROMPT_PATH = REPO_ROOT / "classification.prompt.yml"
GEOM_PROMPT_PATH = REPO_ROOT / "geometry.prompt.yml"


def log(msg: str) -> None:
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}")


def load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("pyyaml not installed; add pyyaml to requirements.txt")
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    log(f"Loaded prompt: {path.name}")
    return data


def build_messages(
    prompt_cfg: Dict[str, Any], navwarn_text: str
) -> Tuple[List[Dict[str, str]], Optional[str], Dict[str, Any]]:
    msgs_cfg = prompt_cfg.get("messages", [])
    model = "teksthjelperGPT4"  # prompt_cfg.get("model")
    schema_raw = prompt_cfg.get("jsonSchema")
    # Simple {{navwarn}} substitution
    messages: List[Dict[str, str]] = []
    for m in msgs_cfg:
        content = m.get("content", "").replace("{{navwarn}}", navwarn_text)
        messages.append({"role": m.get("role", "user"), "content": content})
    json_schema: Dict[str, Any] = {}
    if schema_raw:
        try:
            json_schema = json.loads(schema_raw)
        except json.JSONDecodeError:
            pass
    return messages, model, json_schema


def openai_client() -> Any:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY environment variable is required")
    base_url = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
    log(f"Initializing OpenAI client (base={base_url})")
    return openai.AzureOpenAI(
        api_key=api_key,
        api_version=os.getenv("OPENAI_API_VERSION"),
        azure_endpoint=base_url,
    )


def call_model(
    client: Any,
    model: str,
    messages: List[Dict[str, str]],
    schema: Dict[str, Any],
    max_retries: int = 5,
) -> Optional[Dict[str, Any]]:
    delay = 2
    for attempt in range(1, max_retries + 1):
        try:
            log(f"Model call attempt {attempt}/{max_retries} model={model}")
            parsed: Optional[Dict[str, Any]] = None
            # Prefer new Responses API
            if hasattr(client, "responses"):
                used_schema = False
                try:
                    resp = client.responses.create(
                        model=model,
                        input=messages,
                        response_format={
                            "type": "json_schema",
                            "json_schema": schema.get("schema", schema),
                        },
                    )
                    used_schema = True
                except TypeError:
                    # response_format not supported in this client version
                    log(
                        "Responses.create lacks response_format support; retrying without it"
                    )
                    fallback_msgs = messages + [
                        {"role": "user", "content": "Return ONLY JSON, no prose."}
                    ]
                    resp = client.responses.create(model=model, input=fallback_msgs)
                # Attempt to parse structured output
                if getattr(resp, "output", None):
                    for item in resp.output:
                        for c in getattr(item, "content", []) or []:
                            text_val = getattr(c, "text", None)
                            if not text_val:
                                continue
                            try:
                                parsed = json.loads(text_val)
                                log(
                                    "Model call succeeded (responses API)"
                                    + (" with schema" if used_schema else " (fallback)")
                                )
                                return parsed
                            except Exception:
                                continue
                text_out = getattr(resp, "output_text", None)
                if text_out:
                    try:
                        parsed = json.loads(text_out)
                        log(
                            "Model call succeeded (responses API output_text)"
                            + (" with schema" if used_schema else " (fallback)")
                        )
                        return parsed
                    except Exception:
                        pass
            # Legacy chat.completions fallback
            elif hasattr(client, "chat"):
                chat = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=0,
                )
                content = chat.choices[0].message.content  # type: ignore
                try:
                    parsed = json.loads(content)
                    log("Model call succeeded (chat.completions)")
                    return parsed
                except Exception:
                    pass
            else:
                log("Client does not support responses or chat APIs")
                return None
            raise RuntimeError("No parsable JSON returned")
        except Exception as e:  # pragma: no cover
            if attempt == max_retries:
                print(
                    f"ERROR: model call failed after {attempt} attempts: {e}",
                    file=sys.stderr,
                )
                return None
            log(f"Error: {e}; retrying in {delay}s")
            time.sleep(delay)
            delay *= 2
    return None


def enrich_feature(
    feat: Dict[str, Any],
    clf_prompt: Dict[str, Any],
    geom_prompt: Dict[str, Any],
    client: Any,
    dry_run: bool = False,
) -> bool:
    """Enrich in-place. Return True if modified."""
    modified = False
    props = feat.setdefault("properties", {})
    body = props.get("body") or props.get("text") or ""
    if not body.strip():
        log("Skipping feature with empty body")
        return False

    # Classification / title
    if ("title" not in props or not props.get("category")) and client:
        log("Requesting classification/title...")
        msgs, model, schema = build_messages(clf_prompt, body)
        if model:
            result = call_model(client, model, msgs, schema) or {}
            if isinstance(result, dict):
                if result.get("title") and not props.get("title"):
                    props["title"] = result["title"].strip()
                    log(f"  -> title set: {props['title']}")
                    modified = True
                if result.get("category") and not props.get("category"):
                    props["category"] = result["category"].strip()
                    log(f"  -> category set: {props['category']}")
                    modified = True

    # Geometry
    if (not feat.get("geometry") or not feat["geometry"].get("coordinates")) and client:
        log("Requesting geometry extraction...")
        msgs, model, schema = build_messages(geom_prompt, body)
        if model:
            result = call_model(client, model, msgs, schema) or {}
            if (
                isinstance(result, dict)
                and result.get("type")
                and result.get("coordinates")
            ):
                feat["geometry"] = {
                    "type": result["type"],
                    "coordinates": result["coordinates"],
                }
                log(f"  -> geometry set: {result['type']}")
                modified = True

    if modified:
        props["last_enriched"] = datetime.now(timezone.utc).isoformat()
        log("Feature enriched")
    return modified


def iter_feature_files(years: List[int]) -> List[Path]:
    files: List[Path] = []
    log(f"Scanning years: {years}")
    for y in years:
        year_dir = HISTORY_DIR / str(y)
        if not year_dir.is_dir():
            log(f"  (skip missing year dir {year_dir})")
            continue
        for area_dir in year_dir.iterdir():
            if not area_dir.is_dir():
                continue
            for f in area_dir.glob("*.json"):
                files.append(f)
    log(f"Discovered {len(files)} feature file(s)")
    return files[0:3]


def load_feature(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("type") == "Feature":
            return data
        if "feature" in data and isinstance(data["feature"], dict):
            return data["feature"]
    except Exception as e:  # pragma: no cover
        print(f"WARN: failed to parse {path}: {e}", file=sys.stderr)
    return None


def save_feature(path: Path, feat: Dict[str, Any], dry_run: bool):
    if dry_run:
        return
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(feat, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Enrich historic NavWarn feature JSON files"
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--years", nargs="*", type=int, help="Specific years to process")
    g.add_argument(
        "--all-years", action="store_true", help="Process all year directories present"
    )
    p.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of files processed (0 = no limit)",
    )
    p.add_argument("--dry-run", action="store_true", help="Do not write changes")
    return p.parse_args(argv)


def main(argv: List[str]) -> int:
    args = parse_args(argv)
    if args.all_years:
        years = sorted(
            [
                int(p.name)
                for p in HISTORY_DIR.iterdir()
                if p.is_dir() and p.name.isdigit()
            ]
        )
    else:
        years = args.years or []
    if not years:
        print("No years to process", file=sys.stderr)
        return 0

    clf_prompt = load_yaml(CLASS_PROMPT_PATH)
    geom_prompt = load_yaml(GEOM_PROMPT_PATH)
    client = openai_client()

    files = iter_feature_files(years)
    processed = 0
    modified_count = 0
    for path in files:
        log(f"Processing file: {path.relative_to(REPO_ROOT)}")
        feat = load_feature(path)
        if not feat:
            log("  -> could not load feature (skipped)")
            continue
        before = json.dumps(feat, sort_keys=True)
        changed = enrich_feature(
            feat, clf_prompt, geom_prompt, client, dry_run=args.dry_run
        )
        after = json.dumps(feat, sort_keys=True)
        if changed and before != after:
            save_feature(path, feat, dry_run=args.dry_run)
            modified_count += 1
            log("  -> saved changes" + (" (dry-run)" if args.dry_run else ""))
        else:
            log("  -> no change")
        processed += 1
        if args.limit and processed >= args.limit:
            log(f"Limit {args.limit} reached; stopping early")
            break

    log(f"Processed {processed} file(s); modified {modified_count}.")
    if modified_count and args.dry_run:
        log("(dry-run: no files written)")
    if modified_count and client is None and not args.dry_run:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
