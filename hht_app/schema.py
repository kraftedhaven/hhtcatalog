import csv
import html
import io
import re
from typing import Any


HEADERS = [
    "Action", "SiteID", "Currency", "Title", "Subtitle", "Category",
    "ConditionID", "ConditionNote", "Description", "Price", "BuyItNowPrice",
    "Quantity", "BestOfferEnabled", "PicURL", "PaymentProfileName",
    "ShippingProfileName", "ReturnProfileName", "DispatchTimeMax", "Location",
    "CountryCode", "PostalCode", "C:Brand", "C:Size", "C:Color",
    "C:Department", "C:Type", "C:Style", "C:Material", "C:Pattern",
    "C:Sleeve Length", "C:Neckline", "C:Season", "C:Occasion",
    "C:Size Type", "C:Vintage",
]

EBAY_DRAFT_COLUMNS = [
    "Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)",
    "Custom label (SKU)",
    "Category ID",
    "Title",
    "UPC",
    "Price",
    "Quantity",
    "Item photo URL",
    "Condition ID",
    "Description",
    "Format",
]

SCHEMA_KEYS = [
    "title", "price", "cid", "cnote", "cat", "brand", "size", "color",
    "dept", "type", "style", "mat", "pat", "slv", "nk", "sea", "occ",
    "st", "vin", "desc", "notes", "madeIn", "serialNumber", "measurements",
]

CATEGORY_IDS = {
    "women's tops/blouses": "15724",
    "women's dresses": "63861",
    "women's jeans/pants": "63867",
    "women's sweaters/cardigans": "11484",
    "women's jackets/coats": "57988",
    "women's skirts": "63866",
    "women's activewear pants/leggings": "185100",
    "women's sports bras/crop tops": "15724",
    "men's t-shirts": "15687",
    "men's jeans": "11483",
    "men's jackets/coats": "57988",
    "men's casual shirts/polos": "57990",
    "men's sweaters/hoodies": "11484",
    "men's sweatshirts/hoodies": "155183",
    "men's casual shoes/boat shoes": "93427",
    "handbags/clutches/crossbodies": "169291",
    "backpacks": "169284",
}

ALLOWED_CATEGORY_IDS = set(CATEGORY_IDS.values())
BAG_CATEGORY_IDS = {"169291", "169284"}
SHOE_CATEGORY_IDS = {"93427"}
CONDITION_IDS = {"1000", "1500", "3000", "4000", "5000", "6000"}
NOT_VISIBLE = "Not visible"
PIC_PLACEHOLDER = "[SELLER TO ADD IMAGE URLS]"
LUXURY_RE = re.compile(
    r"gucci|louis vuitton|chanel|prada|fendi|hermes|versace|burberry|"
    r"coach|michael kors|tory burch|balenciaga|dior|saint laurent|ysl",
    re.I,
)


