# app.py
#
# Uppdaterad huvudfil. Nu har vi tre flikar:
#  1) Statistik & Översikt
#  2) Beställningar
#  3) Mottag Leverans (NY) - för att stämma av leveranser och uppdatera snittkostnad

import os
import pathlib
import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

from data import (
    fetch_all_products_with_sales,
    load_product_costs,
    save_product_costs,
    get_current_avg_cost,
    update_avg_cost,
    add_incoming_stock_columns,  # nu kommer den från data.py
    PRODUCT_COSTS_FILE
)
from sheets import push_to_google_sheets

from data import (
    fetch_all_product_costs,
    fetch_sales_data,
    process_sales_data,
    merge_product_and_sales_data,
    calculate_reorder_metrics
)

# Filnamn för lagring av ordrar
ACTIVE_ORDERS_FILE = "active_orders.csv"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("app.log"),
              logging.StreamHandler()]
)
logger = logging.getLogger(__name__)


def main():
    st.set_page_config(
        page_title="Inköpssystem för småföretag",
        layout="wide",
    )

    custom_style()

    api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
    api_token = os.environ.get('CENTRA_API_TOKEN')

    if not api_endpoint or not api_token:
        st.error("API-endpoint och/eller token saknas. Ställ in miljövariabler och starta om.")
        return

    # Initiera ordrar samt product_costs i session_state
    load_orders_from_file()
    if "product_costs" not in st.session_state:
        st.session_state["product_costs"] = load_product_costs()

    # Lägg till efter övrig session_state initiering
    if "selected_delivery" not in st.session_state:
        st.session_state.selected_delivery = None
    
    if "delivery_status" not in st.session_state:
        st.session_state.delivery_status = {
            "pending": [],    # Lista över väntande leveranser
            "completed": []   # Lista över avklarade leveranser
        }

    st.title("Inköpssystem för småföretag")

    # Förenkla huvudnavigering till två flikar
    tab_stats, tab_deliveries = st.tabs([
        "📊 Statistik & Översikt", 
        "📦 Leveranser"
    ])

    with tab_stats:
        render_statistics_tab(api_endpoint, api_token)

    with tab_deliveries:
        render_deliveries_tab()


