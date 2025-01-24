# app.py
#
# Flask-version av din applikation. 
# @app.before_first_request är borttaget (Flask 3.x)
# I stället kallas initialize_app() direkt i if __name__ == "__main__":-blocket.

from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, session, send_file
import os
import logging
import pandas as pd
from datetime import datetime, timedelta
import io
import csv

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
    test_stock_query,
    get_price_lists,
    save_price_list,
    find_price_in_list,
    delete_price_list,
    PRICE_LISTS_FILE
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

        # Hämta aktiva ordrar
        active_orders = get_active_deliveries_summary()

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

        # Spara hela dataframen i cache
        from data import DATAFRAME_CACHE
        DATAFRAME_CACHE["stats_df"] = df.copy()

        # Skapa en förhandsvisning med bara 3 rader
        preview_df = df.head(3)

        return render_template(
            "stats.html",
            df_table=df.to_html(classes="table table-striped", index=False),  # Full data för referens
            preview_table=preview_df.to_html(classes="table table-striped", index=False),  # Förhandsvisning
            total_rows=len(df),  # Totalt antal rader
            active_orders=active_orders,
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
        # Hämta aktiva ordrar även för GET request
        active_orders = get_active_deliveries_summary()
        
        return render_template(
            "stats.html",
            df_table=None,
            active_orders=active_orders,
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
    Pushar nuvarande df till Google Sheets och returnerar URL:en.
    """
    from data import DATAFRAME_CACHE
    df = DATAFRAME_CACHE.get("stats_df")
    if df is None or df.empty:
        return jsonify({'error': 'Ingen data att exportera'}), 400

    # Försök pusha
    sheet_name = f"Produkt_Försäljning_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    sheet_url = push_to_google_sheets(df, sheet_name)
    if sheet_url:
        return jsonify({'sheet_url': sheet_url})
    else:
        return jsonify({'error': 'Kunde inte skapa Google Sheet'}), 500


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
    
    try:
        # Verifiera leveransen först
        is_valid, error_msg = verify_active_delivery(order_name)
        
        if not is_valid:
            flash(f"Fel vid hämtning av leverans: {error_msg}", "error")
            return redirect(url_for("deliveries"))

        # Hämta API-credentials
        api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
        api_token = os.environ.get('CENTRA_API_TOKEN')
        
        if not api_endpoint or not api_token:
            flash("API-uppgifter saknas. Kontrollera miljövariabler.", "error")
            return redirect(url_for('deliveries'))

        # Hantera POST-request (godkänn leverans)
        if request.method == 'POST':
            rowcount = int(request.form.get("rowcount", 0))
            delivery_df = []
            for i in range(rowcount):
                product_id = request.form.get(f"product_id_{i}", "")
                size = request.form.get(f"size_{i}", "")
                quantity_ordered = request.form.get(f"quantity_ordered_{i}", "0")
                quantity_received = request.form.get(f"quantity_received_{i}", "0")
                purchase_price = request.form.get(f"purchase_price_{i}", "0")
                
                # Nya fält
                price = request.form.get(f"price_{i}", "0")
                currency = request.form.get(f"currency_{i}", "")
                exchange_rate = request.form.get(f"exchange_rate_{i}", "0")
                shipping = request.form.get(f"shipping_{i}", "0")
                customs = request.form.get(f"customs_{i}", "0")

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
                    "Price": float(price),
                    "Currency": currency,
                    "Exchange rate": float(exchange_rate),
                    "Shipping": float(shipping),
                    "Customs": float(customs),
                    "new_price_sek": float(request.form.get(f"new_price_sek_{i}") or 0),
                    "new_avg_cost": float(request.form.get(f"new_avg_cost_{i}") or 0)
                })
            delivery_df = pd.DataFrame(delivery_df)

            # Godkänn leverans
            handle_delivery_completion(delivery_df, api_endpoint, api_token)
            flash("Leverans mottagen och arkiverad!", "success")
            return redirect(url_for("deliveries_view", order_name=order_name))
            
        # GET-request - visa formulär
        details_df = get_delivery_details(order_name)
        if details_df is None or details_df.empty:
            flash(f"Ingen aktiv leverans hittades för namnet: '{order_name}'.", "error")
            return redirect(url_for("deliveries"))
            
        # Hämta produktinformation från Centra om det behövs
        if 'Product Number' not in details_df.columns:
            # Lägg till Product Number från Centra API eller annan datakälla
            # Detta beror på hur din data är strukturerad
            pass
        
        # Uppdatera lagersaldo för varje produkt
        updated_details = []
        for _, row in details_df.iterrows():
            current_stock = get_current_stock_from_centra(
                api_endpoint, 
                api_token, 
                row['ProductID'], 
                row['Size']
            )
            logger.info(f"Hämtat lagersaldo för {row['ProductID']} {row['Size']}: {current_stock}")
            
            row_dict = row.to_dict()
            row_dict['Current Stock'] = current_stock
            updated_details.append(row_dict)
        
        # Spara uppdaterad data i cache
        cache_key = f"delivery_details_{order_name}"
        DATAFRAME_CACHE[cache_key] = updated_details
        
        return render_template(
            "deliveries_process.html",
            order_name=order_name,
            details=updated_details,
            rowcount=len(updated_details),
            needs_stock_update=False
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
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'API-uppgifter saknas'}), 400
            flash("API-uppgifter saknas. Kontrollera miljövariabler.", "error")
            return redirect(url_for('deliveries_process', order_name=order_name))
        
        # Hämta leveransdetaljer
        details_df = get_delivery_details(order_name)
        if details_df is None or details_df.empty:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'Kunde inte hitta leveransen'}), 404
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
        
        # Spara uppdaterad data i cache
        from data import DATAFRAME_CACHE
        cache_key = f"delivery_details_{order_name}"
        DATAFRAME_CACHE[cache_key] = updated_details
        
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'success': True})
            
        flash(f"Lagersaldo uppdaterat från Centra! Totalt {len(updated_details)} produkter.", "success")
        
    except Exception as e:
        logger.error(f"Fel vid uppdatering av lagersaldo: {str(e)}")
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return jsonify({'error': str(e)}), 500
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
        order_mask = ALL_ORDERS_DF['OrderName'].astype(str) == str(order_name)
        if not any(order_mask):
            flash(f"Kunde inte hitta leverans: {order_name}", "error")
            return redirect(url_for("deliveries"))
            
        # Sätt IsActive till True och spara
        ALL_ORDERS_DF.loc[order_mask, 'IsActive'] = True
        ALL_ORDERS_DF['IsActive'] = ALL_ORDERS_DF['IsActive'].astype(bool)  # Säkerställ boolean
        
        from data import save_orders_to_file
        save_orders_to_file()
        
        logger.info(f"Leverans {order_name} återaktiverad")
        flash(f"Leverans {order_name} återaktiverad!", "success")
        
    except Exception as e:
        logger.error(f"Fel vid återaktivering av leverans: {str(e)}")
        flash(f"Kunde inte återaktivera leverans: {str(e)}", "error")
        
    return redirect(url_for("deliveries"))


@app.route('/deliveries/view/<order_name>')
def deliveries_view(order_name):
    """
    Visa detaljer för en leverans (aktiv eller avklarad)
    """
    from data import logger, get_delivery_details
    
    try:
        # Hämta leveransdetaljer utan att filtrera på IsActive
        details_df = get_delivery_details(order_name, only_active=False)
        if details_df is None or details_df.empty:
            flash("Kunde inte hitta leveransen.", "error")
            return redirect(url_for("deliveries"))
            
        # Kolla om leveransen är aktiv
        is_active = any(details_df['IsActive'])
        
        # Logga för debugging
        logger.info(f"Visar detaljer för leverans {order_name}")
        logger.info(f"IsActive status: {details_df['IsActive'].tolist()}")
        
        details = details_df.to_dict(orient="records")
        return render_template(
            "deliveries_view.html",
            order_name=order_name,
            details=details,
            is_active=is_active
        )
        
    except Exception as e:
        logger.error(f"Fel vid visning av leverans: {str(e)}")
        flash(f"Kunde inte visa leveransdetaljer: {str(e)}", "error")
        return redirect(url_for("deliveries"))


@app.route('/deliveries/export/<order_name>')
def export_delivery_details(order_name):
    """Exportera leveransdetaljer till CSV"""
    try:
        # Hämta leveransdetaljer
        details_df = get_delivery_details(order_name, only_active=False)
        if details_df is None or details_df.empty:
            flash("Kunde inte hitta leveransen.", "error")
            return redirect(url_for("deliveries"))

        # Skapa CSV i minnet
        output = io.StringIO()
        writer = csv.writer(output)
        
        # Skriv headers
        writer.writerow(['Artikelnummer', 'Mottagen mängd', 'Nytt snitt SEK'])
        
        # Skriv data
        for _, row in details_df.iterrows():
            writer.writerow([
                row.get('Product Number', ''),
                row.get('Mottagen mängd', 0),
                f"{row.get('new_avg_cost', 0):.2f}"
            ])
        
        # Förbered filen för nedladdning
        output.seek(0)
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            mimetype='text/csv',
            as_attachment=True,
            download_name=f'leverans_{order_name}_{datetime.now().strftime("%Y%m%d")}.csv'
        )
        
    except Exception as e:
        logger.error(f"Fel vid export av leveransdetaljer: {str(e)}")
        flash(f"Kunde inte exportera leveransdetaljer: {str(e)}", "error")
        return redirect(url_for("deliveries_view", order_name=order_name))


@app.route('/price_lists')
def price_lists():
    """Visa prislistor"""
    price_list_exists = os.path.exists(PRICE_LISTS_FILE)
    last_updated = None
    current_price_list = []
    
    if price_list_exists:
        last_updated = datetime.fromtimestamp(os.path.getmtime(PRICE_LISTS_FILE)).strftime('%Y-%m-%d %H:%M:%S')
        try:
            df = pd.read_csv(PRICE_LISTS_FILE)
            current_price_list = df.to_dict('records')
        except Exception as e:
            logger.error(f"Fel vid läsning av prislista: {str(e)}")
    
    return render_template('price_lists.html', 
                         price_list_exists=price_list_exists,
                         last_updated=last_updated,
                         current_price_list=current_price_list)

@app.route('/price_lists/upload', methods=['POST'])
def upload_price_list():
    """Hantera uppladdning av prislista"""
    try:
        file = request.files['price_list']
        
        if file and file.filename.endswith('.csv'):
            # Läs CSV med explicit datatyper
            df = pd.read_csv(
                file,
                dtype={
                    'ProductID': str,
                    'Size': str,
                    'Price': float,
                    'Currency': str
                }
            )
            save_price_list(df)
            flash(f"Prislista har laddats upp med {len(df)} produkter", "success")
        else:
            flash("Ogiltig fil. Endast CSV-filer är tillåtna.", "error")
            
    except Exception as e:
        flash(f"Fel vid uppladdning: {str(e)}", "error")
        
    return redirect(url_for('price_lists'))

@app.route('/price_lists/get_price', methods=['POST'])
def get_price():
    """Hämta pris från prislista"""
    data = request.json
    result = find_price_in_list(
        data['product_id'],
        data['product_number'],
        data['size']
    )
    if result:
        # Returnera både pris och valuta
        return jsonify({
            'success': True,
            'price': result['price'],
            'currency': result['currency']
        })
    return jsonify({
        'success': False,
        'message': 'Inget pris hittades i prislistan'
    })


if __name__ == "__main__":
    # Anropa vår init-funktion före app.run()
    initialize_app()
    # Starta Flask-server
    app.run(host='0.0.0.0', port=5000, debug=True)
