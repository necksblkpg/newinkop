# data.py

import os
import requests
import json
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import logging

from firebase_storage import (
    save_active_orders,
    load_active_orders,
    backup_orders,
    save_product_costs,
    load_product_costs as firebase_load_product_costs
)

##########################################
# Flask-importer (för SSE)
##########################################
from flask import Flask, Response

##########################################
# Initiera Flask-app
##########################################
app = Flask(__name__)

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
PRICE_LISTS_FILE = "price_lists.json"

# Timeout för requests (sekunder)
REQUESTS_TIMEOUT = 300

# -----------------------------------------------------------
# 1) Snittkostnader (product_costs.csv)
# -----------------------------------------------------------
def init_data_store():
    """
    Initierar product_costs.csv om den inte finns
    """
    try:
        df = firebase_load_product_costs()
        if df is None:
            df = pd.DataFrame(columns=["ProductID", "AvgCost", "LastUpdated"])
            save_product_costs(df)
            logger.info("Skapade tom product_costs i Firebase.")
        else:
            logger.info("product_costs finns redan i Firebase.")
    except Exception as e:
        logger.error(f"Fel vid initiering av product_costs: {str(e)}")


def load_product_costs():
    """
    Läser in product costs från Firebase
    """
    try:
        df = firebase_load_product_costs()
        if df is None:
            df = pd.DataFrame(columns=["ProductID", "AvgCost", "LastUpdated"])

        if "AvgCost" not in df.columns:
            df["AvgCost"] = 0.0
        if "LastUpdated" not in df.columns:
            df["LastUpdated"] = ""

        return df
    except Exception as e:
        logger.error(f"Fel vid laddning av product costs: {str(e)}")
        return pd.DataFrame(columns=["ProductID", "AvgCost", "LastUpdated"])


