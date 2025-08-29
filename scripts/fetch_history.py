# Get all history files from the server

from datetime import datetime
import time
import re
from urllib import response
import pathlib
import requests

import scraper

HISTORY_URL = "https://msi.nga.mil/api/publications/broadcast-warn?navArea={navArea}&status=all&msgYear={msgYear}&output=xml"


def main():
    # loop through all years from 2010 till last year, and navArea from A to E
    for year in range(2010, datetime.now().year):
        for area in ["A", "B", "C", "D", "E"]:
            url = HISTORY_URL.format(navArea=area, msgYear=year)
            print(url)

            output_dir = pathlib.Path(f"history/{year}/{area}")
            navwarns = scraper.run_scrape(
                url=url, store_xml=True, output_dir=output_dir
            )
            if navwarns > 0:
                print(f"Stored {navwarns} messages in {output_dir}")
            else:
                print(f"No messages found for {url}")

            time.sleep(1)  # Be polite and avoid overwhelming the server


if __name__ == "__main__":
    main()
