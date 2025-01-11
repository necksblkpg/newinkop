# sheets.py
#
# Oförändrat från ditt original, förutom denna kommentarsrad.
# Hanterar Google Sheets-autentisering och uppladdning/hämtning av data.

import logging
import streamlit as st
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread_dataframe import set_with_dataframe
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

def authenticate_google_sheets():
    try:
        scope = [
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_name("key.json", scope)
        client = gspread.authorize(creds)
        return client
    except FileNotFoundError:
        logger.error("key.json inte hittad.")
        st.error("key.json inte hittad. Kontrollera att den ligger i projektets rotmapp.")
        return None
    except Exception as e:
        logger.error(f"Autentiseringsfel: {str(e)}")
        st.error(f"Autentiseringsfel: {str(e)}")
        return None

def push_to_google_sheets(df, sheet_name):
    try:
        client = authenticate_google_sheets()
        if client is None:
            return None

        # Hantera NaN/inf innan skrivning
        df = df.replace([np.inf, -np.inf], np.nan).fillna('')

        # Önskad kolumnordning - uppdaterad för att matcha applikationens visning
        desired_order = [
            "ProductID",
            "Product Number",
            "Size",
            "Product Name",
            "Status",
            "Is Bundle",
            "Supplier",
            "Inköpspris",
            "Quantity Sold",
            "Stock Balance",
            "Incoming Qty",
            "Stock + Incoming",
            "Avg Daily Sales",
            "Days to Zero",
            "Reorder Level",
            "Quantity to Order",
            "Need to Order"
        ]

        # Filtrera och ordna kolumner
        existing_columns = [col for col in desired_order if col in df.columns]
        df = df[existing_columns]

        # Skapa ark
        sheet = client.create(sheet_name)
        worksheet = sheet.get_worksheet(0)

        # Skriv DF -> Sheet
        set_with_dataframe(worksheet, df)

        # Dela ark med fördefinierad e-post
        predefined_email = 'neckwearsweden@gmail.com'
        try:
            sheet.share(predefined_email, perm_type='user', role='writer')
        except Exception as e:
            logger.error(f"Misslyckades med att dela med {predefined_email}: {str(e)}")
            st.error(f"Misslyckades med att dela med {predefined_email}: {str(e)}")
            return None

        return sheet.url

    except Exception as e:
        logger.error(f"Kunde inte pusha data till Google Sheets: {str(e)}")
        st.error(f"Kunde inte pusha data till Google Sheets: {str(e)}")
        return None

def fetch_from_google_sheets(sheet_url):
    try:
        client = authenticate_google_sheets()
        if client is None:
            return None

        sheet_id = sheet_url.split('/')[5]
        sheet = client.open_by_key(sheet_id)
        worksheet = sheet.get_worksheet(0)
        records = worksheet.get_all_records()
        df = pd.DataFrame(records)
        return df

    except Exception as e:
        logger.error(f"Fel vid hämtning: {str(e)}")
        st.error(f"Fel vid hämtning: {str(e)}")
        return None
