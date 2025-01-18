# app.py
#
# Flask-version av din applikation. 
# @app.before_first_request är borttaget (Flask 3.x)
# I stället kallas initialize_app() direkt i if __name__ == "__main__":-blocket.

from flask import Flask, request, render_template, redirect, url_for, flash
import os
import logging
import pandas as pd
from datetime import datetime, timedelta

# Importera hjälpfunktioner från våra andra filer
from data import (
    init_data_store,
    fetch_all_products_with_sales,
    load_orders_from_file,
    save_orders_to_file,
    create_new_delivery,
    cancel_delivery,
    handle_delivery_completion,
    get_active_deliveries_summary,
    get_completed_deliveries_summary,
    get_delivery_details,
    verify_active_delivery,
    get_current_stock_from_centra,
    test_stock_query
)
from sheets import push_to_google_sheets

app = Flask(__name__)
app.secret_key = "hemlig-nyckel"  # Byt ut mot något säkrare i produktion

# Konfiguration av logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def initialize_app():
    """
    Körs en gång när servern startar.
    Laddar/Initierar data och CSV:er m.m.
    """
    init_data_store()  # skapar product_costs.csv om den inte finns
    load_orders_from_file()  # laddar active_orders.csv om den finns
    logging.info("Applikationen är initierad!")


@app.route('/')
def index():
    """
    Enkel startsida.
    """
    return render_template("index.html")


# --------------------------------------------
# Statistik & Översikt
# --------------------------------------------
@app.route('/stats', methods=['GET', 'POST'])
def stats():
    """
    Visar formulär för datum, filter m.m. samt hämtar och visar tabell.
    Tillåter även push till Google Sheets.
    """
    # Förifyll datum
    today = datetime.today()
    default_from_date = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    default_to_date = today.strftime('%Y-%m-%d')

    if request.method == 'POST':
        # Hämta värden från formuläret
        from_date_str = request.form.get("from_date", default_from_date)
        to_date_str = request.form.get("to_date", default_to_date)
        active_filter = (request.form.get("active_filter") == "on")
        bundle_filter = (request.form.get("bundle_filter") == "on")
        exclude_supplier = (request.form.get("exclude_supplier") == "on")
        shipped_filter = (request.form.get("shipped_filter") == "on")
        lead_time = int(request.form.get("lead_time", 7))
        safety_stock = int(request.form.get("safety_stock", 10))

        # Hämta API env-variabler
        api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
        api_token = os.environ.get('CENTRA_API_TOKEN')
        if not api_endpoint or not api_token:
            flash("API-endpoint och/eller token saknas. Sätt miljövariabler!", "error")
            return redirect(url_for("stats"))

        # Hämta & processa data
        df = fetch_all_products_with_sales(
            api_endpoint=api_endpoint,
            api_token=api_token,
            from_date_str=from_date_str,
            to_date_str=to_date_str,
            lead_time=lead_time,
            safety_stock=safety_stock,
            only_shipped=shipped_filter
        )
        if df is None or df.empty:
            flash("Ingen data hittades eller fel vid hämtning.", "warning")
            return redirect(url_for("stats"))

        # Filtrera
        if active_filter and 'Status' in df.columns:
            df = df[df['Status'] == "ACTIVE"]
        if bundle_filter and 'Is Bundle' in df.columns:
            df = df[df['Is Bundle'] == False]
        if exclude_supplier and 'Supplier' in df.columns:
            df = df[df['Supplier'] != "Utgående produkt"]

        # Om ingen data efter filter
        if df.empty:
            flash("Inga produkter matchade dina filterval.", "warning")
            return redirect(url_for("stats"))

        # Lägg på apostrof framför product number för att undvika Excel-problem
        if 'Product Number' in df.columns:
            df['Product Number'] = "'" + df['Product Number'].astype(str)

        # Spara i global cache (DATAFRAME_CACHE) i data.py
        from data import DATAFRAME_CACHE
        DATAFRAME_CACHE["stats_df"] = df.copy()

        # Skicka med tabell till template
        return render_template(
            "stats.html",
            df_table=df.to_html(classes="table table-striped", index=False),
            from_date=from_date_str,
            to_date=to_date_str,
            active_filter=active_filter,
            bundle_filter=bundle_filter,
            exclude_supplier=exclude_supplier,
            shipped_filter=shipped_filter,
            lead_time=lead_time,
            safety_stock=safety_stock
        )
    else:
        # GET - visa tomt formulär
        return render_template(
            "stats.html",
            df_table=None,
            from_date=default_from_date,
            to_date=default_to_date,
            active_filter=True,
            bundle_filter=True,
            exclude_supplier=True,
            shipped_filter=True,
            lead_time=7,
            safety_stock=10
        )