def normalize_listing(raw: dict[str, Any] | None) -> dict[str, Any]:
    data = raw or {}
    brand = _text(data.get("brand")) or NOT_VISIBLE
    item_type = _text(data.get("type")) or NOT_VISIBLE
    category = _normalize_category(data.get("cat"), item_type)
    is_bag_item = category in BAG_CATEGORY_IDS or bool(re.search(r"handbag|crossbody|clutch|backpack|tote|purse", item_type, re.I))
    is_shoe_item = category in SHOE_CATEGORY_IDS or bool(re.search(r"shoe|sneaker|boot|loafer|sandal", item_type, re.I))
    notes = _text(data.get("notes"))

    vintage = _normalize_vintage(data.get("vin"), notes)
    title = _text(data.get("title")) or "Review Needed Item"
    if brand != NOT_VISIBLE and not title.lower().startswith(brand.lower()):
        title = f"{brand} {title}"
    if vintage == "Yes (pre-1999)" and "vintage" not in title.lower():
        title = f"Vintage {title}"
    title = _strip_authentication_claims(title)
    title = fit_title(title, brand if brand != NOT_VISIBLE else "")

    if not category:
        notes = _append_note(notes, "Category could not be mapped confidently to the supplied eBay category list.")
    if brand == NOT_VISIBLE:
        notes = _append_note(notes, "Brand was not clearly visible; seller must verify before listing.")

    made_in = _text(data.get("madeIn"))
    serial = _text(data.get("serialNumber"))
    if LUXURY_RE.search(brand):
        notes = _append_note(notes, "Luxury-brand review required. Do not claim authentication from photos alone.")
        if not made_in:
            notes = _append_note(notes, "Made In label was not visible; verify origin independently.")
        if re.search(r"gucci", brand, re.I) and made_in and not re.search(r"italy|italia", made_in, re.I):
            notes = _append_note(notes, "Gucci origin conflict: visible Made In text is not Italy/Italia. Authenticity must be independently verified.")

    cid = _text(data.get("cid"))
    if cid not in CONDITION_IDS:
        cid = "3000"
        notes = _append_note(notes, "Condition ID defaulted to pre-owned; seller must verify.")
    condition_note = _text(data.get("cnote")) or "Needs seller review. Review all photos for wear, stains, pilling, fading, holes, and other flaws."

    result = {
        "title": title,
        "titleLength": len(title),
        "price": _price(data.get("price")),
        "cid": cid,
        "cnote": condition_note,
        "cat": category,
        "brand": brand,
        "size": "N/A - bag" if is_bag_item else (_text(data.get("size")) or NOT_VISIBLE),
        "color": _text(data.get("color")) or NOT_VISIBLE,
        "dept": _text(data.get("dept")) or NOT_VISIBLE,
        "type": item_type,
        "style": _text(data.get("style")) or NOT_VISIBLE,
        "mat": _text(data.get("mat")) or NOT_VISIBLE,
        "pat": _text(data.get("pat")) or NOT_VISIBLE,
        "slv": "N/A - bag" if is_bag_item else "N/A - footwear" if is_shoe_item else (_text(data.get("slv")) or NOT_VISIBLE),
        "nk": "N/A - bag" if is_bag_item else "N/A - footwear" if is_shoe_item else (_text(data.get("nk")) or NOT_VISIBLE),
        "sea": _text(data.get("sea")) or "All Seasons",
        "occ": _text(data.get("occ")) or "Casual",
        "st": "N/A - bag" if is_bag_item else (_text(data.get("st")) or "Regular"),
        "vin": vintage,
        "desc": "",
        "notes": notes,
        "madeIn": made_in,
        "serialNumber": serial,
        "measurements": _text(data.get("measurements")),
        "pic": _text(data.get("pic")),
    }
    result["desc"] = _html_description(_text(data.get("desc")), result)
    return result


def fit_title(value: str, brand: str = "") -> str:
    title = re.sub(r"\s+", " ", _text(value)).strip()
    if brand and not title.lower().startswith(brand.lower()):
        title = f"{brand} {title}"
    if len(title) <= 80:
        return title
    brand_prefix = brand.strip()
    if brand_prefix and len(brand_prefix) < 80:
        remaining = 80 - len(brand_prefix) - 1
        tail = title[len(brand_prefix):].strip() if title.lower().startswith(brand_prefix.lower()) else title
        words = []
        for word in tail.split():
            candidate = " ".join(words + [word])
            if len(candidate) > remaining:
                break
            words.append(word)
        return f"{brand_prefix} {' '.join(words)}".strip()[:80].strip()
    words = []
    for word in title.split():
        candidate = " ".join(words + [word])
        if len(candidate) > 80:
            break
        words.append(word)
    return " ".join(words).strip() or title[:80].strip()


def listing_to_csv_row(item: dict[str, Any], defaults: dict[str, Any] | None = None) -> list[str]:
    defaults = defaults or {}
    price = _price(item.get("price"))
    return [
        "Add", "0", "USD", _text(item.get("title")), "", _text(item.get("cat")),
        _text(item.get("cid")), _text(item.get("cnote")), _text(item.get("desc")),
        f"{price:.2f}" if price else "0.00", "", "1", "true",
        _text(item.get("pic")) or PIC_PLACEHOLDER,
        _text(defaults.get("paymentProfileName")) or "eBay Payments",
        _text(defaults.get("shippingProfileName")) or "Standard Shipping",
        _text(defaults.get("returnProfileName")) or "30 Day Returns",
        _text(defaults.get("dispatchTimeMax")) or "3",
        _text(defaults.get("location")) or "Kettering, Ohio",
        _text(defaults.get("countryCode")) or "US",
        _text(defaults.get("postalCode")) or "45429",
        _text(item.get("brand")), _text(item.get("size")), _text(item.get("color")),
        _text(item.get("dept")), _text(item.get("type")), _text(item.get("style")),
        _text(item.get("mat")), _text(item.get("pat")), _text(item.get("slv")),
        _text(item.get("nk")), _text(item.get("sea")), _text(item.get("occ")),
        _text(item.get("st")), _text(item.get("vin")),
    ]


def export_ebay_csv(items: list[dict[str, Any]], defaults: dict[str, Any] | None = None) -> str:
    rows = [HEADERS] + [listing_to_csv_row(normalize_listing(item), defaults) for item in items]
    for row in rows:
        if len(row) != len(HEADERS):
            raise ValueError(f"CSV row has {len(row)} fields; expected {len(HEADERS)}.")
    out = io.StringIO(newline="")
    writer = _HtmlAwareWriter(out)
    for row in rows:
        writer.writerow(row)
    return out.getvalue()


