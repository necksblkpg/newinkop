# app.py

from flask import Flask, request, render_template, redirect, url_for, flash, jsonify, session, send_file
import os
import logging
import pandas as pd
from datetime import datetime, timedelta
import io
import csv

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
    PRICE_LISTS_FILE,
    fetch_all_suppliers,
    fetch_collections_and_products
)
from sheets import push_to_google_sheets

app = Flask(__name__)
app.secret_key = "hemlig-nyckel"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def initialize_app():
    init_data_store()
    load_orders_from_file()
    logging.info("Applikationen är initierad!")


@app.route('/')
def index():
    return render_template("index.html")


# --------------------------------------------
# Statistik & Översikt
# --------------------------------------------
@app.route('/stats', methods=['GET', 'POST'])
def stats():
    today = datetime.today()
    default_from_date = (today - timedelta(days=30)).strftime('%Y-%m-%d')
    default_to_date = today.strftime('%Y-%m-%d')

    api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
    api_token = os.environ.get('CENTRA_API_TOKEN')

    all_suppliers = []
    all_collections = []

    if api_endpoint and api_token:
        try:
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_token}"
            }
            # 1) Hämta leverantörer
            supplier_list = fetch_all_suppliers(api_endpoint, headers)
            if supplier_list:
                all_suppliers = [s['name'] for s in supplier_list if s.get('status') == 'ACTIVE']

            # 2) Hämta collections => plocka ut unika collection-namn
            product_map = fetch_collections_and_products(api_endpoint, headers) or {}
            unique_coll = set()
            for cset in product_map.values():
                for c in cset:
                    unique_coll.add(c)
            all_collections = sorted(list(unique_coll))

        except Exception as e:
            logging.error(f"Fel vid hämtning av suppliers/collections: {str(e)}")
            flash("Kunde inte hämta leverantörer eller collections. Kontrollera API-inställningar.", "error")

    if request.method == 'POST':
        from_date_str = request.form.get("from_date", default_from_date)
        to_date_str = request.form.get("to_date", default_to_date)
        active_filter = (request.form.get("active_filter") == "on")
        bundle_filter = (request.form.get("bundle_filter") == "on")
        shipped_filter = (request.form.get("shipped_filter") == "on")
        lead_time = int(request.form.get("lead_time", 7))
        safety_stock = int(request.form.get("safety_stock", 10))

        selected_suppliers = request.form.getlist("suppliers")
        selected_collections = request.form.getlist("collections")

        if not api_endpoint or not api_token:
            flash("API-endpoint och/eller token saknas. Sätt miljövariabler!", "error")
            return redirect(url_for("stats"))

        active_orders = get_active_deliveries_summary()

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

        if active_filter and 'Status' in df.columns:
            df = df[df['Status'] == "ACTIVE"]
        if bundle_filter and 'Is Bundle' in df.columns:
            df = df[df['Is Bundle'] == False]

        if selected_suppliers and 'Supplier' in df.columns:
            df = df[df['Supplier'].isin(selected_suppliers)]

        if selected_collections and 'Collections' in df.columns:
            def has_overlap(collection_list):
                return bool(set(collection_list).intersection(selected_collections))
            df = df[df['Collections'].apply(has_overlap)]

        if df.empty:
            flash("Inga produkter matchade dina filterval.", "warning")
            return redirect(url_for("stats"))

        # Lägg på apostrof framför product number för Excel
        if 'Product Number' in df.columns:
            df['Product Number'] = "'" + df['Product Number'].astype(str)

        from data import DATAFRAME_CACHE
        DATAFRAME_CACHE["stats_df"] = df.copy()

        preview_df = df.head(3)

        return render_template(
            "stats.html",
            df_table=df.to_html(classes="table table-striped", index=False),
            preview_table=preview_df.to_html(classes="table table-striped", index=False),
            total_rows=len(df),
            active_orders=active_orders,
            from_date=from_date_str,
            to_date=to_date_str,
            active_filter=active_filter,
            bundle_filter=bundle_filter,
            shipped_filter=shipped_filter,
            lead_time=lead_time,
            safety_stock=safety_stock,
            suppliers=all_suppliers,
            selected_suppliers=selected_suppliers,
            collections=all_collections,
            selected_collections=selected_collections
        )
    else:
        active_orders = get_active_deliveries_summary()
        return render_template(
            "stats.html",
            df_table=None,
            active_orders=active_orders,
            from_date=default_from_date,
            to_date=default_to_date,
            active_filter=True,
            bundle_filter=True,
            shipped_filter=True,
            lead_time=7,
            safety_stock=10,
            suppliers=all_suppliers,
            selected_suppliers=[],
            collections=all_collections,
            selected_collections=[]
        )


