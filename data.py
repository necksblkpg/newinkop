# data.py

import os
import requests
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

# Globala variabler för att efterlikna st.session_state
ALL_ORDERS_DF = pd.DataFrame(columns=[
    'OrderDate',
    'OrderName',
    'ProductID',
    'Size',
    'Quantity ordered',
    'IsActive'
])
DATAFRAME_CACHE = {}  # t.ex. {"stats_df": df}

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Filnamn för ordrar och kostnader
ACTIVE_ORDERS_FILE = "active_orders.csv"
PRODUCT_COSTS_FILE = "product_costs.csv"

# -----------------------------------------------------------
# 1) Snittkostnader (product_costs.csv)
# -----------------------------------------------------------

def init_data_store():
    """
    Initierar product_costs.csv om den inte finns
    (dvs skapar en tom fil med korrekta kolumner).
    """
    if not os.path.isfile(PRODUCT_COSTS_FILE):
        df = pd.DataFrame(columns=["ProductID", "AvgCost", "LastUpdated"])
        df.to_csv(PRODUCT_COSTS_FILE, index=False)
        logger.info("Skapade tom product_costs.csv.")
    else:
        logger.info("product_costs.csv finns redan.")

def load_product_costs():
    if not os.path.isfile(PRODUCT_COSTS_FILE):
        return pd.DataFrame(columns=["ProductID", "AvgCost", "LastUpdated"])

    try:
        df = pd.read_csv(PRODUCT_COSTS_FILE, dtype={"ProductID": str})
        if "AvgCost" not in df.columns:
            df["AvgCost"] = 0.0
        if "LastUpdated" not in df.columns:
            df["LastUpdated"] = ""
        return df
    except Exception as e:
        logger.error(f"Fel vid laddning av {PRODUCT_COSTS_FILE}: {str(e)}")
        return pd.DataFrame(columns=["ProductID", "AvgCost", "LastUpdated"])

def save_product_costs(df):
    try:
        df.to_csv(PRODUCT_COSTS_FILE, index=False)
    except Exception as e:
        logger.error(f"Fel vid sparning av {PRODUCT_COSTS_FILE}: {str(e)}")

def get_current_avg_cost(product_id):
    """Returnerar nuvarande snittkostnad för en viss product_id, annars 0."""
    cost_df = load_product_costs()
    row = cost_df[cost_df["ProductID"] == product_id]
    if row.empty:
        return 0.0
    return float(row["AvgCost"].iloc[0])

def update_avg_cost(product_id, new_cost):
    """
    Uppdaterar snittkostnaden för en produkt.
    """
    cost_df = load_product_costs()
    idx = cost_df[cost_df["ProductID"] == product_id].index
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    if len(idx) == 0:
        # Skapa ny rad
        new_row = pd.DataFrame([{
            "ProductID": product_id,
            "AvgCost": new_cost,
            "LastUpdated": now_str
        }])
        cost_df = pd.concat([cost_df, new_row], ignore_index=True)
    else:
        cost_df.loc[idx, "AvgCost"] = new_cost
        cost_df.loc[idx, "LastUpdated"] = now_str

    save_product_costs(cost_df)

# -----------------------------------------------------------
# 2) Funktioner för att hantera ordrar (active_orders.csv)
# -----------------------------------------------------------