@app.route('/stats/push_to_sheets', methods=['POST'])
def stats_push_to_sheets():
    """
    Knapp som pushar nuvarande df till Google Sheets.
    Kräver att vi har en df i DATAFRAME_CACHE["stats_df"].
    """
    from data import DATAFRAME_CACHE
    df = DATAFRAME_CACHE.get("stats_df")
    if df is None or df.empty:
        flash("Ingen data att pusha. Hämta data först!", "warning")
        return redirect(url_for("stats"))

    # Försök pusha
    sheet_name = f"Produkt_Försäljning_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    sheet_url = push_to_google_sheets(df, sheet_name)
    if sheet_url:
        flash(f"Data pushad till Google Sheets! Länk: {sheet_url}", "success")
    else:
        flash("Något gick fel vid push till Google Sheets.", "error")

    return redirect(url_for("stats"))


# --------------------------------------------
# Leveranser
# --------------------------------------------
@app.route('/deliveries', methods=['GET'])
def deliveries():
    """
    Huvudlista för leveranser: väntande och avklarade.
    Möjlighet att gå till skapande av ny leverans.
    """
    active_orders = get_active_deliveries_summary()
    completed_orders = get_completed_deliveries_summary()
    return render_template(
        "deliveries.html",
        active_orders=active_orders,
        completed_orders=completed_orders
    )


@app.route('/deliveries/create', methods=['GET', 'POST'])
def deliveries_create():
    """
    Skapa ny leverans via CSV-upload.
    """
    if request.method == 'POST':
        order_name = request.form.get("order_name", "").strip()
        csv_file = request.files.get("csv_file", None)

        if not order_name or not csv_file:
            flash("Du måste ange både namn och CSV-fil!", "error")
            return redirect(url_for("deliveries_create"))

        try:
            df = pd.read_csv(csv_file)
            created = create_new_delivery(order_name, df)
            if created:
                flash(f"Leverans '{order_name}' importerad!", "success")
                return redirect(url_for("deliveries"))
            else:
                flash("Fel vid skapande av leverans. Se log för detaljer.", "error")
                return redirect(url_for("deliveries_create"))
        except Exception as e:
            flash(f"Fel vid uppladdning av CSV: {str(e)}", "error")
            return redirect(url_for("deliveries_create"))

    return render_template("deliveries_create.html")


@app.route('/deliveries/cancel/<order_name>', methods=['GET', 'POST'])
def deliveries_cancel(order_name):
    """
    Makulera en väntande leverans med visst order_name.
    """
    if request.method == 'POST':
        cancel_delivery(order_name)
        flash(f"Leverans '{order_name}' makulerad!", "success")
        return redirect(url_for("deliveries"))
    else:
        # GET -> Bekräftelse
        return render_template("deliveries_cancel.html", order_name=order_name)


