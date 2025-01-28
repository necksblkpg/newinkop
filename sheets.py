# sheets.py
#
# Hanterar Google Sheets-autentisering och uppladdning/hämtning av data.

import logging
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread_dataframe import set_with_dataframe
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

def authenticate_google_sheets():
    """
    Försöker läsa in "key.json" i rotmappen, autentisera med service account,
    och returnerar en gspread.Client. Returnerar None om något går fel.
    """
    try:
        # OAuth-scope
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name("key.json", scope)
        client = gspread.authorize(creds)
        logger.info("Lyckades autentisera mot Google Sheets med key.json!")
        return client
    except FileNotFoundError:
        logger.error("key.json inte hittad i projektets rotmapp.")
        return None
    except Exception as e:
        logger.error(f"Misslyckades med autentisering: {str(e)}")
        return None


def push_to_google_sheets(df: pd.DataFrame, sheet_name: str) -> str:
    """
    Skapar ett nytt Google Sheet med namnet `sheet_name`,
    lägger in dataframe `df` och delar det med en fördefinierad e-post (om du vill).
    Returnerar URL till Google Sheet, eller None vid fel.
    """
    client = authenticate_google_sheets()
    if client is None:
        logger.error("Ingen gspread-klient (autentisering misslyckades).")
        return None

    try:
        # Rensa ut ev. NaN/inf
        df = df.replace([np.inf, -np.inf], np.nan).fillna('')

        # Skapa kalkylarket
        sheet = client.create(sheet_name)
        worksheet = sheet.get_worksheet(0)  # Första fliken

        # Skriv DF -> Sheet
        set_with_dataframe(worksheet, df)

        # Om du vill dela arket med en viss mail, gör så här:
        predefined_email = "neckwearsweden@gmail.com"  # ex.
        try:
            sheet.share(predefined_email, perm_type='user', role='writer')
            logger.info(f"Delade Google Sheet med {predefined_email}")
        except Exception as share_err:
            logger.error(f"Kunde inte dela arket med {predefined_email}: {share_err}")

        logger.info(f"Skapade Google Sheet med namnet '{sheet_name}': {sheet.url}")
        return sheet.url

    except Exception as e:
        logger.error(f"Kunde inte skapa/skriva Google Sheet: {str(e)}")
        return None


def fetch_from_google_sheets(sheet_url: str) -> pd.DataFrame:
    """
    Exempel på hur man kan läsa data tillbaka från en Google Sheets-URL.
    Kräver att du har åtkomst. Returnerar en pandas-DataFrame.
    """
    client = authenticate_google_sheets()
    if client is None:
        logger.error("Ingen gspread-klient, avbryter fetch.")
        return pd.DataFrame()

    try:
        # sheet_url förväntas t.ex. vara "https://docs.google.com/spreadsheets/d/XXX/edit#gid=0"
        # Vi kan plocka ut ID:
        parts = sheet_url.split('/')[5]  # [5] = "XXX" i typical URL
        sheet = client.open_by_key(parts)
        worksheet = sheet.get_worksheet(0)
        records = worksheet.get_all_records()
        df = pd.DataFrame(records)
        return df

    except Exception as e:
        logger.error(f"Fel vid hämtning av Google Sheet: {str(e)}")
        return pd.DataFrame()