def load_orders_from_file():
    """
    Läser in en fil "active_orders.csv" och stoppar i ALL_ORDERS_DF.
    Om fil saknas -> tom dataframe.
    """
    global ALL_ORDERS_DF
    
    # Definiera alla kolumner som ska finnas
    required_columns = [
        "OrderDate",
        "OrderName",
        "ProductID",
        "Size",
        "Quantity ordered",
        "Mottagen mängd",  # Lägg till denna
        "Kommentar",       # Lägg till denna
        "PurchasePrice",
        "IsActive"
    ]
    
    if os.path.isfile(ACTIVE_ORDERS_FILE):
        try:
            df = pd.read_csv(ACTIVE_ORDERS_FILE, dtype={
                "ProductID": str, 
                "Size": str,
                "Mottagen mängd": float,  # Lägg till denna
                "Kommentar": str          # Lägg till denna
            })
            
            # Lägg till saknade kolumner med standardvärden
            if "IsActive" not in df.columns:
                df["IsActive"] = True
            if "OrderName" not in df.columns:
                df["OrderName"] = "Beställning " + df["OrderDate"]
            if "Mottagen mängd" not in df.columns:
                df["Mottagen mängd"] = df["Quantity ordered"]  # Sätt till beställd mängd som default
            if "Kommentar" not in df.columns:
                df["Kommentar"] = ""  # Tom sträng som default
                
            df['Quantity ordered'] = pd.to_numeric(df['Quantity ordered'], errors='coerce').fillna(0)
            df['Mottagen mängd'] = pd.to_numeric(df['Mottagen mängd'], errors='coerce').fillna(0)
            
            # Säkerställ att alla kolumner finns
            for col in required_columns:
                if col not in df.columns:
                    df[col] = None
            
            # Lägg till loggning
            logger.info(f"Laddade ordrar från fil. Antal rader: {len(df)}")
            logger.info(f"Aktiva ordrar: {df[df['IsActive'] == True]['OrderName'].tolist()}")
            logger.info(f"Tillgängliga kolumner: {df.columns.tolist()}")
            
            ALL_ORDERS_DF = df
            logger.info("Ordrar laddade från active_orders.csv.")
        except Exception as e:
            logger.error(f"Kunde inte läsa {ACTIVE_ORDERS_FILE}: {str(e)}")
            # Skapa en tom DataFrame med alla kolumner
            ALL_ORDERS_DF = pd.DataFrame(columns=required_columns)
    else:
        logger.info("Ingen active_orders.csv hittad. ALL_ORDERS_DF blir tom.")
        # Skapa en tom DataFrame med alla kolumner
        ALL_ORDERS_DF = pd.DataFrame(columns=required_columns)

def save_orders_to_file():
    """Sparar ALL_ORDERS_DF till CSV."""
    global ALL_ORDERS_DF
    try:
        ALL_ORDERS_DF.to_csv(ACTIVE_ORDERS_FILE, index=False)
        logger.info("Ordrar sparade till active_orders.csv.")
    except Exception as e:
        logger.error(f"Kunde inte spara ordrar: {str(e)}")

# -----------------------------------------------------------
# 3) Funktioner för att hämta data via Centra (GraphQL)
# -----------------------------------------------------------

def fetch_all_suppliers(api_endpoint, headers):
    suppliers_query = '''
    query Suppliers {
        suppliers {
            id
            name
            status
        }
    }
    '''
    try:
        response = requests.post(api_endpoint,
                                 json={"query": suppliers_query},
                                 headers=headers)
        response.raise_for_status()
        data = response.json()

        if "errors" in data:
            raise Exception(f"GraphQL query error: {json.dumps(data['errors'], indent=2)}")

        fetched_suppliers = data['data']['suppliers']
        return fetched_suppliers

    except Exception as e:
        logger.error(f"Fel vid hämtning av suppliers: {str(e)}")
        return None

def fetch_supplied_product_variants(api_endpoint, headers, supplier_id, products_limit=100):
    variants = []
    page = 1

    variants_query = '''
    query SupplierVariants($id: Int!, $limit: Int!, $page: Int!) {
        supplier(id: $id) {
            suppliedProductVariants(limit: $limit, page: $page) {
                productVariant {
                    product {
                        id
                        name
                        status
                        productNumber
                        isBundle
                    }
                    productSizes {
                        stock {
                            productSize {
                                description
                                quantity
                            }
                        }
                    }
                }
            }
        }
    }
    '''

    while True:
        variables = {"id": supplier_id, "limit": products_limit, "page": page}
        try:
            response = requests.post(api_endpoint,
                                     json={"query": variants_query, "variables": variables},
                                     headers=headers)
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL error: {json.dumps(data['errors'], indent=2)}")

            fetched_variants = data['data']['supplier']['suppliedProductVariants']
            if not fetched_variants:
                break

            variants.extend(fetched_variants)

            if len(fetched_variants) < products_limit:
                break

            page += 1
        except Exception as e:
            logger.error(f"Fel vid hämtning av varianter för supplier {supplier_id}: {str(e)}")
            break

    return variants