@app.route('/deliveries/process/<order_name>', methods=['GET', 'POST'])
def deliveries_process(order_name):
    """
    Mottag en existerande leverans (stäm av mottagna kvantiteter, etc).
    """
    from data import logger, DATAFRAME_CACHE
    logger.info(f"Försöker processa leverans: '{order_name}'")
    
    try:
        # Verifiera leveransen först
        is_valid, error_msg = verify_active_delivery(order_name)
        
        if not is_valid:
            flash(f"Fel vid hämtning av leverans: {error_msg}", "error")
            return redirect(url_for("deliveries"))
        
        if request.method == 'POST':
            rowcount = int(request.form.get("rowcount", 0))
            delivery_df = []
            for i in range(rowcount):
                product_id = request.form.get(f"product_id_{i}", "")
                size = request.form.get(f"size_{i}", "")
                quantity_ordered = request.form.get(f"quantity_ordered_{i}", "0")
                quantity_received = request.form.get(f"quantity_received_{i}", "0")
                purchase_price = request.form.get(f"purchase_price_{i}", "0")
                comment = request.form.get(f"comment_{i}", "")

                # Konvertera strängarna till float först och sedan till int för kvantiteterna
                try:
                    qty_ordered = int(float(quantity_ordered))
                    qty_received = int(float(quantity_received))
                except ValueError as e:
                    logger.error(f"Fel vid konvertering av kvantitet: {str(e)}")
                    qty_ordered = 0
                    qty_received = 0

                delivery_df.append({
                    "OrderName": order_name,
                    "ProductID": product_id,
                    "Size": size,
                    "Quantity ordered": qty_ordered,
                    "Mottagen mängd": qty_received,
                    "PurchasePrice": float(purchase_price),
                    "Kvalitet OK": True,  # Alltid satt till True nu
                    "Kommentar": comment
                })
            delivery_df = pd.DataFrame(delivery_df)

            # Hämta API-credentials
            api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
            api_token = os.environ.get('CENTRA_API_TOKEN')

            # Godkänn leverans
            handle_delivery_completion(delivery_df, api_endpoint, api_token)
            flash("Leverans mottagen och arkiverad!", "success")
            return redirect(url_for("deliveries"))
        
        # GET -> visa listan
        # Kolla först om vi har cachad data med lagersaldo
        cache_key = f"delivery_details_{order_name}"
        cached_details = DATAFRAME_CACHE.get(cache_key)
        
        if cached_details:
            logger.info(f"Använder cachad data för {order_name}: {cached_details}")
            details = cached_details
        else:
            details_df = get_delivery_details(order_name)
            if details_df is None or details_df.empty:
                flash(f"Ingen aktiv leverans hittades för namnet: '{order_name}'.", "error")
                return redirect(url_for("deliveries"))
            details = details_df.to_dict(orient="records")
        
        return render_template(
            "deliveries_process.html",
            order_name=order_name,
            details=details,
            rowcount=len(details)
        )
        
    except Exception as e:
        logger.error(f"Oväntat fel vid processande av leverans '{order_name}': {str(e)}")
        flash(f"Ett oväntat fel uppstod: {str(e)}", "error")
        return redirect(url_for("deliveries"))


@app.route('/deliveries/update_stock/<order_name>', methods=['POST'])
def update_current_stock(order_name):
    """
    Uppdaterar lagersaldo för alla produkter i en leverans genom att hämta från Centra
    """
    from data import logger
    logger.info(f"Uppdaterar lagersaldo för leverans: '{order_name}'")
    
    try:
        # Hämta API-credentials
        api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
        api_token = os.environ.get('CENTRA_API_TOKEN')
        
        if not api_endpoint or not api_token:
            flash("API-uppgifter saknas. Kontrollera miljövariabler.", "error")
            return redirect(url_for('deliveries_process', order_name=order_name))
        
        # Hämta leveransdetaljer
        details_df = get_delivery_details(order_name)
        if details_df is None or details_df.empty:
            flash("Kunde inte hitta leveransen.", "error")
            return redirect(url_for('deliveries'))
            
        # Uppdatera lagersaldo för varje produkt
        updated_details = []
        for _, row in details_df.iterrows():
            # Hämta aktuellt lagersaldo från Centra
            current_stock = get_current_stock_from_centra(
                api_endpoint, 
                api_token, 
                row['ProductID'], 
                row['Size']
            )
            logger.info(f"Hämtat lagersaldo för {row['ProductID']} {row['Size']}: {current_stock}")
            
            # Skapa en kopia av raden och uppdatera Current Stock
            row_dict = row.to_dict()
            row_dict['Current Stock'] = current_stock
            updated_details.append(row_dict)
        
        # Spara uppdaterad data i session
        from data import DATAFRAME_CACHE
        cache_key = f"delivery_details_{order_name}"
        DATAFRAME_CACHE[cache_key] = updated_details
        logger.info(f"Uppdaterade detaljer sparade i cache: {updated_details}")
        
        flash(f"Lagersaldo uppdaterat från Centra! Totalt {len(updated_details)} produkter.", "success")
        
    except Exception as e:
        logger.error(f"Fel vid uppdatering av lagersaldo: {str(e)}")
        flash(f"Kunde inte uppdatera lagersaldo: {str(e)}", "error")
    
    return redirect(url_for('deliveries_process', order_name=order_name))


