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

def pull_store_inventory(store_label: str, shop_raw: str, token: str) -> dict[str, dict]:
    """
    Returns dict keyed by SKU:
      { "SKU123": {"Store": "...", "SKU": "...", "On Hand": int, "Available": int} }
    """
    shop_domain = normalize_shop_domain(shop_raw)

    print(f"\n=== {store_label} ===", flush=True)
    print(f"Using shop domain: {shop_domain}", flush=True)
    print("Pulling inventory (SKU, on_hand, available)...", flush=True)

    after = None
    page = 0
    started = time.time()
    out: dict[str, dict] = {}

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
                on_hand, available = 0, 0
            else:
                on_hand, available = extract_qty(levels[0]["node"])

            out[sku] = {
                "Store": store_label,
                "SKU": sku,
                "On Hand": int(on_hand),
                "Available": int(available),
            }

        dt = time.time() - t0
        elapsed = time.time() - started
        print(
            f"Page {page}: fetched {len(edges)} variants, unique SKUs so far {len(out)} "
            f"({dt:.1f}s this page, {elapsed/60:.1f} min total)",
            flush=True
        )

        if not conn["pageInfo"]["hasNextPage"]:
            break

        after = conn["pageInfo"]["endCursor"]
        time.sleep(0.1)

    print(f"Done ({store_label}). Unique SKUs: {len(out)}", flush=True)
    return out

def main():
    # Store 1 (Wholesale / canonical)
    shop_wh = os.getenv("SHOPIFY_STORE")
    tok_wh = os.getenv("SHOPIFY_TOKEN")

    # Store 2 (DTC / fallback)
    shop_dtc = os.getenv("SHOPIFY_STORE_DTC")
    tok_dtc = os.getenv("SHOPIFY_TOKEN_DTC")

    missing = []
    if not shop_wh or not tok_wh:
        missing.append("SHOPIFY_STORE / SHOPIFY_TOKEN")
    if not shop_dtc or not tok_dtc:
        missing.append("SHOPIFY_STORE_DTC / SHOPIFY_TOKEN_DTC")
    if missing:
        raise ValueError("Missing env vars: " + ", ".join(missing))

    # Pull both stores
    wh = pull_store_inventory("Wholesale", shop_wh, tok_wh)
    dtc = pull_store_inventory("DTC", shop_dtc, tok_dtc)

    # Deduplicate: prefer Wholesale; include DTC-only SKUs
    merged: dict[str, dict] = dict(wh)
    dtc_only = 0
    for sku, row in dtc.items():
        if sku not in merged:
            merged[sku] = row
            dtc_only += 1

    os.makedirs("exports", exist_ok=True)
    out_path = os.path.join("exports", "shopify_inventory_export.csv")

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Store", "SKU", "On Hand", "Available"])
        for sku in sorted(merged.keys()):
            r = merged[sku]
            writer.writerow([r["Store"], r["SKU"], r["On Hand"], r["Available"]])

    print(
        f"\n✅ Export written to {out_path}\n"
        f"Wholesale SKUs: {len(wh)} | DTC SKUs: {len(dtc)} | DTC-only kept: {dtc_only} | Final unique SKUs: {len(merged)}",
        flush=True
    )

if __name__ == "__main__":
    main()