@app.route('/stats/push_to_sheets', methods=['POST'])
def stats_push_to_sheets():
    from data import DATAFRAME_CACHE
    df = DATAFRAME_CACHE.get("stats_df")
    if df is None or df.empty:
        return jsonify({'error': 'Ingen data att exportera'}), 400

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
    active_orders = get_active_deliveries_summary()
    completed_orders = get_completed_deliveries_summary()
    return render_template(
        "deliveries.html",
        active_orders=active_orders,
        completed_orders=completed_orders
    )


@app.route('/deliveries/create', methods=['GET', 'POST'])
def deliveries_create():
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
    if request.method == 'POST':
        cancel_delivery(order_name)
        flash(f"Leverans '{order_name}' makulerad!", "success")
        return redirect(url_for("deliveries"))
    else:
        return render_template("deliveries_cancel.html", order_name=order_name)


@app.route('/deliveries/process/<order_name>', methods=['GET', 'POST'])
def deliveries_process(order_name):
    from data import logger, DATAFRAME_CACHE

    try:
        is_valid, error_msg = verify_active_delivery(order_name)

        if not is_valid:
            flash(f"Fel vid hämtning av leverans: {error_msg}", "error")
            return redirect(url_for("deliveries"))

        api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
        api_token = os.environ.get('CENTRA_API_TOKEN')

        if not api_endpoint or not api_token:
            flash("API-uppgifter saknas. Kontrollera miljövariabler.", "error")
            return redirect(url_for('deliveries'))

        if request.method == 'POST':
            delivery_data = []
            rowcount = int(request.form.get('rowcount', 0))
            
            for i in range(rowcount):
                row_data = {
                    'ProductID': request.form.get(f'product_id_{i}'),
                    'Size': request.form.get(f'size_{i}'),
                    'Mottagen mängd': float(request.form.get(f'mottagen_mangd_{i}', 0)),
                    'Price': float(request.form.get(f'price_{i}', 0)),
                    'Currency': request.form.get(f'currency_{i}'),
                    'Exchange rate': float(request.form.get(f'exchange_rate_{i}', 0)),
                    'Shipping': float(request.form.get(f'shipping_{i}', 0)),
                    'Customs': float(request.form.get(f'customs_{i}', 0)),
                    'new_avg_cost': float(request.form.get(f'new_avg_cost_{i}', 0)),
                    'OrderName': order_name
                }
                delivery_data.append(row_data)
            
            delivery_df = pd.DataFrame(delivery_data)
            handle_delivery_completion(delivery_df)
            flash("Leverans mottagen och arkiverad!", "success")
            return redirect(url_for("deliveries_view", order_name=order_name))

        details_df = get_delivery_details(order_name)
        if details_df is None or details_df.empty:
            flash(f"Ingen aktiv leverans hittades för namnet: '{order_name}'.", "error")
            return redirect(url_for("deliveries"))

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
        logging.error(f"Oväntat fel vid processande av leverans '{order_name}': {str(e)}")
        flash(f"Ett oväntat fel uppstod: {str(e)}", "error")
        return redirect(url_for("deliveries"))