def fetch_all_suppliers_and_variants(api_endpoint, headers, products_limit=100):
    suppliers = fetch_all_suppliers(api_endpoint, headers)
    if suppliers is None:
        return None

    suppliers_data = {}
    for supplier in suppliers:
        supplier_id = supplier['id']
        try:
            supplier_id = int(supplier_id)
        except ValueError:
            logger.error(f"Ogiltigt supplier ID: {supplier_id}")
            continue

        supplier_name = supplier['name']
        variants = fetch_supplied_product_variants(api_endpoint, headers, supplier_id, products_limit)
        if not variants:
            logger.info(f"Inga varianter för leverantör: {supplier_name}")
            continue

        for variant in variants:
            product = variant['productVariant']['product']
            product_id = str(product['id']).strip().upper()
            product_name = product['name']
            product_status = product['status']
            product_number = product['productNumber']
            is_bundle = product['isBundle']

            product_sizes = variant['productVariant'].get('productSizes', [])
            if not product_sizes:
                # Ingen storlek
                key = (product_id, "N/A")
                if key not in suppliers_data:
                    suppliers_data[key] = {
                        "ProductID": product_id,
                        "Product Name": product_name,
                        "Product Number": product_number,
                        "Status": product_status,
                        "Is Bundle": is_bundle,
                        "Supplier": supplier_name,
                        "Stock Balance": 0,
                        "Size": "N/A"
                    }
            else:
                for size_entry in product_sizes:
                    stocks = size_entry.get('stock', [])
                    for stock in stocks:
                        product_size = stock.get('productSize', {})
                        quantity = stock.get('quantity', 0)
                        size_desc = product_size.get('description', "N/A")

                        key = (product_id, size_desc)
                        if key not in suppliers_data:
                            suppliers_data[key] = {
                                "ProductID": product_id,
                                "Product Name": product_name,
                                "Product Number": product_number,
                                "Status": product_status,
                                "Is Bundle": is_bundle,
                                "Supplier": supplier_name,
                                "Stock Balance": 0,
                                "Size": size_desc
                            }
                        suppliers_data[key]['Stock Balance'] += quantity

    return suppliers_data

def fetch_all_product_costs(api_endpoint, headers, limit=100):
    cost_dict = {}
    page = 1
    cost_query = '''
    query AllProductCosts($limit: Int!, $page: Int!) {
        products(limit: $limit, page: $page) {
            id
            productNumber
            variants {
                unitCost {
                    value
                }
            }
        }
    }
    '''

    while True:
        variables = {"limit": limit, "page": page}
        try:
            response = requests.post(api_endpoint,
                                     json={"query": cost_query, "variables": variables},
                                     headers=headers)
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL error: {json.dumps(data['errors'], indent=2)}")

            products = data['data']['products']
            if not products:
                break

            for p in products:
                product_id = str(p['id']).strip().upper()
                variants = p.get('variants', [])
                if variants and variants[0].get('unitCost'):
                    cost_value = variants[0]['unitCost'].get('value', 0)
                else:
                    cost_value = 0
                cost_dict[product_id] = cost_value

            if len(products) < limit:
                break

            page += 1

        except Exception as e:
            logger.error(f"Fel vid hämtning av product costs: {str(e)}")
            return None

    return cost_dict