@app.route('/test_stock/<product_id>/<size>')
def test_stock(product_id, size):
    api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
    api_token = os.environ.get('CENTRA_API_TOKEN')
    
    if not api_endpoint or not api_token:
        return "API credentials saknas", 400
        
    stock = test_stock_query(api_endpoint, api_token, product_id, size)
    return f"Stock för {product_id} storlek {size}: {stock}"


@app.route('/deliveries/reactivate/<order_name>', methods=['POST'])
def deliveries_reactivate(order_name):
    """
    Återaktiverar en avklarad leverans
    """
    from data import logger, ALL_ORDERS_DF
    logger.info(f"Försöker återaktivera leverans: {order_name}")
    
    try:
        # Hitta leveransen och sätt IsActive = True
        mask = ALL_ORDERS_DF['OrderName'] == order_name
        if not any(mask):
            flash(f"Kunde inte hitta leverans: {order_name}", "error")
            return redirect(url_for("deliveries"))
            
        ALL_ORDERS_DF.loc[mask, 'IsActive'] = True
        from data import save_orders_to_file
        save_orders_to_file()
        
        flash(f"Leverans {order_name} återaktiverad!", "success")
        
    except Exception as e:
        logger.error(f"Fel vid återaktivering av leverans: {str(e)}")
        flash(f"Kunde inte återaktivera leverans: {str(e)}", "error")
        
    return redirect(url_for("deliveries"))


@app.route('/deliveries/view/<order_name>')
def deliveries_view(order_name):
    """
    Visa detaljer för en avklarad leverans
    """
    from data import logger, ALL_ORDERS_DF
    
    try:
        # Hämta leveransdetaljer
        details_df = ALL_ORDERS_DF[ALL_ORDERS_DF['OrderName'] == order_name].copy()
        if details_df.empty:
            flash("Kunde inte hitta leveransen.", "error")
            return redirect(url_for("deliveries"))
            
        # Logga kolumnnamn och första raden för debugging
        logger.info(f"Kolumner i details_df: {details_df.columns.tolist()}")
        if not details_df.empty:
            logger.info(f"Första raden: {details_df.iloc[0].to_dict()}")
        
        # Standardisera kolumnnamnen
        if 'Quantity received' in details_df.columns:
            details_df['Mottagen mängd'] = details_df['Quantity received']
        if 'Comment' in details_df.columns:
            details_df['Kommentar'] = details_df['Comment']
            
        details = details_df.to_dict(orient="records")
        return render_template(
            "deliveries_view.html",
            order_name=order_name,
            details=details
        )
        
    except Exception as e:
        logger.error(f"Fel vid visning av leverans: {str(e)}")
        flash(f"Kunde inte visa leveransdetaljer: {str(e)}", "error")
        return redirect(url_for("deliveries"))


if __name__ == "__main__":
    # Anropa vår init-funktion före app.run()
    initialize_app()
    # Starta Flask-server
    app.run(host='0.0.0.0', port=5000, debug=True)