@app.route('/deliveries/update_stock/<order_name>', methods=['POST'])
def update_current_stock(order_name):
    from data import logger
    logger.info(f"Uppdaterar lagersaldo för leverans: '{order_name}'")

    try:
        api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
        api_token = os.environ.get('CENTRA_API_TOKEN')

        if not api_endpoint or not api_token:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'API-uppgifter saknas'}), 400
            flash("API-uppgifter saknas. Kontrollera miljövariabler.", "error")
            return redirect(url_for('deliveries_process', order_name=order_name))

        details_df = get_delivery_details(order_name)
        if details_df is None or details_df.empty:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return jsonify({'error': 'Kunde inte hitta leveransen'}), 404
            flash("Kunde inte hitta leveransen.", "error")
            return redirect(url_for('deliveries'))

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
    from data import logger, ALL_ORDERS_DF
    logger.info(f"Försöker återaktivera leverans: {order_name}")

    try:
        order_mask = ALL_ORDERS_DF['OrderName'].astype(str) == str(order_name)
        if not any(order_mask):
            flash(f"Kunde inte hitta leverans: {order_name}", "error")
            return redirect(url_for("deliveries"))

        ALL_ORDERS_DF.loc[order_mask, 'IsActive'] = True
        ALL_ORDERS_DF['IsActive'] = ALL_ORDERS_DF['IsActive'].astype(bool)

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
    from data import logger, get_delivery_details

    try:
        details_df = get_delivery_details(order_name, only_active=False)
        if details_df is None or details_df.empty:
            flash("Kunde inte hitta leveransen.", "error")
            return redirect(url_for("deliveries"))

        is_active = all(details_df['IsActive'])

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
    try:
        details_df = get_delivery_details(order_name, only_active=False)
        if details_df is None or details_df.empty:
            flash("Kunde inte hitta leveransen.", "error")
            return redirect(url_for("deliveries"))

        # Skapa en ny dataframe med bara de kolumner vi vill exportera
        export_df = details_df[['Product Number', 'Mottagen mängd', 'new_avg_cost']].copy()
        export_df.columns = ['Artikelnummer', 'Mottagen mängd', 'Nytt snitt SEK']
        
        # Exportera till Google Sheets
        sheet_url = push_to_google_sheets(export_df, f"Leverans {order_name}")
        
        if sheet_url:
            return jsonify({'success': True, 'sheet_url': sheet_url})
        else:
            return jsonify({'success': False, 'message': 'Kunde inte skapa Google Sheet'})

    except Exception as e:
        logging.error(f"Fel vid export av leveransdetaljer: {str(e)}")
        return jsonify({'success': False, 'message': str(e)})


@app.route('/price_lists')
def price_lists():
    price_list_exists = os.path.exists(PRICE_LISTS_FILE)
    last_updated = None
    current_price_list = []

    if price_list_exists:
        last_updated = datetime.fromtimestamp(os.path.getmtime(PRICE_LISTS_FILE)).strftime('%Y-%m-%d %H:%M:%S')
        try:
            df = pd.read_csv(PRICE_LISTS_FILE)
            current_price_list = df.to_dict('records')
        except Exception as e:
            logging.error(f"Fel vid läsning av prislista: {str(e)}")

    return render_template('price_lists.html', 
                         price_list_exists=price_list_exists,
                         last_updated=last_updated,
                         current_price_list=current_price_list)


@app.route('/price_lists/upload', methods=['POST'])
def upload_price_list():
    try:
        file = request.files['price_list']

        if file and file.filename.endswith('.csv'):
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
    data = request.get_json()
    product_id = data.get('product_id')
    size = data.get('size')
    
    if not product_id or not size:
        return jsonify({
            'success': False,
            'message': 'Produkt-ID och storlek krävs'
        })
    
    try:
        price_data = find_price_in_list(product_id, None, size)
        if price_data:
            return jsonify({
                'success': True,
                'price': price_data['price'],
                'currency': price_data['currency']
            })
        return jsonify({
            'success': False,
            'message': 'Inget pris hittat i prislistan'
        })
    except Exception as e:
        logger.error(f"Fel vid hämtning av pris: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Ett fel uppstod vid hämtning av pris'
        })


@app.route('/deliveries/get_stock', methods=['POST'])
def get_stock():
    data = request.get_json()
    product_id = data.get('product_id')
    size = data.get('size')
    
    if not product_id or not size:
        return jsonify({
            'success': False,
            'message': 'Produkt-ID och storlek krävs'
        })
    
    try:
        api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
        api_token = os.environ.get('CENTRA_API_TOKEN')
        
        if not api_endpoint or not api_token:
            return jsonify({
                'success': False,
                'message': 'API-uppgifter saknas'
            })
        
        current_stock = get_current_stock_from_centra(
            api_endpoint, 
            api_token, 
            product_id, 
            size
        )
        
        return jsonify({
            'success': True,
            'stock': current_stock
        })
        
    except Exception as e:
        logger.error(f"Fel vid hämtning av lagersaldo: {str(e)}")
        return jsonify({
            'success': False,
            'message': 'Ett fel uppstod vid hämtning av lagersaldo'
        })