def fetch_all_products(api_endpoint, api_token, limit=200):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}"
    }

    suppliers_data = fetch_all_suppliers_and_variants(api_endpoint, headers, products_limit=100)
    if suppliers_data is None:
        return None

    # Hämta lagerinfo via GraphQL
    product_query_template = '''
    query ProductStocks($limit: Int!, $page: Int!) {
        warehouses {
            stock(limit: $limit, page: $page) {
                productSize {
                    quantity
                    size {
                        name
                    }
                    productVariant {
                        product {
                            id
                            name
                            status
                            productNumber
                            isBundle
                        }
                    }
                }
            }
        }
    }
    '''
    page = 1
    while True:
        variables = {"limit": limit, "page": page}
        try:
            response = requests.post(api_endpoint,
                                     json={"query": product_query_template, "variables": variables},
                                     headers=headers)
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL error: {json.dumps(data['errors'], indent=2)}")

            warehouses = data['data']['warehouses']
            if not warehouses:
                break

            for warehouse in warehouses:
                stock_entries = warehouse.get('stock', [])
                if not stock_entries:
                    continue

                for s in stock_entries:
                    p_info = s['productSize']['productVariant']['product']
                    product_id = str(p_info['id']).strip().upper()
                    product_name = p_info.get('name', 'N/A')
                    product_status = p_info.get('status', 'N/A')
                    product_number = p_info.get('productNumber', 'N/A')
                    is_bundle = p_info.get('isBundle', False)
                    quantity = s['productSize']['quantity']
                    size_name = s['productSize']['size']['name'] if s['productSize']['size'] else "N/A"

                    key = (product_id, size_name)
                    if key in suppliers_data:
                        suppliers_data[key]['Stock Balance'] += quantity
                    else:
                        suppliers_data[key] = {
                            "ProductID": product_id,
                            "Product Name": product_name,
                            "Product Number": product_number,
                            "Status": product_status,
                            "Is Bundle": is_bundle,
                            "Supplier": "No Supplier",
                            "Stock Balance": quantity,
                            "Size": size_name
                        }

            if len(warehouses[0].get('stock', [])) < limit:
                break

            page += 1

        except Exception as e:
            logger.error(f"Fel vid hämtning av produktlager: {str(e)}")
            return None

    cost_dict = fetch_all_product_costs(api_endpoint, headers, limit=100)
    if cost_dict is None:
        cost_dict = {}

    all_product_data = list(suppliers_data.values())
    if not all_product_data:
        logger.info("Ingen produktdata hittades.")
        return pd.DataFrame()

    df = pd.DataFrame(all_product_data)
    # Sätt "PurchasePrice" baserat på cost_dict
    df["PurchasePrice"] = df["ProductID"].apply(lambda pid: cost_dict.get(pid, 0.0))
    df['Stock Balance'] = pd.to_numeric(df['Stock Balance'], errors='coerce').fillna(0).astype(int)
    return df

def fetch_sales_data(api_endpoint, headers, from_date_str, to_date_str, only_shipped=False, limit=100):
    sales_data = []
    page = 1

    if only_shipped:
        orders_query = '''
        query Orders($limit: Int!, $page: Int!, $from: DateTimeTz!, $to: DateTimeTz!) {
            orders(limit: $limit, page: $page, where: { orderDate: { from: $from, to: $to }, status: [SHIPPED] }) {
                orderDate
                status
                lines {
                    productVariant {
                        product {
                            id
                            name
                        }
                    }
                    size
                    quantity
                }
            }
        }
        '''
    else:
        orders_query = '''
        query Orders($limit: Int!, $page: Int!, $from: DateTimeTz!, $to: DateTimeTz!) {
            orders(limit: $limit, page: $page, where: { orderDate: { from: $from, to: $to } }) {
                orderDate
                status
                lines {
                    productVariant {
                        product {
                            id
                            name
                        }
                    }
                    size
                    quantity
                }
            }
        }
        '''

    while True:
        variables = {
            "limit": limit,
            "page": page,
            "from": f"{from_date_str}T00:00:00Z",
            "to": f"{to_date_str}T23:59:59Z"
        }
        try:
            response = requests.post(api_endpoint,
                                     json={"query": orders_query, "variables": variables},
                                     headers=headers)
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL error: {json.dumps(data['errors'], indent=2)}")

            orders = data['data']['orders']
            if not orders:
                break

            for order in orders:
                if only_shipped and order.get('status') != "SHIPPED":
                    continue
                for line in order.get('lines', []):
                    pv = line.get('productVariant')
                    if not pv:
                        continue
                    product = pv.get('product')
                    if not product:
                        continue

                    product_id = str(product['id']).strip().upper()
                    size = line['size'] if line['size'] else "N/A"
                    quantity = line.get('quantity', 1)
                    sales_data.append({
                        "ProductID": product_id,
                        "Size": size,
                        "Quantity Sold": quantity
                    })

            if len(orders) < limit:
                break
            page += 1

        except Exception as e:
            logger.error(f"Fel vid hämtning av orders: {str(e)}")
            return None

    return sales_data