def save_product_costs(df):
    """
    Sparar product costs till Firebase
    """
    try:
        from firebase_storage import save_product_costs as firebase_save
        return firebase_save(df)
    except Exception as e:
        logger.error(f"Fel vid sparning av product costs: {str(e)}")
        return False


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
    Läser in ordrar från Firebase Storage och stoppar i ALL_ORDERS_DF.
    Om data saknas -> tom dataframe.
    """
    global ALL_ORDERS_DF

    required_columns = [
        "OrderDate",
        "OrderName",
        "ProductID",
        "Size",
        "Quantity ordered",
        "Mottagen mängd",
        "PurchasePrice",
        "Price",
        "Currency",
        "Exchange rate",
        "Shipping",
        "Customs",
        "Kommentar",
        "IsActive"
    ]

    try:
        df = load_active_orders()

        if df is not None:
            if 'IsActive' in df.columns:
                df['IsActive'] = df['IsActive'].astype(bool)
            else:
                df['IsActive'] = True

            # Lägg till saknade kolumner
            for col in required_columns:
                if col not in df.columns:
                    if col in ["Price", "Exchange rate", "Shipping", "Customs"]:
                        df[col] = 0.0
                    elif col == "Currency":
                        df[col] = "SEK"
                    elif col == "Kommentar":
                        df[col] = ""
                    elif col == "IsActive":
                        df[col] = True
                    elif col == "Mottagen mängd":
                        df[col] = df["Quantity ordered"]

            ALL_ORDERS_DF = df
            logger.info(f"Laddade ordrar från Firebase. Antal rader: {len(df)}")
            logger.info(f"Aktiva ordrar: {df[df['IsActive'] == True]['OrderName'].unique().tolist()}")
            logger.info(f"Inaktiva ordrar: {df[df['IsActive'] == False]['OrderName'].unique().tolist()}")
        else:
            logger.info("Inga ordrar hittades i Firebase. ALL_ORDERS_DF blir tom.")
            ALL_ORDERS_DF = pd.DataFrame(columns=required_columns)

    except Exception as e:
        logger.error(f"Fel vid läsning från Firebase: {str(e)}")
        ALL_ORDERS_DF = pd.DataFrame(columns=required_columns)


def save_orders_to_file():
    """Sparar ALL_ORDERS_DF till Firebase Storage och skapar backup"""
    global ALL_ORDERS_DF
    try:
        numeric_cols = ['Mottagen mängd', 'new_price_sek', 'new_avg_cost']
        for col in numeric_cols:
            if col in ALL_ORDERS_DF.columns:
                ALL_ORDERS_DF[col] = pd.to_numeric(ALL_ORDERS_DF[col], errors='coerce').fillna(0)

        if save_active_orders(ALL_ORDERS_DF):
            logger.info(f"Sparade {len(ALL_ORDERS_DF)} rader till Firebase Storage")
            if backup_orders():
                logger.info("Skapade backup av ordrar")
            else:
                logger.warning("Kunde inte skapa backup av ordrar")
        else:
            logger.error("Kunde inte spara ordrar till Firebase Storage")

    except Exception as e:
        logger.error(f"Fel vid sparning till Firebase: {str(e)}")


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
        response = requests.post(
            api_endpoint,
            json={"query": suppliers_query},
            headers=headers,
            timeout=REQUESTS_TIMEOUT
        )
        response.raise_for_status()
        data = response.json()
        if "errors" in data:
            raise Exception(f"GraphQL query error: {data['errors']}")
        return data['data']['suppliers']
    except Exception as e:
        logger.error(f"Fel vid hämtning av suppliers: {str(e)}")
        return None


def fetch_collections_and_products(api_endpoint, headers):
    coll_query = '''
    query Collections {
        collections {
            id
            status
            name
        }
    }
    '''
    product_map = {}
    try:
        resp = requests.post(
            api_endpoint,
            json={"query": coll_query},
            headers=headers,
            timeout=REQUESTS_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise Exception(f"GraphQL error i collections-listan: {data['errors']}")

        all_collections = data['data']['collections']
        if not all_collections:
            return {}

        coll_products_query = '''
        query CollectionProducts($id:Int!, $limit:Int!, $page:Int!){
          collection(id:$id){
            id
            name
            products(limit:$limit, page:$page) {
              id
            }
          }
        }
        '''
        for c in all_collections:
            c_id = c['id']
            c_name = c['name']

            page = 1
            limit = 100
            while True:
                vars_ = {"id": int(c_id), "limit": limit, "page": page}
                r2 = requests.post(
                    api_endpoint,
                    json={"query": coll_products_query, "variables": vars_},
                    headers=headers,
                    timeout=REQUESTS_TIMEOUT
                )
                r2.raise_for_status()
                d2 = r2.json()
                if "errors" in d2:
                    logger.error(f"Fel i sub-query for collection {c_id}: {d2['errors']}")
                    break

                the_collection = d2['data']['collection']
                if not the_collection or not the_collection['products']:
                    break

                products_list = the_collection['products']
                if len(products_list) == 0:
                    break

                for p in products_list:
                    pid = str(p['id']).strip().upper()
                    if pid not in product_map:
                        product_map[pid] = set()
                    product_map[pid].add(c_name)

                if len(products_list) < limit:
                    break
                page += 1

    except Exception as e:
        logger.error(f"Fel vid hämtning av collections: {str(e)}")
        return {}

    return product_map


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
            response = requests.post(
                api_endpoint,
                json={"query": variants_query, "variables": variables},
                headers=headers,
                timeout=REQUESTS_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL error: {data['errors']}")

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
            response = requests.post(
                api_endpoint,
                json={"query": cost_query, "variables": variables},
                headers=headers,
                timeout=REQUESTS_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL error: {data['errors']}")

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

    page = 1
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

    while True:
        variables = {"limit": limit, "page": page}
        try:
            response = requests.post(
                api_endpoint,
                json={"query": product_query_template, "variables": variables},
                headers=headers,
                timeout=REQUESTS_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL error: {data['errors']}")

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
    df["PurchasePrice"] = df["ProductID"].apply(lambda pid: cost_dict.get(pid, 0.0))
    df['Stock Balance'] = pd.to_numeric(df['Stock Balance'], errors='coerce').fillna(0).astype(int)

    product2coll_map = fetch_collections_and_products(api_endpoint, headers)

    def get_collections(pid):
        cset = product2coll_map.get(pid, set())
        return sorted(list(cset))

    df["Collections"] = df["ProductID"].apply(get_collections)

    return df

# -----------------------------------------------------------
# 3b) Dela upp datum i mindre chunkar för orders
# -----------------------------------------------------------
def _split_date_range(from_date_str, to_date_str, chunk_days=7):
    from_dt = datetime.fromisoformat(from_date_str)
    to_dt = datetime.fromisoformat(to_date_str)

    ranges = []
    current_start = from_dt
    while current_start <= to_dt:
        current_end = min(current_start + timedelta(days=chunk_days - 1), to_dt)
        start_str = current_start.strftime("%Y-%m-%d")
        end_str = current_end.strftime("%Y-%m-%d")
        ranges.append((start_str, end_str))
        current_start = current_end + timedelta(days=1)
    return ranges


def fetch_sales_data_single_range(api_endpoint, headers, from_date_str, to_date_str,
                                  only_shipped=False, limit=100):
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
            response = requests.post(
                api_endpoint,
                json={"query": orders_query, "variables": variables},
                headers=headers,
                timeout=REQUESTS_TIMEOUT
            )
            response.raise_for_status()
            data = response.json()

            if "errors" in data:
                raise Exception(f"GraphQL error: {data['errors']}")

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
            logger.error(f"Fel vid hämtning av orders (intervall {from_date_str} - {to_date_str}): {str(e)}")
            return None

    return sales_data


def fetch_sales_data_chunked(api_endpoint, headers, from_date_str, to_date_str,
                             only_shipped=False, limit=100, chunk_days=7):
    date_ranges = _split_date_range(from_date_str, to_date_str, chunk_days=chunk_days)
    all_sales_data = []

    for (start_str, end_str) in date_ranges:
        logger.info(f"Hämtar orderinfo för intervallet {start_str} - {end_str}")
        chunk_data = fetch_sales_data_single_range(
            api_endpoint,
            headers,
            start_str,
            end_str,
            only_shipped=only_shipped,
            limit=limit
        )
        if chunk_data is None:
            logger.warning("Avbryter p.g.a. None i chunk_data.")
            return None
        all_sales_data.extend(chunk_data)

    return all_sales_data


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


def add_incoming_stock_columns(df):
    if df.empty:
        return df
    out = df.copy()
    if 'Incoming Qty' not in out.columns:
        out['Incoming Qty'] = 0

    if 'Incoming Value' in out.columns:
        out = out.drop(columns=['Incoming Value'])

    active_orders = ALL_ORDERS_DF[ALL_ORDERS_DF['IsActive'] == True].copy()

    if not active_orders.empty:
        incoming = active_orders.groupby(['ProductID', 'Size'])['Quantity ordered'].sum().reset_index()
        for _, row in incoming.iterrows():
            mask = (
                (out['ProductID'].astype(str) == str(row['ProductID'])) &
                (out['Size'].astype(str) == str(row['Size']))
            )
            out.loc[mask, 'Incoming Qty'] = row['Quantity ordered']

    out['Incoming Qty'] = out['Incoming Qty'].fillna(0).astype(int)
    return out


# -----------------------------------------------------------
# 4) Leverans-funktioner
# -----------------------------------------------------------
def create_new_delivery(order_name, products_df):
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

    valid_products = products_df.copy()
    valid_products['ProductID'] = valid_products['ProductID'].astype(str)
    valid_products['Size'] = valid_products['Size'].astype(str)
    valid_products['Quantity to Order'] = pd.to_numeric(valid_products['Quantity to Order'], errors='coerce').fillna(0)

    valid_products = valid_products[valid_products['Quantity to Order'] > 0]
    if valid_products.empty:
        logger.warning("Inga rader med Quantity to Order > 0.")
        return False

    valid_products['OrderDate'] = pd.Timestamp.now().strftime('%Y-%m-%d')
    valid_products['OrderName'] = order_name
    valid_products['Quantity ordered'] = valid_products['Quantity to Order']
    valid_products['IsActive'] = True

    if 'Quantity to Order' in valid_products.columns:
        valid_products.drop(columns=['Quantity to Order'], inplace=True)

    ALL_ORDERS_DF = pd.concat([ALL_ORDERS_DF, valid_products], ignore_index=True)
    save_orders_to_file()
    return True


def cancel_delivery(order_name):
    global ALL_ORDERS_DF
    try:
        logger.info(f"Försöker makulera leverans: {order_name}")
        order_mask = ALL_ORDERS_DF['OrderName'].astype(str) == str(order_name)
        if not any(order_mask):
            logger.warning(f"Hittade ingen leverans med namn: {order_name}")
            return False

        ALL_ORDERS_DF = ALL_ORDERS_DF[~order_mask]
        save_orders_to_file()
        logger.info(f"Leverans {order_name} makulerad")
        return True
    except Exception as e:
        logger.error(f"Fel vid makulering av leverans: {str(e)}")
        raise e


def handle_delivery_completion(delivery_df):
    global ALL_ORDERS_DF
    try:
        order_name = delivery_df['OrderName'].iloc[0]
        logger.info(f"Hanterar färdigställande av leverans: {order_name}")

        order_mask = (ALL_ORDERS_DF['OrderName'].astype(str) == str(order_name))
        ALL_ORDERS_DF.loc[order_mask, 'IsActive'] = False

        for _, row in delivery_df.iterrows():
            row_mask = (
                (ALL_ORDERS_DF['OrderName'].astype(str) == str(order_name)) &
                (ALL_ORDERS_DF['ProductID'].astype(str) == str(row['ProductID'])) &
                (ALL_ORDERS_DF['Size'].astype(str) == str(row['Size']))
            )
            ALL_ORDERS_DF.loc[row_mask, 'Mottagen mängd'] = row['Mottagen mängd']
            ALL_ORDERS_DF.loc[row_mask, 'new_avg_cost'] = row['new_avg_cost']

        save_orders_to_file()
        logger.info(f"Leverans {order_name} markerad som inaktiv och sparad")
        return True
    except Exception as e:
        logger.error(f"Fel vid färdigställande av leverans: {str(e)}")
        return False


def get_active_deliveries_summary():
    global ALL_ORDERS_DF
    if ALL_ORDERS_DF.empty:
        return []

    active = ALL_ORDERS_DF[ALL_ORDERS_DF['IsActive'] == True].copy()
    if active.empty:
        return []

    grouped = active.groupby("OrderName").agg({
        "OrderDate": "first",
        "Quantity ordered": "sum",
        "ProductID": "count"
    }).reset_index()

    grouped.rename(columns={
        "Quantity ordered": "QuantitySum",
        "ProductID": "ProductCount"
    }, inplace=True)

    logger.info(f"Aktiva leveranser: {grouped['OrderName'].tolist()}")
    return grouped.to_dict(orient="records")


def get_completed_deliveries_summary():
    global ALL_ORDERS_DF
    if ALL_ORDERS_DF.empty:
        return []

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

    logger.info(f"Avklarade leveranser: {grouped['OrderName'].tolist()}")
    return grouped.to_dict(orient="records")


def get_delivery_details(order_name, only_active=False):
    global ALL_ORDERS_DF
    order_name = str(order_name)
    logger.info(f"Hämtar leveransdetaljer för: '{order_name}'")

    order_mask = (ALL_ORDERS_DF['OrderName'].astype(str) == order_name)
    if only_active:
        order_mask = order_mask & (ALL_ORDERS_DF['IsActive'] == True)

    df = ALL_ORDERS_DF[order_mask].copy()
    if df.empty:
        logger.warning(f"Inga detaljer hittades för leverans: '{order_name}'")
    else:
        logger.info(f"Hittade {len(df)} rader för leverans '{order_name}'")
        logger.info(f"IsActive status: {df['IsActive'].tolist()}")

    return df if not df.empty else None


def verify_active_delivery(order_name):
    global ALL_ORDERS_DF
    order_name = str(order_name)

    logger.info(f"Verifierar leverans: '{order_name}'")
    logger.info(f"Alla OrderNames i systemet: {ALL_ORDERS_DF['OrderName'].unique().tolist()}")

    if not any(ALL_ORDERS_DF['OrderName'].astype(str) == order_name):
        logger.warning(f"Leverans '{order_name}' hittades inte i systemet")
        return False, "Leveransen hittades inte i systemet"

    active_mask = (
        (ALL_ORDERS_DF['OrderName'].astype(str) == order_name) &
        (ALL_ORDERS_DF['IsActive'] == True)
    )
    if not any(active_mask):
        logger.warning(f"Leverans '{order_name}' finns men är inte aktiv")
        return False, "Leveransen finns men är inte längre aktiv"

    logger.info(f"Leverans '{order_name}' verifierad och aktiv")
    return True, None


def calculate_new_purchase_price(product_id, size, current_stock, current_price, received_qty, new_price):
    if current_stock <= 0:
        return new_price
    total_qty = current_stock + received_qty
    if total_qty <= 0:
        return current_price
    weighted_price = ((current_stock * current_price) + (received_qty * new_price)) / total_qty
    return round(weighted_price, 2)


def get_current_stock_from_centra(api_endpoint, api_token, product_id, size):
    logger.info(f"Försöker hämta lager för produkt: {product_id}, storlek: {size}")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}"
    }
    try:
        product_id_int = int(product_id.strip())
    except (ValueError, AttributeError):
        logger.error(f"Ogiltigt product_id format: {product_id}")
        return 0

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
                        }
                    }
                }
            }
        }
    }
    '''
    try:
        variables = {"productId": [product_id_int]}
        logger.info(f"Skickar GraphQL-query med variabler: {variables}")

        response = requests.post(
            api_endpoint,
            json={"query": stock_query, "variables": variables},
            headers=headers,
            timeout=REQUESTS_TIMEOUT
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
    logger.info("=== TEST STOCK QUERY ===")
    logger.info(f"API Endpoint: {api_endpoint}")
    logger.info(f"Product ID: {product_id}")
    logger.info(f"Size: {size}")

    stock = get_current_stock_from_centra(api_endpoint, api_token, product_id, size)
    logger.info(f"Resultat: {stock}")
    logger.info("=== TEST SLUT ===")
    return stock


def get_price_lists():
    try:
        if not os.path.exists(PRICE_LISTS_FILE):
            return []
        with open(PRICE_LISTS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Fel vid läsning av prislistor: {str(e)}")
        return []


# ----------------------------------------------------------
# 5) CSV-VALIDERINGSMETODER
# ----------------------------------------------------------
def validate_delivery_csv(df):
    required_columns = ["ProductID", "Product Number", "Product Name", "Supplier", "Size", "Quantity to Order"]
    missing_cols = [c for c in required_columns if c not in df.columns]
    if missing_cols:
        return False, f"Saknade kolumner: {missing_cols}"

    try:
        pd.to_numeric(df["Quantity to Order"])
    except ValueError:
        return False, "Kolumnen 'Quantity to Order' måste vara numerisk"

    return True, None


def validate_price_list_csv(df):
    required_cols = ['ProductID', 'Size', 'Price', 'Currency']
    missing_cols = [c for c in required_cols if c not in df.columns]
    if missing_cols:
        return False, f"Saknade kolumner: {missing_cols}"

    try:
        pd.to_numeric(df["Price"])
    except ValueError:
        return False, "Kolumnen 'Price' måste vara numerisk"

    return True, None


def save_price_list(df):
    try:
        ok, err_msg = validate_price_list_csv(df)
        if not ok:
            raise ValueError(err_msg)

        df['ProductID'] = df['ProductID'].astype(str)
        df['Size'] = df['Size'].astype(str)
        df['Price'] = df['Price'].astype(float)
        df['Currency'] = df['Currency'].astype(str)

        df[['ProductID', 'Size', 'Price', 'Currency']].to_csv(PRICE_LISTS_FILE, index=False, encoding='utf-8')
        logger.info(f"Sparade prislista med {len(df)} rader")
    except Exception as e:
        logger.error(f"Fel vid sparande av prislista: {str(e)}")
        raise


def find_price_in_list(product_id, product_number, size):
    try:
        if not os.path.exists(PRICE_LISTS_FILE):
            logger.warning("Ingen prislista finns")
            return None

        df = pd.read_csv(PRICE_LISTS_FILE)
        mask = (
            (df['ProductID'].astype(str) == str(product_id)) &
            (df['Size'].astype(str) == str(size)
        )
        if any(mask):
            row = df[mask].iloc[0]
            return {
                'price': float(row['Price']),
                'currency': row['Currency']
            }
        return None
    except Exception as e:
        logger.error(f"Fel vid sökning i prislista: {str(e)}")
        return None


def delete_price_list(supplier):
    try:
        price_lists = get_price_lists()
        price_lists = [pl for pl in price_lists if pl['supplier'] != supplier]
        with open(PRICE_LISTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(price_lists, f, ensure_ascii=False, indent=2)
        logger.info(f"Tog bort prislista för {supplier}")
        return True
    except Exception as e:
        logger.error(f"Fel vid borttagning av prislista: {str(e)}")
        return False

# -----------------------------------------------------------
# 6) STREAMING-FUNKTION FÖR SALES-DATA
# -----------------------------------------------------------

def fetch_all_products_with_sales_stream(api_endpoint,
                                         api_token,
                                         from_date_str,
                                         to_date_str,
                                         lead_time,
                                         safety_stock,
                                         only_shipped=False,
                                         product_limit=200,
                                         orders_limit=100,
                                         chunk_days=7):
    """
    Generator-funktion som YIELD:ar SSE-event steg för steg.
    Webbläsaren tar emot en kontinuerlig ström av text.
    """

    # 1) Hämta produkter
    yield f"data: Startar hämtning av produkter...\n\n"
    products_df = fetch_all_products(api_endpoint, api_token, limit=product_limit)
    if products_df is None or products_df.empty:
        yield "data: Inga produkter funna.\n\n"
        return

    yield f"data: Hittade {len(products_df)} produkter.\n\n"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_token}"
    }

    # 2) Hämta orders i chunkar
    yield "data: Börjar hämta orderdata i chunkar...\n\n"
    date_ranges = _split_date_range(from_date_str, to_date_str, chunk_days=chunk_days)
    all_sales_data = []

    for (start_str, end_str) in date_ranges:
        yield f"data: Hämta ordrar för intervallet {start_str} - {end_str}\n\n"
        chunk_data = fetch_sales_data_single_range(api_endpoint,
                                                   headers,
                                                   start_str,
                                                   end_str,
                                                   only_shipped=only_shipped,
                                                   limit=orders_limit)
        if chunk_data is None:
            yield f"data: Avbryter - fick None för {start_str}-{end_str}\n\n"
            return

        yield f"data: {len(chunk_data)} rader hittade i {start_str}-{end_str}\n\n"
        all_sales_data.extend(chunk_data)

    yield f"data: Totalt {len(all_sales_data)} orderrader funna.\n\n"

    # 3) Bearbeta sales_data
    yield "data: Bearbetar säljdata...\n\n"
    sales_summary_df = process_sales_data(all_sales_data, from_date_str, to_date_str)

    yield f"data: Skapar slutgiltig DataFrame med {len(sales_summary_df)} unika (ProductID, Size)\n\n"
    merged_df = merge_product_and_sales_data(products_df, sales_summary_df)

    # 4) Räkna ut reorder mm.
    yield "data: Beräknar Reorder Level & Quantity to Order...\n\n"
    merged_df = calculate_reorder_metrics(merged_df, lead_time, safety_stock)

    yield "data: Adderar kommande inkommande lager...\n\n"
    merged_df = add_incoming_stock_columns(merged_df)

    # 5) Avsluta
    yield "data: KLAR! Skriv ut DataFrame i serverlogg...\n\n"
    logger.info(f"SLUTLIG MERGED DF: {merged_df.head(10)}")  # Ex. en första titt
    # Du kan konvertera `merged_df` till JSON eller CSV om du vill
    # men i SSE-exemplet sänder vi bara text om att det är klart.

# -----------------------------------------------------------
# Flask-route som returnerar SSE (Server-Sent Events)
# -----------------------------------------------------------
@app.route("/stream-sales-data")
def stream_sales_data():
    """
    Exempel på en Flask-route som streamar chunkad säljdata.
    Du kan anropa i webbläsaren:
      http://localhost:5000/stream-sales-data
    eller i Replit-länken /stream-sales-data
    """
    api_endpoint = "<DIN_GRAPHQL_ENDPOINT_HÄR>"
    api_token = "<DIN_BEARER_TOKEN>"

    from_date_str = "2024-12-03"
    to_date_str = "2025-01-13"

    lead_time = 14
    safety_stock = 10
    only_shipped = False
    product_limit = 200
    orders_limit = 100
    chunk_days = 7

    def generate_events():
        # yield SSE-data, chunk för chunk
        for msg in fetch_all_products_with_sales_stream(
            api_endpoint,
            api_token,
            from_date_str,
            to_date_str,
            lead_time,
            safety_stock,
            only_shipped=only_shipped,
            product_limit=product_limit,
            orders_limit=orders_limit,
            chunk_days=chunk_days
        ):
            yield msg  # ex: "data: ..." + "\n\n"

        yield "data: [STREAM_END]\n\n"

    return Response(generate_events(), mimetype='text/event-stream')


##########################################
# Om du vill köra lokalt: python data.py
##########################################
if __name__ == "__main__":
    # Exempel: startar Flask server på port 5000
    # Replit kan dock kräva att du kör på en viss port
    load_orders_from_file()
    app.run(host="0.0.0.0", port=5000, debug=True)
