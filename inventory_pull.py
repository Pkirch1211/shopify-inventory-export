import os
import time
import csv
import requests
from dotenv import load_dotenv

load_dotenv()

SHOP_RAW = os.getenv("SHOPIFY_STORE")
TOKEN = os.getenv("SHOPIFY_TOKEN")

if not SHOP_RAW or not TOKEN:
    raise ValueError("Missing SHOPIFY_STORE or SHOPIFY_TOKEN in .env")

def normalize_shop_domain(shop_value: str) -> str:
    s = (shop_value or "").strip()
    s = s.replace("https://", "").replace("http://", "")
    s = s.split("/")[0]
    if s.endswith(".myshopify.com"):
        return s
    return f"{s}.myshopify.com"

SHOP_DOMAIN = normalize_shop_domain(SHOP_RAW)

API_VERSION = "2024-01"
URL = f"https://{SHOP_DOMAIN}/admin/api/{API_VERSION}/graphql.json"

HEADERS = {
    "X-Shopify-Access-Token": TOKEN,
    "Content-Type": "application/json",
}

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

def gql(query: str, variables: dict):
    resp = requests.post(
        URL,
        json={"query": query, "variables": variables},
        headers=HEADERS,
        timeout=60,
    )
    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        msgs = "; ".join(e.get("message", str(e)) for e in payload["errors"])
        raise RuntimeError(f"Shopify GraphQL error: {msgs}")
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

def main():
    # Always write "latest" to a stable path for Power Query
    export_dir = "exports"
    os.makedirs(export_dir, exist_ok=True)
    out_path = os.path.join(export_dir, "shopify_inventory_export.csv")

    page_size = 250

    print(f"Using shop domain: {SHOP_DOMAIN}", flush=True)
    print("Pulling inventory (SKU, on_hand, available)...", flush=True)

    after = None
    page = 0
    written = 0
    started = time.time()

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["SKU", "On Hand", "Available"])

        while True:
            page += 1
            t0 = time.time()
            data = gql(VARIANTS_QUERY, {"first": page_size, "after": after})
            conn = data["productVariants"]
            edges = conn["edges"]

            for edge in edges:
                node = edge["node"]
                sku = (node.get("sku") or "").strip()
                if not sku:
                    continue

                levels = node["inventoryItem"]["inventoryLevels"]["edges"]
                if not levels:
                    writer.writerow([sku, 0, 0])
                    written += 1
                    continue

                on_hand, available = extract_qty(levels[0]["node"])
                writer.writerow([sku, on_hand, available])
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
            time.sleep(0.1)  # gentle pacing

    print(f"Done. Wrote {written} rows to {out_path}", flush=True)

if __name__ == "__main__":
    main()