@app.route('/price_lists/update', methods=['POST'])
def update_price_list():
    try:
        # Läs in existerande prislista
        df = pd.read_csv(PRICE_LISTS_FILE)
        
        # Uppdatera värden från formuläret
        for i in range(len(df)):
            price = request.form.get(f'price_{i}')
            currency = request.form.get(f'currency_{i}')
            product_id = request.form.get(f'product_id_{i}')
            size = request.form.get(f'size_{i}')
            
            mask = (df['ProductID'].astype(str) == str(product_id)) & (df['Size'].astype(str) == str(size))
            if any(mask):
                df.loc[mask, 'Price'] = float(price)
                df.loc[mask, 'Currency'] = currency
        
        # Spara uppdaterad prislista
        df.to_csv(PRICE_LISTS_FILE, index=False)
        flash("Prislistan har uppdaterats", "success")
        
    except Exception as e:
        flash(f"Fel vid uppdatering av prislista: {str(e)}", "error")
    
    return redirect(url_for('price_lists'))


@app.route('/price_lists/delete_item', methods=['POST'])
def delete_price_list_item():
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        size = data.get('size')
        
        df = pd.read_csv(PRICE_LISTS_FILE)
        mask = (df['ProductID'].astype(str) == str(product_id)) & (df['Size'].astype(str) == str(size))
        
        if not any(mask):
            return jsonify({
                'success': False,
                'message': 'Produkten hittades inte i prislistan'
            })
            
        df = df[~mask]
        df.to_csv(PRICE_LISTS_FILE, index=False)
        
        return jsonify({
            'success': True,
            'message': 'Produkt borttagen från prislistan'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'message': str(e)
        })


@app.route('/price_lists/add_item', methods=['POST'])
def add_price_list_item():
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'Ingen data skickades'}), 400

        required_fields = ['product_id', 'size', 'price', 'currency']
        if not all(field in data for field in required_fields):
            return jsonify({'success': False, 'message': 'Saknade fält i data'}), 400

        # Läs in befintlig prislista
        price_list_df = pd.read_csv(PRICE_LISTS_FILE) if os.path.exists(PRICE_LISTS_FILE) else pd.DataFrame(columns=required_fields)

        # Kontrollera om produkten redan finns
        mask = (price_list_df['ProductID'].astype(str) == str(data['product_id'])) & \
               (price_list_df['Size'].astype(str) == str(data['size']))
        
        if any(mask):
            return jsonify({'success': False, 'message': 'Produkten finns redan i prislistan'}), 400

        # Lägg till ny rad
        new_row = pd.DataFrame([{
            'ProductID': str(data['product_id']),
            'Size': str(data['size']),
            'Price': float(data['price']),
            'Currency': str(data['currency'])
        }])
        
        price_list_df = pd.concat([price_list_df, new_row], ignore_index=True)
        price_list_df.to_csv(PRICE_LISTS_FILE, index=False)

        return jsonify({'success': True})

    except Exception as e:
        logger.error(f"Fel vid tillägg av produkt i prislista: {str(e)}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/search_products')
def search_products():
    try:
        query = request.args.get('query', '').strip()
        if not query or len(query) < 2:
            return jsonify({'products': []})

        api_endpoint = os.environ.get('YOUR_API_ENDPOINT')
        api_token = os.environ.get('CENTRA_API_TOKEN')

        if not api_endpoint or not api_token:
            return jsonify({'products': []})

        # Hämta produkter från Centra som matchar sökningen
        products_df = fetch_all_products(api_endpoint, api_token)
        if products_df is None or products_df.empty:
            return jsonify({'products': []})

        # Filtrera baserat på ProductID eller Product Name
        mask = (products_df['ProductID'].astype(str).str.contains(query, case=False)) | \
               (products_df['Product Name'].astype(str).str.contains(query, case=False))
        
        filtered_df = products_df[mask].head(10)  # Begränsa till 10 resultat
        
        results = filtered_df.apply(lambda row: {
            'ProductID': str(row['ProductID']),
            'Product_Name': str(row['Product Name']),
            'Size': str(row['Size'])
        }, axis=1).tolist()

        return jsonify({'products': results})

    except Exception as e:
        logger.error(f"Fel vid produktsökning: {str(e)}")
        return jsonify({'products': []})


if __name__ == "__main__":
    initialize_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
