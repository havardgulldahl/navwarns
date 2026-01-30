# navwarns

Scraping, parsing, interpreting and mapping NAVWARN (navigational warning) bulletins for the Arctic.

Current data sources:

* Navigational warnings for the Barents, White and Kara seas by Rosatom
* Coastal warnings for Murmansk, Arkhangelsk 
* HYDROARC warnings by Norway and Canada 

## Overview

1. Every night, download all active HYDROARC warnings and split into separate NavWarns using simple heuristics.
[![Fetch NAVWARNs](https://github.com/havardgulldahl/navwarns/actions/workflows/scrape.yml/badge.svg)](https://github.com/havardgulldahl/navwarns/actions/workflows/scrape.yml)

2. Run an LLM on the text to classify, clarify and extract location details
[![.github/workflows/parse.yml](https://github.com/havardgulldahl/navwarns/actions/workflows/parse.yml/badge.svg)](https://github.com/havardgulldahl/navwarns/actions/workflows/parse.yml)

3. Collect all current Navwarns into a single map to show on https://havardgulldahl.github.io/navwarns


## Run it yourself

1. Create / activate a virtual environment (optional but recommended)
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Run the text parser against a file containing one or more NAVWARN messages:

```bash
python scripts/scraper.py sample.txt
```

Or pipe data via stdin:

```bash
cat sample.txt | python scripts/scraper.py
```

4. For JSON output add `--json`:

```bash
python scripts/scraper.py sample.txt --json
```

Example line-oriented (non‑JSON) output:

```
2025-08-19T23:59:00 | HYDROARC 136/25(15) | derelict vessel | 1 coords | 2 cancellations
```

The parser:

* Recognises DTG lines in the form `DDHHMMZ MON YY` only when they appear alone on a line.
* Avoids splitting on embedded cancellation references (e.g. `CANCEL THIS MSG 222359Z AUG 25`).
* Normalises message IDs (removes a trailing parenthesised suffix) when multiple messages are present in a single batch, to match current test expectations.
* Performs simple hazard classification via keyword heuristics – refine as needed.

## License

See `LICENSE`.