def process_sales_data(sales_data, from_date, to_date):
    if not sales_data:
        return pd.DataFrame(columns=["ProductID", "Size", "Quantity Sold", "Avg Daily Sales"])
    sales_df = pd.DataFrame(sales_data)
    sales_summary = sales_df.groupby(['ProductID', 'Size']).agg({'Quantity Sold': 'sum'}).reset_index()
    days_in_range = (pd.to_datetime(to_date) - pd.to_datetime(from_date)).days + 1
    sales_summary["Avg Daily Sales"] = (sales_summary["Quantity Sold"] / days_in_range).round(1)
    sales_summary["Quantity Sold"] = sales_summary["Quantity Sold"].astype(int)
    return sales_summary

def merge_product_and_sales_data(products_df, sales_summary_df):
    if products_df.empty:
        return pd.DataFrame()
    merged_df = pd.merge(products_df, sales_summary_df, on=['ProductID', 'Size'], how='left')
    merged_df['Quantity Sold'] = merged_df['Quantity Sold'].fillna(0).astype(int)
    merged_df['Avg Daily Sales'] = merged_df['Avg Daily Sales'].fillna(0).astype(float).round(1)
    return merged_df

def calculate_reorder_metrics(df, lead_time, safety_stock):
    if df.empty:
        return df
    df["Reorder Level"] = (df["Avg Daily Sales"] * lead_time) + safety_stock
    df["Quantity to Order"] = df["Reorder Level"] - df["Stock Balance"]
    df["Quantity to Order"] = df["Quantity to Order"].apply(lambda x: max(x, 0))
    df["Need to Order"] = df["Quantity to Order"].apply(lambda x: "Yes" if x > 0 else "No")
    return df

def fetch_all_products_with_sales(api_endpoint,
                                  api_token,
                                  from_date_str,
                                  to_date_str,
                                  lead_time,
                                  safety_stock,
                                  only_shipped=False,
                                  product_limit=200,
                                  orders_limit=100):
    products_df = fetch_all_products(api_endpoint, api_token, limit=product_limit)
    if products_df is None or products_df.empty:
        return None

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}"
    }
    sales_data = fetch_sales_data(api_endpoint=api_endpoint, headers=headers,
                                  from_date_str=from_date_str,
                                  to_date_str=to_date_str,
                                  only_shipped=only_shipped,
                                  limit=orders_limit)
    if sales_data is None:
        return None

    sales_summary_df = process_sales_data(sales_data, from_date_str, to_date_str)
    merged_df = merge_product_and_sales_data(products_df, sales_summary_df)
    merged_df = calculate_reorder_metrics(merged_df, lead_time, safety_stock)

    # Beräkna Days to Zero
    merged_df['Days to Zero'] = ''
    mask = (merged_df['Avg Daily Sales'] > 0) & (merged_df['Stock Balance'] >= 0)
    merged_df.loc[mask, 'Days to Zero'] = (
        (merged_df.loc[mask, 'Stock Balance'] / merged_df.loc[mask, 'Avg Daily Sales'])
        .round()
        .astype(int)
    )
    merged_df['Days to Zero'] = merged_df['Days to Zero'].astype(str)
    merged_df['Days to Zero'] = merged_df['Days to Zero'].replace('', None)

    # Lägg till inkommande kolumner
    merged_df = add_incoming_stock_columns(merged_df)
    return merged_df

def add_incoming_stock_columns(df):
    """
    Skapar kolumner:
      - 'Incoming Qty'
      - 'Stock + Incoming'
    utifrån ALL_ORDERS_DF (där IsActive == True).
    """
    global ALL_ORDERS_DF
    active_orders = ALL_ORDERS_DF[ALL_ORDERS_DF['IsActive'] == True]
    if active_orders.empty:
        df['Incoming Qty'] = 0
        df['Stock + Incoming'] = df['Stock Balance']
        return df

    incoming_df = (
        active_orders
        .groupby(['ProductID', 'Size'], as_index=False)['Quantity ordered']
        .sum()
        .rename(columns={'Quantity ordered': 'Incoming Qty'})
    )

    out = pd.merge(df, incoming_df, on=['ProductID', 'Size'], how='left')
    out['Incoming Qty'] = out['Incoming Qty'].fillna(0).astype(int)
    out['Stock + Incoming'] = out['Stock Balance'] + out['Incoming Qty']
    return out

