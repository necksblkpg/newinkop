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

    st.title("Inköpssystem för småföretag")

    tab_stats, tab_orders, tab_delivery = st.tabs([
        "📊 Statistik & Översikt", 
        "📦 Beställningar",
        "🛬 Mottag Leverans"
    ])

    with tab_stats:
        render_statistics_tab(api_endpoint, api_token)

    with tab_orders:
        render_orders_tab()

    with tab_delivery:
        render_delivery_tab()


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


def render_orders_tab():
    """
    Visar två sub-tabs:
      1) Aktiva ordrar
      2) Inaktiva ordrar
    """

    sub_tab_active, sub_tab_inactive = st.tabs(["Aktiva ordrar", "Inaktiva ordrar"])

    with sub_tab_active:
        render_active_orders_ui()

    with sub_tab_inactive:
        render_inactive_orders_ui()

    st.markdown("---")

    st.subheader("Importera beställningar från CSV")
    uploaded_file = st.file_uploader("Välj CSV-fil", type=["csv"])
    if uploaded_file:
        try:
            with st.spinner("Importerar beställningar..."):
                imported_df = pd.read_csv(uploaded_file)
                required_cols = ["ProductID", "Size", "Quantity ordered"]
                if all(c in imported_df.columns for c in required_cols):
                    imported_df['ProductID'] = imported_df['ProductID'].astype(str)
                    imported_df['Quantity ordered'] = pd.to_numeric(imported_df['Quantity ordered'], errors='coerce').fillna(0)
                    valid = imported_df[imported_df['Quantity ordered'] > 0].copy()
                    if not valid.empty:
                        valid['OrderDate'] = pd.Timestamp.now().strftime('%Y-%m-%d')
                        valid['IsActive'] = True

                        st.session_state.all_orders = pd.concat([st.session_state.all_orders, valid], ignore_index=True)
                        save_orders_to_file()
                        st.success(f"Importerade {len(valid)} beställningar.")
                        # Uppdatera lager i merged_df
                        if 'merged_df' in st.session_state:
                            merged_df = st.session_state['merged_df'].copy()
                            for _, row in valid.iterrows():
                                mask = (
                                    (merged_df['ProductID'].astype(str) == row['ProductID']) &
                                    (merged_df['Size'].astype(str) == row['Size'])
                                )
                                if any(mask):
                                    # Här kan man välja att inte direkt öka Stock Balance
                                    # utan bara låta "Incoming Qty" vara
                                    # MEN om du vill att Import = direkt påfyllning, kan du göra:
                                    # merged_df.loc[mask, 'Stock Balance'] += row['Quantity ordered']
                                    pass

                            merged_df = add_incoming_stock_columns(merged_df)
                            st.session_state['merged_df'] = merged_df
                            st.info("Lagersaldo uppdaterat (Incoming Qty) i Statistik & Översikt.")

                    else:
                        st.warning("Inga giltiga rader i CSV-filen (måste ha Quantity ordered > 0).")
                else:
                    st.error(f"Krävda kolumner saknas. Behöver: {', '.join(required_cols)}")
        except Exception as e:
            st.error(f"Något gick fel: {str(e)}")

    st.markdown("### Manuell beställning")
    with st.expander("Lägg till beställning manuellt"):
        product_id = st.text_input("ProduktID")
        size = st.text_input("Storlek")
        qty = st.number_input("Kvantitet", min_value=1)
        if st.button("Lägg till order"):
            if product_id and size:
                new_order = {
                    "OrderDate": pd.Timestamp.now().strftime('%Y-%m-%d'),
                    "ProductID": product_id,
                    "Size": size,
                    "Quantity ordered": qty,
                    "IsActive": True
                }
                st.session_state.all_orders = pd.concat(
                    [st.session_state.all_orders, pd.DataFrame([new_order])],
                    ignore_index=True
                )
                save_orders_to_file()

                if 'merged_df' in st.session_state:
                    merged_df = st.session_state['merged_df'].copy()
                    # Samma notering här som ovan, vill du öka lagret direkt eller bara ha det i “Incoming”?
                    # merged_df.loc[mask, 'Stock Balance'] += qty
                    merged_df = add_incoming_stock_columns(merged_df)
                    st.session_state['merged_df'] = merged_df

                st.success("Ny beställning tillagd!")
            else:
                st.warning("Fyll i ProduktID och Storlek.")


