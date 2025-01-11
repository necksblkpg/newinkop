# sheets.py

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

        # Beräkna Days to Zero om inte redan gjorts
        if 'Stock Balance' in df.columns and 'Avg Daily Sales' in df.columns and 'Days to Zero' in df.columns:
            df['Stock Balance'] = pd.to_numeric(df['Stock Balance'], errors='coerce').fillna(0)
            df['Avg Daily Sales'] = pd.to_numeric(df['Avg Daily Sales'], errors='coerce').fillna(0)

            def calc_days_to_zero(row):
                if row['Avg Daily Sales'] == 0:
                    return ''
                return int(round(row['Stock Balance'] / row['Avg Daily Sales'], 0))

            df['Days to Zero'] = df.apply(calc_days_to_zero, axis=1)

        # Önskad kolumnordning
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
            "Avg Daily Sales",
            "Days to Zero",
            "Reorder Level",
            "Quantity to Order",
            "Need to Order",
            "Quantity ordered"
        ]
        existing_columns = [col for col in desired_order if col in df.columns]
        df = df[existing_columns]

        # Skapa ark
        sheet = client.create(sheet_name)
        worksheet = sheet.get_worksheet(0)

        # Skriv DF -> Sheet
        set_with_dataframe(worksheet, df)

        # Dela ark med en fördefinierad e-post
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