# -----------------------------------------------------------
# 4) Leverans-funktioner
# -----------------------------------------------------------

def create_new_delivery(order_name, products_df):
    """
    Skapar en ny leverans (order) och lägger till i ALL_ORDERS_DF.
    Kräver att CSV:en har kolumner:
      ProductID, Product Number, Product Name, Supplier,
      PurchasePrice, Quantity to Order, Size
    """
    global ALL_ORDERS_DF
    required_cols = [
        "ProductID",
        "Product Number",
        "Product Name",
        "Supplier",
        "PurchasePrice",
        "Quantity to Order",
        "Size"
    ]
    missing = [c for c in required_cols if c not in products_df.columns]
    if missing:
        logger.error(f"CSV saknar kolumner: {missing}")
        return False

    # Rensa/validera data
    valid_products = products_df.copy()
    valid_products['ProductID'] = valid_products['ProductID'].astype(str)
    valid_products['Size'] = valid_products['Size'].astype(str)
    valid_products['Quantity to Order'] = pd.to_numeric(valid_products['Quantity to Order'], errors='coerce').fillna(0)

    # Filtrera bort rader med 0
    valid_products = valid_products[valid_products['Quantity to Order'] > 0]
    if valid_products.empty:
        logger.warning("Inga rader med Quantity to Order > 0.")
        return False

    valid_products['OrderDate'] = pd.Timestamp.now().strftime('%Y-%m-%d')
    valid_products['OrderName'] = order_name
    valid_products['Quantity ordered'] = valid_products['Quantity to Order']
    valid_products['IsActive'] = True

    # Ta bort 'Quantity to Order' och eventuella onödiga kolumner
    if 'Quantity to Order' in valid_products.columns:
        valid_products.drop(columns=['Quantity to Order'], inplace=True)

    ALL_ORDERS_DF = pd.concat([ALL_ORDERS_DF, valid_products], ignore_index=True)
    save_orders_to_file()
    return True

def cancel_delivery(order_name):
    global ALL_ORDERS_DF
    mask = ALL_ORDERS_DF['OrderName'] == order_name
    # Ta bort raderna
    ALL_ORDERS_DF = ALL_ORDERS_DF[~mask]
    save_orders_to_file()
    # Uppdatera ev. stats_df om den finns
    from data import DATAFRAME_CACHE
    df = DATAFRAME_CACHE.get("stats_df")
    if df is not None:
        df = add_incoming_stock_columns(df)
        DATAFRAME_CACHE["stats_df"] = df

def handle_delivery_completion(delivery_df, api_endpoint=None, api_token=None):
    """
    När en leverans mottas:
    1. Uppdatera IsActive=False i ALL_ORDERS_DF
    2. Beräkna nya PurchasePrice baserat på mottagna kvantiteter
    3. Uppdatera lagersaldo i stats_df (om finns)
    """
    global ALL_ORDERS_DF
    order_name = delivery_df['OrderName'].iloc[0]

    # Säkerställ att kolumnerna finns
    if 'Mottagen mängd' not in ALL_ORDERS_DF.columns:
        ALL_ORDERS_DF['Mottagen mängd'] = None
    if 'Kommentar' not in ALL_ORDERS_DF.columns:
        ALL_ORDERS_DF['Kommentar'] = None

    # Uppdatera den befintliga ordern med mottagna värden
    for _, row in delivery_df.iterrows():
        product_mask = (ALL_ORDERS_DF['OrderName'] == order_name) & \
                      (ALL_ORDERS_DF['ProductID'] == row['ProductID']) & \
                      (ALL_ORDERS_DF['Size'] == row['Size'])
        
        # Uppdatera värdena i ALL_ORDERS_DF
        ALL_ORDERS_DF.loc[product_mask, 'Mottagen mängd'] = row['Mottagen mängd']
        ALL_ORDERS_DF.loc[product_mask, 'Kommentar'] = row['Kommentar']
        ALL_ORDERS_DF.loc[product_mask, 'IsActive'] = False

    # Spara ändringar till fil
    save_orders_to_file()

    # Uppdatera stats_df (om vi har en)
    from data import DATAFRAME_CACHE
    df = DATAFRAME_CACHE.get("stats_df")
    if df is not None and not df.empty:
        for _, row in delivery_df.iterrows():
            pid = row['ProductID']
            size = row['Size']
            qty_received = row['Mottagen mängd']
            new_price = row['PurchasePrice']

            # Hämta aktuellt lagersaldo från Centra
            current_stock = 0
            if api_endpoint and api_token:
                current_stock = get_current_stock_from_centra(api_endpoint, api_token, pid, size)

            product_mask = (df['ProductID'] == pid) & (df['Size'] == size)
            if any(product_mask):
                current_price = df.loc[product_mask, 'PurchasePrice'].iloc[0]
                
                # Beräkna nytt PurchasePrice
                new_weighted_price = calculate_new_purchase_price(
                    pid, size, current_stock, current_price, 
                    qty_received, new_price
                )
                
                # Uppdatera både pris och lagersaldo
                df.loc[product_mask, 'PurchasePrice'] = new_weighted_price
                df.loc[product_mask, 'Stock Balance'] += qty_received

        df = add_incoming_stock_columns(df)
        DATAFRAME_CACHE["stats_df"] = df

