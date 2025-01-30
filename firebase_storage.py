import firebase_admin
from firebase_admin import credentials, storage
import pandas as pd
import json
import io
from datetime import datetime
import logging

# Konfigurera loggning
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Initiera Firebase
cred = credentials.Certificate('storage-inkop-firebase-adminsdk-fbsvc-f6910c8036.json')
firebase_admin.initialize_app(cred, {
    'storageBucket': 'storage-inkop.firebasestorage.app'
})

bucket = storage.bucket()

def upload_dataframe_to_firebase(df, filename, folder='orders'):
    """
    Laddar upp en DataFrame till Firebase Storage som CSV
    """
    try:
        # Konvertera DataFrame till CSV
        csv_buffer = io.StringIO()
        df.to_csv(csv_buffer, index=False)
        csv_content = csv_buffer.getvalue()

        # Skapa en blob och ladda upp
        blob = bucket.blob(f'{folder}/{filename}')
        blob.upload_from_string(csv_content, content_type='text/csv')
        
        logger.info(f"Lyckades ladda upp {filename} till Firebase Storage i mappen {folder}")
        return True
    except Exception as e:
        logger.error(f"Fel vid uppladdning till Firebase: {str(e)}")
        return False

def download_dataframe_from_firebase(filename, folder='orders'):
    """
    Hämtar en CSV-fil från Firebase Storage och returnerar som DataFrame
    """
    try:
        blob = bucket.blob(f'{folder}/{filename}')
        content = blob.download_as_string().decode('utf-8')
        df = pd.read_csv(io.StringIO(content))
        logger.info(f"Lyckades hämta {filename} från Firebase Storage i mappen {folder}")
        return df
    except Exception as e:
        logger.error(f"Fel vid hämtning från Firebase: {str(e)}")
        return None

def save_active_orders(df):
    """
    Sparar aktiva ordrar till Firebase
    """
    return upload_dataframe_to_firebase(df, 'active_orders.csv', 'orders')

def load_active_orders():
    """
    Hämtar aktiva ordrar från Firebase
    """
    return download_dataframe_from_firebase('active_orders.csv', 'orders')

def backup_orders():
    """
    Skapar en backup av ordrar med tidsstämpel
    """
    try:
        df = load_active_orders()
        if df is not None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f'orders_backup_{timestamp}.csv'
            
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False)
            blob = bucket.blob(f'backup/{filename}')
            blob.upload_from_string(csv_buffer.getvalue(), content_type='text/csv')
            
            logger.info(f"Backup skapad: {filename}")
            return True
    except Exception as e:
        logger.error(f"Fel vid backup: {str(e)}")
    return False

def save_product_costs(df):
    """
    Sparar product costs till Firebase
    """
    return upload_dataframe_to_firebase(df, 'product_costs.csv', 'costs')

def load_product_costs():
    """
    Hämtar product costs från Firebase
    """
    return download_dataframe_from_firebase('product_costs.csv', 'costs')

def save_price_list(df):
    """
    Sparar prislista till Firebase
    """
    return upload_dataframe_to_firebase(df, 'price_lists.csv', 'prices')

def list_files_in_storage():
    """
    Listar alla filer i Firebase Storage
    """
    try:
        blobs = bucket.list_blobs()
        files = {}
        for blob in blobs:
            folder = blob.name.split('/')[0] if '/' in blob.name else 'root'
            if folder not in files:
                files[folder] = []
            files[folder].append({
                'name': blob.name,
                'size': blob.size,
                'updated': blob.updated
            })
        return files
    except Exception as e:
        logger.error(f"Fel vid listning av filer: {str(e)}")
        return None

def load_price_list():
    """
    Hämtar prislista från Firebase
    """
    return download_dataframe_from_firebase('price_lists.csv', 'prices') 