def render_statistics_tab(api_endpoint, api_token):
    st.subheader("Välj datumintervall och filtrera produkter")

    today = datetime.today()
    default_from_date = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    default_to_date = today.strftime('%Y-%m-%d')

    col1, col2 = st.columns(2)
    with col1:
        from_date = st.date_input("Från Datum", value=datetime.strptime(default_from_date, '%Y-%m-%d'))
    with col2:
        to_date = st.date_input("Till Datum", value=datetime.strptime(default_to_date, '%Y-%m-%d'))

    from_date_str = from_date.strftime('%Y-%m-%d')
    to_date_str = to_date.strftime('%Y-%m-%d')

    st.markdown("### Filteralternativ")
    colA, colB, colC, colD = st.columns(4)
    with colA:
        active_filter = st.checkbox("✅ Endast aktiva", value=True)
    with colB:
        bundle_filter = st.checkbox("🚫 Exkl. Bundles", value=True)
    with colC:
        exclude_supplier = st.checkbox("🚫 Exkl. 'Utgående produkt'", value=True)
    with colD:
        shipped_filter = st.checkbox("📦 Endast SHIPPED", value=True)

    st.markdown("### Lagerparametrar")
    colLT, colSS = st.columns(2)
    with colLT:
        lead_time = st.number_input("⏱️ Leveranstid (dagar)", min_value=1, value=7)
    with colSS:
        safety_stock = st.number_input("🛡️ Säkerhetslager", min_value=0, value=10)

    st.markdown("---")

    if st.button("Hämta Produkt- och Försäljningsdata"):
        with st.spinner("Hämtar data..."):
            df = fetch_all_products_with_sales(
                api_endpoint, api_token,
                from_date_str, to_date_str,
                lead_time, safety_stock,
                only_shipped=shipped_filter
            )

        if df is not None and not df.empty:
            if active_filter and 'Status' in df.columns:
                df = df[df['Status'] == "ACTIVE"]
            if bundle_filter and 'Is Bundle' in df.columns:
                df = df[df['Is Bundle'] == False]
            if exclude_supplier and 'Supplier' in df.columns:
                df = df[df['Supplier'] != "Utgående produkt"]

            if df.empty:
                st.warning("Inga produkter matchade dina filter.")
                return

            # Lägg på apostrof för "Product Number"
            if 'Product Number' in df.columns:
                df['Product Number'] = "'" + df['Product Number'].astype(str)

            # Lägg till inkommande kvantitet för enbart aktiva ordrar
            df = add_incoming_stock_columns(df)

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
            cols_we_have = [c for c in desired_order if c in df.columns]
            df = df[cols_we_have]

            # Nollställ "Quantity to Order" kolumnen (används bl.a. vid import)
            if 'Quantity to Order' in df.columns:
                df['Quantity to Order'] = ''

            st.session_state['merged_df'] = df.copy()
            st.success("Data hämtad framgångsrikt!")
            st.dataframe(df, use_container_width=True)

        else:
            st.error("Misslyckades med att hämta data eller ingen data fanns.")

    # Push till Sheets
    if 'merged_df' in st.session_state:
        merged_df = st.session_state['merged_df']
        st.markdown("---")
        if st.button("📤 Push Data till Google Sheets"):
            with st.spinner("Pushar data..."):
                filtered_df = merged_df.replace([np.inf, -np.inf], np.nan).fillna('')
                sheet_name = f"Produkt_Försäljning_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                sheet_url = push_to_google_sheets(filtered_df, sheet_name)
                if sheet_url:
                    st.success(f"Data pushad till Google Sheets! [Öppna Sheet]({sheet_url})")
                else:
                    st.error("Något gick fel vid push till Google Sheets.")