def render_active_orders_ui():
    st.subheader("Aktiva ordrar")
    # Filtrera ut aktiva
    active_orders = st.session_state.all_orders[st.session_state.all_orders['IsActive'] == True].copy()

    if active_orders.empty:
        st.info("Inga aktiva ordrar")
        return

    # sortera
    active_orders = active_orders.sort_values('OrderDate', ascending=False).reset_index(drop=True)

    edited_active_orders = st.data_editor(
        active_orders,
        key="active_orders_editor",
        use_container_width=True,
        column_config={
            "IsActive": st.column_config.CheckboxColumn("Aktiv?", help="Avmarkera för att inaktivera"),
            "OrderDate": st.column_config.TextColumn("OrderDate", disabled=True),
            "ProductID": st.column_config.TextColumn("ProductID", disabled=True),
            "Size": st.column_config.TextColumn("Size", disabled=True),
            "Quantity ordered": st.column_config.NumberColumn("Quantity", disabled=True),
        },
        num_rows="dynamic"
    )

    if st.button("Uppdatera aktiva ordrar"):
        st.session_state.all_orders.update(edited_active_orders)
        save_orders_to_file()
        st.success("Aktiva ordrar uppdaterade!")
        # Uppdatera merged_df
        if 'merged_df' in st.session_state:
            merged_df = st.session_state['merged_df'].copy()
            merged_df = add_incoming_stock_columns(merged_df)
            st.session_state['merged_df'] = merged_df
            st.info("Statistik & översikt uppdaterad.")


def render_inactive_orders_ui():
    st.subheader("Inaktiva ordrar")
    # Filtrera ut inaktiva
    inactive_orders = st.session_state.all_orders[st.session_state.all_orders['IsActive'] == False].copy()
    if inactive_orders.empty:
        st.info("Inga inaktiva ordrar")
        return

    # sortera
    inactive_orders = inactive_orders.sort_values('OrderDate', ascending=False).reset_index(drop=True)

    edited_inactive_orders = st.data_editor(
        inactive_orders,
        key="inactive_orders_editor",
        use_container_width=True,
        column_config={
            "IsActive": st.column_config.CheckboxColumn("Aktiv?", help="Markera för att återaktivera"),
            "OrderDate": st.column_config.TextColumn("OrderDate", disabled=True),
            "ProductID": st.column_config.TextColumn("ProductID", disabled=True),
            "Size": st.column_config.TextColumn("Size", disabled=True),
            "Quantity ordered": st.column_config.NumberColumn("Quantity", disabled=True),
        },
        num_rows="dynamic"
    )

    if st.button("Uppdatera inaktiva ordrar"):
        st.session_state.all_orders.update(edited_inactive_orders)
        save_orders_to_file()
        st.success("Inaktiva ordrar uppdaterade!")
        # Uppdatera merged_df
        if 'merged_df' in st.session_state:
            merged_df = st.session_state['merged_df'].copy()
            merged_df = add_incoming_stock_columns(merged_df)
            st.session_state['merged_df'] = merged_df
            st.info("Statistik & översikt uppdaterad.")