def get_active_deliveries_summary():
    """
    Returnerar en lista av dict med grupp-info:
    {
      "OrderName": "...",
      "OrderDate": "...",
      "QuantitySum": ...,
      "ProductCount": ...
    }
    för IsActive=True
    """
    global ALL_ORDERS_DF
    active_orders = ALL_ORDERS_DF[ALL_ORDERS_DF['IsActive'] == True].copy()
    if active_orders.empty:
        return []

    grouped = active_orders.groupby("OrderName").agg({
        "OrderDate": "first",
        "Quantity ordered": "sum",
        "ProductID": "count"
    }).reset_index()
    grouped.rename(columns={
        "Quantity ordered": "QuantitySum",
        "ProductID": "ProductCount"
    }, inplace=True)
    return grouped.to_dict(orient="records")

def get_completed_deliveries_summary():
    global ALL_ORDERS_DF
    completed = ALL_ORDERS_DF[ALL_ORDERS_DF['IsActive'] == False].copy()
    if completed.empty:
        return []

    grouped = completed.groupby("OrderName").agg({
        "OrderDate": "first",
        "Quantity ordered": "sum",
        "ProductID": "count"
    }).reset_index()
    grouped.rename(columns={
        "Quantity ordered": "QuantitySum",
        "ProductID": "ProductCount"
    }, inplace=True)
    return grouped.to_dict(orient="records")

def get_delivery_details(order_name):
    """
    Returnerar rad-detaljer (DataFrame) för en enskild leverans (IsActive=True).
    """
    global ALL_ORDERS_DF
    
    # Konvertera order_name till string för säkerhets skull
    order_name = str(order_name)
    
    logger.info(f"Hämtar leveransdetaljer för: '{order_name}'")
    logger.info(f"Aktiva ordrar: {ALL_ORDERS_DF[ALL_ORDERS_DF['IsActive'] == True]['OrderName'].tolist()}")
    
    df = ALL_ORDERS_DF[
        (ALL_ORDERS_DF['OrderName'].astype(str) == order_name) &
        (ALL_ORDERS_DF['IsActive'] == True)
    ].copy()
    
    if df.empty:
        logger.warning(f"Inga detaljer hittades för leverans: '{order_name}'")
    else:
        logger.info(f"Hittade {len(df)} rader för leverans '{order_name}'")
    
    return df if not df.empty else None