def render_deliveries_tab():
    """
    Huvudvy för leveranshantering som visar:
    1. Skapa ny leverans
    2. Väntande leveranser
    3. Avklarade leveranser
    """
    if "delivery_view" not in st.session_state:
        st.session_state.delivery_view = "list"
        
    if "selected_delivery" not in st.session_state:
        st.session_state.selected_delivery = None

    # Visa specifika vyer
    if st.session_state.delivery_view == "process":
        render_delivery_processor()
        return
    
    if st.session_state.delivery_view == "create":
        st.subheader("Skapa ny leverans")
        
        # Uppdaterat exempel med Size
        example_df = pd.DataFrame([
            {
                "ProductID": "12345",
                "Product Number": "ABC-123",
                "Product Name": "Exempel Produkt 1",
                "Supplier": "Leverantör AB",
                "Inköpspris": 100,
                "Quantity to Order": 10,
                "Size": "M"
            },
            {
                "ProductID": "67890",
                "Product Number": "XYZ-789",
                "Product Name": "Exempel Produkt 2",
                "Supplier": "Leverantör AB",
                "Inköpspris": 150,
                "Quantity to Order": 5,
                "Size": "L"
            }
        ])
        
        with st.expander("Visa exempel på CSV-format"):
            st.write("CSV-filen måste innehålla följande kolumner:")
            st.dataframe(example_df)
            
            # Ladda ner exempel-CSV
            csv = example_df.to_csv(index=False)
            st.download_button(
                label="📥 Ladda ner exempel-CSV",
                data=csv,
                file_name="exempel_order.csv",
                mime="text/csv"
            )

        # CSV-uppladdning
        order_name = st.text_input(
            "Namn på leveransen",
            placeholder="T.ex. 'Höstkollektion 2024' eller 'Påfyllning basic'",
            key="csv_order_name"
        )
        
        uploaded_file = st.file_uploader("Välj CSV-fil", type=["csv"])
        if uploaded_file and order_name:
            try:
                df = pd.read_csv(uploaded_file)
                
                # Visa endast obligatoriska kolumner i förhandsgranskningen
                preview_cols = [
                    "ProductID", 
                    "Product Number", 
                    "Product Name", 
                    "Supplier", 
                    "Inköpspris", 
                    "Quantity to Order",
                    "Size"
                ]
                
                # Skapa en kopia med endast de obligatoriska kolumnerna som finns
                preview_df = df[preview_cols].copy()
                
                st.write("Förhandsgranskning:")
                st.dataframe(preview_df)
                
                if st.button("Importera leverans", type="primary"):
                    if create_new_delivery(order_name, df):
                        st.success(f"✅ Leverans '{order_name}' importerad!")
                        st.session_state.delivery_view = "list"
                        st.rerun()
                    
            except Exception as e:
                st.error(f"Fel vid import: {str(e)}")

    # Huvudvy
    col1, col2 = st.columns([4, 1])
    with col1:
        st.header("Leveranser")
    with col2:
        st.button("+ Ny leverans", 
            type="primary",
            on_click=lambda: setattr(st.session_state, 'delivery_view', 'create'),
            use_container_width=True)

    # Väntande leveranser
    active_orders = st.session_state.all_orders[
        st.session_state.all_orders['IsActive'] == True
    ].copy()
    
    if not active_orders.empty:
        grouped_orders = active_orders.groupby('OrderName').agg({
            'OrderDate': 'first',
            'Quantity ordered': 'sum',
            'ProductID': 'count'
        }).reset_index()
        
        # Kompakt tabell för väntande leveranser
        st.markdown("### 📦 Väntande leveranser")
        for _, row in grouped_orders.iterrows():
            with st.container():
                cols = st.columns([3, 2, 2, 2])
                with cols[0]:
                    st.write(f"**{row['OrderName']}**")
                with cols[1]:
                    st.write(f"📅 {row['OrderDate']}")
                with cols[2]:
                    st.write(f"🔢 {row['ProductID']} prod. ({row['Quantity ordered']} st)")
                with cols[3]:
                    action_col1, action_col2 = st.columns(2)
                    with action_col1:
                        if st.button("📥", key=f"receive_{row['OrderName']}", 
                            help="Ta emot leverans"):
                            st.session_state.selected_delivery = row['OrderName']
                            st.session_state.delivery_view = "process"
                            st.rerun()
                    with action_col2:
                        if st.button("❌", key=f"cancel_{row['OrderName']}", 
                            help="Makulera leverans"):
                            if st.session_state.get('confirm_cancel') == row['OrderName']:
                                cancel_delivery(row['OrderName'])
                                st.rerun()
                            else:
                                st.session_state.confirm_cancel = row['OrderName']
                                st.warning(f"Klicka igen för att bekräfta makulering av '{row['OrderName']}'")
            
                # Lägg till expander för leveransdetaljer under huvudraden
                with st.expander("📋 Leveransdetaljer"):
                    order_details = active_orders[
                        active_orders['OrderName'] == row['OrderName']
                    ].copy()
                    
                    # Visa alla relevanta kolumner
                    display_cols = [
                        'ProductID',
                        'Product Number',
                        'Product Name',
                        'Supplier',
                        'Inköpspris',
                        'Size',
                        'Quantity ordered'
                    ]
                    
                    # Kontrollera vilka kolumner som faktiskt finns i data
                    available_cols = [col for col in display_cols if col in order_details.columns]
                    
                    st.dataframe(
                        order_details[available_cols],
                        use_container_width=True
                    )
                st.markdown("---")
    else:
        st.info("Inga väntande leveranser")

    # Avklarade leveranser i en kompakt expander
    completed_orders = st.session_state.all_orders[
        st.session_state.all_orders['IsActive'] == False
    ].copy()
    
    if not completed_orders.empty:
        with st.expander("✅ Visa avklarade leveranser"):
            grouped_completed = completed_orders.groupby('OrderName').agg({
                'OrderDate': 'first',
                'Quantity ordered': 'sum',
                'ProductID': 'count'
            }).reset_index()
            
            # Kompakt tabell för avklarade leveranser
            for _, row in grouped_completed.iterrows():
                cols = st.columns([3, 2, 2])
                with cols[0]:
                    st.write(f"**{row['OrderName']}**")
                with cols[1]:
                    st.write(f"📅 {row['OrderDate']}")
                with cols[2]:
                    st.write(f"🔢 {row['ProductID']} prod. ({row['Quantity ordered']} st)")
                st.markdown("---")
    else:
        st.info("Inga avklarade leveranser")