# ----------------------------------------------------------------
#  Ny tab: Mottag Leverans
# ----------------------------------------------------------------
def render_delivery_tab():
    """
    Här kan vi stämma av leveranser (dvs när varor faktiskt anlänt).
    Vi gör en enkel version:
      1. Ange ProductID och storlek
      2. Ange hur mycket som levererats (QTY delivered)
      3. Ange “Baspris” (eller låt systemet hämta “Inköpspris”)
      4. Ange frakt, ev. valutakurs
      5. Beräkna ny “snittkostnad”
      6. Uppdatera lagersaldo i merged_df
      7. Sätt order (IsActive=False) om du vill
    """
    st.header("Mottag Leverans")

    if "merged_df" not in st.session_state:
        st.info("Ingen produktdata i systemet ännu. Hämta i Statistik & Översikt först.")
        return

    merged_df = st.session_state["merged_df"].copy()

    # Välj Produkt
    product_ids = merged_df["ProductID"].unique().tolist()
    product_id = st.selectbox("Välj ProduktID", options=["-"] + product_ids)
    if product_id == "-":
        st.stop()

    # Filtrera storlekar
    product_data = merged_df[merged_df["ProductID"] == product_id].copy()
    sizes = product_data["Size"].unique().tolist()
    size = st.selectbox("Välj Storlek", options=["-"] + sizes)
    if size == "-":
        st.stop()

    mask = (merged_df["ProductID"] == product_id) & (merged_df["Size"] == size)
    current_stock = merged_df.loc[mask, "Stock Balance"].values[0]
    current_cost_in_df = merged_df.loc[mask, "Inköpspris"].values[0]

    st.write(f"Aktuellt lager: **{current_stock}** st")
    st.write(f"Nuvarande inköpspris i merged_df: **{current_cost_in_df}** (separat från snittkostnadstabellen)")

    # Hämta eventuell tidigare snittkostnad
    existing_avg_cost = get_current_avg_cost(product_id)
    st.write(f"Registrerat snittpris i product_costs.csv: **{existing_avg_cost}**")

    colA, colB, colC = st.columns(3)
    with colA:
        qty_delivered = st.number_input("QTY levererad", min_value=1, value=10)
    with colB:
        fraktkostnad = st.number_input("Fraktkostnad (total)", min_value=0.0, value=0.0)
    with colC:
        valutafaktor = st.number_input("Valutafaktor", min_value=0.0, value=1.0, help="Multiplicera grundpris med denna faktor")

    # Exempel: Ny kostnad/produkt = (grundpris * valutafaktor) + (frakt / qty_delivered)
    # Du kan byta logik självklart
    st.markdown("##### Beräkning av nytt leveranspris per enhet")
    base_price = current_cost_in_df * valutafaktor
    if qty_delivered > 0:
        frakt_per_enhet = fraktkostnad / float(qty_delivered)
    else:
        frakt_per_enhet = 0.0

    new_landed_cost_per_unit = round(base_price + frakt_per_enhet, 2)
    st.write(f"**Ny “landed cost” per enhet**: {new_landed_cost_per_unit}")

    # Weighted Average Cost
    # (oldStock * existing_avg_cost + newQty * new_landed_cost) / (oldStock + newQty)
    st.markdown("##### Uppdaterad snittkostnad (viktad)")
    old_stock = float(current_stock)
    old_cost = float(existing_avg_cost)
    new_qty = float(qty_delivered)
    new_cost = float(new_landed_cost_per_unit)

    if (old_stock + new_qty) > 0:
        updated_avg_cost = round(((old_stock * old_cost) + (new_qty * new_cost)) / (old_stock + new_qty), 2)
    else:
        updated_avg_cost = new_cost

    st.write(f"**Viktad snittkostnad**: {updated_avg_cost}")

    if st.button("Mottag leverans"):
        # Uppdatera lager i merged_df
        merged_df.loc[mask, "Stock Balance"] = merged_df.loc[mask, "Stock Balance"] + qty_delivered
        # Sätt Inköpspris i merged_df till new_landed_cost_per_unit, om du vill
        # eller sätt det till updated_avg_cost. Valfritt. 
        merged_df.loc[mask, "Inköpspris"] = updated_avg_cost

        # Uppdatera “product_costs.csv” med updated_avg_cost
        update_avg_cost(product_id, updated_avg_cost)

        # Eventuellt markera relevant order som levererad => IsActive=False
        # Här gör vi ett enkelt svep: Sätt IsActive=False för en rad om den har "Quantity ordered" == qty_delivered
        # Du kan göra mer avancerad logik förstås.
        for idxo, rowo in st.session_state.all_orders.iterrows():
            if rowo["ProductID"] == product_id and rowo["Size"] == size and rowo["IsActive"] == True:
                # Du kan kolla om qty_delivered >= rowo["Quantity ordered"] etc.
                st.session_state.all_orders.at[idxo, "IsActive"] = False

        save_orders_to_file()

        # Spara nya merged_df
        merged_df = add_incoming_stock_columns(merged_df)
        st.session_state["merged_df"] = merged_df

        st.success("Leverans mottagen! Lager & snittkostnad uppdaterad.")


def load_orders_from_file():
    """
    Läser in en fil "active_orders.csv" från disk och stoppar i st.session_state.all_orders.
    Om fil saknas -> tom dataframe.
    """
    if 'all_orders' not in st.session_state:
        st.session_state.all_orders = pd.DataFrame(columns=[
            'OrderDate',
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


if __name__ == "__main__":
    main()