def build_ebay_draft_csv_row(item: dict[str, Any], sku: str = "") -> dict[str, Any]:
    listing = normalize_listing(item)
    price = _price(listing.get("price"))
    return {
        "Action(SiteID=US|Country=US|Currency=USD|Version=1193|CC=UTF-8)": "Draft",
        "Custom label (SKU)": _text(sku or item.get("sku") or item.get("customLabel")),
        "Category ID": _text(listing.get("cat")),
        "Title": _text(listing.get("title")),
        "UPC": _text(item.get("upc")) or "Does not apply",
        "Price": f"{price:.2f}" if price else "0.00",
        "Quantity": _text(item.get("quantity")) or "1",
        "Item photo URL": _text(listing.get("pic")) or PIC_PLACEHOLDER,
        "Condition ID": _text(listing.get("cid")),
        "Description": _text(listing.get("desc")),
        "Format": "FixedPrice",
    }


def csv_from_draft_row(csv_row: dict[str, Any]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=EBAY_DRAFT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    writer.writerow({column: csv_row.get(column, "") for column in EBAY_DRAFT_COLUMNS})
    return output.getvalue()


def export_ebay_draft_csv(items: list[dict[str, Any]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=EBAY_DRAFT_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for item in items:
        writer.writerow(build_ebay_draft_csv_row(item))
    return output.getvalue()


class _HtmlAwareWriter:
    def __init__(self, out: io.StringIO):
        self.out = out

    def writerow(self, row: list[Any]) -> None:
        self.out.write(",".join(_csv_escape(value) for value in row))
        self.out.write("\r\n")


def _csv_escape(value: Any) -> str:
    text = "" if value is None else str(value)
    if any(ch in text for ch in [",", '"', "\n", "\r", "<", ">"]):
        return '"' + text.replace('"', '""') + '"'
    return text


def parse_model_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    text = str(raw or "").replace("```json", "").replace("```", "").strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("JSON object was not found.")
    return json_loads(text[start:end + 1])


def json_loads(text: str) -> dict[str, Any]:
    import json

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Provider response must be a JSON object.")
    return parsed


def _normalize_category(value: Any, item_type: str) -> str:
    text = _text(value)
    if text in ALLOWED_CATEGORY_IDS:
        return text
    mapped = CATEGORY_IDS.get(text.lower())
    if mapped:
        return mapped
    lowered = item_type.lower()
    if "backpack" in lowered:
        return "169284"
    if re.search(r"handbag|crossbody|clutch|tote|purse", lowered):
        return "169291"
    if re.search(r"shoe|sneaker|boot|loafer|sandal", lowered):
        return "93427"
    return ""


def _normalize_vintage(value: Any, notes: str) -> str:
    evidence = f"{_text(value)} {notes}"
    return "Yes (pre-1999)" if re.search(r"yes \(pre-1999\)|pre.?1999|19[5-9]\ds|vintage tag|made in usa", evidence, re.I) else "No"


def _html_description(candidate: str, item: dict[str, Any]) -> str:
    allowed = re.sub(r"<(?!/?(?:p|ul|li|strong)\b)[^>]*>", " ", candidate or "", flags=re.I).strip()
    if re.search(r"</?(?:p|ul|li|strong)\b", allowed, re.I):
        return allowed
    details = [
        ("Brand", item["brand"]),
        ("Size", item["size"]),
        ("Color", item["color"]),
        ("Department", item["dept"]),
        ("Type", item["type"]),
        ("Style", item["style"]),
        ("Material", item["mat"]),
        ("Pattern", item["pat"]),
        ("Condition", item["cnote"]),
        ("Made In label", item["madeIn"]),
        ("Interior patch / serial", item["serialNumber"]),
        ("Measurements", item["measurements"]),
    ]
    detail_html = "".join(
        f"<li><strong>{html.escape(label)}:</strong> {html.escape(value)}</li>"
        for label, value in details if value
    )
    review = f"<p><strong>Seller review:</strong> {html.escape(item['notes'])}</p>" if item.get("notes") else ""
    return (
        f"<p><strong>{html.escape(item['title'])}</strong></p>"
        f"<ul>{detail_html}</ul>{review}"
        "<p>Ships from Kettering, Ohio. 30-day returns accepted.</p>"
    )


def _strip_authentication_claims(title: str) -> str:
    return re.sub(r"\b(authentic|genuine|verified)\b", "", title, flags=re.I).replace("  ", " ").strip()


def _append_note(notes: str, addition: str) -> str:
    return f"{notes} {addition}".strip() if notes else addition


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _price(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(parsed, 2) if parsed > 0 else 0.0
