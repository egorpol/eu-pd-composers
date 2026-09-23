import requests
from bs4 import BeautifulSoup
import pandas as pd
import logging
from urllib.parse import urljoin
from typing import List, Dict, Optional, Any
import re # Import re for direct testing if needed

# --- Configuration ---
BASE_URL = "https://en.wikipedia.org"
REQUEST_HEADERS = {
    'User-Agent': 'My Web Scraper Bot (Mozilla/5.0 compatible; your_email@example.com)' # Please update email
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_wiki_soup(url: str) -> Optional[BeautifulSoup]:
    try:
        response = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
        response.raise_for_status()
    except requests.Timeout:
        logging.error(f"Request timed out for URL: {url}")
        return None
    except requests.RequestException as e:
        logging.error(f"An error occurred while fetching {url}: {e}")
        return None
    except Exception as e:
        logging.error(f"An unexpected error occurred with {url}: {e}")
        return None
    return BeautifulSoup(response.text, "html.parser")

def parse_table(soup: BeautifulSoup, table_selectors: Dict[str, str]) -> Optional[List[Dict[str, Any]]]:
    table = soup.find("table", table_selectors)
    if table is None:
        logging.warning(f"Could not find the table with selectors {table_selectors} on the page.")
        return None

    header_row = table.find("tr")
    if not header_row:
        logging.warning("Table found, but no header row (tr) detected.")
        return None

    headers_from_table = [header.get_text(strip=True) for header in header_row.find_all("th")]
    logging.debug(f"Detected table headers: {headers_from_table}")
    if not headers_from_table:
        logging.warning("Header row found, but no header cells (th) detected.")
        return None

    final_headers = headers_from_table + ['URL']
    num_data_columns_expected = len(headers_from_table)

    data_rows = []
    for row_idx, row in enumerate(table.find_all("tr")[1:]):
        cells = row.find_all("td")
        if not cells:
            logging.debug(f"Skipping row {row_idx+1} as it has no <td> cells (possibly a subheader).")
            continue

        values = [cell.get_text(strip=True) for cell in cells]

        if len(values) < num_data_columns_expected:
            # logging.debug( # This can be very verbose
            #     f"Row {row_idx+1} (content starting with: '{values[0] if values else 'N/A'}') "
            #     f"has {len(values)} cells, expected {num_data_columns_expected}. Padding with empty strings."
            # )
            values.extend([''] * (num_data_columns_expected - len(values)))
        elif len(values) > num_data_columns_expected:
            # logging.debug( # This can be very verbose
            #     f"Row {row_idx+1} (content starting with: '{values[0] if values else 'N/A'}') "
            #     f"has {len(values)} cells, expected {num_data_columns_expected}. Truncating."
            # )
            values = values[:num_data_columns_expected]

        composer_link_tag = cells[0].find("a") if cells else None
        composer_url = urljoin(BASE_URL, composer_link_tag.get('href')) if composer_link_tag and composer_link_tag.get('href') else None
        
        current_row_dict = dict(zip(final_headers, values + [composer_url]))
        data_rows.append(current_row_dict)

    if not data_rows:
        logging.info("Table parsed, but no data rows found or processed.")
        return None
    return data_rows


def clean_data(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        logging.warning("clean_data received an empty or None DataFrame.")
        return pd.DataFrame() # Return empty DataFrame

    # Ensure 'Years' column exists
    if 'Years' not in df.columns:
        logging.error("'Years' column not found in DataFrame. Cannot parse dates.")
        df['Year of birth'] = pd.NA
        df['Year of death'] = pd.NA
        df['Potential EU PD Year'] = pd.NA
        return df

    logging.info("Starting data cleaning and year parsing.")
    # Log a sample of the 'Years' column to see its content
    if not df['Years'].empty:
        logging.info(f"Sample of 'Years' column before parsing (first 5 values):\n{df['Years'].head().to_string()}")
        # Log the exact representation of the first 'Years' value if available
        logging.info(f"Raw first 'Years' value: {repr(df['Years'].iloc[0]) if len(df['Years']) > 0 else 'N/A'}")
    else:
        logging.warning("'Years' column is empty.")

    # Ensure 'Years' is string type for regex operations
    years_str = df['Years'].astype(str)

    # --- Year of birth extraction ---
    # Regex: (optional "b." or "(") then (YYYY) (main capture group)
    # Allows for formats like "YYYY–...", "b. YYYY", "(YYYY)..."
    # Extracts the first 4-digit number, assumes it's birth year if multiple present before a clear death indicator
    birth_regex = r'(?:b(?:orn)?\.?\s*|\(?\s*)?(\d{4})'
    birth_year_extract = years_str.str.extract(birth_regex)
    
    # Log raw extracted birth years
    raw_birth_years_series = birth_year_extract[0] if not birth_year_extract.empty else pd.Series(dtype=str)
    # logging.debug(f"Raw extracted birth year strings (first 5): {raw_birth_years_series.head().tolist()}")
    
    df['Year of birth'] = pd.to_numeric(raw_birth_years_series, errors='coerce')
    # logging.debug(f"Numeric birth years (first 5 isNA): {df['Year of birth'].isna().head().tolist()}")
    # logging.debug(f"Numeric birth years (first 5 non-NA): {df['Year of birth'].dropna().head().tolist()}")


    # --- Year of death extraction ---
    # Regex: Looks for a dash (any type), then optional "c." or "d.", then (YYYY) (capture group 1)
    # OR: Looks for "(d. YYYY)" pattern, (YYYY) is capture group 2
    death_regex = r'[–—‐-]\s*(?:[dc]\.?\s*c?\.\s*)?(\d{4})|\(?d\.?\s*(\d{4})\)?' # Added hyphen, support for c. for death year
    death_year_extract = years_str.str.extract(death_regex)

    # Combine results from the two capture groups of the OR in regex
    # (group 0 from first part of OR, group 1 from second part of OR)
    if not death_year_extract.empty:
        # Take first non-null value from the capture groups for each row
        death_year_series = death_year_extract[0].fillna(death_year_extract[1])
    else:
        death_year_series = pd.Series(dtype=str) # Empty series if no matches
    
    # logging.debug(f"Raw extracted death year strings (first 5): {death_year_series.head().tolist()}")
    df['Year of death'] = pd.to_numeric(death_year_series, errors='coerce')
    # logging.debug(f"Numeric death years (first 5 isNA): {df['Year of death'].isna().head().tolist()}")
    # logging.debug(f"Numeric death years (first 5 non-NA): {df['Year of death'].dropna().head().tolist()}")

    # Remove citation markers (do this after year parsing if years might have them, though unlikely)
    for col in df.select_dtypes(include=['object']):
        if df[col].notna().any():
             df[col] = df[col].astype(str).str.replace(r'\[\d+\]|\[citation needed\]|\[\w+\]', '', regex=True).str.strip()
    
    # Calculate Potential EU Public Domain Year
    if 'Year of death' in df.columns and df['Year of death'].notna().any():
        df['Potential EU PD Year'] = df['Year of death'] + 71
    else:
        df['Potential EU PD Year'] = pd.NA # Use pandas NA for consistency

    logging.info(f"Sample of 'Year of birth' after parsing (first 5):\n{df['Year of birth'].head().to_string()}")
    logging.info(f"Sample of 'Year of death' after parsing (first 5):\n{df['Year of death'].head().to_string()}")
    return df

# In wiki_parser_utils.py (add this new function, keep the old clean_data for now or remove it)

def clean_data_from_parsed_years(df: pd.DataFrame) -> pd.DataFrame:
    """
    Cleans data assuming 'Year of birth' and 'Year of death' columns might already exist.
    Converts them to numeric and calculates 'Potential EU PD Year'.
    """
    if df is None or df.empty:
        logging.warning("clean_data_from_parsed_years received an empty or None DataFrame.")
        return pd.DataFrame()

    logging.info("Starting clean_data_from_parsed_years.")
    logging.info(f"Input DataFrame columns: {df.columns.tolist()}")

    # Standardize potential year column names to 'Year of birth' and 'Year of death'
    # This is a bit of a guess based on your previous output. Adjust as needed.
    # The `parse_table` function's logging of headers for EACH table will be key here.
    
    # If 'Year of birth' is present, try to clean and convert it.
    if 'Year of birth' in df.columns:
        logging.info("Processing 'Year of birth' column.")
        # Remove non-numeric characters except the year itself (e.g. "c.", "b.")
        df['Year of birth'] = df['Year of birth'].astype(str).str.extract(r'(\d{4})')[0]
        df['Year of birth'] = pd.to_numeric(df['Year of birth'], errors='coerce')
    else:
        logging.warning("'Year of birth' column not found. It will be NA.")
        df['Year of birth'] = pd.NA

    # If 'Year of death' is present, try to clean and convert it.
    if 'Year of death' in df.columns:
        logging.info("Processing 'Year of death' column.")
        df['Year of death'] = df['Year of death'].astype(str).str.extract(r'(\d{4})')[0]
        df['Year of death'] = pd.to_numeric(df['Year of death'], errors='coerce')
    else:
        # Handle the case where there might be a single "Years" column from one source
        # and "Year of death" from another. Or if there's a column named "Year of".
        # This part needs to be informed by the individual df inspection.
        # For now, we assume if 'Year of death' is missing, we can't get it simply.
        logging.warning("'Year of death' column not found. It will be NA.")
        df['Year of death'] = pd.NA
        
    # If there was an ambiguous 'Year of' column and 'Year of death' is still NA,
    # we could try to parse 'Year of' if it's actually the death year.
    # Example: (This is speculative, needs confirmation from logs)
    if 'Year of' in df.columns and 'Year of death' in df.columns and df['Year of death'].isna().all():
        logging.info("Attempting to use 'Year of' as 'Year of death'.")
        df['Year of death'] = df['Year of'].astype(str).str.extract(r'(\d{4})')[0]
        df['Year of death'] = pd.to_numeric(df['Year of death'], errors='coerce')


    # Remove citation markers from all object columns
    for col in df.select_dtypes(include=['object']):
        if df[col].notna().any():
            df[col] = df[col].astype(str).str.replace(r'\[\d+\]|\[citation needed\]|\[\w+\]', '', regex=True).str.strip()
    
    # Calculate Potential EU Public Domain Year
    if 'Year of death' in df.columns and df['Year of death'].notna().any():
        df['Potential EU PD Year'] = df['Year of death'] + 71
    else:
        df['Potential EU PD Year'] = pd.NA

    logging.info(f"Sample of 'Year of birth' after cleaning (first 5):\n{df['Year of birth'].head().to_string()}")
    logging.info(f"Sample of 'Year of death' after cleaning (first 5):\n{df['Year of death'].head().to_string()}")
    return df

import requests
import pandas as pd
import numpy as np
import json
import time
import logging # Use logging from your utils
from urllib.parse import unquote # To handle URL-encoded titles

# (REQUEST_HEADERS from your utils can be used as a base if you don't define a specific one here)

def get_pageviews_for_url(article_url: str, start_date: str, end_date: str, session: Optional[requests.Session] = None) -> float:
    """
    Fetches the total pageviews for a specific Wikipedia article URL within a date range.

    Args:
        article_url (str): Full URL of the Wikipedia page (e.g., "https://en.wikipedia.org/wiki/Johann_Sebastian_Bach").
        start_date (str): Start date in YYYYMMDD format.
        end_date (str): End date in YYYYMMDD format.
        session (requests.Session, optional): A requests Session object to use for the request. Defaults to None.

    Returns:
        float: Total pageviews or np.nan if an error occurs or no data.
    """
    if pd.isnull(article_url) or not article_url.startswith("https://en.wikipedia.org/wiki/"):
        logging.warning(f"Invalid or missing Wikipedia URL: {article_url}")
        return np.nan

    # Extract the article title. Handles titles with special characters that might be URL-encoded.
    try:
        title_encoded = article_url.split('/wiki/')[-1]
        # Wikipedia titles are case-sensitive and use underscores for spaces.
        # The API expects the title as it appears in the URL path.
        title = title_encoded # Usually, no further unquoting is needed here as requests handles it.
                              # If issues arise, consider unquote(title_encoded)
    except IndexError:
        logging.warning(f"Could not extract title from URL: {article_url}")
        return np.nan

    # Wikimedia Pageviews API endpoint
    # API docs: https://wikitech.wikimedia.org/wiki/Analytics/AQS/Pageviews
    api_base_url = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia.org/all-access/all-agents/"
    # Using monthly granularity. Daily is also available: /daily/{start_date}/{end_date}
    # Monthly is YYYYMMDD00 for start and end for the first day of the month.
    # To be precise for monthly, start_date and end_date should be YYYYMM01 and YYYYMM(last_day_of_month)
    # For simplicity with YYYYMMDD, the API sums up daily views within the range if daily endpoint is used.
    # Or, for monthly endpoint, it gets views for each month fully or partially in the range.
    # Let's use monthly granularity with YYYYMMDD for start/end day.
    # API expects title to be URL-encoded for the path, requests usually handles this.
    request_url = f"{api_base_url}{title}/monthly/{start_date}00/{end_date}00"


    # It's crucial to have a polite User-Agent
    headers = {
        "User-Agent": "MyWikipediaAnalysisBot/1.0 (https://your-project-url-or-contact.com; youremail@example.com)"
    }

    requester = session if session else requests
    total_pageviews = 0

    try:
        # logging.debug(f"Requesting pageviews for: {title} from URL: {request_url}")
        response = requester.get(request_url, headers=headers, timeout=10)
        response.raise_for_status()  # Raises HTTPError for bad responses (4XX or 5XX)
        data = response.json()

        if 'items' in data and data['items']:
            for item in data['items']:
                total_pageviews += item.get('views', 0)
        else:
            # logging.info(f"No pageview items found for {title} between {start_date} and {end_date}. URL: {article_url}")
            # This is not necessarily an error, could just be no views or a new page.
            return 0.0 # Return 0 if no items, rather than NaN, to distinguish from errors

    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            # logging.warning(f"Page not found (404) for pageviews: {title}. URL: {article_url}")
            pass # Treat as 0 views if page doesn't exist in pageview stats
        else:
            logging.error(f"HTTP error fetching pageviews for {title} (URL: {article_url}): {e.response.status_code} {e.response.reason}")
        return np.nan # Keep as NaN for actual errors other than 404 on items
    except requests.exceptions.RequestException as e:
        logging.error(f"Request error fetching pageviews for {title} (URL: {article_url}): {e}")
        return np.nan
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error for pageviews of {title} (URL: {article_url}): {e}. Response text: {response.text[:200]}")
        return np.nan
    except Exception as e:
        logging.error(f"An unexpected error occurred for {title} (URL: {article_url}): {e}")
        return np.nan
    
    # Optional: Small delay to be polite to the API
    time.sleep(0.05) # 50 ms, adjust as needed

    return float(total_pageviews)