def load_orders_from_file():
    """
    Läser in en fil "active_orders.csv" från disk och stoppar i st.session_state.all_orders.
    Om fil saknas -> tom dataframe.
    """
    if 'all_orders' not in st.session_state:
        st.session_state.all_orders = pd.DataFrame(columns=[
            'OrderDate',
            'OrderName',
            'ProductID',
            'Size',
            'Quantity ordered',
            'IsActive'
        ])

    path = pathlib.Path(ACTIVE_ORDERS_FILE)
    if path.is_file():
        try:
            df = pd.read_csv(path, dtype={"ProductID": str, "Size": str})
            # Om IsActive inte finns i fil -> sätt True
            if "IsActive" not in df.columns:
                df["IsActive"] = True
            # Om OrderName inte finns -> sätt datum som namn
            if "OrderName" not in df.columns:
                df["OrderName"] = "Beställning " + df["OrderDate"]
            df['Quantity ordered'] = pd.to_numeric(df['Quantity ordered'], errors='coerce').fillna(0)
            st.session_state.all_orders = df
            logger.info("Ordrar laddade från fil.")
        except Exception as e:
            logger.error(f"Kunde inte läsa {ACTIVE_ORDERS_FILE}: {str(e)}")
    else:
        logger.info("Ingen fil för ordrar hittades. Skapar ny tom DF.")


def save_orders_to_file():
    """
    Sparar st.session_state.all_orders till en CSV-fil.
    """
    if 'all_orders' not in st.session_state:
        return
    try:
        st.session_state.all_orders.to_csv(ACTIVE_ORDERS_FILE, index=False)
        logger.info("Ordrar sparade till disk.")
    except Exception as e:
        logger.error(f"Kunde inte spara ordrar: {str(e)}")


def custom_style():
    st.markdown("""
        <style>
            .stButton>button {
                background-color: #0B7285;
                color: white;
                padding: 0.7em 1.5em;
                border-radius: 5px;
                border: none;
                font-size: 0.9em;
                margin-top: 10px;
            }
            .stButton>button:hover {
                background-color: #095C68;
            }
            table {
                border-collapse: collapse;
                width: 100%;
            }
            th, td {
                text-align: left;
                padding: 8px;
            }
            th {
                background-color: #f2f2f2;
            }
            tr:nth-child(even) {
                background-color: #f9f9f9;
            }
        </style>
        """, unsafe_allow_html=True)

def handle_delivery_completion(delivery_df):
    """Hanterar godkännande av en leverans"""
    order_name = delivery_df['OrderName'].iloc[0]
    
    # Uppdatera lagersaldo och inaktivera ordern
    mask = st.session_state.all_orders['OrderName'] == order_name
    st.session_state.all_orders.loc[mask, 'IsActive'] = False
    
    # Uppdatera merged_df med nya lagersaldon
    if 'merged_df' in st.session_state:
        merged_df = st.session_state['merged_df'].copy()
        for _, row in delivery_df.iterrows():
            product_mask = (
                (merged_df['ProductID'] == row['ProductID']) &
                (merged_df['Size'] == row['Size'])
            )
            if any(product_mask):
                merged_df.loc[product_mask, 'Stock Balance'] += row['Mottagen mängd']
        
        merged_df = add_incoming_stock_columns(merged_df)
        st.session_state['merged_df'] = merged_df
    
    # Spara ändringar
    save_orders_to_file()

