import os
import time
import csv
import requests
from dotenv import load_dotenv

load_dotenv()

API_VERSION = "2024-01"
PAGE_SIZE = 250

VARIANTS_QUERY = """
query Variants($first:Int!, $after:String) {
  productVariants(first: $first, after: $after) {
    pageInfo { hasNextPage endCursor }
    edges {
      node {
        sku
        inventoryItem {
          inventoryLevels(first: 1) {
            edges {
              node {
                quantities(names: ["available","on_hand"]) { name quantity }
              }
            }
          }
        }
      }
    }
  }
}
"""

def normalize_shop_domain(shop_value: str) -> str:
    s = (shop_value or "").strip()
    s = s.replace("https://", "").replace("http://", "")
    s = s.split("/")[0]
    if s.endswith(".myshopify.com"):
        return s
    return f"{s}.myshopify.com"

def gql(shop_domain: str, token: str, query: str, variables: dict):
    url = f"https://{shop_domain}/admin/api/{API_VERSION}/graphql.json"
    headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}

    resp = requests.post(url, json={"query": query, "variables": variables}, headers=headers, timeout=60)
    resp.raise_for_status()
    payload = resp.json()

    if "errors" in payload:
        msgs = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        raise RuntimeError(f"Shopify GraphQL error ({shop_domain}): {msgs}")

    return payload["data"]

def extract_qty(level_node: dict) -> tuple[int, int]:
    on_hand = 0
    available = 0
    for q in level_node.get("quantities", []):
        if q["name"] == "on_hand":
            on_hand = q["quantity"]
        elif q["name"] == "available":
            available = q["quantity"]
    return on_hand, available

def pull_store_inventory(store_label: str, shop_raw: str, token: str, writer: csv.writer):
    shop_domain = normalize_shop_domain(shop_raw)

    print(f"\n=== {store_label} ===", flush=True)
    print(f"Using shop domain: {shop_domain}", flush=True)
    print("Pulling inventory (SKU, on_hand, available)...", flush=True)

    after = None
    page = 0
    written = 0
    started = time.time()

    while True:
        page += 1
        t0 = time.time()

        data = gql(shop_domain, token, VARIANTS_QUERY, {"first": PAGE_SIZE, "after": after})
        conn = data["productVariants"]
        edges = conn["edges"]

        for edge in edges:
            node = edge["node"]
            sku = (node.get("sku") or "").strip()
            if not sku:
                continue

            levels = node["inventoryItem"]["inventoryLevels"]["edges"]
            if not levels:
                writer.writerow([store_label, sku, 0, 0])
                written += 1
                continue

            on_hand, available = extract_qty(levels[0]["node"])
            writer.writerow([store_label, sku, on_hand, available])
            written += 1

        dt = time.time() - t0
        elapsed = time.time() - started
        print(
            f"Page {page}: fetched {len(edges)} variants, wrote {written} rows "
            f"({dt:.1f}s this page, {elapsed/60:.1f} min total)",
            flush=True
        )

        if not conn["pageInfo"]["hasNextPage"]:
            break

        after = conn["pageInfo"]["endCursor"]
        time.sleep(0.1)

    print(f"Done ({store_label}). Wrote {written} rows.", flush=True)

def main():
    # Store 1 (your existing store)
    shop1 = os.getenv("SHOPIFY_STORE")
    tok1 = os.getenv("SHOPIFY_TOKEN")

    # Store 2 (DTC)
    shop2 = os.getenv("SHOPIFY_STORE_DTC")
    tok2 = os.getenv("SHOPIFY_TOKEN_DTC")

    missing = []
    if not shop1 or not tok1:
        missing.append("SHOPIFY_STORE / SHOPIFY_TOKEN")
    if not shop2 or not tok2:
        missing.append("SHOPIFY_STORE_DTC / SHOPIFY_TOKEN_DTC")
    if missing:
        raise ValueError("Missing env vars: " + ", ".join(missing))

    os.makedirs("exports", exist_ok=True)
    out_path = os.path.join("exports", "shopify_inventory_export.csv")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Store", "SKU", "On Hand", "Available"])

        pull_store_inventory("Wholesale", shop1, tok1, writer)
        pull_store_inventory("DTC", shop2, tok2, writer)

    print(f"\n✅ Combined export written to {out_path}", flush=True)

if __name__ == "__main__":
    main()
