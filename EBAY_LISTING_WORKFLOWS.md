# HHT Listing Builder: eBay Draft, CSV, Category, and Publish Workflows

## The important distinction

HHT supports two separate eBay workflows. They are intentionally different because eBay treats them differently.

| What to use | Where it appears | What it does | When to use it |
|---|---|---|---|
| **Download Seller Hub Draft CSV** | Seller Hub → Listings → Drafts, after the file feed is accepted | Creates true Seller Hub drafts that can be finished in eBay | Best when photos are on the phone or final fields still need work in Seller Hub |
| **Create API Offer (not Seller Hub Draft)** | HHT queue by Offer ID; it is not visible in Seller Hub Drafts | Creates an unpublished Inventory API offer | Best when title, public photo URLs, price, category, policies, and specifics are already reviewed in HHT |
| **Publish Live** | Seller Hub → Active listings | Converts a verified Inventory API offer into a live listing | Only after an explicit final review |

> An unpublished Inventory API offer is **not** a Seller Hub Draft. Searching its Offer ID in Seller Hub Drafts will not find it. eBay’s official Inventory API documentation describes it as an unpublished offer that must be published by the API to create an active listing.[1]

## Recommended workflow: create a Seller Hub Draft CSV

This is the recommended route for newly analyzed photo listings while you are still completing categories, specifics, photos, or shipping choices in eBay.

1. In HHT, open **Analyze**, add 1–5 photos, and select **Analyze photos**.
2. Open **Edit**. Correct the title, price, condition, brand, model, size, material, country of manufacture, and description using facts visible on the item.
3. Under **eBay category and required fields**, type an exact query such as `kids baseball hat`, `women's belt`, `Coach crossbody`, or `men's dress shoes`, then select **Find eBay categories**.
4. Select the correct eBay leaf category from the live results. Do not select a broad department just because it is similar.
5. Complete the fields in **Current eBay fields**. Fields marked **Required** are returned by eBay’s Taxonomy API for the selected category. If a fact is not confirmed, leave it blank and finish it in eBay rather than guessing.
6. Select **Add reviewed item to queue**.
7. In **Queue**, select **Download Seller Hub Draft CSV**. This is the file intended for the current Seller Hub Drafts upload—not the legacy CSV button.
8. In eBay, go to **Seller Hub → Reports → Uploads**, choose **Upload template**, and upload the HHT draft CSV. The first time, download eBay’s own **Create new drafts** template for your category so you can compare its current format with your account’s feed.
9. Wait for the upload result. eBay says uploads can take up to approximately 15 minutes. Open the results file; it is the authoritative explanation for any rejected row.[2]
10. When the result indicates the draft was created, select **Complete listings** or open **Seller Hub → Listings → Drafts**. Add local phone photos directly in eBay if the CSV contained no public photo URL.

## Why the prior draft CSV was rejected

The old output mixed fields from an API/File Exchange-style export into a Seller Hub Drafts workflow. In particular, it could include a numeric API condition ID such as `3000` and a fake image URL placeholder. The Seller Hub Drafts template expects only the compact supported draft columns and accepts `NEW` or `USED` for the Condition ID field; it supports a blank or a real public image URL, not an application placeholder.[2]

The updated **Seller Hub Draft CSV** now:

- sets the action to `Draft`;
- preserves the actual selected eBay Category ID;
- writes `NEW` or `USED` instead of Inventory API condition IDs;
- writes a photo URL only when it is a real public `http(s)` URL;
- leaves unknown UPC/GTIN blank instead of inventing one; and
- contains only the documented draft-template columns.

The separate **Download legacy File Exchange CSV** is still available only for a matching legacy/File Exchange upload template. Do not upload that file as a Seller Hub Drafts file.

## Using Create API Offer correctly

Use this only when the listing is already close to final in HHT.

1. In **Edit**, enter a real numeric eBay category, a positive price, condition, title, seller policies, and, ideally, public image URLs.
2. Add the item to **Queue**.
3. Select **Create API Offer (not Seller Hub Draft)**.
4. HHT saves the returned Offer ID. Select **Verify Offer** to retrieve it directly from eBay’s Inventory API.
5. If revisions are needed, select **Edit**, return to Queue, and select **Update Offer**. An Inventory API update replaces the offer’s fields, so retain the full reviewed record.[1]
6. Only after a final seller review should you select **Publish Live**. This is the action that creates the active eBay listing.

> The HHT workflow does not automatically publish. **Publish Live** is the only user-visible step that makes an Inventory API offer live.

## Categories and eBay item specifics

The quick category menu is just a convenience list. It is not the universe of eBay categories. The live category search is the accurate route for hats, children’s items, accessories, jewelry, collectibles, home goods, and other categories outside that starting menu.

eBay’s Taxonomy API produces leaf-category suggestions based on the words you supply. Suggestions are recommendations rather than proof that the category is correct, so select the one that matches the actual item.[3] Once selected, HHT requests the category’s required and recommended specifics. eBay states that the `aspectRequired` value from `getItemAspectsForCategory` identifies required specifics for that leaf category.[4]

## Photos and CSV uploads

CSV uploads cannot transfer images stored only on a phone or within HHT. The CSV can contain up to 24 public image URLs separated with `|`. If you do not have public URLs, leave the image field empty and add photos from your device in Seller Hub Drafts. Do not use a made-up image URL or the previous placeholder text.

## Reference links

[1] [eBay Inventory API: Managing offers](https://developer.ebay.com/api-docs/sell/static/inventory/managing-offers.html)

[2] [eBay Seller Hub: Uploadable templates and Creating draft listings](https://pages.ebay.com/sh/reports/help/uploadable-file-feeds/)

[3] [eBay Taxonomy API: getCategorySuggestions](https://developer.ebay.com/api-docs/commerce/taxonomy/resources/category_tree/methods/getCategorySuggestions)

[4] [eBay required item specifics](https://developer.ebay.com/api-docs/user-guides/static/trading-user-guide/item-specifics-requirements.html)