def render_delivery_processor():
    """
    Dedikerad sida för att hantera en leverans som precis anlänt.
    Här stämmer man av mottagen kvantitet och kvalitet innan leveransen godkänns.
    """
    # Visa tillbaka-knapp
    col1, col2, col3 = st.columns([1, 2, 1])
    with col1:
        if st.button("← Tillbaka till leveranser"):
            st.session_state.delivery_view = "list"
            st.session_state.selected_delivery = None
            if "delivery_check" in st.session_state:
                del st.session_state.delivery_check
            st.rerun()

    st.header("Hantera inkommande leverans")
    
    # Hämta leveransdata
    order_name = st.session_state.selected_delivery
    order_data = st.session_state.all_orders[
        (st.session_state.all_orders['OrderName'] == order_name) & 
        (st.session_state.all_orders['IsActive'] == True)
    ].copy()
    
    # Visa leveransöversikt
    st.subheader(f"📦 {order_name}")
    col1, col2 = st.columns(2)
    with col1:
        st.info(f"Orderdatum: {order_data['OrderDate'].iloc[0]}")
    with col2:
        st.info(f"Antal produkter: {len(order_data)}")
    
    # Initiera leveranskontroll om det inte redan gjorts
    if "delivery_check" not in st.session_state:
        delivery_check = order_data.copy()
        delivery_check['Mottagen mängd'] = delivery_check['Quantity ordered']
        delivery_check['Kvalitet OK'] = False
        delivery_check['Kommentar'] = ''
        st.session_state.delivery_check = delivery_check

    # Visa instruktioner
    st.markdown("---")
    st.markdown("### 📋 Instruktioner")
    st.markdown("""
    1. Kontrollera mottagen mängd för varje produkt
    2. Markera kvaliteten som OK när produkten är kontrollerad
    3. Lägg till kommentarer vid behov
    4. Tryck på 'Godkänn leverans' när allt är klart
    """)
    
    # Visa och hantera produkter
    edited_df = st.data_editor(
        st.session_state.delivery_check,
        key="delivery_processor",
        use_container_width=True,
        column_config={
            "OrderName": None,  # Dölj dessa kolumner
            "OrderDate": None,
            "IsActive": None,
            "ProductID": st.column_config.TextColumn("ProduktID", disabled=True),
            "Size": st.column_config.TextColumn("Storlek", disabled=True),
            "Quantity ordered": st.column_config.NumberColumn(
                "Beställd mängd", 
                disabled=True,
                help="Ursprungligt beställd mängd"
            ),
            "Mottagen mängd": st.column_config.NumberColumn(
                "Mottagen mängd",
                help="Ange faktiskt mottagen mängd",
                min_value=0
            ),
            "Kvalitet OK": st.column_config.CheckboxColumn(
                "✓ Kvalitet OK",
                help="Markera när produktens kvalitet är kontrollerad och godkänd"
            ),
            "Kommentar": st.column_config.TextColumn(
                "Kommentar",
                help="Lägg till eventuell kommentar om avvikelser"
            ),
        }
    )
    
    # Uppdatera session state med ändringar
    st.session_state.delivery_check = edited_df
    
    # Visa sammanfattning och avvikelser
    st.markdown("### 📊 Sammanfattning")
    total_ordered = edited_df['Quantity ordered'].sum()
    total_received = edited_df['Mottagen mängd'].sum()
    products_ok = edited_df['Kvalitet OK'].sum()
    
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Totalt beställt", total_ordered)
    with col2:
        st.metric("Totalt mottaget", total_received)
    with col3:
        st.metric("Produkter OK", f"{products_ok} av {len(edited_df)}")
    
    # Visa avvikelser om sådana finns
    avvikelser = edited_df[
        (edited_df['Quantity ordered'] != edited_df['Mottagen mängd']) |
        (edited_df['Kommentar'].str.len() > 0)
    ]
    if not avvikelser.empty:
        st.markdown("### ⚠️ Avvikelser")
        for _, row in avvikelser.iterrows():
            st.warning(
                f"**{row['ProductID']} ({row['Size']})**: " +
                f"Beställt: {row['Quantity ordered']}, Mottaget: {row['Mottagen mängd']}" +
                (f"\nKommentar: {row['Kommentar']}" if row['Kommentar'] else "")
            )
    
    # Knapp för att godkänna leveransen
    st.markdown("---")
    col1, col2 = st.columns(2)
    with col2:
        if st.button("✅ Godkänn leverans", 
            type="primary",
            use_container_width=True,
            disabled=not all(edited_df['Kvalitet OK'])  # Kräv att alla produkter är OK
        ):
            handle_delivery_completion(edited_df)
            st.success("✅ Leverans godkänd och arkiverad!")
            st.session_state.delivery_view = "list"
            st.session_state.selected_delivery = None
            if "delivery_check" in st.session_state:
                del st.session_state.delivery_check
            st.rerun()