def verify_active_delivery(order_name):
    """
    Kontrollerar om en leverans med givet namn finns och är aktiv.
    Returnerar True/False och eventuellt felmeddelande.
    """
    global ALL_ORDERS_DF
    
    # Konvertera order_name till string för säkerhets skull
    order_name = str(order_name)
    
    logger.info(f"Verifierar leverans: '{order_name}'")
    logger.info(f"Alla OrderNames i systemet: {ALL_ORDERS_DF['OrderName'].unique().tolist()}")
    
    # Kontrollera om leveransen finns överhuvudtaget
    if not any(ALL_ORDERS_DF['OrderName'].astype(str) == order_name):
        logger.warning(f"Leverans '{order_name}' hittades inte i systemet")
        return False, "Leveransen hittades inte i systemet"
        
    # Kontrollera om leveransen är aktiv
    active_mask = (ALL_ORDERS_DF['OrderName'].astype(str) == order_name) & (ALL_ORDERS_DF['IsActive'] == True)
    if not any(active_mask):
        logger.warning(f"Leverans '{order_name}' finns men är inte aktiv")
        return False, "Leveransen finns men är inte längre aktiv"
    
    logger.info(f"Leverans '{order_name}' verifierad och aktiv")
    return True, None

def calculate_new_purchase_price(product_id, size, current_stock, current_price, received_qty, new_price):
    """
    Beräknar nytt viktat PurchasePrice baserat på:
    - Nuvarande lagersaldo och pris
    - Mottagen kvantitet och nytt pris
    """
    if current_stock <= 0:
        return new_price
        
    total_qty = current_stock + received_qty
    if total_qty <= 0:
        return current_price
        
    weighted_price = ((current_stock * current_price) + (received_qty * new_price)) / total_qty
    return round(weighted_price, 2)

def get_current_stock_from_centra(api_endpoint, api_token, product_id, size):
    """
    Hämtar aktuellt lagersaldo från Centra för en specifik produkt och storlek
    """
    logger.info(f"Försöker hämta lager för produkt: {product_id}, storlek: {size}")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}"
    }
    
    # Konvertera product_id till int och ta bort eventuella mellanslag
    try:
        product_id_int = int(product_id.strip())
    except (ValueError, AttributeError):
        logger.error(f"Ogiltigt product_id format: {product_id}")
        return 0
    
    # Uppdaterad query som tar emot en array av produkt-ID:n
    stock_query = '''
    query ProductStocks($productId: [Int!]!) {
        warehouses {
            stock(where: { productId: $productId }) {
                productSize {
                    quantity
                    size {
                        name
                    }
                    productVariant {
                        product {
                            id
                            name
                        }
                    }
                }
            }
        }
    }
    '''
    
    try:
        # Skicka produkt-ID som en array
        variables = {"productId": [product_id_int]}
        logger.info(f"Skickar GraphQL-query med variabler: {variables}")
        
        response = requests.post(
            api_endpoint,
            json={
                "query": stock_query, 
                "variables": variables
            },
            headers=headers
        )
        
        if not response.ok:
            logger.error(f"API svarade med status {response.status_code}: {response.text}")
            return 0
            
        data = response.json()
        logger.info(f"API svar för produkt {product_id}: {data}")
        
        if "errors" in data:
            logger.error(f"GraphQL error för produkt {product_id}: {data['errors']}")
            return 0
            
        total_quantity = 0
        warehouses = data.get('data', {}).get('warehouses', [])
        
        for warehouse in warehouses:
            stock_entries = warehouse.get('stock', [])
            for stock in stock_entries:
                product_size = stock.get('productSize', {})
                stock_size = product_size.get('size', {}).get('name')
                if stock_size == size:
                    quantity = product_size.get('quantity', 0)
                    total_quantity += quantity
                    logger.info(f"Hittade {quantity} st för produkt {product_id} storlek {size}")
        
        if total_quantity == 0:
            logger.warning(f"Ingen matchande storlek '{size}' hittad för produkt {product_id}")
        else:
            logger.info(f"Totalt lagersaldo för produkt {product_id} storlek {size}: {total_quantity}")
            
        return total_quantity
        
    except Exception as e:
        logger.error(f"Fel vid hämtning av lagersaldo för produkt {product_id}: {str(e)}")
        return 0

def test_stock_query(api_endpoint, api_token, product_id, size):
    """
    Testfunktion för att debugga stock-queryn
    """
    logger.info("=== TEST STOCK QUERY ===")
    logger.info(f"API Endpoint: {api_endpoint}")
    logger.info(f"Product ID: {product_id}")
    logger.info(f"Size: {size}")
    
    stock = get_current_stock_from_centra(api_endpoint, api_token, product_id, size)
    logger.info(f"Resultat: {stock}")
    logger.info("=== TEST SLUT ===")
    return stock
