import pandas as pd
from firebase_storage import upload_dataframe_to_firebase, download_dataframe_from_firebase, logger

def test_firebase_connection():
    print("Testar Firebase-anslutning...")
    
    # Skapa en test DataFrame
    test_df = pd.DataFrame({
        'test_column': ['test_value']
    })
    
    # Försök ladda upp
    print("Försöker ladda upp testfil...")
    success = upload_dataframe_to_firebase(test_df, 'test.csv', 'test')
    print(f"Uppladdning {'lyckades' if success else 'misslyckades'}")
    
    if success:
        # Försök ladda ner
        print("Försöker ladda ner testfil...")
        downloaded_df = download_dataframe_from_firebase('test.csv', 'test')
        if downloaded_df is not None:
            print("Nedladdning lyckades!")
            print("Innehåll:", downloaded_df)
        else:
            print("Nedladdning misslyckades")

if __name__ == "__main__":
    test_firebase_connection() 