def create_new_delivery(order_name, products_df):
    """Skapar en ny leverans och lägger till i all_orders"""
    try:
        # Definiera obligatoriska kolumner
        required_cols = [
            "ProductID", 
            "Product Number", 
            "Product Name", 
            "Supplier", 
            "Inköpspris", 
            "Quantity to Order",
            "Size"
        ]
        
        # Validera att alla obligatoriska kolumner finns
        missing_cols = [col for col in required_cols if col not in products_df.columns]
        if missing_cols:
            st.error(f"CSV-filen saknar följande obligatoriska kolumner: {', '.join(missing_cols)}")
            return False

        # Rensa och validera data
        valid_products = products_df.copy()
        valid_products['ProductID'] = valid_products['ProductID'].astype(str)
        valid_products['Size'] = valid_products['Size'].astype(str)
        valid_products['Quantity to Order'] = pd.to_numeric(valid_products['Quantity to Order'], errors='coerce').fillna(0)
        
        # Filtrera bort rader med quantity = 0
        valid_products = valid_products[valid_products['Quantity to Order'] > 0].copy()
        
        if valid_products.empty:
            st.error("Inga giltiga produkter hittades (Quantity to Order måste vara > 0)")
            return False

        # Behåll alla kolumner och lägg till de nödvändiga för systemet
        valid_products['OrderDate'] = pd.Timestamp.now().strftime('%Y-%m-%d')
        valid_products['OrderName'] = order_name
        valid_products['Quantity ordered'] = valid_products['Quantity to Order']
        valid_products['IsActive'] = True
        
        # Ta bort Quantity to Order eftersom vi nu har Quantity ordered
        valid_products = valid_products.drop(columns=['Quantity to Order'])

        # Lägg till i befintliga ordrar
        st.session_state.all_orders = pd.concat(
            [st.session_state.all_orders, valid_products], 
            ignore_index=True
        )
        save_orders_to_file()

        return True

    except Exception as e:
        st.error(f"Ett fel uppstod: {str(e)}")
        return False

def cancel_delivery(order_name):
    """Makulerar en leverans"""
    try:
        # Ta bort leveransen från all_orders
        mask = st.session_state.all_orders['OrderName'] == order_name
        st.session_state.all_orders = st.session_state.all_orders[~mask]
        
        # Spara ändringar
        save_orders_to_file()
        
        # Uppdatera merged_df om den finns
        if 'merged_df' in st.session_state:
            merged_df = st.session_state['merged_df'].copy()
            merged_df = add_incoming_stock_columns(merged_df)
            st.session_state['merged_df'] = merged_df
        
        # Rensa confirm_cancel
        if 'confirm_cancel' in st.session_state:
            del st.session_state.confirm_cancel
            
        st.success(f"Leverans '{order_name}' makulerad")
        
    except Exception as e:
        st.error(f"Kunde inte makulera leveransen: {str(e)}")

if __name__ == "__main__":
    main()
