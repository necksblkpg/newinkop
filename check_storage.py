from firebase_storage import list_files_in_storage, logger
import json
from datetime import datetime

def main():
    print("Kontrollerar Firebase Storage...")
    try:
        files = list_files_in_storage()
        print(f"Hämtade filer: {files}")
        
        if files:
            print("\nInnehåll i Firebase Storage:")
            print("============================")
            for folder, file_list in files.items():
                print(f"\nMapp: /{folder}/")
                for file in file_list:
                    updated = datetime.fromtimestamp(file['updated'].timestamp()).strftime('%Y-%m-%d %H:%M:%S')
                    size = f"{file['size']/1024:.1f} KB" if file['size'] > 1024 else f"{file['size']} bytes"
                    print(f"  - {file['name']} ({size}, uppdaterad: {updated})")
        else:
            print("Kunde inte hämta fillistan från Firebase Storage")
    except Exception as e:
        print(f"Ett fel uppstod: {str(e)}")
        logger.error(f"Fel vid körning av check_storage: {str(e)}")

if __name__ == "__main__":
    main() 